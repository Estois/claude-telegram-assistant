import hashlib

import bot
from tests.helpers import BotTestCase, write_accounts_file


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class AccountStoreMutationTests(BotTestCase):
    def _store(self):
        return bot.AccountStore(self.tmp_path / "accounts.hash", session_timeout_seconds=900)

    def test_is_configured_false_when_no_accounts_file(self):
        self.assertFalse(self._store().is_configured)

    def test_is_configured_true_once_an_account_has_a_hash(self):
        write_accounts_file(self.tmp_path / "accounts.hash", {"alice": ["hunter2"]})
        self.assertTrue(self._store().is_configured)

    def test_add_hash_creates_a_new_account_that_then_unlocks(self):
        store = self._store()

        created = store.add_hash("alice", _sha("hunter2"))

        self.assertTrue(created)
        self.assertEqual(store.try_unlock(chat_id=1, attempt="hunter2"), "alice")

    def test_add_hash_to_existing_account_returns_false_and_both_unlock(self):
        store = self._store()
        store.add_hash("alice", _sha("laptop-pw"))

        created = store.add_hash("alice", _sha("phone-pw"))

        self.assertFalse(created)
        self.assertEqual(store.try_unlock(chat_id=1, attempt="laptop-pw"), "alice")
        self.assertEqual(store.try_unlock(chat_id=2, attempt="phone-pw"), "alice")

    def test_add_hash_deduplicates_identical_hashes(self):
        store = self._store()
        digest = _sha("hunter2")

        store.add_hash("alice", digest)
        store.add_hash("alice", digest)

        contents = (self.tmp_path / "accounts.hash").read_text()
        self.assertEqual(contents.count(digest), 1)

    def test_remove_account_returns_true_and_prevents_further_unlock(self):
        store = self._store()
        store.add_hash("alice", _sha("hunter2"))

        removed = store.remove_account("alice")

        self.assertTrue(removed)
        self.assertIsNone(store.try_unlock(chat_id=1, attempt="hunter2"))
        self.assertNotIn("alice", store.list_ids())

    def test_remove_account_returns_false_for_unknown_account(self):
        store = self._store()
        store.add_hash("alice", _sha("hunter2"))

        self.assertFalse(store.remove_account("nobody"))

    def test_invalid_account_header_is_ignored(self):
        # A '# account:' header whose id has illegal characters is dropped.
        (self.tmp_path / "accounts.hash").write_text(
            f"# account: bad id!\n{_sha('x')}\n# account: alice\n{_sha('hunter2')}\n"
        )
        store = self._store()

        self.assertEqual(store.list_ids(), ["alice"])
        self.assertEqual(store.try_unlock(chat_id=1, attempt="hunter2"), "alice")
