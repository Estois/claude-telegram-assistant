import bot
from tests.helpers import BotTestCase


class ClaudeSessionStoreTests(BotTestCase):
    def test_get_returns_none_when_no_file_exists(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")

        self.assertIsNone(store.get(chat_id=1))

    def test_set_then_get_returns_the_stored_session_id(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")

        store.set(chat_id=1, session_id="abc-123")

        self.assertEqual(store.get(chat_id=1), "abc-123")

    def test_chat_id_is_stringified_so_int_and_str_keys_match(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")

        store.set(chat_id=42, session_id="abc-123")

        self.assertEqual(store.get(chat_id="42"), "abc-123")

    def test_synthetic_string_chat_id_works_like_a_real_one(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")

        store.set(chat_id="maintenance:amy", session_id="xyz-789")

        self.assertEqual(store.get(chat_id="maintenance:amy"), "xyz-789")

    def test_clear_removes_an_existing_entry_and_returns_true(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")
        store.set(chat_id=1, session_id="abc-123")

        self.assertTrue(store.clear(chat_id=1))
        self.assertIsNone(store.get(chat_id=1))

    def test_clear_returns_false_when_chat_id_not_present(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")

        self.assertFalse(store.clear(chat_id=999))

    def test_set_persists_across_new_store_instances(self):
        path = self.tmp_path / "sessions.json"
        bot.ClaudeSessionStore(path).set(chat_id=1, session_id="abc-123")

        reloaded = bot.ClaudeSessionStore(path)

        self.assertEqual(reloaded.get(chat_id=1), "abc-123")

    def test_set_overwrites_an_existing_session_id_for_the_same_chat(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")
        store.set(chat_id=1, session_id="old-session")

        store.set(chat_id=1, session_id="new-session")

        self.assertEqual(store.get(chat_id=1), "new-session")

    def test_multiple_chats_are_tracked_independently(self):
        store = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")
        store.set(chat_id=1, session_id="session-one")
        store.set(chat_id=2, session_id="session-two")

        self.assertEqual(store.get(chat_id=1), "session-one")
        self.assertEqual(store.get(chat_id=2), "session-two")


if __name__ == "__main__":
    import unittest

    unittest.main()
