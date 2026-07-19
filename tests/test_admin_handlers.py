import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)

VALID_HASH = "a" * 64


class AdminHandlerTests(AsyncBotTestCase):
    ADMIN_UID = 999

    def _make_bot(self):
        news_bot = bot.YKBot(
            make_config(
                self.tmp_path, admin_account_id="amy", admin_telegram_user_id=self.ADMIN_UID
            )
        )
        write_accounts_file(news_bot._config.accounts_file, {"amy": ["adminpass"]})
        news_bot._accounts.try_unlock(chat_id=1, attempt="adminpass")
        return news_bot

    def _admin_update(self):
        return FakeUpdate(user_id=self.ADMIN_UID, chat_id=1)

    async def test_adduser_wrong_arg_count_shows_usage(self):
        news_bot = self._make_bot()
        update = self._admin_update()

        await news_bot.handle_add_user(update, FakeContext(args=["onlyname"]))

        self.assertIn("Usage: /adduser", update.message.replies[0])

    async def test_adduser_rejects_a_non_sha256_hash(self):
        news_bot = self._make_bot()
        update = self._admin_update()

        await news_bot.handle_add_user(update, FakeContext(args=["bob", "not-a-hash"]))

        self.assertIn("SHA256", update.message.replies[0])
        self.assertNotIn("bob", news_bot._accounts.list_ids())

    async def test_adduser_creates_the_account(self):
        news_bot = self._make_bot()
        update = self._admin_update()

        await news_bot.handle_add_user(update, FakeContext(args=["bob", VALID_HASH]))

        self.assertEqual(update.message.replies, ["Created account 'bob' with 1 password."])
        self.assertIn("bob", news_bot._accounts.list_ids())

    async def test_removeuser_refuses_to_remove_admin_account(self):
        news_bot = self._make_bot()
        update = self._admin_update()

        await news_bot.handle_remove_user(update, FakeContext(args=["amy"]))

        self.assertEqual(update.message.replies, ["Refusing to remove the admin account."])
        self.assertIn("amy", news_bot._accounts.list_ids())

    async def test_adduserteleid_adds_then_reports_already_allowed(self):
        news_bot = self._make_bot()
        # Add a fresh id not already on the allowlist; the admin (999) stays
        # allowed throughout, so the second call still reaches the handler.
        arg = ["424242"]

        update1 = self._admin_update()
        await news_bot.handle_add_user_tele_id(update1, FakeContext(args=arg))
        update2 = self._admin_update()
        await news_bot.handle_add_user_tele_id(update2, FakeContext(args=arg))

        self.assertIn("Added", update1.message.replies[0])
        self.assertTrue(news_bot._allowlist.is_allowed(self.ADMIN_UID))
        self.assertIn("already allowed", update2.message.replies[0])

    async def test_non_admin_caller_gets_trivia_and_no_account_created(self):
        news_bot = self._make_bot()
        # Right admin account is unlocked on chat 1, but this is a different
        # Telegram user id, so _require_admin must reject it.
        update = FakeUpdate(user_id=111111, chat_id=1)

        await news_bot.handle_add_user(update, FakeContext(args=["bob", VALID_HASH]))

        self.assertIn("fact:", update.message.replies[0])
        self.assertNotIn("bob", news_bot._accounts.list_ids())
