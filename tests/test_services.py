"""BotServices is the one-way dependency bundle handler classes receive.

The point is that a handler class can be built against this object alone —
no YKBot, no back-reference — so the dependency direction stays one-way.
"""

import unittest

import bot
from ykbot.services import BotServices
from tests.helpers import BotTestCase, make_config


class BotServicesTests(BotTestCase):
    def test_bot_exposes_its_services(self):
        b = bot.YKBot(make_config(self.tmp_path))

        self.assertIsInstance(b.services, BotServices)

    def test_carries_the_singleton_stores(self):
        b = bot.YKBot(make_config(self.tmp_path))
        svc = b.services

        self.assertIs(svc.config, b._config)
        self.assertIs(svc.accounts, b._accounts)
        self.assertIs(svc.allowlist, b._allowlist)
        self.assertIs(svc.claude, b._claude)
        self.assertIs(svc.trivia, b._trivia)
        self.assertIs(svc.lta, b._lta)
        self.assertIs(svc.stops, b._bus_stops)
        self.assertIs(svc.message_log, b._message_log)

    def test_per_account_factories_are_callable(self):
        b = bot.YKBot(make_config(self.tmp_path))
        svc = b.services

        self.assertIsNotNone(svc.interests_for("alice"))
        self.assertIsNotNone(svc.bus_locations_for("alice"))
        self.assertIsNotNone(svc.news_history_for("alice"))

    def test_shared_helpers_are_bound_to_the_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        svc = b.services

        self.assertEqual(svc.require_session, b._require_session)
        self.assertEqual(svc.require_admin, b._require_admin)
        self.assertEqual(svc.is_admin, b._is_admin)
        self.assertEqual(svc.reply_result, b._reply_result)
        self.assertEqual(svc.send_formatted, b._send_formatted)
        self.assertEqual(svc.run_claude, b._run_claude)
        self.assertEqual(svc.safe_edit, b._safe_edit)

    def test_can_be_constructed_without_a_bot(self):
        """The whole point: a handler class is testable against a stub."""
        svc = BotServices(
            config=make_config(self.tmp_path),
            accounts=None, allowlist=None, claude=None, trivia=None,
            lta=None, stops=None, message_log=None,
            interests_for=lambda a: None,
            bus_locations_for=lambda a: None,
            news_history_for=lambda a: None,
            require_session=None, require_admin=None, is_admin=None,
            reply_result=None, send_formatted=None, run_claude=None, safe_edit=None,
        )

        self.assertIsNotNone(svc.config)


if __name__ == "__main__":
    unittest.main()
