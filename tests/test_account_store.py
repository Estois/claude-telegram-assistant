import bot
from helpers import BotTestCase, write_accounts_file


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


if __name__ == "__main__":
    import unittest

    unittest.main()
