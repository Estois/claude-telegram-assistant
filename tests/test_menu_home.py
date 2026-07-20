import unittest

from telegram.ext import CallbackQueryHandler

import bot
from ykbot import menus
from tests.helpers import (
    AsyncBotTestCase, BotTestCase, FakeContext, FakeUpdate, make_config,
    write_accounts_file,
)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class HomeKeyboardTests(unittest.TestCase):
    def test_is_a_single_home_button(self):
        rows = menus.home_keyboard().inline_keyboard
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].callback_data, "home")
        self.assertIn("Menu", rows[0][0].text)


class HandleMenuHomeTests(AsyncBotTestCase):
    CHAT_ID = 41

    def _make_bot(self, account="alice"):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {account: ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_posts_a_new_message_and_never_edits(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertEqual(update.callback_query.edits, [])
        self.assertEqual(update.message.replies[-1], "What would you like to do?")
        self.assertIn("act:bus", datas(update.message.replies_markup[-1]))

    async def test_answers_the_callback_query(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertEqual(len(update.callback_query.answers), 1)

    async def test_locked_chat_gets_trivia_not_a_menu(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        update = FakeUpdate(user_id=1, chat_id=99, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertIsNone(update.message.replies_markup[-1])
        self.assertEqual(update.callback_query.edits, [])

    async def test_admin_session_gets_the_admin_row(self):
        b = self._make_bot(account="amy")
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertIn("menu:admin", datas(update.message.replies_markup[-1]))

    async def test_non_admin_session_has_no_admin_row(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertNotIn("menu:admin", datas(update.message.replies_markup[-1]))

    async def test_clears_pending_awaiting_capture(self):
        """Tapping 🏠 Home while a 'please type X' capture is armed must
        cancel it — otherwise the next unrelated message could be silently
        swallowed as the answer to a prompt the user navigated away from."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="home")
        ctx = FakeContext(user_data={"awaiting": {"kind": "news_subject", "payload": {}}})

        await b.handle_menu_home(update, ctx)

        self.assertNotIn("awaiting", ctx.user_data)


class HomeHandlerRegistrationTests(BotTestCase):
    def test_home_pattern_is_registered(self):
        b = bot.YKBot(make_config(self.tmp_path))
        app = b._build_application()
        patterns = [
            h.pattern.pattern
            for group in app.handlers.values()
            for h in group
            if isinstance(h, CallbackQueryHandler)
        ]
        self.assertIn(r"^home$", patterns)
