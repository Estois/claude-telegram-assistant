import bot
from tests.helpers import BotTestCase, write_accounts_file


class AccountStoreTests(BotTestCase):
    def test_unlock_with_correct_password_returns_account_id(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)

        self.assertEqual(store.try_unlock(chat_id=1, attempt="hunter2"), "alice")

    def test_unlock_with_wrong_password_returns_none(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)

        self.assertIsNone(store.try_unlock(chat_id=1, attempt="wrong-password"))
        self.assertIsNone(store.account_for(chat_id=1))

    def test_multiple_hashes_on_one_account_both_unlock_it(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["phone-password", "laptop-password"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)

        self.assertEqual(store.try_unlock(chat_id=1, attempt="phone-password"), "alice")
        self.assertEqual(store.try_unlock(chat_id=2, attempt="laptop-password"), "alice")

    def test_lock_ends_an_active_session(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)
        store.try_unlock(chat_id=1, attempt="hunter2")

        self.assertTrue(store.lock(chat_id=1))
        self.assertIsNone(store.account_for(chat_id=1))
        # Locking an already-locked chat reports nothing was active.
        self.assertFalse(store.lock(chat_id=1))

    def test_session_is_locked_once_its_timeout_elapses(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=0)

        store.try_unlock(chat_id=1, attempt="hunter2")

        self.assertIsNone(store.account_for(chat_id=1))

    def test_has_active_session_false_for_an_account_that_was_never_unlocked(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)

        self.assertFalse(store.has_active_session("alice"))

    def test_has_active_session_true_once_unlocked(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)

        store.try_unlock(chat_id=1, attempt="hunter2")

        self.assertTrue(store.has_active_session("alice"))

    def test_has_active_session_false_after_expiry(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=0)

        store.try_unlock(chat_id=1, attempt="hunter2")

        self.assertFalse(store.has_active_session("alice"))

    def test_has_active_session_false_after_lock(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)
        store.try_unlock(chat_id=1, attempt="hunter2")

        store.lock(chat_id=1)

        self.assertFalse(store.has_active_session("alice"))

    def test_has_active_session_true_if_any_of_several_chats_holds_it(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["phone-password", "laptop-password"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)
        store.try_unlock(chat_id=1, attempt="phone-password")
        store.try_unlock(chat_id=2, attempt="laptop-password")
        store.lock(chat_id=1)

        self.assertTrue(store.has_active_session("alice"))

    def test_has_active_session_does_not_confuse_different_accounts(self):
        accounts_file = self.tmp_path / "accounts.hash"
        write_accounts_file(accounts_file, {"alice": ["hunter2"], "bob": ["swordfish"]})
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)

        store.try_unlock(chat_id=1, attempt="hunter2")

        self.assertTrue(store.has_active_session("alice"))
        self.assertFalse(store.has_active_session("bob"))


if __name__ == "__main__":
    import unittest

    unittest.main()
