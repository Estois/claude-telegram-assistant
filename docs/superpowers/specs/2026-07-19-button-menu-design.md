# Design — Inline button menu for commands

**Date:** 2026-07-19
**Status:** Approved (brainstorm), pending implementation plan

## Problem / goal

The bot's features are reached almost entirely through slash commands (`/news`,
`/addinterests`, `/summarize`, etc.). Discoverability and one-handed use are
poor: users must remember command names and argument syntax. The goal is to add
an **inline button menu** as an alternative entry point to the common actions,
without removing the slash commands, and without regressing the bot's security
properties (fail-closed allowlist, anti-enumeration, Claude tool-sandboxing).

`/bus` already demonstrates the target pattern (inline keyboards + callback
handlers + a Back button). This design generalizes that pattern one level up
into a top-level menu, and adds submenus for Interests and Admin.

## Non-goals (YAGNI)

- **Persistent reply keyboard.** Rejected: its taps send *text messages* that
  would have to be routed through `handle_plain_text`, colliding with the
  password-unlock and awaiting-capture paths. Inline keyboards keep navigation
  on the callback path instead.
- **Guided `adduser` flow.** `/adduser <name> <sha256hash>` needs two fields
  incl. a hash; it stays slash-only.
- **Telegram native "Menu Button" configuration.** Not needed; `/menu` +
  welcome message + Back buttons cover access.
- **Any change to non-menu behavior** (news content, bus logic, auth, storage).
- **Removing or changing slash commands.** All keep working unchanged.

## Decisions (settled during brainstorm)

1. **Inline keyboard**, not persistent reply keyboard. Consistent with `/bus`;
   keeps menu taps off the text-input path.
2. **Access:** a new `/menu` command + the post-unlock welcome message both
   render the top-level board. **Back / Back-to-menu** buttons appear in
   submenus and item views (the `/bus` Back pattern generalized). The board is
   NOT auto-appended to every reply.
3. **Unified awaiting slot:** a single `context.user_data["awaiting"]` replaces
   per-feature booleans (see below).
4. **End session** shows a confirm step before re-locking.
5. **Remove interest** shows a confirm step before deleting.
6. **`/help`** stays slash-only (not a top-level button).
7. **Admin submenu (middle option):** `getusers` = one-tap; `removeuser` =
   pick-account button + confirm; `adduserteleid` = awaiting-text; `adduser`
   stays slash-only.
8. **Code organization (Approach B):** pure keyboard/markup builders go in a new
   `ykbot/menus.py`; async handlers stay as `YKBot` methods in `ykbot/app.py`
   alongside the bus handlers they resemble. No mixins (per the prior refactor
   decision to keep the stateful orchestrator cohesive).

## Architecture

### New module: `ykbot/menus.py` (pure)

Functions take plain data, return `telegram.InlineKeyboardMarkup`, hold no
`self` and touch no stores — independently unit-testable. Mirrors how
`_build_arrivals_keyboard` already isolates bus markup.

- `top_level_keyboard(is_admin: bool)` → Bus / News / Interests / Summarize /
  End session; an Admin row is included only when `is_admin` is true.
- `interests_keyboard()` → List / Add / Update / Remove / Compile / ⬅️ Back.
- `admin_keyboard()` → Get users / Remove user / Allow Telegram id / ⬅️ Back.
  (No "add user" — slash-only.)
- `interest_pick_keyboard(entries, action)` → one button per interest,
  `callback_data=f"int_{action}:{index}"` where `action ∈ {"rm", "upd"}`, + Back.
- `account_pick_keyboard(accounts)` → one button per account (admin remove), + Back.
- `confirm_keyboard(token)` → ✅ Yes / ❌ No, reused for end-session,
  remove-interest, and remove-user.

### `callback_data` namespace

Extends the existing `bus_*` scheme. All values stay within Telegram's 64-byte
`callback_data` limit (indices and short account names only).

| Pattern | Meaning |
|---|---|
| `menu:root`, `menu:interests`, `menu:admin` | navigation to a (sub)menu |
| `act:news`, `act:summarize`, `act:endsession` | top-level actions |
| `act:addinterest`, `act:compile`, `act:getinterests` | interest actions |
| `act:getusers`, `act:adduserteleid` | admin actions |
| `int_rm:<i>`, `int_upd:<i>` | select interest i to remove / update |
| `adm_rm:<name>` | select account to remove (admin) |
| `cfm:<token>`, `cxl:<token>` | confirm / cancel; token encodes the pending action (`endsession`, `int_rm:3`, `adm_rm:bob`) |

New `CallbackQueryHandler` registrations in `_build_application`, one per
pattern group, following the existing bus registrations.

### Navigation model

Mostly **stateless**: every menu, submenu, and pick view is rebuilt from the
`callback_data` plus a fresh store read, so views survive a bot restart. Back
buttons carry an explicit target (`menu:root` / `menu:interests`) rather than
relying on remembered history. The only stateful piece is the awaiting slot.

### Entry points

- New `/menu` command → `handle_menu` renders `top_level_keyboard(is_admin)`.
- Post-unlock welcome message also renders the top-level board.
- `is_admin` is derived from the same check `_require_admin` uses (admin account
  id + admin Telegram user id).

## Unified awaiting slot (text capture)

Replace the single `context.user_data["awaiting_bus_code"]` boolean with:

```python
context.user_data["awaiting"] = {"kind": <str>, "payload": <dict>}
```

Kinds: `bus_code` (migrated), `news_subject`, `summarize_url`, `add_interest`,
`update_interest` (payload `{"index": i}`).

Rules:
- A button that needs text sends a prompt (e.g. "Send the subject for your news
  digest:") and sets the slot.
- Setting a new capture **overwrites** any prior one (starting News cancels a
  half-finished Summarize) — free with a single slot.
- **One dispatch point** near the top of `handle_plain_text`, guarded to fire
  **only when the session is unlocked**. If locked, the text falls through
  untouched to the password-unlock path, remaining indistinguishable from a
  wrong password (preserves anti-enumeration). The slot is cleared after a
  capture fires.

## Handler refactor (share logic between slash + button)

Each affected handler splits into a thin argument-parser (slash) and a shared
core called by both the slash handler and the capture/confirm flow:

| Core | Called by |
|---|---|
| `_do_news(update, account_id, subject)` | `handle_news` (args) + `news_subject` capture |
| `_do_summarize(update, url)` | `handle_summarize` (args) + `summarize_url` capture |
| `_do_add_interest(update, account_id, text)` | `handle_add_interests` (args) + `add_interest` capture |
| `_do_update_interest(update, account_id, index, text)` | `handle_update_interests` (args) + `update_interest` capture |
| `_do_remove_interest(update, account_id, index)` | `handle_remove_interest` (args) + `cfm:int_rm:<i>` confirm |

No-arg actions (`getinterests`, `compileinterests`, `getusers`) reuse their
existing handlers directly from the button callback.

## Security invariants (must hold — all mirror existing patterns)

- Every new callback handler calls `_require_session` first (admin ones
  `_require_admin`) and `query.answer()`, exactly like the bus callbacks.
- **Button visibility ≠ authorization.** The Admin row is hidden for
  non-admins, but `menu:admin` and every `adm_*` / admin `act:*` callback still
  re-checks `_require_admin`. A hand-crafted admin callback from a non-admin is
  rejected.
- A locked user tapping a stale inline button gets the same non-committal
  handling as today — no command-enumeration leak.
- Fail-closed allowlist still gates at the message level; unchanged.
- Claude tool-sandboxing (`--tools`) is untouched; `/news` and `/summarize`
  still inject interests/URL from Python.

## Testing (hold 100% line coverage)

- `tests/test_menus.py` — pure keyboard builders: admin vs non-admin rows,
  per-interest / per-account buttons, confirm markup, and a `callback_data`
  ≤ 64-byte assertion.
- Callback/handler tests using the existing `FakeCallbackQuery`
  (`tests/helpers.py`): menu navigation, each `act:*`, confirm/cancel flows for
  end-session / remove-interest / remove-user, and the awaiting-slot dispatch:
  - unlocked → routes text to the correct capture and clears the slot;
  - **locked → falls through to password unlock** (critical security test);
  - setting a new capture overwrites a prior one.
- Migrate the existing `awaiting_bus_code` tests to the unified slot.
- Reuse `tests/helpers.py` doubles (`FakeUpdate`/`FakeMessage`/
  `FakeCallbackQuery`/`FakeBot`/`FakeContext`, `make_config`,
  `write_accounts_file`); do not re-roll fakes.

## Deployment

Code change (`*.py`) → follow the STANDING RUNBOOK in `.claude/HANDOFF.md`
(verify while old bot runs → /proc-scan kill + `setsid nohup python3 bot.py`
→ confirm one healthy instance). Keep the suite green at 100% and pyflakes
clean before the swap.
