# /research Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/research <subject>` — a deeper, non-time-boxed counterpart to
`/news` — with 🔍 More depth / 🌐 More breadth follow-up buttons on every
findings message that re-run the research and deliver an updated report, and
the same 🏠 Menu button every other menu-initiated result carries.

**Architecture:** `_build_research_prompt(subject, interests, mode,
previous_findings=None)` for the three prompt variants; a shared
`_strip_preamble(reply, marker)` refactored out of `_strip_news_preamble`;
follow-up state lives in `context.user_data["research"]` (in-memory,
lost-on-restart, same pattern as `bus_menu`/`awaiting`); `_send_formatted`
gains an optional `last_chunk_markup` override so the depth/breadth+home
keyboard can replace the plain home-only default on the last chunk.

**Tech Stack:** Python 3, python-telegram-bot, stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-07-21-research-command-design.md`

## Global Constraints

- Branch: `research-command`. Base commit `9640ec9` (the `/news` recency-limit commit).
- Test command: `python3 -m unittest discover -s tests -t .` — must be green after every task.
- Static gate: `python3 -m pyflakes bot.py ykbot/` — must be clean after every task.
- Coverage must stay at **100%** line coverage of `bot.py` + `ykbot/`. Verify with
  `python3 -m coverage run --source=ykbot,bot -m unittest discover -s tests -t . && python3 -m coverage report -m`.
- Sandbox prereq (the repo `.venv` is Windows-only — **never recreate it here**):
  `pip install --break-system-packages -r requirements-dev.txt pyflakes coverage`
- Reuse the doubles in `tests/helpers.py` (`FakeUpdate`, `FakeContext`, `make_config`, `write_accounts_file`, `AsyncBotTestCase`, `BotTestCase`). Do not define new fakes.
- **Callback safety:** every reply site touched here must use `update.effective_message`, never `update.message` — real callback updates have `update.message is None`; `FakeUpdate` aliases the two so tests cannot catch a violation. Check by reading the code, per the standing runbook's gotcha.
- Depth/breadth callback data is exactly `"research:depth"` / `"research:breadth"`. Menu-button callback data is `"act:research"`. Awaiting-capture kind is `"research_subject"`.
- Do not add a cap on depth/breadth taps or any new persisted (on-disk) state — this feature is explicitly in-memory only, like `bus_menu`/`awaiting`.
- Commit after each task. Do not push.

---

### Task 1: Prompt builder, shared interests block, shared preamble stripper

Pure functions only — nothing user-facing changes yet.

**Files:**
- Modify: `ykbot/app.py` — `_build_news_prompt` (423), `_strip_news_preamble` (483)
- Test: `tests/test_pure_builders.py`, `tests/test_news_bot_builders.py`

**Interfaces:**
- Produces:
  - `YKBot._interests_block(interests: list) -> str`
  - `YKBot._strip_preamble(reply: str, marker: str) -> str`
  - `YKBot._build_research_prompt(subject: str, interests: list, mode: str, previous_findings: Optional[str] = None) -> str`
- `_strip_news_preamble` keeps its exact existing signature/behavior (becomes a one-line wrapper), so no existing test changes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pure_builders.py` (inside `NewsPromptBuilderTests`, or a
new `ResearchPromptBuilderTests` class following the same
`ExplodingStore`-touches-no-store pattern):

```python
class ResearchPromptBuilderTests(BotTestCase):
    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        b._interests_for = lambda account_id: ExplodingStore()
        return b

    def test_initial_mentions_subject_and_is_not_time_boxed(self):
        b = self._make_bot()

        prompt = b._build_research_prompt("fusion power", [], mode="initial").lower()

        self.assertIn("fusion power", prompt)
        self.assertIn("not limited to", prompt)
        self.assertNotIn("past 7 days", prompt)

    def test_initial_header_marker_is_research_findings(self):
        b = self._make_bot()

        prompt = b._build_research_prompt("fusion power", [], mode="initial")

        self.assertIn("Research Findings", prompt)

    def test_depth_embeds_previous_findings(self):
        b = self._make_bot()

        prompt = b._build_research_prompt(
            "fusion power", [], mode="depth",
            previous_findings="Research Findings — fusion power\n\nSome prior text.",
        )

        self.assertIn("Some prior text.", prompt)
        self.assertIn("more depth", prompt.lower())

    def test_breadth_embeds_previous_findings(self):
        b = self._make_bot()

        prompt = b._build_research_prompt(
            "fusion power", [], mode="breadth",
            previous_findings="Research Findings — fusion power\n\nSome prior text.",
        )

        self.assertIn("Some prior text.", prompt)
        self.assertIn("wide", prompt.lower() + prompt.lower())  # "wider"/"broader" etc.

    def test_interests_are_injected_and_not_instructed_to_be_revealed(self):
        b = self._make_bot()

        prompt = b._build_research_prompt("fusion power", ["clean energy"], mode="initial")

        self.assertIn("clean energy", prompt)
        self.assertIn("do not mention this list", prompt.lower())
```

Add to `tests/test_news_bot_builders.py`:

```python
    def test_strip_preamble_generic_marker(self):
        reply = "Blah blah.\n\nResearch Findings — X\n\nBody"
        cleaned = bot.YKBot._strip_preamble(reply, "Research Findings")
        self.assertTrue(cleaned.startswith("Research Findings — X"))

    def test_strip_news_preamble_still_works_as_a_thin_wrapper(self):
        # Regression: refactoring _strip_news_preamble onto _strip_preamble
        # must not change its existing observable behavior.
        reply = "Preamble.\n\nNews Digest — X — 2026-07-17\n\nBody"
        cleaned = bot.YKBot._strip_news_preamble(reply)
        self.assertTrue(cleaned.startswith("News Digest — X"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_pure_builders tests.test_news_bot_builders -v`
Expected: FAIL — `AttributeError: 'YKBot' object has no attribute '_build_research_prompt'` / `'_strip_preamble'`.

- [ ] **Step 3: Extract `_interests_block`**

In `ykbot/app.py`, replace the inline interests-block construction at the
top of `_build_news_prompt` (423) with a call to a new shared helper placed
just above it:

```python
    @staticmethod
    def _interests_block(interests: list) -> str:
        if not interests:
            return ""
        return (
            "For context, here are my personal interests (do not mention this list, "
            "its contents, or whether it is empty anywhere in your reply):\n"
            + "\n".join(f"- {entry}" for entry in interests)
            + "\n\n"
        )

    def _build_news_prompt(self, subject: str, interests: list) -> str:
        window_days = self._config.news_recency_days
        today = date.today()
        cutoff = today - timedelta(days=window_days)
        return (
            self._interests_block(interests)
            + f'Write a news digest about "{subject}", covering only developments from the '
            # ... (unchanged from here down)
```

Keep every other line of `_build_news_prompt` exactly as it is today — this
step only replaces the `if interests: ... else: ...` block with the
`self._interests_block(interests)` call.

- [ ] **Step 4: Add `_strip_preamble` and make `_strip_news_preamble` a wrapper**

Replace `_strip_news_preamble` (483) with:

```python
    @staticmethod
    def _strip_preamble(reply: str, marker: str) -> str:
        """Drop any preamble the model emits before the first line whose
        visible text — after leading whitespace and any opening HTML tags —
        starts with `marker`. Returns the reply unchanged if no such line
        exists, rather than risk dropping real content."""
        lines = reply.splitlines(keepends=True)
        marker_len = len(marker)
        for i, line in enumerate(lines):
            visible = re.sub(r"^\s*(?:<[^>]+>\s*)*", "", line)
            if visible[:marker_len].lower() == marker.lower():
                return "".join(lines[i:])
        return reply

    @staticmethod
    def _strip_news_preamble(reply: str) -> str:
        """Thin wrapper kept for the existing call site and existing tests.
        See _strip_preamble for the actual logic."""
        return YKBot._strip_preamble(reply, "News Digest")
```

- [ ] **Step 5: Add `_build_research_prompt`**

Directly below `_build_news_prompt`:

```python
    def _build_research_prompt(
        self, subject: str, interests: list, mode: str, previous_findings: Optional[str] = None,
    ) -> str:
        today = date.today()
        sourcing_rules = (
            "Take extra care with sourcing: only use information you can verify against "
            "reputable, established sources — major news organizations, official or primary "
            "sources, and well-known publications. Do NOT rely on blogs, forums, social-media "
            "posts, content farms, or obscure/unknown sites. Corroborate each item across "
            "more than one reputable source where you can, and leave out anything you cannot "
            "verify or that reads like rumor or speculation. Label every dated item in "
            "(YYYY-MM-DD) form and end every section with its source(s) as clickable links "
            'in the form <a href="URL">Publication</a>.'
        )
        formatting_rules = (
            "Begin the reply immediately with the findings header line, which MUST start "
            f'with the literal words "Research Findings" (for example: <b>Research Findings '
            f'— {subject}</b>). Put NOTHING before that header. Format it for a Telegram '
            "message using only these HTML tags: <b>, <i>, <u>, <s>, <code>, <pre>, and "
            '<a href="URL">text</a>. Do not use Markdown syntax. Use short paragraphs, '
            "clear section headers via <b>, and simple bullet points (•) for scanability. "
            "Escape any literal <, >, or & characters that aren't part of a tag."
        )

        if mode == "initial":
            return (
                self._interests_block(interests)
                + f'Research "{subject}" thoroughly and produce a detailed findings report — '
                "not just a quick recent-news digest, but a deeper investigation: relevant "
                "background and context, the current state of the topic, multiple angles or "
                "perspectives where relevant, and notable nuance or disagreement between "
                "sources. Biased toward my documented interests where they're relevant to "
                "this subject (don't force a connection that isn't there). "
                f"Today's date is {today.isoformat()}. You are not limited to recent "
                "developments the way a news digest is — pull in older foundational "
                "background where it helps understanding, though still highlight what's "
                "currently most significant. "
                + sourcing_rules + " " + formatting_rules
            )

        direction = (
            "The user wants MORE DEPTH than the previous pass: more granular detail, more "
            "corroborating sources per claim, and explicit caveats, edge cases, or "
            "disagreements between sources that the previous pass glossed over."
            if mode == "depth" else
            "The user wants MORE BREADTH than the previous pass: additional related angles, "
            "adjacent subtopics, perspectives or stakeholders not yet covered, and a wider "
            "variety of source types."
        )
        return (
            self._interests_block(interests)
            + f'You previously researched "{subject}" and produced this findings report:\n\n'
            f"{previous_findings}\n\n"
            f"{direction} Produce a full REPLACEMENT report — not a diff and not just new "
            "material tacked on — that supersedes the previous pass. "
            + sourcing_rules + " " + formatting_rules
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_pure_builders tests.test_news_bot_builders -v`
Expected: PASS.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent.

- [ ] **Step 7: Commit**

```bash
git add ykbot/app.py tests/test_pure_builders.py tests/test_news_bot_builders.py
git commit -m "Add the /research prompt builder and a shared preamble stripper"
```

---

### Task 2: Keyboards — depth/breadth row and the menu layout change

**Files:**
- Modify: `ykbot/menus.py` — `top_level_keyboard` (13), append new function
- Modify: `tests/test_menus.py` — `TopLevelKeyboardTests.test_non_admin_has_no_admin_row`
  (14) currently asserts the exact ordered button list; it must change to
  match the new layout, not be reverted.
- Test: `tests/test_menus.py` (new keyboard-shape tests, same file every
  other pure-menus test already lives in — `menus` is already imported there
  via `from ykbot import menus`, not through `bot`)

**Interfaces:**
- Produces: `menus.research_followup_keyboard(from_menu: bool) -> InlineKeyboardMarkup`
- Changes: `menus.top_level_keyboard(is_admin)`'s row layout (see spec's
  "Menu layout" section) — adds a Research button.

- [ ] **Step 1: Write the failing tests**

Update the existing test in `tests/test_menus.py` (14) to match the new
layout instead of the old one:

```python
class TopLevelKeyboardTests(unittest.TestCase):
    def test_non_admin_has_no_admin_row(self):
        d = datas(menus.top_level_keyboard(is_admin=False))
        self.assertEqual(
            d,
            [
                "act:bus", "act:news",
                "act:research", "act:summarize",
                "menu:interests", "act:endsession",
            ],
        )
        self.assertNotIn("menu:admin", d)

    def test_admin_gets_admin_row(self):
        self.assertIn("menu:admin", datas(menus.top_level_keyboard(is_admin=True)))
```

Add a new class to the same file:

```python
class ResearchFollowupKeyboardTests(unittest.TestCase):
    def test_always_has_depth_and_breadth(self):
        d = datas(menus.research_followup_keyboard(from_menu=False))
        self.assertIn("research:depth", d)
        self.assertIn("research:breadth", d)
        self.assertNotIn("home", d)

    def test_home_row_only_when_from_menu(self):
        self.assertIn("home", datas(menus.research_followup_keyboard(from_menu=True)))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_menus -v`
Expected: FAIL — `AttributeError: module 'ykbot.menus' has no attribute 'research_followup_keyboard'`, and `test_non_admin_has_no_admin_row` fails against the current layout (expected list won't match until Step 3 lands).

- [ ] **Step 3: Update `top_level_keyboard`**

```python
def top_level_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🚌 Bus", callback_data="act:bus"),
         InlineKeyboardButton("📰 News", callback_data="act:news")],
        [InlineKeyboardButton("🔬 Research", callback_data="act:research"),
         InlineKeyboardButton("🔗 Summarize", callback_data="act:summarize")],
        [InlineKeyboardButton("⭐ Interests", callback_data="menu:interests"),
         InlineKeyboardButton("🔒 End session", callback_data="act:endsession")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Admin", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 4: Add `research_followup_keyboard`**

Append to `ykbot/menus.py`:

```python
def research_followup_keyboard(from_menu: bool) -> InlineKeyboardMarkup:
    """Depth/breadth buttons go on every /research finding regardless of how
    it was reached; the 🏠 row only when the research thread started from
    the button menu, matching every other result message in this bot."""
    rows = [[
        InlineKeyboardButton("🔍 More depth", callback_data="research:depth"),
        InlineKeyboardButton("🌐 More breadth", callback_data="research:breadth"),
    ]]
    if from_menu:
        rows.append([HOME_BUTTON])
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_menus -v`
Expected: PASS.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`. If any *other* existing test (outside `test_menus.py`) asserts
the old row layout or the old button pairing, update that assertion to match
the new layout rather than reverting it — search with
`grep -rn "act:summarize\|menu:interests" tests/` to find any.

- [ ] **Step 6: Commit**

```bash
git add ykbot/menus.py tests/test_menus.py
git commit -m "Add the Research button to the top-level menu and the depth/breadth keyboard"
```

---

### Task 3: `_send_formatted`'s `last_chunk_markup` override

**Files:**
- Modify: `ykbot/app.py` — `_send_formatted` (1334)
- Test: `tests/test_reply_result.py` (extend existing `SendFormattedButtonTests`)

**Interfaces:**
- Produces: `_send_formatted(update, text, from_menu=False, last_chunk_markup: Optional[InlineKeyboardMarkup] = None)`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_reply_result.py`, inside `SendFormattedButtonTests`:

```python
    async def test_last_chunk_markup_overrides_the_home_default(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        custom = menus.research_followup_keyboard(from_menu=False)

        await b._send_formatted(update, "short", from_menu=True, last_chunk_markup=custom)

        markup = update.message.replies_markup[-1]
        self.assertIn("research:depth", [b.callback_data for row in markup.inline_keyboard for b in row])
        self.assertNotIn("home", [b.callback_data for row in markup.inline_keyboard for b in row])

    async def test_last_chunk_markup_only_on_the_final_chunk(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        text = self._long_text()
        custom = menus.research_followup_keyboard(from_menu=False)

        await b._send_formatted(update, text, last_chunk_markup=custom)

        markups = update.message.replies_markup
        self.assertIsNone(markups[0])
        self.assertIsNone(markups[1])
        self.assertIsNotNone(markups[2])

    async def test_omitting_last_chunk_markup_keeps_old_default_behavior(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._send_formatted(update, "short", from_menu=True)

        self.assertEqual(
            update.message.replies_markup[-1].inline_keyboard[0][0].callback_data, "home"
        )
```

Add `from ykbot import menus` to this test file's imports if not already present.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_reply_result -v`
Expected: FAIL — `TypeError: _send_formatted() got an unexpected keyword argument 'last_chunk_markup'`.

- [ ] **Step 3: Update `_send_formatted`**

```python
    async def _send_formatted(
        self, update: Update, text: str, from_menu: bool = False,
        last_chunk_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Sends Claude's reply, chunked for Telegram's message limit. Tries
        HTML formatting first and falls back to plain text per chunk if the
        markup doesn't parse — LLM-generated tags aren't guaranteed
        well-formed, so a broken reply must never mean a lost reply.

        The button goes on the LAST chunk only, so a multi-chunk reply ends
        with one button row rather than one per message. last_chunk_markup,
        when given, replaces the default "home button only if from_menu"
        logic entirely — used by /research to attach its depth/breadth row
        (plus 🏠 when appropriate) instead of the plain home button."""
        chunks = TelegramFormatter.chunk(text)
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            if not last:
                markup = None
            elif last_chunk_markup is not None:
                markup = last_chunk_markup
            else:
                markup = menus.home_keyboard() if from_menu else None
            try:
                await update.effective_message.reply_text(
                    chunk, parse_mode=ParseMode.HTML, reply_markup=markup
                )
            except BadRequest:
                logger.warning("Telegram rejected HTML formatting, sending chunk as plain text")
                await update.effective_message.reply_text(chunk, reply_markup=markup)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_reply_result -v`
Expected: PASS.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent. All pre-existing `_send_formatted` tests still pass unchanged (default path untouched).

- [ ] **Step 5: Commit**

```bash
git add ykbot/app.py tests/test_reply_result.py
git commit -m "Let _send_formatted's last chunk take a caller-supplied keyboard override"
```

---

### Task 4: The `/research` command itself — slash, menu button, capture

**Files:**
- Modify: `ykbot/app.py` — `_build_command_specs` (160), `handle_menu_action` (615), `_consume_awaiting` (1142), `_build_application` (285, the `act:` regex at line ~318-320), new `handle_research`/`_do_research` near `handle_news`/`_do_news` (726)
- Test: `tests/test_menu_capture_button.py` or a new `tests/test_research.py`

**Interfaces:**
- Consumes: `_build_research_prompt`, `_strip_preamble` (Task 1); `_send_formatted(..., last_chunk_markup=...)` (Task 3); `menus.research_followup_keyboard` (Task 2).
- Produces: `YKBot.handle_research(update, context)`, `YKBot._do_research(update, context, account_id, subject, from_menu=False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research.py`:

```python
import bot
from ykbot import menus
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [] if markup is None else [b.callback_data for row in markup.inline_keyboard for b in row]


class ResearchEntryPointTests(AsyncBotTestCase):
    CHAT_ID = 81

    def _make_bot(self, reply="Research Findings — fusion power\n\nBody."):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return reply

        b._run_claude = fake_run_claude
        return b

    async def test_slash_without_subject_shows_usage(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_research(update, FakeContext(args=[]))

        self.assertIn("/research <subject>", update.message.replies[-1])

    async def test_slash_research_delivers_findings_with_depth_breadth_but_no_home(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_research(update, FakeContext(args=["fusion", "power"]))

        markup = update.message.replies_markup[-1]
        self.assertIn("research:depth", datas(markup))
        self.assertIn("research:breadth", datas(markup))
        self.assertNotIn("home", datas(markup))

    async def test_menu_action_research_arms_capture_slot(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="act:research")
        context = FakeContext()

        await b.handle_menu_action(update, context)

        self.assertEqual(context.user_data["awaiting"], {"kind": "research_subject", "payload": {}})

    async def test_capture_delivers_findings_with_home_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, text="fusion power")
        context = FakeContext(
            user_data={"awaiting": {"kind": "research_subject", "payload": {}}}
        )

        await b._consume_awaiting(update, context, self.CHAT_ID)

        markup = update.message.replies_markup[-1]
        self.assertIn("home", datas(markup))
        self.assertIn("research:depth", datas(markup))

    async def test_successful_research_stores_followup_state(self):
        b = self._make_bot(reply="Research Findings — fusion power\n\nSome body text.")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)
        context = FakeContext(args=["fusion", "power"])

        await b.handle_research(update, context)

        state = context.user_data["research"]
        self.assertEqual(state["subject"], "fusion power")
        self.assertIn("Some body text.", state["findings"])
        self.assertFalse(state["from_menu"])

    async def test_failed_research_does_not_store_state(self):
        b = self._make_bot()

        async def failing(update, context, prompt, **kwargs):
            return None

        b._run_claude = failing
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)
        context = FakeContext(args=["fusion", "power"])

        await b.handle_research(update, context)

        self.assertNotIn("research", context.user_data)

    async def test_research_command_is_registered_and_listed_under_its_own_category(self):
        b = bot.YKBot(make_config(self.tmp_path))

        listing = b._build_command_list(admin=False)

        self.assertIn("Research:", listing)
        self.assertIn("/research <subject>", listing)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research -v`
Expected: FAIL — `AttributeError: 'YKBot' object has no attribute 'handle_research'`.

- [ ] **Step 3: Add the `CommandSpec`**

In `_build_command_specs` (160), directly after the `"news"` spec:

```python
            CommandSpec(
                "research", "/research <subject>",
                "deeper background and multiple angles on a subject — not limited to "
                "recent news like /news", self.handle_research,
                category="Research",
            ),
```

- [ ] **Step 4: Add `handle_research` / `_do_research`**

Directly after `_do_news` (currently ending around line 756):

```python
    async def handle_research(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /research <subject>\nExample: /research fusion power"
            )
            return

        await self._do_research(update, context, account_id, " ".join(context.args))

    async def _do_research(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        subject: str,
        from_menu: bool = False,
    ) -> None:
        if self._config.news_learning_enabled:
            try:
                await asyncio.to_thread(self._news_history_for(account_id).append, subject)
            except Exception:
                logger.exception("Failed to log /research subject for %s", account_id)
        interests = await asyncio.to_thread(self._interests_for(account_id).list)
        reply = await self._run_claude(
            update, context, self._build_research_prompt(subject, interests, mode="initial"),
            web_tools=True,
        )
        if reply is None:
            return
        findings = self._strip_preamble(reply, "Research Findings")
        context.user_data["research"] = {
            "subject": subject, "findings": findings, "from_menu": from_menu,
        }
        await self._send_formatted(
            update, findings, from_menu,
            last_chunk_markup=menus.research_followup_keyboard(from_menu),
        )
```

- [ ] **Step 5: Wire the menu button and capture branch**

In `handle_menu_action` (615), add a branch alongside `news`/`summarize`/`addinterest`:

```python
        elif action == "research":
            context.user_data["awaiting"] = {"kind": "research_subject", "payload": {}}
            await query.edit_message_text(
                "Send the subject to research:", reply_markup=menus.back_keyboard("menu:root")
            )
```

In `_consume_awaiting` (1142), add a branch alongside `news_subject`:

```python
        elif kind == "research_subject":
            await self._do_research(update, context, account_id, text.strip(), from_menu=True)
```

- [ ] **Step 6: Register the command and widen the `act:` callback pattern**

In `_build_application`, the `CommandHandler` loop already picks up
`handle_research` automatically via `_command_specs`. Widen the existing
`act:` pattern (currently `r"^act:(getinterests|compile|endsession|news|summarize|addinterest)$"`)
to include `research`:

```python
        app.add_handler(CallbackQueryHandler(
            self.handle_menu_action,
            pattern=r"^act:(getinterests|compile|endsession|news|summarize|addinterest|research)$"))
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research -v`
Expected: PASS, 7 tests.

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent.

- [ ] **Step 8: Commit**

```bash
git add ykbot/app.py tests/test_research.py
git commit -m "Add /research: slash command, menu button, and text-capture entry points"
```

---

### Task 5: Depth/breadth follow-up handling

**Files:**
- Modify: `ykbot/app.py` — new `handle_research_followup`/`_do_research_followup` near `_do_research`; `_build_application` (new registration)
- Test: `tests/test_research.py` (extend)

**Interfaces:**
- Consumes: `context.user_data["research"]` state written by `_do_research` (Task 4); `_build_research_prompt(..., mode="depth"/"breadth", previous_findings=...)` (Task 1).
- Produces: `YKBot.handle_research_followup(update, context)`, `YKBot._do_research_followup(update, context, account_id, state, mode)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_research.py`:

```python
class ResearchFollowupTests(AsyncBotTestCase):
    CHAT_ID = 82

    def _make_bot(self, reply="Research Findings — fusion power\n\nDeeper body."):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        async def fake_run_claude(update, context, prompt, **kwargs):
            fake_run_claude.last_prompt = prompt
            return reply

        b._run_claude = fake_run_claude
        return b

    def _state(self, from_menu=False):
        return {
            "subject": "fusion power",
            "findings": "Research Findings — fusion power\n\nOriginal body.",
            "from_menu": from_menu,
        }

    async def test_expired_state_replies_without_calling_claude(self):
        b = self._make_bot()

        async def should_not_be_called(*args, **kwargs):
            raise AssertionError("Claude must not be called for expired state")

        b._run_claude = should_not_be_called
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")

        await b.handle_research_followup(update, FakeContext())

        self.assertIn("expired", update.message.replies[-1].lower())

    async def test_depth_tap_embeds_previous_findings_and_updates_state(self):
        b = self._make_bot(reply="Research Findings — fusion power\n\nDeeper body.")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": self._state()})

        await b.handle_research_followup(update, context)

        self.assertIn("Original body.", b._run_claude.last_prompt)
        self.assertIn("MORE DEPTH", b._run_claude.last_prompt)
        self.assertIn("Deeper body.", context.user_data["research"]["findings"])

    async def test_breadth_tap_uses_breadth_wording(self):
        b = self._make_bot(reply="Research Findings — fusion power\n\nWider body.")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:breadth")
        context = FakeContext(user_data={"research": self._state()})

        await b.handle_research_followup(update, context)

        self.assertIn("MORE BREADTH", b._run_claude.last_prompt)

    async def test_followup_carries_home_button_from_stored_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": self._state(from_menu=True)})

        await b.handle_research_followup(update, context)

        markup = update.message.replies_markup[-1]
        self.assertIn("home", datas(markup))

    async def test_followup_without_from_menu_has_no_home_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": self._state(from_menu=False)})

        await b.handle_research_followup(update, context)

        markup = update.message.replies_markup[-1]
        self.assertNotIn("home", datas(markup))

    async def test_failed_followup_leaves_state_untouched(self):
        b = self._make_bot()

        async def failing(update, context, prompt, **kwargs):
            return None

        b._run_claude = failing
        original_state = self._state()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": dict(original_state)})

        await b.handle_research_followup(update, context)

        self.assertEqual(context.user_data["research"], original_state)

    async def test_locked_chat_gets_trivia_not_a_followup(self):
        b = bot.YKBot(make_config(self.tmp_path))
        update = FakeUpdate(user_id=1, chat_id=999, callback_data="research:depth")

        await b.handle_research_followup(update, FakeContext())

        self.assertNotIn("research", FakeContext().user_data)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_research -v`
Expected: FAIL — `AttributeError: 'YKBot' object has no attribute 'handle_research_followup'`.

- [ ] **Step 3: Add the handler and its core**

Directly after `_do_research`:

```python
    async def handle_research_followup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        mode = query.data.split(":", 1)[1]  # "depth" or "breadth"
        state = context.user_data.get("research")
        if state is None:
            await update.effective_message.reply_text(
                "This research thread has expired — run /research <subject> again."
            )
            return
        await self._do_research_followup(update, context, account_id, state, mode)

    async def _do_research_followup(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        state: dict,
        mode: str,
    ) -> None:
        interests = await asyncio.to_thread(self._interests_for(account_id).list)
        prompt = self._build_research_prompt(
            state["subject"], interests, mode=mode, previous_findings=state["findings"],
        )
        reply = await self._run_claude(update, context, prompt, web_tools=True)
        if reply is None:
            return
        findings = self._strip_preamble(reply, "Research Findings")
        from_menu = state["from_menu"]
        context.user_data["research"] = {
            "subject": state["subject"], "findings": findings, "from_menu": from_menu,
        }
        await self._send_formatted(
            update, findings, from_menu,
            last_chunk_markup=menus.research_followup_keyboard(from_menu),
        )
```

- [ ] **Step 4: Register the callback handler**

In `_build_application`, alongside the other `research`-related registration
from Task 4 (or right after it):

```python
        app.add_handler(CallbackQueryHandler(self.handle_research_followup, pattern=r"^research:"))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_research -v`
Expected: PASS, 14 tests total (7 from Task 4 + 7 here).

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && python3 -m pyflakes bot.py ykbot/`
Expected: `OK`, pyflakes silent.

- [ ] **Step 6: Commit**

```bash
git add ykbot/app.py tests/test_research.py
git commit -m "Add depth/breadth follow-up handling for /research"
```

---

### Task 6: Documentation, coverage, and deploy

**Files:**
- Modify: `README.md` (Commands table, env-var-adjacent prose if needed, "Button menu" section)
- Verify: whole repo

- [ ] **Step 1: Document the command**

Add a row to the Commands table in `README.md` (near `/news`):

```markdown
| `/research <subject>` | Deeper, non-time-boxed research on a subject: background, current state, multiple angles — tap 🔍 More depth / 🌐 More breadth on the result to refine it further |
```

Add a short paragraph near the existing "Button menu" section documenting
the 🔬 Research button, the depth/breadth follow-up buttons, and that the
follow-up state is in-memory (lost on restart, same as the bus-menu origin
memory and text-capture slots) — mirror the tone of the existing 🏠 Menu
paragraph added by the 2026-07-20 menu-return-button work.

- [ ] **Step 2: Run the full suite**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK`, count = 484 (current baseline after the `/news` recency
change) + everything added across Tasks 1–5 here.

- [ ] **Step 3: Confirm 100% coverage**

```bash
python3 -m coverage run --source=ykbot,bot -m unittest discover -s tests -t . \
  && python3 -m coverage report -m
```
Expected: `TOTAL ... 100%`. Add tests for any uncovered line — do **not** add a `# pragma: no cover`.

- [ ] **Step 4: Static gate and import smoke**

Run: `python3 -m pyflakes bot.py ykbot/ && python3 -c "import bot" && echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 5: Read for callback safety**

Run: `grep -n "update.message.reply_text\|update.message.replies_markup" ykbot/app.py`

Confirm every new site added in Tasks 4–5 (`handle_research`, `_do_research`,
`handle_research_followup`, `_do_research_followup`) replies via
`update.effective_message`, never `update.message`, on any path reachable
from a callback query (`handle_research_followup` and everything it calls).
`FakeUpdate` cannot catch this — it's a read-and-verify step, not a code
change, per the standing runbook's gotcha.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "Document /research and its depth/breadth follow-up buttons"
```

- [ ] **Step 7: Deploy via the standing runbook**

Code change → restart required. Follow `.claude/HANDOFF.md`'s runbook
exactly: /proc-scan for the PID (never `pgrep -f "python3 bot.py"`), kill,
wait for `Application.stop() complete`, relaunch with
`setsid nohup python3 bot.py >> bot.log 2>&1 < /dev/null &`, confirm
`Application started`, no traceback, and exactly one instance from a repeat
/proc scan.

- [ ] **Step 8: Live smoke test**

Ask the user to run this in Telegram — it exercises what unit tests cannot
prove (real callback updates have `update.message is None`):

1. `/research fusion power` typed directly. Confirm a "Research Findings"
   report arrives with 🔍 More depth / 🌐 More breadth, and **no** 🏠.
2. Tap 🔍 More depth. Confirm a **new** message arrives (the first stays on
   screen) with an expanded report and fresh depth/breadth buttons, still no 🏠.
3. `/menu` → 🔬 Research → type a subject. Confirm the result has depth,
   breadth, **and** 🏠.
4. Tap 🌐 More breadth on that one. Confirm the new message still carries 🏠.
5. Restart the bot (or simulate by clearing `context.user_data` if a real
   restart isn't convenient) and tap a pre-restart depth/breadth button.
   Confirm the "This research thread has expired" message, not a crash or
   silent no-op.

- [ ] **Step 9: Report and await the merge decision**

Do not push. Report the branch state and let the user decide between
merging to `main` and opening a PR.
