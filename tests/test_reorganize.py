import asyncio
import unittest

import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


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


class CompileInterestsCommandTests(AsyncBotTestCase):
    # Regression guard for the Task 4 signature change to
    # _build_reorganize_prompt(entries, theme_counts): handle_compile_interests
    # (the /compileinterests command) is a second live caller that was missed
    # when the caller update only touched _reorganize_account, and calling it
    # with the old one-arg signature raised TypeError.
    def _make_bot(self, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")
        return news_bot

    async def test_compile_interests_reorganizes_without_raising(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("alice")
        interests.add("games")
        interests.add("more games")

        async def fake_run_claude(update, context, prompt, chat_id=None):
            return "1. a\n2. b"

        news_bot._run_claude = fake_run_claude

        update = FakeUpdate(user_id=1, chat_id=1)
        await news_bot.handle_compile_interests(update, FakeContext(args=[]))

        self.assertEqual(news_bot._interests_for("alice").list(), ["a", "b"])


class ReorganizeLearningTests(AsyncBotTestCase):
    def _make_bot(self, **overrides):
        return bot.YKBot(make_config(self.tmp_path, **overrides))

    async def test_recurring_theme_and_counts_reach_the_prompt(self):
        news_bot = self._make_bot()
        history = news_bot._news_history_for("amy")
        for _ in range(3):
            history.append("quantum computing")
        history.append("obscure onetime")
        news_bot._interests_for("amy").add("games")  # only 1 interest, but history exists
        fake = FakeClaudeClient("1. games\n2. quantum computing")
        news_bot._claude = fake

        await news_bot._reorganize_account("amy")

        prompt = fake.calls[0][1]
        self.assertIn("quantum computing (requested 3x)", prompt)
        self.assertIn("obscure onetime (requested 1x)", prompt)
        self.assertEqual(
            news_bot._interests_for("amy").list(), ["games", "quantum computing"]
        )

    async def test_history_pruned_after_pass(self):
        news_bot = self._make_bot(news_history_window_days=60, news_history_max_entries=100)
        history = news_bot._news_history_for("amy")
        # one in-window, one ancient
        history.append("keep me")
        path = news_bot._account_dir("amy") / news_bot.NEWS_HISTORY_FILENAME
        import json, time
        with path.open("a") as h:
            h.write(json.dumps({"ts": time.time() - 999 * 86400, "subject": "ancient"}) + "\n")
        news_bot._interests_for("amy").add("a")
        news_bot._interests_for("amy").add("b")
        news_bot._claude = FakeClaudeClient("1. a\n2. b")

        await news_bot._reorganize_account("amy")

        self.assertNotIn("ancient", path.read_text())
        self.assertIn("keep me", path.read_text())

    async def test_skips_when_no_interests_and_no_history(self):
        news_bot = self._make_bot()
        fake = FakeClaudeClient("1. should not be used")
        news_bot._claude = fake
        await news_bot._reorganize_account("amy")
        self.assertEqual(fake.calls, [])

    async def test_disabled_learning_ignores_history(self):
        news_bot = self._make_bot(news_learning_enabled=False)
        history = news_bot._news_history_for("amy")
        for _ in range(3):
            history.append("quantum computing")
        news_bot._interests_for("amy").add("games")  # only 1 interest
        fake = FakeClaudeClient("1. should not be used")
        news_bot._claude = fake
        await news_bot._reorganize_account("amy")
        self.assertEqual(fake.calls, [])  # <2 interests and history ignored -> skip


class CompileInterestsLearningTests(AsyncBotTestCase):
    def _make_bot(self, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")
        return news_bot

    async def test_compile_folds_recurring_history_into_prompt(self):
        news_bot = self._make_bot()
        for _ in range(2):
            news_bot._news_history_for("alice").append("quantum computing")
        news_bot._interests_for("alice").add("games")
        news_bot._interests_for("alice").add("anime")
        captured = {}

        async def fake_run_claude(update, context, prompt, chat_id=None):
            captured["prompt"] = prompt
            return "1. games\n2. anime\n3. quantum computing"

        news_bot._run_claude = fake_run_claude
        await news_bot.handle_compile_interests(
            FakeUpdate(user_id=1, chat_id=1), FakeContext(args=[])
        )
        self.assertIn("quantum computing (requested 2x)", captured["prompt"])

    async def test_compile_ignores_history_when_disabled(self):
        news_bot = self._make_bot(news_learning_enabled=False)
        for _ in range(2):
            news_bot._news_history_for("alice").append("quantum computing")
        news_bot._interests_for("alice").add("games")
        news_bot._interests_for("alice").add("anime")
        captured = {}

        async def fake_run_claude(update, context, prompt, chat_id=None):
            captured["prompt"] = prompt
            return "1. games\n2. anime"

        news_bot._run_claude = fake_run_claude
        await news_bot.handle_compile_interests(
            FakeUpdate(user_id=1, chat_id=1), FakeContext(args=[])
        )
        self.assertNotIn("requested", captured["prompt"])


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
