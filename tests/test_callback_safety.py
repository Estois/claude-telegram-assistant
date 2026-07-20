"""Drives callback-reachable handlers with callback_only=True FakeUpdates,
which model a real Telegram callback-query update: update.message is None,
and only update.effective_message / update.callback_query.message exist.

A handler that replies via update.message.reply_text instead of
update.effective_message.reply_text passes the rest of the suite (where
FakeUpdate's default aliases .message and .effective_message to the same
object) but raises AttributeError the first time a real button reaches it in
production. That has already happened twice in this project (_require_admin,
_run_claude). These tests close that gap structurally: any handler that
regresses to update.message fails here with
'NoneType' object has no attribute 'reply_text'."""

import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def home_data(markup):
    return None if markup is None else markup.inline_keyboard[0][0].callback_data


class FakeUpdateGuardTests(AsyncBotTestCase):
    async def test_callback_only_without_callback_data_raises(self):
        with self.assertRaises(ValueError):
            FakeUpdate(user_id=1, chat_id=1, callback_only=True)


class CallbackSafetyTests(AsyncBotTestCase):
    CHAT_ID = 61
    USER_ID = 1
    ADMIN_USER_ID = 100000001

    def _make_bot(self, accounts=None):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(
            b._config.accounts_file, accounts or {"alice": ["pw"], "amy": ["pw2"]}
        )
        return b

    def _unlock(self, b, account="alice"):
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw" if account == "alice" else "pw2")

    def _update(self, callback_data, user_id=None):
        return FakeUpdate(
            user_id=user_id or self.USER_ID,
            chat_id=self.CHAT_ID,
            callback_data=callback_data,
            callback_only=True,
        )

    # -- handle_menu_home --------------------------------------------------

    async def test_menu_home(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("home")

        await b.handle_menu_home(update, FakeContext())

        self.assertEqual(update.effective_message.replies[-1], "What would you like to do?")
        self.assertIsNone(update.message)

    # -- handle_menu_nav -----------------------------------------------------

    async def test_menu_nav_root(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("menu:root")

        await b.handle_menu_nav(update, FakeContext())

        self.assertEqual(update.callback_query.last_text, "What would you like to do?")

    async def test_menu_nav_interests(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("menu:interests")

        await b.handle_menu_nav(update, FakeContext())

        self.assertEqual(update.callback_query.last_text, "Interests:")

    async def test_menu_nav_admin_non_admin_gets_trivia(self):
        b = self._make_bot()
        self._unlock(b)  # unlocks "alice", which is not the admin account
        update = self._update("menu:admin")

        await b.handle_menu_nav(update, FakeContext())

        self.assertIsNotNone(update.callback_query.last_text)
        self.assertIn("fact:", update.callback_query.last_text)

    # -- handle_menu_action ---------------------------------------------------

    async def test_menu_action_getinterests_populated(self):
        b = self._make_bot()
        self._unlock(b)
        b._interests_for("alice").add("space")
        update = self._update("act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertEqual(home_data(update.effective_message.replies_markup[-1]), "home")
        self.assertIn("space", update.effective_message.replies[-1])

    async def test_menu_action_getinterests_empty(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("No interests saved yet", update.effective_message.replies[-1])
        self.assertEqual(home_data(update.effective_message.replies_markup[-1]), "home")

    async def test_menu_action_compile(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("act:compile")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("Need at least 2 interests", update.effective_message.replies[-1])
        self.assertEqual(home_data(update.effective_message.replies_markup[-1]), "home")

    # -- handle_menu_bus -------------------------------------------------------

    async def test_menu_bus(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("act:bus")

        await b.handle_menu_bus(update, FakeContext())

        self.assertEqual(update.effective_message.replies[-1], "Pick a stop:")
        self.assertIsNone(update.message)

    # -- handle_confirm ---------------------------------------------------------

    async def test_confirm_remove_interest(self):
        b = self._make_bot()
        self._unlock(b)
        b._interests_for("alice").add("gone")
        update = self._update("cfm:int_rm:1")

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(home_data(update.effective_message.replies_markup[-1]), "home")
        self.assertIn("Removed interest #1", update.effective_message.replies[-1])

    async def test_confirm_end_session(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("cfm:endsession")

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(update.callback_query.last_text, "Session ended.")
        self.assertFalse(b._accounts.has_active_session("alice"))

    # -- handle_admin_action / handle_admin_select_remove -----------------------

    async def test_admin_action_getusers(self):
        b = self._make_bot()
        self._unlock(b, account="amy")
        update = self._update("act:getusers", user_id=self.ADMIN_USER_ID)

        await b.handle_admin_action(update, FakeContext())

        self.assertIn("alice", update.callback_query.last_text)
        self.assertIn("amy", update.callback_query.last_text)

    async def test_admin_select_remove(self):
        b = self._make_bot()
        self._unlock(b, account="amy")
        update = self._update("adm_rm:alice", user_id=self.ADMIN_USER_ID)

        await b.handle_admin_select_remove(update, FakeContext())

        self.assertIn("Remove account 'alice'?", update.callback_query.last_text)

    # -- handle_cancel ------------------------------------------------------------

    async def test_cancel(self):
        b = self._make_bot()
        self._unlock(b)
        update = self._update("cxl:endsession")

        await b.handle_cancel(update, FakeContext())

        self.assertEqual(update.callback_query.last_text, "Cancelled.")

    # -- locked-user path -----------------------------------------------------------

    async def test_locked_user_gets_trivia_via_effective_message(self):
        b = self._make_bot()
        # Chat never unlocked: _require_session must reply via
        # effective_message, since update.message is None on a real
        # callback update.
        update = self._update("home")

        await b.handle_menu_home(update, FakeContext())

        self.assertTrue(update.effective_message.replies, "expected a trivia reply")
        self.assertIn("fact:", update.effective_message.replies[0])
        self.assertIsNone(update.message)


class BusHandlerCallbackSafetyTests(AsyncBotTestCase):
    """The bus handlers moved modules; re-pin that they still reply via
    effective_message. A real callback update has message = None."""

    CHAT_ID = 9100

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_bus_back_replies_without_a_message(self):
        b = self._make_bot()
        update = FakeUpdate(
            user_id=1, chat_id=self.CHAT_ID, callback_only=True, callback_data="bus_back"
        )

        await b._bus.handle_bus_back(update, FakeContext())

        self.assertIsNone(update.message)

    async def test_bus_add_by_code_replies_without_a_message(self):
        b = self._make_bot()
        update = FakeUpdate(
            user_id=1, chat_id=self.CHAT_ID, callback_only=True, callback_data="bus_addbycode"
        )

        await b._bus.handle_bus_add_by_code(update, FakeContext())

        self.assertIsNone(update.message)
