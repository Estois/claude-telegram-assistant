"""Covers the last-resort error handler and the logging setup.

Both exist because failures were previously invisible: an uncaught handler
exception made a button silently do nothing, and httpx's INFO logging wrote
the bot token into bot.log on every poll.
"""

import logging
import unittest
from logging.handlers import RotatingFileHandler

import bot
from ykbot.app import configure_logging
from tests.helpers import (
    AsyncBotTestCase, BotTestCase, FakeContext, FakeUpdate, make_config,
)


class HandleErrorTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    async def test_replies_with_a_generic_notice(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        context = FakeContext()
        context.error = RuntimeError("boom")

        with self.assertLogs("ykbot", level="ERROR"):
            await b.handle_error(update, context)

        self.assertEqual(
            update.message.replies, ["Something went wrong on my end. Try again in a moment."]
        )

    async def test_notice_leaks_nothing_about_the_failure(self):
        """The text must not name the command or echo the exception — a
        locked user probing commands must learn nothing from it."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        context = FakeContext()
        context.error = RuntimeError("secret-command-name blew up")

        with self.assertLogs("ykbot", level="ERROR"):
            await b.handle_error(update, context)

        self.assertNotIn("secret-command-name", update.message.replies[0])

    async def test_callback_update_still_gets_the_notice(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2, callback_only=True, callback_data="home")
        context = FakeContext()
        context.error = RuntimeError("boom")

        with self.assertLogs("ykbot", level="ERROR"):
            await b.handle_error(update, context)

        self.assertEqual(len(update.effective_message.replies), 1)

    async def test_update_without_a_message_is_logged_and_dropped(self):
        """Some updates (poll answers, chat-member changes) have no message
        to reply to. Logging must still happen; replying must not explode."""
        b = self._make_bot()
        context = FakeContext()
        context.error = RuntimeError("boom")

        with self.assertLogs("ykbot", level="ERROR"):
            await b.handle_error(object(), context)

    async def test_failure_to_deliver_the_notice_is_swallowed(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        context = FakeContext()
        context.error = RuntimeError("boom")

        async def exploding_reply(text, **kwargs):
            raise RuntimeError("telegram is down")

        update.effective_message.reply_text = exploding_reply

        with self.assertLogs("ykbot", level="ERROR") as captured:
            await b.handle_error(update, context)

        self.assertIn("Failed to deliver the error notice", "\n".join(captured.output))

    async def test_error_handler_is_registered(self):
        b = self._make_bot()
        app = b._build_application()
        self.assertIn(b.handle_error, app.error_handlers)


class ConfigureLoggingTests(BotTestCase):
    def setUp(self):
        super().setUp()
        root = logging.getLogger()
        self._saved_handlers = root.handlers[:]
        self._saved_level = root.level
        self._saved_httpx = logging.getLogger("httpx").level
        self._saved_httpcore = logging.getLogger("httpcore").level

        def restore():
            for handler in logging.getLogger().handlers[:]:
                if handler not in self._saved_handlers:
                    handler.close()
            root.handlers = self._saved_handlers
            root.setLevel(self._saved_level)
            logging.getLogger("httpx").setLevel(self._saved_httpx)
            logging.getLogger("httpcore").setLevel(self._saved_httpcore)

        self.addCleanup(restore)

    def test_httpx_is_silenced_so_the_token_never_reaches_the_log(self):
        """httpx logs full request URLs at INFO and the Telegram API URL
        contains the bot token, so INFO here means the live credential in
        every log line."""
        configure_logging(self.tmp_path)

        self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertEqual(logging.getLogger("httpcore").level, logging.WARNING)

    def test_installs_a_rotating_file_handler(self):
        configure_logging(self.tmp_path)

        handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].maxBytes, bot.LOG_MAX_BYTES)
        self.assertEqual(handlers[0].backupCount, bot.LOG_BACKUP_COUNT)

    def test_writes_to_bot_log_in_the_given_directory(self):
        configure_logging(self.tmp_path)

        logging.getLogger("ykbot").warning("hello")

        self.assertIn("hello", (self.tmp_path / bot.LOG_FILENAME).read_text())


if __name__ == "__main__":
    unittest.main()
