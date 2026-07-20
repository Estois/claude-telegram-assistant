"""The shared dependency bundle handler classes receive.

YKBot grew to 99 methods because every handler needed the same handful of
things: config, the stores, and the shared reply/auth/claude helpers. Rather
than have extracted handler classes hold a back-reference to YKBot — which
would keep the coupling and merely hide it behind a file boundary — they
take this object. The dependency direction stays one-way, and a handler can
be constructed against a stub.

Deliberately a plain dataclass with no behavior: it is a bundle, not a
service locator, and it must not accumulate logic of its own.
"""

from dataclasses import dataclass
from typing import Callable

from ykbot.accounts import AccountStore, AllowlistStore
from ykbot.bus import BusStopsCache, LtaBusClient
from ykbot.claude import ClaudeClient
from ykbot.config import Config
from ykbot.message_log import MessageLogStore
from ykbot.trivia import TriviaBook


@dataclass(frozen=True)
class BotServices:
    config: Config

    # Singleton stores and clients. Concrete types, not `object`: none of
    # these modules import this one, so there is no import cycle, and a
    # precise type is the whole point of a dependency bundle.
    accounts: AccountStore
    allowlist: AllowlistStore
    claude: ClaudeClient
    trivia: TriviaBook
    lta: LtaBusClient
    stops: BusStopsCache
    message_log: MessageLogStore

    # Per-account store factories.
    interests_for: Callable
    bus_locations_for: Callable
    news_history_for: Callable

    # Shared helpers, bound to the owning YKBot.
    require_session: Callable
    require_admin: Callable
    is_admin: Callable
    reply_result: Callable
    send_formatted: Callable
    run_claude: Callable
    safe_edit: Callable
