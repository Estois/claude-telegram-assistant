import bot
from tests.helpers import (
    AsyncBotTestCase,
    BotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


def callback_datas(markup):
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def button_texts(markup):
    return [btn.text for row in markup.inline_keyboard for btn in row]


class ArrivalsKeyboardTests(BotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    def test_keyboard_has_refresh_back_and_favorite_buttons(self):
        news_bot = self._make_bot()

        markup = news_bot._build_arrivals_keyboard("46009", "alice")

        datas = callback_datas(markup)
        self.assertIn("bus_refresh:46009", datas)
        self.assertIn("bus_back", datas)
        self.assertIn("bus_fav_add:46009", datas)  # not yet a favorite

    def test_keyboard_shows_remove_when_stop_is_already_a_favorite(self):
        news_bot = self._make_bot()
        news_bot._bus_locations_for("alice").add("Home", "46009")

        markup = news_bot._build_arrivals_keyboard("46009", "alice")

        datas = callback_datas(markup)
        self.assertIn("bus_fav_remove:46009", datas)
        self.assertNotIn("bus_fav_add:46009", datas)
        # Refresh/Back still present regardless of favorite state.
        self.assertIn("bus_refresh:46009", datas)
        self.assertIn("bus_back", datas)


class MenuBuilderTests(BotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    def test_build_fav_menu_lists_favorites_and_a_location_button(self):
        news_bot = self._make_bot()
        news_bot._bus_locations_for("alice").add("Home", "46009")

        text, markup = news_bot._build_fav_menu("alice")

        self.assertEqual(text, "Pick a stop:")
        self.assertIn("bus_stop:46009", callback_datas(markup))
        self.assertIn("bus_request_location", callback_datas(markup))

    def test_build_near_menu_lists_each_stop_with_distance(self):
        news_bot = self._make_bot()
        stops = [
            {"Description": "Opp Blk 1", "BusStopCode": "46009", "distance_m": 42.0},
            {"Description": "Blk 22", "BusStopCode": "46008", "distance_m": 120.9},
        ]

        text, markup = news_bot._build_near_menu(stops)

        self.assertEqual(text, "Nearest stops:")
        self.assertEqual(
            callback_datas(markup), ["bus_stop:46009", "bus_stop:46008"]
        )
        self.assertIn("Opp Blk 1 (42m)", button_texts(markup))


class _BusBotMixin:
    CHAT_ID = 7

    def _make_bot(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        # Deterministic arrivals text, independent of LTA config/network.

        async def fake_arrivals(stop_code):
            return f"Arrivals for {stop_code}"

        news_bot._format_arrivals = fake_arrivals
        return news_bot

    def _update(self, callback_data=None):
        return FakeUpdate(user_id=100, chat_id=self.CHAT_ID, callback_data=callback_data)


class MenuContextTests(_BusBotMixin, AsyncBotTestCase):
    async def test_handle_bus_records_fav_menu_context(self):
        news_bot = self._make_bot()
        ctx = FakeContext()
        update = self._update()

        await news_bot.handle_bus(update, ctx)

        self.assertEqual(ctx.user_data["bus_menu"], {"kind": "fav"})
        self.assertEqual(update.message.replies, ["Pick a stop:"])


class RefreshTests(_BusBotMixin, AsyncBotTestCase):
    async def test_refresh_re_renders_arrivals_with_timestamp_footer(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_refresh:46009")

        await news_bot.handle_bus_refresh(update, FakeContext())

        text = update.callback_query.last_text
        self.assertTrue(text.startswith("Arrivals for 46009"))
        self.assertIn("🔄 Updated ", text)
        self.assertIn("bus_back", callback_datas(update.callback_query.last_markup))


class BackTests(_BusBotMixin, AsyncBotTestCase):
    async def test_back_to_fav_menu_edits_message_to_preferred_stops(self):
        news_bot = self._make_bot()
        news_bot._bus_locations_for("alice").add("Home", "46009")
        ctx = FakeContext(user_data={"bus_menu": {"kind": "fav"}})
        update = self._update(callback_data="bus_back")

        await news_bot.handle_bus_back(update, ctx)

        self.assertEqual(update.callback_query.last_text, "Pick a stop:")
        self.assertIn("bus_stop:46009", callback_datas(update.callback_query.last_markup))

    async def test_back_to_near_menu_rebuilds_stored_stops(self):
        news_bot = self._make_bot()
        stops = [{"Description": "Opp Blk 1", "BusStopCode": "46009", "distance_m": 42.0}]
        ctx = FakeContext(user_data={"bus_menu": {"kind": "near", "stops": stops}})
        update = self._update(callback_data="bus_back")

        await news_bot.handle_bus_back(update, ctx)

        self.assertEqual(update.callback_query.last_text, "Nearest stops:")
        self.assertIn("bus_stop:46009", callback_datas(update.callback_query.last_markup))

    async def test_back_falls_back_to_fav_menu_when_context_lost(self):
        news_bot = self._make_bot()
        ctx = FakeContext()  # no bus_menu (e.g. bot restarted)
        update = self._update(callback_data="bus_back")

        await news_bot.handle_bus_back(update, ctx)

        text = update.callback_query.last_text
        self.assertTrue(text.startswith("Menu expired"))
        self.assertIn("bus_request_location", callback_datas(update.callback_query.last_markup))


class AddBusStopByCodeTests(_BusBotMixin, AsyncBotTestCase):
    def test_confirm_keyboard_has_add_and_cancel(self):
        news_bot = self._make_bot()

        datas = callback_datas(news_bot._build_add_confirm_keyboard("46009"))

        self.assertIn("bus_confirmadd:46009", datas)
        self.assertIn("bus_cancel_add", datas)

    def test_bus_menu_has_add_by_code_button(self):
        news_bot = self._make_bot()

        _, markup = news_bot._build_fav_menu("alice")

        self.assertIn("bus_addbycode", callback_datas(markup))

    async def test_add_by_code_arms_awaiting_slot(self):
        news_bot = self._make_bot()
        ctx = FakeContext()
        update = self._update(callback_data="bus_addbycode")

        await news_bot.handle_bus_add_by_code(update, ctx)

        self.assertEqual(ctx.user_data["awaiting"], {"kind": "bus_code", "payload": {}})
        self.assertIn("code", update.callback_query.last_text.lower())

    async def test_awaiting_code_message_shows_confirmation(self):
        news_bot = self._make_bot()
        ctx = FakeContext(user_data={"awaiting": {"kind": "bus_code", "payload": {}}})
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="46009")

        await news_bot.handle_plain_text(update, ctx)

        reply = update.message.replies[0]
        self.assertIn("Add this stop", reply)
        self.assertIn("Arrivals for 46009", reply)
        self.assertFalse(news_bot._bus_locations_for("alice").has_code("46009"))  # not added yet
        self.assertNotIn("awaiting", ctx.user_data)  # one-shot, consumed
        self.assertEqual(ctx.user_data["bus_menu"], {"kind": "fav"})

    async def test_awaiting_code_non_digit_is_rejected_not_treated_as_password(self):
        news_bot = self._make_bot()
        ctx = FakeContext(user_data={"awaiting": {"kind": "bus_code", "payload": {}}})
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="not a code")

        await news_bot.handle_plain_text(update, ctx)

        self.assertIn("doesn't look like a bus stop code", update.message.replies[0])

    async def test_awaiting_code_already_favorited(self):
        news_bot = self._make_bot()
        news_bot._bus_locations_for("alice").add("Home", "46009")
        ctx = FakeContext(user_data={"awaiting": {"kind": "bus_code", "payload": {}}})
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="46009")

        await news_bot.handle_plain_text(update, ctx)

        self.assertIn("already in your preferred", update.message.replies[0])

    async def test_awaiting_code_on_locked_chat_falls_through_to_password(self):
        news_bot = self._make_bot()  # unlocks chat 7; use a different, locked chat
        ctx = FakeContext(user_data={"awaiting": {"kind": "bus_code", "payload": {}}})
        update = FakeUpdate(user_id=100, chat_id=999, text="46009")

        await news_bot.handle_plain_text(update, ctx)

        # Session not active for chat 999 → treated as a (failed) password, not a code.
        self.assertIn("fact:", update.message.replies[0])
        self.assertFalse(news_bot._bus_locations_for("alice").has_code("46009"))

    async def test_awaiting_falls_through_to_password_when_locked(self):
        news_bot = self._make_bot()
        news_bot._accounts.lock(self.CHAT_ID)  # ensure locked
        ctx = FakeContext(user_data={"awaiting": {"kind": "bus_code", "payload": {}}})
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="hunter2")

        await news_bot.handle_plain_text(update, ctx)

        # Locked → the text was treated as a (correct) password, not a bus code.
        self.assertIsNotNone(news_bot._accounts.account_for(self.CHAT_ID))

    async def test_confirm_adds_the_stop(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_confirmadd:46009")

        await news_bot.handle_bus_confirm_add(update, FakeContext())

        self.assertTrue(news_bot._bus_locations_for("alice").has_code("46009"))
        self.assertTrue(update.callback_query.last_text.startswith("⭐ Added"))
        datas = callback_datas(update.callback_query.last_markup)
        self.assertIn("bus_refresh:46009", datas)          # full arrivals keyboard
        self.assertIn("bus_fav_remove:46009", datas)       # now removable

    async def test_cancel_adds_nothing(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_cancel_add")

        await news_bot.handle_bus_cancel_add(update, FakeContext())

        self.assertIn("Cancelled", update.callback_query.last_text)
        self.assertFalse(news_bot._bus_locations_for("alice").has_code("46009"))


if __name__ == "__main__":
    import unittest

    unittest.main()
