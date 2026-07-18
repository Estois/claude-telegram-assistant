import asyncio

import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeBot,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class _DeleteFailingBot(FakeBot):
    async def delete_message(self, chat_id, message_id):
        raise RuntimeError("no delete permission")


class AppMiscTests(AsyncBotTestCase):
    CHAT_ID = 4
    USER_ID = 100

    def _make_bot(self, unlock_account="alice", **overrides):
        news_bot = bot.YKBot(make_config(self.tmp_path, **overrides))
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        if unlock_account:
            news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="hunter2")
        return news_bot

    def _update(self, **kwargs):
        return FakeUpdate(user_id=self.USER_ID, chat_id=self.CHAT_ID, **kwargs)

    def _admin_bot(self):
        news_bot = bot.YKBot(
            make_config(self.tmp_path, admin_account_id="amy", admin_telegram_user_id=self.USER_ID)
        )
        write_accounts_file(
            news_bot._config.accounts_file, {"amy": ["adminpass"], "bob": ["bobpass"]}
        )
        news_bot._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="adminpass")
        return news_bot

    def _stub_run_claude(self, news_bot, result):
        async def fake(update, context, prompt, **kwargs):
            return result

        news_bot._run_claude = fake

    # -- /news branches ------------------------------------------------------
    async def test_news_without_args_shows_usage(self):
        news_bot = self._make_bot()
        update = self._update()

        await news_bot.handle_news(update, FakeContext(args=[]))

        self.assertIn("Usage: /news", update.message.replies[0])

    async def test_news_history_append_failure_does_not_break_the_digest(self):
        news_bot = self._make_bot()

        class BadHistory:
            def append(self, subject):
                raise RuntimeError("disk full")

        news_bot._news_history_for = lambda account_id: BadHistory()
        self._stub_run_claude(news_bot, "News Digest — topic — 2026-07-19")
        update = self._update()

        await news_bot.handle_news(update, FakeContext(args=["topic"]))

        self.assertTrue(update.message.replies)  # digest still sent

    # -- bus/location branches ----------------------------------------------
    async def test_describe_stop_uses_cache_description(self):
        news_bot = self._make_bot()

        class Stops:
            def find_by_code(self, code):
                return {"Description": "Opp Blk 1"} if code == "46009" else None

        news_bot._bus_stops = Stops()

        self.assertEqual(news_bot._describe_stop("46009"), "Opp Blk 1 (46009)")

    async def test_location_with_no_nearby_stops_reports_none(self):
        news_bot = self._make_bot()

        class Stops:
            is_available = True

            def nearest(self, lat, lon, count):
                return []

        news_bot._bus_stops = Stops()

        class _Loc:
            latitude = 1.3
            longitude = 103.8

        update = self._update(location=_Loc())

        await news_bot.handle_location(update, FakeContext())

        self.assertIn("No bus stops found nearby", update.message.replies[0])

    # -- unlock delete-failure ----------------------------------------------
    async def test_unlock_still_welcomes_when_message_delete_fails(self):
        news_bot = self._make_bot(unlock_account=None)
        update = self._update(text="hunter2", message_id=7, full_name="Al")
        ctx = FakeContext()
        ctx.bot = _DeleteFailingBot()

        await news_bot.handle_plain_text(update, ctx)

        self.assertEqual(news_bot._accounts.account_for(self.CHAT_ID), "alice")
        self.assertTrue(ctx.bot.sent)
        self.assertIn("Welcome, Al!", ctx.bot.sent[0][1])

    # -- admin usage branches -----------------------------------------------
    async def test_removeuser_without_args_shows_usage(self):
        news_bot = self._admin_bot()
        update = self._update()

        await news_bot.handle_remove_user(update, FakeContext(args=[]))

        self.assertIn("Usage: /removeuser", update.message.replies[0])

    async def test_removeuser_reports_unknown_account(self):
        news_bot = self._admin_bot()
        update = self._update()

        await news_bot.handle_remove_user(update, FakeContext(args=["ghost"]))

        self.assertIn("No account named 'ghost'", update.message.replies[0])

    async def test_adduserteleid_rejects_non_numeric(self):
        news_bot = self._admin_bot()
        update = self._update()

        await news_bot.handle_add_user_tele_id(update, FakeContext(args=["abc"]))

        self.assertIn("Usage: /adduserteleid", update.message.replies[0])

    # -- _keep_typing --------------------------------------------------------
    async def test_keep_typing_emits_a_chat_action_until_cancelled(self):
        news_bot = self._make_bot()
        ctx = FakeContext()

        task = asyncio.create_task(news_bot._keep_typing(ctx, self.CHAT_ID))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertTrue(ctx.bot.actions)

    # -- reorganize machinery -----------------------------------------------
    async def test_reorganize_account_replaces_interests(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("alice")
        interests.add("cybersecurity")
        interests.add("cyber security")

        async def fake_run(chat_id, prompt):
            return "1. cybersecurity"

        news_bot._claude.run = fake_run

        await news_bot._reorganize_account("alice")

        self.assertEqual(interests.list(), ["cybersecurity"])

    async def test_reorganize_account_skips_when_nothing_to_do(self):
        news_bot = self._make_bot()
        interests = news_bot._interests_for("alice")
        interests.add("only one")
        calls = {"n": 0}

        async def fake_run(chat_id, prompt):
            calls["n"] += 1
            return "1. x"

        news_bot._claude.run = fake_run

        await news_bot._reorganize_account("alice")

        self.assertEqual(calls["n"], 0)
        self.assertEqual(interests.list(), ["only one"])

    async def test_reorganize_interests_visits_every_account(self):
        news_bot = self._make_bot()
        write_accounts_file(
            news_bot._config.accounts_file, {"alice": ["a"], "bob": ["b"]}
        )
        seen = []

        async def fake_account(account_id):
            seen.append(account_id)

        news_bot._reorganize_account = fake_account

        await news_bot._reorganize_interests()

        self.assertEqual(sorted(seen), ["alice", "bob"])

    async def test_on_startup_schedules_the_reorganize_task(self):
        news_bot = self._make_bot()

        await news_bot._on_startup(None)

        self.assertIsNotNone(news_bot._reorganize_task)
        news_bot._reorganize_task.cancel()
        await asyncio.gather(news_bot._reorganize_task, return_exceptions=True)

    async def test_on_startup_skips_when_interval_not_positive(self):
        news_bot = self._make_bot(interests_reorganize_interval_seconds=0)

        await news_bot._on_startup(None)

        self.assertIsNone(news_bot._reorganize_task)

    async def test_on_shutdown_cancels_the_reorganize_task(self):
        news_bot = self._make_bot()

        async def sleeper():
            await asyncio.sleep(100)

        news_bot._reorganize_task = asyncio.create_task(sleeper())

        await news_bot._on_shutdown(None)
        await asyncio.gather(news_bot._reorganize_task, return_exceptions=True)

        self.assertTrue(news_bot._reorganize_task.cancelled())
