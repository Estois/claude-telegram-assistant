"""Telegram bot entry point.

Thin re-export shim — the implementation lives in the ``ykbot`` package. This
module stays the process entry point (``python bot.py``) and the import target
for the test suite (``import bot``); every public name is re-exported below.
"""

from ykbot._logging import logger
from ykbot.accounts import AccountStore, AllowlistStore
from ykbot.app import YKBot, main
from ykbot.bus import BusLocationsStore, BusStopsCache, LtaBusClient
from ykbot.claude import ClaudeClient, ClaudeSessionStore
from ykbot.config import Config
from ykbot.formatting import CommandSpec, TelegramFormatter
from ykbot.interests import InterestsStore
from ykbot.trivia import FALLBACK_TRIVIA, AnimalEmoji, TriviaBook

# Backwards-compatible alias: some tests still import bot.NewsBot until they
# are renamed to YKBot. Dropped once they are.
NewsBot = YKBot

__all__ = [
    "Config",
    "AccountStore",
    "AllowlistStore",
    "ClaudeSessionStore",
    "ClaudeClient",
    "InterestsStore",
    "AnimalEmoji",
    "TriviaBook",
    "FALLBACK_TRIVIA",
    "TelegramFormatter",
    "LtaBusClient",
    "BusStopsCache",
    "BusLocationsStore",
    "CommandSpec",
    "YKBot",
    "NewsBot",
    "main",
    "logger",
]


if __name__ == "__main__":
    main()
