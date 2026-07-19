import bot
from tests.helpers import (
    AsyncBotTestCase,
    BotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class _HandlerBase(AsyncBotTestCase):
    CHAT_ID = 5
    USER_ID = 100

    def _make_bot(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        return news_bot

    def _update(self):
        return FakeUpdate(user_id=self.USER_ID, chat_id=self.CHAT_ID)


class RemoveInterestTests(_HandlerBase):
    async def test_usage_shown_for_no_args(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_remove_interest(update, FakeContext(args=[]))
        self.assertIn("Usage: /removeinterest", update.message.replies[0])

    async def test_non_numeric_index_shows_usage(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_remove_interest(update, FakeContext(args=["two"]))
        self.assertIn("Usage: /removeinterest", update.message.replies[0])

    async def test_empty_list_reports_none_saved(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_remove_interest(update, FakeContext(args=["1"]))
        self.assertIn("No interests saved yet", update.message.replies[0])

    async def test_out_of_range_index_rejected(self):
        news_bot = self._make_bot()
        news_bot._interests_for("alice").add("only one")
        update = self._update()
        await news_bot.handle_remove_interest(update, FakeContext(args=["5"]))
        self.assertIn("No interest #5", update.message.replies[0])

    async def test_successful_remove(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("alice")
        interests.add("cybersecurity")
        interests.add("formula 1")
        update = self._update()

        await news_bot.handle_remove_interest(update, FakeContext(args=["1"]))

        self.assertIn('Removed interest #1: "cybersecurity"', update.message.replies[0])
        self.assertEqual(interests.list(), ["formula 1"])


class SummarizeTests(_HandlerBase):
    def _stub_claude(self, news_bot, result):
        async def fake(update, context, prompt, **kwargs):
            fake.calls.append(kwargs)
            return result

        fake.calls = []
        news_bot._run_claude = fake
        return fake

    async def test_usage_without_args(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_summarize(update, FakeContext(args=[]))
        self.assertIn("Usage: /summarize", update.message.replies[0])

    async def test_rejects_non_http_url(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_summarize(update, FakeContext(args=["ftp://example.com/x"]))
        self.assertIn("public http(s) URL", update.message.replies[0])

    async def test_rejects_internal_url_ssrf(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_summarize(update, FakeContext(args=["http://169.254.169.254/latest/meta-data"]))
        self.assertIn("public http(s) URL", update.message.replies[0])

    async def test_public_url_runs_claude_with_web_tools_and_sends(self):
        news_bot = self._make_bot()
        fake = self._stub_claude(news_bot, "A concise summary.")
        update = self._update()

        await news_bot.handle_summarize(update, FakeContext(args=["https://example.com/article"]))

        self.assertEqual(fake.calls[0].get("web_tools"), True)
        self.assertEqual(update.message.replies, ["A concise summary."])


class UrlGuardTests(BotTestCase):
    def _ok(self, url):
        return bot.YKBot._is_fetchable_url(url)

    def test_public_https_url_allowed(self):
        self.assertTrue(self._ok("https://example.com/page"))
        self.assertTrue(self._ok("http://news.bbc.co.uk/story"))

    def test_non_http_scheme_blocked(self):
        self.assertFalse(self._ok("ftp://example.com"))
        self.assertFalse(self._ok("file:///etc/passwd"))
        self.assertFalse(self._ok("notaurl"))

    def test_internal_hosts_blocked(self):
        for url in (
            "http://localhost/x",
            "http://app.localhost/x",
            "https://svc.internal/x",
            "http://127.0.0.1/x",
            "http://10.0.0.5/x",
            "http://192.168.1.1/x",
            "http://172.16.0.1/x",
            "http://169.254.169.254/latest",
            "http://[::1]/x",
        ):
            self.assertFalse(self._ok(url), url)


class SummarizePromptTests(BotTestCase):
    def test_prompt_asks_to_fetch_the_url(self):
        prompt = bot.YKBot._build_summarize_prompt("https://example.com/a")
        self.assertIn("https://example.com/a", prompt)
        self.assertIn("Fetch", prompt)
