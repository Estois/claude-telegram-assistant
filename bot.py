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
from typing import Optional

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

# Sentinel chat id for the background interests-reorganization job, which
# isn't tied to any real Telegram chat. Telegram user ids are always
# positive and group/channel ids always negative, so 0 never collides.
MAINTENANCE_CHAT_ID = 0

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
    news_interests_file: Path
    interests_reorganize_interval_seconds: int
    password_hash_file: Path
    session_timeout_seconds: int
    sessions_file: Path
    trivia_cache_file: Path
    trivia_state_file: Path
    lta_api_key: Optional[str]
    bus_locations_file: Path
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
            news_interests_file=Path(
                os.environ.get("NEWS_INTERESTS_FILE", str(base_dir / "interests.md"))
            ).resolve(),
            interests_reorganize_interval_seconds=int(
                os.environ.get("INTERESTS_REORGANIZE_INTERVAL_SECONDS", "86400")
            ),
            password_hash_file=Path(
                os.environ.get("PASSWORD_HASH_FILE", str(base_dir / "password.hash"))
            ).resolve(),
            session_timeout_seconds=int(os.environ.get("SESSION_TIMEOUT_SECONDS", "900")),
            sessions_file=base_dir / "sessions.json",
            trivia_cache_file=base_dir / "trivia_cache.json",
            trivia_state_file=base_dir / "trivia_state.json",
            lta_api_key=os.environ.get("LTA_API_KEY"),
            bus_locations_file=Path(
                os.environ.get("BUS_LOCATIONS_FILE", str(base_dir / "bus_locations.json"))
            ).resolve(),
            bus_stops_cache_file=Path(
                os.environ.get("BUS_STOPS_CACHE_FILE", str(base_dir / "bus_stops_cache.json"))
            ).resolve(),
            bus_nearby_count=int(os.environ.get("BUS_NEARBY_COUNT", "3")),
        )


class PasswordGate:
    """Tracks per-chat unlock state and verifies password attempts.

    The password is never stored in plaintext — only its SHA256 hex digest,
    read fresh from disk on every attempt so it can be rotated without a
    restart.
    """

    def __init__(self, hash_file: Path, session_timeout_seconds: int):
        self._hash_file = hash_file
        self._session_timeout_seconds = session_timeout_seconds
        self._unlocked_until: dict[int, float] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self._load_hashes())

    def is_unlocked(self, chat_id: int) -> bool:
        expiry = self._unlocked_until.get(chat_id)
        return expiry is not None and time.monotonic() < expiry

    def try_unlock(self, chat_id: int, attempt: str) -> bool:
        hashes = self._load_hashes()
        if not hashes:
            return False
        if hashlib.sha256(attempt.encode()).hexdigest() not in hashes:
            return False
        self._unlocked_until[chat_id] = time.monotonic() + self._session_timeout_seconds
        return True

    def lock(self, chat_id: int) -> bool:
        """Ends the session for a chat immediately. Returns whether one was
        active."""
        was_unlocked = self.is_unlocked(chat_id)
        self._unlocked_until.pop(chat_id, None)
        return was_unlocked

    def _load_hashes(self) -> set:
        """Every non-comment, non-empty line is a valid password hash — any
        of them unlocks a session, so multiple people/devices can each have
        their own password."""
        if not self._hash_file.exists():
            return set()
        hashes = set()
        for line in self._hash_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                hashes.add(line.lower())
        return hashes


class ClaudeSessionStore:
    """Persists chat_id -> Claude session id mappings to disk."""

    def __init__(self, path: Path):
        self._path = path

    def get(self, chat_id: int) -> Optional[str]:
        return self._read().get(str(chat_id))

    def set(self, chat_id: int, session_id: str) -> None:
        sessions = self._read()
        sessions[str(chat_id)] = session_id
        self._write(sessions)

    def clear(self, chat_id: int) -> bool:
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
        self._locks: dict[int, asyncio.Lock] = {}

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def run(self, chat_id: int, prompt: str) -> str:
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
    """Read-only loader for the name -> bus stop code mapping shown as
    /bus's preset location buttons. Hand-edited directly on disk."""

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


class NewsBot:
    """Wires the Telegram handlers together with the services above."""

    def __init__(self, config: Config):
        self._config = config
        self._password_gate = PasswordGate(config.password_hash_file, config.session_timeout_seconds)
        self._sessions = ClaudeSessionStore(config.sessions_file)
        self._claude = ClaudeClient(config, self._sessions)
        self._trivia = TriviaBook(config.trivia_cache_file, config.trivia_state_file, FALLBACK_TRIVIA)
        self._interests = InterestsStore(config.news_interests_file)
        self._lta = LtaBusClient(config.lta_api_key)
        self._bus_stops = BusStopsCache(config.bus_stops_cache_file)
        self._bus_locations = BusLocationsStore(config.bus_locations_file)
        self._help_text = self._build_help_text()
        self._reorganize_task: Optional[asyncio.Task] = None

    def run(self) -> None:
        self._warn_about_missing_config()
        self._build_application().run_polling()

    def _build_help_text(self) -> str:
        minutes = self._config.session_timeout_seconds // 60
        return (
            "Every command needs an unlocked session — send the password as a plain "
            f"message first (lasts {minutes} minutes).\n\n"
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
        if not self._config.news_interests_file.exists():
            logger.warning(
                "Interests file %s doesn't exist yet — /news will run without personalization.",
                self._config.news_interests_file,
            )
        if not self._password_gate.is_configured:
            logger.warning(
                "No password hash in %s — /news will stay locked until you add one.",
                self._config.password_hash_file,
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

    async def _require_session(self, update: Update) -> bool:
        """Every command runs behind the password gate. The one exception is
        handle_plain_text, which is the unlock mechanism itself — gating it
        too would make it impossible to ever unlock. Checks authorization
        first; unauthorized senders get silence, not a lock notice, so the
        bot doesn't confirm its own existence to strangers."""
        if not self._is_authorized(update):
            return False
        if not self._password_gate.is_unlocked(update.effective_chat.id):
            # effective_message (not .message) so this also works for
            # callback-query updates, where .message is None but
            # .callback_query.message holds the original message.
            await update.effective_message.reply_text("🔒 Locked. Send the password to start a session.")
            return False
        return True

    def _build_news_prompt(self, subject: str) -> str:
        interests_file = self._config.news_interests_file
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
        if not await self._require_session(update):
            return
        await update.message.reply_text("Hi!")

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
            return
        await update.message.reply_text(self._help_text)

    async def handle_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
            return
        self._sessions.clear(update.effective_chat.id)
        await update.message.reply_text("Started a new conversation.")

    async def handle_end_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
            return
        ended = self._password_gate.lock(update.effective_chat.id)
        await update.message.reply_text("Session ended." if ended else "No active session.")

    async def handle_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
            return

        if not context.args:
            await update.message.reply_text("Usage: /news <subject>\nExample: /news cybersecurity")
            return

        subject = " ".join(context.args)
        reply = await self._run_claude(update, context, self._build_news_prompt(subject))
        if reply is not None:
            await self._send_formatted(update, reply)

    async def handle_add_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: /addinterests <topic>\nExample: /addinterests cybersecurity, "
                "especially supply chain attacks"
            )
            return
        topic = " ".join(context.args)
        index = self._interests.add(topic)
        await update.message.reply_text(f"Added as interest #{index}.")

    async def handle_get_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
            return
        entries = self._interests.list()
        if not entries:
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return
        await update.message.reply_text(self._format_interests_listing(entries))

    async def handle_update_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
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
        entries = self._interests.list()
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

        self._interests.update(index, new_text)
        await update.message.reply_text(f"Updated interest #{index}.")

    async def handle_compile_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manual trigger for the same reorganization the background job
        runs periodically: merge duplicate/overlapping interests and add
        retrieval-helpful detail."""
        if not await self._require_session(update):
            return

        entries = self._interests.list()
        if len(entries) < 2:
            await update.message.reply_text("Need at least 2 interests to reorganize. See /getinterests.")
            return

        prompt = self._build_reorganize_prompt(entries)
        raw_reply = await self._run_claude(update, context, prompt, chat_id=MAINTENANCE_CHAT_ID)
        if raw_reply is None:
            return

        new_entries = InterestsStore.parse_entries(raw_reply)
        if not new_entries:
            await update.message.reply_text("Reorganize returned nothing usable — no changes made.")
            return

        self._interests.replace_all(new_entries)
        summary = f"🗂 Reorganized interests: {len(entries)} → {len(new_entries)} entries."
        await update.message.reply_text(
            f"{summary}\n\n{self._format_interests_listing(new_entries)}"
        )

    @staticmethod
    def _format_interests_listing(entries: list) -> str:
        return "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))

    async def handle_bus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
            return

        locations = self._bus_locations.list()
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
        if not await self._require_session(update):
            await query.answer()
            return

        await query.answer()
        await query.edit_message_text(
            "Reply to this message with your location — tap 📎 (attach) and choose "
            "Location — and I'll show the nearest stops."
        )

    async def handle_bus_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not await self._require_session(update):
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        text = await self._format_arrivals(stop_code)
        await query.edit_message_text(text)

    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_session(update):
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
        if not await self._require_session(update):
            return
        await update.message.reply_text("Unknown command.")

    async def handle_plain_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Non-command text is always treated as a password attempt, never
        as a prompt to relay to Claude."""
        if not self._is_authorized(update):
            return

        chat_id = update.effective_chat.id
        attempt = (update.message.text or "").strip()

        if self._password_gate.try_unlock(chat_id, attempt):
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
        chat_id: Optional[int] = None,
    ) -> Optional[str]:
        """Runs prompt through Claude and returns the raw reply. Uses the
        Telegram chat's own Claude session by default; pass chat_id to
        target a different one (e.g. the shared maintenance session).
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
        # Shares the maintenance lock with /compileinterests so the two
        # never run the same Claude session concurrently.
        async with self._claude.lock_for(MAINTENANCE_CHAT_ID):
            entries = self._interests.list()
            if len(entries) < 2:
                return  # nothing meaningful to merge or expand

            prompt = self._build_reorganize_prompt(entries)
            raw_reply = await self._claude.run(MAINTENANCE_CHAT_ID, prompt)
            new_entries = InterestsStore.parse_entries(raw_reply)
            if not new_entries:
                logger.warning("Interests reorganize returned nothing parseable, leaving interests.md untouched")
                return

            self._interests.replace_all(new_entries)
            logger.info("Reorganized interests: %d -> %d entries", len(entries), len(new_entries))

        await self._notify_reorganized(app, len(entries), len(new_entries))

    async def _notify_reorganized(self, app: Application, before_count: int, after_count: int) -> None:
        allowed = self._config.allowed_user_ids
        if not allowed:
            return
        summary = f"🗂 Reorganized interests: {before_count} → {after_count} entries. See /getinterests."
        for user_id in allowed:
            try:
                await app.bot.send_message(chat_id=user_id, text=summary)
            except Exception:
                logger.exception("Failed to notify chat %s about reorganized interests", user_id)


def main() -> None:
    NewsBot(Config.from_env()).run()


if __name__ == "__main__":
    main()
