# Non-blocking store reads, and decomposing YKBot

Addresses findings 4 and 5 of `docs/code-quality-review-2026-07-20.md`, the two
items left open when the other eight shipped.

Two independent pieces, done in this order. Piece 1 is worth doing on its own and
does not depend on Piece 2. Piece 2 depends on Piece 1 only for convenience — the
call sites it moves are easier to move once they are already correct.

---

## Piece 1 — Take blocking store reads off the event loop

### Problem

Every store does synchronous `read_text` / `write_text`, called from `async def`
handlers. Each call blocks the event loop for the duration of the disk operation,
stalling every other in-flight update.

At current scale this costs approximately nothing: the files are kilobytes, the
disk is local, and the bot serves a handful of users on a ~10s poll. This is a
latent issue, fixed now because the call sites are all in one file and Piece 2 is
about to scatter them across several.

### What is NOT the problem

`_is_authorized` looks like a hot-path I/O site but is not: `AllowlistStore` holds
its ids in a `set` in memory (`accounts.py`), so `is_allowed` is a set lookup. The
allowlist is checked on every single update — leave it alone.

`_warn_about_missing_config` does read from disk, but runs from `run()` at startup
before the event loop exists. Leave it alone.

### The complication

Five helpers do disk I/O while being **synchronous**:

| Helper | Reads | Call sites |
|---|---|---|
| `_recent_theme_counts` | `news_history.recent()` | 2 |
| `_build_news_prompt` | `interests.list()` | 1 |
| `_format_user_listing` | `accounts.list_ids()` | 2 |
| `_build_fav_menu` | `bus_locations.list()` | 3 |
| `_build_arrivals_keyboard` | `bus_locations.list()` | 5 |

A sync function cannot `await asyncio.to_thread(...)`. The obvious fix — make them
`async` — cascades: their callers must become async, and so on. That colours a
large part of the file for no benefit.

### Approach: hoist the read, keep the builder pure

Make these five take their data as a parameter instead of fetching it. The async
caller does the fetch off-thread and passes the result in.

```python
# before
def _build_fav_menu(self, account_id: str) -> tuple[str, InlineKeyboardMarkup]:
    locations = self._bus_locations_for(account_id).list()   # blocking
    ...

# after
locations = await asyncio.to_thread(self._bus_locations_for(account_id).list)
text, markup = self._build_fav_menu(locations)
```

This is **the convention this codebase already uses**. Every builder in
`ykbot/menus.py` takes plain data and returns markup — no `self`, no stores. And
`_build_near_menu` already takes `stops` as a parameter; `_build_fav_menu` is the
one that breaks the pattern. So this makes the file more consistent, not less.

It also directly serves Piece 2: a pure builder moves to another module without
dragging dependencies behind it.

### Scope

1. Convert the five helpers above to take data. 13 call sites.
2. Wrap the remaining direct store calls inside `async def` bodies with
   `asyncio.to_thread`. This includes writes (`add`, `remove`, `update`,
   `replace_all`, `append`, `prune`), not only reads.
3. Leave `_is_authorized` and `_warn_about_missing_config` untouched, for the
   reasons above.

### Explicitly not doing

- **Not making the store classes async.** They are sync, testable, and called
  from sync contexts (including tests) too. The thread hop belongs at the
  boundary, not inside the store.
- **Not caching file contents in memory.** Reading per request is deliberate — it
  is what lets a hand-edit of `interests.md` / `accounts.hash` / `.env` take
  effect without a restart, a property the standing runbook's Phase 0 depends on.

---

## Piece 2 — Decompose YKBot, starting with bus

### Problem

`ykbot/app.py` is ~1600 lines and `YKBot` carries 99 methods. The 2026-07-18
refactor correctly extracted the 13 stateless service classes but deliberately
left the orchestrator whole to avoid mixins. That call was right about mixins and
has been overtaken by growth: `app.py` is now over half the package, and every new
feature lands in it.

This buys maintainability, not correctness. Nothing is broken.

### Approach: a shared services object, not a back-reference

Extracted handler classes need `require_session`, `reply_result`, `run_claude`,
`send_formatted`, `safe_edit`, `is_admin`, the stores, and config.

**Rejected — passing the `YKBot` instance.** Handlers would call back into the bot
(`self._bot._require_session(...)`). This yields smaller files and nothing else:
the coupling is unchanged, merely spread across a file boundary where it is harder
to see, plus a circular reference. Not worth the churn.

**Rejected — explicit callables per handler.** The sharpest boundary, but
constructor signatures grow unwieldy and every new shared helper means editing
every handler class.

**Chosen — `BotServices`.** One object holding what handlers share. Each handler
class takes it in `__init__`. The dependency direction becomes one-way, and a
handler class can be constructed against a stub.

```python
class BusHandlers:
    def __init__(self, services: BotServices):
        self._svc = services

    async def handle_bus(self, update, context):
        account_id = await self._svc.require_session(update)
        ...
```

`BotServices` holds: `config`; the singleton stores (`accounts`, `allowlist`,
`claude`, `trivia`, `lta`, `stops_cache`, `message_log`); the per-account store
factories (`interests_for`, `bus_locations_for`, `news_history_for`); and the
shared async helpers (`require_session`, `require_admin`, `is_admin`,
`reply_result`, `send_formatted`, `run_claude`, `safe_edit`).

### Why bus first

Bus is the largest and most self-contained cluster: ~20 methods, ~275 lines, its
own two stores (`LtaBusClient`, `BusStopsCache`, `BusLocationsStore`), and almost
no overlap with other domains. It is the best pilot — if the services boundary is
wrong, bus reveals it.

Methods moving to `ykbot/handlers/bus.py`: `handle_bus`, `_open_bus_menu`,
`_build_fav_menu`, `_build_near_menu`, `handle_bus_request_location`,
`handle_bus_callback`, `_render_arrivals`, `handle_bus_refresh`,
`handle_bus_back`, `handle_bus_fav_add`, `handle_bus_fav_remove`,
`handle_bus_add_by_code`, `_prompt_add_bus_stop`, `_build_add_confirm_keyboard`,
`handle_bus_confirm_add`, `handle_bus_cancel_add`, `_describe_stop`,
`_build_arrivals_keyboard`, `handle_location`, `_format_arrivals`.

### The two seams that stay central

- **`_consume_awaiting`** dispatches text-capture completions across every domain.
  It stays on `YKBot` and its `bus_code` branch delegates:
  `await self._bus.prompt_add_bus_stop(update, context, account_id, text)`.
  `_prompt_add_bus_stop` loses its underscore since it is now called across a
  module boundary.
- **`_build_command_specs`** is the single source of truth for command
  registration and `/help`. It stays on `YKBot` and references
  `self._bus.handle_bus`.

Handler registration in `_build_application` likewise points at `self._bus.*`.

### Deliberately deferred

Interests, admin, news/summarize and menu navigation are **not** extracted in this
work. Do bus, live with it, then decide. Committing to all five upfront risks
discovering the boundary is wrong at the fourth one.

### After this piece

`app.py` drops roughly 275 lines. `YKBot` keeps construction, registration,
command specs, auth, the shared reply/claude helpers, the text-capture dispatcher,
the plain-text/password path, and the background reorganize loop.

---

## Testing

Both pieces are pure refactors: **no user-visible behavior changes.** The existing
457 tests are the specification, and they must stay green at 100% coverage
throughout.

- **Piece 1:** existing tests should pass with no edits. A test that needs
  changing means behavior changed — stop and investigate rather than editing the
  test. Add coverage for any builder whose signature changed, asserting it is
  callable with plain data and touches no store.
- **Piece 2:** bus tests currently reach for `bot.YKBot` internals. Retarget them
  at `BusHandlers` where the method moved. `tests/test_bus_navigation.py`,
  `tests/test_bus_domain.py`, `tests/test_unlock_bus_arrivals.py` and the bus
  cases in `tests/test_locked_paths.py` and `tests/test_menu_home_screens.py` are
  affected.
- **Callback safety:** every callback-reachable handler that moves must keep
  replying via `update.effective_message`. Use `FakeUpdate(callback_only=True)`
  for the moved handlers — the default double aliases `effective_message` to
  `message` and cannot catch a violation.
- Verify with `python3 -m unittest discover -s tests -t .`,
  `python3 -m pyflakes bot.py ykbot/`, and
  `python3 -m coverage run -m unittest discover -s tests -t . && python3 -m coverage report`
  (the coverage source list and `fail_under = 100` now live in `pyproject.toml`).

## Deployment

Both pieces are code changes, so each ships through the STANDING RUNBOOK at the
top of `.claude/HANDOFF.md`. Note the runbook's launch line changed on
2026-07-20: redirect to `bot-startup.log`, and tail `bot.log` (rotated in-process)
to verify.

Because these are refactors with no visible behavior change, the live smoke test
is a regression check: unlock, `/bus` with a saved favourite, tap through to
arrivals, 🔄 Refresh, ⬅️ Back, and send a location.
