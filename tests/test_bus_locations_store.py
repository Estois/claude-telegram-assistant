import bot
from tests.helpers import BotTestCase


class BusLocationsStoreTests(BotTestCase):
    def test_list_returns_empty_dict_when_no_file_exists(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")

        self.assertEqual(store.list(), {})

    def test_add_then_list_reflects_the_new_entry(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")

        store.add("Home", "46009")

        self.assertEqual(store.list(), {"Home": "46009"})

    def test_add_with_an_existing_name_overwrites_its_code(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")
        store.add("Home", "46009")

        store.add("Home", "46008")

        self.assertEqual(store.list(), {"Home": "46008"})

    def test_has_code_true_after_add(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")
        store.add("Home", "46009")

        self.assertTrue(store.has_code("46009"))

    def test_has_code_false_for_unknown_code(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")

        self.assertFalse(store.has_code("00000"))

    def test_remove_by_code_removes_matching_entry_and_returns_true(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")
        store.add("Home", "46009")

        self.assertTrue(store.remove_by_code("46009"))
        self.assertEqual(store.list(), {})

    def test_remove_by_code_returns_false_when_nothing_matches(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")

        self.assertFalse(store.remove_by_code("00000"))

    def test_remove_by_code_removes_every_name_pointing_at_that_code(self):
        store = bot.BusLocationsStore(self.tmp_path / "bus_locations.json")
        store.add("Home", "46009")
        store.add("Home Alt", "46009")
        store.add("Work", "46101")

        store.remove_by_code("46009")

        self.assertEqual(store.list(), {"Work": "46101"})

    def test_list_returns_empty_dict_for_corrupt_json(self):
        path = self.tmp_path / "bus_locations.json"
        path.write_text("{not valid json")
        store = bot.BusLocationsStore(path)

        self.assertEqual(store.list(), {})

    def test_add_persists_across_new_store_instances(self):
        path = self.tmp_path / "bus_locations.json"
        bot.BusLocationsStore(path).add("Home", "46009")

        reloaded = bot.BusLocationsStore(path)

        self.assertEqual(reloaded.list(), {"Home": "46009"})


if __name__ == "__main__":
    import unittest

    unittest.main()
