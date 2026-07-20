# App Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take blocking store I/O off the event loop, then extract the bus domain out of `YKBot` behind a shared services object.

**Architecture:** Two independent pieces. Piece 1 (Tasks 1-4) splits every store-touching helper into one of two shapes — a **pure builder** that takes plain data, or an **async fetcher** that does `await asyncio.to_thread(...)` — and wraps the remaining store calls in async bodies. Piece 2 (Tasks 5-7) introduces `BotServices`, moves the ~20 bus methods into `ykbot/handlers/bus.py` as `BusHandlers(services)`, and leaves two seams (`_consume_awaiting`, `_build_command_specs`) on `YKBot` delegating inward.

**Tech Stack:** Python 3.12, python-telegram-bot 21.6, stdlib `unittest`, `asyncio.to_thread`.

**Spec:** `docs/superpowers/specs/2026-07-20-app-decomposition-design.md`

## Global Constraints

- Branch: `app-decomposition`. Base commit `7e0baa4` (the spec).
- **These are pure refactors. No user-visible behavior changes.** The existing 457 tests are the specification.
- **If an existing test needs editing to pass, STOP and report it** — in Piece 1 it means behavior changed. The one sanctioned exception is Task 6, where bus tests are deliberately retargeted from `bot.YKBot` to `BusHandlers`.
- Test command: `python3 -m unittest discover -s tests -t .` — 457 tests, must be green after every task.
- Static gate: `python3 -m pyflakes bot.py ykbot/` — must print nothing.
- Coverage must stay at **100%**: `python3 -m coverage run -m unittest discover -s tests -t . && python3 -m coverage report`. Source list and `fail_under = 100` live in `pyproject.toml`, so no flags are needed. No `# pragma: no cover`.
- **The two shapes.** A helper that touches a store becomes exactly one of:
  - **pure builder** — takes the data as a parameter, stays `def`, touches no store, no `self._config` reads of store data. Matches the existing convention in `ykbot/menus.py`.
  - **async fetcher** — becomes `async def`, does `await asyncio.to_thread(...)`, returns data.
- **Do NOT make the store classes async.** The thread hop belongs at the call boundary. Stores stay sync and are called from sync contexts (including tests).
- **Do NOT cache file contents in memory.** Reading per request is deliberate — it lets a hand-edit of `interests.md` / `accounts.hash` / `.env` apply without a restart, which the standing runbook's Phase 0 depends on.
- **Leave `_is_authorized` alone.** It looks like a hot-path I/O site but `AllowlistStore` holds its ids in a `set` in memory; `is_allowed` is a set lookup.
- **Leave `_warn_about_missing_config` alone.** It reads from disk but runs from `run()` at startup, before the event loop exists.
- **Callback safety:** every callback-reachable reply must use `update.effective_message`, never `update.message`. Test moved callback handlers with `FakeUpdate(callback_only=True, callback_data=...)` — the default double aliases the two and cannot catch a violation.
- Never recreate, activate, or delete the repo `.venv` (Windows-layout). If tools are missing: `pip install --break-system-packages -r requirements-dev.txt pyflakes coverage`.
- Commit after each task. Do not push.

---

## File structure

| File | Responsibility | Piece |
|---|---|---|
| `ykbot/app.py` | Shrinks. Keeps construction, registration, command specs, auth, shared reply/claude helpers, text-capture dispatch, plain-text path, reorganize loop. | 1 & 2 |
| `ykbot/services.py` | **New.** `BotServices` — the shared dependency bundle handler classes receive. | 2 |
| `ykbot/handlers/__init__.py` | **New.** Empty package marker. | 2 |
| `ykbot/handlers/bus.py` | **New.** `BusHandlers` — the ~20 bus methods. | 2 |
| `tests/test_pure_builders.py` | **New.** Pins that the converted builders take data and touch no store. | 1 |
| `tests/test_bus_*.py`, `tests/test_locked_paths.py`, `tests/test_menu_home_screens.py` | Retargeted at `BusHandlers`. | 2 |

---

# PIECE 1 — Take blocking store reads off the event loop

### Task 1: Make the two bus builders pure

`_build_fav_menu` reads `bus_locations.list()`; `_build_arrivals_keyboard` reads `bus_locations.has_code()`. Both are sync, so neither can `await`. Hoist the reads to their async callers.

`_build_near_menu` already takes `stops` as data — it is the model to copy, and needs no change.

**Files:**
- Modify: `ykbot/app.py` — `_build_fav_menu` (1091), `_build_arrivals_keyboard` (1300), and their call sites (1088, 1143, 1163, 1177, 1181, 1200, 1217, 1283)
- Test: `tests/test_pure_builders.py` (create)

**Interfaces:**
- Produces:
  - `_build_fav_menu(self, locations: dict) -> tuple[str, InlineKeyboardMarkup]` — was `(self, account_id: str)`
  - `_build_arrivals_keyboard(self, stop_code: str, is_favorite: bool) -> InlineKeyboardMarkup` — was `(self, stop_code: str, account_id: str)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pure_builders.py`:

```python
"""The keyboard builders take plain data and touch no store.

A sync builder that reads from disk blocks the event loop and cannot be
moved off it, because a `def` cannot await. Keeping them pure is also the
convention already used by every builder in ykbot/menus.py.
"""

import unittest

import bot
from tests.helpers import BotTestCase, make_config


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class ExplodingStore:
    """Any store access at all is a failure for a pure builder."""

    def __getattr__(self, name):
        raise AssertionError(f"pure builder touched the store: .{name}")


class FavMenuBuilderTests(BotTestCase):
    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        b._bus_locations_for = lambda account_id: ExplodingStore()
        return b

    def test_takes_locations_and_touches_no_store(self):
        b = self._make_bot()

        text, markup = b._build_fav_menu({"Home": "46009"})

        self.assertEqual(text, "Pick a stop:")
        self.assertIn("bus_stop:46009", datas(markup))

    def test_empty_locations_still_offers_location_and_add(self):
        b = self._make_bot()

        _text, markup = b._build_fav_menu({})

        self.assertIn("bus_request_location", datas(markup))
        self.assertIn("bus_addbycode", datas(markup))
        self.assertIn("home", datas(markup))


class ArrivalsKeyboardBuilderTests(BotTestCase):
    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        b._bus_locations_for = lambda account_id: ExplodingStore()
        return b

    def test_favorite_shows_remove_and_touches_no_store(self):
        b = self._make_bot()

        markup = b._build_arrivals_keyboard("46009", is_favorite=True)

        self.assertIn("bus_fav_remove:46009", datas(markup))
        self.assertNotIn("bus_fav_add:46009", datas(markup))

    def test_non_favorite_shows_add(self):
        b = self._make_bot()

        markup = b._build_arrivals_keyboard("46009", is_favorite=False)

        self.assertIn("bus_fav_add:46009", datas(markup))
        self.assertNotIn("bus_fav_remove:46009", datas(markup))

    def test_nav_row_is_unchanged(self):
        b = self._make_bot()

        markup = b._build_arrivals_keyboard("46009", is_favorite=False)

        self.assertIn("bus_refresh:46009", datas(markup))
        self.assertIn("bus_back", datas(markup))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_pure_builders -v`
Expected: FAIL — `AssertionError: pure builder touched the store: .list` (and `.has_code`), because the builders still fetch their own data.

- [ ] **Step 3: Make `_build_fav_menu` pure**

Replace `_build_fav_menu` (app.py:1091) with:

```python
    def _build_fav_menu(self, locations: dict) -> "tuple[str, InlineKeyboardMarkup]":
        """Pure: takes the saved stops rather than reading them. A sync
        builder cannot await, so the disk read has to happen in the async
        caller — same shape as _build_near_menu and everything in menus.py."""
        buttons = [
            [InlineKeyboardButton(name, callback_data=f"bus_stop:{code}")]
            for name, code in locations.items()
        ]
        buttons.append(
            [InlineKeyboardButton("📍 Send my location", callback_data="bus_request_location")]
        )
        buttons.append(
            [InlineKeyboardButton("➕ Add a stop by code", callback_data="bus_addbycode")]
        )
        buttons.append([menus.HOME_BUTTON])
        return "Pick a stop:", InlineKeyboardMarkup(buttons)
```

- [ ] **Step 4: Make `_build_arrivals_keyboard` pure**

Replace `_build_arrivals_keyboard` (app.py:1300) with:

```python
    def _build_arrivals_keyboard(self, stop_code: str, is_favorite: bool) -> InlineKeyboardMarkup:
        """Pure: takes whether the stop is already saved rather than looking
        it up, so the caller can do that read off the event loop."""
        if is_favorite:
            fav_button = InlineKeyboardButton(
                "🗑 Remove from preferred", callback_data=f"bus_fav_remove:{stop_code}"
            )
        else:
            fav_button = InlineKeyboardButton(
                "⭐ Add as preferred", callback_data=f"bus_fav_add:{stop_code}"
            )
        nav_row = [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"bus_refresh:{stop_code}"),
            InlineKeyboardButton("⬅️ Back", callback_data="bus_back"),
        ]
        return InlineKeyboardMarkup([nav_row, [fav_button]])
```

- [ ] **Step 5: Add an async fetcher for the favourite check**

The `is_favorite` lookup is needed at five call sites. Add this helper next to `_build_arrivals_keyboard` so the read is written once:

```python
    async def _arrivals_keyboard_for(self, stop_code: str, account_id: str) -> InlineKeyboardMarkup:
        """Async fetcher: does the store read off the event loop, then hands
        plain data to the pure builder."""
        is_favorite = await asyncio.to_thread(
            self._bus_locations_for(account_id).has_code, stop_code
        )
        return self._build_arrivals_keyboard(stop_code, is_favorite)
```

- [ ] **Step 6: Update every call site**

`_open_bus_menu` (app.py:1088) — replace the body's middle two lines:

```python
        context.user_data["bus_menu"] = {"kind": "fav"}
        locations = await asyncio.to_thread(self._bus_locations_for(account_id).list)
        text, markup = self._build_fav_menu(locations)
        await update.effective_message.reply_text(text, reply_markup=markup)
```

`handle_bus_back` (app.py:1177 and 1181) — both `_build_fav_menu(account_id)` calls become:

```python
            locations = await asyncio.to_thread(self._bus_locations_for(account_id).list)
            text, markup = self._build_fav_menu(locations)
```

Replace all five `self._build_arrivals_keyboard(stop_code, account_id)` calls (app.py:1143, 1163, 1200, 1217, 1283) with:

```python
await self._arrivals_keyboard_for(stop_code, account_id)
```

Note these appear inside `query.edit_message_text(...)` argument lists — assign to a local first where the line would otherwise get long:

```python
        markup = await self._arrivals_keyboard_for(stop_code, account_id)
        await query.edit_message_text(text, reply_markup=markup)
```

- [ ] **Step 7: Run the tests**

Run: `python3 -m unittest tests.test_pure_builders -v`
Expected: PASS, 5 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK` with 462 tests, pyflakes silent.

If an existing bus test fails, read it before touching it — it is probably calling `_build_fav_menu(account_id)` or `_build_arrivals_keyboard(stop_code, account_id)` directly with the old signature. Updating such a call is expected. Changing an *assertion* is not — stop and report if you need to.

- [ ] **Step 8: Commit**

```bash
git add ykbot/app.py tests/test_pure_builders.py
git commit -m "Make the bus keyboard builders pure so their reads can move off the loop"
```

---

### Task 2: Make the three remaining sync I/O helpers pure or async

`_build_news_prompt` and `_format_user_listing` become pure builders. `_recent_theme_counts` is a *fetch*, not a builder — it becomes an async fetcher.

**Files:**
- Modify: `ykbot/app.py` — `_recent_theme_counts` (107), `_build_news_prompt` (395), `_format_user_listing` (1016), and call sites (902, 1622, 704, 1014, 1047)
- Test: `tests/test_pure_builders.py` (extend)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `async _recent_theme_counts(self, account_id: str) -> list` — was `def`
  - `_build_news_prompt(self, subject: str, interests: list) -> str` — was `(self, subject, account_id)`
  - `_format_user_listing(self, account_ids: list) -> str` — was `(self)`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pure_builders.py`, before the `if __name__` block:

```python
class NewsPromptBuilderTests(BotTestCase):
    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        b._interests_for = lambda account_id: ExplodingStore()
        return b

    def test_takes_interests_and_touches_no_store(self):
        b = self._make_bot()

        prompt = b._build_news_prompt("space", ["rockets", "telescopes"])

        self.assertIn("space", prompt)
        self.assertIn("rockets", prompt)

    def test_empty_interests_still_builds_a_prompt(self):
        b = self._make_bot()

        prompt = b._build_news_prompt("space", [])

        self.assertIn("space", prompt)


class UserListingBuilderTests(BotTestCase):
    def test_takes_account_ids_and_marks_sessions(self):
        b = bot.YKBot(make_config(self.tmp_path))

        listing = b._format_user_listing(["alice", "bob"])

        self.assertIn("alice", listing)
        self.assertIn("bob", listing)
        self.assertIn("🔒 locked", listing)

    def test_empty_list_says_so(self):
        b = bot.YKBot(make_config(self.tmp_path))

        self.assertEqual(b._format_user_listing([]), "No accounts configured.")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_pure_builders -v`
Expected: FAIL — `TypeError` on the new signatures, and `AssertionError: pure builder touched the store: .list`.

- [ ] **Step 3: Make `_recent_theme_counts` an async fetcher**

Replace it (app.py:107) with:

```python
    async def _recent_theme_counts(self, account_id: str) -> list:
        """Recent /news request themes with counts for this account, or an
        empty list when learning is disabled. Shared by the daily reorganize
        job and the manual /compileinterests command so they behave the same.

        Async because it reads the history file: the read goes to a worker
        thread so it doesn't stall the event loop."""
        if not self._config.news_learning_enabled:
            return []
        return await asyncio.to_thread(
            self._news_history_for(account_id).theme_counts,
            self._config.news_history_window_days,
            self._config.news_history_max_entries,
        )
```

Both call sites (app.py:902 in `_do_compile_interests`, app.py:1622 in `_reorganize_account`) are already inside `async def`, so add `await`:

```python
        theme_counts = await self._recent_theme_counts(account_id)
```

- [ ] **Step 4: Make `_build_news_prompt` pure**

Change its signature and drop the fetch. The first two lines of app.py:395 become:

```python
    def _build_news_prompt(self, subject: str, interests: list) -> str:
        if interests:
```

Leave the rest of the method body exactly as it is.

Its one call site, in `_do_news` (app.py:704), becomes:

```python
        interests = await asyncio.to_thread(self._interests_for(account_id).list)
        reply = await self._run_claude(
            update, context, self._build_news_prompt(subject, interests), web_tools=True
        )
```

- [ ] **Step 5: Make `_format_user_listing` pure**

Replace it (app.py:1016) with:

```python
    def _format_user_listing(self, account_ids: list) -> str:
        """Pure: takes the account ids rather than reading them. Session
        state is in-memory, so has_active_session stays a direct call."""
        if not account_ids:
            return "No accounts configured."
        return "\n".join(
            f"{account_id} — {'🟢 active' if self._accounts.has_active_session(account_id) else '🔒 locked'}"
            for account_id in account_ids
        )
```

`handle_get_users` (app.py:1014) becomes:

```python
        account_ids = await asyncio.to_thread(self._accounts.list_ids)
        await update.message.reply_text(self._format_user_listing(account_ids))
```

`handle_admin_action`'s `getusers` branch (app.py:1047) becomes:

```python
            account_ids = await asyncio.to_thread(self._accounts.list_ids)
            await self._safe_edit(
                query, self._format_user_listing(account_ids), reply_markup=menus.admin_keyboard()
            )
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m unittest tests.test_pure_builders -v`
Expected: PASS, 9 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK` with 466 tests, pyflakes silent.

Existing tests calling these three with old signatures need their *calls* updated. Assertions must not change — stop and report if one does.

- [ ] **Step 7: Commit**

```bash
git add ykbot/app.py tests/test_pure_builders.py
git commit -m "Make the news-prompt and user-listing builders pure; fetch themes off-thread"
```

---

### Task 3: Wrap the remaining store calls in async bodies

Every remaining disk-touching store call that sits inside an `async def`. In-memory operations are **excluded** — `AccountStore`'s session and throttle methods (`has_active_session`, `account_for`, `is_unlocked`, `lock`, `try_unlock`, `register_failed_unlock`, `clear_failed_unlocks`, `unlock_retry_after`) work on in-memory dicts, and `AllowlistStore.is_allowed` is a set lookup.

Note `try_unlock` *can* write (the scrypt upgrade path) but is on the password hot path and its own read is already guarded; leave it.

**Files:**
- Modify: `ykbot/app.py` at the lines listed below
- Test: no new test file — the existing 466 tests cover every one of these paths, and must stay green unmodified.

**Interfaces:**
- Consumes: nothing. Produces: no signature changes.

- [ ] **Step 1: Confirm the target list**

Run this scan to regenerate the list and confirm it matches what you are about to change:

```bash
python3 - <<'PY'
import re
src = open('ykbot/app.py').read().split('\n')
cur = None; is_async = False
STORE = re.compile(r'(_interests_for|_bus_locations_for|_news_history_for)\([^)]*\)\.(\w+)'
                   r'|self\._accounts\.(\w+)|self\._message_log\.(\w+)|\binterests\.(\w+)\(')
SKIP = {'has_active_session', 'account_for', 'is_unlocked', 'lock', 'try_unlock',
        'register_failed_unlock', 'clear_failed_unlocks', 'unlock_retry_after'}
for i, l in enumerate(src, 1):
    m = re.match(r'    (async )?def (\w+)', l)
    if m:
        is_async = bool(m.group(1)); cur = m.group(2)
    mm = STORE.search(l)
    if mm and is_async:
        meth = next((g for g in mm.groups()[1:] if g), '?')
        if meth in SKIP or 'to_thread' in l:
            continue
        print(f"{i:5} {cur:28} {l.strip()[:70]}")
PY
```

- [ ] **Step 2: Wrap each call**

The transformation is mechanical. A read:

```python
        entries = self._interests_for(account_id).list()
```
becomes
```python
        entries = await asyncio.to_thread(self._interests_for(account_id).list)
```

A call with arguments passes them to `to_thread` rather than calling:

```python
        index = self._interests_for(account_id).add(text)
```
becomes
```python
        index = await asyncio.to_thread(self._interests_for(account_id).add, text)
```

Apply to these sites (line numbers before your edits shift them — work bottom-up, or re-run the scan between edits):

| Line | Method | Call |
|---|---|---|
| 610 | `handle_interest_pick` | `self._interests_for(account_id).list()` |
| 632 | `handle_interest_select` | `self._interests_for(account_id).list()` |
| 699 | `_do_news` | `self._news_history_for(account_id).append(subject)` |
| 781 | `_do_add_interest` | `self._interests_for(account_id).add(text)` |
| 793 | `_do_show_interests` | `self._interests_for(account_id).list()` |
| 820 | `handle_update_interests` | `interests.list()` |
| 842, 848 | `_do_update_interest` | `interests.list()`, `interests.update(index, new_text)` |
| 864 | `handle_remove_interest` | `interests.list()` |
| 874, 878 | `_do_remove_interest` | `interests.remove(index)`, `interests.list()` |
| 901, 923 | `_do_compile_interests` | `interests.list()`, `interests.replace_all(new_entries)` |
| 957 | `handle_add_user` | `self._accounts.add_hash(name, password_hash)` |
| 978 | `_do_remove_user` | `self._accounts.remove_account(name)` |
| 1059 | `handle_admin_pick_remove` | `self._accounts.list_ids()` |
| 1195 | `handle_bus_fav_add` | `self._bus_locations_for(account_id).add(name, stop_code)` |
| 1212 | `handle_bus_fav_remove` | `self._bus_locations_for(account_id).remove_by_code(stop_code)` |
| 1247 | `_prompt_add_bus_stop` | `self._bus_locations_for(account_id).has_code(code)` |
| 1279 | `handle_bus_confirm_add` | `self._bus_locations_for(account_id).add(name, stop_code)` |
| 1393 | `_consume_awaiting` | `self._interests_for(account_id).list()` |
| 1463 | `_log_message` | `self._message_log.log(user.username, user.id, text)` |
| 1605 | `_reorganize_interests` | `self._accounts.list_ids()` |
| 1621, 1638, 1647 | `_reorganize_account` | `interests.list()`, `interests.replace_all(...)`, `news_history.prune(...)` |

Careful at line 878: `len(interests.list())` sits inside an f-string. Hoist it:

```python
        removed = await asyncio.to_thread(interests.remove, index)
        if removed is None:
            remaining = await asyncio.to_thread(interests.list)
            await self._reply_result(
                update,
                f"No interest #{index}. Valid range: 1-{len(remaining)}. See /getinterests.",
                from_menu,
            )
            return
```

- [ ] **Step 3: Verify nothing was missed**

Re-run the Step 1 scan. Expected: no output.

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK` with 466 tests unchanged, pyflakes silent.

**These tests must pass with zero edits.** If one fails, you have changed behavior — most likely by awaiting something that returns a store object rather than data, or by dropping a return value. Stop and report.

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py
git commit -m "Move remaining store reads and writes off the event loop"
```

---

### Task 4: Verify Piece 1 and confirm coverage

**Files:** none modified — verification only.

- [ ] **Step 1: Confirm no sync method touches a store**

```bash
python3 - <<'PY'
import re
src = open('ykbot/app.py').read().split('\n')
cur = None; is_async = False
STORE = re.compile(r'(_interests_for|_bus_locations_for|_news_history_for)\([^)]*\)\.\w+'
                   r'|self\._accounts\.(list_ids|add_hash|remove_account|is_configured)'
                   r'|self\._message_log\.log')
bad = []
for i, l in enumerate(src, 1):
    m = re.match(r'    (async )?def (\w+)', l)
    if m:
        is_async = bool(m.group(1)); cur = m.group(2)
    if cur and STORE.search(l) and not is_async and cur != '_warn_about_missing_config':
        bad.append((i, cur))
print("sync store access:", bad if bad else "none (expected)")
PY
```
Expected: `none (expected)`. `_warn_about_missing_config` is excluded deliberately — it runs at startup, before the loop.

- [ ] **Step 2: Full gate**

```bash
python3 -m unittest discover -s tests -t . 2>&1 | tail -3
python3 -m pyflakes bot.py ykbot/ && echo PYFLAKES_CLEAN
python3 -m coverage run -m unittest discover -s tests -t . && python3 -m coverage report
```
Expected: 466 tests `OK`; pyflakes clean; coverage TOTAL 100% (the `fail_under = 100` in `pyproject.toml` makes `coverage report` exit non-zero otherwise).

- [ ] **Step 3: Report**

Piece 1 is complete. Report the test count and coverage. No commit — nothing changed.

---

# PIECE 2 — Extract the bus domain

### Task 5: Introduce BotServices

The dependency bundle handler classes receive. Introduced on its own so its shape can be reviewed before any handler moves.

**Files:**
- Create: `ykbot/services.py`
- Modify: `ykbot/app.py` — `__init__` (84), `ykbot/__init__.py` (re-export)
- Test: `tests/test_services.py` (create)

**Interfaces:**
- Produces: `BotServices` with attributes `config`, `accounts`, `allowlist`, `claude`, `trivia`, `lta`, `stops`, `message_log`, `interests_for`, `bus_locations_for`, `news_history_for`, `require_session`, `require_admin`, `is_admin`, `reply_result`, `send_formatted`, `run_claude`, `safe_edit`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_services.py`:

```python
"""BotServices is the one-way dependency bundle handler classes receive.

The point is that a handler class can be built against this object alone —
no YKBot, no back-reference — so the dependency direction stays one-way.
"""

import unittest

import bot
from ykbot.services import BotServices
from tests.helpers import BotTestCase, make_config


class BotServicesTests(BotTestCase):
    def test_bot_exposes_its_services(self):
        b = bot.YKBot(make_config(self.tmp_path))

        self.assertIsInstance(b.services, BotServices)

    def test_carries_the_singleton_stores(self):
        b = bot.YKBot(make_config(self.tmp_path))
        svc = b.services

        self.assertIs(svc.config, b._config)
        self.assertIs(svc.accounts, b._accounts)
        self.assertIs(svc.allowlist, b._allowlist)
        self.assertIs(svc.claude, b._claude)
        self.assertIs(svc.trivia, b._trivia)
        self.assertIs(svc.lta, b._lta)
        self.assertIs(svc.stops, b._bus_stops)
        self.assertIs(svc.message_log, b._message_log)

    def test_per_account_factories_are_callable(self):
        b = bot.YKBot(make_config(self.tmp_path))
        svc = b.services

        self.assertIsNotNone(svc.interests_for("alice"))
        self.assertIsNotNone(svc.bus_locations_for("alice"))
        self.assertIsNotNone(svc.news_history_for("alice"))

    def test_shared_helpers_are_bound_to_the_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        svc = b.services

        self.assertEqual(svc.require_session, b._require_session)
        self.assertEqual(svc.require_admin, b._require_admin)
        self.assertEqual(svc.is_admin, b._is_admin)
        self.assertEqual(svc.reply_result, b._reply_result)
        self.assertEqual(svc.send_formatted, b._send_formatted)
        self.assertEqual(svc.run_claude, b._run_claude)
        self.assertEqual(svc.safe_edit, b._safe_edit)

    def test_can_be_constructed_without_a_bot(self):
        """The whole point: a handler class is testable against a stub."""
        svc = BotServices(
            config=make_config(self.tmp_path),
            accounts=None, allowlist=None, claude=None, trivia=None,
            lta=None, stops=None, message_log=None,
            interests_for=lambda a: None,
            bus_locations_for=lambda a: None,
            news_history_for=lambda a: None,
            require_session=None, require_admin=None, is_admin=None,
            reply_result=None, send_formatted=None, run_claude=None, safe_edit=None,
        )

        self.assertIsNotNone(svc.config)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_services -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ykbot.services'`.

- [ ] **Step 3: Create `ykbot/services.py`**

```python
"""The shared dependency bundle handler classes receive.

YKBot grew to 99 methods because every handler needed the same handful of
things: config, the stores, and the shared reply/auth/claude helpers. Rather
than have extracted handler classes hold a back-reference to YKBot — which
would keep the coupling and merely hide it behind a file boundary — they
take this object. The dependency direction stays one-way, and a handler can
be constructed against a stub.

Deliberately a plain dataclass with no behavior: it is a bundle, not a
service locator, and it must not accumulate logic of its own.
"""

from dataclasses import dataclass
from typing import Callable

from ykbot.accounts import AccountStore, AllowlistStore
from ykbot.bus import BusStopsCache, LtaBusClient
from ykbot.claude import ClaudeClient
from ykbot.config import Config
from ykbot.message_log import MessageLogStore
from ykbot.trivia import TriviaBook


@dataclass(frozen=True)
class BotServices:
    config: Config

    # Singleton stores and clients. Concrete types, not `object`: none of
    # these modules import this one, so there is no import cycle, and a
    # precise type is the whole point of a dependency bundle.
    accounts: AccountStore
    allowlist: AllowlistStore
    claude: ClaudeClient
    trivia: TriviaBook
    lta: LtaBusClient
    stops: BusStopsCache
    message_log: MessageLogStore

    # Per-account store factories.
    interests_for: Callable
    bus_locations_for: Callable
    news_history_for: Callable

    # Shared helpers, bound to the owning YKBot.
    require_session: Callable
    require_admin: Callable
    is_admin: Callable
    reply_result: Callable
    send_formatted: Callable
    run_claude: Callable
    safe_edit: Callable
```

- [ ] **Step 4: Build it in `YKBot.__init__`**

At the end of `YKBot.__init__` (app.py:84), after every `self._x` is assigned, add:

```python
        self.services = BotServices(
            config=self._config,
            accounts=self._accounts,
            allowlist=self._allowlist,
            claude=self._claude,
            trivia=self._trivia,
            lta=self._lta,
            stops=self._bus_stops,
            message_log=self._message_log,
            interests_for=self._interests_for,
            bus_locations_for=self._bus_locations_for,
            news_history_for=self._news_history_for,
            require_session=self._require_session,
            require_admin=self._require_admin,
            is_admin=self._is_admin,
            reply_result=self._reply_result,
            send_formatted=self._send_formatted,
            run_claude=self._run_claude,
            safe_edit=self._safe_edit,
        )
```

Add the import at the top of `ykbot/app.py`:

```python
from ykbot.services import BotServices
```

Note the one name change: `YKBot.__init__` calls the stops cache `self._bus_stops`, while the `BotServices` field is `stops`. That is deliberate — inside `BusHandlers` the `bus_` prefix is redundant. Every other field keeps its existing name.

- [ ] **Step 5: Re-export**

In `ykbot/__init__.py`, add `from ykbot.services import BotServices` and `"BotServices"` to `__all__`. In `bot.py`, add `BotServices` to both the import list and `__all__`, keeping the existing alphabetical-ish grouping.

- [ ] **Step 6: Run the tests**

Run: `python3 -m unittest tests.test_services -v`
Expected: PASS, 5 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK` with 471 tests, pyflakes silent.

- [ ] **Step 7: Commit**

```bash
git add ykbot/services.py ykbot/app.py ykbot/__init__.py bot.py tests/test_services.py
git commit -m "Add BotServices, the one-way dependency bundle for handler classes"
```

---

### Task 6: Move the bus domain into ykbot/handlers/bus.py

The pilot. Twenty methods move; `YKBot` keeps two seams that delegate inward.

**Files:**
- Create: `ykbot/handlers/__init__.py`, `ykbot/handlers/bus.py`
- Modify: `ykbot/app.py` (remove the moved methods; wire `self._bus`), `ykbot/__init__.py`, `bot.py`
- Test: retarget `tests/test_bus_navigation.py`, `tests/test_bus_domain.py`, `tests/test_unlock_bus_arrivals.py`, and the bus cases in `tests/test_locked_paths.py`, `tests/test_menu_home_screens.py`, `tests/test_callback_safety.py`

**Interfaces:**
- Consumes: `BotServices` from Task 5; the pure builders from Task 1.
- Produces: `BusHandlers(services: BotServices)` exposing `handle_bus`, `open_bus_menu`, `handle_bus_request_location`, `handle_bus_callback`, `handle_bus_refresh`, `handle_bus_back`, `handle_bus_fav_add`, `handle_bus_fav_remove`, `handle_bus_add_by_code`, `prompt_add_bus_stop`, `handle_bus_confirm_add`, `handle_bus_cancel_add`, `handle_location`. Internal: `_build_fav_menu`, `_build_near_menu`, `_build_arrivals_keyboard`, `_arrivals_keyboard_for`, `_render_arrivals`, `_describe_stop`, `_format_arrivals`, `_build_add_confirm_keyboard`.
- `YKBot` gains `self._bus: BusHandlers`.

**Two methods lose their leading underscore** because they are now called across a module boundary: `_open_bus_menu` → `open_bus_menu` (called by `handle_menu_bus`) and `_prompt_add_bus_stop` → `prompt_add_bus_stop` (called by `_consume_awaiting`).

- [ ] **Step 1: Create the package marker**

`ykbot/handlers/__init__.py`:

```python
"""Per-domain Telegram handler classes.

Each class takes a BotServices and owns one domain's handlers, so app.py
holds wiring rather than every handler in the bot.
"""
```

- [ ] **Step 2: Move the methods**

Create `ykbot/handlers/bus.py` with this module docstring and class skeleton, then **move — do not retype —** the twenty methods listed in Interfaces from `ykbot/app.py`:

```python
"""Bus arrivals: favourite stops, nearby lookup by location, and live timings.

The first domain extracted from YKBot. Everything shared arrives through
BotServices, so this module never imports or references YKBot.
"""

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ykbot import menus
from ykbot._logging import logger
from ykbot.services import BotServices


class BusHandlers:
    """Every /bus and bus-button interaction."""

    def __init__(self, services: BotServices):
        self._svc = services
```

Mechanical substitutions inside every moved method:

| In `app.py` | In `bus.py` |
|---|---|
| `self._require_session(` | `self._svc.require_session(` |
| `self._reply_result(` | `self._svc.reply_result(` |
| `self._safe_edit(` | `self._svc.safe_edit(` |
| `self._bus_locations_for(` | `self._svc.bus_locations_for(` |
| `self._lta` | `self._svc.lta` |
| `self._bus_stops` | `self._svc.stops` |
| `self._config` | `self._svc.config` |
| `self._trivia` | `self._svc.trivia` |
| `self._open_bus_menu(` | `self.open_bus_menu(` |
| `self._prompt_add_bus_stop(` | `self.prompt_add_bus_stop(` |

Everything else (`self._build_fav_menu`, `self._render_arrivals`, …) stays `self.`, because those methods move too.

Delete each moved method from `ykbot/app.py`.

- [ ] **Step 3: Wire it up in YKBot**

In `YKBot.__init__`, immediately after the `self.services = BotServices(...)` block from Task 5:

```python
        self._bus = BusHandlers(self.services)
```

Import at the top of `ykbot/app.py`:

```python
from ykbot.handlers.bus import BusHandlers
```

In `_build_application`, repoint every bus registration at `self._bus`:

```python
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_callback, pattern=r"^bus_stop:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_refresh, pattern=r"^bus_refresh:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_back, pattern=r"^bus_back$"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_fav_add, pattern=r"^bus_fav_add:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_fav_remove, pattern=r"^bus_fav_remove:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_confirm_add, pattern=r"^bus_confirmadd:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_cancel_add, pattern=r"^bus_cancel_add$"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_add_by_code, pattern=r"^bus_addbycode$"))
        app.add_handler(
            CallbackQueryHandler(self._bus.handle_bus_request_location, pattern=r"^bus_request_location$")
        )
        app.add_handler(MessageHandler(filters.LOCATION, self._bus.handle_location))
```

Keep the existing registration order — pattern precedence matters.

- [ ] **Step 4: Repoint the two seams**

`_build_command_specs` — the `bus` entry's handler becomes `self._bus.handle_bus`.

`handle_menu_bus` (app.py:565) — its body becomes:

```python
        await self._bus.open_bus_menu(update, context, account_id)
```

`_consume_awaiting`'s `bus_code` branch:

```python
        if kind == "bus_code":
            await self._bus.prompt_add_bus_stop(update, context, account_id, text)
```

- [ ] **Step 5: Re-export**

In `ykbot/__init__.py` add `from ykbot.handlers.bus import BusHandlers` and `"BusHandlers"` to `__all__`; mirror in `bot.py`.

- [ ] **Step 6: Retarget the tests**

Find every test reaching for a moved method:

```bash
grep -rln "_build_fav_menu\|_build_near_menu\|_build_arrivals_keyboard\|_render_arrivals\|_open_bus_menu\|_prompt_add_bus_stop\|handle_bus\|handle_location\|_format_arrivals\|_describe_stop" tests/
```

For each hit, change `news_bot.handle_bus_refresh(...)` to `news_bot._bus.handle_bus_refresh(...)` (and likewise for the internals). Tests that construct a `YKBot` keep doing so — reaching through `._bus` keeps the diff small and keeps exercising real wiring.

**Change call targets only. Do not change a single assertion.** These tests are the specification for a refactor that must not alter behavior; an assertion that needs editing means something broke. Stop and report.

- [ ] **Step 7: Add a callback-safety test for the moved handlers**

Append to `tests/test_callback_safety.py`:

```python
class BusHandlerCallbackSafetyTests(AsyncBotTestCase):
    """The bus handlers moved modules; re-pin that they still reply via
    effective_message. A real callback update has message = None."""

    CHAT_ID = 9100

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_bus_back_replies_without_a_message(self):
        b = self._make_bot()
        update = FakeUpdate(
            user_id=1, chat_id=self.CHAT_ID, callback_only=True, callback_data="bus_back"
        )

        await b._bus.handle_bus_back(update, FakeContext())

        self.assertIsNone(update.message)

    async def test_bus_add_by_code_replies_without_a_message(self):
        b = self._make_bot()
        update = FakeUpdate(
            user_id=1, chat_id=self.CHAT_ID, callback_only=True, callback_data="bus_addbycode"
        )

        await b._bus.handle_bus_add_by_code(update, FakeContext())

        self.assertIsNone(update.message)
```

- [ ] **Step 8: Run the tests**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK` with 473 tests, pyflakes silent.

Run: `python3 -m coverage run -m unittest discover -s tests -t . && python3 -m coverage report`
Expected: TOTAL 100%, and `ykbot/handlers/bus.py` at 100%.

- [ ] **Step 9: Confirm the extraction is clean**

```bash
grep -c "YKBot" ykbot/handlers/bus.py          # expect 0
grep -cE "handle_bus|_render_arrivals" ykbot/app.py   # expect only the self._bus.* wiring
wc -l ykbot/app.py ykbot/handlers/bus.py
```
Expected: `bus.py` never mentions `YKBot`; `app.py` drops to roughly 1330 lines.

- [ ] **Step 10: Commit**

```bash
git add ykbot/handlers ykbot/app.py ykbot/__init__.py bot.py tests/
git commit -m "Extract the bus domain into ykbot/handlers/bus.py behind BotServices"
```

---

### Task 7: Documentation and deploy

**Files:**
- Modify: `README.md`, `docs/code-quality-review-2026-07-20.md`

- [ ] **Step 1: Update the review document**

In `docs/code-quality-review-2026-07-20.md`, change finding 5's heading to `## 5. ~~MEDIUM~~ DONE — Blocking file I/O in async handlers` and finding 4's to `## 4. MEDIUM — PARTLY DONE (bus extracted) — YKBot is a god object`. Update the STATUS block at the top: finding 5 done, finding 4 partly done with interests/admin/news/menu still in `app.py`.

- [ ] **Step 2: Document the structure in README**

Add to `README.md`, after the "Button menu" section:

```markdown
### Code layout

`bot.py` is a thin entry-point shim; the implementation lives in `ykbot/`.
Service classes (accounts, claude, interests, bus, trivia, formatting, menus)
each own one concern. `ykbot/app.py` holds the `YKBot` orchestrator: wiring,
handler registration, the command list, auth, and the shared reply helpers.

Per-domain handlers live in `ykbot/handlers/`. Each takes a `BotServices`
(`ykbot/services.py`) — the shared bundle of config, stores and helpers — so
the dependency direction is one-way and a handler class never references
`YKBot`. Bus is extracted this way; the other domains still sit in `app.py`.

Stores are synchronous and read from disk per request, so hand-edits to data
files apply without a restart. Handlers therefore call them through
`asyncio.to_thread` to keep the event loop free, and keyboard builders take
plain data rather than fetching their own.
```

- [ ] **Step 3: Full gate**

```bash
python3 -m unittest discover -s tests -t . 2>&1 | tail -3
python3 -m pyflakes bot.py ykbot/ && python3 -c "import bot" && echo CLEAN
python3 -m coverage run -m unittest discover -s tests -t . && python3 -m coverage report
```
Expected: 473 tests `OK`, `CLEAN`, TOTAL 100%.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/code-quality-review-2026-07-20.md
git commit -m "Document the handlers package and close findings 4 and 5"
```

- [ ] **Step 5: Deploy**

Follow the STANDING RUNBOOK at the top of `.claude/HANDOFF.md`. Find the PID with the /proc scan — **never** `pgrep -f "python3 bot.py"`, which matches the checking shell:

```bash
for p in $(pgrep '^python3$'); do \
  tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q 'bot[.]py' \
  && echo "$p"; done
```

Stop the old instance before starting the new one:

```bash
kill <pid>
setsid nohup python3 bot.py >> bot-startup.log 2>&1 < /dev/null &
```

Note the redirect target is `bot-startup.log`, not `bot.log` — the app rotates `bot.log` itself. Verify by tailing `bot.log` for `Application started` and a following `getUpdates ... 200 OK`, no traceback, and exactly one instance from a repeat /proc scan.

- [ ] **Step 6: Live regression check**

This is a refactor with no intended behavior change, so the smoke test is a regression sweep. Ask the user to: unlock, run `/bus`, tap a saved favourite through to arrivals, tap 🔄 Refresh, tap ⬅️ Back, send a location, and add a stop by code. Then `/news <subject>` to confirm the prompt still carries interests.

- [ ] **Step 7: Report and await the merge decision**

Do not push. Report branch state and let the user choose between merging to `main` and opening a PR.
