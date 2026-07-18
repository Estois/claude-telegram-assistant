"""Telegram bot entry point.

Thin re-export shim — the implementation lives in the ``ykbot`` package. This
module stays the process entry point (``python bot.py``) and the import target
for the test suite (``import bot``); every public name is re-exported below.
"""

from ykbot import (
    AccountStore,
    AllowlistStore,
    AnimalEmoji,
    BusLocationsStore,
    BusStopsCache,
    ClaudeClient,
    ClaudeSessionStore,
    CommandSpec,
    Config,
    FALLBACK_TRIVIA,
    InterestsStore,
    LtaBusClient,
    MessageLogStore,
    NewsHistoryStore,
    TelegramFormatter,
    TriviaBook,
    YKBot,
    logger,
    main,
)

__all__ = [
    "Config",
    "AccountStore",
    "AllowlistStore",
    "ClaudeSessionStore",
    "ClaudeClient",
    "InterestsStore",
    "MessageLogStore",
    "NewsHistoryStore",
    "AnimalEmoji",
    "TriviaBook",
    "FALLBACK_TRIVIA",
    "TelegramFormatter",
    "LtaBusClient",
    "BusStopsCache",
    "BusLocationsStore",
    "CommandSpec",
    "YKBot",
    "main",
    "logger",
]


if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
