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
    # Cap each per-user log so unbounded user input can't fill the disk. When
    # a file exceeds MAX_BYTES it's trimmed to roughly the most recent half.
    MAX_BYTES = 1_000_000

    def __init__(self, logs_dir: Path):
        self._dir = logs_dir

    def log(self, username: Optional[str], user_id: int, text: str) -> None:
        path = self._dir / self._filename_for(username, user_id)
        line = f"{datetime.now().isoformat(timespec='seconds')}\t{text}\n"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with path.open("a") as handle:
                handle.write(line)
            self._trim(path)
        except OSError:
            # Logging must never break message handling.
            logger.exception("Failed to write message log %s", path)

    def _trim(self, path: Path) -> None:
        if path.stat().st_size <= self.MAX_BYTES:
            return
        data = path.read_bytes()[-(self.MAX_BYTES // 2) :]
        # Drop the partial first line left by the byte-offset cut.
        newline = data.find(b"\n")
        if newline != -1:
            data = data[newline + 1 :]
        path.write_bytes(data)

    @staticmethod
    def _filename_for(username: Optional[str], user_id: int) -> str:
        if username:
            safe = _SAFE_NAME.sub("_", username).strip("._") or str(user_id)
            return f"{safe}.log"
        return f"id_{user_id}.log"
