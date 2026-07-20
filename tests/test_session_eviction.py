"""Expired sessions and spent throttle records are evicted, not just ignored.

Both dicts are keyed by chat id and were previously append-only, so every
chat the process ever saw stayed resident for its lifetime.
"""

import time
import unittest
from unittest import mock

import bot
from tests.helpers import BotTestCase, make_config, write_accounts_file


class SessionEvictionTests(BotTestCase):
    CHAT_ID = 8100

    def _make_store(self, timeout=900):
        config = make_config(self.tmp_path, session_timeout_seconds=timeout)
        write_accounts_file(config.accounts_file, {"alice": ["pw"]})
        return bot.AccountStore(config.accounts_file, timeout)

    def test_expired_session_is_dropped_from_the_dict(self):
        store = self._make_store(timeout=60)
        store.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        self.assertIn(self.CHAT_ID, store._sessions)

        with mock.patch.object(time, "monotonic", return_value=time.monotonic() + 3600):
            self.assertFalse(store.is_unlocked(self.CHAT_ID))

        self.assertNotIn(self.CHAT_ID, store._sessions)

    def test_live_session_is_kept(self):
        store = self._make_store()
        store.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        self.assertTrue(store.is_unlocked(self.CHAT_ID))
        self.assertIn(self.CHAT_ID, store._sessions)

    def test_account_for_still_returns_none_after_eviction(self):
        store = self._make_store(timeout=60)
        store.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        with mock.patch.object(time, "monotonic", return_value=time.monotonic() + 3600):
            self.assertIsNone(store.account_for(self.CHAT_ID))


class FailureEvictionTests(BotTestCase):
    CHAT_ID = 8200

    def _make_store(self):
        config = make_config(self.tmp_path)
        write_accounts_file(config.accounts_file, {"alice": ["pw"]})
        return bot.AccountStore(config.accounts_file, 900)

    def test_stale_failures_are_dropped_when_the_window_passes(self):
        store = self._make_store()
        store.register_failed_unlock(self.CHAT_ID)
        self.assertIn(self.CHAT_ID, store._failures)

        with mock.patch.object(
            time, "monotonic", return_value=time.monotonic() + store.FAILURE_WINDOW + 60
        ):
            self.assertEqual(store.unlock_retry_after(self.CHAT_ID), 0.0)

        self.assertNotIn(self.CHAT_ID, store._failures)

    def test_recent_failures_are_kept_so_the_throttle_still_works(self):
        store = self._make_store()
        for _ in range(store.MAX_FAILURES):
            store.register_failed_unlock(self.CHAT_ID)

        self.assertGreater(store.unlock_retry_after(self.CHAT_ID), 0)
        self.assertIn(self.CHAT_ID, store._failures)


if __name__ == "__main__":
    unittest.main()
