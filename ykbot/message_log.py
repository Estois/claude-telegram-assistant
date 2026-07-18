"""Per-user message logging: one append-only file per Telegram user.

Files are named by the sender's @username (falling back to their numeric id
when they have no username), so each user's messages accumulate in their own
file. Only messages from authenticated (unlocked) chats are ever written —
see YKBot._log_message — so password attempts never reach disk.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from ykbot._logging import logger

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


class MessageLogStore:
    def __init__(self, logs_dir: Path):
        self._dir = logs_dir

    def log(self, username: Optional[str], user_id: int, text: str) -> None:
        path = self._dir / self._filename_for(username, user_id)
        line = f"{datetime.now().isoformat(timespec='seconds')}\t{text}\n"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with path.open("a") as handle:
                handle.write(line)
        except OSError:
            # Logging must never break message handling.
            logger.exception("Failed to write message log %s", path)

    @staticmethod
    def _filename_for(username: Optional[str], user_id: int) -> str:
        if username:
            safe = _SAFE_NAME.sub("_", username).strip("._") or str(user_id)
            return f"{safe}.log"
        return f"id_{user_id}.log"
