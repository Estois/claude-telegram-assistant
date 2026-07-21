import bot
from telegram.error import BadRequest
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeMessage,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class _HtmlRejectingMessage(FakeMessage):
    """Rejects the first (HTML) send like Telegram does for bad markup, so
    _send_formatted's plain-text fallback path is exercised."""

    async def reply_text(self, text, **kwargs):
        if "parse_mode" in kwargs:
            raise BadRequest("can't parse entities")
        self.replies.append(text)


class HandlerTestBase(AsyncBotTestCase):
    CHAT_ID = 9
    USER_ID = 100

    def _make_bot(self, unlock=True, **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        if unlock:
            news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        return news_bot

    def _update(self, **kwargs):
        return FakeUpdate(user_id=self.USER_ID, chat_id=self.CHAT_ID, **kwargs)

    def _stub_claude(self, news_bot, result):
        async def fake_run_claude(update, context, prompt, **kwargs):
            return result

        news_bot._run_claude = fake_run_claude


class UpdateInterestsTests(HandlerTestBase):
    async def test_usage_shown_with_too_few_args(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_update_interests(update, FakeContext(args=["1"]))

        self.assertIn("Usage: /updateinterests", update.message.replies[0])

    async def test_non_numeric_index_rejected(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_update_interests(update, FakeContext(args=["two", "x"]))

        self.assertIn("Index must be a number", update.message.replies[0])

    async def test_out_of_range_index_rejected(self):
        news_bot = self._make_bot()
        news_bot._interests_for("alice").add("only one")
        update = self._update()

        await news_bot.handle_update_interests(update, FakeContext(args=["5", "x"]))

        self.assertIn("No interest #5", update.message.replies[0])

    async def test_successful_update_replaces_entry(self):
        news_bot = self._make_bot()
        news_bot._interests_for("alice").add("old topic")
        self._stub_claude(news_bot, "refined topic")
        update = self._update()

        await news_bot.handle_update_interests(update, FakeContext(args=["1", "refine it"]))

        self.assertEqual(update.message.replies, ["Updated interest #1."])
        self.assertEqual(news_bot._interests_for("alice").list(), ["refined topic"])


class CompileInterestsTests(HandlerTestBase):
    async def test_guard_requires_at_least_two_interests(self):
        news_bot = self._make_bot()
        news_bot._interests_for("alice").add("only one")
        update = self._update()

        await news_bot.handle_compile_interests(update, FakeContext())

        self.assertIn("Need at least 2 interests", update.message.replies[0])

    async def test_reorganizes_and_reports_new_count(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("alice")
        interests.add("cybersecurity")
        interests.add("cyber security")
        self._stub_claude(news_bot, "1. cybersecurity")
        update = self._update()

        await news_bot.handle_compile_interests(update, FakeContext())

        self.assertIn("Reorganized interests", update.message.replies[0])
        self.assertEqual(interests.list(), ["cybersecurity"])


class FormatArrivalsBranchTests(HandlerTestBase):
    async def test_lookup_error_reports_try_again(self):
        news_bot = self._make_bot()

        class Boom:
            is_configured = True

            async def get_arrivals(self, code):
                raise RuntimeError("network down")

        object.__setattr__(news_bot.services, "lta", Boom())

        text = await news_bot._bus._format_arrivals("46009")

        self.assertIn("Couldn't fetch arrival times", text)

    async def test_no_services_reports_no_data(self):
        news_bot = self._make_bot()

        class Empty:
            is_configured = True

            async def get_arrivals(self, code):
                return []

        object.__setattr__(news_bot.services, "lta", Empty())

        text = await news_bot._bus._format_arrivals("46009")

        self.assertIn("No bus data available", text)


class RunClaudeTests(HandlerTestBase):
    async def test_busy_lock_asks_user_to_wait(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot._claude.lock_for(self.CHAT_ID).acquire()

        result = await news_bot._run_claude(update, FakeContext(), "prompt")

        self.assertIsNone(result)
        self.assertIn("Still working", update.message.replies[0])

    async def test_busy_lock_does_not_also_send_the_processing_notice(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot._claude.lock_for(self.CHAT_ID).acquire()

        await news_bot._run_claude(
            update, FakeContext(), "prompt", processing_notice="Working on it…"
        )

        self.assertEqual(update.message.replies, ["Still working on your last request — hang on."])

    async def test_processing_notice_sent_before_the_claude_call(self):
        news_bot = self._make_bot()

        async def fake_run(prompt, web_tools=False):
            return "the answer"

        news_bot._claude.run = fake_run
        update = self._update()

        await news_bot._run_claude(
            update, FakeContext(), "prompt", processing_notice="Working on it…"
        )

        self.assertEqual(update.message.replies, ["Working on it…"])

    async def test_no_processing_notice_by_default(self):
        news_bot = self._make_bot()

        async def fake_run(prompt, web_tools=False):
            return "the answer"

        news_bot._claude.run = fake_run
        update = self._update()

        await news_bot._run_claude(update, FakeContext(), "prompt")

        self.assertEqual(update.message.replies, [])

    async def test_max_wait_minutes_derives_from_config(self):
        news_bot = self._make_bot(claude_timeout_seconds=125)
        self.assertEqual(news_bot._max_wait_minutes(), 2)

    async def test_max_wait_minutes_floors_at_one(self):
        news_bot = self._make_bot(claude_timeout_seconds=30)
        self.assertEqual(news_bot._max_wait_minutes(), 1)

    async def test_success_returns_claude_reply(self):
        news_bot = self._make_bot()

        async def fake_run(prompt, web_tools=False):
            return "the answer"

        news_bot._claude.run = fake_run
        update = self._update()

        result = await news_bot._run_claude(update, FakeContext(), "prompt")

        self.assertEqual(result, "the answer")

    async def test_failure_replies_and_returns_none(self):
        news_bot = self._make_bot()

        async def boom(prompt, web_tools=False):
            raise RuntimeError("kaboom")

        news_bot._claude.run = boom
        update = self._update()

        result = await news_bot._run_claude(update, FakeContext(), "prompt")

        self.assertIsNone(result)
        self.assertIn("went wrong", update.message.replies[0].lower())
        self.assertNotIn("kaboom", update.message.replies[0])


class ClaudeFailureReplyTests(HandlerTestBase):
    async def test_usage_limit_produces_reset_notice(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot._reply_to_claude_failure(
            update, RuntimeError("Claude usage limit reached, resets at 3pm")
        )

        self.assertIn("usage limit has been reached", update.message.replies[0])

    async def test_generic_error_is_surfaced_plainly(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot._reply_to_claude_failure(update, RuntimeError("disk full"))

        self.assertIn("went wrong", update.message.replies[0].lower())
        self.assertNotIn("disk full", update.message.replies[0])


class SendFormattedTests(HandlerTestBase):
    async def test_html_chunk_is_sent(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot._send_formatted(update, "hello world")

        self.assertEqual(update.message.replies, ["hello world"])

    async def test_falls_back_to_plain_text_when_html_is_rejected(self):
        news_bot = self._make_bot()
        update = self._update()
        update.message = _HtmlRejectingMessage()
        update.effective_message = update.message

        await news_bot._send_formatted(update, "<b>oops")

        self.assertEqual(update.message.replies, ["<b>oops"])


class MiscBranchTests(HandlerTestBase):
    async def test_bus_request_location_shows_instructions(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_request_location")

        await news_bot._bus.handle_bus_request_location(update, FakeContext())

        self.assertIn("Reply to this message with your location", update.callback_query.last_text)

    async def test_bus_callback_when_locked_gives_trivia_not_arrivals(self):
        news_bot = self._make_bot(unlock=False)
        update = self._update(callback_data="bus_stop:46009")

        await news_bot._bus.handle_bus_callback(update, FakeContext())

        self.assertIn("fact:", update.message.replies[0])
        self.assertEqual(update.callback_query.edits, [])

    async def test_end_session_ends_an_active_session(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_end_session(update, FakeContext())

        self.assertEqual(update.message.replies, ["Session ended."])
        self.assertIsNone(news_bot._accounts.account_for(self.CHAT_ID))

    async def test_adduser_rejects_an_illegal_account_name(self):
        news_bot = bot.YKBot(
            make_config(self.tmp_path, admin_account_id="amy", admin_telegram_user_id=self.USER_ID)
        )
        write_accounts_file(news_bot._config.accounts_file, {"amy": ["adminpass"]})
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="adminpass")
        update = self._update()

        await news_bot.handle_add_user(update, FakeContext(args=["bad name", "a" * 64]))

        self.assertIn("Name must be", update.message.replies[0])

    async def test_removeuser_deletes_an_existing_non_admin_account(self):
        news_bot = bot.YKBot(
            make_config(self.tmp_path, admin_account_id="amy", admin_telegram_user_id=self.USER_ID)
        )
        write_accounts_file(
            news_bot._config.accounts_file, {"amy": ["adminpass"], "bob": ["bobpass"]}
        )
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="adminpass")
        update = self._update()

        await news_bot.handle_remove_user(update, FakeContext(args=["bob"]))

        self.assertIn("Removed account 'bob'", update.message.replies[0])
        self.assertNotIn("bob", news_bot._accounts.list_ids())
