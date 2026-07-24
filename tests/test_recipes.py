import bot
from tests.helpers import (
    AsyncBotTestCase, BotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def datas(markup):
    return [] if markup is None else [b.callback_data for row in markup.inline_keyboard for b in row]


class RecipeInterpretPromptTests(BotTestCase):
    def test_mentions_anti_invention_rule_and_not_a_recipe_escape_hatch(self):
        prompt = bot.YKBot._build_recipe_interpret_prompt("2 eggs, scramble them")

        self.assertIn("do NOT invent", prompt)
        self.assertIn("NOT_A_RECIPE", prompt)
        self.assertIn("Title:", prompt)
        self.assertIn("2 eggs, scramble them", prompt)


class ParseRecipeReplyTests(BotTestCase):
    def test_parses_title_and_body(self):
        reply = "Title: Fried Rice\n\n<b>Ingredients</b>\n• rice\n\n<b>Steps</b>\n1. cook"

        result = bot.YKBot._parse_recipe_reply(reply)

        self.assertEqual(result[0], "Fried Rice")
        self.assertIn("Ingredients", result[1])

    def test_not_a_recipe_returns_none(self):
        self.assertIsNone(bot.YKBot._parse_recipe_reply("NOT_A_RECIPE"))

    def test_missing_title_prefix_returns_none(self):
        self.assertIsNone(bot.YKBot._parse_recipe_reply("Just some text with no marker"))

    def test_empty_body_returns_none(self):
        self.assertIsNone(bot.YKBot._parse_recipe_reply("Title: Only A Title\n\n"))


class RecipeSlashEntryPointTests(AsyncBotTestCase):
    CHAT_ID = 91

    def _make_bot(self, reply="Title: Banana Bread\n\nIngredients...\nSteps..."):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"beth": ["bethpass"], "dave": ["davepass"]})

        async def fake_run_claude(update, context, prompt, **kwargs):
            fake_run_claude.last_prompt = prompt
            fake_run_claude.last_kwargs = kwargs
            return reply

        b._run_claude = fake_run_claude
        return b

    def _unlock(self, b, account):
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt=f"{account}pass")

    async def test_addrecipe_arms_capture_for_a_closed_member(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_add_recipe(update, FakeContext())

        self.assertIn("Paste the recipe", update.message.replies[-1])

    async def test_addrecipe_rejects_non_member_with_trivia(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_add_recipe(update, FakeContext())

        self.assertIn("fact:", update.message.replies[-1])
        self.assertNotIn("closed", update.message.replies[-1].lower())

    async def test_add_recipe_saves_on_successful_parse(self):
        b = self._make_bot(reply="Title: Banana Bread\n\n<b>Ingredients</b>\n• bananas")
        context = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_recipe(update, context, "beth", "2 bananas, mash and bake")

        self.assertEqual(b._recipes_for("beth").list()[0]["name"], "Banana Bread")
        self.assertIn("Saved recipe #1", update.message.replies[-1])
        self.assertIn("Banana Bread", update.message.replies[-1])

    async def test_add_recipe_not_a_recipe_does_not_save(self):
        b = self._make_bot(reply="NOT_A_RECIPE")
        context = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_recipe(update, context, "beth", "asdkjfh")

        self.assertEqual(b._recipes_for("beth").list(), [])
        self.assertIn("couldn't find a recipe", update.message.replies[-1])

    async def test_add_recipe_failed_claude_call_sends_nothing_extra(self):
        b = self._make_bot()

        async def failing(update, context, prompt, **kwargs):
            return None

        b._run_claude = failing
        context = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_recipe(update, context, "beth", "something")

        self.assertEqual(update.message.replies, [])
        self.assertEqual(b._recipes_for("beth").list(), [])

    async def test_interpret_prompt_runs_without_web_tools(self):
        b = self._make_bot()
        context = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_recipe(update, context, "beth", "something")

        self.assertEqual(b._run_claude.last_kwargs.get("web_tools"), False)

    async def test_getrecipes_lists_saved_titles(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Banana Bread", "body")
        b._recipes_for("beth").add("Fried Rice", "body")
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_get_recipes(update, FakeContext())

        reply = update.message.replies[-1]
        self.assertIn("1. Banana Bread", reply)
        self.assertIn("2. Fried Rice", reply)

    async def test_getrecipes_empty(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_get_recipes(update, FakeContext())

        self.assertIn("No recipes saved yet", update.message.replies[-1])

    async def test_getrecipes_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_get_recipes(update, FakeContext())

        self.assertIn("fact:", update.message.replies[-1])

    async def test_updaterecipe_usage_without_index(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_update_recipe(update, FakeContext(args=[]))

        self.assertIn("Usage: /updaterecipe", update.message.replies[-1])

    async def test_updaterecipe_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_update_recipe(update, FakeContext(args=["1"]))

        self.assertIn("fact:", update.message.replies[-1])

    async def test_updaterecipe_no_recipes_saved_yet(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_update_recipe(update, FakeContext(args=["1"]))

        self.assertIn("No recipes saved yet", update.message.replies[-1])

    async def test_updaterecipe_out_of_range(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Only One", "body")
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_update_recipe(update, FakeContext(args=["5"]))

        self.assertIn("No recipe #5", update.message.replies[-1])

    async def test_updaterecipe_valid_index_arms_capture(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Only One", "body")
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)
        context = FakeContext(args=["1"])

        await b.handle_update_recipe(update, context)

        self.assertEqual(
            context.user_data["awaiting"], {"kind": "update_recipe", "payload": {"index": 1}}
        )

    async def test_do_update_recipe_replaces_entry(self):
        b = self._make_bot(reply="Title: New Name\n\nnew body")
        b._recipes_for("beth").add("Old Name", "old body")
        context = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_update_recipe(update, context, "beth", 1, "pasted replacement")

        self.assertEqual(b._recipes_for("beth").list()[0]["name"], "New Name")
        self.assertIn("Updated recipe #1", update.message.replies[-1])

    async def test_do_update_recipe_not_a_recipe_leaves_entry_untouched(self):
        b = self._make_bot(reply="NOT_A_RECIPE")
        b._recipes_for("beth").add("Old Name", "old body")
        context = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_update_recipe(update, context, "beth", 1, "gibberish")

        self.assertEqual(b._recipes_for("beth").list()[0]["name"], "Old Name")
        self.assertIn("couldn't find a recipe", update.message.replies[-1])

    async def test_do_update_recipe_stale_index_at_execution_time(self):
        b = self._make_bot(reply="Title: New Name\n\nnew body")
        context = FakeContext()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_update_recipe(update, context, "beth", 1, "pasted replacement")

        self.assertIn("No recipe #1", update.message.replies[-1])

    async def test_removerecipe_deletes_directly_without_confirm(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Gone Soon", "body")
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_remove_recipe(update, FakeContext(args=["1"]))

        self.assertEqual(b._recipes_for("beth").list(), [])
        self.assertIn('Removed recipe #1: "Gone Soon"', update.message.replies[-1])

    async def test_removerecipe_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_remove_recipe(update, FakeContext(args=["1"]))

        self.assertIn("fact:", update.message.replies[-1])

    async def test_removerecipe_usage_without_index(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_remove_recipe(update, FakeContext(args=[]))

        self.assertIn("Usage: /removerecipe", update.message.replies[-1])

    async def test_removerecipe_no_recipes_saved_yet(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_remove_recipe(update, FakeContext(args=["1"]))

        self.assertIn("No recipes saved yet", update.message.replies[-1])

    async def test_do_remove_recipe_out_of_range(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_recipe(update, "beth", 5)

        self.assertIn("No recipe #5", update.message.replies[-1])

    async def test_recipe_commands_listed_only_for_closed_session(self):
        b = self._make_bot()

        member_listing = b._build_command_list(closed=True)
        non_member_listing = b._build_command_list(closed=False)

        self.assertIn("/addrecipe", member_listing)
        self.assertNotIn("/addrecipe", non_member_listing)
        # The grouping must stay invisible: no separate "Closed:" section.
        self.assertNotIn("Closed:", member_listing)
        self.assertIn("Recipes:", member_listing)


class RecipeMenuWiringTests(AsyncBotTestCase):
    CHAT_ID = 92

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"beth": ["bethpass"], "dave": ["davepass"]})
        return b

    def _unlock(self, b, account):
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt=f"{account}pass")

    def _update(self, callback_data, user_id=1):
        return FakeUpdate(
            user_id=user_id, chat_id=self.CHAT_ID, callback_data=callback_data, callback_only=True,
        )

    async def test_menu_markup_shows_recipes_button_for_member(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        self.assertIn("menu:recipes", datas(update.message.replies_markup[-1]))

    async def test_menu_markup_hides_recipes_button_for_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b.handle_menu(update, FakeContext())

        self.assertNotIn("menu:recipes", datas(update.message.replies_markup[-1]))

    async def test_menu_nav_recipes_for_member(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = self._update("menu:recipes")

        await b.handle_menu_nav(update, FakeContext())

        self.assertEqual(update.callback_query.last_text, "Recipes:")

    async def test_menu_nav_recipes_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = self._update("menu:recipes")

        await b.handle_menu_nav(update, FakeContext())

        self.assertIn("fact:", update.callback_query.last_text)

    async def test_menu_action_addrecipe_arms_capture_for_member(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = self._update("act:addrecipe")
        context = FakeContext()

        await b.handle_menu_action(update, context)

        self.assertEqual(context.user_data["awaiting"], {"kind": "recipe_raw", "payload": {}})

    async def test_menu_action_addrecipe_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = self._update("act:addrecipe")
        context = FakeContext()

        await b.handle_menu_action(update, context)

        self.assertNotIn("awaiting", context.user_data)
        self.assertIn("fact:", update.callback_query.last_text)

    async def test_menu_action_getrecipes_for_member(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Banana Bread", "body")
        self._unlock(b, "beth")
        update = self._update("act:getrecipes")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("Banana Bread", update.effective_message.replies[-1])

    async def test_menu_action_getrecipes_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = self._update("act:getrecipes")

        await b.handle_menu_action(update, FakeContext())

        self.assertIn("fact:", update.callback_query.last_text)

    async def test_recipe_pick_locked_chat_answers_without_editing(self):
        b = self._make_bot()
        update = self._update("rcp_pick:view")  # never unlocked

        await b.handle_recipe_pick(update, FakeContext())

        self.assertEqual(update.callback_query.edits, [])

    async def test_recipe_select_locked_chat_answers_without_editing(self):
        b = self._make_bot()
        update = self._update("rcp_view:1")  # never unlocked

        await b.handle_recipe_select(update, FakeContext())

        self.assertEqual(update.callback_query.edits, [])

    async def test_recipe_select_view_falls_back_to_plain_text_on_bad_html(self):
        from telegram.error import BadRequest

        b = self._make_bot()
        b._recipes_for("beth").add("Banana Bread", "body")
        self._unlock(b, "beth")
        update = self._update("rcp_view:1")
        query = update.callback_query
        calls = []
        original = query.edit_message_text

        async def flaky(text, **kwargs):
            calls.append(kwargs)
            if kwargs.get("parse_mode") is not None:
                raise BadRequest("bad html")
            await original(text, **kwargs)

        query.edit_message_text = flaky

        await b.handle_recipe_select(update, FakeContext())

        self.assertEqual(len(calls), 2)
        self.assertEqual(query.last_text, "<b>Banana Bread</b>\n\nbody")

    async def test_recipe_pick_shows_options_for_member(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Banana Bread", "body")
        self._unlock(b, "beth")
        update = self._update("rcp_pick:view")

        await b.handle_recipe_pick(update, FakeContext())

        self.assertIn("rcp_view:1", datas(update.callback_query.last_markup))

    async def test_recipe_pick_empty_shows_empty_message(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = self._update("rcp_pick:rm")

        await b.handle_recipe_pick(update, FakeContext())

        self.assertIn("No recipes saved yet", update.callback_query.last_text)

    async def test_recipe_pick_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = self._update("rcp_pick:view")

        await b.handle_recipe_pick(update, FakeContext())

        self.assertIn("fact:", update.callback_query.last_text)

    async def test_recipe_select_view_shows_full_recipe(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Banana Bread", "<b>Ingredients</b>\n• bananas")
        self._unlock(b, "beth")
        update = self._update("rcp_view:1")

        await b.handle_recipe_select(update, FakeContext())

        self.assertIn("Banana Bread", update.callback_query.last_text)
        self.assertIn("Ingredients", update.callback_query.last_text)

    async def test_recipe_select_view_missing_entry(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = self._update("rcp_view:9")

        await b.handle_recipe_select(update, FakeContext())

        self.assertIn("no longer exists", update.callback_query.last_text)

    async def test_recipe_select_upd_arms_capture(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Banana Bread", "body")
        self._unlock(b, "beth")
        update = self._update("rcp_upd:1")
        context = FakeContext()

        await b.handle_recipe_select(update, context)

        self.assertEqual(
            context.user_data["awaiting"], {"kind": "update_recipe", "payload": {"index": 1}}
        )

    async def test_recipe_select_rm_shows_confirm(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Banana Bread", "body")
        self._unlock(b, "beth")
        update = self._update("rcp_rm:1")

        await b.handle_recipe_select(update, FakeContext())

        self.assertIn("cfm:rcp_rm:1", datas(update.callback_query.last_markup))
        self.assertIn("Delete recipe #1", update.callback_query.last_text)

    async def test_recipe_select_rejects_non_member(self):
        b = self._make_bot()
        self._unlock(b, "dave")
        update = self._update("rcp_view:1")

        await b.handle_recipe_select(update, FakeContext())

        self.assertIn("fact:", update.callback_query.last_text)

    async def test_confirm_rcp_rm_deletes(self):
        b = self._make_bot()
        b._recipes_for("beth").add("Gone Soon", "body")
        self._unlock(b, "beth")
        update = self._update("cfm:rcp_rm:1")

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(b._recipes_for("beth").list(), [])
        self.assertIn('Removed recipe #1: "Gone Soon"', update.callback_query.message.replies[-1])

    async def test_confirm_rcp_rm_rejects_non_member(self):
        b = self._make_bot()
        b._recipes_for("dave").add("Should Survive", "body")
        self._unlock(b, "dave")
        update = self._update("cfm:rcp_rm:1")

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(b._recipes_for("dave").list()[0]["name"], "Should Survive")


class ConsumeAwaitingRecipeTests(AsyncBotTestCase):
    CHAT_ID = 93

    def _make_bot(self, reply="Title: Banana Bread\n\nbody"):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"beth": ["bethpass"], "dave": ["davepass"]})

        async def fake_run_claude(update, context, prompt, **kwargs):
            return reply

        b._run_claude = fake_run_claude
        return b

    def _unlock(self, b, account):
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt=f"{account}pass")

    async def test_recipe_raw_capture_saves_for_member(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, text="2 bananas, bake")
        context = FakeContext(user_data={"awaiting": {"kind": "recipe_raw", "payload": {}}})

        handled = await b._consume_awaiting(update, context, self.CHAT_ID)

        self.assertTrue(handled)
        self.assertEqual(b._recipes_for("beth").list()[0]["name"], "Banana Bread")

    async def test_recipe_raw_capture_silently_swallowed_for_non_member(self):
        # Shouldn't be reachable in practice (every arming site already
        # gates on closed membership) — defense in depth, mirrors
        # add_user_tele_id's re-check in the same function.
        b = self._make_bot()
        self._unlock(b, "dave")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, text="2 bananas, bake")
        context = FakeContext(user_data={"awaiting": {"kind": "recipe_raw", "payload": {}}})

        handled = await b._consume_awaiting(update, context, self.CHAT_ID)

        self.assertTrue(handled)
        self.assertEqual(update.message.replies, [])
        self.assertEqual(b._recipes_for("dave").list(), [])

    async def test_update_recipe_capture_valid_index(self):
        b = self._make_bot(reply="Title: New Name\n\nnew body")
        b._recipes_for("beth").add("Old Name", "old body")
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, text="replacement text")
        context = FakeContext(
            user_data={"awaiting": {"kind": "update_recipe", "payload": {"index": 1}}}
        )

        await b._consume_awaiting(update, context, self.CHAT_ID)

        self.assertEqual(b._recipes_for("beth").list()[0]["name"], "New Name")

    async def test_update_recipe_capture_stale_index(self):
        b = self._make_bot()
        self._unlock(b, "beth")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, text="replacement text")
        context = FakeContext(
            user_data={"awaiting": {"kind": "update_recipe", "payload": {"index": 5}}}
        )

        await b._consume_awaiting(update, context, self.CHAT_ID)

        self.assertIn("no longer exists", update.message.replies[-1])
