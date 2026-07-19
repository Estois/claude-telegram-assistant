import bot
from tests.helpers import AsyncBotTestCase, FakeContext, FakeUpdate, make_config


class ClaudeHelpersCallbackSafeTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    async def test_reply_to_claude_failure_uses_effective_message_when_message_none(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)
        update.message = None  # simulate a real callback-query update
        await b._reply_to_claude_failure(update, RuntimeError("boom"))
        self.assertTrue(update.effective_message.replies)  # replied, did not crash

    async def test_send_formatted_uses_effective_message_when_message_none(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)
        update.message = None  # simulate a real callback-query update
        await b._send_formatted(update, "hello there")
        self.assertTrue(update.effective_message.replies)  # sent, did not crash

    async def test_run_claude_busy_guard_uses_effective_message_when_message_none(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)
        lock = b._claude.lock_for(update.effective_chat.id)
        await lock.acquire()
        try:
            update.message = None  # simulate a real callback-query update
            context = FakeContext()
            result = await b._run_claude(update, context, "some prompt")
        finally:
            lock.release()
        self.assertIsNone(result)
        self.assertTrue(update.effective_message.replies)  # replied, did not crash
        self.assertIn(
            "Still working on your last request — hang on.",
            update.effective_message.replies,
        )
