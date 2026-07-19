# telegram-bot

A locally-hosted Telegram bot built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot). Runs via long polling, so no public URL or webhook setup is needed.

The bot only responds to a fixed set of commands — it does not relay
arbitrary text to Claude. Each command maps to a preset prompt that's run
through the `claude` CLI (`claude -p`), and the reply is sent back on
Telegram. Each command is an independent one-shot invocation — nothing
carries over between commands.

## Commands

Every command requires an unlocked session (see Accounts below) — including
`/help` and `/start`. There is no way to see what the bot can do without a
password first. `/news`, the interests commands, and `/bus` all operate on
whichever account your password unlocked (see Accounts).

| Command | Does |
| --- | --- |
| `/news <subject>` | Generates a news digest on `<subject>`, biased toward your account's saved interests |
| `/summarize <url>` | Fetches a public web page and summarizes it (internal/private URLs are refused) |
| `/addinterests <topic>` | Adds an interest — a word, sentence, or paragraph |
| `/getinterests` | Lists your account's interests with their index |
| `/updateinterests <index> <topic>` | Revises the interest at `<index>` — Claude decides whether `<topic>` adds detail to it or replaces it outright |
| `/removeinterest <index>` | Deletes the interest at `<index>` |
| `/compileinterests` | Runs the same merge/enrich pass the background job runs periodically, on demand |
| `/bus` | Bus arrival times — pick a preset stop, share your location for the nearest ones, or add a stop by code |
| `/endsession` | Ends the current session immediately, instead of waiting out the timeout |
| `/help` | Lists all commands, grouped by category |

Interests are stored as a numbered list in `accounts/<id>/interests.md` —
`/news` reads the current account's file before writing each digest, so
changes take effect immediately with no restart needed. The file is
managed through the commands above; hand-editing it works too as long as
entries stay in `N. text` form.

Every `INTERESTS_REORGANIZE_INTERVAL_SECONDS` (default 86400 = daily), a
background job runs once per account: it sends that account's interests
list to Claude, which merges duplicate or overlapping entries and adds a
short blurb of extra detail to entries where that would help `/news`
retrieve more relevant results — without inventing interests the list
doesn't already imply. Runs automatically and silently — the result is
recorded in the log, not pushed to Telegram (broadcasting one account's
reorganize summary to every allowed user would leak its id and interest
counts across accounts); you'll simply see the updated list next time you
run `/getinterests`. Set the interval to `0` to disable it. Run
`/compileinterests` to trigger the same pass on demand for your own
account — it shares a lock with the background job so the two never run
concurrently and step on each other's edits. Its reply includes the full
reorganized list, same as `/getinterests`.

### Passive interest learning

Each time you run `/news <subject>`, the subject is logged to your account's
`accounts/<id>/news_history.jsonl` (gitignored as part of the `accounts/`
directory, since it contains real data about user requests). The daily
background job and `/compileinterests` both examine this log to detect
recurring request themes and fold them into your interests automatically —
a passive, hands-off way to refine your interests over time without
manually editing them. The learning is silent: it doesn't announce what it
found, so you'll only notice by checking `/getinterests` and seeing new or
expanded interests. It's also additive: new learned themes never drop or
replace your manually-added interests, only enrich the list. Run
`/compileinterests` to see the full reorganized result immediately, or let
the daily pass handle it passively. Note that only the daily background
pass prunes old entries from the log according to the retention window; the
on-demand `/compileinterests` learns from the current log without pruning.

Configure the passive learning behavior with three optional environment
variables (see Configuration below):

- `NEWS_LEARNING_ENABLED` (default `true`) — set to `false` to disable
  logging of `/news` subjects entirely.
- `NEWS_HISTORY_WINDOW_DAYS` (default `60`) — how many days of request
  history the daily job considers when looking for recurring themes.
- `NEWS_HISTORY_MAX_ENTRIES` (default `100`) — maximum number of logged
  requests to keep per account; older entries are pruned first.

Unrecognized commands get a random animal-fact reply, same as a locked
command or a wrong password attempt — never an error, and never a hint
about what commands exist. Plain text messages are never sent to Claude
— see Accounts below.

### Button menu

`/menu` (and the post-unlock welcome message) shows an inline-button board
as a faster alternative to typing slash commands — every button ultimately
runs the same handler code as its slash-command equivalent. All slash
commands keep working unchanged, and `/help` stays slash-only (it's not on
the board).

The top-level board has 🚌 Bus, 📰 News, ⭐ Interests, 🔗 Summarize, and 🔒 End
session, plus a 🛠 Admin row shown only to an admin session. Bus opens the
same fav-stop menu as `/bus`. News, Summarize, and Interests → ➕ Add each
arm a one-shot capture and prompt for text — your next plain message is
read as that command's argument instead of a password attempt. End session
asks for a ✅ Yes/❌ No confirmation before locking.

Tapping ⭐ Interests opens a submenu: 📋 List, ➕ Add, ✏️ Update, 🗑 Remove,
🗂 Compile, and ⬅️ Back. Update and Remove first show one button per saved
interest to pick from; Remove then asks for a Yes/No confirmation, Update
prompts for the replacement wording (same "revise or replace" behavior as
`/updateinterests`).

Tapping 🛠 Admin (admin sessions only) opens 👥 Get users, 🗑 Remove user,
🆔 Allow Telegram id, and ⬅️ Back. Remove user shows one button per
removable account, then a Yes/No confirmation. Allow Telegram id prompts
for the id, same as `/adduserteleid`. Adding a new account with a password
hash stays slash-only (`/adduser <name> <sha256hash>`) — there's no
sensible way to type a hash through a button flow.

Every button action re-checks the session server-side (and admin actions
re-check admin) exactly like the slash command it mirrors — a button only
being visible to the right session doesn't mean the underlying handler
trusts that; it never does.

## Accounts

Every command is gated behind a password — even ones like `/help` or
`/addinterests` that don't touch Claude. A locked command gets the same
random animal-fact reply as an unrecognized command or a wrong password,
never a "locked" notice or the command's actual output — a "locked"
message would itself confirm the command exists and that the bot uses a
password gate, which an attacker probing the bot shouldn't learn for
free. So `/help`'s command list stays hidden until unlocked, same as
everything else. The one exception is plain text itself, which is the
unlock mechanism and can't be gated behind its own gate.

Which password you send determines which **account** your session unlocks
as. Each account has its own isolated interests list and preferred bus
stops, stored under `accounts/<account id>/`. One account can have
multiple passwords (e.g. one per device) — any of them unlock the same
account and see the same data. Two different accounts see completely
separate interests/bus locations, even though they're both talking to the
same bot.

1. Pick a password and hash it: `printf '%s' 'yourpassword' | sha256sum`.
2. Open `accounts.hash` in this directory. Add a `# account: <id>` header
   line (letters, digits, `-`/`_` only — it's used as a folder name under
   `accounts/`), then the hex digest on its own line below it. Add more
   digests under the same header for more passwords on the same account,
   or another `# account: <id>` header further down for a completely
   separate account. The file has these instructions in it as comments.
3. In Telegram, send the plain-text password (not a command) as a message.
   If it matches, the bot deletes that message immediately, permanently
   removing the password from the chat, and starts a session as that
   password's account. On that first successful unlock the bot rewrites the
   entry from bare SHA-256 to a salted **scrypt** hash — so you keep the
   familiar `sha256sum` workflow when adding passwords, and they're
   automatically upgraded to a slow, salted hash the first time they're used.
   Repeated wrong guesses from one chat are rate-limited (5 failures → a
   short cooldown) to blunt brute-force attempts.
4. The session lasts `SESSION_TIMEOUT_SECONDS` (default 900 = 15 minutes)
   from the moment it unlocks, regardless of activity. After it expires,
   every command locks again and needs the password re-sent. Send
   `/endsession` to lock it early instead of waiting out the timeout.

Any plain-text message is treated as a password attempt, correct or not —
the bot never runs arbitrary text through Claude. A wrong guess (or no
accounts configured yet) gets a random animal fact in reply, with no
indication it was read as a password attempt.

Message deletion only works in private chats (the expected setup here) —
Telegram bots can't delete other users' messages in groups without admin
rights.

An account's directory (`accounts/<id>/interests.md`,
`accounts/<id>/bus_locations.json`) is created automatically the first
time that account unlocks and touches interests or bus locations — nothing
to set up by hand beyond the password itself.

### Admin account

One account is designated admin — by default the `amy` account, and only
when unlocked from Telegram user id `100000001` (both `ADMIN_ACCOUNT_ID`
and `ADMIN_TELEGRAM_USER_ID` are configurable in `.env`). Knowing the
admin account's password from a *different* Telegram account isn't enough
on its own, and being an allowed Telegram user with a different account's
password isn't enough either — both checks have to pass.

The admin gets four extra commands, usable only from an unlocked admin
session:

| Command | Does |
| --- | --- |
| `/adduser <name> <sha256hash>` | Creates account `<name>` with that password hash, or adds the hash to `<name>` if it already exists |
| `/removeuser <name>` | Deletes `<name>`'s entry (header + all its hashes) from `accounts.hash`. Refuses to remove the admin account itself |
| `/adduserteleid <telegram_user_id>` | Adds a Telegram user id to the allowlist — takes effect immediately, no restart needed, and is also written back to `TELEGRAM_ALLOWED_USER_IDS` in `.env` so it survives one |
| `/getusers` | Lists every account in `accounts.hash` and whether it currently has an active (unlocked, not-yet-expired) session |

`/adduser` takes a ready-made hash, not a plaintext password — hash it
first the same way as any other password: `printf '%s' 'theirpassword' |
sha256sum`. A non-admin who sends any of these four commands gets the
same animal-fact reply as an unrecognized command; there's no hint that
admin-only commands exist at all.

Every command's name, usage, and description live in one place in
`ykbot/app.py` (`YKBot._build_command_specs`), which is what both registers it with
Telegram and renders it in `/help` and the post-unlock welcome message —
a new command only needs to be added there once to show up everywhere.
Admin commands only render in `/help`/the welcome message for an admin
session; everyone else's command list is unaffected by their existence.

### Trivia source

Wrong-password replies pull from a local cache (`trivia_cache.json`) of
1000 unique animal facts, rotated so nothing repeats until every fact in
the cache has been used once. The cache is built from
[ekohrt/animal-fun-facts-dataset](https://github.com/ekohrt/animal-fun-facts-dataset)
(MIT licensed), a dataset of animal facts scraped from various sites
across the web. To build or refresh it:

```bash
python fetch_animal_trivia.py
```

No credentials needed. It downloads the dataset, dedupes and filters out
fragments, and writes 1000 random unique facts to `trivia_cache.json`.
If that file doesn't exist yet, the bot falls back to a small built-in
static list so wrong-password replies never break.

Each reply is prefixed with an emoji matched to the animal (`AnimalEmoji`
in `bot.py`) — 🐕 for "American Leopard Hound", 🦕 for "Parasaurolophus",
🐙 for "Octopus", and so on, falling back to 🐾 for anything unmatched.
Tuned against the actual cache: ~99.8% of its ~600 distinct animal names
get a specific match.

## Bus arrivals

`/bus` pulls real-time arrival times from Singapore's LTA DataMall API.

1. Sign up for a free API key at
   [datamall.lta.gov.sg](https://datamall.lta.gov.sg/content/datamall/en/request-for-api.html)
   and set `LTA_API_KEY` in `.env`. Shared across all accounts — it's just
   API auth, not account-specific data.
2. Edit `accounts/<id>/bus_locations.json` (start from
   `bus_locations.example.json`, and create the `accounts/<id>/` directory
   if that account hasn't unlocked a session yet) — a flat
   `{"name": "busStopCode"}` map shown as that account's `/bus` preset
   buttons. Find a stop's code on its physical signage, or via
   [OneMap](https://www.onemap.gov.sg/)/Google Maps. You can also skip
   hand-editing entirely and use the "⭐ Add as preferred" button (see
   below) from within Telegram instead.
3. For the "share my location" path specifically (preset buttons work
   without this), build the nearest-stop lookup cache:

   ```bash
   python fetch_bus_stops.py
   ```

   Pages through LTA's static bus stop dataset (~5000 stops) into
   `bus_stops_cache.json`. Re-run occasionally to pick up new stops —
   LTA doesn't publish a changelog, so there's no reliable auto-refresh
   trigger; this is a manual, infrequent maintenance step.

`/bus` shows one inline keyboard: your preset locations as buttons, plus a
"📍 Send my location" button. Telegram doesn't allow inline buttons to
trigger native location sharing directly (only the separate reply-keyboard
UI can do that), so tapping it instead edits the message with instructions
to reply to it with your location (📎 → Location) — the bot picks up that
reply the same way as any shared location. Sharing your location shows the
nearest `BUS_NEARBY_COUNT` stops (default 3) as buttons — bus stops
usually come in direction-opposite pairs across a road, so picking from a
short list beats guessing from the single nearest one. Tapping any stop
button (preset or nearby) fetches and displays live arrivals for it.

The `/bus` menu also has a "➕ Add a stop by code" button, for adding a
preferred stop you already know the 5-digit code for (without having to be
near it or navigate to it). Tapping it arms a one-shot capture: your next
plain message is read as a bus-stop code instead of a password attempt (only
while your session is unlocked — otherwise it's treated as a password as
usual). The bot then shows that stop's description and live arrivals with an
✅ Add / ❌ Cancel confirmation, so you can check you typed the right code
before it's saved.

The arrivals screen has three buttons. A top row of "🔄 Refresh" — re-fetches
the arrival timers for the same stop and re-renders the message (a "🔄 Updated
HH:MM:SS" footer confirms the refresh and keeps Telegram from rejecting an
unchanged edit) — and "⬅️ Back", which returns to whichever menu you came
from: your preset stops (from `/bus`) or the nearby stops (from a shared
location). The menu you came from is remembered per user in memory only, so
after a bot restart, Back on an old message falls back to the preset menu with
a "Menu expired —" note. Below that, a "⭐ Add as preferred" button for a
stop not already in the current account's `bus_locations.json`, or "🗑
Remove from preferred" for one that is — tapping it edits that account's
file directly and flips the button, so you can toggle a stop in or out
without leaving Telegram. Added names are auto-derived from the stop's
description in `bus_stops_cache.json` (e.g. "Opp Parkway East Hosp
(83159)"), falling back to just the stop code if that cache isn't built
yet.

## Message logging

Messages from **unlocked** chats are appended to per-user files under
`message_logs/` (gitignored), one file per sender — `<username>.log`, or
`id_<telegram_user_id>.log` for users with no `@username`. Each line is a
tab-separated `ISO-timestamp` and the message text (commands included;
non-text messages such as shared locations are recorded as
`[non-text message]`).

The logger runs in an earlier handler group than the command handlers, and
only writes for chats that are **already unlocked**. The unlock message
itself is still locked at that point, so **password attempts (and wrong
guesses) are never logged** — consistent with the rest of the bot never
storing plaintext passwords. Set `MESSAGE_LOGGING_ENABLED=false` to turn the
feature off, or `MESSAGE_LOGS_DIR` to relocate the folder. Each per-user log
is capped (~1 MB, trimmed to the most recent half) so unbounded input can't
fill the disk. The bot's own `bot.log` is *not* auto-rotated — if you run the
bot long-term, point `logrotate` at it (or redirect to `systemd`/`journald`).

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token it gives you.
2. Install the [Claude Code CLI](https://claude.com/claude-code) on this machine and make sure it's authenticated (`claude auth status`). Any skills you want the bot to use must be available to that CLI (personal skills under `~/.claude`, or project skills in `CLAUDE_WORKDIR`, see below).
3. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and fill it in:

   ```bash
   cp .env.example .env
   ```

   - `TELEGRAM_BOT_TOKEN` — from BotFather.
   - `TELEGRAM_ALLOWED_USER_IDS` — comma-separated numeric Telegram user IDs
     allowed to use the bot (get yours from
     [@userinfobot](https://t.me/userinfobot)). **Set this.** The bot runs
     Claude with permission checks bypassed (see Security below), so anyone
     who can message it can run arbitrary tools on this machine.

5. Run the bot:

   ```bash
   python bot.py
   ```

Message your bot on Telegram with the password first (see Accounts below)
to unlock a session, then try `/news cybersecurity` or `/help` for the
full command list.

## Testing

Unit tests live in `tests/`, one file per class under test, written as
plain `unittest.TestCase` subclasses (stdlib only — no extra dependency
needed to run them). `tests/` is a real package (`tests/__init__.py`),
and its shared helpers (`BotTestCase`, fixture builders) live in
`tests/helpers.py`, imported from test files as `from tests.helpers
import ...`. Run the whole suite with:

```bash
python -m unittest discover -s tests -t .
```

The `-t .` (top-level dir = repo root) matters: VS Code's Testing
sidebar forces `top_level_dir` to the workspace root when it discovers
tests, so the CLI command needs to match that or you'll get inconsistent
results between the two. Run a single file the same way, just narrowing
the pattern:

```bash
python -m unittest discover -s tests -t . -p "test_account_store.py"
```

The suite also happens to run under `pytest tests/` if you have it
installed (`pip install -r requirements-dev.txt`) — useful for local
experimentation — but `unittest` via the command above is the supported,
zero-extra-dependency way to run tests here.

## Running with Docker

Alternative to the venv setup above — runs the bot in a container instead.
Still needs step 1 (BotFather token) and step 4 (`.env`) from Setup; skips
steps 2–3 (Claude CLI install, venv) since the image handles those.

```bash
cp .env.example .env   # fill it in, same as the non-Docker setup
docker compose up -d --build
docker compose logs -f  # equivalent of tailing bot.log
```

What `docker-compose.yml` does:

- Builds the image from `Dockerfile`, which installs the Claude Code CLI
  and this repo's Python dependencies — no local venv needed.
- Bind-mounts the whole repo into the container at `/app`. This means the
  gitignored, hand-edited files (`accounts.hash`, `accounts/`,
  `trivia_cache.json`, `message_logs/`, ...) are read from and written back
  to your actual working directory, exactly like running `python bot.py`
  directly — no separate data volume to manage, and no rebuild needed
  after editing `bot.py` (just `docker compose restart`).
- Bind-mounts `~/.claude` into the container. **This is the important
  one**: Claude Code auth is OAuth-based (`~/.claude/.credentials.json`),
  tied to this machine's login — the container reuses it instead of
  needing its own `claude auth login`. It also carries over your global
  personal skills and `~/.claude/settings.json`, so Claude behaves the
  same inside the container as it does on the host.

Caveats:

- If `.env` sets `CLAUDE_WORKDIR` to an absolute host path outside this
  repo, it won't resolve inside the container unless you also add a bind
  mount for it in `docker-compose.yml` — leave it unset (the default) to
  avoid this entirely.
- `TELEGRAM_ALLOWED_USER_IDS` matters even more here: the container's
  Claude runs with the same `bypassPermissions` access as the host CLI, so
  the allowlist is the only thing standing between a stranger and shell
  access on this machine (see Security below).
- Rebuild (`docker compose up -d --build`) after changing
  `requirements.txt` or the `Dockerfile` itself; a plain `docker compose
  restart` is enough for `bot.py`/data-file changes since those are
  bind-mounted, not baked into the image.

## Configuration

Optional environment variables (see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLAUDE_BIN` | `claude` | Path to the Claude Code CLI binary |
| `CLAUDE_WORKDIR` | this repo's directory | Working directory Claude runs in — controls which project `CLAUDE.md`/skills are in scope |
| `CLAUDE_MODEL` | CLI default | Model alias or name to pass to `--model` |
| `CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Passed to `--permission-mode`; see Security below |
| `CLAUDE_TIMEOUT_SECONDS` | `600` | Kill the `claude` process if it runs longer than this |
| `ACCOUNTS_FILE` | `accounts.hash` | Path to the account-id-to-password-hash file |
| `ACCOUNTS_DIR` | `accounts/` | Directory holding each account's `interests.md` and `bus_locations.json` |
| `SESSION_TIMEOUT_SECONDS` | `900` | How long an unlocked session lasts, in seconds |
| `INTERESTS_REORGANIZE_INTERVAL_SECONDS` | `86400` | How often the background reorganize job runs per account; `0` disables it |
| `BUS_STOPS_CACHE_FILE` | `bus_stops_cache.json` | Path to the nearest-stop lookup cache built by `fetch_bus_stops.py` |
| `BUS_NEARBY_COUNT` | `3` | How many nearest stops to show as buttons when you share your location |
| `NEWS_LEARNING_ENABLED` | `true` | Enable passive learning from `/news` requests; set to `false` to disable logging |
| `NEWS_HISTORY_WINDOW_DAYS` | `60` | How many days of `/news` history to consider when detecting recurring themes |
| `NEWS_HISTORY_MAX_ENTRIES` | `100` | Maximum logged `/news` requests per account; oldest entries are pruned first |
| `MESSAGE_LOGGING_ENABLED` | `true` | Log messages from unlocked chats to `message_logs/`; set to `false` to disable |
| `MESSAGE_LOGS_DIR` | `message_logs/` | Directory holding the per-user message log files |
| `ENV_FILE` | `.env` | Path `/adduserteleid` rewrites `TELEGRAM_ALLOWED_USER_IDS` in |
| `ADMIN_ACCOUNT_ID` | `amy` | Account id allowed to use the admin commands (see Admin account) |
| `ADMIN_TELEGRAM_USER_ID` | `100000001` | Telegram user id allowed to use the admin commands, alongside `ADMIN_ACCOUNT_ID` |

## Security

**Claude runs with no host access.** Every `claude -p` call is launched with
`--tools` restricted to a hard allowlist — only `WebSearch`/`WebFetch` for
`/news`, and *no tools at all* for the interest commands. `Bash`, `Read`,
`Write`, and `Edit` are never available, so a prompt injection (via a `/news`
subject, an interest entry, etc.) cannot be turned into file access or shell
commands on the machine. (`--tools` limits tool *availability* even under
`--permission-mode bypassPermissions`, which only bypasses the interactive
approval prompt that a TTY-less Telegram session can't answer.)

**The allowlist fails closed.** `TELEGRAM_ALLOWED_USER_IDS` is required: if it
is unset the bot ignores *every* message (and logs a warning) rather than
exposing itself to anyone. Set it in `.env` with at least your own Telegram
user id before first run, and only add user IDs you trust.

**Errors aren't echoed.** Claude/CLI failures are logged server-side; the user
just gets a generic "something went wrong" reply, so internal paths and stderr
never leak into the chat.
