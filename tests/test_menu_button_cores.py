import bot
from tests.helpers import (
    AsyncBotTestCase, FakeContext, FakeUpdate, make_config, write_accounts_file,
)


def home_data(markup):
    return None if markup is None else markup.inline_keyboard[0][0].callback_data


class InterestCoreButtonTests(AsyncBotTestCase):
    CHAT_ID = 51

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_add_interest_has_button_from_menu(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_interest(update, "alice", "space", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_interest_bare_from_slash(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_add_interest(update, "alice", "space")

        self.assertIsNone(update.message.replies_markup[-1])

    async def test_show_interests_listing_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("space")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_show_interests(update, "alice", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_show_interests_empty_state_has_button(self):
        """The empty state is exactly where a dead end hurts most."""
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_show_interests(update, "alice", from_menu=True)

        self.assertIn("No interests saved yet", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_remove_interest_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 1, from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_remove_interest_out_of_range_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 9, from_menu=True)

        self.assertIn("No interest #9", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_remove_interest_bare_from_slash(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_remove_interest(update, "alice", 1)

        self.assertIsNone(update.message.replies_markup[-1])

    async def test_update_interest_has_button_from_menu(self):
        b = self._make_bot()
        b._interests_for("alice").add("original")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return "revised text"

        b._run_claude = fake_run_claude
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_update_interest(update, FakeContext(), "alice", 1, "new text", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")


class CompileCoreButtonTests(AsyncBotTestCase):
    CHAT_ID = 52

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_too_few_interests_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_compile_interests(update, FakeContext(), "alice", from_menu=True)

        self.assertIn("Need at least 2 interests", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_unusable_reply_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("one")
        b._interests_for("alice").add("two")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return "   "

        b._run_claude = fake_run_claude
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_compile_interests(update, FakeContext(), "alice", from_menu=True)

        self.assertIn("no changes made", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_successful_compile_has_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("one")
        b._interests_for("alice").add("two")

        async def fake_run_claude(update, context, prompt, **kwargs):
            return "1. merged topic"

        b._run_claude = fake_run_claude
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID)

        await b._do_compile_interests(update, FakeContext(), "alice", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")


class AdminCoreButtonTests(AsyncBotTestCase):
    CHAT_ID = 53

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"amy": ["pw"], "bob": ["pw2"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_remove_user_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_remove_user(update, "bob", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_refusing_to_remove_admin_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_remove_user(update, "amy", from_menu=True)

        self.assertIn("Refusing", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_user_tele_id_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_add_user_tele_id(update, "424242", from_menu=True)

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_non_numeric_tele_id_has_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_add_user_tele_id(update, "nope", from_menu=True)

        self.assertIn("isn't a numeric", update.message.replies[-1])
        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_add_user_tele_id_bare_from_slash(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=100000001, chat_id=self.CHAT_ID)

        await b._do_add_user_tele_id(update, "424242")

        self.assertIsNone(update.message.replies_markup[-1])


class CallbackCallersPassFromMenuTests(AsyncBotTestCase):
    CHAT_ID = 54

    def _make_bot(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"alice": ["pw"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        return b

    async def test_menu_action_getinterests_attaches_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("space")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="act:getinterests")

        await b.handle_menu_action(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_menu_action_compile_attaches_button(self):
        b = self._make_bot()
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="act:compile")

        await b.handle_menu_action(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_confirm_remove_interest_attaches_button(self):
        b = self._make_bot()
        b._interests_for("alice").add("gone")
        update = FakeUpdate(user_id=1, chat_id=self.CHAT_ID, callback_data="cfm:int_rm:1")

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")

    async def test_confirm_remove_user_attaches_button(self):
        b = bot.YKBot(make_config(self.tmp_path))
        write_accounts_file(b._config.accounts_file, {"amy": ["pw"], "bob": ["pw2"]})
        b._accounts.try_unlock(chat_id=self.CHAT_ID, attempt="pw")
        update = FakeUpdate(
            user_id=100000001, chat_id=self.CHAT_ID, callback_data="cfm:adm_rm:bob"
        )

        await b.handle_confirm(update, FakeContext())

        self.assertEqual(home_data(update.message.replies_markup[-1]), "home")
