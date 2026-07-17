import unittest
from datetime import datetime, timedelta, timezone

import bot


class LtaBusClientFormatEtaTests(unittest.TestCase):
    def test_missing_eta_renders_as_dash(self):
        self.assertEqual(bot.LtaBusClient._format_eta(None), "—")

    def test_eta_in_the_past_renders_as_arr(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        self.assertEqual(bot.LtaBusClient._format_eta(past), "Arr")

    def test_eta_a_few_minutes_out_renders_as_n_min(self):
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        self.assertEqual(bot.LtaBusClient._format_eta(future), "5 min")


class LtaBusClientParseServicesTests(unittest.TestCase):
    def test_parse_services_sorts_by_service_number_and_formats_arrivals(self):
        now = datetime.now(timezone.utc)
        soon = (now + timedelta(minutes=2)).isoformat()
        data = {
            "Services": [
                {
                    "ServiceNo": "858",
                    "NextBus": {"EstimatedArrival": soon},
                    "NextBus2": {"EstimatedArrival": ""},
                    "NextBus3": {"EstimatedArrival": ""},
                },
                {
                    "ServiceNo": "21",
                    "NextBus": {"EstimatedArrival": soon},
                    "NextBus2": {},
                    "NextBus3": {},
                },
            ]
        }

        services = bot.LtaBusClient._parse_services(data)

        self.assertEqual([s["service_no"] for s in services], ["21", "858"])
        self.assertEqual(services[0]["arrivals"], ["2 min", "—", "—"])


if __name__ == "__main__":
    unittest.main()
