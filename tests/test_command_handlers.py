import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class CommandHandlerTests(AsyncBotTestCase):
    CHAT_ID = 5
    USER_ID = 100

    def _make_bot(self, **config_overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **config_overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        return news_bot

    def _update(self):
        return FakeUpdate(user_id=self.USER_ID, chat_id=self.CHAT_ID)

    async def test_start_greets_an_unlocked_user(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_start(update, FakeContext())

        self.assertEqual(update.message.replies, ["Hi!"])

    async def test_help_reports_the_session_timeout(self):
        news_bot = self._make_bot(session_timeout_seconds=1800)
        update = self._update()

        await news_bot.handle_help(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("30 minutes", update.message.replies[0])

    async def test_clear_claude_memory_forgets_the_chats_session(self):
        news_bot = self._make_bot()
        news_bot._sessions.set(self.CHAT_ID, "session-abc")
        update = self._update()

        await news_bot.handle_clear_claude_memory(update, FakeContext())

        self.assertEqual(update.message.replies, ["Cleared Claude's memory."])
        self.assertIsNone(news_bot._sessions.get(self.CHAT_ID))

    async def test_add_interests_without_args_shows_usage(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_add_interests(update, FakeContext(args=[]))

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("Usage: /addinterests", update.message.replies[0])

    async def test_add_interests_stores_the_topic(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_add_interests(update, FakeContext(args=["cybersecurity"]))

        self.assertEqual(update.message.replies, ["Added as interest #1."])
        self.assertEqual(news_bot._interests_for("alice").list(), ["cybersecurity"])

    async def test_get_interests_reports_empty_list(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_get_interests(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("No interests saved yet", update.message.replies[0])

    async def test_unknown_command_replies_with_trivia_not_an_error(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_unknown_command(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        reply = update.message.replies[0]
        self.assertIn("fact:", reply)
        self.assertNotIn("Unknown command", reply)


if __name__ == "__main__":
    import unittest

    unittest.main()
