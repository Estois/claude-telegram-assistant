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
        news_bot = bot.YKBot(make_config(self.tmp_path, **config_overrides))
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
        news_bot = bot.YKBot(make_config(self.tmp_path, **config_overrides))
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
        news_bot = bot.YKBot(
            make_config(self.tmp_path, admin_account_id="amy", admin_telegram_user_id=999)
        )
        write_accounts_file(news_bot._config.accounts_file, {"amy": ["adminpass"]})
        update = FakeUpdate(user_id=999, chat_id=1)

        account_id = await news_bot._require_admin(update)

        self.assertIsNone(account_id)
        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("fact:", update.message.replies[0])


class RequireClosedTests(AsyncBotTestCase):
    def _make_bot(self, closed_account_ids=frozenset({"amy", "beth"})):
        news_bot = bot.YKBot(
            make_config(self.tmp_path, closed_account_ids=closed_account_ids)
        )
        write_accounts_file(
            news_bot._config.accounts_file, {"amy": ["amypass"], "beth": ["bethpass"], "dave": ["davepass"]}
        )
        return news_bot

    async def test_member_account_passes(self):
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=1, attempt="bethpass")
        update = FakeUpdate(user_id=1, chat_id=1)

        account_id = await news_bot._require_closed(update)

        self.assertEqual(account_id, "beth")
        self.assertEqual(update.message.replies, [])

    async def test_non_member_account_gets_trivia_not_a_hint(self):
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=1, attempt="davepass")
        update = FakeUpdate(user_id=1, chat_id=1)

        account_id = await news_bot._require_closed(update)

        self.assertIsNone(account_id)
        self.assertEqual(len(update.message.replies), 1)
        reply = update.message.replies[0]
        self.assertIn("fact:", reply)
        self.assertNotIn("closed", reply.lower())

    async def test_admin_account_qualifies_by_membership_no_telegram_id_needed(self):
        # amy is in closed_account_ids regardless of who unlocked it — the
        # Telegram-user-id dual-check is admin-only hardening, not required
        # for the Closed tier.
        news_bot = self._make_bot()
        news_bot._accounts.try_unlock(chat_id=1, attempt="amypass")
        update = FakeUpdate(user_id=1, chat_id=1)  # not the admin telegram id

        account_id = await news_bot._require_closed(update)

        self.assertEqual(account_id, "amy")

    async def test_locked_chat_gets_trivia_not_a_lock_notice(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)

        account_id = await news_bot._require_closed(update)

        self.assertIsNone(account_id)
        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("fact:", update.message.replies[0])


if __name__ == "__main__":
    import unittest

    unittest.main()
