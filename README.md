# telegram-bot

A locally-hosted Telegram bot built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot). Runs via long polling, so no public URL or webhook setup is needed.

The bot only responds to a fixed set of commands — it does not relay
arbitrary text to Claude. Each command maps to a preset prompt that's run
through the `claude` CLI (`claude -p`), and the reply is sent back on
Telegram. Each Telegram chat keeps its own Claude session, so follow-up
messages continue the same conversation; use `/new` to start over.

## Commands

| Command | Does |
| --- | --- |
| `/news <subject>` | Generates a news digest on `<subject>`, biased toward your saved interests. Requires an unlocked session (see below). |
| `/addinterests <topic>` | Adds an interest — a word, sentence, or paragraph |
| `/getinterests` | Lists your interests with their index |
| `/updateinterests <index> <topic>` | Revises the interest at `<index>` — Claude decides whether `<topic>` adds detail to it or replaces it outright. Requires an unlocked session. |
| `/new` | Resets the conversation for this chat |
| `/help` | Lists commands |

Interests are stored as a numbered list in `interests.md` (path
configurable via `NEWS_INTERESTS_FILE`) — `/news` reads it before writing
each digest, so changes take effect immediately with no restart needed.
The file is managed through the commands above; hand-editing it works too
as long as entries stay in `N. text` form.

Unrecognized commands get a short reply pointing to `/help`. Plain text
messages are never sent to Claude — see Password / session below.

## Password / session

`/news` and `/updateinterests` are gated behind a password, since they're
the commands that invoke Claude with permission checks bypassed.
`/addinterests` and `/getinterests` are plain local file operations and
work regardless of lock state.

1. Pick a password and hash it: `printf '%s' 'yourpassword' | sha256sum`.
2. Open `password.hash` in this directory and put the hex digest on its own
   line (the file has instructions in it as comments).
3. In Telegram, send the plain-text password (not a command) as a message.
   If it matches, the bot deletes that message immediately, permanently
   removing the password from the chat, and starts a session.
4. The session lasts `SESSION_TIMEOUT_SECONDS` (default 900 = 15 minutes)
   from the moment it unlocks, regardless of activity. After it expires,
   `/news` locks again and needs the password re-sent.

Any plain-text message is treated as a password attempt, correct or not —
the bot never runs arbitrary text through Claude. A wrong guess (or no
password configured yet) gets a random animal fact in reply, with no
indication it was read as a password attempt.

Message deletion only works in private chats (the expected setup here) —
Telegram bots can't delete other users' messages in groups without admin
rights.

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

Message your bot on Telegram — try `/news cybersecurity`, or `/help` for the
full command list.

## Configuration

Optional environment variables (see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLAUDE_BIN` | `claude` | Path to the Claude Code CLI binary |
| `CLAUDE_WORKDIR` | this repo's directory | Working directory Claude runs in — controls which project `CLAUDE.md`/skills are in scope |
| `CLAUDE_MODEL` | CLI default | Model alias or name to pass to `--model` |
| `CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Passed to `--permission-mode`; see Security below |
| `CLAUDE_TIMEOUT_SECONDS` | `600` | Kill the `claude` process if it runs longer than this |

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
