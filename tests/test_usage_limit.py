import asyncio
import unittest

import bot
from tests.helpers import AsyncBotTestCase, FakeContext, FakeUpdate, make_config


class FakeClaudeClient:
    """Stand-in for ClaudeClient: returns a canned result or raises a canned
    exception instead of actually invoking the claude CLI."""

    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def lock_for(self, chat_id):
        return asyncio.Lock()

    async def run(self, chat_id, prompt):
        if self._exc is not None:
            raise self._exc
        return self._result


class UsageLimitResetNoticeTests(unittest.TestCase):
    def test_returns_none_when_no_usage_limit_mentioned(self):
        self.assertIsNone(bot.NewsBot._usage_limit_reset_notice("Here is your news digest."))

    def test_extracts_reset_time_after_reset_at(self):
        text = "Claude AI usage limit reached. Your limit will reset at 9am (Asia/Singapore)."

        self.assertEqual(bot.NewsBot._usage_limit_reset_notice(text), "9am (Asia/Singapore)")

    def test_extracts_reset_time_after_resets_in(self):
        text = "Your usage limit resets in 4 hours."

        self.assertEqual(bot.NewsBot._usage_limit_reset_notice(text), "4 hours")

    def test_falls_back_to_soon_when_reset_time_not_parseable(self):
        text = "You have hit your usage limit."

        self.assertEqual(bot.NewsBot._usage_limit_reset_notice(text), "soon")

    def test_matching_is_case_insensitive(self):
        text = "USAGE LIMIT reached, RESETS AT 3pm."

        self.assertEqual(bot.NewsBot._usage_limit_reset_notice(text), "3pm")


class BuildUsageLimitMessageTests(unittest.TestCase):
    def test_includes_the_reset_notice_and_mentions_usage_limit(self):
        message = bot.NewsBot._build_usage_limit_message("9am (Asia/Singapore)")

        self.assertIn("9am (Asia/Singapore)", message)
        self.assertIn("usage limit", message.lower())


class RunClaudeUsageLimitTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.NewsBot(make_config(self.tmp_path))

    async def test_successful_reply_mentioning_usage_limit_is_replaced(self):
        news_bot = self._make_bot()
        news_bot._claude = FakeClaudeClient(
            result="I couldn't finish the web search — usage limit reached. It resets at 5pm UTC."
        )
        update = FakeUpdate(user_id=1, chat_id=1)

        reply = await news_bot._run_claude(update, FakeContext(), "some prompt")

        self.assertIsNone(reply)
        self.assertEqual(len(update.message.replies), 1)
        sent = update.message.replies[0]
        self.assertNotIn("web search", sent)
        self.assertIn("5pm UTC", sent)

    async def test_raised_usage_limit_error_is_replaced(self):
        news_bot = self._make_bot()
        news_bot._claude = FakeClaudeClient(
            exc=RuntimeError("Claude usage limit reached. Resets in 2 hours.")
        )
        update = FakeUpdate(user_id=1, chat_id=1)

        reply = await news_bot._run_claude(update, FakeContext(), "some prompt")

        self.assertIsNone(reply)
        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("2 hours", update.message.replies[0])

    async def test_non_usage_limit_error_keeps_the_original_generic_wording(self):
        news_bot = self._make_bot()
        news_bot._claude = FakeClaudeClient(exc=RuntimeError("network unreachable"))
        update = FakeUpdate(user_id=1, chat_id=1)

        reply = await news_bot._run_claude(update, FakeContext(), "some prompt")

        self.assertIsNone(reply)
        self.assertEqual(update.message.replies, ["Something went wrong: network unreachable"])

    async def test_normal_reply_without_usage_limit_wording_passes_through_unchanged(self):
        news_bot = self._make_bot()
        news_bot._claude = FakeClaudeClient(result="Here is your digest.")
        update = FakeUpdate(user_id=1, chat_id=1)

        reply = await news_bot._run_claude(update, FakeContext(), "some prompt")

        self.assertEqual(reply, "Here is your digest.")
        self.assertEqual(update.message.replies, [])


if __name__ == "__main__":
    unittest.main()
