import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import bot


class BotTestCase(unittest.TestCase):
    """Base class providing a scratch temp directory per test
    (self.tmp_path), cleaned up automatically after each test runs."""

    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, ignore_errors=True)


class AsyncBotTestCase(unittest.IsolatedAsyncioTestCase):
    """Like BotTestCase, but for tests that need to await YKBot's async
    handler methods."""

    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, ignore_errors=True)


class FakeUser:
    def __init__(self, id):
        self.id = id


class FakeChat:
    def __init__(self, id):
        self.id = id


class FakeMessage:
    """Stand-in for telegram.Message: records reply_text calls instead of
    hitting the real Telegram API."""

    def __init__(self):
        self.replies: list = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    """Minimal stand-in for telegram.Update, covering what YKBot's
    handlers actually touch: effective_user/effective_chat for
    authorization and account lookup, message/effective_message for
    replies (effective_message is used so this also works for the
    callback-query handlers, where .message is None on a real Update)."""

    def __init__(self, user_id: int, chat_id: int):
        self.effective_user = FakeUser(user_id)
        self.effective_chat = FakeChat(chat_id)
        self.message = FakeMessage()
        self.effective_message = self.message


class FakeBot:
    """Stand-in for the telegram.Bot handed to handlers as context.bot."""

    async def send_chat_action(self, chat_id, action):
        pass

    async def send_message(self, chat_id, text, **kwargs):
        pass

    async def delete_message(self, chat_id, message_id):
        pass


class FakeContext:
    def __init__(self, args=None):
        self.args = args or []
        self.bot = FakeBot()


def make_config(tmp_path: Path, **overrides) -> "bot.Config":
    """Builds a Config with every file/dir rooted under tmp_path, so tests
    that need a full YKBot instance never touch real repo data. Pass
    keyword overrides for anything a specific test cares about."""
    defaults = dict(
        base_dir=tmp_path,
        telegram_token="test-token",
        allowed_user_ids=None,
        env_file=tmp_path / ".env",
        admin_account_id="amy",
        admin_telegram_user_id=100000001,
        claude_bin="claude",
        claude_workdir=str(tmp_path),
        claude_model=None,
        claude_permission_mode="bypassPermissions",
        claude_timeout_seconds=600,
        accounts_dir=tmp_path / "accounts",
        interests_reorganize_interval_seconds=86400,
        accounts_file=tmp_path / "accounts.hash",
        session_timeout_seconds=900,
        sessions_file=tmp_path / "sessions.json",
        trivia_cache_file=tmp_path / "trivia_cache.json",
        trivia_state_file=tmp_path / "trivia_state.json",
        lta_api_key=None,
        bus_stops_cache_file=tmp_path / "bus_stops_cache.json",
        bus_nearby_count=3,
        news_learning_enabled=True,
        news_history_window_days=60,
        news_history_max_entries=100,
    )
    defaults.update(overrides)
    return bot.Config(**defaults)


def write_accounts_file(path, accounts: dict) -> None:
    """Writes an accounts.hash-format file. accounts maps account id ->
    list of plaintext passwords (hashed here so tests stay readable)."""
    lines = []
    for account_id, passwords in accounts.items():
        lines.append(f"# account: {account_id}")
        for password in passwords:
            lines.append(hashlib.sha256(password.encode()).hexdigest())
    path.write_text("\n".join(lines) + "\n")
