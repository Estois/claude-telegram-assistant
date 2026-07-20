import asyncio
from unittest import mock

import bot
import ykbot.app as app_module
from tests.helpers import (
    AsyncBotTestCase,
    BotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class RunAndBuildTests(BotTestCase):
    def test_run_warns_and_starts_polling(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        started = {"polled": False}

        class FakeApp:
            def run_polling(self):
                started["polled"] = True

        news_bot._build_application = lambda: FakeApp()

        news_bot.run()

        self.assertTrue(started["polled"])

    def test_warns_when_allowlist_unset(self):
        news_bot = bot.YKBot(make_config(self.tmp_path, allowed_user_ids=None))

        with self.assertLogs("ykbot", level="WARNING") as captured:
            news_bot._warn_about_missing_config()

        self.assertTrue(any("TELEGRAM_ALLOWED_USER_IDS" in m for m in captured.output))

    def test_build_application_registers_handlers(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))

        app = news_bot._build_application()

        self.assertTrue(app.handlers[0])  # group 0 has registered handlers

    def test_main_builds_from_env_and_runs(self):
        with mock.patch.object(
            app_module.Config, "from_env", return_value=make_config(self.tmp_path)
        ), mock.patch.object(app_module.YKBot, "run", lambda self: None):
            app_module.main()  # should not raise


class InterestsNoneBranchTests(AsyncBotTestCase):
    CHAT_ID = 12
    USER_ID = 100

    def _make_bot(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        return news_bot

    def _update(self):
        return FakeUpdate(user_id=self.USER_ID, chat_id=self.CHAT_ID)

    def _stub_run_claude(self, news_bot, result):
        async def fake(update, context, prompt, **kwargs):
            return result

        news_bot._run_claude = fake

    async def test_get_interests_lists_saved_entries(self):
        news_bot = self._make_bot()
        news_bot._interests_for("alice").add("cybersecurity")
        update = self._update()

        await news_bot.handle_get_interests(update, FakeContext())

        self.assertIn("1. cybersecurity", update.message.replies[0])

    async def test_update_interests_reports_empty_list(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_update_interests(update, FakeContext(args=["1", "x"]))

        self.assertIn("No interests saved yet", update.message.replies[0])

    async def test_update_interests_stops_when_claude_returns_none(self):
        news_bot = self._make_bot()
        news_bot._interests_for("alice").add("old")
        self._stub_run_claude(news_bot, None)
        update = self._update()

        await news_bot.handle_update_interests(update, FakeContext(args=["1", "refine"]))

        self.assertEqual(update.message.replies, [])  # no "Updated" reply
        self.assertEqual(news_bot._interests_for("alice").list(), ["old"])

    async def test_compile_interests_stops_when_claude_returns_none(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("alice")
        interests.add("a")
        interests.add("b")
        self._stub_run_claude(news_bot, None)
        update = self._update()

        await news_bot.handle_compile_interests(update, FakeContext())

        self.assertEqual(update.message.replies, [])

    async def test_compile_interests_reports_unusable_reorganize(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("alice")
        interests.add("a")
        interests.add("b")
        self._stub_run_claude(news_bot, "nothing parseable here")
        update = self._update()

        await news_bot.handle_compile_interests(update, FakeContext())

        self.assertIn("returned nothing usable", update.message.replies[0])
        self.assertEqual(interests.list(), ["a", "b"])  # unchanged


class AdminAndBusGuardTests(AsyncBotTestCase):
    CHAT_ID = 13
    USER_ID = 100

    def _make_bot(self):
        # Account exists but this chat stays locked, so _require_admin /
        # _require_session both short-circuit.
        news_bot = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        return news_bot

    def _update(self, **kwargs):
        return FakeUpdate(user_id=self.USER_ID, chat_id=self.CHAT_ID, **kwargs)

    async def test_remove_user_rejects_non_admin(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_remove_user(update, FakeContext(args=["bob"]))

        self.assertIn("fact:", update.message.replies[0])

    async def test_adduserteleid_rejects_non_admin(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_add_user_tele_id(update, FakeContext(args=["123"]))

        self.assertIn("fact:", update.message.replies[0])

    async def test_bus_locked_gives_trivia(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot._bus.handle_bus(update, FakeContext())

        self.assertIn("fact:", update.message.replies[0])

    async def test_bus_request_location_locked_answers_and_gives_trivia(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_request_location")

        await news_bot._bus.handle_bus_request_location(update, FakeContext())

        self.assertIn("fact:", update.message.replies[0])
        self.assertEqual(update.callback_query.edits, [])

    async def test_bus_fav_remove_locked_answers_and_gives_trivia(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_fav_remove:46009")

        await news_bot._bus.handle_bus_fav_remove(update, FakeContext())

        self.assertIn("fact:", update.message.replies[0])
        self.assertEqual(update.callback_query.edits, [])


class ReorganizeLoopTests(AsyncBotTestCase):
    def _make_bot(self):
        return bot.YKBot(make_config(self.tmp_path))

    async def test_loop_runs_iterations_and_survives_errors(self):
        news_bot = self._make_bot()
        calls = []

        async def fake_reorg():
            calls.append(1)
            raise RuntimeError("boom")  # exercises the per-iteration except

        news_bot._reorganize_interests = fake_reorg

        task = asyncio.create_task(news_bot._reorganize_loop(0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertTrue(calls)

    async def test_reorganize_interests_swallows_a_per_account_error(self):
        news_bot = self._make_bot()
        write_accounts_file(
            news_bot._config.accounts_file, {"alice": ["a"], "bob": ["b"]}
        )
        seen = []

        async def failing_account(account_id):
            seen.append(account_id)
            raise RuntimeError("nope")

        news_bot._reorganize_account = failing_account

        await news_bot._reorganize_interests()  # must not raise

        self.assertEqual(sorted(seen), ["alice", "bob"])
