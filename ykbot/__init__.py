"""The ykbot package: a password-gated Telegram bot that runs Claude Code.

``bot.py`` at the repo root is a thin entry-point shim; this package holds the
implementation. Every public name the shim and the test suite import is
re-exported here so ``from ykbot import X`` and ``import bot`` both work.
"""

from ykbot._logging import logger
from ykbot.accounts import AccountStore, AllowlistStore
from ykbot.app import (
    LOG_BACKUP_COUNT,
    LOG_FILENAME,
    LOG_MAX_BYTES,
    YKBot,
    configure_logging,
    main,
)
from ykbot.bus import BusLocationsStore, BusStopsCache, LtaBusClient
from ykbot.claude import ClaudeClient
from ykbot.config import Config
from ykbot.formatting import CommandSpec, TelegramFormatter
from ykbot.interests import InterestsStore
from ykbot.message_log import MessageLogStore
from ykbot.news_history import NewsHistoryStore
from ykbot.services import BotServices
from ykbot.trivia import FALLBACK_TRIVIA, AnimalEmoji, TriviaBook

__all__ = [
    "Config",
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
    "YKBot",
    "main",
    "configure_logging",
    "LOG_FILENAME",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
    "logger",
]
