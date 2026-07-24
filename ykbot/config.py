"""Runtime configuration, loaded once from the environment.

``load_dotenv()`` runs at import time so the ``.env`` file is applied whenever
``Config`` is importable — matching the bot's original import-time behaviour.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    """Runtime settings, loaded once from the environment."""

    base_dir: Path
    telegram_token: str
    allowed_user_ids: Optional[frozenset]
    env_file: Path
    admin_account_id: str
    admin_telegram_user_id: int
    closed_account_ids: frozenset
    claude_bin: str
    claude_workdir: str
    claude_model: Optional[str]
    claude_permission_mode: str
    claude_timeout_seconds: int
    accounts_dir: Path
    interests_reorganize_interval_seconds: int
    accounts_file: Path
    session_timeout_seconds: int
    trivia_cache_file: Path
    trivia_state_file: Path
    lta_api_key: Optional[str]
    bus_stops_cache_file: Path
    bus_nearby_count: int
    news_learning_enabled: bool
    news_history_window_days: int
    news_history_max_entries: int
    news_recency_days: int
    message_logging_enabled: bool
    message_logs_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        # This module lives one directory below the repo root (ykbot/), so the
        # data files sit at ``parent.parent``, not ``parent``.
        base_dir = Path(__file__).resolve().parent.parent

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
            )

        raw_ids = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        allowed_user_ids = (
            frozenset(int(x) for x in raw_ids.split(",") if x.strip()) if raw_ids else None
        )

        return cls(
            base_dir=base_dir,
            telegram_token=token,
            allowed_user_ids=allowed_user_ids,
            env_file=Path(os.environ.get("ENV_FILE", str(base_dir / ".env"))).resolve(),
            admin_account_id=os.environ.get("ADMIN_ACCOUNT_ID", "amy"),
            admin_telegram_user_id=int(os.environ.get("ADMIN_TELEGRAM_USER_ID", "100000001")),
            closed_account_ids=frozenset(
                x.strip()
                for x in os.environ.get("CLOSED_ACCOUNT_IDS", "amy,beth").split(",")
                if x.strip()
            ),
            claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
            claude_workdir=os.environ.get("CLAUDE_WORKDIR", str(base_dir)),
            claude_model=os.environ.get("CLAUDE_MODEL"),
            claude_permission_mode=os.environ.get("CLAUDE_PERMISSION_MODE", "bypassPermissions"),
            claude_timeout_seconds=int(os.environ.get("CLAUDE_TIMEOUT_SECONDS", "600")),
            accounts_dir=Path(
                os.environ.get("ACCOUNTS_DIR", str(base_dir / "accounts"))
            ).resolve(),
            interests_reorganize_interval_seconds=int(
                os.environ.get("INTERESTS_REORGANIZE_INTERVAL_SECONDS", "86400")
            ),
            accounts_file=Path(
                os.environ.get("ACCOUNTS_FILE", str(base_dir / "accounts.hash"))
            ).resolve(),
            session_timeout_seconds=int(os.environ.get("SESSION_TIMEOUT_SECONDS", "900")),
            trivia_cache_file=base_dir / "trivia_cache.json",
            trivia_state_file=base_dir / "trivia_state.json",
            lta_api_key=os.environ.get("LTA_API_KEY"),
            bus_stops_cache_file=Path(
                os.environ.get("BUS_STOPS_CACHE_FILE", str(base_dir / "bus_stops_cache.json"))
            ).resolve(),
            bus_nearby_count=int(os.environ.get("BUS_NEARBY_COUNT", "3")),
            news_learning_enabled=os.environ.get("NEWS_LEARNING_ENABLED", "true").strip().lower()
            != "false",
            news_history_window_days=int(os.environ.get("NEWS_HISTORY_WINDOW_DAYS", "60")),
            news_history_max_entries=int(os.environ.get("NEWS_HISTORY_MAX_ENTRIES", "100")),
            news_recency_days=int(os.environ.get("NEWS_RECENCY_DAYS", "7")),
            message_logging_enabled=os.environ.get("MESSAGE_LOGGING_ENABLED", "true").strip().lower()
            != "false",
            message_logs_dir=Path(
                os.environ.get("MESSAGE_LOGS_DIR", str(base_dir / "message_logs"))
            ).resolve(),
        )
