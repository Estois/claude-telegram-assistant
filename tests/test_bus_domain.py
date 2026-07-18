from unittest import mock

import bot
from tests.helpers import AsyncBotTestCase, BotTestCase


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        return FakeResp(self._payload)


class LtaBusClientTests(AsyncBotTestCase):
    async def test_get_arrivals_raises_when_key_is_missing(self):
        client = bot.LtaBusClient(None)
        with self.assertRaises(RuntimeError):
            await client.get_arrivals("46009")

    async def test_get_arrivals_parses_and_sorts_services(self):
        payload = {
            "Services": [
                {"ServiceNo": "21", "NextBus": {"EstimatedArrival": ""}},
                {"ServiceNo": "10", "NextBus": {"EstimatedArrival": ""}},
            ]
        }
        client = bot.LtaBusClient("key")

        with mock.patch(
            "ykbot.bus.httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(payload)
        ):
            services = await client.get_arrivals("46009")

        self.assertEqual([s["service_no"] for s in services], ["10", "21"])


class BusStopsCacheTests(BotTestCase):
    def test_is_available_true_when_cache_file_exists(self):
        cache_file = self.tmp_path / "bus_stops_cache.json"
        cache_file.write_text("[]")
        self.assertTrue(bot.BusStopsCache(cache_file).is_available)

    def test_corrupt_cache_loads_as_empty(self):
        cache_file = self.tmp_path / "bus_stops_cache.json"
        cache_file.write_text("{not json")
        cache = bot.BusStopsCache(cache_file)

        self.assertIsNone(cache.find_by_code("46009"))
        self.assertEqual(cache.nearest(1.3, 103.8, 3), [])
