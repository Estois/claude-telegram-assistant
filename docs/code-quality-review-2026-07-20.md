# Code quality review — 2026-07-20

> **STATUS — updated 2026-07-20, same day.** Findings 1, 2, 3, 5, 6, 7, 8, 9 and
> 10 are **DONE** (commits `18f88a3`, `12b758b`, the CI commit, and the
> `app-decomposition` branch's Piece 1, which wrapped every store call site in
> `asyncio.to_thread`). Suite went 443 → 473 tests, still 100% coverage.
>
> **Finding 4 is PARTLY DONE.** The `app-decomposition` branch's Piece 2
> extracted the bus domain into `ykbot/handlers/bus.py`, taking it behind a new
> `BotServices` bundle (`ykbot/services.py`). The interests, admin, news, and
> menu domains still live in `app.py` — see the per-finding note below for what
> remains.
>
> **One deliberate omission:** the exposed token has **not been rotated**. The
> user was told and chose to defer it. The *logging* leak is fixed, but the
> credential sitting in the existing `bot.log` is still live. Raise it
> periodically (tracked in the `telegram-bot-token-rotation-pending` memory).

Whole-codebase review of `bot.py` + `ykbot/` (2913 lines) against general
Python/asyncio/python-telegram-bot practice. Written for a Claude session to act
on directly: each finding names exact files and lines, states why it matters, and
gives the concrete fix.

Reviewed at `main` @ `71fb46a`. Suite: 443 tests, 100% line coverage, pyflakes
clean.

**Work top-down.** Findings 1 and 2 are worth doing on their own; everything
below 4 is optional polish that should not be started without asking the user
first, because some of it (finding 4 especially) is a large diff for a codebase
that currently works.

---

## Read this before changing anything

Several things that look like defects in this codebase are deliberate, load-bearing
decisions with real reasoning behind them. **Do not "fix" these:**

- **`except Exception` in six places** (`app.py:650, 1292, 1381, 1482, 1549, 1558`).
  Every one logs via `logger.exception` and guards a genuine I/O boundary —
  history logging must not break a digest, an LTA outage must not break `/bus`, a
  failed password-message deletion must not break unlocking. These are correct
  defensive boundaries, not swallowed errors.
- **`ClaudeClient` runs the Claude *CLI*, never the paid Anthropic API**
  (`ykbot/claude.py`). This was explicitly chosen on cost grounds and rejected
  once already. Do not reintroduce `ANTHROPIC_API_KEY` or the `anthropic` SDK.
- **`--tools` restriction in `ClaudeClient._build_command`** is a security
  boundary against prompt-injection→host-access. Do not loosen it.
- **`YKBot` was deliberately NOT split with mixins** during the 2026-07-18
  refactor — invisible shared-`self` coupling plus Pylance noise. Finding 4 below
  proposes composition instead, which is a different thing.
- **The default `FakeUpdate` aliases `effective_message` to `message`.** Changing
  that default would break the suite by design; the opt-in
  `FakeUpdate(callback_only=True)` is the correct mechanism.
- **Stores read from disk per request** so hand-edits to data files apply without
  a restart. Finding 5 is about *how* that read happens, not about caching it away.

---

## 1. ~~HIGH~~ DONE (logging) / OPEN (rotation) — bot token written to `bot.log`

**Where:** `ykbot/app.py:39-41` (logging config), effect visible in `bot.log`.

`httpx` logs every request at INFO level including the full URL, and the
python-telegram-bot API URL embeds the bot token in its path. The result, on
every ~10s poll:

```
2026-07-20 09:26:22,660 - httpx - INFO - HTTP Request: POST
https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/1.1 200 OK"
```

`bot.log` is 18,066 lines and effectively every line contains the live token.

**Why it matters:** the token is full control of the bot — read every message,
impersonate it, revoke nothing. `bot.log` is gitignored (`.gitignore:10`, `*.log`),
so it is not in git history, but it sits in plaintext on the host and in any
Docker bind-mount, and it leaks the moment a log excerpt is pasted into a chat,
an issue, or a support request. Logging credentials is a standard finding in any
security review.

**Fix** — in `ykbot/app.py`, alongside the logging setup:

```python
# httpx logs every request URL at INFO, and the Telegram API URL contains the
# bot token in its path — so INFO-level httpx logging writes the token to
# bot.log on every poll. WARNING keeps real failures without the credential.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
```

**Then rotate the credential**, because the current token is already sitting in
18k lines of log: talk to `@BotFather` → `/revoke` → put the new token in `.env`
→ restart per the standing runbook. Ask the user before doing this — it is their
bot and revoking briefly breaks it.

Optionally scrub the existing log: `sed -i 's|/bot[0-9]\{8,10\}:[A-Za-z0-9_-]\{30,\}/|/bot<REDACTED>/|g' bot.log`.

---

## 2. ~~HIGH~~ DONE — No error handler is registered

**Where:** `ykbot/app.py:227-274` (`_build_application`). `grep -rn "add_error_handler"`
returns nothing.

python-telegram-bot dispatches uncaught handler exceptions to a registered error
handler. With none registered, PTB logs `No error handlers are registered` and
the user's button or command simply does nothing — no message, no feedback.

**This has already bitten this project.** The 2026-07-20 whole-branch review found
that re-tapping 👥 Get users raised an unhandled `BadRequest: message is not
modified` that surfaced to nobody. `_safe_edit` fixed that specific case; the
general gap remains, and the next unhandled exception will be just as silent.

**Why it matters:** it is the difference between a bug you learn about and a bug
that quietly degrades the product. It is also the standard PTB idiom — its absence
is the first thing a PTB-literate reviewer looks for.

**Fix** — add to `YKBot` and register it last in `_build_application`, just before
`return app` (`app.py:274`):

```python
    async def handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Last-resort net for exceptions no handler caught. Logs the full
        traceback, then tells the user something went wrong — a button that
        silently does nothing is worse than an error message."""
        logger.exception("Unhandled exception while processing update", exc_info=context.error)
        message = getattr(update, "effective_message", None)
        if message is None:
            return
        try:
            await message.reply_text("Something went wrong on my end. Try again in a moment.")
        except Exception:
            logger.exception("Failed to deliver the error notice")
```

```python
        app.add_error_handler(self.handle_error)
```

**Test it** with `FakeUpdate(callback_only=True, ...)` — cover the happy path, the
`update` with no `effective_message`, and the reply-also-fails path, so coverage
stays at 100%.

**Watch the trivia rule:** the user-facing string must not leak whether a command
exists to a locked user. `handle_error` only ever fires *after* a handler was
already entered, and every handler gates on `_require_session` first, so a generic
message here does not create an enumeration path. Keep it generic anyway.

---

## 3. ~~MEDIUM~~ DONE — `logging.basicConfig` ran at import time

**Where:** `ykbot/app.py:39-41`, executed at module import.

```python
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
```

Configuring the *root* logger as a side effect of importing a module is a
long-standing Python anti-pattern. `import bot` — which the entire test suite does
— reconfigures global logging for the importing process. Libraries should attach a
handler to their own logger and let the application decide global policy.

**Why it matters:** it makes logging behaviour depend on import order, and it means
the test suite runs with the bot's logging config whether it wants it or not.
Low practical impact here (single app, single entry point), which is why this is
Medium and not High.

**Fix:** move the `basicConfig` call (plus the httpx silencing from finding 1) into
a `_configure_logging()` function called from `main()` (`app.py:1603`), which is
the real entry point. Keep `logger = logging.getLogger("ykbot")` in
`ykbot/_logging.py` exactly as it is.

Verify afterwards that `python3 -m unittest discover -s tests -t .` output stays
pristine — if log lines start appearing in test output, add a `logging.disable`
or a `NullHandler` in the test bootstrap rather than reverting this.

---

## 4. MEDIUM — PARTLY DONE (bus extracted) — YKBot is a god object

**Where:** `ykbot/app.py` — 1605 lines, `class YKBot` carrying 99 methods.

The 2026-07-18 refactor correctly extracted the 13 stateless service classes into
`config/accounts/claude/interests/bus/trivia/formatting/menus/...`. The
orchestrator was deliberately left whole to avoid mixins. That decision was right
about mixins and has since been overtaken by growth: `app.py` is now 55% of the
package, and 99 methods on one class is past the point where a reader can hold it
in their head or a model can reliably edit it.

**Why it matters:** this is where every future feature lands, so it keeps growing.
Merge conflicts, review difficulty, and edit reliability all degrade with file size.

**Fix — composition, not mixins.** Group handlers into collaborator classes that
receive the services they need through `__init__`, and have `YKBot` hold and
delegate to them:

```python
class InterestHandlers:
    """Interest slash-commands and their button flows."""

    def __init__(self, interests_for, run_claude, reply_result, require_session):
        ...
```

Candidate split, roughly by existing domain: `handlers/interests.py`,
`handlers/bus.py`, `handlers/admin.py`, `handlers/news.py`, `handlers/menu.py`,
leaving `app.py` with construction, `_build_application`, `_build_command_specs`,
the shared reply helpers (`_reply_result`, `_send_formatted`, `_run_claude`), and
the background reorganize loop.

**This is a large, risky, low-reward diff on code that currently works and is at
100% coverage. Do not start it without the user's explicit agreement.** If it does
go ahead, do it as its own spec + plan under `docs/superpowers/`, one domain per
commit, with the suite green at every step — the same way the `bot.py`→`ykbot/`
refactor was done.

---

## 5. ~~MEDIUM~~ DONE — Blocking file I/O in async handlers

**Where:** every store. `accounts.py:207,239,278,281,288`, `bus.py:97,125,151`,
`interests.py:24,74`, `message_log.py:32`, `news_history.py:24,50,56`,
`trivia.py:229,238,251`.

All of these are synchronous `read_text` / `write_text` / `open()` calls, invoked
from `async def` handlers. Each one blocks the event loop for the duration of the
disk operation, stalling every other in-flight update.

**Why it matters in principle:** it defeats the point of an async framework. **Why
it barely matters today:** the files are small (kilobytes), the disk is local, and
the bot serves a handful of users with one poll every ~10s. The measurable cost is
approximately zero right now.

So treat this as a latent scaling issue, not a live bug. It becomes real if the
user base grows, if `trivia_cache.json` (1000 entries) or the message logs get
much larger, or if the data directory ever moves to network storage.

**Fix, when it's worth it:** wrap the store calls at the call site with
`asyncio.to_thread`, e.g. `entries = await asyncio.to_thread(self._interests_for(account_id).list)`.
Do **not** convert the stores themselves to async — they are sync, testable, and
used from sync contexts too; keeping the thread hop at the boundary preserves that.

**Do not "fix" this by caching file contents in memory.** Reading per request is
deliberate, so hand-edits to `interests.md` / `accounts.hash` / `.env` take effect
without a restart. That property is documented in the standing runbook's Phase 0
and is relied on.

---

## 6. ~~MEDIUM~~ PARTLY DONE — No CI, no linter, no formatter, no type checker

**Where:** repo root. No `.github/`, no `.pre-commit-config.yaml`, no ruff/flake8/
mypy config. `pyproject.toml` contains only `[tool.pytest.ini_options]`.

Quality currently depends on a human remembering to run
`python3 -m unittest discover -s tests -t .` and `python3 -m pyflakes bot.py ykbot/`.
That has held so far, but nothing enforces it — a push with a failing suite or a
coverage regression would go unnoticed.

**Fix — smallest useful version first.** Add `.github/workflows/ci.yml`:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements-dev.txt pyflakes coverage
      - run: python -m pyflakes bot.py ykbot/
      - run: python -m coverage run --source=ykbot,bot -m unittest discover -s tests -t .
      - run: python -m coverage report --fail-under=100
```

`--fail-under=100` turns the project's existing 100%-coverage norm into an
enforced invariant, which is the single highest-value line here.

Then, optionally and in this order: **ruff** (replaces pyflakes, adds import
sorting and a large lint set — expect a first-run diff), then **mypy** (this
codebase is partially annotated; expect meaningful work, see finding 9).

Adding a formatter (`ruff format` / `black`) would touch nearly every line at
once. Only do it if the user wants it, and as its own isolated commit so it never
mixes with logic changes.

---

## 7. ~~LOW~~ DONE — `bot.log` grew without bound

**Where:** the runbook's launch line, `python3 bot.py >> bot.log 2>&1`.

Already 18,066 lines with no rotation. Combined with finding 1, that is 18k lines
of credential.

**Fix:** either use `logging.handlers.RotatingFileHandler` inside
`_configure_logging()` (see finding 3) instead of shell redirection, or add a
`logrotate` entry. The handler approach keeps it in-process and portable to the
Docker runtime.

---

## 8. ~~LOW~~ DONE — Two unbounded in-memory dicts

**Where:** `ykbot/claude.py:23` (`self._locks: dict`, via `lock_for` →
`setdefault`) and `ykbot/accounts.py:90-91` (`self._sessions`, `self._failures`).

Each is keyed by chat id and never evicted, so all three grow monotonically with
the number of distinct chats the process has ever seen. `_sessions` entries are
expiry-checked on read but not removed; `_failures` prunes its inner list but
never drops the outer key.

**Why it's Low:** entries are tiny and the allowlist bounds the population to
explicitly approved users. This is a theoretical leak, not a practical one.

**Fix, if touched:** drop the key when a session expires in
`AccountStore.account_for`, `pop` the entry in `clear_failed_unlocks` (already
done) and when a pruned failure list is empty, and evict a chat's lock in
`ClaudeClient` once it is unlocked and uncontended. Only worth doing alongside
other work in those files.

---

## 9. ~~LOW~~ PARTLY DONE — Partial and imprecise type annotations

**Where:** throughout. Examples: `self._locks: dict` (`claude.py:23`) and
`self._sessions: dict[int, tuple]` (`accounts.py:90`) use bare or partial
generics; several signatures return bare `list` (`app.py:102 _build_command_specs`,
`interests.py`); `_build_fav_menu` annotates its return as the *string*
`"tuple[str, InlineKeyboardMarkup]"`.

Annotations are present and broadly honest, which is better than most code of this
size — they are just not precise enough for a type checker to do useful work.

**Fix:** tighten to `dict[int, asyncio.Lock]`, `dict[int, tuple[str, float]]`,
`list[CommandSpec]`, `tuple[str, InlineKeyboardMarkup]`. Add
`from __future__ import annotations` at the top of modules using string
annotations so they can be written unquoted. Worth doing as the enabling step for
mypy in finding 6, not on its own.

---

## 10. ~~LOW~~ DONE — `pyproject.toml` had no project metadata

**Where:** `pyproject.toml` — only `[tool.pytest.ini_options]`.

No `[project]` table, so name, version, description, dependencies and
`requires-python` live nowhere machine-readable. `requirements.txt` pins exact
versions (good), but there is no lockfile with hashes and no dependency-update
automation.

**Fix:** add a `[project]` table with `requires-python = ">=3.12"` and move the
runtime deps into it, keeping `requirements.txt` as a thin `-e .` or generated
export. Add Dependabot (`.github/dependabot.yml`, `pip` ecosystem, weekly) once
CI from finding 6 exists — updates are only safe to accept automatically when
something verifies them.

---

## What actually shipped

`18f88a3` — httpx/httpcore pinned to WARNING (token no longer logged);
`configure_logging(log_dir)` called from `main()` with `force=True` instead of
import-time `basicConfig`; `RotatingFileHandler` (5 MB × 5); `handle_error`
registered via `add_error_handler` with a deliberately generic notice.
`tests/test_error_handler.py` (9 tests).

`12b758b` — expired sessions evicted in `AccountStore.is_unlocked`, spent
throttle records dropped in `unlock_retry_after`; bare generics replaced with
`dict[Union[int, str], asyncio.Lock]`, `dict[int, tuple[str, float]]`,
`dict[int, list[float]]`. `tests/test_session_eviction.py` (5 tests).

CI commit — `.github/workflows/ci.yml` (pyflakes → import smoke → suite →
`--fail-under=100`), `.github/dependabot.yml`, `[project]` +
`[tool.coverage.*]` in `pyproject.toml`. **Verified by cloning the repo to a
scratch dir with no `.env` and running the exact CI gate: pass, exit 0.**

Two things a future session should know:

- **`configure_logging` uses `force=True`.** Without it, `basicConfig` is a
  silent no-op whenever the root logger already has a handler — which made the
  new logging test pass alone and fail in the full suite. Don't remove it.
- **The deploy runbook changed.** The launch line now redirects to
  `bot-startup.log`, not `bot.log`, because the app rotates `bot.log` itself
  now and a shell append would fight the handler over the same file. Keep
  tailing `bot.log` to verify a restart. Updated in `.claude/HANDOFF.md`.

## Still open

**Finding 4 (split `YKBot`)** — untouched. Roughly a day, wants its own spec and
plan under `docs/superpowers/`, one domain per commit. `app.py` grew slightly
with this work (`handle_error`, `configure_logging`), so the case is marginally
stronger, not weaker.

**Finding 5 (blocking I/O)** — untouched, deliberately. Still costs approximately
nothing at current scale. Revisit if the user base grows or data moves to
network storage.

**Finding 6 remainder** — ruff and mypy were **not** added. Adding either to CI
without first fixing what they report would make CI red on arrival, and a ruff
first-run diff touches many files. Both want their own commit. The CI skeleton
they'd slot into now exists.

**Finding 9 remainder** — the dict generics are fixed; bare `list` return types
(`_build_command_specs`, several store methods) and the string-quoted
`"tuple[str, InlineKeyboardMarkup]"` annotations are not. Do these as the
enabling step for mypy, not on their own.

**Token rotation** — not done, user deferred. The logging leak is closed; the
already-exposed credential is still live.

## Suggested order

| # | Finding | Effort | Do it? |
|---|---------|--------|--------|
| 1 | Token in logs | ~10 min + token rotation | **Yes** — ask before revoking |
| 2 | No error handler | ~30 min incl. tests | **Yes** |
| 3 | Import-time `basicConfig` | ~15 min | Yes, pairs with 1 |
| 7 | Log rotation | ~15 min | Yes, pairs with 3 |
| 6 | CI + `--fail-under=100` | ~30 min | Yes |
| 9 | Type precision | ~1-2 h | Only as prep for mypy |
| 8 | Unbounded dicts | ~30 min | Opportunistic |
| 10 | Project metadata | ~20 min | Opportunistic |
| 5 | Blocking I/O | ~2 h | Defer until scale demands |
| 4 | Split `YKBot` | ~1 day | **Ask first** — big, risky, works today |

Findings 1, 2, 3, 7 and 6 together are roughly one focused session and cover
everything genuinely below standard. Everything else is judgement.

Whatever gets done ships through the **STANDING RUNBOOK** at the top of
`.claude/HANDOFF.md` and keeps the suite at 100%.
