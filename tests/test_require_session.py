import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class RequireSessionTests(AsyncBotTestCase):
    def _make_bot(self, **config_overrides):
        news_bot = bot.NewsBot(make_config(self.tmp_path, **config_overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        return news_bot

    async def test_returns_account_id_when_unlocked(self):
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")
        update = FakeUpdate(user_id=1, chat_id=1)

        account_id = await news_bot._require_session(update)

        self.assertEqual(account_id, "alice")
        self.assertEqual(update.message.replies, [])

    async def test_locked_chat_gets_trivia_not_a_lock_notice(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)

        account_id = await news_bot._require_session(update)

        self.assertIsNone(account_id)
        self.assertEqual(len(update.message.replies), 1)
        reply = update.message.replies[0]
        self.assertNotIn("Locked", reply)
        self.assertNotIn("password", reply)
        self.assertIn("fact:", reply)

    async def test_unauthorized_sender_gets_silence_not_trivia(self):
        news_bot = self._make_bot(allowed_user_ids=frozenset({42}))
        update = FakeUpdate(user_id=999, chat_id=1)

        account_id = await news_bot._require_session(update)

        self.assertIsNone(account_id)
        self.assertEqual(update.message.replies, [])


class HandleUnknownCommandTests(AsyncBotTestCase):
    def _make_bot(self, **config_overrides):
        news_bot = bot.NewsBot(make_config(self.tmp_path, **config_overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        return news_bot

    async def test_locked_chat_gets_a_single_trivia_reply(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)

        await news_bot.handle_unknown_command(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("fact:", update.message.replies[0])

    async def test_unlocked_chat_also_gets_trivia_for_an_unrecognized_command(self):
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")
        update = FakeUpdate(user_id=1, chat_id=1)

        await news_bot.handle_unknown_command(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("fact:", update.message.replies[0])


class RequireAdminLockedCallerTests(AsyncBotTestCase):
    async def test_locked_caller_gets_trivia_not_a_lock_notice(self):
        news_bot = bot.NewsBot(
            make_config(self.tmp_path, admin_account_id="amy", admin_telegram_user_id=999)
        )
        write_accounts_file(news_bot._config.accounts_file, {"amy": ["adminpass"]})
        update = FakeUpdate(user_id=999, chat_id=1)

        account_id = await news_bot._require_admin(update)

        self.assertIsNone(account_id)
        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("fact:", update.message.replies[0])


if __name__ == "__main__":
    import unittest

    unittest.main()
