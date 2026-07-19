import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


class ActionCoreTests(AsyncBotTestCase):
    CHAT_ID = 5

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_do_add_interest_saves_and_replies(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_interest(update, "alice", "cybersecurity")

        self.assertEqual(b._interests_for("alice").list(), ["cybersecurity"])
        self.assertTrue(update.message.replies)

    async def test_do_remove_interest_deletes(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 1)

        self.assertEqual(b._interests_for("alice").list(), [])

    async def test_slash_news_still_replies_usage_without_args(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_news(update, FakeContext(args=[]))

        self.assertIn("Usage: /news", update.message.replies[0])
