import bot
from tests.helpers import (
    AsyncBotTestCase,
    FakeContext,
    FakeUpdate,
    make_config,
    write_accounts_file,
)


class LockedPathTests(AsyncBotTestCase):
    """Every gated handler, when the chat is locked, must reply with trivia
    (never a 'locked' notice) and take no action — see _require_session."""

    CHAT_ID = 3
    USER_ID = 100

    def _make_bot(self):
        news_bot = bot.YKBot(make_config(self.tmp_path))
        # Account exists but this chat never unlocks it.
        write_accounts_file(news_bot._config.accounts_file, {"alice": ["hunter2"]})
        return news_bot

    def _update(self, **kwargs):
        return FakeUpdate(user_id=self.USER_ID, chat_id=self.CHAT_ID, **kwargs)

    async def _assert_trivia(self, update):
        self.assertTrue(update.message.replies, "expected a trivia reply")
        self.assertIn("fact:", update.message.replies[0])

    async def test_start_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_start(update, FakeContext())
        await self._assert_trivia(update)

    async def test_help_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_help(update, FakeContext())
        await self._assert_trivia(update)


    async def test_end_session_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_end_session(update, FakeContext())
        await self._assert_trivia(update)

    async def test_news_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_news(update, FakeContext(args=["x"]))
        await self._assert_trivia(update)

    async def test_add_interests_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_add_interests(update, FakeContext(args=["x"]))
        await self._assert_trivia(update)

    async def test_get_interests_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_get_interests(update, FakeContext())
        await self._assert_trivia(update)

    async def test_update_interests_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_update_interests(update, FakeContext(args=["1", "x"]))
        await self._assert_trivia(update)

    async def test_compile_interests_locked(self):
        news_bot = self._make_bot()
        update = self._update()
        await news_bot.handle_compile_interests(update, FakeContext())
        await self._assert_trivia(update)

    async def test_location_locked(self):
        news_bot = self._make_bot()

        class _Loc:
            latitude = 1.3
            longitude = 103.8

        update = self._update(location=_Loc())
        await news_bot.handle_location(update, FakeContext())
        await self._assert_trivia(update)

    async def test_bus_fav_add_locked_answers_and_gives_trivia(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_fav_add:46009")
        await news_bot.handle_bus_fav_add(update, FakeContext())
        await self._assert_trivia(update)
        self.assertEqual(update.callback_query.edits, [])

    async def test_bus_refresh_locked_answers_and_gives_trivia(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_refresh:46009")
        await news_bot.handle_bus_refresh(update, FakeContext())
        await self._assert_trivia(update)
        self.assertEqual(update.callback_query.edits, [])

    async def test_bus_back_locked_answers_and_gives_trivia(self):
        news_bot = self._make_bot()
        update = self._update(callback_data="bus_back")
        await news_bot.handle_bus_back(update, FakeContext())
        await self._assert_trivia(update)
        self.assertEqual(update.callback_query.edits, [])
