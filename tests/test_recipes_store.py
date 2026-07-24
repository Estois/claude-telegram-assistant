import bot
from tests.helpers import BotTestCase


class RecipeStoreTests(BotTestCase):
    def test_add_appends_and_returns_1_based_index(self):
        store = bot.RecipeStore(self.tmp_path / "recipes.json")

        self.assertEqual(store.add("Banana Bread", "Ingredients...\nSteps..."), 1)
        self.assertEqual(store.add("Fried Rice", "Ingredients...\nSteps..."), 2)
        self.assertEqual(
            store.list(),
            [
                {"name": "Banana Bread", "body": "Ingredients...\nSteps..."},
                {"name": "Fried Rice", "body": "Ingredients...\nSteps..."},
            ],
        )

    def test_list_on_missing_file_returns_empty_list(self):
        store = bot.RecipeStore(self.tmp_path / "recipes.json")

        self.assertEqual(store.list(), [])

    def test_body_preserves_multiple_lines(self):
        store = bot.RecipeStore(self.tmp_path / "recipes.json")
        body = "Ingredients\n• rice\n• eggs\n\nSteps\n1. cook\n2. eat"

        store.add("Fried Rice", body)

        self.assertEqual(store.list()[0]["body"], body)

    def test_remove_deletes_and_returns_the_entry(self):
        store = bot.RecipeStore(self.tmp_path / "recipes.json")
        store.add("A", "a-body")
        store.add("B", "b-body")
        store.add("C", "c-body")

        removed = store.remove(2)

        self.assertEqual(removed, {"name": "B", "body": "b-body"})
        self.assertEqual([e["name"] for e in store.list()], ["A", "C"])

    def test_remove_out_of_range_returns_none_and_keeps_list(self):
        store = bot.RecipeStore(self.tmp_path / "recipes.json")
        store.add("only one", "body")

        self.assertIsNone(store.remove(5))
        self.assertIsNone(store.remove(0))
        self.assertEqual(len(store.list()), 1)

    def test_update_replaces_entry_at_index(self):
        store = bot.RecipeStore(self.tmp_path / "recipes.json")
        store.add("Old Name", "old body")
        store.add("Other", "other body")

        self.assertTrue(store.update(1, "New Name", "new body"))
        self.assertEqual(store.list()[0], {"name": "New Name", "body": "new body"})
        self.assertEqual(store.list()[1], {"name": "Other", "body": "other body"})

    def test_update_with_out_of_range_index_returns_false_and_leaves_list_unchanged(self):
        store = bot.RecipeStore(self.tmp_path / "recipes.json")
        store.add("A", "a-body")

        self.assertFalse(store.update(5, "New", "new body"))
        self.assertFalse(store.update(0, "New", "new body"))
        self.assertEqual(store.list(), [{"name": "A", "body": "a-body"}])
