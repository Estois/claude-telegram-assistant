import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [] if markup is None else [b.callback_data for row in markup.inline_keyboard for b in row]


class ResearchEntryPointTests(AsyncBotTestCase):
    CHAT_ID = 81

    def _make_bot(self, reply="Research Findings — fusion power\n\nBody."):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return reply

        b._run_claude = fake_run_claude
        return b

    async def test_slash_without_subject_shows_usage(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_research(update, FakeContext(args=[]))

        self.assertIn("/research <subject>", update.message.replies[-1])

    async def test_slash_research_delivers_findings_with_depth_breadth_but_no_home(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_research(update, FakeContext(args=["fusion", "power"]))

        markup = update.message.replies_markup[-1]
        self.assertIn("research:depth", datas(markup))
        self.assertIn("research:breadth", datas(markup))
        self.assertNotIn("home", datas(markup))

    async def test_menu_action_research_arms_capture_slot(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="act:research")
        context = FakeContext()

        await b.handle_menu_action(update, context)

        self.assertEqual(context.user_data["awaiting"], {"kind": "research_subject", "payload": {}})

    async def test_capture_delivers_findings_with_home_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, text="fusion power")
        context = FakeContext(
            user_data={"awaiting": {"kind": "research_subject", "payload": {}}}
        )

        await b._consume_awaiting(update, context, self.CHAT_ID)

        markup = update.message.replies_markup[-1]
        self.assertIn("home", datas(markup))
        self.assertIn("research:depth", datas(markup))

    async def test_successful_research_stores_followup_state(self):
        b = self._make_bot(reply="Research Findings — fusion power\n\nSome body text.")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)
        context = FakeContext(args=["fusion", "power"])

        await b.handle_research(update, context)

        state = context.user_data["research"]
        self.assertEqual(state["subject"], "fusion power")
        self.assertIn("Some body text.", state["findings"])
        self.assertFalse(state["from_menu"])

    async def test_failed_research_does_not_store_state(self):
        b = self._make_bot()

        async def failing(update, context, prompt, **kwargs):
            return None

        b._run_claude = failing
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)
        context = FakeContext(args=["fusion", "power"])

        await b.handle_research(update, context)

        self.assertNotIn("research", context.user_data)

    async def test_research_command_is_registered_and_listed_under_its_own_category(self):
        b = bot.YKBot(make_config(self.tmp_path))

        listing = b._build_command_list(admin=False)

        self.assertIn("Research:", listing)
        self.assertIn("/research <subject>", listing)


class ResearchFollowupTests(AsyncBotTestCase):
    CHAT_ID = 82

    def _make_bot(self, reply="Research Findings — fusion power\n\nDeeper body."):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")

        async def fake_run_claude(update, context, prompt, **kwargs):
            fake_run_claude.last_prompt = prompt
            return reply

        b._run_claude = fake_run_claude
        return b

    def _state(self, from_menu=False):
        return {
            "subject": "fusion power",
            "findings": "Research Findings — fusion power\n\nOriginal body.",
            "from_menu": from_menu,
        }

    async def test_expired_state_replies_without_calling_claude(self):
        b = self._make_bot()

        async def should_not_be_called(*args, **kwargs):
            raise AssertionError("Claude must not be called for expired state")

        b._run_claude = should_not_be_called
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")

        await b.handle_research_followup(update, FakeContext())

        self.assertIn("expired", update.message.replies[-1].lower())

    async def test_depth_tap_embeds_previous_findings_and_updates_state(self):
        b = self._make_bot(reply="Research Findings — fusion power\n\nDeeper body.")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": self._state()})

        await b.handle_research_followup(update, context)

        self.assertIn("Original body.", b._run_claude.last_prompt)
        self.assertIn("MORE DEPTH", b._run_claude.last_prompt)
        self.assertIn("Deeper body.", context.user_data["research"]["findings"])

    async def test_breadth_tap_uses_breadth_wording(self):
        b = self._make_bot(reply="Research Findings — fusion power\n\nWider body.")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:breadth")
        context = FakeContext(user_data={"research": self._state()})

        await b.handle_research_followup(update, context)

        self.assertIn("MORE BREADTH", b._run_claude.last_prompt)

    async def test_followup_carries_home_button_from_stored_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": self._state(from_menu=True)})

        await b.handle_research_followup(update, context)

        markup = update.message.replies_markup[-1]
        self.assertIn("home", datas(markup))

    async def test_followup_without_from_menu_has_no_home_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": self._state(from_menu=False)})

        await b.handle_research_followup(update, context)

        markup = update.message.replies_markup[-1]
        self.assertNotIn("home", datas(markup))

    async def test_failed_followup_leaves_state_untouched(self):
        b = self._make_bot()

        async def failing(update, context, prompt, **kwargs):
            return None

        b._run_claude = failing
        original_state = self._state()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="research:depth")
        context = FakeContext(user_data={"research": dict(original_state)})

        await b.handle_research_followup(update, context)

        self.assertEqual(context.user_data["research"], original_state)

    async def test_locked_chat_gets_trivia_not_a_followup(self):
        b = bot.YKBot(make_config(self.tmp_path))
        update = FakeUpdate(user_id=1, chat_id=999, callback_data="research:depth")
        context = FakeContext()

        await b.handle_research_followup(update, context)

        self.assertNotIn("research", context.user_data)
