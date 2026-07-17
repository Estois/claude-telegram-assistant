# telegram-bot

A locally-hosted Telegram bot built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot). Runs via long polling, so no public URL or webhook setup is needed.

The bot only responds to a fixed set of commands — it does not relay
arbitrary text to Claude. Each command maps to a preset prompt that's run
through the `claude` CLI (`claude -p`), and the reply is sent back on
Telegram. Each Telegram chat keeps its own Claude session, so follow-up
messages continue the same conversation; use `/clearclaudememory` to start
over.

## Commands

Every command requires an unlocked session (see Accounts below) — including
`/help` and `/start`. There is no way to see what the bot can do without a
password first. `/news`, the interests commands, and `/bus` all operate on
whichever account your password unlocked (see Accounts).

| Command | Does |
| --- | --- |
| `/news <subject>` | Generates a news digest on `<subject>`, biased toward your account's saved interests |
| `/addinterests <topic>` | Adds an interest — a word, sentence, or paragraph |
| `/getinterests` | Lists your account's interests with their index |
| `/updateinterests <index> <topic>` | Revises the interest at `<index>` — Claude decides whether `<topic>` adds detail to it or replaces it outright |
| `/compileinterests` | Runs the same merge/enrich pass the background job runs periodically, on demand |
| `/bus` | Bus arrival times — pick a preset stop, or share your location for the nearest ones |
| `/endsession` | Ends the current session immediately, instead of waiting out the timeout |
| `/clearclaudememory` | Clears Claude's memory of this conversation and starts fresh |
| `/help` | Lists commands |

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
doesn't already imply. Runs automatically; each allowed user gets a
one-line Telegram notification (tagged with the account id) when an
account's list changes. Set the interval to `0` to disable it. Run
`/compileinterests` to trigger the same pass on demand for your own
account — it shares a lock with the background job so the two never run
concurrently and step on each other's edits. Its reply includes the full
reorganized list, same as `/getinterests`.

Unrecognized commands get a short reply pointing to `/help`. Plain text
messages are never sent to Claude — see Accounts below.

## Accounts

Every command is gated behind a password — even ones like `/help` or
`/addinterests` that don't touch Claude. A locked command gets only
"🔒 Locked. Send the password to start a session," never the command's
actual output, so `/help`'s command list stays hidden until unlocked. The
one exception is plain text itself, which is the unlock mechanism and
can't be gated behind its own gate.

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
   password's account.
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

The admin gets three extra commands, usable only from an unlocked admin
session:

| Command | Does |
| --- | --- |
| `/adduser <name> <sha256hash>` | Creates account `<name>` with that password hash, or adds the hash to `<name>` if it already exists |
| `/removeuser <name>` | Deletes `<name>`'s entry (header + all its hashes) from `accounts.hash`. Refuses to remove the admin account itself |
| `/adduserteleid <telegram_user_id>` | Adds a Telegram user id to the allowlist — takes effect immediately, no restart needed, and is also written back to `TELEGRAM_ALLOWED_USER_IDS` in `.env` so it survives one |

`/adduser` takes a ready-made hash, not a plaintext password — hash it
first the same way as any other password: `printf '%s' 'theirpassword' |
sha256sum`. A non-admin who sends any of these three commands gets the
same animal-fact reply as an unrecognized command; there's no hint that
admin-only commands exist at all.

Every command's name, usage, and description live in one place in `bot.py`
(`NewsBot._build_command_specs`), which is what both registers it with
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

The arrivals screen itself has a second button: "⭐ Add as preferred" for a
stop not already in the current account's `bus_locations.json`, or "🗑
Remove from preferred" for one that is — tapping it edits that account's
file directly and flips the button, so you can toggle a stop in or out
without leaving Telegram. Added names are auto-derived from the stop's
description in `bus_stops_cache.json` (e.g. "Opp Parkway East Hosp
(83159)"), falling back to just the stop code if that cache isn't built
yet.

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
needed to run them). Run the whole suite with:

```bash
python -m unittest discover -s tests
```

That's also exactly what VS Code's Testing sidebar runs (see
`.vscode/settings.json`), so results match between the CLI and the
editor. Run a single file by narrowing the discovery pattern (plain
`python -m unittest tests.test_account_store` looks like it should work
but doesn't — see the import note below):

```bash
python -m unittest discover -s tests -p "test_account_store.py"
```

Shared test helpers (a `BotTestCase` base class providing a scratch temp
directory per test, plus small fixture-builder functions) live in
`tests/helpers.py`, imported directly as `from helpers import ...`.
That only resolves with `-s tests` explicitly given, as above — a bare
`python -m unittest discover` from the repo root finds zero tests
(`tests/` has no `__init__.py`, so default discovery doesn't descend
into it), and adding `__init__.py` to fix that would break the `from
helpers import ...` imports the other way instead (`tests` would become
a real package, so `helpers` would only resolve as `tests.helpers`).
`-s tests` is the one invocation that makes both sides work, which is
exactly what `.vscode/settings.json` is configured to do.

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
  `sessions.json`, `trivia_cache.json`, ...) are read from and written back
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
| `ENV_FILE` | `.env` | Path `/adduserteleid` rewrites `TELEGRAM_ALLOWED_USER_IDS` in |
| `ADMIN_ACCOUNT_ID` | `amy` | Account id allowed to use the admin commands (see Admin account) |
| `ADMIN_TELEGRAM_USER_ID` | `100000001` | Telegram user id allowed to use the admin commands, alongside `ADMIN_ACCOUNT_ID` |

## Security

Telegram messages arrive with no TTY attached, so Claude can't interactively
ask "may I run this tool?" — a prompt would just hang forever. The bot works
around this by running Claude with `--permission-mode bypassPermissions`,
which skips all tool-permission checks (file edits, shell commands, network
requests, everything).

That makes the `TELEGRAM_ALLOWED_USER_IDS` allowlist load-bearing, not
optional: anyone who can message the bot can get Claude to run arbitrary
commands on this machine. The bot logs a warning and runs wide open if the
allowlist is unset. Only add user IDs you trust.
