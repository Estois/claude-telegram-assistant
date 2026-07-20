import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def home_data(markup):
    return None if markup is None else markup.inline_keyboard[0][0].callback_data


class CaptureButtonTests(AsyncBotTestCase):
    CHAT_ID = 61

    def _make_bot(self, account="alice", reply="News Digest — x"):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {account: ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return reply

        b._run_claude = fake_run_claude
        return b

    async def _consume(self, b, kind, text, payload=None, user_id=1):
        update = FakeUpdate(user_id=user_id, chat_id=self.CHAT_ID, text=text)
        context = FakeContext(user_data={"awaiting": {"kind": kind, "payload": payload or {}}})
        await b._consume_awaiting(update, context, self.CHAT_ID)
        return update

    async def test_news_from_capture_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "news_subject", "space")
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_slash_news_stays_bare(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_news(update, FakeContext(args=["space"]))

        self.assertIsNone(update.message.replies_markup[-1])

    async def test_summarize_from_capture_gets_button(self):
        b = self._make_bot(reply="a summary")
        update = await self._consume(b, "summarize_url", "https://example.com/a")
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_rejected_url_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "summarize_url", "http://127.0.0.1/secret")
        self.assertIn("public http(s) URL", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_interest_from_capture_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "add_interest", "sailing")
        self.assertEqual(b._interests_for("alice").list(), ["sailing"])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_update_interest_from_capture_gets_button(self):
        b = self._make_bot(reply="revised")
        b._interests_for("alice").add("original")
        update = await self._consume(b, "update_interest", "new wording", payload={"index": 1})
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_stale_update_index_gets_button(self):
        b = self._make_bot()
        update = await self._consume(b, "update_interest", "text", payload={"index": 7})
        self.assertIn("no longer exists", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_user_tele_id_from_capture_gets_button(self):
        b = self._make_bot(account="amy")
        update = await self._consume(b, "add_user_tele_id", "424242", user_id=100000001)
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")
