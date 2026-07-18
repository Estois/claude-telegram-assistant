"""Account authentication and the Telegram-user allowlist."""

import hashlib
import re
import time
from pathlib import Path
from typing import Optional

from ykbot._logging import logger


class AccountStore:
    """Maps password hashes to account ids, and tracks which account each
    chat is currently unlocked as.

    One account can have multiple password hashes — e.g. one per device —
    and any of them unlocks that account, sharing its interests and bus
    locations. The password itself is never stored, only its SHA256 hex
    digest, read fresh from disk on every attempt so accounts can be
    added/rotated without a restart.
    """

    ACCOUNT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

    def __init__(self, accounts_file: Path, session_timeout_seconds: int):
        self._accounts_file = accounts_file
        self._session_timeout_seconds = session_timeout_seconds
        self._sessions: dict[int, tuple] = {}  # chat_id -> (account_id, expiry)

    @property
    def is_configured(self) -> bool:
        return any(self._load_accounts().values())

    def list_ids(self) -> list:
        return list(self._load_accounts().keys())

    def is_unlocked(self, chat_id: int) -> bool:
        session = self._sessions.get(chat_id)
        return session is not None and time.monotonic() < session[1]

    def account_for(self, chat_id: int) -> Optional[str]:
        """The account this chat is currently unlocked as, or None if
        locked/expired."""
        if not self.is_unlocked(chat_id):
            return None
        return self._sessions[chat_id][0]

    def has_active_session(self, account_id: str) -> bool:
        """Whether any chat is currently unlocked as this account (a chat
        can lock/unlock a different account than one it used before, and
        the same account can be unlocked from several chats at once)."""
        now = time.monotonic()
        return any(
            unlocked_account_id == account_id and expiry > now
            for unlocked_account_id, expiry in self._sessions.values()
        )

    def try_unlock(self, chat_id: int, attempt: str) -> Optional[str]:
        """Returns the account id the attempt unlocked, or None if it
        matched no account's hash."""
        digest = hashlib.sha256(attempt.encode()).hexdigest()
        for account_id, hashes in self._load_accounts().items():
            if digest in hashes:
                self._sessions[chat_id] = (account_id, time.monotonic() + self._session_timeout_seconds)
                return account_id
        return None

    def lock(self, chat_id: int) -> bool:
        """Ends the session for a chat immediately. Returns whether one was
        active."""
        was_unlocked = self.is_unlocked(chat_id)
        self._sessions.pop(chat_id, None)
        return was_unlocked

    def add_hash(self, account_id: str, password_hash: str) -> bool:
        """Adds a password hash to an account, creating it if it doesn't
        exist yet. Returns True if the account was newly created, False if
        the hash was added to (or already present on) an existing one."""
        header, sections = self._parse_sections()
        created = account_id not in sections
        hashes = sections.setdefault(account_id, [])
        digest = password_hash.strip().lower()
        if digest not in hashes:
            hashes.append(digest)
        self._write_sections(header, sections)
        return created

    def remove_account(self, account_id: str) -> bool:
        """Deletes an account's header and all its password hashes from the
        accounts file. Returns whether the account existed."""
        header, sections = self._parse_sections()
        if account_id not in sections:
            return False
        del sections[account_id]
        self._write_sections(header, sections)
        return True

    def _load_accounts(self) -> dict:
        _, sections = self._parse_sections()
        return {account_id: set(hashes) for account_id, hashes in sections.items()}

    def _parse_sections(self):
        """Parses the accounts file into (header_comment_lines, sections),
        where sections is an ordered {account_id: [hash, ...]} dict built
        from '# account: <id>' header lines followed by that account's
        password hash(es), one per line, until the next header. Comment
        lines before the first header are kept as-is so they survive a
        rewrite; hashes appearing before any header can't be attributed to
        an account and are dropped with a warning."""
        if not self._accounts_file.exists():
            return [], {}

        header: list = []
        sections: dict = {}
        current = None
        for raw_line in self._accounts_file.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("# account:"):
                candidate = line.split(":", 1)[1].strip()
                if not self.ACCOUNT_ID_PATTERN.match(candidate):
                    logger.warning("Ignoring invalid account id in %s: %r", self._accounts_file, candidate)
                    current = None
                    continue
                current = candidate
                sections.setdefault(current, [])
                continue
            if line.startswith("#"):
                if current is None:
                    header.append(raw_line)
                continue
            if current is None:
                logger.warning(
                    "Password hash in %s before any '# account:' header, ignoring", self._accounts_file
                )
                continue
            if line.lower() not in sections[current]:
                sections[current].append(line.lower())
        return header, sections

    def _write_sections(self, header: list, sections: dict) -> None:
        blocks = []
        if header:
            blocks.append("\n".join(header))
        for account_id, hashes in sections.items():
            blocks.append("\n".join([f"# account: {account_id}"] + hashes))
        self._accounts_file.write_text("\n\n".join(blocks) + "\n")



class AllowlistStore:
    """Tracks which Telegram user ids may talk to the bot at all — the
    layer above accounts, checked before the password gate even applies.

    Held in memory for immediate effect (so /adduserteleid works without a
    restart) and persisted back to the TELEGRAM_ALLOWED_USER_IDS line in
    the .env file so it survives one. An empty/unset allowlist means wide
    open, matching Config.allowed_user_ids' existing meaning.
    """

    def __init__(self, env_file: Path, initial_ids: Optional[frozenset]):
        self._env_file = env_file
        self._ids: set = set(initial_ids) if initial_ids else set()

    @property
    def ids(self) -> Optional[frozenset]:
        return frozenset(self._ids) if self._ids else None

    def is_allowed(self, user_id: int) -> bool:
        return not self._ids or user_id in self._ids

    def add(self, user_id: int) -> bool:
        """Returns True if the id was newly added, False if it was already
        allowed."""
        if user_id in self._ids:
            return False
        self._ids.add(user_id)
        self._persist()
        return True

    def _persist(self) -> None:
        ids_line = f"TELEGRAM_ALLOWED_USER_IDS={','.join(str(i) for i in sorted(self._ids))}"
        if not self._env_file.exists():
            self._env_file.write_text(ids_line + "\n")
            return

        lines = self._env_file.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("TELEGRAM_ALLOWED_USER_IDS="):
                lines[i] = ids_line
                break
        else:
            lines.append(ids_line)
        self._env_file.write_text("\n".join(lines) + "\n")

