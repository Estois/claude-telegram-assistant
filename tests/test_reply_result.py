from telegram.error import BadRequest

import bot
from ykbot.formatting import TelegramFormatter
from tests.helpers import AsyncBotTestCase, FakeUpdate, make_config


class ReplyResultTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    async def test_attaches_home_button_when_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._reply_result(update, "done", True)

        markup = update.message.replies_markup[-1]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "home")

    async def test_no_button_when_not_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._reply_result(update, "done", False)

        self.assertEqual(update.message.replies, ["done"])
        self.assertIsNone(update.message.replies_markup[-1])

    async def test_passes_through_extra_kwargs(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        seen = {}

        async def capture(text, **kwargs):
            seen.update(kwargs)

        update.effective_message.reply_text = capture

        await b._reply_result(update, "done", False, parse_mode="HTML")

        self.assertEqual(seen["parse_mode"], "HTML")


class SendFormattedButtonTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    def _long_text(self):
        """Three paragraphs, each just under the limit, so chunk() returns 3."""
        para = "x" * (TelegramFormatter.MESSAGE_LIMIT - 100)
        return "\n\n".join([para, para, para])

    async def test_button_only_on_the_last_chunk(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        text = self._long_text()
        self.assertEqual(len(TelegramFormatter.chunk(text)), 3)

        await b._send_formatted(update, text, True)

        markups = update.message.replies_markup
        self.assertEqual(len(markups), 3)
        self.assertIsNone(markups[0])
        self.assertIsNone(markups[1])
        self.assertEqual(markups[2].inline_keyboard[0][0].callback_data, "home")

    async def test_no_button_when_not_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._send_formatted(update, "short", False)

        self.assertEqual(update.message.replies_markup, [None])

    async def test_defaults_to_no_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)

        await b._send_formatted(update, "short")

        self.assertEqual(update.message.replies_markup, [None])

    async def test_button_survives_the_plain_text_fallback(self):
        """A reply whose HTML doesn't parse still has to come back with its
        button — the fallback path must carry the markup too."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=2)
        calls = []
        original = update.message.reply_text

        async def flaky(text, **kwargs):
            calls.append(kwargs)
            if kwargs.get("parse_mode") is not None:
                raise BadRequest("bad html")
            await original(text, **kwargs)

        update.effective_message.reply_text = flaky

        await b._send_formatted(update, "<b>oops", True)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["reply_markup"].inline_keyboard[0][0].callback_data, "home")
