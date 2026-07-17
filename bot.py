"""Telegram bot that runs Claude Code behind a password-gated session.

Commands are fixed and preset — no free-form prompts are ever relayed to
Claude. See README.md for setup and the command list.
"""

# Standard library
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Third-party
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

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
    news_interests_file: Path
    password_hash_file: Path
    session_timeout_seconds: int
    sessions_file: Path
    trivia_cache_file: Path
    trivia_state_file: Path

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
            password_hash_file=Path(
                os.environ.get("PASSWORD_HASH_FILE", str(base_dir / "password.hash"))
            ).resolve(),
            session_timeout_seconds=int(os.environ.get("SESSION_TIMEOUT_SECONDS", "900")),
            sessions_file=base_dir / "sessions.json",
            trivia_cache_file=base_dir / "trivia_cache.json",
            trivia_state_file=base_dir / "trivia_state.json",
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
        return self._load_hash() is not None

    def is_unlocked(self, chat_id: int) -> bool:
        expiry = self._unlocked_until.get(chat_id)
        return expiry is not None and time.monotonic() < expiry

    def try_unlock(self, chat_id: int, attempt: str) -> bool:
        expected_hash = self._load_hash()
        if not expected_hash:
            return False
        if hashlib.sha256(attempt.encode()).hexdigest() != expected_hash:
            return False
        self._unlocked_until[chat_id] = time.monotonic() + self._session_timeout_seconds
        return True

    def _load_hash(self) -> Optional[str]:
        if not self._hash_file.exists():
            return None
        for line in self._hash_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line.lower()
        return None


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
        entries = []
        for line in self._path.read_text().splitlines():
            match = self.ENTRY_PATTERN.match(line.strip())
            if match:
                entries.append(match.group(1))
        return entries

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

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.split())

    def _write(self, entries: list) -> None:
        body = "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))
        self._path.write_text(self.HEADER + body + ("\n" if body else ""))


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
        return f"🐾 {item['animal']} fact: {item['fact']}"

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


class NewsBot:
    """Wires the Telegram handlers together with the services above."""

    def __init__(self, config: Config):
        self._config = config
        self._password_gate = PasswordGate(config.password_hash_file, config.session_timeout_seconds)
        self._sessions = ClaudeSessionStore(config.sessions_file)
        self._claude = ClaudeClient(config, self._sessions)
        self._trivia = TriviaBook(config.trivia_cache_file, config.trivia_state_file, FALLBACK_TRIVIA)
        self._interests = InterestsStore(config.news_interests_file)
        self._help_text = self._build_help_text()

    def run(self) -> None:
        self._warn_about_missing_config()
        self._build_application().run_polling()

    def _build_help_text(self) -> str:
        minutes = self._config.session_timeout_seconds // 60
        return (
            "Commands:\n"
            "/news <subject> — news digest on a subject, biased toward your interests. "
            "Requires an unlocked session — send the password as a plain message first "
            f"(lasts {minutes} minutes).\n"
            "/addinterests <topic> — add an interest (word, sentence, or paragraph)\n"
            "/getinterests — list your interests with their index\n"
            "/updateinterests <index> <topic> — revise or rewrite an interest "
            "(Claude decides which); requires an unlocked session\n"
            "/new — start a fresh conversation\n"
            "/help — show this message"
        )

    def _build_application(self) -> Application:
        app = Application.builder().token(self._config.telegram_token).build()
        app.add_handler(CommandHandler("start", self.handle_start))
        app.add_handler(CommandHandler("help", self.handle_help))
        app.add_handler(CommandHandler("new", self.handle_new))
        app.add_handler(CommandHandler("news", self.handle_news))
        app.add_handler(CommandHandler("addinterests", self.handle_add_interests))
        app.add_handler(CommandHandler("getinterests", self.handle_get_interests))
        app.add_handler(CommandHandler("updateinterests", self.handle_update_interests))
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

    def _is_authorized(self, update: Update) -> bool:
        allowed = self._config.allowed_user_ids
        if allowed is None:
            return True
        authorized = update.effective_user.id in allowed
        if not authorized:
            logger.warning("Ignoring message from unauthorized user %s", update.effective_user.id)
        return authorized

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

    # -- Command handlers -------------------------------------------------

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        await update.message.reply_text("Hi!")

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        await update.message.reply_text(self._help_text)

    async def handle_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        self._sessions.clear(update.effective_chat.id)
        await update.message.reply_text("Started a new conversation.")

    async def handle_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        chat_id = update.effective_chat.id
        if not self._password_gate.is_unlocked(chat_id):
            await update.message.reply_text("🔒 Locked. Send the password to start a session.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /news <subject>\nExample: /news cybersecurity")
            return

        subject = " ".join(context.args)
        reply = await self._run_claude(update, context, self._build_news_prompt(subject))
        if reply is not None:
            await self._send_formatted(update, reply)

    async def handle_add_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
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
        if not self._is_authorized(update):
            return
        entries = self._interests.list()
        if not entries:
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return
        listing = "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))
        await update.message.reply_text(listing)

    async def handle_update_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        chat_id = update.effective_chat.id
        if not self._password_gate.is_unlocked(chat_id):
            await update.message.reply_text("🔒 Locked. Send the password to start a session.")
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

    async def handle_unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
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
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str
    ) -> Optional[str]:
        """Runs prompt through Claude for this chat and returns the raw
        reply. Returns None if a busy/error message was already sent to the
        user and the caller should stop."""
        chat_id = update.effective_chat.id
        lock = self._claude.lock_for(chat_id)
        if lock.locked():
            await update.message.reply_text("Still working on your last request — hang on.")
            return None

        async with lock:
            keep_typing = asyncio.create_task(self._keep_typing(context, chat_id))
            try:
                return await self._claude.run(chat_id, prompt)
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


def main() -> None:
    NewsBot(Config.from_env()).run()


if __name__ == "__main__":
    main()
