import json
import time

import bot
from tests.helpers import BotTestCase


class NewsHistoryStoreTests(BotTestCase):
    def _store(self):
        return bot.NewsHistoryStore(self.tmp_path / "news_history.jsonl")

    def test_append_then_recent_returns_records(self):
        store = self._store()
        store.append("cybersecurity")
        store.append("  formula   1  ")  # normalized
        recent = store.recent(window_days=60, max_entries=100)
        self.assertEqual([r["subject"] for r in recent], ["cybersecurity", "formula 1"])

    def test_recent_drops_entries_older_than_window(self):
        path = self.tmp_path / "news_history.jsonl"
        old = {"ts": time.time() - 100 * 86400, "subject": "old topic"}
        new = {"ts": time.time(), "subject": "new topic"}
        path.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
        recent = self._store().recent(window_days=60, max_entries=100)
        self.assertEqual([r["subject"] for r in recent], ["new topic"])

    def test_recent_caps_to_max_entries_keeping_newest(self):
        store = self._store()
        for i in range(5):
            store.append(f"topic {i}")
        recent = store.recent(window_days=60, max_entries=2)
        self.assertEqual([r["subject"] for r in recent], ["topic 3", "topic 4"])

    def test_recent_treats_nonpositive_max_entries_as_unbounded(self):
        store = self._store()
        for i in range(5):
            store.append(f"topic {i}")
        for max_entries in (0, -1):
            recent = store.recent(window_days=60, max_entries=max_entries)
            self.assertEqual(
                [r["subject"] for r in recent],
                [f"topic {i}" for i in range(5)],
                msg=f"max_entries={max_entries} should return all in-window records",
            )

    def test_theme_counts_aggregates_case_insensitively_most_frequent_first(self):
        store = self._store()
        for subject in ["Games", "games", "GAMES", "anime"]:
            store.append(subject)
        self.assertEqual(
            store.theme_counts(window_days=60, max_entries=100),
            [("games", 3), ("anime", 1)],
        )

    def test_corrupt_lines_are_skipped_not_fatal(self):
        path = self.tmp_path / "news_history.jsonl"
        path.write_text('not json\n{"ts": 0}\n' + json.dumps({"ts": time.time(), "subject": "ok"}) + "\n")
        recent = self._store().recent(window_days=60, max_entries=100)
        self.assertEqual([r["subject"] for r in recent], ["ok"])

    def test_structurally_invalid_records_are_logged_when_dropped(self):
        path = self.tmp_path / "news_history.jsonl"
        # Valid JSON, but not a usable record: missing "subject", and a non-dict.
        path.write_text('{"ts": 0}\n123\n' + json.dumps({"ts": time.time(), "subject": "ok"}) + "\n")
        with self.assertLogs("ykbot", level="WARNING") as captured:
            recent = self._store().recent(window_days=60, max_entries=100)
        self.assertEqual([r["subject"] for r in recent], ["ok"])
        # One log line per dropped structurally-invalid record.
        dropped = [m for m in captured.output if "invalid" in m.lower()]
        self.assertEqual(len(dropped), 2)

    def test_prune_rewrites_file_to_window(self):
        path = self.tmp_path / "news_history.jsonl"
        old = {"ts": time.time() - 100 * 86400, "subject": "old"}
        new = {"ts": time.time(), "subject": "new"}
        path.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
        self._store().prune(window_days=60, max_entries=100)
        self.assertEqual(path.read_text().count("\n"), 1)
        self.assertIn("new", path.read_text())
        self.assertNotIn("old", path.read_text())

    def test_missing_file_reads_empty(self):
        self.assertEqual(self._store().recent(60, 100), [])
