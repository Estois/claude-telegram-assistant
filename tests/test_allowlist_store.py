import bot
from tests.helpers import BotTestCase


class AllowlistStoreTests(BotTestCase):
    def test_is_allowed_is_wide_open_when_no_initial_ids(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=None)

        self.assertTrue(store.is_allowed(12345))
        self.assertTrue(store.is_allowed(1))

    def test_is_allowed_true_for_id_in_initial_set(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=frozenset({111, 222}))

        self.assertTrue(store.is_allowed(111))
        self.assertTrue(store.is_allowed(222))

    def test_is_allowed_false_for_id_not_in_initial_set(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=frozenset({111}))

        self.assertFalse(store.is_allowed(999))

    def test_add_returns_true_for_a_newly_added_id(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=frozenset({111}))

        self.assertTrue(store.add(222))

    def test_add_returns_false_when_id_already_allowed(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=frozenset({111}))

        self.assertFalse(store.add(111))

    def test_add_makes_the_new_id_allowed(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=frozenset({111}))
        store.add(222)

        self.assertTrue(store.is_allowed(222))

    def test_ids_property_is_none_when_empty(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=None)

        self.assertIsNone(store.ids)

    def test_ids_property_reflects_additions(self):
        store = bot.AllowlistStore(self.tmp_path / ".env", initial_ids=frozenset({111}))
        store.add(222)

        self.assertEqual(store.ids, frozenset({111, 222}))

    def test_persist_creates_env_file_when_missing(self):
        env_file = self.tmp_path / ".env"
        store = bot.AllowlistStore(env_file, initial_ids=None)

        store.add(555)

        self.assertEqual(env_file.read_text(), "TELEGRAM_ALLOWED_USER_IDS=555\n")

    def test_persist_updates_existing_line_and_keeps_other_lines(self):
        env_file = self.tmp_path / ".env"
        env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\nTELEGRAM_ALLOWED_USER_IDS=111\nOTHER=1\n")
        store = bot.AllowlistStore(env_file, initial_ids=frozenset({111}))

        store.add(222)

        lines = env_file.read_text().splitlines()
        self.assertIn("TELEGRAM_BOT_TOKEN=abc123", lines)
        self.assertIn("TELEGRAM_ALLOWED_USER_IDS=111,222", lines)
        self.assertIn("OTHER=1", lines)

    def test_persist_appends_line_when_env_file_lacks_it(self):
        env_file = self.tmp_path / ".env"
        env_file.write_text("TELEGRAM_BOT_TOKEN=abc123\n")
        store = bot.AllowlistStore(env_file, initial_ids=None)

        store.add(555)

        lines = env_file.read_text().splitlines()
        self.assertIn("TELEGRAM_BOT_TOKEN=abc123", lines)
        self.assertIn("TELEGRAM_ALLOWED_USER_IDS=555", lines)


if __name__ == "__main__":
    import unittest

    unittest.main()
