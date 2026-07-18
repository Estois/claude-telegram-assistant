import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class NewsCaptureTests(AsyncBotTestCase):
    def _make_bot(self, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=1, attempt="hunter2")

        async def fake_run_claude(update, context, prompt):
            return None  # skip the real Claude subprocess

        news_bot._run_claude = fake_run_claude
        return news_bot

    async def test_news_request_is_logged(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)
        await news_bot.handle_news(update, FakeContext(args=["quantum", "computing"]))
        recent = news_bot._news_history_for("alice").recent(60, 100)
        self.assertEqual([r["subject"] for r in recent], ["quantum computing"])

    async def test_nothing_logged_when_learning_disabled(self):
        news_bot = self._make_bot(news_learning_enabled=False)
        update = FakeUpdate(user_id=1, chat_id=1)
        await news_bot.handle_news(update, FakeContext(args=["quantum", "computing"]))
        self.assertEqual(news_bot._news_history_for("alice").recent(60, 100), [])

    async def test_empty_news_request_is_not_logged(self):
        news_bot = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=1)
        await news_bot.handle_news(update, FakeContext(args=[]))  # usage message path
        self.assertEqual(news_bot._news_history_for("alice").recent(60, 100), [])
