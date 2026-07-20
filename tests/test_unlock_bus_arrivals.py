import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class _Loc:
    def __init__(self, lat, lon):
        self.latitude = lat
        self.longitude = lon


class _FakeBusStops:
    def __init__(self, available, nearest=None):
        self.is_available = available
        self._nearest = nearest or []

    def nearest(self, lat, lon, count):
        return self._nearest


class _FakeLta:
    def __init__(self, configured, services=None):
        self.is_configured = configured
        self._services = services or []

    async def get_arrivals(self, stop_code):
        return self._services


class UnlockFlowTests(AsyncBotTestCase):
    CHAT_ID = 5

    def _make_bot(self, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        return news_bot

    async def test_correct_password_unlocks_deletes_message_and_welcomes(self):
        news_bot = self._make_bot()
        update = FakeUpdate(
            user_id=100, chat_id=self.CHAT_ID, text="hunter2", message_id=42, full_name="Alice"
        )
        ctx = FakeContext()

        await news_bot.handle_plain_text(update, ctx)

        self.assertEqual(news_bot._accounts.account_for(self.CHAT_ID), "alice")
        self.assertIn((self.CHAT_ID, 42), ctx.bot.deleted)
        self.assertEqual(len(ctx.bot.sent), 1)
        self.assertIn("Welcome, Alice!", ctx.bot.sent[0][1])

    async def test_wrong_password_replies_trivia_and_stays_locked(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="wrong")

        await news_bot.handle_plain_text(update, FakeContext())

        self.assertIsNone(news_bot._accounts.account_for(self.CHAT_ID))
        self.assertIn("fact:", update.message.replies[0])

    async def test_unauthorized_user_is_ignored_silently(self):
        news_bot = self._make_bot(allowed_user_ids=frozenset({999}))
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="hunter2")
        ctx = FakeContext()

        await news_bot.handle_plain_text(update, ctx)

        self.assertEqual(update.message.replies, [])
        self.assertEqual(ctx.bot.sent, [])
        self.assertIsNone(news_bot._accounts.account_for(self.CHAT_ID))


class BusCallbackTests(AsyncBotTestCase):
    CHAT_ID = 6

    def _make_bot(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")

        async def fake_arrivals(stop_code):
            return f"Arrivals for {stop_code}"

        news_bot._bus._format_arrivals = fake_arrivals
        return news_bot

    def _update(self, callback_data):
        return FakeUpdate(user_id=100, chat_id=self.CHAT_ID, callback_data=callback_data)

    async def test_selecting_a_stop_shows_arrivals_with_keyboard(self):
        news_bot = self._make_bot()
        update = self._update("bus_stop:46009")

        await news_bot._bus.handle_bus_callback(update, FakeContext())

        text = update.callback_query.last_text
        self.assertTrue(text.startswith("Arrivals for 46009"))
        self.assertIn("🔄 Updated ", text)
        datas = [
            b.callback_data
            for row in update.callback_query.last_markup.inline_keyboard
            for b in row
        ]
        self.assertIn("bus_refresh:46009", datas)
        self.assertIn("bus_back", datas)

    async def test_fav_add_stores_the_stop_and_confirms(self):
        news_bot = self._make_bot()
        update = self._update("bus_fav_add:46009")

        await news_bot._bus.handle_bus_fav_add(update, FakeContext())

        self.assertTrue(news_bot._bus_locations_for("alice").has_code("46009"))
        self.assertTrue(update.callback_query.last_text.startswith("⭐ Added"))

    async def test_fav_remove_deletes_the_stop_and_confirms(self):
        news_bot = self._make_bot()
        news_bot._bus_locations_for("alice").add("Home", "46009")
        update = self._update("bus_fav_remove:46009")

        await news_bot._bus.handle_bus_fav_remove(update, FakeContext())

        self.assertFalse(news_bot._bus_locations_for("alice").has_code("46009"))
        self.assertTrue(update.callback_query.last_text.startswith("🗑 Removed"))


class ArrivalsFormattingTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    async def test_reports_when_lta_is_not_configured(self):
        news_bot = self._make_bot()  # lta_api_key None by default

        text = await news_bot._bus._format_arrivals("46009")

        self.assertIn("LTA_API_KEY is not configured", text)

    async def test_formats_service_arrival_lines(self):
        news_bot = self._make_bot()
        object.__setattr__(
            news_bot.services,
            "lta",
            _FakeLta(
                configured=True,
                services=[{"service_no": "71", "arrivals": ["3 min", "9 min"]}],
            ),
        )

        text = await news_bot._bus._format_arrivals("46009")

        self.assertIn("🚌 Stop 46009", text)
        self.assertIn("Svc 71: 3 min, 9 min", text)


class LocationHandlerTests(AsyncBotTestCase):
    CHAT_ID = 8

    def _make_bot(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        return news_bot

    async def test_reports_when_nearby_lookup_is_unavailable(self):
        news_bot = self._make_bot()
        object.__setattr__(news_bot.services, "stops", _FakeBusStops(available=False))
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, location=_Loc(1.3, 103.8))

        await news_bot._bus.handle_location(update, FakeContext())

        self.assertIn("isn't set up yet", update.message.replies[0])

    async def test_shows_nearest_stops_and_records_near_context(self):
        news_bot = self._make_bot()
        object.__setattr__(
            news_bot.services,
            "stops",
            _FakeBusStops(
                available=True,
                nearest=[{"Description": "Blk 1", "BusStopCode": "46009", "distance_m": 42.0}],
            ),
        )
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, location=_Loc(1.3, 103.8))
        ctx = FakeContext()

        await news_bot._bus.handle_location(update, ctx)

        self.assertEqual(update.message.replies, ["Nearest stops:"])
        self.assertEqual(ctx.user_data["bus_menu"]["kind"], "near")
