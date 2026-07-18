"""The numbered interests.md list read by /news."""

import re
from pathlib import Path


class InterestsStore:
    """Manages a flat, numbered list of interest entries in interests.md,
    read by /news to bias digest topics and framing."""

    HEADER = (
        "# News Interests\n\n"
        "Read by /news to bias digest topic selection and framing. Managed "
        "via /addinterests, /getinterests, /updateinterests.\n\n"
    )
    ENTRY_PATTERN = re.compile(r"^\d+\.\s+(.*)$")

    def __init__(self, path: Path):
        self._path = path

    def list(self) -> list:
        if not self._path.exists():
            return []
        return self.parse_entries(self._path.read_text())

    def add(self, text: str) -> int:
        """Appends a new entry and returns its 1-based index."""
        entries = self.list()
        entries.append(self._normalize(text))
        self._write(entries)
        return len(entries)

    def update(self, index: int, text: str) -> bool:
        """Replaces the entry at the given 1-based index. Returns False if
        the index is out of range."""
        entries = self.list()
        if not 1 <= index <= len(entries):
            return False
        entries[index - 1] = self._normalize(text)
        self._write(entries)
        return True

    def replace_all(self, entries: list) -> None:
        """Overwrites the whole list, e.g. after a reorganization pass."""
        self._write([self._normalize(entry) for entry in entries])

    @classmethod
    def parse_entries(cls, text: str) -> list:
        """Extracts entries from arbitrary 'N. text' lines, e.g. Claude's
        raw reply to a reorganize prompt."""
        entries = []
        for line in text.splitlines():
            match = cls.ENTRY_PATTERN.match(line.strip())
            if match:
                entries.append(match.group(1))
        return entries

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.split())

    def _write(self, entries: list) -> None:
        body = "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))
        self._path.write_text(self.HEADER + body + ("\n" if body else ""))

