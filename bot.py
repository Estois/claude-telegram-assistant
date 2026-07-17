"""Telegram bot that runs Claude Code behind a password-gated session.

Commands are fixed and preset — no free-form prompts are ever relayed to
Claude. See README.md for setup and the command list.
"""

# Standard library
import asyncio
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

# Third-party
import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

FALLBACK_TRIVIA = [
    {"animal": "Octopus", "fact": "Octopuses have three hearts and blue blood."},
    {"animal": "Sloth", "fact": "Sloths can take up to a month to digest a single meal."},
    {"animal": "Mantis Shrimp", "fact": "Mantis shrimp can punch with the force of a bullet."},
    {"animal": "Elephant", "fact": "Elephants are one of the few animals that can recognize themselves in a mirror."},
    {"animal": "Wood Frog", "fact": "Wood frogs survive winter by letting up to 65% of their body freeze solid."},
]


@dataclass(frozen=True)
class Config:
    """Runtime settings, loaded once from the environment."""

    base_dir: Path
    telegram_token: str
    allowed_user_ids: Optional[frozenset]
    claude_bin: str
    claude_workdir: str
    claude_model: Optional[str]
    claude_permission_mode: str
    claude_timeout_seconds: int
    accounts_dir: Path
    interests_reorganize_interval_seconds: int
    accounts_file: Path
    session_timeout_seconds: int
    sessions_file: Path
    trivia_cache_file: Path
    trivia_state_file: Path
    lta_api_key: Optional[str]
    bus_stops_cache_file: Path
    bus_nearby_count: int

    @classmethod
    def from_env(cls) -> "Config":
        base_dir = Path(__file__).resolve().parent

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
            )

        raw_ids = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        allowed_user_ids = (
            frozenset(int(x) for x in raw_ids.split(",") if x.strip()) if raw_ids else None
        )

        return cls(
            base_dir=base_dir,
            telegram_token=token,
            allowed_user_ids=allowed_user_ids,
            claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
            claude_workdir=os.environ.get("CLAUDE_WORKDIR", str(base_dir)),
            claude_model=os.environ.get("CLAUDE_MODEL"),
            claude_permission_mode=os.environ.get("CLAUDE_PERMISSION_MODE", "bypassPermissions"),
            claude_timeout_seconds=int(os.environ.get("CLAUDE_TIMEOUT_SECONDS", "600")),
            accounts_dir=Path(
                os.environ.get("ACCOUNTS_DIR", str(base_dir / "accounts"))
            ).resolve(),
            interests_reorganize_interval_seconds=int(
                os.environ.get("INTERESTS_REORGANIZE_INTERVAL_SECONDS", "86400")
            ),
            accounts_file=Path(
                os.environ.get("ACCOUNTS_FILE", str(base_dir / "accounts.hash"))
            ).resolve(),
            session_timeout_seconds=int(os.environ.get("SESSION_TIMEOUT_SECONDS", "900")),
            sessions_file=base_dir / "sessions.json",
            trivia_cache_file=base_dir / "trivia_cache.json",
            trivia_state_file=base_dir / "trivia_state.json",
            lta_api_key=os.environ.get("LTA_API_KEY"),
            bus_stops_cache_file=Path(
                os.environ.get("BUS_STOPS_CACHE_FILE", str(base_dir / "bus_stops_cache.json"))
            ).resolve(),
            bus_nearby_count=int(os.environ.get("BUS_NEARBY_COUNT", "3")),
        )


class AccountStore:
    """Maps password hashes to account ids, and tracks which account each
    chat is currently unlocked as.

    One account can have multiple password hashes — e.g. one per device —
    and any of them unlocks that account, sharing its interests and bus
    locations. The password itself is never stored, only its SHA256 hex
    digest, read fresh from disk on every attempt so accounts can be
    added/rotated without a restart.
    """

    ACCOUNT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

    def __init__(self, accounts_file: Path, session_timeout_seconds: int):
        self._accounts_file = accounts_file
        self._session_timeout_seconds = session_timeout_seconds
        self._sessions: dict[int, tuple] = {}  # chat_id -> (account_id, expiry)

    @property
    def is_configured(self) -> bool:
        return any(self._load_accounts().values())

    def list_ids(self) -> list:
        return list(self._load_accounts().keys())

    def is_unlocked(self, chat_id: int) -> bool:
        session = self._sessions.get(chat_id)
        return session is not None and time.monotonic() < session[1]

    def account_for(self, chat_id: int) -> Optional[str]:
        """The account this chat is currently unlocked as, or None if
        locked/expired."""
        if not self.is_unlocked(chat_id):
            return None
        return self._sessions[chat_id][0]

    def try_unlock(self, chat_id: int, attempt: str) -> Optional[str]:
        """Returns the account id the attempt unlocked, or None if it
        matched no account's hash."""
        digest = hashlib.sha256(attempt.encode()).hexdigest()
        for account_id, hashes in self._load_accounts().items():
            if digest in hashes:
                self._sessions[chat_id] = (account_id, time.monotonic() + self._session_timeout_seconds)
                return account_id
        return None

    def lock(self, chat_id: int) -> bool:
        """Ends the session for a chat immediately. Returns whether one was
        active."""
        was_unlocked = self.is_unlocked(chat_id)
        self._sessions.pop(chat_id, None)
        return was_unlocked

    def _load_accounts(self) -> dict:
        """Parses '# account: <id>' header lines, each followed by that
        account's password hash(es), one per line, until the next header.
        Hashes appearing before any header can't be attributed to an
        account and are skipped with a warning."""
        if not self._accounts_file.exists():
            return {}

        accounts: dict = {}
        current = None
        for raw_line in self._accounts_file.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("# account:"):
                candidate = line.split(":", 1)[1].strip()
                if not self.ACCOUNT_ID_PATTERN.match(candidate):
                    logger.warning("Ignoring invalid account id in %s: %r", self._accounts_file, candidate)
                    current = None
                    continue
                current = candidate
                accounts.setdefault(current, set())
                continue
            if line.startswith("#"):
                continue
            if current is None:
                logger.warning(
                    "Password hash in %s before any '# account:' header, ignoring", self._accounts_file
                )
                continue
            accounts[current].add(line.lower())
        return accounts


class ClaudeSessionStore:
    """Persists chat_id -> Claude session id mappings to disk. chat_id is
    usually a real Telegram chat id, but the background/manual interests
    maintenance jobs pass a synthetic string key instead (one per account,
    so each account's reorganize pass gets its own Claude conversation)."""

    def __init__(self, path: Path):
        self._path = path

    def get(self, chat_id: Union[int, str]) -> Optional[str]:
        return self._read().get(str(chat_id))

    def set(self, chat_id: Union[int, str], session_id: str) -> None:
        sessions = self._read()
        sessions[str(chat_id)] = session_id
        self._write(sessions)

    def clear(self, chat_id: Union[int, str]) -> bool:
        sessions = self._read()
        if sessions.pop(str(chat_id), None) is None:
            return False
        self._write(sessions)
        return True

    def _read(self) -> dict:
        if self._path.exists():
            return json.loads(self._path.read_text())
        return {}

    def _write(self, sessions: dict) -> None:
        self._path.write_text(json.dumps(sessions, indent=2))


class ClaudeClient:
    """Runs prompts through the Claude Code CLI, keeping one session per chat
    so follow-up commands continue the same conversation."""

    def __init__(self, config: Config, sessions: ClaudeSessionStore):
        self._config = config
        self._sessions = sessions
        self._locks: dict = {}

    def lock_for(self, chat_id: Union[int, str]) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def run(self, chat_id: Union[int, str], prompt: str) -> str:
        session_id = self._sessions.get(chat_id)
        is_new_session = session_id is None
        if is_new_session:
            session_id = str(uuid.uuid4())

        cmd = self._build_command(prompt, session_id, is_new_session)
        logger.info("Running claude for chat %s (session %s)", chat_id, session_id)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self._config.claude_workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._config.claude_timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"claude timed out after {self._config.claude_timeout_seconds}s")

        if proc.returncode != 0:
            error_output = stderr.decode(errors="replace").strip()
            raise RuntimeError(error_output or f"claude exited with code {proc.returncode}")

        # Only persist once claude confirms success, so a failed first turn
        # doesn't leave behind a dead session id.
        self._sessions.set(chat_id, session_id)
        return stdout.decode(errors="replace").strip()

    def _build_command(self, prompt: str, session_id: str, is_new_session: bool) -> list:
        cmd = [
            self._config.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "text",
            "--permission-mode",
            self._config.claude_permission_mode,
        ]
        if self._config.claude_model:
            cmd += ["--model", self._config.claude_model]
        cmd += ["--session-id", session_id] if is_new_session else ["--resume", session_id]
        return cmd


class InterestsStore:
    """Manages a flat, numbered list of interest entries in interests.md,
    read by /news to bias digest topics and framing."""

    HEADER = (
        "# News Interests\n\n"
        "Read by /news to bias digest topic selection and framing. Managed "
        "via /addinterests, /getinterests, /updateinterests.\n\n"
    )
    ENTRY_PATTERN = re.compile(r"^\d+\.\s+(.*)$")

    def __init__(self, path: Path):
        self._path = path

    def list(self) -> list:
        if not self._path.exists():
            return []
        return self.parse_entries(self._path.read_text())

    def add(self, text: str) -> int:
        """Appends a new entry and returns its 1-based index."""
        entries = self.list()
        entries.append(self._normalize(text))
        self._write(entries)
        return len(entries)

    def update(self, index: int, text: str) -> bool:
        """Replaces the entry at the given 1-based index. Returns False if
        the index is out of range."""
        entries = self.list()
        if not 1 <= index <= len(entries):
            return False
        entries[index - 1] = self._normalize(text)
        self._write(entries)
        return True

    def replace_all(self, entries: list) -> None:
        """Overwrites the whole list, e.g. after a reorganization pass."""
        self._write([self._normalize(entry) for entry in entries])

    @classmethod
    def parse_entries(cls, text: str) -> list:
        """Extracts entries from arbitrary 'N. text' lines, e.g. Claude's
        raw reply to a reorganize prompt."""
        entries = []
        for line in text.splitlines():
            match = cls.ENTRY_PATTERN.match(line.strip())
            if match:
                entries.append(match.group(1))
        return entries

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.split())

    def _write(self, entries: list) -> None:
        body = "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))
        self._path.write_text(self.HEADER + body + ("\n" if body else ""))


class AnimalEmoji:
    """Looks up a representative emoji for an animal name. Checked, in
    order, against exact names, then multi-word phrases, then a dinosaur
    catch-all, then single words (with basic plural stripping) — falling
    back to a generic paw print for anything unmatched. Tuned empirically
    against the ~600 distinct animal names in trivia_cache.json."""

    DEFAULT = "🐾"

    # Portmanteau/proper-noun dog-breed mixes with no matching keyword.
    EXACT = {
        "cockalier": "🐕", "dalmador": "🐕", "dorgi": "🐕", "eskipoo": "🐕",
        "glechon": "🐕", "havashu": "🐕", "labrabull": "🐕", "pomeagle": "🐕",
        "poochon": "🐕", "poogle": "🐕", "pugshire": "🐕", "siberpoo": "🐕",
        "springador": "🐕", "yorkie-poo": "🐕", "jack-chi": "🐕",
        "chorkie": "🐕", "ori-pei": "🐕", "pitsky": "🐕", "siberian": "🐕",
        "french lop": "🐇", "boggle": "🐇", "peruvian inca orchid": "🐕",
        "pocket beagle": "🐕", "chinook": "🐕", "brug": "🐕",
    }

    # Multi-word matches, checked as substrings of the full lowercased name.
    PHRASES = {
        "polar bear": "🐻‍❄️", "black bear": "🐻", "sun bear": "🐻",
        "grizzly bear": "🐻", "asiatic black bear": "🐻", "sea lion": "🦭",
        "sea turtle": "🐢", "sea horse": "🐴", "guide dog": "🦮",
        "killer whale": "🐋", "flying fox": "🦇", "flying squirrel": "🐿️",
        "flying lemur": "🐿️", "hermit crab": "🦀", "king charles spaniel": "🐕",
        "mountain dog": "🐕", "aardwolf": "🐺", "prairie dog": "🐕",
        "mata mata": "🐢", "orang-utan": "🦧", "saint bernard": "🐕",
        "jack russell": "🐕", "tasmanian devil": "🦡", "thorny devil": "🦎",
        "mud dauber": "🐝", "sergeant major": "🐠", "stick insect": "🐛",
        "water bug": "🐛", "bracco italiano": "🐕", "epagneul pont audemer": "🐕",
        "aye-aye": "🐒", "tully monster": "🦕",
    }

    # Single words, checked against each token in the lowercased name.
    WORDS = {
        "dog": "🐕", "puppy": "🐶", "poodle": "🐩", "wolf": "🐺", "fox": "🦊",
        "coyote": "🐺", "dingo": "🐕", "cat": "🐈", "kitten": "🐱", "lion": "🦁",
        "liger": "🦁", "tiger": "🐅", "cheetah": "🐆", "leopard": "🐆",
        "panther": "🐆", "jaguar": "🐆", "jaguarundi": "🐆", "margay": "🐈",
        "lynx": "🐈", "caracal": "🐈", "ocelot": "🐈", "cougar": "🐈",
        "balinese": "🐈", "serval": "🐈", "wildcat": "🐈", "bear": "🐻",
        "panda": "🐼", "koala": "🐨",
        "elephant": "🐘", "rhino": "🦏", "rhinoceros": "🦏", "hippopotamus": "🦛",
        "hippo": "🦛", "giraffe": "🦒", "okapi": "🦒", "zebra": "🦓",
        "zorse": "🦓", "camel": "🐪", "llama": "🦙", "alpaca": "🦙",
        "goat": "🐐", "ibex": "🐐", "markhor": "🐐", "sheep": "🐑",
        "cow": "🐄", "cattle": "🐄", "bull": "🐂", "ox": "🐂", "bison": "🐃",
        "buffalo": "🐃", "zebu": "🐃", "pig": "🐖", "babirusa": "🐗",
        "boar": "🐗", "warthog": "🐗", "horse": "🐴", "mustang": "🐴",
        "donkey": "🫏", "deer": "🦌", "moose": "🫎", "elk": "🦌",
        "reindeer": "🦌", "caribou": "🦌", "gazelle": "🦌", "antelope": "🦌",
        "oryx": "🦌", "kudu": "🦌", "wildebeest": "🐃", "gnu": "🐃",
        "waterbuck": "🦌", "muntjac": "🦌", "takin": "🐐", "gerenuk": "🦌",
        "nyala": "🦌", "saiga": "🦌", "kob": "🦌", "kangaroo": "🦘",
        "wallaby": "🦘", "quokka": "🦘", "raccoon": "🦝", "coati": "🦝",
        "coatimundi": "🦝", "binturong": "🦝", "otter": "🦦", "badger": "🦡",
        "beaver": "🦫", "skunk": "🦨", "polecat": "🦨", "hedgehog": "🦔",
        "tenrec": "🦔", "porcupine": "🦔", "solenodon": "🦔", "squirrel": "🐿️",
        "chipmunk": "🐿️", "marmot": "🐿️", "groundhog": "🐿️", "gopher": "🐿️",
        "mouse": "🐁", "rat": "🐀", "lemming": "🐁", "gerbil": "🐁",
        "hamster": "🐹", "pacarana": "🐹", "mara": "🐹", "rabbit": "🐇",
        "hare": "🐇", "bat": "🦇", "eidolon": "🦇", "monkey": "🐒",
        "baboon": "🐒", "macaque": "🐒", "marmoset": "🐒", "gibbon": "🐒",
        "mandrill": "🐒", "tarsier": "🐒", "lesula": "🐒", "douc": "🐒",
        "gorilla": "🦍", "orangutan": "🦧", "chimp": "🐒", "chimpanzee": "🐒",
        "sloth": "🦥", "megatherium": "🦥", "armadillo": "🦔",
        "anteater": "🦣", "pangolin": "🦔", "hyena": "🐕", "mongoose": "🦫",
        "meerkat": "🦫", "weasel": "🦫", "ferret": "🦦", "ermine": "🦫",
        "wolverine": "🦡", "seal": "🦭", "walrus": "🦭", "dolphin": "🐬",
        "whale": "🐳", "narwhal": "🐳", "manatee": "🦭", "dugong": "🦭",
        "platypus": "🦫", "lemur": "🐒", "loris": "🐒", "civet": "🐈",
        "genet": "🐈", "wombat": "🐨", "opossum": "🐀", "possum": "🐀",
        "numbat": "🐿️", "bilby": "🐇", "echidna": "🦔", "tapir": "🐖",
        "capybara": "🐹", "chinchilla": "🐹", "degu": "🐹", "jerboa": "🐁",
        "pika": "🐇", "dormouse": "🐁", "mole": "🐁", "human": "🧑",
        "fossa": "🐈", "hyrax": "🐹",
        "eagle": "🦅", "hawk": "🦅", "falcon": "🦅", "kite": "🦅", "buzzard": "🦅",
        "owl": "🦉", "vulture": "🦅", "parrot": "🦜", "macaw": "🦜",
        "flamingo": "🦩", "swan": "🦢", "duck": "🦆", "goose": "🦢",
        "wigeon": "🦆", "teal": "🦆", "merganser": "🦆", "chicken": "🐔",
        "hen": "🐔", "rooster": "🐓", "turkey": "🦃", "peacock": "🦚",
        "penguin": "🐧", "waimanu": "🐧", "ostrich": "🦤", "kiwi": "🥝",
        "kakapo": "🦜", "kea": "🦜", "dove": "🕊️", "pigeon": "🕊️",
        "woodpecker": "🐦", "flicker": "🐦", "hummingbird": "🐦",
        "kingfisher": "🐦", "crane": "🐦", "stork": "🐦", "jabiru": "🐦",
        "cardinal": "🐦", "bluebird": "🐦", "robin": "🐦", "jay": "🐦",
        "kingbird": "🐦", "meadowlark": "🐦", "nightjar": "🐦",
        "frigatebird": "🐦", "booby": "🐦", "puffin": "🐧", "cassowary": "🐦",
        "sparrow": "🐦", "finch": "🐦", "warbler": "🐦", "parula": "🐦",
        "waxwing": "🐦", "thrush": "🐦", "chickadee": "🐦", "lyrebird": "🐦",
        "treecreeper": "🐦", "fairywren": "🐦", "wren": "🐦",
        "flycatcher": "🐦", "frogmouth": "🐦", "gull": "🐦", "xeme": "🐦",
        "shoebill": "🦩", "potoo": "🦉", "bird": "🐦", "bustard": "🐦",
        "oxpecker": "🐦", "honeyguide": "🐦", "glider": "🐿️",
        "snake": "🐍", "cobra": "🐍", "viper": "🐍", "python": "🐍",
        "rattlesnake": "🐍", "mamba": "🐍", "adder": "🐍", "anaconda": "🐍",
        "taipan": "🐍", "kingsnake": "🐍", "asp": "🐍", "cascabel": "🐍",
        "keelback": "🐍", "rinkhals": "🐍", "earthsnake": "🐍",
        "lizard": "🦎", "gecko": "🦎", "iguana": "🦎", "skink": "🦎",
        "monitor": "🦎", "chameleon": "🦎", "newt": "🦎", "salamander": "🦎",
        "axolotl": "🦎", "tortoise": "🐢", "turtle": "🐢", "terrapin": "🐢",
        "crocodile": "🐊", "alligator": "🐊", "caiman": "🐊", "gharial": "🐊",
        "sarcosuchus": "🐊", "dragon": "🐉", "meganeura": "🐉",
        "dinosaur": "🦖", "raptor": "🦖", "thalassomedon": "🦕",
        "thanatosdrakon": "🦕", "frog": "🐸", "toad": "🐸", "bullfrog": "🐸",
        "caecilian": "🐸",
        "shark": "🦈", "fish": "🐟", "pufferfish": "🐡", "blowfish": "🐡",
        "angelfish": "🐠", "catfish": "🐟", "goldfish": "🐠", "sunfish": "🐟",
        "swordfish": "🐟", "sailfish": "🐟", "triggerfish": "🐠",
        "filefish": "🐠", "blobfish": "🐟", "lungfish": "🐟", "stonefish": "🐟",
        "fangtooth": "🐟", "fringehead": "🐟", "gar": "🐟", "mojarra": "🐟",
        "cichlid": "🐠", "betta": "🐠", "tang": "🐠", "grouper": "🐟",
        "arowana": "🐟", "pollock": "🐟", "tuna": "🐟", "archerfish": "🐟",
        "coelacanth": "🐟", "hagfish": "🐟", "lamprey": "🐟", "eel": "🐟",
        "moray": "🐟", "ray": "🐟", "rays": "🐟", "sawfish": "🦈",
        "wolffish": "🐟", "cod": "🐟", "pacu": "🐟", "piranha": "🐟",
        "tigerfish": "🐟", "perch": "🐟", "sucker": "🐟",
        "squid": "🦑", "octopus": "🐙", "jellyfish": "🪼", "crab": "🦀",
        "lobster": "🦞", "shrimp": "🦐", "seahorse": "🐴", "anemone": "🌊",
        "starfish": "⭐", "snail": "🐌", "slug": "🐌", "cone": "🐚",
        "bee": "🐝", "wasp": "🐝", "hornet": "🐝", "ant": "🐜", "beetle": "🪲",
        "weevil": "🪲", "butterfly": "🦋", "moth": "🦋", "dragonfly": "🐉",
        "mosquito": "🦟", "cricket": "🦗", "grasshopper": "🦗", "locust": "🦗",
        "mantis": "🦗", "cicada": "🦗", "cockroach": "🪳", "earwig": "🪲",
        "millipede": "🐛", "centipede": "🐛", "spider": "🕷️", "tarantula": "🕷️",
        "scorpion": "🦂", "worm": "🪱", "earthworm": "🪱", "superworm": "🪱",
        "caterpillar": "🐛", "fly": "🪰", "flies": "🪰", "aphid": "🪲",
        "aphids": "🪲", "woodlouse": "🐛", "insect": "🐛", "birdeater": "🕷️",
        "armyworm": "🐛",
        # Dog breed / kennel-club keywords.
        "terrier": "🐕", "hound": "🐕", "coonhound": "🐕", "spaniel": "🐕",
        "retriever": "🐕", "shepherd": "🐕", "corgi": "🐕", "husky": "🐕",
        "chow": "🐕", "heeler": "🐕", "elkhound": "🐕", "newfoundland": "🐕",
        "newfie": "🐕", "laika": "🐕", "schnauzer": "🐕", "malamute": "🐕",
        "mastiff": "🐕", "chihuahua": "🐕", "beagle": "🐕", "dachshund": "🐕",
        "whippet": "🐕", "greyhound": "🐕", "wolfhound": "🐕", "bulldog": "🐕",
        "rottweiler": "🐕", "doberman": "🐕", "boxer": "🐕", "papillon": "🐕",
        "vizsla": "🐕", "weimaraner": "🐕", "akita": "🐕", "shiba": "🐕",
        "sheepdog": "🐕", "setter": "🐕", "pointer": "🐕", "collie": "🐕",
        "basenji": "🐕", "schipperke": "🐕", "schapendoes": "🐕",
        "pekingese": "🐕", "havanese": "🐕", "otterhound": "🐕",
        "akbash": "🐕", "gampr": "🐕", "zuchon": "🐕",
    }

    # Dog-breed keywords take priority over incidental descriptive words
    # elsewhere in the name — "American Leopard Hound" and "Bull Terrier"
    # are dog breeds, not a leopard or a bull, but a first-match-wins scan
    # of the full WORDS table would hit "leopard"/"bull" first.
    BREED_WORDS = frozenset(
        {
            "terrier", "hound", "coonhound", "spaniel", "retriever", "shepherd",
            "corgi", "husky", "chow", "heeler", "elkhound", "newfoundland",
            "newfie", "laika", "schnauzer", "malamute", "mastiff", "chihuahua",
            "beagle", "dachshund", "whippet", "greyhound", "wolfhound", "bulldog",
            "rottweiler", "doberman", "boxer", "papillon", "vizsla", "weimaraner",
            "akita", "shiba", "sheepdog", "setter", "pointer", "collie", "basenji",
            "schipperke", "schapendoes", "pekingese", "havanese", "otterhound",
            "akbash", "gampr", "zuchon",
        }
    )

    @classmethod
    def for_name(cls, name: str) -> str:
        lowered = name.lower()
        if lowered in cls.EXACT:
            return cls.EXACT[lowered]
        for phrase, emoji in cls.PHRASES.items():
            if phrase in lowered:
                return emoji
        if "saur" in lowered:
            return "🦕"

        words = re.findall(r"[a-z']+", lowered)
        if any(word in cls.BREED_WORDS for word in words):
            return "🐕"
        for word in words:
            if word in cls.WORDS:
                return cls.WORDS[word]
            if word.endswith("s") and word[:-1] in cls.WORDS:
                return cls.WORDS[word[:-1]]
        return cls.DEFAULT


class TriviaBook:
    """Serves animal trivia facts in a shuffled, non-repeating rotation.

    Walks a shuffled permutation of the pool so nothing repeats until every
    fact has been used once, then reshuffles for the next cycle.
    """

    def __init__(self, cache_file: Path, state_file: Path, fallback: list):
        self._cache_file = cache_file
        self._state_file = state_file
        self._fallback = fallback

    def next_fact(self) -> str:
        pool = self._load_cache() or self._fallback
        item = pool[self._next_index(len(pool))]
        emoji = AnimalEmoji.for_name(item["animal"])
        return f"{emoji} {item['animal']} fact: {item['fact']}"

    def _load_cache(self) -> list:
        if not self._cache_file.exists():
            return []
        try:
            return json.loads(self._cache_file.read_text())
        except json.JSONDecodeError:
            logger.exception("%s is corrupt", self._cache_file)
            return []

    def _next_index(self, pool_size: int) -> int:
        state = {}
        if self._state_file.exists():
            try:
                state = json.loads(self._state_file.read_text())
            except json.JSONDecodeError:
                state = {}

        order = state.get("order") or []
        position = state.get("position", 0)

        if len(order) != pool_size or position >= len(order):
            order = list(range(pool_size))
            random.shuffle(order)
            position = 0

        index = order[position]
        self._state_file.write_text(json.dumps({"order": order, "position": position + 1}))
        return index


class TelegramFormatter:
    """Splits long text into Telegram-sized chunks, preferring to break on
    paragraph boundaries so HTML tags aren't split across messages."""

    MESSAGE_LIMIT = 4000

    @classmethod
    def chunk(cls, text: str) -> list:
        if not text:
            return ["(no output)"]
        if len(text) <= cls.MESSAGE_LIMIT:
            return [text]

        chunks = []
        current = ""
        for paragraph in text.split("\n\n"):
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= cls.MESSAGE_LIMIT:
                current = candidate
                continue

            if current:
                chunks.append(current)
            current = paragraph
            while len(current) > cls.MESSAGE_LIMIT:
                chunks.append(current[: cls.MESSAGE_LIMIT])
                current = current[cls.MESSAGE_LIMIT :]

        if current:
            chunks.append(current)
        return chunks


class LtaBusClient:
    """Wraps Singapore LTA DataMall's real-time bus arrival API."""

    BASE_URL = "https://datamall2.mytransport.sg/ltaodataservice"

    def __init__(self, api_key: Optional[str]):
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def get_arrivals(self, bus_stop_code: str) -> list:
        """Returns services sorted by number, each as
        {"service_no": "21", "arrivals": ["2 min", "Arr", "15 min"]}."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.BASE_URL}/v3/BusArrival",
                params={"BusStopCode": bus_stop_code},
                headers={"AccountKey": self._api_key, "accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        return self._parse_services(data)

    @classmethod
    def _parse_services(cls, data: dict) -> list:
        services = []
        for svc in data.get("Services", []):
            arrivals = [
                cls._format_eta(svc.get(key, {}).get("EstimatedArrival"))
                for key in ("NextBus", "NextBus2", "NextBus3")
            ]
            services.append({"service_no": svc["ServiceNo"], "arrivals": arrivals})

        services.sort(key=lambda s: s["service_no"])
        return services

    @staticmethod
    def _format_eta(eta_iso: Optional[str]) -> str:
        if not eta_iso:
            return "—"
        arrival = datetime.fromisoformat(eta_iso)
        minutes = round((arrival - datetime.now(arrival.tzinfo)).total_seconds() / 60)
        return "Arr" if minutes <= 0 else f"{minutes} min"


class BusStopsCache:
    """Loads the static LTA bus-stops dataset (built by fetch_bus_stops.py)
    for nearest-stop lookups from a live location."""

    def __init__(self, cache_file: Path):
        self._cache_file = cache_file

    @property
    def is_available(self) -> bool:
        return self._cache_file.exists()

    def nearest(self, lat: float, lon: float, count: int) -> list:
        stops = self._load()
        for stop in stops:
            stop["distance_m"] = self._haversine_m(
                lat, lon, stop["Latitude"], stop["Longitude"]
            )
        stops.sort(key=lambda s: s["distance_m"])
        return stops[:count]

    def find_by_code(self, code: str) -> Optional[dict]:
        return next((s for s in self._load() if s["BusStopCode"] == code), None)

    def _load(self) -> list:
        if not self._cache_file.exists():
            return []
        try:
            return json.loads(self._cache_file.read_text())
        except json.JSONDecodeError:
            logger.exception("%s is corrupt", self._cache_file)
            return []

    @staticmethod
    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        earth_radius_m = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * earth_radius_m * math.asin(math.sqrt(a))


class BusLocationsStore:
    """Manages the name -> bus stop code mapping shown as /bus's preset
    location buttons. Hand-editable, and also written to directly when a
    stop is added/removed as preferred from the bot."""

    def __init__(self, path: Path):
        self._path = path

    def list(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            logger.exception("%s is corrupt", self._path)
            return {}

    def has_code(self, code: str) -> bool:
        return code in self.list().values()

    def add(self, name: str, code: str) -> None:
        locations = self.list()
        locations[name] = code
        self._write(locations)

    def remove_by_code(self, code: str) -> bool:
        """Removes every entry pointing at this stop code. Returns whether
        anything was removed."""
        locations = self.list()
        names = [name for name, stop_code in locations.items() if stop_code == code]
        if not names:
            return False
        for name in names:
            del locations[name]
        self._write(locations)
        return True

    def _write(self, locations: dict) -> None:
        self._path.write_text(json.dumps(locations, indent=2))


class NewsBot:
    """Wires the Telegram handlers together with the services above."""

    INTERESTS_FILENAME = "interests.md"
    BUS_LOCATIONS_FILENAME = "bus_locations.json"

    def __init__(self, config: Config):
        self._config = config
        self._accounts = AccountStore(config.accounts_file, config.session_timeout_seconds)
        self._sessions = ClaudeSessionStore(config.sessions_file)
        self._claude = ClaudeClient(config, self._sessions)
        self._trivia = TriviaBook(config.trivia_cache_file, config.trivia_state_file, FALLBACK_TRIVIA)
        self._lta = LtaBusClient(config.lta_api_key)
        self._bus_stops = BusStopsCache(config.bus_stops_cache_file)
        self._help_text = self._build_help_text()
        self._reorganize_task: Optional[asyncio.Task] = None

    def _account_dir(self, account_id: str) -> Path:
        path = self._config.accounts_dir / account_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _interests_for(self, account_id: str) -> InterestsStore:
        return InterestsStore(self._account_dir(account_id) / self.INTERESTS_FILENAME)

    def _bus_locations_for(self, account_id: str) -> BusLocationsStore:
        return BusLocationsStore(self._account_dir(account_id) / self.BUS_LOCATIONS_FILENAME)

    @staticmethod
    def _maintenance_key(account_id: str) -> str:
        """Synthetic ClaudeClient/session key for an account's background
        interests-reorganization conversation — never a real Telegram chat
        id, so it can't collide with one."""
        return f"maintenance:{account_id}"

    def run(self) -> None:
        self._warn_about_missing_config()
        self._build_application().run_polling()

    def _build_help_text(self) -> str:
        minutes = self._config.session_timeout_seconds // 60
        return (
            "Every command needs an unlocked session — send the password as a plain "
            f"message first (lasts {minutes} minutes). Interests and preferred bus "
            "stops belong to whichever account your password unlocked.\n\n"
            "Commands:\n"
            "/news <subject> — news digest on a subject, biased toward your interests\n"
            "/addinterests <topic> — add an interest (word, sentence, or paragraph)\n"
            "/getinterests — list your interests with their index\n"
            "/updateinterests <index> <topic> — revise or rewrite an interest "
            "(Claude decides which)\n"
            "/compileinterests — merge duplicate interests and add retrieval detail now "
            "(runs automatically too)\n"
            "/bus — bus arrival times, from a preset stop or your live location\n"
            "/endsession — end the current session now\n"
            "/new — start a fresh conversation\n"
            "/help — show this message"
        )

    def _build_application(self) -> Application:
        app = (
            Application.builder()
            .token(self._config.telegram_token)
            .post_init(self._on_startup)
            .post_shutdown(self._on_shutdown)
            .build()
        )
        app.add_handler(CommandHandler("start", self.handle_start))
        app.add_handler(CommandHandler("help", self.handle_help))
        app.add_handler(CommandHandler("new", self.handle_new))
        app.add_handler(CommandHandler("endsession", self.handle_end_session))
        app.add_handler(CommandHandler("news", self.handle_news))
        app.add_handler(CommandHandler("addinterests", self.handle_add_interests))
        app.add_handler(CommandHandler("getinterests", self.handle_get_interests))
        app.add_handler(CommandHandler("updateinterests", self.handle_update_interests))
        app.add_handler(CommandHandler("compileinterests", self.handle_compile_interests))
        app.add_handler(CommandHandler("bus", self.handle_bus))
        app.add_handler(CallbackQueryHandler(self.handle_bus_callback, pattern=r"^bus_stop:"))
        app.add_handler(CallbackQueryHandler(self.handle_bus_fav_add, pattern=r"^bus_fav_add:"))
        app.add_handler(CallbackQueryHandler(self.handle_bus_fav_remove, pattern=r"^bus_fav_remove:"))
        app.add_handler(
            CallbackQueryHandler(self.handle_bus_request_location, pattern=r"^bus_request_location$")
        )
        app.add_handler(MessageHandler(filters.LOCATION, self.handle_location))
        app.add_handler(MessageHandler(filters.COMMAND, self.handle_unknown_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_plain_text))
        return app

    def _warn_about_missing_config(self) -> None:
        if self._config.allowed_user_ids is None:
            logger.warning(
                "TELEGRAM_ALLOWED_USER_IDS is not set - anyone who messages this bot can run "
                "Claude Code with permission checks bypassed (%s). Set it before exposing this bot.",
                self._config.claude_permission_mode,
            )
        if not self._accounts.is_configured:
            logger.warning(
                "No accounts configured in %s — every command will stay locked until you add one.",
                self._config.accounts_file,
            )
        if not self._lta.is_configured:
            logger.warning(
                "LTA_API_KEY is not set — /bus will not be able to fetch arrival times."
            )

    def _is_authorized(self, update: Update) -> bool:
        allowed = self._config.allowed_user_ids
        if allowed is None:
            return True
        authorized = update.effective_user.id in allowed
        if not authorized:
            logger.warning("Ignoring message from unauthorized user %s", update.effective_user.id)
        return authorized

    async def _require_session(self, update: Update) -> Optional[str]:
        """Every command runs behind the password gate. The one exception is
        handle_plain_text, which is the unlock mechanism itself — gating it
        too would make it impossible to ever unlock. Checks authorization
        first; unauthorized senders get silence, not a lock notice, so the
        bot doesn't confirm its own existence to strangers. Returns the
        unlocked account id, or None if the caller should stop (already
        replied with the lock notice in that case)."""
        if not self._is_authorized(update):
            return None
        account_id = self._accounts.account_for(update.effective_chat.id)
        if account_id is None:
            # effective_message (not .message) so this also works for
            # callback-query updates, where .message is None but
            # .callback_query.message holds the original message.
            await update.effective_message.reply_text("🔒 Locked. Send the password to start a session.")
            return None
        return account_id

    def _build_news_prompt(self, subject: str, account_id: str) -> str:
        interests_file = self._account_dir(account_id) / self.INTERESTS_FILENAME
        return (
            f"Silently read {interests_file} for context on my personal interests — "
            "do not mention this file, its contents, or whether it has been filled in "
            "anywhere in your reply. "
            f'Write a news digest about "{subject}", covering the most significant recent '
            "developments, biased toward my documented interests where they're relevant to "
            "this subject (don't force a connection that isn't there). "
            "Output ONLY the digest itself — no preamble, no meta-commentary about your "
            "process or sources of context. "
            "Format it for a Telegram message using only these HTML tags: <b>, <i>, <u>, "
            '<s>, <code>, <pre>, and <a href="URL">text</a>. Do not use Markdown syntax '
            "(no **, no #, no [text](url)). Use short paragraphs and simple bullet points "
            "(•) for scanability. Escape any literal <, >, or & characters that aren't part "
            "of a tag."
        )

    @staticmethod
    def _build_update_interest_prompt(old_entry: str, new_input: str) -> str:
        return (
            "I'm editing one entry in a personal interests list used to bias news "
            f'digests. Existing entry: "{old_entry}"\n'
            f'New input: "{new_input}"\n\n'
            "Decide whether the new input is meant to add detail to the existing "
            "entry or fully replace it, and produce the resulting entry text "
            "accordingly. Reply with ONLY the final entry text as a single plain-text "
            "paragraph — no quotes, no labels, no explanation, no formatting, nothing "
            "else."
        )

    @staticmethod
    def _build_reorganize_prompt(entries: list) -> str:
        numbered = "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))
        return (
            "Here is my current news-interests list, used to bias what /news digests "
            f"cover:\n\n{numbered}\n\n"
            "Reorganize this list: merge entries that are duplicates or clearly the same "
            "underlying topic, and where it would help a news-search assistant retrieve "
            "more relevant results, add a short blurb of extra detail to an entry — "
            "specific sub-topics, entities, or angles reasonably implied by what's "
            "already there. Don't invent interests the list doesn't imply, and don't "
            "drop any distinct topic. "
            "Reply with ONLY the resulting list, renumbered from 1, one entry per line, "
            "in the exact form '<number>. <entry text>' — plain text, no extra "
            "commentary, no headers, no markdown."
        )

    # -- Command handlers -------------------------------------------------

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        await update.message.reply_text("Hi!")

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        await update.message.reply_text(self._help_text)

    async def handle_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        self._sessions.clear(update.effective_chat.id)
        await update.message.reply_text("Started a new conversation.")

    async def handle_end_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        ended = self._accounts.lock(update.effective_chat.id)
        await update.message.reply_text("Session ended." if ended else "No active session.")

    async def handle_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        if not context.args:
            await update.message.reply_text("Usage: /news <subject>\nExample: /news cybersecurity")
            return

        subject = " ".join(context.args)
        reply = await self._run_claude(update, context, self._build_news_prompt(subject, account_id))
        if reply is not None:
            await self._send_formatted(update, reply)

    async def handle_add_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: /addinterests <topic>\nExample: /addinterests cybersecurity, "
                "especially supply chain attacks"
            )
            return
        topic = " ".join(context.args)
        index = self._interests_for(account_id).add(topic)
        await update.message.reply_text(f"Added as interest #{index}.")

    async def handle_get_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        entries = self._interests_for(account_id).list()
        if not entries:
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return
        await update.message.reply_text(self._format_interests_listing(entries))

    async def handle_update_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        if len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /updateinterests <index> <topic>\nExample: /updateinterests 2 "
                "also mention zero-day exploits"
            )
            return

        index_arg, *topic_parts = context.args
        if not index_arg.isdigit():
            await update.message.reply_text("Index must be a number. See /getinterests for valid indices.")
            return

        index = int(index_arg)
        interests = self._interests_for(account_id)
        entries = interests.list()
        if not entries:
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return
        if not 1 <= index <= len(entries):
            await update.message.reply_text(
                f"No interest #{index}. Valid range: 1-{len(entries)}. See /getinterests."
            )
            return

        topic = " ".join(topic_parts)
        prompt = self._build_update_interest_prompt(entries[index - 1], topic)
        new_text = await self._run_claude(update, context, prompt)
        if new_text is None:
            return

        interests.update(index, new_text)
        await update.message.reply_text(f"Updated interest #{index}.")

    async def handle_compile_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manual trigger for the same reorganization the background job
        runs periodically: merge duplicate/overlapping interests and add
        retrieval-helpful detail."""
        account_id = await self._require_session(update)
        if account_id is None:
            return

        interests = self._interests_for(account_id)
        entries = interests.list()
        if len(entries) < 2:
            await update.message.reply_text("Need at least 2 interests to reorganize. See /getinterests.")
            return

        prompt = self._build_reorganize_prompt(entries)
        raw_reply = await self._run_claude(
            update, context, prompt, chat_id=self._maintenance_key(account_id)
        )
        if raw_reply is None:
            return

        new_entries = InterestsStore.parse_entries(raw_reply)
        if not new_entries:
            await update.message.reply_text("Reorganize returned nothing usable — no changes made.")
            return

        interests.replace_all(new_entries)
        summary = f"🗂 Reorganized interests: {len(entries)} → {len(new_entries)} entries."
        await update.message.reply_text(
            f"{summary}\n\n{self._format_interests_listing(new_entries)}"
        )

    @staticmethod
    def _format_interests_listing(entries: list) -> str:
        return "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))

    async def handle_bus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        locations = self._bus_locations_for(account_id).list()
        buttons = [
            [InlineKeyboardButton(name, callback_data=f"bus_stop:{code}")]
            for name, code in locations.items()
        ]
        buttons.append(
            [InlineKeyboardButton("📍 Send my location", callback_data="bus_request_location")]
        )
        await update.message.reply_text("Pick a stop:", reply_markup=InlineKeyboardMarkup(buttons))

    async def handle_bus_request_location(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if await self._require_session(update) is None:
            await query.answer()
            return

        await query.answer()
        await query.edit_message_text(
            "Reply to this message with your location — tap 📎 (attach) and choose "
            "Location — and I'll show the nearest stops."
        )

    async def handle_bus_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        text = await self._format_arrivals(stop_code)
        await query.edit_message_text(text, reply_markup=self._build_arrivals_keyboard(stop_code, account_id))

    async def handle_bus_fav_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        name = self._describe_stop(stop_code)
        self._bus_locations_for(account_id).add(name, stop_code)

        text = await self._format_arrivals(stop_code)
        await query.edit_message_text(
            f'⭐ Added "{name}" to preferred stops.\n\n{text}',
            reply_markup=self._build_arrivals_keyboard(stop_code, account_id),
        )

    async def handle_bus_fav_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        self._bus_locations_for(account_id).remove_by_code(stop_code)

        text = await self._format_arrivals(stop_code)
        await query.edit_message_text(
            f"🗑 Removed from preferred stops.\n\n{text}",
            reply_markup=self._build_arrivals_keyboard(stop_code, account_id),
        )

    def _describe_stop(self, stop_code: str) -> str:
        stop = self._bus_stops.find_by_code(stop_code)
        if stop:
            return f"{stop['Description']} ({stop_code})"
        return f"Stop {stop_code}"

    def _build_arrivals_keyboard(self, stop_code: str, account_id: str) -> InlineKeyboardMarkup:
        if self._bus_locations_for(account_id).has_code(stop_code):
            fav_button = InlineKeyboardButton(
                "🗑 Remove from preferred", callback_data=f"bus_fav_remove:{stop_code}"
            )
        else:
            fav_button = InlineKeyboardButton(
                "⭐ Add as preferred", callback_data=f"bus_fav_add:{stop_code}"
            )
        return InlineKeyboardMarkup([[fav_button]])

    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return

        if not self._bus_stops.is_available:
            await update.message.reply_text(
                "Nearby-stop lookup isn't set up yet — run fetch_bus_stops.py first, "
                "or use a preset location via /bus."
            )
            return

        loc = update.message.location
        nearest = self._bus_stops.nearest(loc.latitude, loc.longitude, self._config.bus_nearby_count)
        if not nearest:
            await update.message.reply_text("No bus stops found nearby.")
            return

        buttons = [
            [
                InlineKeyboardButton(
                    f"{stop['Description']} ({int(stop['distance_m'])}m)",
                    callback_data=f"bus_stop:{stop['BusStopCode']}",
                )
            ]
            for stop in nearest
        ]
        await update.message.reply_text("Nearest stops:", reply_markup=InlineKeyboardMarkup(buttons))

    async def _format_arrivals(self, stop_code: str) -> str:
        if not self._lta.is_configured:
            return "LTA_API_KEY is not configured — can't fetch arrival times."

        try:
            services = await self._lta.get_arrivals(stop_code)
        except Exception:
            logger.exception("LTA bus arrival lookup failed for stop %s", stop_code)
            return f"Couldn't fetch arrival times for stop {stop_code}. Try again shortly."

        if not services:
            return f"🚌 Stop {stop_code}\nNo bus data available right now."

        lines = [f"🚌 Stop {stop_code}"]
        for svc in services:
            lines.append(f"Svc {svc['service_no']}: " + ", ".join(svc["arrivals"]))
        return "\n".join(lines)

    async def handle_unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        await update.message.reply_text("Unknown command.")

    async def handle_plain_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Non-command text is always treated as a password attempt, never
        as a prompt to relay to Claude."""
        if not self._is_authorized(update):
            return

        chat_id = update.effective_chat.id
        attempt = (update.message.text or "").strip()

        if self._accounts.try_unlock(chat_id, attempt) is not None:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            except Exception:
                logger.exception("Failed to delete password message")
            minutes = self._config.session_timeout_seconds // 60
            await context.bot.send_message(
                chat_id=chat_id, text=f"Unlocked. Session active for {minutes} minutes."
            )
            return

        # Wrong password (or none configured yet) — no hint that this was an
        # auth attempt, just a generic reply.
        await update.message.reply_text(self._trivia.next_fact())

    # -- Claude dispatch ----------------------------------------------------

    async def _run_claude(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        prompt: str,
        *,
        chat_id: Optional[Union[int, str]] = None,
    ) -> Optional[str]:
        """Runs prompt through Claude and returns the raw reply. Uses the
        Telegram chat's own Claude session by default; pass chat_id to
        target a different one (e.g. an account's maintenance session).
        Returns None if a busy/error message was already sent to the user
        and the caller should stop."""
        telegram_chat_id = update.effective_chat.id
        claude_chat_id = chat_id if chat_id is not None else telegram_chat_id

        lock = self._claude.lock_for(claude_chat_id)
        if lock.locked():
            await update.message.reply_text("Still working on your last request — hang on.")
            return None

        async with lock:
            keep_typing = asyncio.create_task(self._keep_typing(context, telegram_chat_id))
            try:
                return await self._claude.run(claude_chat_id, prompt)
            except Exception as exc:
                logger.exception("claude invocation failed")
                await update.message.reply_text(f"Something went wrong: {exc}")
                return None
            finally:
                keep_typing.cancel()

    async def _send_formatted(self, update: Update, text: str) -> None:
        """Sends Claude's reply, chunked for Telegram's message limit. Tries
        HTML formatting first and falls back to plain text per chunk if the
        markup doesn't parse — LLM-generated tags aren't guaranteed
        well-formed, so a broken reply must never mean a lost reply."""
        for chunk in TelegramFormatter.chunk(text):
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
            except BadRequest:
                logger.warning("Telegram rejected HTML formatting, sending chunk as plain text")
                await update.message.reply_text(chunk)

    @staticmethod
    async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        try:
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    # -- Background interests maintenance ------------------------------

    async def _on_startup(self, app: Application) -> None:
        interval = self._config.interests_reorganize_interval_seconds
        if interval > 0:
            self._reorganize_task = asyncio.create_task(self._reorganize_loop(app, interval))

    async def _on_shutdown(self, app: Application) -> None:
        if self._reorganize_task:
            self._reorganize_task.cancel()

    async def _reorganize_loop(self, app: Application, interval_seconds: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    await self._reorganize_interests(app)
                except Exception:
                    logger.exception("Interests reorganization failed")
        except asyncio.CancelledError:
            pass

    async def _reorganize_interests(self, app: Application) -> None:
        for account_id in self._accounts.list_ids():
            try:
                await self._reorganize_account(app, account_id)
            except Exception:
                logger.exception("Interests reorganization failed for account %s", account_id)

    async def _reorganize_account(self, app: Application, account_id: str) -> None:
        # Shares the maintenance lock with /compileinterests (for this
        # account) so the two never run the same Claude session concurrently.
        maintenance_key = self._maintenance_key(account_id)
        async with self._claude.lock_for(maintenance_key):
            interests = self._interests_for(account_id)
            entries = interests.list()
            if len(entries) < 2:
                return  # nothing meaningful to merge or expand

            prompt = self._build_reorganize_prompt(entries)
            raw_reply = await self._claude.run(maintenance_key, prompt)
            new_entries = InterestsStore.parse_entries(raw_reply)
            if not new_entries:
                logger.warning(
                    "Interests reorganize for %s returned nothing parseable, leaving its interests.md untouched",
                    account_id,
                )
                return

            interests.replace_all(new_entries)
            logger.info("Reorganized interests for %s: %d -> %d entries", account_id, len(entries), len(new_entries))

        await self._notify_reorganized(app, account_id, len(entries), len(new_entries))

    async def _notify_reorganized(
        self, app: Application, account_id: str, before_count: int, after_count: int
    ) -> None:
        allowed = self._config.allowed_user_ids
        if not allowed:
            return
        summary = (
            f"🗂 [{account_id}] Reorganized interests: {before_count} → {after_count} "
            "entries. See /getinterests."
        )
        for user_id in allowed:
            try:
                await app.bot.send_message(chat_id=user_id, text=summary)
            except Exception:
                logger.exception("Failed to notify chat %s about reorganized interests", user_id)


def main() -> None:
    NewsBot(Config.from_env()).run()


if __name__ == "__main__":
    main()
