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
    def __init__(self, id, full_name=None, username=None):
        self.id = id
        self.full_name = full_name
        self.username = username


class FakeChat:
    def __init__(self, id):
        self.id = id


class FakeMessage:
    """Stand-in for telegram.Message: records reply_text calls instead of
    hitting the real Telegram API. text/message_id/location cover what the
    plain-text (password) and location handlers read off the message."""

    def __init__(self, text=None, message_id=1, location=None):
        self.replies: list = []
        self.replies_markup: list = []
        self.text = text
        self.message_id = message_id
        self.location = location

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.replies_markup.append(kwargs.get("reply_markup"))


class FakeCallbackQuery:
    """Stand-in for telegram.CallbackQuery: records answer() and
    edit_message_text() calls instead of hitting the real Telegram API."""

    def __init__(self, data: str, message: "FakeMessage"):
        self.data = data
        self.message = message
        self.answers: list = []
        self.edits: list = []  # (text, reply_markup) tuples

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))

    @property
    def last_text(self):
        return self.edits[-1][0] if self.edits else None

    @property
    def last_markup(self):
        return self.edits[-1][1] if self.edits else None


class FakeUpdate:
    """Minimal stand-in for telegram.Update, covering what YKBot's
    handlers actually touch: effective_user/effective_chat for
    authorization and account lookup, message/effective_message for
    replies (effective_message is used so this also works for the
    callback-query handlers, where .message is None on a real Update).

    Pass callback_data to also attach a FakeCallbackQuery, for testing the
    inline-button (callback-query) handlers."""

    def __init__(
        self,
        user_id: int,
        chat_id: int,
        callback_data: str = None,
        text: str = None,
        message_id: int = 1,
        location=None,
        full_name: str = None,
        username: str = None,
    ):
        self.effective_user = FakeUser(user_id, full_name=full_name, username=username)
        self.effective_chat = FakeChat(chat_id)
        self.message = FakeMessage(text=text, message_id=message_id, location=location)
        self.effective_message = self.message
        self.callback_query = (
            FakeCallbackQuery(callback_data, self.message)
            if callback_data is not None
            else None
        )


class FakeBot:
    """Stand-in for the telegram.Bot handed to handlers as context.bot;
    records send_message/delete_message calls for assertions."""

    def __init__(self):
        self.sent: list = []  # (chat_id, text) tuples
        self.deleted: list = []  # (chat_id, message_id) tuples
        self.actions: list = []  # (chat_id, action) tuples

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class FakeContext:
    def __init__(self, args=None, user_data=None):
        self.args = args or []
        self.bot = FakeBot()
        self.user_data = user_data if user_data is not None else {}


def make_config(tmp_path: Path, **overrides) -> "bot.Config":
    """Builds a Config with every file/dir rooted under tmp_path, so tests
    that need a full YKBot instance never touch real repo data. Pass
    keyword overrides for anything a specific test cares about."""
    defaults = dict(
        base_dir=tmp_path,
        telegram_token="test-token",
        # The allowlist fails closed in production; default the test config to
        # allow the ids the suite uses so tests aren't all silently blocked.
        # Tests that exercise authorization pass their own allowed_user_ids.
        allowed_user_ids=frozenset(range(0, 1000)) | {12345, 111111, 100000001},
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
        trivia_cache_file=tmp_path / "trivia_cache.json",
        trivia_state_file=tmp_path / "trivia_state.json",
        lta_api_key=None,
        bus_stops_cache_file=tmp_path / "bus_stops_cache.json",
        bus_nearby_count=3,
        news_learning_enabled=True,
        news_history_window_days=60,
        news_history_max_entries=100,
        message_logging_enabled=True,
        message_logs_dir=tmp_path / "message_logs",
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
