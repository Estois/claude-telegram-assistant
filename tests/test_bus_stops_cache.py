import json

import bot
from helpers import BotTestCase

STOPS = [
    {"BusStopCode": "A", "Description": "Very close stop", "Latitude": 1.3001, "Longitude": 103.8001},
    {"BusStopCode": "B", "Description": "Far away stop", "Latitude": 1.3500, "Longitude": 103.9000},
    {"BusStopCode": "C", "Description": "Medium distance stop", "Latitude": 1.3050, "Longitude": 103.8050},
]


class BusStopsCacheTests(BotTestCase):
    def _write_cache(self):
        cache_file = self.tmp_path / "bus_stops_cache.json"
        cache_file.write_text(json.dumps(STOPS))
        return bot.BusStopsCache(cache_file)

    def test_nearest_orders_by_distance_ascending(self):
        cache = self._write_cache()

        nearest = cache.nearest(lat=1.3000, lon=103.8000, count=2)

        self.assertEqual([s["BusStopCode"] for s in nearest], ["A", "C"])
        self.assertLess(nearest[0]["distance_m"], nearest[1]["distance_m"])

    def test_find_by_code_returns_matching_stop(self):
        cache = self._write_cache()

        stop = cache.find_by_code("B")

        self.assertEqual(stop["Description"], "Far away stop")

    def test_find_by_code_returns_none_for_unknown_code(self):
        cache = self._write_cache()

        self.assertIsNone(cache.find_by_code("does-not-exist"))
