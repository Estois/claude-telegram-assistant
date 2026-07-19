import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class MenuTests(AsyncBotTestCase):
    CHAT_ID = 9
    USER = 100
    ADMIN_USER = 100000001

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"], "amy": ["adm"]})
        return b

    def _unlock(self, b, chat_id, pw):
        b._accounts.try_unlock(chat_id=chat_id, attempt=pw)

    async def test_menu_command_shows_top_level_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        markup = update.message.replies_markup[-1]  # see helper note below
        self.assertIn("act:news", datas(markup))
        self.assertNotIn("menu:admin", datas(markup))

    async def test_admin_sees_admin_row(self):
        b = self._make_bot()
        self._unlock(b, 1, "adm")  # amy account on admin chat
        update = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1)

        await b.handle_menu(update, FakeContext())

        self.assertIn("menu:admin", datas(update.message.replies_markup[-1]))

    async def test_locked_user_gets_no_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        # A locked user still gets a trivia reply via _require_session (no
        # reply_markup kwarg), which FakeMessage.reply_text records as a
        # bare None entry — so check no *real* markup was attached, not
        # that reply_text was never called.
        self.assertFalse(any(getattr(update.message, "replies_markup", [])))

    async def test_nav_to_interests_edits_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:interests")

        await b.handle_menu_nav(update, FakeContext())

        self.assertIn("int_pick:rm", datas(update.callback_query.last_markup))

    async def test_nav_to_admin_as_admin_shows_admin_board(self):
        b = self._make_bot()
        self._unlock(b, 1, "adm")  # amy account on admin chat
        update = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1, callback_data="menu:admin")

        await b.handle_menu_nav(update, FakeContext())

        board = datas(update.callback_query.last_markup)
        self.assertIn("act:getusers", board)
        self.assertIn("adm_pick_rm", board)

    async def test_nav_to_admin_as_non_admin_is_rejected(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")  # alice account, non-admin
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:admin")

        await b.handle_menu_nav(update, FakeContext())

        markup = update.callback_query.last_markup
        board = datas(markup) if markup else []
        self.assertNotIn("act:getusers", board)
        self.assertNotIn("adm_pick_rm", board)

    async def test_nav_to_root_rerenders_top_level_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")  # alice account, non-admin
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:root")

        await b.handle_menu_nav(update, FakeContext())

        board = datas(update.callback_query.last_markup)
        self.assertIn("act:news", board)
        self.assertIn("menu:interests", board)
        self.assertNotIn("menu:admin", board)

    async def test_menu_bus_opens_fav_menu(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:bus")
        context = FakeContext()

        await b.handle_menu_bus(update, context)

        self.assertTrue(update.message.replies)
        self.assertEqual(context.user_data["bus_menu"], {"kind": "fav"})

    async def test_locked_user_nav_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:interests")

        await b.handle_menu_nav(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertNotIn("int_pick:rm", datas(update.callback_query.last_markup) if update.callback_query.last_markup else [])
        self.assertIsNone(update.callback_query.last_text)

    async def test_locked_user_menu_bus_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:bus")
        context = FakeContext()

        await b.handle_menu_bus(update, context)

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertNotIn("bus_menu", context.user_data)
        # A locked user still gets a trivia reply via _require_session (no
        # reply_markup kwarg) — the fav-menu keyboard must not appear.
        self.assertFalse(any(update.message.replies_markup))

    async def test_action_getinterests_lists(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("weather")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("weather", update.message.replies[-1])

    async def test_action_compile_reports_need_more_interests(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:compile")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("Need at least 2 interests", update.message.replies[-1])

    async def test_endsession_asks_confirm_then_locks(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        confirm = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:endsession")
        await b.handle_menu_action(confirm, FakeContext())
        self.assertIn("cfm:endsession",
                      [b_.callback_data for row in confirm.callback_query.last_markup.inline_keyboard for b_ in row])

        yes = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cfm:endsession")
        await b.handle_confirm(yes, FakeContext())
        self.assertIsNone(b._accounts.account_for(self.CHAT_ID))

    async def test_cancel_dismisses(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cxl:endsession")

        await b.handle_cancel(update, FakeContext())

        self.assertIsNotNone(b._accounts.account_for(self.CHAT_ID))  # still unlocked
        self.assertTrue(update.callback_query.edits)

    async def test_locked_user_menu_action_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        # A locked user still gets a trivia reply via _require_session (no
        # reply_markup kwarg) — no real interests listing must appear.
        self.assertFalse(any(update.message.replies_markup))

    async def test_locked_user_confirm_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cfm:endsession")

        await b.handle_confirm(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertIsNone(update.callback_query.last_text)

    async def test_locked_user_cancel_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cxl:endsession")

        await b.handle_cancel(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertIsNone(update.callback_query.last_text)
