"""Per-account append-only log of /news request subjects.

One JSON object per line: {"ts": <unix seconds float>, "subject": "<text>"}.
Read paths tolerate a missing file and skip malformed lines rather than crash,
matching the other per-account stores.
"""

import json
import time
from pathlib import Path

from ykbot._logging import logger


class NewsHistoryStore:
    def __init__(self, path: Path):
        self._path = path

    def append(self, subject: str) -> None:
        subject = " ".join(subject.split())
        if not subject:
            return
        record = {"ts": time.time(), "subject": subject}
        with self._path.open("a") as handle:
            handle.write(json.dumps(record) + "\n")

    def recent(self, window_days: int, max_entries: int) -> list:
        """Records newer than window_days, capped to the most recent
        max_entries (records are stored in append/chronological order).

        A max_entries of 0 or less means no cap — every in-window record is
        returned."""
        cutoff = time.time() - window_days * 86400
        within = [r for r in self._read() if r.get("ts", 0) >= cutoff]
        return within[-max_entries:] if max_entries > 0 else within

    def theme_counts(self, window_days: int, max_entries: int) -> list:
        """Recent subjects lowercased and aggregated into (subject, count)
        pairs, most frequent first (ties broken alphabetically)."""
        counts: dict = {}
        for record in self.recent(window_days, max_entries):
            key = record["subject"].lower()
            counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def prune(self, window_days: int, max_entries: int) -> None:
        if not self._path.exists():
            return
        kept = self.recent(window_days, max_entries)
        self._path.write_text("".join(json.dumps(r) + "\n" for r in kept))

    def _read(self) -> list:
        if not self._path.exists():
            return []
        records = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.exception("Skipping malformed line in %s", self._path)
                continue
            if isinstance(record, dict) and "subject" in record and "ts" in record:
                records.append(record)
            else:
                logger.warning(
                    "Skipping structurally-invalid record in %s: %r",
                    self._path,
                    record,
                )
        return records
