# Passive interest learning from /news — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture each `/news` subject to a per-account log and let the existing daily reorganize pass fold recurring request themes into `interests.md`.

**Architecture:** A new `NewsHistoryStore` (append-only JSONL per account) is written on the `/news` hot path (no Claude call). The existing per-account daily job (`YKBot._reorganize_account`) reads that history and includes recurring themes, with counts, in the one Claude call it already makes — then prunes the log to a rolling window.

**Tech Stack:** Python 3.12 stdlib (`json`, `time`, `re`), `python-telegram-bot`, `unittest` tests, `pyflakes` static gate. No new dependencies.

## Global Constraints

- Run the suite: `python3 -m unittest discover -s tests -t .` (all 111+ green). A single test: `python3 -m unittest tests.<module> -v`.
- Static gate after each code change: `python3 -m pyflakes bot.py ykbot/` must be clean. Import gate: `python3 -c "import bot"` exits 0.
- Every new public class MUST be re-exported in BOTH `ykbot/__init__.py` (import + `__all__`) and `bot.py` (`from ykbot import (...)` + `__all__`), or `import bot` / the tests break.
- Every new `Config` field MUST be added in three places: the `Config` dataclass, `Config.from_env()`, AND `tests/helpers.py::make_config`'s `defaults` dict (which constructs `bot.Config(**defaults)` and will raise if a field is missing).
- Behavior is per-account, silent (no Telegram broadcast — that would leak one account's data across users), and additive+merge (never drop a user's existing interests).
- The daily pass still makes at most ONE Claude call per account (no added Claude cost).
- Match existing store conventions: missing/corrupt files degrade to empty via `logger.exception`, never crash.

---

### Task 1: Config fields for news learning

**Files:**
- Modify: `ykbot/config.py` (dataclass fields + `from_env`)
- Modify: `tests/helpers.py:86-108` (`make_config` defaults)
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Produces: `Config.news_learning_enabled: bool`, `Config.news_history_window_days: int`, `Config.news_history_max_entries: int`.

- [ ] **Step 1: Write the failing test** — `tests/test_config.py`

```python
import os
import unittest
from unittest import mock

import bot


class ConfigNewsLearningTests(unittest.TestCase):
    def test_defaults_when_env_unset(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t"}, clear=True):
            config = bot.Config.from_env()
        self.assertTrue(config.news_learning_enabled)
        self.assertEqual(config.news_history_window_days, 60)
        self.assertEqual(config.news_history_max_entries, 100)

    def test_reads_env_overrides(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "t",
            "NEWS_LEARNING_ENABLED": "false",
            "NEWS_HISTORY_WINDOW_DAYS": "30",
            "NEWS_HISTORY_MAX_ENTRIES": "50",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = bot.Config.from_env()
        self.assertFalse(config.news_learning_enabled)
        self.assertEqual(config.news_history_window_days, 30)
        self.assertEqual(config.news_history_max_entries, 50)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_config -v`
Expected: FAIL — `AttributeError`/`TypeError` (fields don't exist yet).

- [ ] **Step 3: Add the three dataclass fields** in `ykbot/config.py`, after `bus_nearby_count: int`:

```python
    bus_nearby_count: int
    news_learning_enabled: bool
    news_history_window_days: int
    news_history_max_entries: int
```

- [ ] **Step 4: Populate them in `from_env`**, immediately after the `bus_nearby_count=...` line in the `return cls(` block:

```python
            bus_nearby_count=int(os.environ.get("BUS_NEARBY_COUNT", "3")),
            news_learning_enabled=os.environ.get("NEWS_LEARNING_ENABLED", "true").strip().lower()
            != "false",
            news_history_window_days=int(os.environ.get("NEWS_HISTORY_WINDOW_DAYS", "60")),
            news_history_max_entries=int(os.environ.get("NEWS_HISTORY_MAX_ENTRIES", "100")),
        )
```

- [ ] **Step 5: Update `make_config` defaults** in `tests/helpers.py`, after `bus_nearby_count=3,`:

```python
        bus_nearby_count=3,
        news_learning_enabled=True,
        news_history_window_days=60,
        news_history_max_entries=100,
    )
```

- [ ] **Step 6: Run tests + gates**

Run: `python3 -m unittest tests.test_config -v && python3 -m unittest discover -s tests -t . && python3 -m pyflakes bot.py ykbot/`
Expected: new tests PASS, full suite PASS (all prior tests still green because `make_config` was updated), pyflakes clean.

- [ ] **Step 7: Commit**

```bash
git add ykbot/config.py tests/helpers.py tests/test_config.py
git commit -m "Add news-learning config fields"
```

---

### Task 2: NewsHistoryStore

**Files:**
- Create: `ykbot/news_history.py`
- Modify: `ykbot/__init__.py` (re-export), `bot.py` (re-export)
- Test: `tests/test_news_history_store.py` (new)

**Interfaces:**
- Produces: `NewsHistoryStore(path: Path)` with:
  - `append(subject: str) -> None`
  - `recent(window_days: int, max_entries: int) -> list[dict]`
  - `theme_counts(window_days: int, max_entries: int) -> list[tuple[str, int]]`
  - `prune(window_days: int, max_entries: int) -> None`
- Consumes: `ykbot._logging.logger`.

- [ ] **Step 1: Write the failing test** — `tests/test_news_history_store.py`

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_news_history_store -v`
Expected: FAIL — `AttributeError: module 'bot' has no attribute 'NewsHistoryStore'`.

- [ ] **Step 3: Create `ykbot/news_history.py`**

```python
"""Per-account append-only log of /news request subjects.

One JSON object per line: {"ts": <unix seconds float>, "subject": "<text>"}.
Read paths tolerate a missing file and skip malformed lines rather than crash,
matching the other per-account stores.
"""

import json
import time
from pathlib import Path

from ykbot._logging import logger


class NewsHistoryStore:
    def __init__(self, path: Path):
        self._path = path

    def append(self, subject: str) -> None:
        subject = " ".join(subject.split())
        if not subject:
            return
        record = {"ts": time.time(), "subject": subject}
        with self._path.open("a") as handle:
            handle.write(json.dumps(record) + "\n")

    def recent(self, window_days: int, max_entries: int) -> list:
        """Records newer than window_days, capped to the most recent
        max_entries (records are stored in append/chronological order)."""
        cutoff = time.time() - window_days * 86400
        within = [r for r in self._read() if r.get("ts", 0) >= cutoff]
        return within[-max_entries:] if max_entries > 0 else within

    def theme_counts(self, window_days: int, max_entries: int) -> list:
        """Recent subjects lowercased and aggregated into (subject, count)
        pairs, most frequent first (ties broken alphabetically)."""
        counts: dict = {}
        for record in self.recent(window_days, max_entries):
            key = record["subject"].lower()
            counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def prune(self, window_days: int, max_entries: int) -> None:
        if not self._path.exists():
            return
        kept = self.recent(window_days, max_entries)
        self._path.write_text("".join(json.dumps(r) + "\n" for r in kept))

    def _read(self) -> list:
        if not self._path.exists():
            return []
        records = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.exception("Skipping malformed line in %s", self._path)
                continue
            if isinstance(record, dict) and "subject" in record and "ts" in record:
                records.append(record)
        return records
```

- [ ] **Step 4: Re-export in `ykbot/__init__.py`** — add the import (alphabetically among the `from ykbot.*` lines) and the `__all__` entry:

```python
from ykbot.interests import InterestsStore
from ykbot.news_history import NewsHistoryStore
from ykbot.trivia import FALLBACK_TRIVIA, AnimalEmoji, TriviaBook
```
and in `__all__`, add `"NewsHistoryStore",` (e.g. after `"InterestsStore",`).

- [ ] **Step 5: Re-export in `bot.py`** — add `NewsHistoryStore,` to the `from ykbot import (...)` list (after `LtaBusClient,`) and `"NewsHistoryStore",` to `__all__` (after `"InterestsStore",`).

- [ ] **Step 6: Run tests + gates**

Run: `python3 -m unittest tests.test_news_history_store -v && python3 -c "import bot" && python3 -m pyflakes bot.py ykbot/ && python3 -m unittest discover -s tests -t .`
Expected: all PASS, pyflakes clean.

- [ ] **Step 7: Commit**

```bash
git add ykbot/news_history.py ykbot/__init__.py bot.py tests/test_news_history_store.py
git commit -m "Add NewsHistoryStore for per-account /news request logging"
```

---

### Task 3: Capture /news subjects

**Files:**
- Modify: `ykbot/app.py` (import, `NEWS_HISTORY_FILENAME` constant, `_news_history_for` helper, capture in `handle_news`)
- Test: `tests/test_news_capture.py` (new)

**Interfaces:**
- Consumes: `NewsHistoryStore`, `Config.news_learning_enabled`.
- Produces: `YKBot._news_history_for(account_id: str) -> NewsHistoryStore`.

- [ ] **Step 1: Write the failing test** — `tests/test_news_capture.py`

```python
import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class NewsCaptureTests(AsyncBotTestCase):
    def _make_bot(self, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")

        async def fake_run_claude(update, context, prompt):
            return None  # skip the real Claude subprocess

        news_bot._run_claude = fake_run_claude
        return news_bot

    async def test_news_request_is_logged(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)
        await news_bot.handle_news(update, FakeContext(args=["quantum", "computing"]))
        recent = news_bot._news_history_for("alice").recent(60, 100)
        self.assertEqual([r["subject"] for r in recent], ["quantum computing"])

    async def test_nothing_logged_when_learning_disabled(self):
        news_bot = self._make_bot(news_learning_enabled=False)
        update = FakeUpdate(user_id=1, chat_id=1)
        await news_bot.handle_news(update, FakeContext(args=["quantum", "computing"]))
        self.assertEqual(news_bot._news_history_for("alice").recent(60, 100), [])

    async def test_empty_news_request_is_not_logged(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)
        await news_bot.handle_news(update, FakeContext(args=[]))  # usage message path
        self.assertEqual(news_bot._news_history_for("alice").recent(60, 100), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_news_capture -v`
Expected: FAIL — `AttributeError: 'YKBot' object has no attribute '_news_history_for'`.

- [ ] **Step 3: Import + constant + helper in `ykbot/app.py`.**
Add to the ykbot imports: `from ykbot.news_history import NewsHistoryStore`.
Add the filename constant next to the existing ones in `YKBot`:

```python
    INTERESTS_FILENAME = "interests.md"
    BUS_LOCATIONS_FILENAME = "bus_locations.json"
    NEWS_HISTORY_FILENAME = "news_history.jsonl"
```

Add the helper next to `_interests_for`:

```python
    def _news_history_for(self, account_id: str) -> NewsHistoryStore:
        return NewsHistoryStore(self._account_dir(account_id) / self.NEWS_HISTORY_FILENAME)
```

- [ ] **Step 4: Capture in `handle_news`** — after `subject = " ".join(context.args)` and before the `_run_claude` call:

```python
        subject = " ".join(context.args)
        if self._config.news_learning_enabled:
            try:
                self._news_history_for(account_id).append(subject)
            except Exception:
                # Logging the subject must never break the digest reply.
                logger.exception("Failed to log /news subject for %s", account_id)
        reply = await self._run_claude(update, context, self._build_news_prompt(subject, account_id))
```

- [ ] **Step 5: Run tests + gates**

Run: `python3 -m unittest tests.test_news_capture -v && python3 -m pyflakes bot.py ykbot/ && python3 -m unittest discover -s tests -t .`
Expected: all PASS, pyflakes clean.

- [ ] **Step 6: Commit**

```bash
git add ykbot/app.py tests/test_news_capture.py
git commit -m "Log /news subjects to per-account history"
```

---

### Task 4: Combined reorganize+learn prompt

**Files:**
- Modify: `ykbot/app.py` (`_build_reorganize_prompt` signature + body)
- Test: `tests/test_reorganize.py` (add prompt-builder tests)

**Interfaces:**
- Changes: `_build_reorganize_prompt(entries: list, theme_counts: list) -> str` (was `(entries)`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_reorganize.py`:

```python
class ReorganizePromptTests(unittest.TestCase):
    def test_no_history_block_when_theme_counts_empty(self):
        prompt = bot.YKBot._build_reorganize_prompt(["games", "anime"], [])
        self.assertIn("1. games", prompt)
        self.assertNotIn("requested", prompt)

    def test_history_block_lists_counts_and_recurrence_rule(self):
        prompt = bot.YKBot._build_reorganize_prompt(
            ["games"], [("quantum computing", 3), ("obscure onetime", 1)]
        )
        self.assertIn("quantum computing (requested 3x)", prompt)
        self.assertIn("obscure onetime (requested 1x)", prompt)
        self.assertIn("recurs", prompt)
        self.assertIn("never add a one-off", prompt)
        self.assertIn("Do not remove or override", prompt)
```
(`import unittest` is already present at the bottom of the file; move it to the top if the linter prefers — add `import unittest` at the top with the other imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_reorganize.ReorganizePromptTests -v`
Expected: FAIL — `TypeError` (builder takes 1 arg) then assertion failures.

- [ ] **Step 3: Replace `_build_reorganize_prompt`** in `ykbot/app.py`:

```python
    @staticmethod
    def _build_reorganize_prompt(entries: list, theme_counts: list) -> str:
        numbered = "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))
        history_block = ""
        if theme_counts:
            lines = "\n".join(
                f"- {subject} (requested {count}x)" for subject, count in theme_counts
            )
            history_block = (
                "\n\nSeparately, here are the topics I've recently asked /news to cover, "
                f"with how many times I asked for each:\n{lines}\n\n"
                "Fold a topic from this request history into the interests list ONLY if "
                "it recurs (requested more than once) or is clearly significant — never "
                "add a one-off request. Do not remove or override any interest already "
                "in the list above."
            )
        return (
            "Here is my current news-interests list, used to bias what /news digests "
            f"cover:\n\n{numbered}\n"
            + history_block
            + "\n\nReorganize this list: merge entries that are duplicates or clearly the "
            "same underlying topic, and where it would help a news-search assistant "
            "retrieve more relevant results, add a short blurb of extra detail to an "
            "entry — specific sub-topics, entities, or angles reasonably implied by what's "
            "already there. Don't invent interests the list doesn't imply, and don't drop "
            "any distinct topic. "
            "Reply with ONLY the resulting list, renumbered from 1, one entry per line, in "
            "the exact form '<number>. <entry text>' — plain text, no extra commentary, no "
            "headers, no markdown."
        )
```

- [ ] **Step 4: Run tests + gates**

Run: `python3 -m unittest tests.test_reorganize -v && python3 -m pyflakes bot.py ykbot/`
Expected: new prompt tests PASS; the existing `ReorganizeAccountTests` FAIL now (they call `_reorganize_account`, which still calls the builder with one arg) — that is fixed in Task 5. If you are running strictly task-by-task, expect the builder tests green and note the account tests will go green after Task 5. (Do not commit until Step 5 below.)

- [ ] **Step 5: Update the one internal caller** in `_reorganize_account` so the module imports cleanly — change `self._build_reorganize_prompt(entries)` to `self._build_reorganize_prompt(entries, theme_counts)` and define `theme_counts = []` just above it for now (full wiring in Task 5). Then:

Run: `python3 -m unittest discover -s tests -t . && python3 -m pyflakes bot.py ykbot/`
Expected: all PASS, pyflakes clean.

- [ ] **Step 6: Commit**

```bash
git add ykbot/app.py tests/test_reorganize.py
git commit -m "Extend reorganize prompt to fold in recurring /news themes"
```

---

### Task 5: Wire history into the daily pass AND /compileinterests

**Files:**
- Modify: `ykbot/app.py` (new `_recent_theme_counts` helper; `_reorganize_account`; `handle_compile_interests`)
- Test: `tests/test_reorganize.py` (add wiring tests)

**Interfaces:**
- Produces: `YKBot._recent_theme_counts(account_id: str) -> list` — recent theme counts for the account, or `[]` when learning is disabled.
- Consumes: `_news_history_for`, `Config.news_history_window_days/max_entries/news_learning_enabled`, `_build_reorganize_prompt(entries, theme_counts)`.
- Both `_reorganize_account` (daily job) and `handle_compile_interests` (`/compileinterests`) fold in history via the shared helper, so the manual and automatic reorganize behave identically (`/compileinterests`'s docstring calls itself "the same reorganization the background job runs"). Only the daily job prunes the log.
- NOTE: ensure `tests/test_reorganize.py` imports `FakeUpdate`, `FakeContext`, and `write_accounts_file` from `tests.helpers` (the Task 4 fix added a `CompileInterestsCommandTests` that may already have added these — add any that are missing).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_reorganize.py`:

```python
class ReorganizeLearningTests(AsyncBotTestCase):
    def _make_bot(self, **overrides):
        return bot.YKBot(make_config(self.tmp_path, **overrides))

    async def test_recurring_theme_and_counts_reach_the_prompt(self):
        news_bot = self._make_bot()
        history = news_bot._news_history_for("amy")
        for _ in range(3):
            history.append("quantum computing")
        history.append("obscure onetime")
        news_bot._interests_for("amy").add("games")  # only 1 interest, but history exists
        fake = FakeClaudeClient("1. games\n2. quantum computing")
        news_bot._claude = fake

        await news_bot._reorganize_account("amy")

        prompt = fake.calls[0][1]
        self.assertIn("quantum computing (requested 3x)", prompt)
        self.assertIn("obscure onetime (requested 1x)", prompt)
        self.assertEqual(
            news_bot._interests_for("amy").list(), ["games", "quantum computing"]
        )

    async def test_history_pruned_after_pass(self):
        news_bot = self._make_bot(news_history_window_days=60, news_history_max_entries=100)
        history = news_bot._news_history_for("amy")
        # one in-window, one ancient
        history.append("keep me")
        path = news_bot._account_dir("amy") / news_bot.NEWS_HISTORY_FILENAME
        import json, time
        with path.open("a") as h:
            h.write(json.dumps({"ts": time.time() - 999 * 86400, "subject": "ancient"}) + "\n")
        news_bot._interests_for("amy").add("a")
        news_bot._interests_for("amy").add("b")
        news_bot._claude = FakeClaudeClient("1. a\n2. b")

        await news_bot._reorganize_account("amy")

        self.assertNotIn("ancient", path.read_text())
        self.assertIn("keep me", path.read_text())

    async def test_skips_when_no_interests_and_no_history(self):
        news_bot = self._make_bot()
        fake = FakeClaudeClient("1. should not be used")
        news_bot._claude = fake
        await news_bot._reorganize_account("amy")
        self.assertEqual(fake.calls, [])

    async def test_disabled_learning_ignores_history(self):
        news_bot = self._make_bot(news_learning_enabled=False)
        history = news_bot._news_history_for("amy")
        for _ in range(3):
            history.append("quantum computing")
        news_bot._interests_for("amy").add("games")  # only 1 interest
        fake = FakeClaudeClient("1. should not be used")
        news_bot._claude = fake
        await news_bot._reorganize_account("amy")
        self.assertEqual(fake.calls, [])  # <2 interests and history ignored -> skip


class CompileInterestsLearningTests(AsyncBotTestCase):
    def _make_bot(self, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")
        return news_bot

    async def test_compile_folds_recurring_history_into_prompt(self):
        news_bot = self._make_bot()
        for _ in range(2):
            news_bot._news_history_for("alice").append("quantum computing")
        news_bot._interests_for("alice").add("games")
        news_bot._interests_for("alice").add("anime")
        captured = {}

        async def fake_run_claude(update, context, prompt, chat_id=None):
            captured["prompt"] = prompt
            return "1. games\n2. anime\n3. quantum computing"

        news_bot._run_claude = fake_run_claude
        await news_bot.handle_compile_interests(
            FakeUpdate(user_id=1, chat_id=1), FakeContext(args=[])
        )
        self.assertIn("quantum computing (requested 2x)", captured["prompt"])

    async def test_compile_ignores_history_when_disabled(self):
        news_bot = self._make_bot(news_learning_enabled=False)
        for _ in range(2):
            news_bot._news_history_for("alice").append("quantum computing")
        news_bot._interests_for("alice").add("games")
        news_bot._interests_for("alice").add("anime")
        captured = {}

        async def fake_run_claude(update, context, prompt, chat_id=None):
            captured["prompt"] = prompt
            return "1. games\n2. anime"

        news_bot._run_claude = fake_run_claude
        await news_bot.handle_compile_interests(
            FakeUpdate(user_id=1, chat_id=1), FakeContext(args=[])
        )
        self.assertNotIn("requested", captured["prompt"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_reorganize.ReorganizeLearningTests -v`
Expected: FAIL (history not read; skip guard still `< 2`).

- [ ] **Step 3: Add the shared `_recent_theme_counts` helper** in `ykbot/app.py` (next to `_news_history_for`):

```python
    def _recent_theme_counts(self, account_id: str) -> list:
        """Recent /news request themes with counts for this account, or an
        empty list when learning is disabled. Shared by the daily reorganize
        job and the manual /compileinterests command so they behave the same."""
        if not self._config.news_learning_enabled:
            return []
        return self._news_history_for(account_id).theme_counts(
            self._config.news_history_window_days,
            self._config.news_history_max_entries,
        )
```

- [ ] **Step 4: Rewrite `_reorganize_account`** in `ykbot/app.py` to use the helper, widen the skip guard, and prune after (remove the temporary `theme_counts = []` placeholder left from Task 4):

```python
    async def _reorganize_account(self, account_id: str) -> None:
        # Shares the maintenance lock with /compileinterests (for this account)
        # so the two never run the same Claude session concurrently. The result
        # is logged, not broadcast (that would leak this account's interests to
        # other users); the owner sees it on their next /getinterests.
        maintenance_key = self._maintenance_key(account_id)
        async with self._claude.lock_for(maintenance_key):
            interests = self._interests_for(account_id)
            entries = interests.list()
            theme_counts = self._recent_theme_counts(account_id)

            # Nothing to do without either enough interests to reorganize or any
            # request history to learn from.
            if len(entries) < 2 and not theme_counts:
                return

            prompt = self._build_reorganize_prompt(entries, theme_counts)
            raw_reply = await self._claude.run(maintenance_key, prompt)
            new_entries = InterestsStore.parse_entries(raw_reply)
            if not new_entries:
                logger.warning(
                    "Interests reorganize for %s returned nothing parseable, leaving its interests.md untouched",
                    account_id,
                )
            else:
                interests.replace_all(new_entries)
                logger.info(
                    "Reorganized interests for %s: %d -> %d entries",
                    account_id, len(entries), len(new_entries),
                )

            # Prune the log to the window after the daily pass (the manual
            # /compileinterests path deliberately does not prune).
            if self._config.news_learning_enabled:
                self._news_history_for(account_id).prune(
                    self._config.news_history_window_days,
                    self._config.news_history_max_entries,
                )
```

- [ ] **Step 5: Wire `handle_compile_interests`** to fold in history too — replace the temporary `theme_counts = []  # TODO(Task 5)...` placeholder (added by the Task 4 fix) with the shared helper, and drop the TODO comment:

```python
        theme_counts = self._recent_theme_counts(account_id)
        prompt = self._build_reorganize_prompt(entries, theme_counts)
```
Leave the rest of `handle_compile_interests` unchanged. It does NOT prune (pruning is the daily job's responsibility).

- [ ] **Step 6: Run tests + gates**

Run: `python3 -m unittest tests.test_reorganize -v && python3 -m pyflakes bot.py ykbot/ && python3 -m unittest discover -s tests -t .`
Expected: all PASS (the original `ReorganizeAccountTests`, the Task 4 `CompileInterestsCommandTests`, and the new `ReorganizeLearningTests` + `CompileInterestsLearningTests`), pyflakes clean.

- [ ] **Step 7: Commit**

```bash
git add ykbot/app.py tests/test_reorganize.py
git commit -m "Feed recent /news themes into the daily pass and /compileinterests"
```

---

### Task 6: Document the behavior

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a subsection** near the interests/`/news` docs describing: `/news` subjects are logged per account to `accounts/<id>/news_history.jsonl`; the daily pass folds recurring themes into interests; it is silent (seen via `/getinterests`), additive, and never drops manual interests; and the env vars `NEWS_LEARNING_ENABLED` (default true), `NEWS_HISTORY_WINDOW_DAYS` (60), `NEWS_HISTORY_MAX_ENTRIES` (100). Mention `news_history.jsonl` is gitignored real data (confirm it matches an existing ignore rule such as `accounts/`).

- [ ] **Step 2: Verify** the ignore actually covers it: `git check-ignore accounts/amy/news_history.jsonl` (should print the path if ignored). If `accounts/` is already ignored, nothing to change.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document passive interest learning from /news"
```

---

### Task 7: Final verification

- [ ] **Step 1: Full gate**

Run: `python3 -c "import bot" && python3 -m pyflakes bot.py ykbot/ && python3 -m unittest discover -s tests -t . && python3 -m pytest tests/ -q`
Expected: import OK, pyflakes clean, all tests green under both runners.

- [ ] **Step 2: Live smoke test** — restart the bot and confirm a clean startup, then that a `/news` request writes a `news_history.jsonl` under the live account dir. Confirm `bot.log` shows no traceback.

- [ ] **Step 3:** Merge the `news-interest-learning` branch per the team's normal flow (PR or fast-forward), or hand back for review.

## Self-Review notes

- Spec coverage: capture (Task 3), learn/promotion (Tasks 4–5), config (Task 1), retention/prune (Tasks 2, 5), silent/private (unchanged behavior in Task 5), error handling (Tasks 2, 3, 5), tests (every task) — all covered.
- The `< 2 entries` skip was widened so learning works for accounts with few manual interests — an intentional behavior change from the old reorganize, covered by `test_skips_when_no_interests_and_no_history` and `test_disabled_learning_ignores_history`.
- `_build_reorganize_prompt` signature change is internal (only caller is `_reorganize_account`; no test pins the old signature) — verified via grep before planning.
