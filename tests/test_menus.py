import unittest

from ykbot import menus


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


class TopLevelKeyboardTests(unittest.TestCase):
    def test_non_admin_has_no_admin_row(self):
        d = datas(menus.top_level_keyboard(is_admin=False))
        self.assertEqual(
            d,
            [
                "act:bus", "act:news",
                "act:research", "act:summarize",
                "menu:interests", "act:endsession",
            ],
        )
        self.assertNotIn("menu:admin", d)

    def test_admin_gets_admin_row(self):
        self.assertIn("menu:admin", datas(menus.top_level_keyboard(is_admin=True)))

    def test_non_closed_has_no_recipes_row(self):
        self.assertNotIn(
            "menu:recipes", datas(menus.top_level_keyboard(is_admin=False, is_closed=False))
        )

    def test_closed_gets_recipes_row(self):
        self.assertIn(
            "menu:recipes", datas(menus.top_level_keyboard(is_admin=False, is_closed=True))
        )


class ResearchFollowupKeyboardTests(unittest.TestCase):
    def test_always_has_depth_and_breadth(self):
        d = datas(menus.research_followup_keyboard(from_menu=False))
        self.assertIn("research:depth", d)
        self.assertIn("research:breadth", d)
        self.assertNotIn("home", d)

    def test_home_row_only_when_from_menu(self):
        self.assertIn("home", datas(menus.research_followup_keyboard(from_menu=True)))


class InterestsKeyboardTests(unittest.TestCase):
    def test_has_all_actions_and_back(self):
        d = datas(menus.interests_keyboard())
        for expected in (
            "act:getinterests", "act:addinterest", "int_pick:upd",
            "int_pick:rm", "act:compile", "menu:root",
        ):
            self.assertIn(expected, d)


class RecipesKeyboardTests(unittest.TestCase):
    def test_has_all_actions_and_back(self):
        d = datas(menus.recipes_keyboard())
        for expected in (
            "act:getrecipes", "act:addrecipe", "rcp_pick:view",
            "rcp_pick:upd", "rcp_pick:rm", "menu:root",
        ):
            self.assertIn(expected, d)


class AdminKeyboardTests(unittest.TestCase):
    def test_has_admin_actions_and_back_but_no_adduser(self):
        d = datas(menus.admin_keyboard())
        self.assertEqual(
            set(d), {"act:getusers", "adm_pick_rm", "act:adduserteleid", "menu:root"}
        )


class PickKeyboardTests(unittest.TestCase):
    def test_interest_pick_one_button_per_entry_1_based(self):
        m = menus.interest_pick_keyboard(["a", "b", "c"], action="rm")
        d = datas(m)
        self.assertEqual(d[:3], ["int_rm:1", "int_rm:2", "int_rm:3"])
        self.assertIn("menu:interests", d)  # Back

    def test_interest_pick_upd_uses_upd_prefix(self):
        self.assertEqual(datas(menus.interest_pick_keyboard(["x"], "upd"))[0], "int_upd:1")

    def test_account_pick_one_button_per_account(self):
        m = menus.account_pick_keyboard(["alice", "bob"])
        self.assertEqual(datas(m)[:2], ["adm_rm:alice", "adm_rm:bob"])
        self.assertIn("menu:admin", datas(m))  # Back

    def test_recipe_pick_one_button_per_entry_1_based(self):
        entries = [{"name": "Banana Bread", "body": "..."}, {"name": "Fried Rice", "body": "..."}]
        m = menus.recipe_pick_keyboard(entries, action="rm")
        d = datas(m)
        self.assertEqual(d[:2], ["rcp_rm:1", "rcp_rm:2"])
        self.assertIn("menu:recipes", d)  # Back

    def test_recipe_pick_view_uses_view_prefix(self):
        entries = [{"name": "Banana Bread", "body": "..."}]
        self.assertEqual(datas(menus.recipe_pick_keyboard(entries, "view"))[0], "rcp_view:1")

    def test_recipe_pick_truncates_long_names(self):
        entries = [{"name": "x" * 80, "body": "..."}]
        label = texts(menus.recipe_pick_keyboard(entries, "rm"))[0]
        self.assertEqual(label, f"1. {'x' * 40}")


class ConfirmKeyboardTests(unittest.TestCase):
    def test_yes_no_tokens(self):
        self.assertEqual(datas(menus.confirm_keyboard("endsession")),
                         ["cfm:endsession", "cxl:endsession"])

    def test_callback_data_within_64_bytes(self):
        m = menus.interest_pick_keyboard(["x"] * 50, "rm")
        for d in datas(m):
            self.assertLessEqual(len(d.encode("utf-8")), 64)
