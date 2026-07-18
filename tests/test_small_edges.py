import hashlib
import json
import os
import time
from unittest import mock

import bot
from tests.helpers import BotTestCase


class AnimalEmojiEdgeTests(BotTestCase):
    def test_saur_suffix_maps_to_dinosaur(self):
        self.assertEqual(bot.AnimalEmoji.for_name("brontosaurus"), "🦕")


class NewsHistoryEdgeTests(BotTestCase):
    def _store(self):
        return bot.NewsHistoryStore(self.tmp_path / "news_history.jsonl")

    def test_append_ignores_a_blank_subject(self):
        store = self._store()

        store.append("   ")

        self.assertFalse((self.tmp_path / "news_history.jsonl").exists())
        self.assertEqual(store.recent(60, 100), [])

    def test_unparseable_line_is_skipped(self):
        path = self.tmp_path / "news_history.jsonl"
        path.write_text("{ this is not json\n" + json.dumps({"ts": time.time(), "subject": "ok"}) + "\n")

        recent = self._store().recent(60, 100)

        self.assertEqual([r["subject"] for r in recent], ["ok"])

    def test_blank_lines_between_records_are_ignored(self):
        path = self.tmp_path / "news_history.jsonl"
        path.write_text("\n" + json.dumps({"ts": time.time(), "subject": "ok"}) + "\n\n")

        recent = self._store().recent(60, 100)

        self.assertEqual([r["subject"] for r in recent], ["ok"])


class AccountsFilePreservationTests(BotTestCase):
    def test_leading_comments_survive_a_rewrite(self):
        accounts_file = self.tmp_path / "accounts.hash"
        digest = hashlib.sha256(b"hunter2").hexdigest()
        accounts_file.write_text(f"# my accounts file\n# account: alice\n{digest}\n")
        store = bot.AccountStore(accounts_file, session_timeout_seconds=900)

        store.add_hash("bob", hashlib.sha256(b"swordfish").hexdigest())

        contents = accounts_file.read_text()
        self.assertIn("# my accounts file", contents)
        self.assertIn("# account: alice", contents)
        self.assertIn("# account: bob", contents)


class ConfigFromEnvTests(BotTestCase):
    def test_missing_token_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                bot.Config.from_env()
