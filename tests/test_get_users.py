import bot
from tests.helpers import (
    AsyncBotTestCase,
    BotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class FormatUserListingTests(BotTestCase):
    def _make_bot(self, **config_overrides):
        return bot.YKBot(make_config(self.tmp_path, **config_overrides))

    def test_no_accounts_configured(self):
        news_bot = self._make_bot()

        self.assertEqual(news_bot._format_user_listing(), "No accounts configured.")

    def test_lists_every_account_as_locked_when_none_unlocked(self):
        news_bot = self._make_bot()
        write_accounts_file(
            news_bot._config.accounts_file, {"alice": ["hunter2"], "bob": ["swordfish"]}
        )

        listing = news_bot._format_user_listing()

        self.assertIn("alice — 🔒 locked", listing)
        self.assertIn("bob — 🔒 locked", listing)

    def test_marks_an_unlocked_account_as_active(self):
        news_bot = self._make_bot()
        write_accounts_file(
            news_bot._config.accounts_file, {"alice": ["hunter2"], "bob": ["swordfish"]}
        )
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")

        listing = news_bot._format_user_listing()

        self.assertIn("alice — 🟢 active", listing)
        self.assertIn("bob — 🔒 locked", listing)


class HandleGetUsersTests(AsyncBotTestCase):
    ADMIN_TELEGRAM_USER_ID = 999

    def _make_bot(self):
        news_bot = bot.YKBot(
            make_config(
                self.tmp_path,
                admin_account_id="amy",
                admin_telegram_user_id=self.ADMIN_TELEGRAM_USER_ID,
            )
        )
        write_accounts_file(
            news_bot._config.accounts_file, {"amy": ["adminpass"], "alice": ["hunter2"]}
        )
        return news_bot

    async def test_admin_session_gets_the_user_listing(self):
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=1, attempt="adminpass")
        update = FakeUpdate(user_id=self.ADMIN_TELEGRAM_USER_ID, chat_id=1)

        await news_bot.handle_get_users(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("amy — 🟢 active", update.message.replies[0])
        self.assertIn("alice — 🔒 locked", update.message.replies[0])

    async def test_non_admin_account_gets_trivia_instead_of_the_listing(self):
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=2, attempt="hunter2")
        update = FakeUpdate(user_id=self.ADMIN_TELEGRAM_USER_ID, chat_id=2)

        await news_bot.handle_get_users(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        self.assertNotIn("🔒 locked", update.message.replies[0])
        self.assertNotIn("🟢 active", update.message.replies[0])

    async def test_admin_account_from_wrong_telegram_user_gets_trivia_instead(self):
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=1, attempt="adminpass")
        update = FakeUpdate(user_id=111111, chat_id=1)

        await news_bot.handle_get_users(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        self.assertNotIn("🟢 active", update.message.replies[0])
        self.assertNotIn("🔒 locked", update.message.replies[0])

    async def test_locked_chat_gets_trivia_not_a_lock_notice_or_the_listing(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=self.ADMIN_TELEGRAM_USER_ID, chat_id=1)

        await news_bot.handle_get_users(update, FakeContext())

        self.assertEqual(len(update.message.replies), 1)
        reply = update.message.replies[0]
        self.assertNotIn("🔒 locked", reply)
        self.assertNotIn("🟢 active", reply)
        self.assertNotIn("Locked", reply)
        self.assertIn("fact:", reply)


if __name__ == "__main__":
    import unittest

    unittest.main()
