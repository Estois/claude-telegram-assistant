"""The keyboard builders take plain data and touch no store.

A sync builder that reads from disk blocks the event loop and cannot be
moved off it, because a `def` cannot await. Keeping them pure is also the
convention already used by every builder in ykbot/menus.py.
"""

import unittest

import bot
from tests.helpers import BotTestCase, make_config


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class ExplodingStore:
    """Any store access at all is a failure for a pure builder."""

    def __getattr__(self, name):
        raise AssertionError(f"pure builder touched the store: .{name}")


class FavMenuBuilderTests(BotTestCase):
    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        b._bus_locations_for = lambda account_id: ExplodingStore()
        return b

    def test_takes_locations_and_touches_no_store(self):
        b = self._make_bot()

        text, markup = b._bus._build_fav_menu({"Home": "46009"})

        self.assertEqual(text, "Pick a stop:")
        self.assertIn("bus_stop:46009", datas(markup))

    def test_empty_locations_still_offers_location_and_add(self):
        b = self._make_bot()

        _text, markup = b._bus._build_fav_menu({})

        self.assertIn("bus_request_location", datas(markup))
        self.assertIn("bus_addbycode", datas(markup))
        self.assertIn("home", datas(markup))


class ArrivalsKeyboardBuilderTests(BotTestCase):
    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        b._bus_locations_for = lambda account_id: ExplodingStore()
        return b

    def test_favorite_shows_remove_and_touches_no_store(self):
        b = self._make_bot()

        markup = b._bus._build_arrivals_keyboard("46009", is_favorite=True)

        self.assertIn("bus_fav_remove:46009", datas(markup))
        self.assertNotIn("bus_fav_add:46009", datas(markup))

    def test_non_favorite_shows_add(self):
        b = self._make_bot()

        markup = b._bus._build_arrivals_keyboard("46009", is_favorite=False)

        self.assertIn("bus_fav_add:46009", datas(markup))
        self.assertNotIn("bus_fav_remove:46009", datas(markup))

    def test_nav_row_is_unchanged(self):
        b = self._make_bot()

        markup = b._bus._build_arrivals_keyboard("46009", is_favorite=False)

        self.assertIn("bus_refresh:46009", datas(markup))
        self.assertIn("bus_back", datas(markup))


class NewsPromptBuilderTests(BotTestCase):
    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        b._interests_for = lambda account_id: ExplodingStore()
        return b

    def test_takes_interests_and_touches_no_store(self):
        b = self._make_bot()

        prompt = b._build_news_prompt("space", ["rockets", "telescopes"])

        self.assertIn("space", prompt)
        self.assertIn("rockets", prompt)

    def test_empty_interests_still_builds_a_prompt(self):
        b = self._make_bot()

        prompt = b._build_news_prompt("space", [])

        self.assertIn("space", prompt)

    def test_recency_window_uses_configured_days(self):
        b = bot.YKBot(make_config(self.tmp_path, news_recency_days=3))
        b._interests_for = lambda account_id: ExplodingStore()

        prompt = b._build_news_prompt("space", [])

        self.assertIn("past 3 days", prompt)


class UserListingBuilderTests(BotTestCase):
    def test_takes_account_ids_and_marks_sessions(self):
        b = bot.YKBot(make_config(self.tmp_path))

        listing = b._format_user_listing(["alice", "bob"])

        self.assertIn("alice", listing)
        self.assertIn("bob", listing)
        self.assertIn("🔒 locked", listing)

    def test_empty_list_says_so(self):
        b = bot.YKBot(make_config(self.tmp_path))

        self.assertEqual(b._format_user_listing([]), "No accounts configured.")


if __name__ == "__main__":
    unittest.main()
