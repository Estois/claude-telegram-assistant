import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class MenuTests(AsyncBotTestCase):
    CHAT_ID = 9
    USER = 100
    ADMIN_USER = 100000001

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"], "amy": ["adm"]})
        return b

    def _unlock(self, b, chat_id, pw):
        b._accounts.try_unlock(chat_id=chat_id, attempt=pw)

    async def test_menu_command_shows_top_level_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        markup = update.message.replies_markup[-1]  # see helper note below
        self.assertIn("act:news", datas(markup))
        self.assertNotIn("menu:admin", datas(markup))

    async def test_admin_sees_admin_row(self):
        b = self._make_bot()
        self._unlock(b, 1, "adm")  # amy account on admin chat
        update = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1)

        await b.handle_menu(update, FakeContext())

        self.assertIn("menu:admin", datas(update.message.replies_markup[-1]))

    async def test_locked_user_gets_no_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        # A locked user still gets a trivia reply via _require_session (no
        # reply_markup kwarg), which FakeMessage.reply_text records as a
        # bare None entry — so check no *real* markup was attached, not
        # that reply_text was never called.
        self.assertFalse(any(getattr(update.message, "replies_markup", [])))

    async def test_nav_to_interests_edits_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:interests")

        await b.handle_menu_nav(update, FakeContext())

        self.assertIn("int_pick:rm", datas(update.callback_query.last_markup))

    async def test_nav_to_admin_as_admin_shows_admin_board(self):
        b = self._make_bot()
        self._unlock(b, 1, "adm")  # amy account on admin chat
        update = FakeUpdate(user_id=self.ADMIN_USER, chat_id=1, callback_data="menu:admin")

        await b.handle_menu_nav(update, FakeContext())

        board = datas(update.callback_query.last_markup)
        self.assertIn("act:getusers", board)
        self.assertIn("adm_pick_rm", board)

    async def test_nav_to_admin_as_non_admin_is_rejected(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")  # alice account, non-admin
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:admin")

        await b.handle_menu_nav(update, FakeContext())

        markup = update.callback_query.last_markup
        board = datas(markup) if markup else []
        self.assertNotIn("act:getusers", board)
        self.assertNotIn("adm_pick_rm", board)

    async def test_nav_to_root_rerenders_top_level_board(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")  # alice account, non-admin
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:root")

        await b.handle_menu_nav(update, FakeContext())

        board = datas(update.callback_query.last_markup)
        self.assertIn("act:news", board)
        self.assertIn("menu:interests", board)
        self.assertNotIn("menu:admin", board)

    async def test_menu_bus_opens_fav_menu(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:bus")
        context = FakeContext()

        await b.handle_menu_bus(update, context)

        self.assertTrue(update.message.replies)
        self.assertEqual(context.user_data["bus_menu"], {"kind": "fav"})

    async def test_locked_user_nav_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="menu:interests")

        await b.handle_menu_nav(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertNotIn("int_pick:rm", datas(update.callback_query.last_markup) if update.callback_query.last_markup else [])
        self.assertIsNone(update.callback_query.last_text)

    async def test_locked_user_menu_bus_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:bus")
        context = FakeContext()

        await b.handle_menu_bus(update, context)

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertNotIn("bus_menu", context.user_data)
        # A locked user still gets a trivia reply via _require_session (no
        # reply_markup kwarg) — the fav-menu keyboard must not appear.
        self.assertFalse(any(update.message.replies_markup))

    async def test_action_getinterests_lists(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("weather")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("weather", update.message.replies[-1])

    async def test_action_compile_reports_need_more_interests(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:compile")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("Need at least 2 interests", update.message.replies[-1])

    async def test_endsession_asks_confirm_then_locks(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        confirm = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:endsession")
        await b.handle_menu_action(confirm, FakeContext())
        self.assertIn("cfm:endsession",
                      [b_.callback_data for row in confirm.callback_query.last_markup.inline_keyboard for b_ in row])

        yes = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cfm:endsession")
        await b.handle_confirm(yes, FakeContext())
        self.assertIsNone(b._accounts.account_for(self.CHAT_ID))

    async def test_cancel_dismisses(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cxl:endsession")

        await b.handle_cancel(update, FakeContext())

        self.assertIsNotNone(b._accounts.account_for(self.CHAT_ID))  # still unlocked
        self.assertTrue(update.callback_query.edits)

    async def test_locked_user_menu_action_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        # A locked user still gets a trivia reply via _require_session (no
        # reply_markup kwarg) — no real interests listing must appear.
        self.assertFalse(any(update.message.replies_markup))

    async def test_locked_user_confirm_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cfm:endsession")

        await b.handle_confirm(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertIsNone(update.callback_query.last_text)

    async def test_locked_user_cancel_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cxl:endsession")

        await b.handle_cancel(update, FakeContext())

        self.assertTrue(update.callback_query.answers)  # query.answer() was still called
        self.assertIsNone(update.callback_query.last_text)

    async def test_news_button_arms_capture_then_core(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")

        async def fake_do_news(update, context, account_id, subject):
            update.effective_message.replies.append(f"digest:{subject}")
        b._do_news = fake_do_news

        arm = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:news")
        ctx = FakeContext()
        await b.handle_menu_action(arm, ctx)
        self.assertEqual(ctx.user_data["awaiting"]["kind"], "news_subject")
        self.assertIn("Send the subject for your news digest:", arm.callback_query.last_text)

        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="climate")
        await b.handle_plain_text(typed, ctx)
        self.assertIn("digest:climate", typed.message.replies)

    async def test_summarize_button_arms_capture_then_core(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")

        async def fake_do_summarize(update, context, url):
            update.effective_message.replies.append(f"summary:{url}")
        b._do_summarize = fake_do_summarize

        arm = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:summarize")
        ctx = FakeContext()
        await b.handle_menu_action(arm, ctx)
        self.assertEqual(ctx.user_data["awaiting"]["kind"], "summarize_url")
        self.assertIn("Send the URL to summarize:", arm.callback_query.last_text)

        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="https://example.com/a")
        await b.handle_plain_text(typed, ctx)
        self.assertIn("summary:https://example.com/a", typed.message.replies)

    async def test_summarize_capture_rejects_bad_url(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        ctx = FakeContext(user_data={"awaiting": {"kind": "summarize_url", "payload": {}}})
        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="not-a-url")

        await b.handle_plain_text(typed, ctx)

        self.assertTrue(any("public http(s) URL" in r for r in typed.message.replies))

    async def test_addinterest_button_arms_capture_then_core(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")

        arm = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="act:addinterest")
        ctx = FakeContext()
        await b.handle_menu_action(arm, ctx)
        self.assertEqual(ctx.user_data["awaiting"]["kind"], "add_interest")
        self.assertIn(
            "Send the interest to add (a word, sentence, or paragraph):", arm.callback_query.last_text
        )

        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="weather")
        await b.handle_plain_text(typed, ctx)
        self.assertIn("Added as interest #1.", typed.message.replies)
        self.assertIn("weather", b._interests_for("alice").list())

    async def test_pick_rm_lists_interests_as_buttons(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("one")
        b._interests_for("alice").add("two")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_pick:rm")

        await b.handle_interest_pick(update, FakeContext())

        d = [b_.callback_data for row in update.callback_query.last_markup.inline_keyboard for b_ in row]
        self.assertIn("int_rm:1", d)
        self.assertIn("int_rm:2", d)

    async def test_pick_upd_prompts_update_verb(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("one")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_pick:upd")

        await b.handle_interest_pick(update, FakeContext())

        self.assertIn("Pick an interest to update:", update.callback_query.last_text)
        d = [b_.callback_data for row in update.callback_query.last_markup.inline_keyboard for b_ in row]
        self.assertIn("int_upd:1", d)

    async def test_pick_with_no_interests_shows_empty_message(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_pick:rm")

        await b.handle_interest_pick(update, FakeContext())

        self.assertEqual(update.callback_query.last_text, "No interests saved yet.")
        d = [b_.callback_data for row in update.callback_query.last_markup.inline_keyboard for b_ in row]
        self.assertIn("int_pick:rm", d)

    async def test_locked_user_interest_pick_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_pick:rm")

        await b.handle_interest_pick(update, FakeContext())

        self.assertTrue(update.callback_query.answers)
        self.assertIsNone(update.callback_query.last_text)

    async def test_select_rm_then_confirm_deletes(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("doomed")
        sel = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_rm:1")
        await b.handle_interest_select(sel, FakeContext())
        d = [b_.callback_data for row in sel.callback_query.last_markup.inline_keyboard for b_ in row]
        self.assertIn("cfm:int_rm:1", d)

        yes = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="cfm:int_rm:1")
        await b.handle_confirm(yes, FakeContext())
        self.assertEqual(b._interests_for("alice").list(), [])

    async def test_select_upd_arms_capture_with_index(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("orig")
        ctx = FakeContext()
        sel = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_upd:1")

        await b.handle_interest_select(sel, ctx)

        self.assertEqual(ctx.user_data["awaiting"], {"kind": "update_interest", "payload": {"index": 1}})
        self.assertIn("Send the new text for interest #1", sel.callback_query.last_text)

    async def test_locked_user_interest_select_is_rejected(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, callback_data="int_rm:1")

        await b.handle_interest_select(update, FakeContext())

        self.assertTrue(update.callback_query.answers)
        self.assertIsNone(update.callback_query.last_text)

    async def test_update_interest_capture_completes(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        b._interests_for("alice").add("orig")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return "revised text"
        b._run_claude = fake_run_claude

        ctx = FakeContext(user_data={"awaiting": {"kind": "update_interest", "payload": {"index": 1}}})
        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="new wording")

        await b.handle_plain_text(typed, ctx)

        self.assertIn("Updated interest #1.", typed.message.replies)
        self.assertEqual(b._interests_for("alice").list(), ["revised text"])

    async def test_update_interest_capture_with_stale_index(self):
        b = self._make_bot()
        self._unlock(b, self.CHAT_ID, "pw")
        # No interests saved, so index 1 no longer exists.
        ctx = FakeContext(user_data={"awaiting": {"kind": "update_interest", "payload": {"index": 1}}})
        typed = FakeUpdate(user_id=self.USER, chat_id=self.CHAT_ID, text="new wording")

        await b.handle_plain_text(typed, ctx)

        self.assertIn("Interest #1 no longer exists. See /getinterests.", typed.message.replies)
