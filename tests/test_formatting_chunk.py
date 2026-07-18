import bot
from tests.helpers import BotTestCase

LIMIT = 4000


class ChunkTests(BotTestCase):
    def test_empty_text_yields_placeholder(self):
        self.assertEqual(bot.TelegramFormatter.chunk(""), ["(no output)"])

    def test_paragraphs_are_packed_up_to_the_limit(self):
        para = "x" * 1500
        text = "\n\n".join([para, para, para])  # 4500+ across 3 paragraphs

        chunks = bot.TelegramFormatter.chunk(text)

        self.assertTrue(len(chunks) >= 2)
        self.assertTrue(all(len(c) <= LIMIT for c in chunks))
        # No paragraph content is lost across the split.
        self.assertEqual("".join(chunks).count("x"), 4500)

    def test_single_oversized_paragraph_is_hard_split(self):
        text = "y" * (LIMIT * 2 + 137)

        chunks = bot.TelegramFormatter.chunk(text)

        self.assertTrue(all(len(c) <= LIMIT for c in chunks))
        self.assertEqual("".join(chunks), text)
