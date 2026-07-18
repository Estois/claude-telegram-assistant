import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class LogMessageTests(AsyncBotTestCase):
    CHAT_ID = 20
    USER_ID = 100

    def _make_bot(self, unlock=True, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        if unlock:
            news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        return news_bot

    def _update(self, text=None, username="bob", **kwargs):
        return FakeUpdate(
            user_id=self.USER_ID, chat_id=self.CHAT_ID, text=text, username=username, **kwargs
        )

    def _log_dir(self, news_bot):
        return news_bot._config.message_logs_dir

    async def test_unlocked_chat_message_is_logged_to_username_file(self):
        news_bot = self._make_bot()
        update = self._update(text="hello there", username="bob")

        await news_bot._log_message(update, FakeContext())

        contents = (self._log_dir(news_bot) / "bob.log").read_text()
        self.assertIn("hello there", contents)

    async def test_commands_are_logged_too(self):
        news_bot = self._make_bot()
        update = self._update(text="/news tokyo", username="bob")

        await news_bot._log_message(update, FakeContext())

        self.assertIn("/news tokyo", (self._log_dir(news_bot) / "bob.log").read_text())

    async def test_locked_chat_message_is_not_logged(self):
        # Pre-unlock messages are password attempts — they must never hit disk.
        news_bot = self._make_bot(unlock=False)
        update = self._update(text="hunter2", username="bob")

        await news_bot._log_message(update, FakeContext())

        self.assertFalse(self._log_dir(news_bot).exists())

    async def test_callback_update_without_a_message_is_ignored(self):
        news_bot = self._make_bot()
        update = self._update(text="ignored")
        update.message = None  # a callback-query update carries no message

        await news_bot._log_message(update, FakeContext())

        self.assertFalse(self._log_dir(news_bot).exists())

    async def test_logging_disabled_writes_nothing(self):
        news_bot = self._make_bot(message_logging_enabled=False)
        update = self._update(text="hello", username="bob")

        await news_bot._log_message(update, FakeContext())

        self.assertFalse(self._log_dir(news_bot).exists())

    async def test_user_without_username_uses_id_file(self):
        news_bot = self._make_bot()
        update = self._update(text="hi", username=None)

        await news_bot._log_message(update, FakeContext())

        path = self._log_dir(news_bot) / f"id_{self.USER_ID}.log"
        self.assertTrue(path.exists())

    async def test_non_text_message_is_recorded_with_a_placeholder(self):
        news_bot = self._make_bot()
        update = self._update(text=None, username="bob")  # e.g. a shared location

        await news_bot._log_message(update, FakeContext())

        self.assertIn("[non-text message]", (self._log_dir(news_bot) / "bob.log").read_text())
