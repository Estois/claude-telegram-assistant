# Passive interest learning from /news — Design

**Date:** 2026-07-18
**Status:** Approved (design), pending implementation plan

## Goal

Turn each `/news <subject>` request into a durable learning signal so that a
per-account interests list refines itself over time from what the user actually
asks about — automatically, silently, and without adding latency or Claude cost
to the `/news` hot path.

Today the subject a user types into `/news` is used once (to bias that one
digest) and then discarded. This feature captures it and feeds recurring
request themes back into `accounts/<id>/interests.md`.

## Scope

- **Per-account.** Each account learns only from its own `/news` requests, into
  its own `interests.md`. Nothing global or cross-account.
- **In scope:** capturing request subjects; a background pass that promotes
  recurring/significant themes into interests; config switches; graceful
  degradation; tests.
- **Out of scope (YAGNI):** interest decay/removal, interest weighting,
  analyzing digest *content* (v1 signal is the typed subject only), inline
  per-request interest suggestions, any user-facing confirmation UI.

## Architecture

Two small pieces bolt onto machinery that already exists (the per-account
daily interests-reorganize loop in `ykbot/app.py`):

### 1. Capture — the only change to the `/news` hot path

When a valid `/news <subject>` request is handled, append a record to a
per-account append-only log `accounts/<id>/news_history.jsonl`. This is a local
file append — **no Claude call, negligible latency.**

A new `NewsHistoryStore` class (in a new `ykbot/news_history.py`) owns this
file, mirroring the shape of the other per-account stores (`InterestsStore`,
`BusLocationsStore`):

- `append(subject: str) -> None` — appends one JSON line `{"ts": <unix float>,
  "subject": "<text>"}`.
- `recent(window_days: int, max_entries: int) -> list[dict]` — returns the
  records within the rolling window (see Retention), newest-first-bounded.
- `theme_counts(window_days, max_entries) -> list[tuple[str, int]]` — the recent
  subjects normalized (lowercased, whitespace-collapsed) and aggregated into
  `(subject, count)` pairs, most frequent first. Used to compute recurrence.
- `prune(window_days, max_entries) -> None` — rewrites the file keeping only the
  in-window records, bounding file growth.

### 2. Learn — extend the existing daily pass

`YKBot._reorganize_account(account_id)` already makes exactly one Claude call
per account per day to merge/enrich interests. It gets extended to *also* read
the recent request history and include it — with frequency counts — in that
**same** Claude call. **One Claude call per account per day, as today: no new
Claude cost.**

The reorganize prompt is replaced by a combined prompt that receives:
1. the current numbered interests list, and
2. the recent `/news` request themes with their counts,

and is instructed to fold recurring/significant themes into the list, then merge
and enrich as the reorganize step already does.

## Data flow

```
/news cybersecurity ─▶ NewsHistoryStore.append("cybersecurity")   (hot path, no Claude)
        … accumulates over days …
daily pass, per account:
   interests.md  +  recent news_history theme counts
        ─▶ one Claude prompt ─▶ updated interests.md  ─▶ prune news_history
```

## Promotion policy (the "iterative" part)

The combined prompt instructs Claude to:

- Promote a requested theme into an interest **only if it recurs** (appears
  multiple times across the window, aggregated across similar phrasings) **or is
  clearly significant** — never a single one-off request.
- **Never drop or override the user's existing interests** (which may have been
  added manually via `/addinterests`). The operation is additive + merge only.
- Continue the existing reorganize behavior: merge duplicates, enrich entries
  with retrieval-helpful detail, renumber from 1.

Recurrence is grounded in real counts we compute (`theme_counts`) and pass into
the prompt, rather than relying on Claude to remember frequency.

## Retention / window

The daily pass considers a rolling window: **the smaller of the last
`NEWS_HISTORY_WINDOW_DAYS` (default 60) days or `NEWS_HISTORY_MAX_ENTRIES`
(default 100) records.** After each pass, `news_history.jsonl` is pruned to that
window so it cannot grow unbounded.

## Configuration (new, all with safe defaults)

Added to `Config` / `Config.from_env()`:

- `news_learning_enabled: bool` — env `NEWS_LEARNING_ENABLED` (default `true`).
  Master switch. When false: `/news` still works but neither logs nor learns
  (capture is also gated on it), and the daily pass behaves exactly as today.
- `news_history_window_days: int` — env `NEWS_HISTORY_WINDOW_DAYS` (default 60).
- `news_history_max_entries: int` — env `NEWS_HISTORY_MAX_ENTRIES` (default 100).

Reuses the existing `interests_reorganize_interval_seconds` scheduler — no new
background loop.

## Visibility & privacy

Silent, matching the existing reorganize: the result is logged, never broadcast
(broadcasting one account's learned interests to all allowlisted users would
leak that account's data — the reason the reorganize broadcast was removed in a
prior review). The user sees the effect on their next `/getinterests`. The
`news_history.jsonl` file is itself the audit trail of what fed the learning.

No new owner notification in v1: the background job runs without a chat context,
so there is no reliable owner chat to DM.

## Error handling

All new I/O degrades gracefully, matching the sibling stores:

- Missing/corrupt `news_history.jsonl` → treated as empty, `logger.exception`
  on corruption (same pattern as `ClaudeSessionStore`, `BusStopsCache`,
  `BusLocationsStore`). Malformed individual JSON lines are skipped, not fatal.
- A failed or unparseable Claude reply in the daily pass leaves `interests.md`
  untouched (the existing guard in `_reorganize_account`), and history is still
  pruned so a transient failure doesn't wedge the window.
- A logging failure inside `/news` must never break the digest reply — capture
  is wrapped so the user still gets their news even if the append fails.

## Testing

- **`NewsHistoryStore`** unit tests: append then read; window-by-days pruning;
  max-entries bounding; corrupt/partial-line tolerance; `theme_counts`
  normalization + aggregation ordering.
- **Combined prompt builder** tested as a pure function: given interests +
  theme counts, asserts the prompt contains both and the recurrence/additive
  instructions.
- **Extended `_reorganize_account`** tested with a fake Claude (existing test
  doubles): a fixed history + interests → asserts recurring themes are merged
  in, one-offs are *not* promoted, pre-existing manual interests are preserved,
  and history is pruned afterward. A `news_learning_enabled=false` case asserts
  today's behavior is unchanged and nothing is logged.

## Impact on existing code

- `ykbot/news_history.py` — new module (`NewsHistoryStore`).
- `ykbot/app.py` — `handle_news` gains the capture call; `_reorganize_account`
  reads history + uses the combined prompt + prunes; a new
  `_news_history_for(account_id)` helper (mirrors `_interests_for`); the
  reorganize prompt builder is replaced/extended.
- `ykbot/config.py` — three new fields + env reads.
- `ykbot/__init__.py` + `bot.py` — re-export `NewsHistoryStore`.
- `README.md` — document the learning behavior and the new env vars.
- Tests — new `tests/test_news_history_store.py`, extensions to the reorganize
  test.
