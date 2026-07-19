import bot
from tests.helpers import BotTestCase


class MessageLogStoreTests(BotTestCase):
    def _store(self):
        return bot.MessageLogStore(self.tmp_path / "message_logs")

    def test_oversized_log_is_trimmed_to_the_recent_half(self):
        store = self._store()
        store.MAX_BYTES = 2000  # small cap for the test
        path = self.tmp_path / "message_logs" / "bob.log"

        for i in range(500):
            store.log("bob", 111, f"message number {i}")

        size = path.stat().st_size
        self.assertLessEqual(size, store.MAX_BYTES)
        text = path.read_text()
        self.assertIn("message number 499", text)  # most recent kept
        self.assertNotIn("message number 0\n", text)  # oldest dropped
        self.assertTrue(text.startswith("2"))  # trimmed on a clean line boundary (ISO ts)

    def test_logs_to_username_file_with_the_text(self):
        store = self._store()

        store.log("alice", 111, "/news tokyo")

        contents = (self.tmp_path / "message_logs" / "alice.log").read_text()
        self.assertIn("/news tokyo", contents)

    def test_falls_back_to_user_id_file_without_a_username(self):
        store = self._store()

        store.log(None, 100000001, "hello")

        path = self.tmp_path / "message_logs" / "id_100000001.log"
        self.assertTrue(path.exists())
        self.assertIn("hello", path.read_text())

    def test_sanitizes_unsafe_username_characters(self):
        store = self._store()

        store.log("../../etc/passwd", 1, "x")

        files = [p.name for p in (self.tmp_path / "message_logs").iterdir()]
        self.assertEqual(len(files), 1)
        self.assertNotIn("/", files[0])
        self.assertTrue(files[0].endswith(".log"))

    def test_appends_successive_messages(self):
        store = self._store()

        store.log("alice", 111, "first")
        store.log("alice", 111, "second")

        lines = (self.tmp_path / "message_logs" / "alice.log").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("first", lines[0])
        self.assertIn("second", lines[1])

    def test_creates_the_logs_directory(self):
        self.assertFalse((self.tmp_path / "message_logs").exists())

        self._store().log("alice", 111, "hi")

        self.assertTrue((self.tmp_path / "message_logs").is_dir())

    def test_write_failure_is_swallowed(self):
        # A plain file where the logs directory should be makes mkdir fail;
        # logging must never raise into message handling.
        (self.tmp_path / "message_logs").write_text("i am a file, not a dir")
        store = self._store()

        store.log("alice", 111, "hi")  # must not raise

    def test_line_is_timestamped_and_tab_separated(self):
        store = self._store()

        store.log("alice", 111, "the message")

        line = (self.tmp_path / "message_logs" / "alice.log").read_text().rstrip("\n")
        stamp, text = line.split("\t", 1)
        self.assertEqual(text, "the message")
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
