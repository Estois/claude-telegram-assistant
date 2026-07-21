# /research — a deeper counterpart to /news

## Problem

`/news <subject>` is deliberately narrow: a quick digest of the past week's
developments (see the 2026-07-21 recency-limit change), biased toward the
account's interests. Sometimes the user wants more than a digest — background
and context, multiple angles, deeper sourcing, not capped to the last 7 days.
There is no command for that today, and no way to ask for *more* once a
result comes back short.

## Scope

A new `/research <subject>` command, structurally a sibling of `/news`
(same session gate, same account-interests bias, same web-tools-only Claude
sandboxing, same button-menu + text-capture entry points), but:

- **Not time-boxed.** Confirmed with the user: research is allowed to pull in
  older background/context; it is not limited to the past week the way
  `/news` is.
- **Every findings message carries two follow-up buttons** — 🔍 More depth
  and 🌐 More breadth — that re-run the research in that direction and
  deliver an updated findings message, which again carries the same two
  buttons (chainable: tap depth twice to go deeper twice).
- **Carries the 🏠 Menu button** under the same `from_menu` rule as every
  other result in this bot (see `2026-07-20-menu-return-button-design.md`) —
  attached only when the *original* `/research` call came from the button
  menu, and preserved across depth/breadth taps regardless of how many
  follow-ups have happened since.

Out of scope: a distinct recency dial, a "compare to /news" mode, admin
visibility into what people research, and any cap on how many times someone
can tap depth/breadth.

## Design

### Prompt builder

`_build_research_prompt(subject, interests, mode, previous_findings=None)`,
`mode ∈ {"initial", "depth", "breadth"}`.

`initial` asks Claude to research the subject "thoroughly": relevant
background/context, the current state of the topic, multiple angles or
perspectives where relevant, notable nuance or disagreement between sources —
explicitly telling it this is deeper than a news digest and it is *not*
limited to recent developments, while still highlighting what's currently
most significant. Same sourcing bar as `/news` (reputable sources only,
corroborate where possible, no rumor/speculation), same per-item dating and
`<a href="URL">Publication</a>` linking, same Telegram-HTML-only formatting
rules. Header must begin with the literal words **"Research Findings"** (the
`/news` equivalent of "News Digest") so a shared preamble-stripper can find
it.

`depth`/`breadth` embed the previous findings text verbatim and ask Claude to
produce a **replacement** report — not a diff, not a "here's more" tacked on
— that goes deeper (more granular detail, more corroborating sources per
claim, caveats/edge cases/disagreements the first pass glossed over) or wider
(additional related angles, adjacent subtopics, perspectives/stakeholders not
yet covered, a broader variety of source types) than the pass it's replacing.
Same header requirement, same sourcing/formatting rules.

The interests-injection block used by `_build_news_prompt` is extracted into
a small shared helper (`_interests_block(interests)`) so both prompt
builders stay in sync rather than maintaining two copies of the same
"mention my interests but never reveal the list" instruction.

### Preamble stripping

`_strip_news_preamble` becomes a thin wrapper around a new
`_strip_preamble(reply, marker)` that does the actual "drop everything before
the first line whose visible text starts with `marker`" work — existing
tests calling `_strip_news_preamble` directly keep passing unchanged. A
`/research` reply is stripped the same way against the marker `"Research
Findings"`, on every pass (initial and every depth/breadth follow-up).

### Follow-up state

Depth/breadth buttons cannot carry the subject or the previous findings in
`callback_data` — Telegram's 64-byte limit was already hit once in this
project (account names bounded to ≤32 chars for `adm_rm:<name>`), and a
research subject or a multi-paragraph findings report is far larger. Instead,
state rides in `context.user_data["research"]`, the same in-memory,
per-chat, lost-on-restart pattern already used for `bus_menu` origin memory
and `awaiting` capture slots:

```python
context.user_data["research"] = {
    "subject": subject,
    "findings": stripped_reply,   # always the MOST RECENT pass
    "from_menu": from_menu,        # sticky across every follow-up
}
```

Set after every successful pass (initial or follow-up) — never on a failed
Claude call, so a failed depth tap doesn't wipe out a good report the user
could still tap again.

`callback_data` is just `"research:depth"` / `"research:breadth"` (no
payload), matching the `act:news` / `int_rm:3` / `cfm:token` colon-prefixed
convention already used everywhere else in this file.

**Expiry:** if `context.user_data["research"]` is missing (bot restarted
since, or the buttons are attached to some other chat's stale message — it
can't be, since `user_data` is per-chat, but a session could have ended and
restarted since), the handler replies with a plain "This research thread has
expired — run /research <subject> again." rather than crashing or silently
doing nothing. Mirrors the existing "Menu expired —" bus-menu fallback and
the "Interest #4 no longer exists" pattern.

### Delivery — reusing `_send_formatted`, not replacing it

`_send_formatted` gains one new optional parameter:

```python
async def _send_formatted(
    self, update: Update, text: str, from_menu: bool = False,
    last_chunk_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
```

When `last_chunk_markup` is given, it's used verbatim on the final chunk
(instead of the default "home button only if `from_menu`" logic) — every
other caller passes nothing and keeps today's behavior exactly. `/research`
passes `menus.research_followup_keyboard(from_menu)`:

```python
def research_followup_keyboard(from_menu: bool) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("🔍 More depth", callback_data="research:depth"),
        InlineKeyboardButton("🌐 More breadth", callback_data="research:breadth"),
    ]]
    if from_menu:
        rows.append([HOME_BUTTON])
    return InlineKeyboardMarkup(rows)
```

Reuses the existing `HOME_BUTTON` constant `menus.py` already exports for
exactly this kind of composition. A multi-chunk research report still gets
exactly one button row, on the last chunk — same rule as the plain 🏠 case.

### Entry points (mirrors /news exactly)

- **Slash:** `/research <subject>` → `handle_research` → `_do_research(...,
  from_menu=False)`.
- **Menu button:** a new "🔬 Research" button (`act:research`) added to
  `handle_menu_action`'s existing pattern, arms
  `context.user_data["awaiting"] = {"kind": "research_subject", "payload": {}}`
  and prompts "Send the subject to research:" with `menus.back_keyboard("menu:root")`
  — identical shape to the existing `news`/`summarize`/`addinterest` branches.
- **Capture:** `_consume_awaiting` gets one new `elif kind == "research_subject"`
  branch calling `_do_research(..., from_menu=True)`.

### Follow-up handling

One new callback handler, `handle_research_followup`, registered on pattern
`^research:`:

```python
async def handle_research_followup(self, update, context):
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
```

`_do_research_followup` builds the follow-up prompt from `state["subject"]`
+ `state["findings"]` + `mode`, runs Claude with `web_tools=True`, and on a
non-`None` reply: strips the preamble, updates
`context.user_data["research"]` with the new findings (subject and
`from_menu` unchanged), and delivers via `_send_formatted(..., state["from_menu"],
last_chunk_markup=menus.research_followup_keyboard(state["from_menu"]))`.
On a failed Claude call, the state is left untouched (the user can tap again)
and nothing further is sent — `_run_claude` has already delivered the
error/usage-limit notice.

### Passive interest-learning

`/research`'s initial call also logs its subject via the same
`news_history_for(account_id)` store `/news` already uses (gated the same
way on `Config.news_learning_enabled`) — it's the same "what has this
account been asking about" signal the interest-reorganize job already
consumes, and there is no reason to keep two separate logs for two flavors
of "look this up for me." Depth/breadth follow-ups do **not** log again —
they're refinements of a subject already logged once.

### Menu layout

Adding a fourth top-level action means rebalancing
`menus.top_level_keyboard`. Proposed layout (a deliberate rearrangement,
flagged here for review rather than silently shipped):

```
🚌 Bus        📰 News
🔬 Research   🔗 Summarize
⭐ Interests   🔒 End session
🛠 Admin                        (admin sessions only)
```

Groups the two web-research commands together, and pairs the two
one-shot account actions (Interests, End session) together. Today's layout
has Interests paired with Summarize and End session alone.

### Command registration

New `CommandSpec("research", "/research <subject>", "deeper background and
multiple angles on a subject — not limited to recent news like /news",
self.handle_research, category="Research")`. A new category (rather than
folding into "News") so `/help` lists it as its own scannable section.

## Deliberate non-goals

- **No cap on depth/breadth taps.** Each tap is an independent, serialized
  (per-chat lock, same as every other Claude call) one-shot invocation;
  repeated taps just mean repeated Claude calls and a longer embedded
  "previous findings" blob in the prompt. Not addressed here — revisit only
  if it proves to be a real problem in practice.
- **No new persisted data.** `context.user_data["research"]` is in-memory,
  lost on restart, exactly like `bus_menu` and `awaiting` — the user just
  re-runs `/research <subject>` if the bot restarts mid-thread.
- **Bus arrivals / other menus untouched** beyond the one new row/button.

## Security

Identical posture to every other command: `_require_session` gates
`handle_research` and is re-checked in `handle_research_followup` (every
callback handler re-checks; button visibility is never authorization).
`/research` gets the same `--tools "WebSearch" "WebFetch"` sandboxing as
`/news`/`/summarize` — never the full tool set — so this is not a new
prompt-injection surface beyond what `/news` already has. No account data is
embedded in the prompt beyond the same interests list `/news` already
injects (with the same "never reveal this list" instruction).

## Testing

Suite must stay green at 100% line coverage
(`python3 -m unittest discover -s tests -t .`), reusing `tests/helpers.py`
doubles — no new fakes.

- `_build_research_prompt`: initial mode mentions the subject, is not
  time-boxed (no "past N days" language), mentions reputable sourcing/dates;
  depth/breadth modes embed the previous findings text and each other's
  distinguishing instruction (depth → more granular/nuanced; breadth → wider
  net of angles/subtopics).
- `_strip_preamble`/`_strip_news_preamble`/(research call sites): existing
  news tests unchanged; new tests for the "Research Findings" marker.
- `menus.research_followup_keyboard`: depth+breadth present always; home row
  present only when `from_menu=True`.
- `_send_formatted` with `last_chunk_markup` set: overrides the default
  home-only logic, still only on the last chunk of a multi-chunk reply;
  omitting the parameter keeps every existing test passing unchanged.
- `_do_research`: depth/breadth buttons are present **regardless** of entry
  point (only the 🏠 row is conditional) — verify slash-invoked research
  still gets depth/breadth buttons but not 🏠, and menu-invoked research
  gets both.
- `handle_research_followup`: expired state (no `context.user_data["research"]`)
  replies with the expiry notice and does not call Claude; depth vs breadth
  produce different prompts; state is updated after a successful pass and
  left untouched after a failed one; `from_menu` carries through from the
  original state, not re-derived from the follow-up's own callback query
  (which, being a callback, would otherwise look "from_menu" every time).
- Menu wiring: `act:research` arms the capture slot with the right prompt
  text and back target; `_consume_awaiting`'s new branch calls `_do_research`
  with `from_menu=True`; `/research` slash command registers and rejects a
  missing subject the same way `/news` does.
- `handle_research` / `_build_command_specs` / `/help`: `/research` appears
  under its own "Research:" category.

## Deployment

A code change: full standing runbook (verify while the old bot runs →
/proc-scan kill → `setsid nohup python3 bot.py >> bot.log 2>&1 < /dev/null &`
→ confirm one healthy instance, no traceback). Live smoke test: `/research
<subject>` directly (confirm findings arrive, depth/breadth present, no 🏠);
`/menu` → 🔬 Research → type a subject (confirm 🏠 also present); tap 🔍 More
depth (confirm a new, deeper message arrives with fresh depth/breadth
buttons and the same 🏠 presence as the first message); restart the bot and
tap a pre-restart depth button (confirm the expiry message, not a crash).
