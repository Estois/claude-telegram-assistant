# telegram-bot

A locally-hosted Telegram bot built with [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot). Runs via long polling, so no public URL or webhook setup is needed.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token it gives you.
2. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`:

   ```bash
   cp .env.example .env
   ```

4. Run the bot:

   ```bash
   python bot.py
   ```

Message your bot on Telegram — `/start` replies with a greeting, and any other text message gets echoed back.
