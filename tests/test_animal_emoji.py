import unittest

import bot


class AnimalEmojiTests(unittest.TestCase):
    def test_exact_name_match(self):
        self.assertEqual(bot.AnimalEmoji.for_name("Cockalier"), "🐕")

    def test_multi_word_phrase_match(self):
        self.assertEqual(bot.AnimalEmoji.for_name("Polar Bear"), "🐻‍❄️")

    def test_breed_word_takes_priority_over_incidental_word(self):
        # "Leopard" alone maps to 🐆, but this is a dog breed (Hound), not
        # a leopard — BREED_WORDS must win the scan before WORDS does.
        self.assertEqual(bot.AnimalEmoji.for_name("American Leopard Hound"), "🐕")
        self.assertEqual(bot.AnimalEmoji.for_name("Bull Terrier"), "🐕")

    def test_plural_word_falls_back_to_singular_match(self):
        self.assertEqual(bot.AnimalEmoji.for_name("Cats"), "🐈")

    def test_unmatched_name_returns_default_paw_print(self):
        self.assertEqual(bot.AnimalEmoji.for_name("Completely Unknown Creature"), "🐾")


if __name__ == "__main__":
    unittest.main()
