"""The recipes.json store, written by /addrecipe (Closed-group only)."""

import json
from pathlib import Path
from typing import List, Optional


class RecipeStore:
    """Manages a numbered list of {"name", "body"} recipe entries, mirroring
    InterestsStore's add/list/update/remove-by-1-based-index API. Structured
    entries (rather than interests.md's flat single-line-per-entry format)
    because a recipe body is multi-line and interests.md's normalizer
    collapses everything to one line."""

    def __init__(self, path: Path):
        self._path = path

    def list(self) -> List[dict]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text())

    def add(self, name: str, body: str) -> int:
        """Appends a new entry and returns its 1-based index."""
        entries = self.list()
        entries.append({"name": name, "body": body})
        self._write(entries)
        return len(entries)

    def update(self, index: int, name: str, body: str) -> bool:
        """Replaces the entry at the given 1-based index. Returns False if
        the index is out of range."""
        entries = self.list()
        if not 1 <= index <= len(entries):
            return False
        entries[index - 1] = {"name": name, "body": body}
        self._write(entries)
        return True

    def remove(self, index: int) -> Optional[dict]:
        """Deletes the entry at the given 1-based index and returns it, or
        None if the index is out of range. Remaining entries renumber."""
        entries = self.list()
        if not 1 <= index <= len(entries):
            return None
        removed = entries.pop(index - 1)
        self._write(entries)
        return removed

    def _write(self, entries: List[dict]) -> None:
        self._path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
