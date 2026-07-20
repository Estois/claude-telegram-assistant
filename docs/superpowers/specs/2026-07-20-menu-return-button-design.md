# Return-to-menu button on result messages

## Problem

Result messages are dead ends. After a `/news` digest, an interests listing, or a
compile summary, the only way back to the button board is to type `/menu`.

The obvious fix — reuse the existing `menu:root` callback — does not work.
`handle_menu_nav` navigates with `query.edit_message_text`, which *replaces* the
message the button is attached to. Putting that on a digest would destroy the
digest to show the menu. The whole point is to keep the result on screen.

## Scope

A 🏠 Menu button is attached to **button-initiated results only**. A result you
got by typing a slash command stays bare — if you are typing commands you do not
need a button to reach the menu.

Bus arrivals screens are explicitly **out of scope**: they already carry
🔄 Refresh / ⬅️ Back / ⭐ favourite, and a third nav row crowds them.

## Design

### Detecting "button-initiated"

Not by sniffing `update.callback_query`. Four of the six menu flows —
news, summarize, add-interest, update-interest — arm a text-capture slot
(`context.user_data["awaiting"]`) and produce their result from a *typed reply*
handled by `_consume_awaiting` (app.py:1251). At reply time there is no callback
query, so sniffing would silently drop the button from precisely the slowest,
most valuable results.

Instead every `_do_*` core gains an explicit final parameter:

```python
async def _do_show_interests(self, update, account_id, from_menu: bool = False) -> None:
```

Callers set it:

| Caller | Passes |
|---|---|
| Slash handlers (`handle_news`, `handle_get_interests`, …) | default `False` |
| `handle_menu_action` (`getinterests`, `compile`) | `True` |
| `handle_confirm` (`int_rm:`, `adm_rm:`) | `True` |
| `_consume_awaiting` (every branch except `bus_code`) | `True` |

`handle_interest_select` and `handle_admin_action` are not in the table: neither
calls a `_do_*` core. They render a confirm screen or arm a capture slot, and the
result arrives later through `handle_confirm` or `_consume_awaiting`.

Defaulting to `False` means no slash handler changes.

### The sender

One helper owns the decision:

```python
async def _reply_result(self, update, text, from_menu, **kwargs) -> None:
    markup = menus.home_keyboard() if from_menu else None
    await update.effective_message.reply_text(text, reply_markup=markup, **kwargs)
```

Every reply site inside a `_do_*` core routes through it — including the error
and empty-state branches ("No interests saved yet.", "No interest #4.",
"Reorganize returned nothing usable"), so a failed button action is no more of a
dead end than a successful one.

`_send_formatted` gains the same `from_menu` parameter and attaches the keyboard
**to the last chunk only**. A three-chunk digest gets one button, not three.
The HTML-then-plain-text fallback keeps the markup on both paths.

### The keyboard and its handler

```python
def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="home")]])
```

New callback pattern `^home$` → `handle_menu_home`, registered alongside the
other menu handlers. It gates on `_require_session`, answers the query, then
**posts a fresh menu message** via `update.effective_message.reply_text` — it
never edits. That is the entire difference from `menu:root`, and the reason it
needs its own callback data rather than a flag on the existing one.

### Bus menu

`_build_fav_menu` and `_build_near_menu` append a `[🏠 Menu]` row
**unconditionally** — present whether you arrived via `/bus` or via the Bus
button. These are menu screens, not results; a nav button that appears or
vanishes depending on how you reached the same screen would be incoherent.

Arrivals screens (`_build_arrivals_keyboard`) are untouched.

### Admin "Get users"

`handle_admin_action` renders the user listing with `query.edit_message_text`
(app.py:940), replacing the admin menu and leaving **no buttons at all** — a
worse dead end than the result-message case, since the nav it consumed is gone.
It gains `reply_markup=menus.admin_keyboard()` so the admin menu returns with the
listing. This is an addition beyond the stated scope, called out here for review.

## Deliberate non-goals

- **Stale buttons stay tappable.** An old digest keeps its 🏠 indefinitely.
  Stripping it would mean editing that old message — the exact destruction this
  feature exists to avoid. Menu nav is stateless and `_require_session`-gated, so
  a tap on a week-old button is safe; it just posts a current menu.
- **Existing ⬅️ Back nav is unchanged.** It still edits in place. Menu screens are
  disposable; results are not.
- **No new persisted state.** `from_menu` is a call parameter, nothing is stored.

## Security

`handle_menu_home` re-checks `_require_session` before rendering, like every
other callback handler. A locked user tapping a stale 🏠 gets trivia — no
enumeration leak, consistent with the rest of the menu.

The menu it renders is rebuilt through `_menu_markup`, which calls `_is_admin`
at render time. A user who tapped 🏠 on a message from an admin session that has
since ended gets a non-admin board. Button presence is never authorization —
every admin action re-checks `_require_admin` regardless.

## Testing

Suite must stay green at 100% line coverage
(`python3 -m unittest discover -s tests -t .`), reusing the doubles in
`tests/helpers.py`.

- `home_keyboard()` shape; the 🏠 row on both bus menu builders.
- `_reply_result` attaches markup when `from_menu=True`, omits it otherwise.
- `_send_formatted` with multi-chunk text: markup on the final chunk only, on
  both the HTML and the plain-text fallback path.
- Each `_do_*` core: markup present via the menu path, absent via the slash path.
- `_consume_awaiting` passes `from_menu=True` for every capture kind.
- `handle_menu_home`: posts a new message rather than editing; locked user gets
  trivia; admin row reflects current session state, not the state at send time.

**Known limitation:** `FakeUpdate` aliases `effective_message` → `message` (both
non-None), so unit tests cannot prove callback-safety. `handle_menu_home` and
every touched reply site must use `update.effective_message`, verified by
inspection and a live restart, per the standing runbook.

## Deployment

A code change: full runbook (verify while the old bot runs → /proc-scan kill →
`setsid nohup python3 bot.py >> bot.log 2>&1 < /dev/null &` → confirm one healthy
instance). Live smoke test: tap News from the menu, type a subject, confirm the
digest arrives with 🏠 attached, tap it, confirm the digest is still on screen
with a fresh menu below it.
