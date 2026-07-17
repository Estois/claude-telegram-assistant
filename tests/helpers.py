import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path


class BotTestCase(unittest.TestCase):
    """Base class providing a scratch temp directory per test
    (self.tmp_path), cleaned up automatically after each test runs."""

    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, ignore_errors=True)


def write_accounts_file(path, accounts: dict) -> None:
    """Writes an accounts.hash-format file. accounts maps account id ->
    list of plaintext passwords (hashed here so tests stay readable)."""
    lines = []
    for account_id, passwords in accounts.items():
        lines.append(f"# account: {account_id}")
        for password in passwords:
            lines.append(hashlib.sha256(password.encode()).hexdigest())
    path.write_text("\n".join(lines) + "\n")
