import asyncio
import unittest

import bot
from tests.helpers import AsyncBotTestCase, make_config


class FakeClaudeClient:
    """Stand-in for ClaudeClient used by the reorganize path: hands back a
    canned reorganized list and records the prompts it was asked to run."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def lock_for(self, chat_id):
        return asyncio.Lock()

    async def run(self, chat_id, prompt):
        self.calls.append((chat_id, prompt))
        return self._result


class ReorganizeAccountTests(AsyncBotTestCase):
    def _make_bot(self, **overrides):
        return bot.YKBot(make_config(self.tmp_path, **overrides))

    async def test_reorganize_rewrites_the_interests_list(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("amy")
        interests.add("games")
        interests.add("more games")
        interests.add("cybersecurity")
        news_bot._claude = FakeClaudeClient("1. games and consoles\n2. cybersecurity")

        await news_bot._reorganize_account("amy")

        self.assertEqual(news_bot._interests_for("amy").list(), ["games and consoles", "cybersecurity"])

    async def test_reorganize_skips_accounts_with_fewer_than_two_entries(self):
        news_bot = self._make_bot()
        news_bot._interests_for("amy").add("only one")
        fake = FakeClaudeClient("1. should not be used")
        news_bot._claude = fake

        await news_bot._reorganize_account("amy")

        self.assertEqual(fake.calls, [])
        self.assertEqual(news_bot._interests_for("amy").list(), ["only one"])

    async def test_reorganize_leaves_list_untouched_when_reply_has_no_entries(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("amy")
        interests.add("games")
        interests.add("cybersecurity")
        news_bot._claude = FakeClaudeClient("Sorry, I can't help with that.")

        await news_bot._reorganize_account("amy")

        self.assertEqual(news_bot._interests_for("amy").list(), ["games", "cybersecurity"])

    async def test_reorganize_account_takes_no_app_and_broadcasts_nothing(self):
        # Regression guard: the reorg result is logged, never pushed to
        # Telegram users, so the method must not accept an Application/bot to
        # send through. Configuring several allowlisted users changes nothing.
        news_bot = self._make_bot(allowed_user_ids=frozenset({1, 2, 3}))
        interests = news_bot._interests_for("amy")
        interests.add("games")
        interests.add("cybersecurity")
        news_bot._claude = FakeClaudeClient("1. games\n2. cybersecurity")

        # Called with account_id only — no app argument exists to broadcast with.
        await news_bot._reorganize_account("amy")

        self.assertEqual(news_bot._interests_for("amy").list(), ["games", "cybersecurity"])


class ReorganizePromptTests(unittest.TestCase):
    def test_no_history_block_when_theme_counts_empty(self):
        prompt = bot.YKBot._build_reorganize_prompt(["games", "anime"], [])
        self.assertIn("1. games", prompt)
        self.assertNotIn("requested", prompt)

    def test_history_block_lists_counts_and_recurrence_rule(self):
        prompt = bot.YKBot._build_reorganize_prompt(
            ["games"], [("quantum computing", 3), ("obscure onetime", 1)]
        )
        self.assertIn("quantum computing (requested 3x)", prompt)
        self.assertIn("obscure onetime (requested 1x)", prompt)
        self.assertIn("recurs", prompt)
        self.assertIn("never add a one-off", prompt)
        self.assertIn("Do not remove or override", prompt)


if __name__ == "__main__":
    unittest.main()
