"""Telegram bot entry point.

Thin re-export shim — the implementation lives in the ``ykbot`` package. This
module stays the process entry point (``python bot.py``) and the import target
for the test suite (``import bot``); every public name is re-exported below.
"""

from ykbot import (
    LOG_BACKUP_COUNT,
    LOG_FILENAME,
    LOG_MAX_BYTES,
    AccountStore,
    AllowlistStore,
    AnimalEmoji,
    BotServices,
    BusHandlers,
    BusLocationsStore,
    BusStopsCache,
    ClaudeClient,
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
    configure_logging,
    logger,
    main,
)

__all__ = [
    "Config",
    "configure_logging",
    "LOG_FILENAME",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
    "AccountStore",
    "AllowlistStore",
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
    "BotServices",
    "BusHandlers",
    "YKBot",
    "main",
    "logger",
]


if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
