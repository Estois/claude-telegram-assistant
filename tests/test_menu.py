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
