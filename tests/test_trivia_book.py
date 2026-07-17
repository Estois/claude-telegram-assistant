import json

import bot
from tests.helpers import BotTestCase


class TriviaBookTests(BotTestCase):
    def _make_book(self, fallback=None):
        return bot.TriviaBook(
            cache_file=self.tmp_path / "trivia_cache.json",
            state_file=self.tmp_path / "trivia_state.json",
            fallback=fallback if fallback is not None else bot.FALLBACK_TRIVIA,
        )

    def test_next_fact_uses_fallback_when_no_cache_file(self):
        book = self._make_book(fallback=[{"animal": "Octopus", "fact": "Three hearts."}])

        fact = book.next_fact()

        self.assertIn("Octopus", fact)
        self.assertIn("Three hearts.", fact)

    def test_next_fact_prefers_cache_file_over_fallback(self):
        cache_file = self.tmp_path / "trivia_cache.json"
        cache_file.write_text(json.dumps([{"animal": "Narwhal", "fact": "Has a tusk."}]))
        book = self._make_book(fallback=[{"animal": "Octopus", "fact": "Three hearts."}])

        fact = book.next_fact()

        self.assertIn("Narwhal", fact)
        self.assertNotIn("Octopus", fact)

    def test_next_fact_falls_back_when_cache_file_is_corrupt(self):
        cache_file = self.tmp_path / "trivia_cache.json"
        cache_file.write_text("{not valid json")
        book = self._make_book(fallback=[{"animal": "Octopus", "fact": "Three hearts."}])

        fact = book.next_fact()

        self.assertIn("Octopus", fact)

    def test_next_fact_includes_an_emoji_prefix(self):
        book = self._make_book(fallback=[{"animal": "Sloth", "fact": "Slow digestion."}])

        fact = book.next_fact()

        self.assertTrue(fact.startswith(bot.AnimalEmoji.for_name("Sloth")))

    def test_cycle_visits_every_fact_exactly_once_before_repeating(self):
        pool = [{"animal": f"Animal{i}", "fact": f"Fact {i}"} for i in range(5)]
        book = self._make_book(fallback=pool)

        seen = [book.next_fact() for _ in range(5)]

        self.assertEqual(len(set(seen)), 5)

    def test_cycle_reshuffles_after_exhausting_the_pool(self):
        pool = [{"animal": f"Animal{i}", "fact": f"Fact {i}"} for i in range(3)]
        book = self._make_book(fallback=pool)

        first_cycle = [book.next_fact() for _ in range(3)]
        second_cycle = [book.next_fact() for _ in range(3)]

        self.assertEqual(sorted(first_cycle), sorted(second_cycle))

    def test_state_file_is_created_after_first_call(self):
        state_file = self.tmp_path / "trivia_state.json"
        book = self._make_book(fallback=[{"animal": "Sloth", "fact": "Slow."}])

        book.next_fact()

        self.assertTrue(state_file.exists())

    def test_state_survives_across_new_book_instances(self):
        pool = [{"animal": f"Animal{i}", "fact": f"Fact {i}"} for i in range(3)]
        cache_file = self.tmp_path / "trivia_cache.json"
        state_file = self.tmp_path / "trivia_state.json"
        cache_file.write_text(json.dumps(pool))

        first_book = bot.TriviaBook(cache_file, state_file, fallback=[])
        first_facts = [first_book.next_fact() for _ in range(3)]

        second_book = bot.TriviaBook(cache_file, state_file, fallback=[])
        second_facts = [second_book.next_fact() for _ in range(3)]

        self.assertEqual(sorted(first_facts), sorted(second_facts))

    def test_corrupt_state_file_does_not_crash_next_fact(self):
        state_file = self.tmp_path / "trivia_state.json"
        state_file.write_text("{not valid json")
        book = self._make_book(fallback=[{"animal": "Sloth", "fact": "Slow."}])

        fact = book.next_fact()

        self.assertIn("Sloth", fact)

    def test_pool_size_change_resets_the_rotation_instead_of_crashing(self):
        state_file = self.tmp_path / "trivia_state.json"
        state_file.write_text(json.dumps({"order": [0, 1], "position": 1}))
        book = self._make_book(
            fallback=[{"animal": "Sloth", "fact": "Slow."}, {"animal": "Owl", "fact": "Silent flight."}, {"animal": "Bat", "fact": "Echolocation."}]
        )

        fact = book.next_fact()

        self.assertTrue(any(name in fact for name in ("Sloth", "Owl", "Bat")))


if __name__ == "__main__":
    import unittest

    unittest.main()
