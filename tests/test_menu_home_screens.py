import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class BusMenuHomeTests(AsyncBotTestCase):
    CHAT_ID = 71

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    def test_fav_menu_has_home_row(self):
        b = self._make_bot()
        _text, markup = b._build_fav_menu("alice")
        self.assertIn("home", datas(markup))

    def test_near_menu_has_home_row(self):
        b = self._make_bot()
        stops = [{"Description": "Opp Blk 1", "BusStopCode": "12345", "distance_m": 40}]
        _text, markup = b._build_near_menu(stops)
        self.assertIn("home", datas(markup))

    async def test_slash_bus_also_gets_home(self):
        """Unconditional: the same screen must not gain or lose a nav button
        depending on how the user reached it."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_bus(update, FakeContext())

        self.assertIn("home", datas(update.message.replies_markup[-1]))


class AdminGetUsersHomeTests(AsyncBotTestCase):
    CHAT_ID = 72

    async def test_get_users_listing_keeps_the_admin_menu(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"amy": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        update = FakeUpdate(
            user_id=100000001, chat_id=self.CHAT_ID, callback_data="act:getusers"
        )

        await b.handle_admin_action(update, FakeContext())

        self.assertIn("menu:root", datas(update.callback_query.last_markup))
