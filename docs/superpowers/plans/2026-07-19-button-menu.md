# Inline Button Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an inline-button menu as an alternative entry point to the bot's commands (Bus / News / Interests / Summarize / End session, plus Admin for admins), while keeping every slash command working unchanged.

**Architecture:** Pure `InlineKeyboardMarkup` builders live in a new `ykbot/menus.py`; async callback handlers live on the `YKBot` orchestrator in `ykbot/app.py` alongside the existing `bus_*` handlers they mirror. Free-text actions arm a single unified `context.user_data["awaiting"]` slot consumed in `handle_plain_text`, and each action shares one `_do_*` core between its slash handler and its button flow. Navigation is stateless (rebuilt from `callback_data`); only text-capture is stateful.

**Tech Stack:** Python 3, `python-telegram-bot` (v20+, `telegram.InlineKeyboardButton/InlineKeyboardMarkup`, `CallbackQueryHandler`), stdlib `unittest`.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-19-button-menu-design.md` (read it first).
- Keep the whole suite green: `python3 -m unittest discover -s tests -t .`
- Keep line coverage at 100%: `pip install --break-system-packages coverage && python3 -m coverage run --source=ykbot,bot -m unittest discover -s tests -t . && python3 -m coverage report -m`
- Static gate clean: `python3 -m pyflakes bot.py ykbot/`
- Reuse the test doubles in `tests/helpers.py` (`FakeUpdate`, `FakeCallbackQuery`, `FakeContext`, `FakeBot`, `make_config`, `write_accounts_file`); do NOT re-roll fakes.
- Every callback handler MUST call `self._require_session(update)` (admin ones `self._require_admin(update)`) and `query.answer()` before acting — mirror the existing bus handlers exactly.
- Button visibility is NOT authorization: admin callbacks re-check `_require_admin` even though the Admin button is hidden for non-admins.
- Cores reachable from a callback MUST reply via `update.effective_message.reply_text` (NOT `update.message`, which is `None` on a real callback update). For slash invocations `effective_message is message`, so behavior is unchanged.
- The sandbox `.venv` is Windows-only — never recreate it. Install test deps with `--break-system-packages`.
- This is a code change: deploy via the STANDING RUNBOOK at the top of `.claude/HANDOFF.md` (verify while old bot runs → /proc-scan kill → `setsid nohup python3 bot.py >> bot.log 2>&1 < /dev/null &` → confirm one healthy instance). Restart only after the whole feature is merged, not between tasks.

### callback_data namespace (used across tasks)

| Pattern | Meaning | Task |
|---|---|---|
| `menu:root` / `menu:interests` / `menu:admin` | render that (sub)menu | 5, 8, 9 |
| `act:bus` | open the bus fav menu | 5 |
| `act:news` / `act:summarize` / `act:addinterest` | arm a free-text capture | 7 |
| `act:getinterests` / `act:compile` | run a no-arg action | 6 |
| `act:endsession` | show end-session confirm | 6 |
| `act:getusers` / `act:adduserteleid` | admin no-arg / capture | 9 |
| `int_pick:rm` / `int_pick:upd` | render per-interest pick keyboard | 8 |
| `int_rm:<i>` / `int_upd:<i>` | interest #i selected | 8 |
| `adm_pick_rm` | render per-account pick keyboard | 9 |
| `adm_rm:<name>` | account `<name>` selected | 9 |
| `cfm:<token>` / `cxl:<token>` | confirm / cancel a pending action | 6, 8, 9 |

All values stay ≤ 64 bytes (indices and short account ids only).

Run a single test with: `python3 -m unittest tests.<module>.<Class>.<method> -v`
Run the whole suite with: `python3 -m unittest discover -s tests -t .`

---

### Task 1: Pure keyboard builders (`ykbot/menus.py`)

**Files:**
- Create: `ykbot/menus.py`
- Test: `tests/test_menus.py`

**Interfaces:**
- Produces:
  - `top_level_keyboard(is_admin: bool) -> InlineKeyboardMarkup`
  - `interests_keyboard() -> InlineKeyboardMarkup`
  - `admin_keyboard() -> InlineKeyboardMarkup`
  - `interest_pick_keyboard(entries: list[str], action: str) -> InlineKeyboardMarkup` (`action ∈ {"rm","upd"}`; buttons `int_<action>:<i>` for 1-based index i, plus a Back button `menu:interests`)
  - `account_pick_keyboard(accounts: list[str]) -> InlineKeyboardMarkup` (buttons `adm_rm:<name>`, plus Back `menu:admin`)
  - `confirm_keyboard(token: str) -> InlineKeyboardMarkup` (`cfm:<token>` / `cxl:<token>`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_menus.py`:

```python
import unittest

from ykbot import menus


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


class TopLevelKeyboardTests(unittest.TestCase):
    def test_non_admin_has_no_admin_row(self):
        d = datas(menus.top_level_keyboard(is_admin=False))
        self.assertEqual(
            d, ["act:bus", "act:news", "menu:interests", "act:summarize", "act:endsession"]
        )
        self.assertNotIn("menu:admin", d)

    def test_admin_gets_admin_row(self):
        self.assertIn("menu:admin", datas(menus.top_level_keyboard(is_admin=True)))


class InterestsKeyboardTests(unittest.TestCase):
    def test_has_all_actions_and_back(self):
        d = datas(menus.interests_keyboard())
        for expected in (
            "act:getinterests", "act:addinterest", "int_pick:upd",
            "int_pick:rm", "act:compile", "menu:root",
        ):
            self.assertIn(expected, d)


class AdminKeyboardTests(unittest.TestCase):
    def test_has_admin_actions_and_back_but_no_adduser(self):
        d = datas(menus.admin_keyboard())
        self.assertEqual(
            set(d), {"act:getusers", "adm_pick_rm", "act:adduserteleid", "menu:root"}
        )


class PickKeyboardTests(unittest.TestCase):
    def test_interest_pick_one_button_per_entry_1_based(self):
        m = menus.interest_pick_keyboard(["a", "b", "c"], action="rm")
        d = datas(m)
        self.assertEqual(d[:3], ["int_rm:1", "int_rm:2", "int_rm:3"])
        self.assertIn("menu:interests", d)  # Back

    def test_interest_pick_upd_uses_upd_prefix(self):
        self.assertEqual(datas(menus.interest_pick_keyboard(["x"], "upd"))[0], "int_upd:1")

    def test_account_pick_one_button_per_account(self):
        m = menus.account_pick_keyboard(["alice", "bob"])
        self.assertEqual(datas(m)[:2], ["adm_rm:alice", "adm_rm:bob"])
        self.assertIn("menu:admin", datas(m))  # Back


class ConfirmKeyboardTests(unittest.TestCase):
    def test_yes_no_tokens(self):
        self.assertEqual(datas(menus.confirm_keyboard("endsession")),
                         ["cfm:endsession", "cxl:endsession"])

    def test_callback_data_within_64_bytes(self):
        m = menus.interest_pick_keyboard(["x"] * 50, "rm")
        for d in datas(m):
            self.assertLessEqual(len(d.encode("utf-8")), 64)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_menus -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ykbot.menus'`.

- [ ] **Step 3: Write minimal implementation**

Create `ykbot/menus.py`:

```python
"""Pure inline-keyboard builders for the button menu.

Each function maps plain data to a telegram.InlineKeyboardMarkup — no self,
no stores — so they're unit-testable in isolation. The async handlers that
use them live on YKBot in app.py (mirrors how app.py keeps
_build_arrivals_keyboard focused)."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def top_level_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🚌 Bus", callback_data="act:bus"),
         InlineKeyboardButton("📰 News", callback_data="act:news")],
        [InlineKeyboardButton("⭐ Interests", callback_data="menu:interests"),
         InlineKeyboardButton("🔗 Summarize", callback_data="act:summarize")],
        [InlineKeyboardButton("🔒 End session", callback_data="act:endsession")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Admin", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def interests_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 List", callback_data="act:getinterests"),
         InlineKeyboardButton("➕ Add", callback_data="act:addinterest")],
        [InlineKeyboardButton("✏️ Update", callback_data="int_pick:upd"),
         InlineKeyboardButton("🗑 Remove", callback_data="int_pick:rm")],
        [InlineKeyboardButton("🗂 Compile", callback_data="act:compile")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:root")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Get users", callback_data="act:getusers")],
        [InlineKeyboardButton("🗑 Remove user", callback_data="adm_pick_rm")],
        [InlineKeyboardButton("🆔 Allow Telegram id", callback_data="act:adduserteleid")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:root")],
    ])


def interest_pick_keyboard(entries: list, action: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{i}. {entry[:40]}", callback_data=f"int_{action}:{i}")]
        for i, entry in enumerate(entries, start=1)
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:interests")])
    return InlineKeyboardMarkup(rows)


def account_pick_keyboard(accounts: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(name, callback_data=f"adm_rm:{name}")]
        for name in accounts
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=f"cfm:{token}"),
        InlineKeyboardButton("❌ No", callback_data=f"cxl:{token}"),
    ]])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_menus -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add ykbot/menus.py tests/test_menus.py
git commit -m "Add pure inline-keyboard builders (ykbot/menus.py)"
```

---

### Task 2: Unify the awaiting-text slot

Replace the single `awaiting_bus_code` boolean with `context.user_data["awaiting"] = {"kind", "payload"}` and route it through one dispatch method. Behavior is identical for now (only `bus_code` exists); later tasks add kinds.

**Files:**
- Modify: `ykbot/app.py` (`handle_bus_add_by_code` ~L876; `handle_plain_text` ~L1020)
- Modify: `tests/test_bus_navigation.py` (the `awaiting_bus_code` tests, ~L184-200)

**Interfaces:**
- Produces:
  - `context.user_data["awaiting"]` shape `{"kind": str, "payload": dict}`.
  - `async _consume_awaiting(self, update, context, chat_id) -> bool` — if a capture is armed and the chat is unlocked, handle it and return `True`; otherwise pop nothing-or-clear and return `False` so the caller treats the text as a password attempt.

- [ ] **Step 1: Update the failing tests**

In `tests/test_bus_navigation.py`, replace the two `awaiting_bus_code` tests (the ones creating `FakeContext(user_data={"awaiting_bus_code": True})`) with the new slot shape, and assert `handle_bus_add_by_code` sets the new slot:

```python
    async def test_add_by_code_arms_awaiting_slot(self):
        news_bot = self._make_bot()
        ctx = FakeContext()
        update = self._update(callback_data="bus_addbycode")

        await news_bot.handle_bus_add_by_code(update, ctx)

        self.assertEqual(ctx.user_data["awaiting"], {"kind": "bus_code", "payload": {}})

    async def test_awaiting_bus_code_consumed_when_unlocked(self):
        news_bot = self._make_bot()
        ctx = FakeContext(user_data={"awaiting": {"kind": "bus_code", "payload": {}}})
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="46009")

        await news_bot.handle_plain_text(update, ctx)

        # Routed to the bus-stop prompt, not treated as a password.
        self.assertNotIn("awaiting", ctx.user_data)
        self.assertTrue(update.message.replies)

    async def test_awaiting_falls_through_to_password_when_locked(self):
        news_bot = self._make_bot()
        news_bot._accounts.lock(self.CHAT_ID)  # ensure locked
        ctx = FakeContext(user_data={"awaiting": {"kind": "bus_code", "payload": {}}})
        update = FakeUpdate(user_id=100, chat_id=self.CHAT_ID, text="hunter2")

        await news_bot.handle_plain_text(update, ctx)

        # Locked → the text was treated as a (correct) password, not a bus code.
        self.assertIsNotNone(news_bot._accounts.account_for(self.CHAT_ID))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_bus_navigation -v`
Expected: FAIL (handler still sets/reads `awaiting_bus_code`).

- [ ] **Step 3: Implement the unified slot**

In `ykbot/app.py`, change `handle_bus_add_by_code` to set the new slot:

```python
        context.user_data["awaiting"] = {"kind": "bus_code", "payload": {}}
```

Add a dispatch method near `handle_plain_text` (later tasks extend the `if/elif`):

```python
    async def _consume_awaiting(self, update, context, chat_id) -> bool:
        """If a text-capture is armed AND the chat is unlocked, handle the
        text and return True. If locked, clear the slot and return False so
        the caller treats the message as a password attempt (indistinguishable
        from a wrong guess — no capture leaks past the lock)."""
        awaiting = context.user_data.pop("awaiting", None)
        if not awaiting:
            return False
        account_id = self._accounts.account_for(chat_id)
        if account_id is None:
            return False  # locked: fall through to the password path
        text = update.message.text or ""
        kind = awaiting["kind"]
        if kind == "bus_code":
            await self._prompt_add_bus_stop(update, context, account_id, text)
        return True
```

Replace the old block at the top of `handle_plain_text` (the `if context.user_data.pop("awaiting_bus_code", False):` block, ~L1017-1026) with:

```python
        # Text-capture armed by a menu button (bus code, news subject, etc.).
        # Only consumed while unlocked; otherwise it's a password attempt.
        if await self._consume_awaiting(update, context, chat_id):
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_bus_navigation -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_bus_navigation.py
git commit -m "Unify awaiting-text state into a single context.user_data['awaiting'] slot"
```

---

### Task 3: Extract shared cores for the free-text actions

Split `handle_news` / `handle_summarize` / `handle_add_interests` / `handle_update_interests` / `handle_remove_interest` into a thin arg-parser (slash) + a shared `_do_*` core the button flow will reuse. Behavior via slash is unchanged. Cores reply via `update.effective_message`.

**Files:**
- Modify: `ykbot/app.py` (the five handlers, L457-613)
- Test: `tests/test_action_cores.py` (create)

**Interfaces:**
- Produces (all reply via `update.effective_message.reply_text`):
  - `async _do_news(self, update, context, account_id: str, subject: str) -> None`
  - `async _do_summarize(self, update, context, url: str) -> None`
  - `async _do_add_interest(self, update, account_id: str, text: str) -> None`
  - `async _do_update_interest(self, update, context, account_id: str, index: int, text: str) -> None`
  - `async _do_remove_interest(self, update, account_id: str, index: int) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_action_cores.py`:

```python
import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


class ActionCoreTests(AsyncBotTestCase):
    CHAT_ID = 5

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_do_add_interest_saves_and_replies(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_interest(update, "alice", "cybersecurity")

        self.assertEqual(b._interests_for("alice").list(), ["cybersecurity"])
        self.assertTrue(update.message.replies)

    async def test_do_remove_interest_deletes(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 1)

        self.assertEqual(b._interests_for("alice").list(), [])

    async def test_slash_news_still_replies_usage_without_args(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_news(update, FakeContext(args=[]))

        self.assertIn("Usage: /news", update.message.replies[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_action_cores -v`
Expected: FAIL — `AttributeError: 'YKBot' object has no attribute '_do_add_interest'`.

- [ ] **Step 3: Implement the cores and repoint the handlers**

In `ykbot/app.py`, replace the bodies below the `_require_session` guard of each handler so the handler parses args then calls the core. Example for `handle_news`:

```python
    async def handle_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        if not context.args:
            await update.message.reply_text("Usage: /news <subject>\nExample: /news cybersecurity")
            return
        await self._do_news(update, context, account_id, " ".join(context.args))

    async def _do_news(self, update, context, account_id: str, subject: str) -> None:
        if self._config.news_learning_enabled:
            try:
                self._news_history_for(account_id).append(subject)
            except Exception:
                logger.exception("Failed to log /news subject for %s", account_id)
        reply = await self._run_claude(
            update, context, self._build_news_prompt(subject, account_id), web_tools=True
        )
        if reply is not None:
            await self._send_formatted(update, self._strip_news_preamble(reply))
```

Apply the same shape to the others:

```python
    async def handle_summarize(self, update, context) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: /summarize <url>\nExample: /summarize https://example.com/article"
            )
            return
        url = context.args[0]
        if not self._is_fetchable_url(url):
            await update.message.reply_text("Please give a single public http(s) URL to summarize.")
            return
        await self._do_summarize(update, context, url)

    async def _do_summarize(self, update, context, url: str) -> None:
        reply = await self._run_claude(
            update, context, self._build_summarize_prompt(url), web_tools=True
        )
        if reply is not None:
            await self._send_formatted(update, reply)

    async def handle_add_interests(self, update, context) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: /addinterests <topic>\nExample: /addinterests cybersecurity, "
                "especially supply chain attacks"
            )
            return
        await self._do_add_interest(update, account_id, " ".join(context.args))

    async def _do_add_interest(self, update, account_id: str, text: str) -> None:
        index = self._interests_for(account_id).add(text)
        await update.effective_message.reply_text(f"Added as interest #{index}.")
```

For `handle_update_interests`, keep the arg validation in the handler, then call the core; the core does the Claude call + store update:

```python
    async def _do_update_interest(self, update, context, account_id: str, index: int, text: str) -> None:
        interests = self._interests_for(account_id)
        entries = interests.list()
        prompt = self._build_update_interest_prompt(entries[index - 1], text)
        new_text = await self._run_claude(update, context, prompt)
        if new_text is None:
            return
        interests.update(index, new_text)
        await update.effective_message.reply_text(f"Updated interest #{index}.")
```

Repoint `handle_update_interests` to validate index/range (as it does now) then `await self._do_update_interest(update, context, account_id, index, " ".join(topic_parts))`.

For `handle_remove_interest`, keep validation in the handler, add the core:

```python
    async def _do_remove_interest(self, update, account_id: str, index: int) -> None:
        interests = self._interests_for(account_id)
        removed = interests.remove(index)
        if removed is None:
            await update.effective_message.reply_text(
                f"No interest #{index}. Valid range: 1-{len(interests.list())}. See /getinterests."
            )
            return
        await update.effective_message.reply_text(f'Removed interest #{index}: "{removed}"')
```

Repoint `handle_remove_interest` to call `await self._do_remove_interest(update, account_id, int(context.args[0]))` after its existing empty-list guard.

Change the two interest cores' replies and `_do_summarize`/`_do_news` reply paths to use `update.effective_message` where they build a fresh reply (the `_send_formatted` path already uses `update.effective_message` internally — verify; if it uses `update.message`, leave the slash behavior untouched, the button task will confirm).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_action_cores -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS (existing news/summarize/interest tests still green)

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_action_cores.py
git commit -m "Extract shared _do_* cores for the free-text actions"
```

---

### Task 4: Extract content-action cores (interests listing, compile, open bus)

Same split for the no-arg content actions the menu reuses.

**Files:**
- Modify: `ykbot/app.py` (`handle_get_interests` L541, `handle_compile_interests` L615, `handle_bus` L731)
- Test: `tests/test_action_cores.py` (extend)

**Interfaces:**
- Produces (reply via `update.effective_message`):
  - `async _do_show_interests(self, update, account_id: str) -> None`
  - `async _do_compile_interests(self, update, context, account_id: str) -> None`
  - `async _open_bus_menu(self, update, context, account_id: str) -> None`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_action_cores.py`:

```python
    async def test_do_show_interests_lists_when_present(self):
        b = self._make_bot()
        b._interests_for("alice").add("space")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_show_interests(update, "alice")

        self.assertIn("space", update.message.replies[-1])

    async def test_open_bus_menu_sets_fav_context(self):
        b = self._make_bot()
        ctx = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._open_bus_menu(update, ctx, "alice")

        self.assertEqual(ctx.user_data["bus_menu"], {"kind": "fav"})
        self.assertTrue(update.message.replies)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_action_cores -v`
Expected: FAIL — `AttributeError: ... '_do_show_interests'`.

- [ ] **Step 3: Implement the cores and repoint the handlers**

```python
    async def _do_show_interests(self, update, account_id: str) -> None:
        entries = self._interests_for(account_id).list()
        if not entries:
            await update.effective_message.reply_text(
                "No interests saved yet. Add one with /addinterests <topic>."
            )
            return
        await update.effective_message.reply_text(self._format_interests_listing(entries))
```

Repoint `handle_get_interests` body (after the `_require_session` guard) to `await self._do_show_interests(update, account_id)`.

Move the compile body into `_do_compile_interests(update, context, account_id)` (identical logic, replies via `update.effective_message`), and repoint `handle_compile_interests` to call it after the guard.

Extract `handle_bus`'s body into `_open_bus_menu`:

```python
    async def _open_bus_menu(self, update, context, account_id: str) -> None:
        context.user_data["bus_menu"] = {"kind": "fav"}
        text, markup = self._build_fav_menu(account_id)
        await update.effective_message.reply_text(text, reply_markup=markup)
```

Repoint `handle_bus` body (after guard) to `await self._open_bus_menu(update, context, account_id)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_action_cores -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_action_cores.py
git commit -m "Extract content-action cores (show interests, compile, open bus)"
```

---

### Task 5: Top-level menu — `/menu`, welcome board, root navigation, Bus button

**Files:**
- Modify: `ykbot/app.py` (`_command_specs` L107-178, `_build_application` L239-254, `handle_plain_text` welcome send L1044)
- Test: `tests/test_menu.py` (create)

**Interfaces:**
- Consumes: `ykbot.menus.top_level_keyboard`, `_open_bus_menu` (Task 4), `_is_admin`.
- Produces:
  - `async handle_menu(self, update, context)` — slash `/menu`, sends `top_level_keyboard`.
  - `async handle_menu_nav(self, update, context)` — pattern `^menu:`, edits to the requested board.
  - `async handle_menu_bus(self, update, context)` — pattern `^act:bus$`, opens the bus menu.
  - Welcome message now carries the top-level board.

- [ ] **Step 1: Write the failing test**

Create `tests/test_menu.py`:

```python
import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class MenuTests(AsyncBotTestCase):
    CHAT_ID = 9
    USER = 100
    ADMIN_USER = 100000001

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"], "amy": ["adm"]})
        return b

    def _unlock(self, b, chat_id, pw):
        b._accounts.try_unlock(chat_id=chat_id, attempt=pw)

    async def test_menu_command_shows_top_level_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        markup = update.message.replies_markup[-1]  # see helper note below
        self.assertIn("act:news", datas(markup))
        self.assertNotIn("menu:admin", datas(markup))

    async def test_admin_sees_admin_row(self):
        b = self._make_bot()
        self._unlock(b, 1, "adm")  # amy account on admin chat
        update = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1)

        await b.handle_menu(update, FakeContext())

        self.assertIn("menu:admin", datas(update.message.replies_markup[-1]))

    async def test_locked_user_gets_no_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        self.assertFalse(getattr(update.message, "replies_markup", []))

    async def test_nav_to_interests_edits_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:interests")

        await b.handle_menu_nav(update, FakeContext())

        self.assertIn("int_pick:rm", datas(update.callback_query.last_markup))
```

Add a small capture to `tests/helpers.py` `FakeMessage.reply_text` so tests can read the markup passed to `reply_text` (the callback path already exposes `last_markup`):

```python
    def __init__(self, text=None, message_id=1, location=None):
        self.replies: list = []
        self.replies_markup: list = []
        self.text = text
        self.message_id = message_id
        self.location = location

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.replies_markup.append(kwargs.get("reply_markup"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_menu -v`
Expected: FAIL — `AttributeError: ... 'handle_menu'`.

- [ ] **Step 3: Implement**

Add the import at the top of `ykbot/app.py`:

```python
from ykbot import menus
```

Add a `/menu` CommandSpec to `_build_command_specs` (place it in a new "Menu" category near the top, after `help` or before `news`):

```python
            CommandSpec(
                "menu", "/menu", "show the button menu", self.handle_menu,
                category="Menu",
            ),
```

Add the handlers:

```python
    def _menu_markup(self, update, account_id):
        return menus.top_level_keyboard(self._is_admin(update, account_id))

    async def handle_menu(self, update, context) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        await update.effective_message.reply_text(
            "What would you like to do?", reply_markup=self._menu_markup(update, account_id)
        )

    async def handle_menu_nav(self, update, context) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        target = query.data.split(":", 1)[1]
        if target == "interests":
            await query.edit_message_text("Interests:", reply_markup=menus.interests_keyboard())
        elif target == "admin":
            if not self._is_admin(update, account_id):
                await query.edit_message_text(self._trivia.next_fact())
                return
            await query.edit_message_text("Admin:", reply_markup=menus.admin_keyboard())
        else:  # root
            await query.edit_message_text(
                "What would you like to do?", reply_markup=self._menu_markup(update, account_id)
            )

    async def handle_menu_bus(self, update, context) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        await self._open_bus_menu(update, context, account_id)
```

Register the callback handlers in `_build_application` (with the other `CallbackQueryHandler`s):

```python
        app.add_handler(CallbackQueryHandler(self.handle_menu_nav, pattern=r"^menu:"))
        app.add_handler(CallbackQueryHandler(self.handle_menu_bus, pattern=r"^act:bus$"))
```

Attach the board to the welcome message. In `handle_plain_text`, after a successful unlock, replace the welcome send (~L1044) with:

```python
            await context.bot.send_message(
                chat_id=chat_id,
                text=self._build_welcome_text(name),
                reply_markup=menus.top_level_keyboard(self._is_admin(update, account_id)),
            )
```

Update `_build_welcome_text` copy so it points at the board rather than only `/help`:

```python
            f"👋 Welcome, {name}! Session active for {minutes} minutes.\n\n"
            "Tap a button below to get started, or send /help for the full command list.\n\n"
```

(`FakeBot.send_message` already ignores `reply_markup` via `**kwargs`, so existing unlock tests stay green.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_menu -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_menu.py tests/helpers.py
git commit -m "Add /menu command, top-level board, welcome board, and root navigation"
```

---

### Task 6: No-arg actions + End-session confirm flow

Wire `act:getinterests`, `act:compile` to the content cores, and add the end-session confirm (`act:endsession` → confirm → `cfm:endsession` / `cxl:endsession`).

**Files:**
- Modify: `ykbot/app.py` (add action + confirm/cancel handlers; register them)
- Test: `tests/test_menu.py` (extend)

**Interfaces:**
- Consumes: `_do_show_interests`, `_do_compile_interests` (Task 4), `menus.confirm_keyboard`.
- Produces:
  - `async handle_menu_action(self, update, context)` — pattern `^act:(getinterests|compile|endsession)$`.
  - `async handle_confirm(self, update, context)` — pattern `^cfm:`.
  - `async handle_cancel(self, update, context)` — pattern `^cxl:`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_menu.py`:

```python
    async def test_action_getinterests_lists(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("weather")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("weather", update.message.replies[-1])

    async def test_endsession_asks_confirm_then_locks(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        confirm = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:endsession")
        await b.handle_menu_action(confirm, FakeContext())
        self.assertIn("cfm:endsession",
                      [b_.callback_data for row in confirm.callback_query.last_markup.inline_keyboard for b_ in row])

        yes = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cfm:endsession")
        await b.handle_confirm(yes, FakeContext())
        self.assertIsNone(b._accounts.account_for(self.CHAT_ID))

    async def test_cancel_dismisses(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cxl:endsession")

        await b.handle_cancel(update, FakeContext())

        self.assertIsNotNone(b._accounts.account_for(self.CHAT_ID))  # still unlocked
        self.assertTrue(update.callback_query.edits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_menu -v`
Expected: FAIL — `AttributeError: ... 'handle_menu_action'`.

- [ ] **Step 3: Implement**

```python
    async def handle_menu_action(self, update, context) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        action = query.data.split(":", 1)[1]
        if action == "getinterests":
            await self._do_show_interests(update, account_id)
        elif action == "compile":
            await self._do_compile_interests(update, context, account_id)
        elif action == "endsession":
            await query.edit_message_text(
                "End your session now?", reply_markup=menus.confirm_keyboard("endsession")
            )

    async def handle_confirm(self, update, context) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        token = query.data.split(":", 1)[1]
        if token == "endsession":
            self._accounts.lock(update.effective_chat.id)
            await query.edit_message_text("Session ended.")

    async def handle_cancel(self, update, context) -> None:
        query = update.callback_query
        if await self._require_session(update) is None:
            await query.answer()
            return
        await query.answer()
        await query.edit_message_text("Cancelled.")
```

Register (pattern for `handle_menu_action` deliberately excludes `act:bus`, handled in Task 5, and the capture actions added in Task 7):

```python
        app.add_handler(CallbackQueryHandler(
            self.handle_menu_action, pattern=r"^act:(getinterests|compile|endsession)$"))
        app.add_handler(CallbackQueryHandler(self.handle_confirm, pattern=r"^cfm:"))
        app.add_handler(CallbackQueryHandler(self.handle_cancel, pattern=r"^cxl:"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_menu -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_menu.py
git commit -m "Wire no-arg menu actions and the end-session confirm flow"
```

---

### Task 7: Free-text capture actions (News, Summarize, Add interest)

`act:news` / `act:summarize` / `act:addinterest` arm the unified `awaiting` slot and prompt; `_consume_awaiting` routes each to its Task-3 core.

**Files:**
- Modify: `ykbot/app.py` (`handle_menu_action` pattern + branches; `_consume_awaiting`)
- Test: `tests/test_menu.py` (extend)

**Interfaces:**
- Consumes: `_do_news`, `_do_summarize`, `_do_add_interest` (Task 3), `_is_fetchable_url`.
- Produces: new `awaiting` kinds `news_subject`, `summarize_url`, `add_interest`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_menu.py`:

```python
    async def test_news_button_arms_capture_then_core(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")

        async def fake_do_news(update, context, account_id, subject):
            update.effective_message.replies.append(f"digest:{subject}")
        b._do_news = fake_do_news

        arm = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:news")
        ctx = FakeContext()
        await b.handle_menu_action(arm, ctx)
        self.assertEqual(ctx.user_data["awaiting"]["kind"], "news_subject")

        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="climate")
        await b.handle_plain_text(typed, ctx)
        self.assertIn("digest:climate", typed.message.replies)

    async def test_summarize_capture_rejects_bad_url(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        ctx = FakeContext(user_data={"awaiting": {"kind": "summarize_url", "payload": {}}})
        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="not-a-url")

        await b.handle_plain_text(typed, ctx)

        self.assertTrue(any("public http(s) URL" in r for r in typed.message.replies))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_menu -v`
Expected: FAIL — `awaiting` is not armed (`act:news` not yet handled).

- [ ] **Step 3: Implement**

Extend `handle_menu_action`'s registration pattern and add branches:

```python
        elif action == "news":
            context.user_data["awaiting"] = {"kind": "news_subject", "payload": {}}
            await query.edit_message_text("Send the subject for your news digest:")
        elif action == "summarize":
            context.user_data["awaiting"] = {"kind": "summarize_url", "payload": {}}
            await query.edit_message_text("Send the URL to summarize:")
        elif action == "addinterest":
            context.user_data["awaiting"] = {"kind": "add_interest", "payload": {}}
            await query.edit_message_text("Send the interest to add (a word, sentence, or paragraph):")
```

Update the registration pattern:

```python
        app.add_handler(CallbackQueryHandler(
            self.handle_menu_action,
            pattern=r"^act:(getinterests|compile|endsession|news|summarize|addinterest)$"))
```

Extend `_consume_awaiting`'s dispatch:

```python
        elif kind == "news_subject":
            await self._do_news(update, context, account_id, text.strip())
        elif kind == "summarize_url":
            url = text.strip()
            if not self._is_fetchable_url(url):
                await update.effective_message.reply_text(
                    "Please give a single public http(s) URL to summarize."
                )
            else:
                await self._do_summarize(update, context, url)
        elif kind == "add_interest":
            await self._do_add_interest(update, account_id, text.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_menu -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_menu.py
git commit -m "Add News/Summarize/Add-interest capture button flows"
```

---

### Task 8: Interests submenu — pick-to-remove (confirm) and pick-to-update (capture)

`int_pick:rm` / `int_pick:upd` render a per-interest keyboard; selecting removes (with confirm) or updates (via capture).

**Files:**
- Modify: `ykbot/app.py` (pick handler; extend `handle_confirm`; extend `_consume_awaiting`; register)
- Test: `tests/test_menu.py` (extend)

**Interfaces:**
- Consumes: `menus.interest_pick_keyboard`, `menus.confirm_keyboard`, `_do_remove_interest`, `_do_update_interest`.
- Produces:
  - `async handle_interest_pick(self, update, context)` — pattern `^int_pick:`.
  - `async handle_interest_select(self, update, context)` — pattern `^int_(rm|upd):`.
  - `handle_confirm` token `int_rm:<i>`; new `awaiting` kind `update_interest` (payload `{"index": i}`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_menu.py`:

```python
    async def test_pick_rm_lists_interests_as_buttons(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("one")
        b._interests_for("alice").add("two")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_pick:rm")

        await b.handle_interest_pick(update, FakeContext())

        d = [b_.callback_data for row in update.callback_query.last_markup.inline_keyboard for b_ in row]
        self.assertIn("int_rm:1", d)
        self.assertIn("int_rm:2", d)

    async def test_select_rm_then_confirm_deletes(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("doomed")
        sel = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_rm:1")
        await b.handle_interest_select(sel, FakeContext())
        d = [b_.callback_data for row in sel.callback_query.last_markup.inline_keyboard for b_ in row]
        self.assertIn("cfm:int_rm:1", d)

        yes = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cfm:int_rm:1")
        await b.handle_confirm(yes, FakeContext())
        self.assertEqual(b._interests_for("alice").list(), [])

    async def test_select_upd_arms_capture_with_index(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("orig")
        ctx = FakeContext()
        sel = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_upd:1")

        await b.handle_interest_select(sel, ctx)

        self.assertEqual(ctx.user_data["awaiting"], {"kind": "update_interest", "payload": {"index": 1}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_menu -v`
Expected: FAIL — `AttributeError: ... 'handle_interest_pick'`.

- [ ] **Step 3: Implement**

```python
    async def handle_interest_pick(self, update, context) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        action = query.data.split(":", 1)[1]  # "rm" or "upd"
        entries = self._interests_for(account_id).list()
        if not entries:
            await query.edit_message_text(
                "No interests saved yet.", reply_markup=menus.interests_keyboard()
            )
            return
        verb = "remove" if action == "rm" else "update"
        await query.edit_message_text(
            f"Pick an interest to {verb}:",
            reply_markup=menus.interest_pick_keyboard(entries, action),
        )

    async def handle_interest_select(self, update, context) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        action, index_str = query.data.split(":")[0].split("_")[1], query.data.split(":", 1)[1]
        index = int(index_str)
        if action == "rm":
            entry = self._interests_for(account_id).list()
            label = entry[index - 1] if 1 <= index <= len(entry) else f"#{index}"
            await query.edit_message_text(
                f'Delete interest #{index}: "{label}"?',
                reply_markup=menus.confirm_keyboard(f"int_rm:{index}"),
            )
        else:  # upd
            context.user_data["awaiting"] = {"kind": "update_interest", "payload": {"index": index}}
            await query.edit_message_text(
                f"Send the new text for interest #{index} "
                "(I'll decide whether to revise or replace it):"
            )
```

Extend `handle_confirm` to also handle the `int_rm:` token (note the token itself contains a colon, so split with `maxsplit=1`):

```python
        token = query.data.split(":", 1)[1]
        if token == "endsession":
            self._accounts.lock(update.effective_chat.id)
            await query.edit_message_text("Session ended.")
        elif token.startswith("int_rm:"):
            index = int(token.split(":", 1)[1])
            await self._do_remove_interest(update, account_id, index)
```

Extend `_consume_awaiting`:

```python
        elif kind == "update_interest":
            index = awaiting["payload"]["index"]
            entries = self._interests_for(account_id).list()
            if 1 <= index <= len(entries):
                await self._do_update_interest(update, context, account_id, index, text.strip())
            else:
                await update.effective_message.reply_text(
                    f"Interest #{index} no longer exists. See /getinterests."
                )
```

Register:

```python
        app.add_handler(CallbackQueryHandler(self.handle_interest_pick, pattern=r"^int_pick:"))
        app.add_handler(CallbackQueryHandler(self.handle_interest_select, pattern=r"^int_(rm|upd):"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_menu -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_menu.py
git commit -m "Add Interests submenu: pick-to-remove (confirm) and pick-to-update (capture)"
```

---

### Task 9: Admin submenu — Get users, Allow Telegram id (capture), Remove user (pick + confirm)

**Files:**
- Modify: `ykbot/app.py` (admin cores; admin action/pick handlers; extend `handle_confirm` + `_consume_awaiting`; register)
- Test: `tests/test_menu.py` (extend)

**Interfaces:**
- Consumes: `menus.account_pick_keyboard`, `menus.confirm_keyboard`.
- Produces:
  - `async _do_add_user_tele_id(self, update, raw: str) -> None`, `async _do_remove_user(self, update, name: str) -> None` (both reply via `update.effective_message`).
  - `async handle_admin_action(self, update, context)` — pattern `^act:(getusers|adduserteleid)$` (re-checks `_require_admin`).
  - `async handle_admin_pick_remove(self, update, context)` — pattern `^adm_pick_rm$`.
  - `async handle_admin_select_remove(self, update, context)` — pattern `^adm_rm:`.
  - `awaiting` kind `add_user_tele_id`; `handle_confirm` token `adm_rm:<name>`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_menu.py`:

```python
    def _admin_bot(self):
        b = self._make_bot()
        b._accounts.try_unlock(chat_id=1, attempt="adm")  # amy on chat 1
        return b

    async def test_admin_getusers_lists(self):
        b = self._admin_bot()
        update = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1, callback_data="act:getusers")

        await b.handle_admin_action(update, FakeContext())

        self.assertTrue(update.callback_query.edits or update.message.replies)

    async def test_non_admin_action_rejected(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")  # alice, not admin
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:getusers")

        await b.handle_admin_action(update, FakeContext())

        # No user listing leaked to a non-admin.
        self.assertNotIn("alice —", "".join(r or "" for r in update.message.replies))

    async def test_adduserteleid_capture_adds(self):
        b = self._admin_bot()
        ctx = FakeContext(user_data={"awaiting": {"kind": "add_user_tele_id", "payload": {}}})
        typed = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1, text="424242")

        await b.handle_plain_text(typed, ctx)

        self.assertIn(424242, b._allowlist.ids)

    async def test_remove_user_pick_then_confirm(self):
        b = self._admin_bot()
        sel = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1, callback_data="adm_rm:alice")
        await b.handle_admin_select_remove(sel, FakeContext())
        d = [b_.callback_data for row in sel.callback_query.last_markup.inline_keyboard for b_ in row]
        self.assertIn("cfm:adm_rm:alice", d)

        yes = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1, callback_data="cfm:adm_rm:alice")
        await b.handle_confirm(yes, FakeContext())
        self.assertNotIn("alice", b._accounts.list_ids())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_menu -v`
Expected: FAIL — `AttributeError: ... 'handle_admin_action'`.

- [ ] **Step 3: Implement**

Add admin cores (mirror the slash validation, reply via `effective_message`):

```python
    async def _do_add_user_tele_id(self, update, raw: str) -> None:
        raw = raw.strip()
        if not raw.isdigit():
            await update.effective_message.reply_text("That isn't a numeric Telegram user id.")
            return
        user_id = int(raw)
        added = self._allowlist.add(user_id)
        await update.effective_message.reply_text(
            f"Added {user_id} to the allowlist." if added else f"{user_id} is already allowed."
        )

    async def _do_remove_user(self, update, name: str) -> None:
        if name == self._config.admin_account_id:
            await update.effective_message.reply_text("Refusing to remove the admin account.")
            return
        removed = self._accounts.remove_account(name)
        await update.effective_message.reply_text(
            f"Removed account '{name}'." if removed else f"No account named '{name}'."
        )
```

(Repoint `handle_add_user_tele_id` / `handle_remove_user` slash bodies to call these cores after their existing validation, keeping the exact slash reply strings; or leave the slash handlers as-is and only add the cores — either keeps slash behavior. Prefer repointing to stay DRY.)

Add the admin callback handlers:

```python
    async def handle_admin_action(self, update, context) -> None:
        query = update.callback_query
        if await self._require_admin(update) is None:
            await query.answer()
            return
        await query.answer()
        action = query.data.split(":", 1)[1]
        if action == "getusers":
            await query.edit_message_text(self._format_user_listing())
        elif action == "adduserteleid":
            context.user_data["awaiting"] = {"kind": "add_user_tele_id", "payload": {}}
            await query.edit_message_text("Send the Telegram user id to allow:")

    async def handle_admin_pick_remove(self, update, context) -> None:
        query = update.callback_query
        if await self._require_admin(update) is None:
            await query.answer()
            return
        await query.answer()
        accounts = [a for a in self._accounts.list_ids() if a != self._config.admin_account_id]
        if not accounts:
            await query.edit_message_text("No removable accounts.", reply_markup=menus.admin_keyboard())
            return
        await query.edit_message_text(
            "Pick an account to remove:", reply_markup=menus.account_pick_keyboard(accounts)
        )

    async def handle_admin_select_remove(self, update, context) -> None:
        query = update.callback_query
        if await self._require_admin(update) is None:
            await query.answer()
            return
        await query.answer()
        name = query.data.split(":", 1)[1]
        await query.edit_message_text(
            f"Remove account '{name}'?", reply_markup=menus.confirm_keyboard(f"adm_rm:{name}")
        )
```

Extend `handle_confirm` for the admin token — this one must re-check admin, so branch before the generic session check is insufficient; add inside `handle_confirm` after `token` is computed:

```python
        elif token.startswith("adm_rm:"):
            if await self._require_admin(update) is None:
                return
            await self._do_remove_user(update, token.split(":", 1)[1])
```

Extend `_consume_awaiting` — the admin capture must re-check admin (a stale slot must not let a non-admin add an id):

```python
        elif kind == "add_user_tele_id":
            if self._is_admin(update, account_id):
                await self._do_add_user_tele_id(update, text)
```

Register:

```python
        app.add_handler(CallbackQueryHandler(
            self.handle_admin_action, pattern=r"^act:(getusers|adduserteleid)$"))
        app.add_handler(CallbackQueryHandler(self.handle_admin_pick_remove, pattern=r"^adm_pick_rm$"))
        app.add_handler(CallbackQueryHandler(self.handle_admin_select_remove, pattern=r"^adm_rm:"))
```

Note ordering: register the specific `^act:(getusers|adduserteleid)$` admin handler and the general `^act:(...)$` menu-action handler so their patterns don't overlap (they don't — disjoint action names). Register `handle_admin_action` before `handle_menu_action` is unnecessary since patterns are disjoint, but keep admin registrations grouped.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_menu -v` → PASS
Run: `python3 -m unittest discover -s tests -t .` → all PASS

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_menu.py
git commit -m "Add Admin submenu: get users, allow Telegram id (capture), remove user (pick+confirm)"
```

---

### Task 10: Coverage backfill, static gate, docs, deploy

**Files:**
- Modify: `README.md` (document the button menu)
- Test: any file needed to reach 100%

- [ ] **Step 1: Measure coverage**

Run:
```bash
pip install --break-system-packages coverage
python3 -m coverage run --source=ykbot,bot -m unittest discover -s tests -t . && python3 -m coverage report -m
```
Expected: identify any uncovered lines in `ykbot/app.py` / `ykbot/menus.py` (e.g. locked-branch `query.answer()` early returns, the `menu:admin` non-admin branch, `_consume_awaiting` locked fall-through).

- [ ] **Step 2: Add tests for each uncovered line**

For every uncovered line, add a focused test in `tests/test_menu.py` or `tests/test_menus.py` (e.g. a locked user tapping each callback returns early and answers the query; a non-admin hand-sending `menu:admin` / `act:getusers` / `cfm:adm_rm:x` is rejected). Use `FakeUpdate(..., callback_data=...)` with no unlock to hit locked branches.

- [ ] **Step 3: Verify 100% and static gate**

Run:
```bash
python3 -m coverage run --source=ykbot,bot -m unittest discover -s tests -t . && python3 -m coverage report -m
python3 -m pyflakes bot.py ykbot/
```
Expected: `TOTAL ... 100%` (only `bot.py`'s `__main__` guard excluded via existing `# pragma: no cover`); pyflakes prints nothing.

- [ ] **Step 4: Document + commit**

Add a "Button menu" subsection to `README.md` describing `/menu`, the top-level buttons, the Interests and Admin submenus, and that slash commands still work. Then:

```bash
git add README.md tests/
git commit -m "Backfill button-menu coverage to 100% and document the menu"
```

- [ ] **Step 5: Deploy per the STANDING RUNBOOK**

Follow `.claude/HANDOFF.md`: verify the suite green + pyflakes clean while the old bot runs, then /proc-scan the PID, `kill` it, wait for `Application.stop() complete`, relaunch `setsid nohup python3 bot.py >> bot.log 2>&1 < /dev/null &`, and confirm exactly one healthy instance in `bot.log`. Push to `origin/main` once the user approves.

---

## Self-Review

**Spec coverage:**
- Inline keyboard (not persistent) → Tasks 1,5 (callback-based). ✓
- `/menu` + welcome board + Back buttons → Task 5 (welcome), Task 1 (Back targets baked into keyboards), nav in Task 5. ✓
- Unified `awaiting` slot, locked-falls-through → Task 2 (+ extended 7,8,9). ✓
- Slash/button shared cores → Tasks 3,4,9. ✓
- Top-level Bus/News/Interests/Summarize/End session (+Admin) → Task 5. ✓
- End-session confirm → Task 6; Remove-interest confirm → Task 8; Remove-user confirm → Task 9. ✓
- `/help` slash-only (not a button) → not added as a button anywhere. ✓
- Admin middle option (getusers tap, removeuser pick+confirm, adduserteleid capture, adduser slash-only) → Task 9. ✓
- Button visibility ≠ authorization (admin re-checks) → Task 5 nav, Task 9 handlers/confirm/capture all re-check `_require_admin`/`_is_admin`. ✓
- Anti-enumeration for locked users → callbacks return after `query.answer()` with no leak; Task 10 tests it. ✓
- `ykbot/menus.py` pure builders + handlers on YKBot → Task 1 + Tasks 5-9. ✓
- 100% coverage held → Task 10. ✓
- Claude tool-sandboxing untouched → `_do_news`/`_do_summarize` reuse existing `_run_claude(..., web_tools=True)` unchanged. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code. ✓

**Type consistency:** `awaiting` shape `{"kind","payload"}` consistent across Tasks 2,7,8,9. `interest_pick_keyboard(entries, action)` / `int_<action>:<i>` consistent between Task 1 and Task 8. `confirm_keyboard(token)` → `cfm:<token>`/`cxl:<token>` consistent across Tasks 1,6,8,9. Core signatures match their call sites. ✓

**Note added during review:** the `callback_data` namespace gained `int_pick:{rm,upd}` and `adm_pick_rm` beyond the spec's original list; these are consistent extensions (render the pick keyboards) and are documented in the namespace table above.
