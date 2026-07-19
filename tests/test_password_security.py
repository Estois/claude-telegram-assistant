import hashlib
import os

import bot
from ykbot import accounts
from tests.helpers import (
    AsyncBotTestCase,
    BotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


def _sha(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


class HashVerifyTests(BotTestCase):
    def test_scrypt_roundtrip(self):
        h = accounts.hash_scrypt("hunter2")

        self.assertTrue(h.startswith("scrypt$"))
        self.assertTrue(accounts.verify_password("hunter2", h))
        self.assertFalse(accounts.verify_password("wrong", h))

    def test_scrypt_salts_are_unique(self):
        self.assertNotEqual(accounts.hash_scrypt("pw"), accounts.hash_scrypt("pw"))

    def test_verify_legacy_sha256(self):
        self.assertTrue(accounts.verify_password("hunter2", _sha("hunter2")))
        self.assertFalse(accounts.verify_password("nope", _sha("hunter2")))

    def test_malformed_scrypt_verifies_false(self):
        self.assertFalse(accounts.verify_password("hunter2", "scrypt$not$valid"))


class MigrationTests(BotTestCase):
    def _store(self):
        return bot.AccountStore(self.tmp_path / "accounts.hash", session_timeout_seconds=900)

    def test_sha256_entry_is_upgraded_to_scrypt_on_unlock(self):
        path = self.tmp_path / "accounts.hash"
        write_accounts_file(path, {"alice": ["hunter2"]})  # writes SHA-256
        store = self._store()

        self.assertEqual(store.try_unlock(chat_id=1, attempt="hunter2"), "alice")

        contents = path.read_text()
        self.assertIn("scrypt$", contents)
        self.assertNotIn(_sha("hunter2"), contents)
        # A fresh store (reading the now-scrypt file) still unlocks.
        self.assertEqual(self._store().try_unlock(chat_id=2, attempt="hunter2"), "alice")

    def test_scrypt_entry_unlocks_without_rewrite(self):
        path = self.tmp_path / "accounts.hash"
        path.write_text(f"# account: alice\n{accounts.hash_scrypt('hunter2')}\n")
        store = self._store()
        before = path.read_text()

        self.assertEqual(store.try_unlock(chat_id=1, attempt="hunter2"), "alice")
        self.assertEqual(path.read_text(), before)  # no migration needed

    def test_migration_write_failure_does_not_block_unlock(self):
        write_accounts_file(self.tmp_path / "accounts.hash", {"alice": ["hunter2"]})
        store = self._store()

        def boom(header, sections):
            raise OSError("disk full")

        store._write_sections = boom

        self.assertEqual(store.try_unlock(chat_id=1, attempt="hunter2"), "alice")
        self.assertEqual(store.account_for(1), "alice")  # unlocked despite write failure


class AtomicWriteTests(BotTestCase):
    def test_accounts_file_written_atomically_with_0600_perms(self):
        store = bot.AccountStore(self.tmp_path / "accounts.hash", session_timeout_seconds=900)

        store.add_hash("alice", _sha("hunter2"))  # triggers _write_sections

        path = self.tmp_path / "accounts.hash"
        self.assertEqual(store.list_ids(), ["alice"])
        # POSIX-only: on Windows os.chmod only honors the read-only bit, so a
        # writable file always reports 0o666 regardless of the mode we request.
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        # No stray temp file left behind.
        self.assertEqual([p for p in self.tmp_path.iterdir() if p.name != "accounts.hash"], [])

    def test_env_written_atomically(self):
        env = self.tmp_path / ".env"
        store = bot.AllowlistStore(env, initial_ids=frozenset({1}))

        store.add(42)

        self.assertIn("TELEGRAM_ALLOWED_USER_IDS=1,42", env.read_text())
        if os.name == "posix":  # see note in the accounts.hash test above
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p for p in self.tmp_path.iterdir() if p.name != ".env"], [])

    def test_atomic_write_reraises_and_swallows_cleanup_failure(self):
        from unittest import mock

        target = self.tmp_path / "accounts.hash"
        with mock.patch("ykbot.accounts.os.replace", side_effect=OSError("replace failed")), \
             mock.patch("ykbot.accounts.os.unlink", side_effect=OSError("unlink failed")):
            with self.assertRaises(OSError):
                accounts._atomic_write_text(target, "data")


class ThrottleTests(BotTestCase):
    def _store(self):
        return bot.AccountStore(self.tmp_path / "accounts.hash", session_timeout_seconds=900)

    def test_not_throttled_initially(self):
        self.assertEqual(self._store().unlock_retry_after(chat_id=1), 0.0)

    def test_below_threshold_not_throttled(self):
        store = self._store()
        for _ in range(bot.AccountStore.MAX_FAILURES - 1):
            store.register_failed_unlock(1)
        self.assertEqual(store.unlock_retry_after(1), 0.0)

    def test_throttled_after_max_failures(self):
        store = self._store()
        for _ in range(bot.AccountStore.MAX_FAILURES):
            store.register_failed_unlock(1)
        self.assertGreater(store.unlock_retry_after(1), 0.0)
        self.assertEqual(store.unlock_retry_after(2), 0.0)  # per-chat

    def test_clear_resets_throttle(self):
        store = self._store()
        for _ in range(bot.AccountStore.MAX_FAILURES):
            store.register_failed_unlock(1)
        store.clear_failed_unlocks(1)
        self.assertEqual(store.unlock_retry_after(1), 0.0)


class ThrottledHandlerTests(AsyncBotTestCase):
    CHAT_ID = 5

    async def test_correct_password_is_ignored_while_throttled(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        for _ in range(bot.AccountStore.MAX_FAILURES):
            news_bot._accounts.register_failed_unlock(self.CHAT_ID)
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="hunter2")

        await news_bot.handle_plain_text(update, FakeContext())

        # Throttled: even the correct password does not unlock; trivia reply.
        self.assertIsNone(news_bot._accounts.account_for(self.CHAT_ID))
        self.assertIn("fact:", update.message.replies[0])

    async def test_failed_attempt_is_recorded(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="wrongpw")

        await news_bot.handle_plain_text(update, FakeContext())

        self.assertEqual(len(news_bot._accounts._failures.get(self.CHAT_ID, [])), 1)
