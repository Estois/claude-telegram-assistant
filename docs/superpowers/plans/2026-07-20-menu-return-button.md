# Return-to-Menu Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach a 🏠 Menu button to results produced from the button menu, so they aren't dead ends — tapping it posts a *fresh* menu message and leaves the result on screen.

**Architecture:** A new `menus.home_keyboard()` with its own `^home$` callback, handled by `YKBot.handle_menu_home`, which replies rather than edits. Every `_do_*` core gains an explicit `from_menu: bool = False` parameter (callback sniffing does not work — four flows deliver their result through a typed reply) and sends through one new helper, `_reply_result`.

**Tech Stack:** Python 3, python-telegram-bot, stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-07-20-menu-return-button-design.md`

## Global Constraints

- Branch: `menu-return-button`. Base commit `ef662ce` (the spec).
- Test command: `python3 -m unittest discover -s tests -t .` — must be green after every task.
- Static gate: `python3 -m pyflakes bot.py ykbot/` — must be clean after every task.
- Coverage must stay at **100%** line coverage of `bot.py` + `ykbot/`. Verify with
  `python3 -m coverage run --source=ykbot,bot -m unittest discover -s tests -t . && python3 -m coverage report -m`.
- Sandbox prereq (the repo `.venv` is Windows-only — **never recreate it here**):
  `pip install --break-system-packages -r requirements-dev.txt pyflakes coverage`
- Reuse the doubles in `tests/helpers.py` (`FakeUpdate`, `FakeContext`, `make_config`, `write_accounts_file`, `AsyncBotTestCase`, `BotTestCase`). Do not define new fakes.
- **Callback safety:** every reply site touched here must use `update.effective_message`, never `update.message`. Real callback updates have `update.message is None`. `FakeUpdate` aliases the two, so tests **cannot** catch a violation — check by reading the code.
- Button label is exactly `"🏠 Menu"`; callback data is exactly `"home"`.
- Do **not** touch `_build_arrivals_keyboard` or the `bus_code` capture branch. Bus arrivals are out of scope.
- Commit after each task. Do not push.

---

### Task 1: The 🏠 keyboard and its handler

Delivers a working `^home$` callback. Nothing emits the button yet — that comes in Tasks 3–5.

**Files:**
- Modify: `ykbot/menus.py` (append a new function)
- Modify: `ykbot/app.py` (new handler after `handle_menu_nav`, ends line 498; registration after line 257)
- Test: `tests/test_menu_home.py` (create)

**Interfaces:**
- Produces: `menus.home_keyboard() -> InlineKeyboardMarkup`; `YKBot.handle_menu_home(update, context) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_menu_home.py`:

```python
import unittest

from telegram.ext import CallbackQueryHandler

import bot
from ykbot import menus
from tests.helpers import (
    AsyncBotTestCase, BotTestCase, FakeContext, FakeUpdate, make_config,
    write_accounts_file,
)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class HomeKeyboardTests(unittest.TestCase):
    def test_is_a_single_home_button(self):
        rows = menus.home_keyboard().inline_keyboard
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].callback_data, "home")
        self.assertIn("Menu", rows[0][0].text)


class HandleMenuHomeTests(AsyncBotTestCase):
    CHAT_ID = 41

    def _make_bot(self, account="alice"):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {account: ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_posts_a_new_message_and_never_edits(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertEqual(update.callback_query.edits, [])
        self.assertEqual(update.message.replies[-1], "What would you like to do?")
        self.assertIn("act:bus", datas(update.message.replies_markup[-1]))

    async def test_answers_the_callback_query(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertEqual(len(update.callback_query.answers), 1)

    async def test_locked_chat_gets_trivia_not_a_menu(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        update = FakeUpdate(user_id=1, chat_id=99, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertIsNone(update.message.replies_markup[-1])
        self.assertEqual(update.callback_query.edits, [])

    async def test_admin_session_gets_the_admin_row(self):
        b = self._make_bot(account="amy")
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertIn("menu:admin", datas(update.message.replies_markup[-1]))

    async def test_non_admin_session_has_no_admin_row(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="home")

        await b.handle_menu_home(update, FakeContext())

        self.assertNotIn("menu:admin", datas(update.message.replies_markup[-1]))


class HomeHandlerRegistrationTests(BotTestCase):
    def test_home_pattern_is_registered(self):
        b = bot.YKBot(make_config(self.tmp_path))
        app = b._build_application()
        patterns = [
            h.pattern.pattern
            for group in app.handlers.values()
            for h in group
            if isinstance(h, CallbackQueryHandler)
        ]
        self.assertIn(r"^home$", patterns)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_menu_home -v`
Expected: FAIL — `AttributeError: module 'ykbot.menus' has no attribute 'home_keyboard'`.

- [ ] **Step 3: Add the keyboard builder**

Append to `ykbot/menus.py`:

```python
def home_keyboard() -> InlineKeyboardMarkup:
    """A lone 🏠 Menu button for results produced from the button menu, so
    they aren't dead ends. Its callback_data is deliberately NOT menu:root:
    that one navigates by editing the message in place, which would destroy
    the very result this button is attached to."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="home")]])
```

- [ ] **Step 4: Add the handler**

In `ykbot/app.py`, immediately after `handle_menu_nav` (which ends at line 498):

```python
    async def handle_menu_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Posts a FRESH menu message rather than editing. The 🏠 button sits
        on result messages, so editing would replace the result the user just
        asked for — the opposite of what the button is for."""
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        await update.effective_message.reply_text(
            "What would you like to do?", reply_markup=self._menu_markup(update, account_id)
        )
```

- [ ] **Step 5: Register the handler**

In `_build_application`, directly after the `handle_menu_nav` registration (line 257):

```python
        app.add_handler(CallbackQueryHandler(self.handle_menu_home, pattern=r"^home$"))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_menu_home -v`
Expected: PASS, 7 tests.

Then the full suite and the static gate:

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, and pyflakes prints nothing.

- [ ] **Step 7: Commit**

```bash
git add ykbot/menus.py ykbot/app.py tests/test_menu_home.py
git commit -m "Add the 🏠 Menu button and a handler that replies instead of editing"
```

---

### Task 2: The sending layer

Adds `_reply_result` and teaches `_send_formatted` about the button. No call site passes `from_menu=True` yet, so behavior is unchanged — this task is purely the mechanism.

**Files:**
- Modify: `ykbot/app.py` (new `_reply_result`; rewrite `_send_formatted`, lines 1427-1437)
- Test: `tests/test_reply_result.py` (create)

**Interfaces:**
- Consumes: `menus.home_keyboard()` from Task 1.
- Produces:
  - `YKBot._reply_result(update, text: str, from_menu: bool, **kwargs) -> None`
  - `YKBot._send_formatted(update, text: str, from_menu: bool = False) -> None`

Note the asymmetry, and keep it: `_reply_result` takes `from_menu` **positionally and required** (every caller is being written in this change, so there is no default to forget), while `_send_formatted` defaults it to `False` because it has pre-existing callers outside the `_do_*` cores.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reply_result.py`:

```python
from telegram.error import BadRequest

import bot
from ykbot.formatting import TelegramFormatter
from tests.helpers import AsyncBotTestCase, FakeUpdate, make_config


class ReplyResultTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    async def test_attaches_home_button_when_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._reply_result(update, "done", True)

        markup = update.message.replies_markup[-1]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "home")

    async def test_no_button_when_not_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._reply_result(update, "done", False)

        self.assertEqual(update.message.replies, ["done"])
        self.assertIsNone(update.message.replies_markup[-1])

    async def test_passes_through_extra_kwargs(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        seen = {}

        async def capture(text, **kwargs):
            seen.update(kwargs)

        update.effective_message.reply_text = capture

        await b._reply_result(update, "done", False, parse_mode="HTML")

        self.assertEqual(seen["parse_mode"], "HTML")


class SendFormattedButtonTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    def _long_text(self):
        """Three paragraphs, each just under the limit, so chunk() returns 3."""
        para = "x" * (TelegramFormatter.MESSAGE_LIMIT - 100)
        return "\n\n".join([para, para, para])

    async def test_button_only_on_the_last_chunk(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        text = self._long_text()
        self.assertEqual(len(TelegramFormatter.chunk(text)), 3)

        await b._send_formatted(update, text, True)

        markups = update.message.replies_markup
        self.assertEqual(len(markups), 3)
        self.assertIsNone(markups[0])
        self.assertIsNone(markups[1])
        self.assertEqual(markups[2].inline_keyboard[0][0].callback_data, "home")

    async def test_no_button_when_not_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._send_formatted(update, "short", False)

        self.assertEqual(update.message.replies_markup, [None])

    async def test_defaults_to_no_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._send_formatted(update, "short")

        self.assertEqual(update.message.replies_markup, [None])

    async def test_button_survives_the_plain_text_fallback(self):
        """A reply whose HTML doesn't parse still has to come back with its
        button — the fallback path must carry the markup too."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        calls = []
        original = update.message.reply_text

        async def flaky(text, **kwargs):
            calls.append(kwargs)
            if kwargs.get("parse_mode") is not None:
                raise BadRequest("bad html")
            await original(text, **kwargs)

        update.effective_message.reply_text = flaky

        await b._send_formatted(update, "<b>oops", True)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["reply_markup"].inline_keyboard[0][0].callback_data, "home")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_reply_result -v`
Expected: FAIL — `AttributeError: 'YKBot' object has no attribute '_reply_result'`, plus failures on the chunk-markup assertions.

- [ ] **Step 3: Add `_reply_result`**

In `ykbot/app.py`, directly above `_send_formatted` (currently line 1427):

```python
    async def _reply_result(
        self, update: Update, text: str, from_menu: bool, **kwargs
    ) -> None:
        """Sends a result reply, attaching the 🏠 Menu button when the action
        was started from the button menu. Slash-command results stay bare — if
        you're already typing commands you don't need a button to reach the
        menu. Always replies via effective_message: results from a button flow
        can arrive on an update whose .message is None."""
        await update.effective_message.reply_text(
            text,
            reply_markup=menus.home_keyboard() if from_menu else None,
            **kwargs,
        )
```

- [ ] **Step 4: Rewrite `_send_formatted`**

Replace the body of `_send_formatted` (lines 1427-1437) with:

```python
    async def _send_formatted(self, update: Update, text: str, from_menu: bool = False) -> None:
        """Sends Claude's reply, chunked for Telegram's message limit. Tries
        HTML formatting first and falls back to plain text per chunk if the
        markup doesn't parse — LLM-generated tags aren't guaranteed
        well-formed, so a broken reply must never mean a lost reply.

        The 🏠 Menu button goes on the LAST chunk only, so a multi-chunk digest
        ends with one button rather than one per message."""
        chunks = TelegramFormatter.chunk(text)
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            markup = menus.home_keyboard() if (from_menu and last) else None
            try:
                await update.effective_message.reply_text(
                    chunk, parse_mode=ParseMode.HTML, reply_markup=markup
                )
            except BadRequest:
                logger.warning("Telegram rejected HTML formatting, sending chunk as plain text")
                await update.effective_message.reply_text(chunk, reply_markup=markup)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_reply_result -v`
Expected: PASS, 7 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent. (The existing `_send_formatted` tests still pass — the default keeps old behavior.)

- [ ] **Step 6: Commit**

```bash
git add ykbot/app.py tests/test_reply_result.py
git commit -m "Add _reply_result and put the menu button on the last chunk only"
```

---

### Task 3: Thread `from_menu` through the direct-reply cores

Seven cores that reply immediately, plus the two callback handlers that call them.

**Files:**
- Modify: `ykbot/app.py` — `_do_add_interest` (704), `_do_show_interests` (714), `_do_update_interest` (754), `_do_remove_interest` (791), `_do_compile_interests` (810), `_do_remove_user` (887), `_do_add_user_tele_id` (906), `handle_menu_action` (509), `handle_confirm` (580)
- Test: `tests/test_menu_button_cores.py` (create)

**Interfaces:**
- Consumes: `YKBot._reply_result(update, text, from_menu, **kwargs)` from Task 2.
- Produces, all with `from_menu: bool = False` as the final parameter:
  - `_do_add_interest(update, account_id, text, from_menu=False)`
  - `_do_show_interests(update, account_id, from_menu=False)`
  - `_do_update_interest(update, context, account_id, index, text, from_menu=False)`
  - `_do_remove_interest(update, account_id, index, from_menu=False)`
  - `_do_compile_interests(update, context, account_id, from_menu=False)`
  - `_do_remove_user(update, name, from_menu=False)`
  - `_do_add_user_tele_id(update, raw, from_menu=False)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_menu_button_cores.py`:

```python
import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def home_data(markup):
    return None if markup is None else markup.inline_keyboard[0][0].callback_data


class InterestCoreButtonTests(AsyncBotTestCase):
    CHAT_ID = 51

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_add_interest_has_button_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_interest(update, "alice", "space", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_interest_bare_from_slash(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_interest(update, "alice", "space")

        self.assertIsNone(update.message.replies_markup[-1])

    async def test_show_interests_listing_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("space")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_show_interests(update, "alice", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_show_interests_empty_state_has_button(self):
        """The empty state is exactly where a dead end hurts most."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_show_interests(update, "alice", from_menu=True)

        self.assertIn("No interests saved yet", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_remove_interest_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 1, from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_remove_interest_out_of_range_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 9, from_menu=True)

        self.assertIn("No interest #9", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_remove_interest_bare_from_slash(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 1)

        self.assertIsNone(update.message.replies_markup[-1])


class CompileCoreButtonTests(AsyncBotTestCase):
    CHAT_ID = 52

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_too_few_interests_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_compile_interests(update, FakeContext(), "alice", from_menu=True)

        self.assertIn("Need at least 2 interests", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_unusable_reply_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("one")
        b._interests_for("alice").add("two")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return "   "

        b._run_claude = fake_run_claude
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_compile_interests(update, FakeContext(), "alice", from_menu=True)

        self.assertIn("no changes made", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_successful_compile_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("one")
        b._interests_for("alice").add("two")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return "1. merged topic"

        b._run_claude = fake_run_claude
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_compile_interests(update, FakeContext(), "alice", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")


class AdminCoreButtonTests(AsyncBotTestCase):
    CHAT_ID = 53

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"amy": ["pw"], "bob": ["pw2"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_remove_user_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_remove_user(update, "bob", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_refusing_to_remove_admin_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_remove_user(update, "amy", from_menu=True)

        self.assertIn("Refusing", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_user_tele_id_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_add_user_tele_id(update, "424242", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_non_numeric_tele_id_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_add_user_tele_id(update, "nope", from_menu=True)

        self.assertIn("isn't a numeric", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_user_tele_id_bare_from_slash(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_add_user_tele_id(update, "424242")

        self.assertIsNone(update.message.replies_markup[-1])


class CallbackCallersPassFromMenuTests(AsyncBotTestCase):
    CHAT_ID = 54

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_menu_action_getinterests_attaches_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("space")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_menu_action_compile_attaches_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="act:compile")

        await b.handle_menu_action(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_confirm_remove_interest_attaches_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="cfm:int_rm:1")

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_confirm_remove_user_attaches_button(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"amy": ["pw"], "bob": ["pw2"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        update = FakeUpdate(
            user_id=100000001, chat_id=self.CHAT_ID, callback_data="cfm:adm_rm:bob"
        )

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_menu_button_cores -v`
Expected: FAIL — `TypeError: _do_add_interest() got an unexpected keyword argument 'from_menu'` and similar.

- [ ] **Step 3: Update `_do_add_interest` (line 704)**

```python
    async def _do_add_interest(
        self, update: Update, account_id: str, text: str, from_menu: bool = False
    ) -> None:
        index = self._interests_for(account_id).add(text)
        await self._reply_result(update, f"Added as interest #{index}.", from_menu)
```

- [ ] **Step 4: Update `_do_show_interests` (line 714)**

```python
    async def _do_show_interests(
        self, update: Update, account_id: str, from_menu: bool = False
    ) -> None:
        entries = self._interests_for(account_id).list()
        if not entries:
            await self._reply_result(
                update, "No interests saved yet. Add one with /addinterests <topic>.", from_menu
            )
            return
        await self._reply_result(update, self._format_interests_listing(entries), from_menu)
```

- [ ] **Step 5: Update `_do_update_interest` (line 754)**

Add `from_menu: bool = False` as the last parameter and change the final reply:

```python
    async def _do_update_interest(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        index: int,
        text: str,
        from_menu: bool = False,
    ) -> None:
        interests = self._interests_for(account_id)
        entries = interests.list()
        prompt = self._build_update_interest_prompt(entries[index - 1], text)
        new_text = await self._run_claude(update, context, prompt)
        if new_text is None:
            return

        interests.update(index, new_text)
        await self._reply_result(update, f"Updated interest #{index}.", from_menu)
```

- [ ] **Step 6: Update `_do_remove_interest` (line 791)**

```python
    async def _do_remove_interest(
        self, update: Update, account_id: str, index: int, from_menu: bool = False
    ) -> None:
        interests = self._interests_for(account_id)
        removed = interests.remove(index)
        if removed is None:
            await self._reply_result(
                update,
                f"No interest #{index}. Valid range: 1-{len(interests.list())}. See /getinterests.",
                from_menu,
            )
            return
        await self._reply_result(update, f'Removed interest #{index}: "{removed}"', from_menu)
```

- [ ] **Step 7: Update `_do_compile_interests` (line 810)**

Add `from_menu: bool = False` as the last parameter, and convert all three reply sites:

```python
    async def _do_compile_interests(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        from_menu: bool = False,
    ) -> None:
        interests = self._interests_for(account_id)
        entries = interests.list()
        theme_counts = self._recent_theme_counts(account_id)
        if len(entries) < 2 and not theme_counts:
            await self._reply_result(
                update, "Need at least 2 interests to reorganize. See /getinterests.", from_menu
            )
            return

        prompt = self._build_reorganize_prompt(entries, theme_counts)
        raw_reply = await self._run_claude(
            update, context, prompt, chat_id=self._maintenance_key(account_id)
        )
        if raw_reply is None:
            return

        new_entries = InterestsStore.parse_entries(raw_reply)
        if not new_entries:
            await self._reply_result(
                update, "Reorganize returned nothing usable — no changes made.", from_menu
            )
            return

        interests.replace_all(new_entries)
        summary = f"🗂 Reorganized interests: {len(entries)} → {len(new_entries)} entries."
        await self._reply_result(
            update, f"{summary}\n\n{self._format_interests_listing(new_entries)}", from_menu
        )
```

- [ ] **Step 8: Update `_do_remove_user` (line 887)**

```python
    async def _do_remove_user(self, update: Update, name: str, from_menu: bool = False) -> None:
        if name == self._config.admin_account_id:
            await self._reply_result(update, "Refusing to remove the admin account.", from_menu)
            return
        removed = self._accounts.remove_account(name)
        await self._reply_result(
            update,
            f"Removed account '{name}'." if removed else f"No account named '{name}'.",
            from_menu,
        )
```

- [ ] **Step 9: Update `_do_add_user_tele_id` (line 906)**

```python
    async def _do_add_user_tele_id(
        self, update: Update, raw: str, from_menu: bool = False
    ) -> None:
        raw = raw.strip()
        if not raw.isdigit():
            await self._reply_result(update, "That isn't a numeric Telegram user id.", from_menu)
            return
        user_id = int(raw)
        added = self._allowlist.add(user_id)
        await self._reply_result(
            update,
            f"Added {user_id} to the allowlist." if added else f"{user_id} is already allowed.",
            from_menu,
        )
```

- [ ] **Step 10: Update the two callback callers**

In `handle_menu_action` (line 509), the `getinterests` and `compile` branches:

```python
        if action == "getinterests":
            await self._do_show_interests(update, account_id, from_menu=True)
        elif action == "compile":
            await self._do_compile_interests(update, context, account_id, from_menu=True)
```

In `handle_confirm` (line 580), the `int_rm:` and `adm_rm:` branches:

```python
        elif token.startswith("int_rm:"):
            index = int(token.split(":", 1)[1])
            await self._do_remove_interest(update, account_id, index, from_menu=True)
        elif token.startswith("adm_rm:"):
            if await self._require_admin(update) is None:
                return
            await self._do_remove_user(update, token.split(":", 1)[1], from_menu=True)
```

- [ ] **Step 11: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_menu_button_cores -v`
Expected: PASS, 19 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent. Existing tests still pass because every new parameter defaults to `False`.

- [ ] **Step 12: Commit**

```bash
git add ykbot/app.py tests/test_menu_button_cores.py
git commit -m "Attach the menu button to interest, compile and admin results"
```

---

### Task 4: The capture flows — news, summarize, and the rest of `_consume_awaiting`

This is the case plain callback-detection would have missed: these results arrive on a **typed message**, not a callback query.

**Files:**
- Modify: `ykbot/app.py` — `_do_news` (624), `_do_summarize` (659), `_consume_awaiting` (1251)
- Test: `tests/test_menu_capture_button.py` (create)

**Interfaces:**
- Consumes: `_reply_result` and `_send_formatted(..., from_menu)` from Task 2; `_do_add_interest`, `_do_update_interest`, `_do_add_user_tele_id` signatures from Task 3.
- Produces: `_do_news(update, context, account_id, subject, from_menu=False)`, `_do_summarize(update, context, url, from_menu=False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_menu_capture_button.py`:

```python
import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def home_data(markup):
    return None if markup is None else markup.inline_keyboard[0][0].callback_data


class CaptureButtonTests(AsyncBotTestCase):
    CHAT_ID = 61

    def _make_bot(self, account="alice", reply="News Digest — x"):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {account: ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return reply

        b._run_claude = fake_run_claude
        return b

    async def _consume(self, b, kind, text, payload=None, user_id=1):
        update = FakeUpdate(user_id=user_id, chat_id=self.CHAT_ID, text=text)
        context = FakeContext(user_data={"awaiting": {"kind": kind, "payload": payload or {}}})
        await b._consume_awaiting(update, context, self.CHAT_ID)
        return update

    async def test_news_from_capture_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "news_subject", "space")
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_slash_news_stays_bare(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_news(update, FakeContext(args=["space"]))

        self.assertIsNone(update.message.replies_markup[-1])

    async def test_summarize_from_capture_gets_button(self):
        b = self._make_bot(reply="a summary")
        update = await self._consume(b, "summarize_url", "https://example.com/a")
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_rejected_url_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "summarize_url", "http://127.0.0.1/secret")
        self.assertIn("public http(s) URL", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_interest_from_capture_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "add_interest", "sailing")
        self.assertEqual(b._interests_for("alice").list(), ["sailing"])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_update_interest_from_capture_gets_button(self):
        b = self._make_bot(reply="revised")
        b._interests_for("alice").add("original")
        update = await self._consume(b, "update_interest", "new wording", payload={"index": 1})
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_stale_update_index_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "update_interest", "text", payload={"index": 7})
        self.assertIn("no longer exists", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_user_tele_id_from_capture_gets_button(self):
        b = self._make_bot(account="amy")
        update = await self._consume(b, "add_user_tele_id", "424242", user_id=100000001)
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_menu_capture_button -v`
Expected: FAIL — the button assertions fail with `None != 'home'` (the capture path doesn't request the button yet).

- [ ] **Step 3: Update `_do_news` (line 624)**

Add the parameter and pass it to `_send_formatted`:

```python
    async def _do_news(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        subject: str,
        from_menu: bool = False,
    ) -> None:
        if self._config.news_learning_enabled:
            try:
                self._news_history_for(account_id).append(subject)
            except Exception:
                # Logging the subject must never break the digest reply.
                logger.exception("Failed to log /news subject for %s", account_id)
        reply = await self._run_claude(
            update, context, self._build_news_prompt(subject, account_id), web_tools=True
        )
        if reply is not None:
            await self._send_formatted(update, self._strip_news_preamble(reply), from_menu)
```

- [ ] **Step 4: Update `_do_summarize` (line 659)**

```python
    async def _do_summarize(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        url: str,
        from_menu: bool = False,
    ) -> None:
        reply = await self._run_claude(
            update, context, self._build_summarize_prompt(url), web_tools=True
        )
        if reply is not None:
            await self._send_formatted(update, reply, from_menu)
```

- [ ] **Step 5: Update `_consume_awaiting` (line 1251)**

Every branch **except `bus_code`** now passes `from_menu=True`, including the two inline error replies:

```python
        text = update.message.text or ""
        kind = awaiting["kind"]
        if kind == "bus_code":
            await self._prompt_add_bus_stop(update, context, account_id, text)
        elif kind == "news_subject":
            await self._do_news(update, context, account_id, text.strip(), from_menu=True)
        elif kind == "summarize_url":
            url = text.strip()
            if not self._is_fetchable_url(url):
                await self._reply_result(
                    update, "Please give a single public http(s) URL to summarize.", True
                )
            else:
                await self._do_summarize(update, context, url, from_menu=True)
        elif kind == "add_interest":
            await self._do_add_interest(update, account_id, text.strip(), from_menu=True)
        elif kind == "add_user_tele_id":
            if self._is_admin(update, account_id):
                await self._do_add_user_tele_id(update, text, from_menu=True)
        elif kind == "update_interest":
            index = awaiting["payload"]["index"]
            entries = self._interests_for(account_id).list()
            if 1 <= index <= len(entries):
                await self._do_update_interest(
                    update, context, account_id, index, text.strip(), from_menu=True
                )
            else:
                await self._reply_result(
                    update, f"Interest #{index} no longer exists. See /getinterests.", True
                )
        return True
```

`bus_code` is left alone deliberately — bus arrivals are out of scope and already have their own nav.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_menu_capture_button -v`
Expected: PASS, 8 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent.

- [ ] **Step 7: Commit**

```bash
git add ykbot/app.py tests/test_menu_capture_button.py
git commit -m "Attach the menu button to results from the text-capture flows"
```

---

### Task 5: Bus menu row and the admin user listing

Two screens that dead-end today. Both fixes are unconditional — these are menus, not results.

**Files:**
- Modify: `ykbot/app.py` — `_build_fav_menu` (983), `_build_near_menu` (997), `handle_admin_action` (940)
- Test: `tests/test_menu_home_screens.py` (create)

**Interfaces:**
- Consumes: `menus.home_keyboard()` from Task 1 (for the callback data constant only; these two builders append a plain button row).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_menu_home_screens.py`:

```python
import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class BusMenuHomeTests(AsyncBotTestCase):
    CHAT_ID = 71

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    def test_fav_menu_has_home_row(self):
        b = self._make_bot()
        _text, markup = b._build_fav_menu("alice")
        self.assertIn("home", datas(markup))

    def test_near_menu_has_home_row(self):
        b = self._make_bot()
        stops = [{"Description": "Opp Blk 1", "BusStopCode": "12345", "distance_m": 40}]
        _text, markup = b._build_near_menu(stops)
        self.assertIn("home", datas(markup))

    async def test_slash_bus_also_gets_home(self):
        """Unconditional: the same screen must not gain or lose a nav button
        depending on how the user reached it."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_bus(update, FakeContext())

        self.assertIn("home", datas(update.message.replies_markup[-1]))


class AdminGetUsersHomeTests(AsyncBotTestCase):
    CHAT_ID = 72

    async def test_get_users_listing_keeps_the_admin_menu(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"amy": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        update = FakeUpdate(
            user_id=100000001, chat_id=self.CHAT_ID, callback_data="act:getusers"
        )

        await b.handle_admin_action(update, FakeContext())

        self.assertIn("menu:root", datas(update.callback_query.last_markup))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_menu_home_screens -v`
Expected: FAIL — `'home' not found in [...]`, and `AttributeError: 'NoneType' object has no attribute 'inline_keyboard'` for the admin test (the listing edit currently passes no markup).

- [ ] **Step 3: Add the 🏠 row to `_build_fav_menu` (line 983)**

Add one row immediately before the return:

```python
        buttons.append(
            [InlineKeyboardButton("➕ Add a stop by code", callback_data="bus_addbycode")]
        )
        buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="home")])
        return "Pick a stop:", InlineKeyboardMarkup(buttons)
```

- [ ] **Step 4: Add the 🏠 row to `_build_near_menu` (line 997)**

```python
    def _build_near_menu(self, stops: list) -> "tuple[str, InlineKeyboardMarkup]":
        buttons = [
            [
                InlineKeyboardButton(
                    f"{stop['Description']} ({int(stop['distance_m'])}m)",
                    callback_data=f"bus_stop:{stop['BusStopCode']}",
                )
            ]
            for stop in stops
        ]
        buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="home")])
        return "Nearest stops:", InlineKeyboardMarkup(buttons)
```

- [ ] **Step 5: Keep the admin menu under the user listing (line 940)**

In `handle_admin_action`:

```python
        if action == "getusers":
            await query.edit_message_text(
                self._format_user_listing(), reply_markup=menus.admin_keyboard()
            )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_menu_home_screens -v`
Expected: PASS, 4 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent.

If an existing bus-navigation test asserts an exact button list for either menu, update that assertion to include `"home"` — do not remove the new row.

- [ ] **Step 7: Commit**

```bash
git add ykbot/app.py tests/test_menu_home_screens.py
git commit -m "Add a 🏠 Menu row to the bus menus and keep the admin menu under Get users"
```

---

### Task 6: Documentation, coverage, and deploy

**Files:**
- Modify: `README.md` (the "Button menu" section, lines 84-110)
- Verify: whole repo

- [ ] **Step 1: Document the button**

Append to the "Button menu" section of `README.md`, after the paragraph ending "asks for a ✅ Yes/❌ No confirmation before locking.":

```markdown
Results you got from the menu come back with a 🏠 Menu button attached — tapping
it posts a fresh menu **below** the result rather than replacing it, so a digest
you're still reading stays on screen. Results from a typed slash command have no
button (if you're typing commands you don't need one). The ⬅️ Back buttons inside
the menus still navigate by editing in place, which is what you want for a
throwaway menu screen. Old 🏠 buttons stay tappable indefinitely and always render
a current menu — button presence is never authorization, so every action still
re-checks your session.
```

- [ ] **Step 2: Run the full suite**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK` with 424 tests (379 before this feature, plus the 7 + 7 + 19 + 8 + 4 = 45 added here).

- [ ] **Step 3: Confirm 100% coverage**

Run:
```bash
python3 -m coverage run --source=ykbot,bot -m unittest discover -s tests -t . \
  && python3 -m coverage report -m
```
Expected: `TOTAL ... 100%`. If any new line is uncovered, add a test for it — do **not** add a `# pragma: no cover`.

- [ ] **Step 4: Static gate and import smoke**

Run: `python3 -m pyflakes bot.py ykbot/ && python3 -c "import bot" && echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 5: Read for callback safety**

Run: `grep -n "update.message.reply_text" ykbot/app.py`

Confirm every hit is inside a slash-command handler, not on a path reachable from a callback query or from `_consume_awaiting`. Tests cannot catch this — `FakeUpdate` aliases `effective_message` to `message`. This is a read-and-verify step, not a code change.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "Document the return-to-menu button"
```

- [ ] **Step 7: Deploy via the standing runbook**

This is a code change, so a restart is required. Follow `.claude/HANDOFF.md`'s runbook exactly:

Find the running PID with the /proc scan — **never** `pgrep -f "python3 bot.py"`, which matches the checking shell itself:

```bash
for p in $(pgrep '^python3$'); do \
  tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q 'bot[.]py' \
  && echo "$p"; done
```

Then stop the old instance before starting the new one:

```bash
kill <pid>
# wait for "Application stop() complete" in bot.log
setsid nohup python3 bot.py >> bot.log 2>&1 < /dev/null &
```

Verify: `Application started` followed by a `getUpdates ... 200 OK` in `bot.log`, no traceback, and exactly one instance from a repeat /proc scan.

- [ ] **Step 8: Live smoke test**

Ask the user to run this in Telegram — it exercises the case unit tests cannot prove (real callback updates have `update.message is None`):

1. `/menu` → tap 📰 News → type a subject.
2. Confirm the digest arrives with 🏠 Menu attached.
3. Tap 🏠 — the digest must still be on screen, with a fresh menu below it.
4. `/news space` typed directly — confirm **no** button on that digest.

- [ ] **Step 9: Report and await the merge decision**

Do not push. Report the branch state and let the user decide between merging to `main` and opening a PR.
