import unittest

import bot


class TelegramFormatterTests(unittest.TestCase):
    def test_empty_text_becomes_placeholder(self):
        self.assertEqual(bot.TelegramFormatter.chunk(""), ["(no output)"])

    def test_text_under_the_limit_is_returned_as_a_single_chunk(self):
        text = "short reply, well under the 4000-char limit"

        self.assertEqual(bot.TelegramFormatter.chunk(text), [text])

    def test_long_text_splits_on_paragraph_boundaries(self):
        paragraph_one = "A" * 3000
        paragraph_two = "B" * 3000
        text = f"{paragraph_one}\n\n{paragraph_two}"

        chunks = bot.TelegramFormatter.chunk(text)

        self.assertEqual(chunks, [paragraph_one, paragraph_two])
        self.assertTrue(all(len(c) <= bot.TelegramFormatter.MESSAGE_LIMIT for c in chunks))


if __name__ == "__main__":
    unittest.main()
