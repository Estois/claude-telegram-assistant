import bot
from tests.helpers import BotTestCase


class InterestsStoreTests(BotTestCase):
    def test_add_appends_and_returns_1_based_index(self):
        store = bot.InterestsStore(self.tmp_path / "interests.md")

        self.assertEqual(store.add("cybersecurity"), 1)
        self.assertEqual(store.add("formula 1"), 2)
        self.assertEqual(store.list(), ["cybersecurity", "formula 1"])

    def test_add_normalizes_internal_whitespace(self):
        store = bot.InterestsStore(self.tmp_path / "interests.md")

        store.add("cybersecurity,   especially\n  supply chain attacks")

        self.assertEqual(store.list(), ["cybersecurity, especially supply chain attacks"])

    def test_update_replaces_entry_at_index(self):
        store = bot.InterestsStore(self.tmp_path / "interests.md")
        store.add("games")
        store.add("anime")

        self.assertTrue(store.update(1, "games, especially PS5"))
        self.assertEqual(store.list(), ["games, especially PS5", "anime"])

    def test_update_with_out_of_range_index_returns_false_and_leaves_list_unchanged(self):
        store = bot.InterestsStore(self.tmp_path / "interests.md")
        store.add("games")

        self.assertFalse(store.update(5, "does not exist"))
        self.assertFalse(store.update(0, "also invalid"))
        self.assertEqual(store.list(), ["games"])

    def test_parse_entries_extracts_numbered_lines_only(self):
        raw_reply = (
            "Here is the reorganized list:\n\n"
            "1. cybersecurity, cloud, vulnerability analysis\n"
            "2. games, PlayStation 5, Switch 2\n"
            "Some trailing commentary that isn't a numbered entry."
        )

        entries = bot.InterestsStore.parse_entries(raw_reply)

        self.assertEqual(
            entries,
            ["cybersecurity, cloud, vulnerability analysis", "games, PlayStation 5, Switch 2"],
        )
