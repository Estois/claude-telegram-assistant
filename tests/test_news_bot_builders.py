import bot
from tests.helpers import BotTestCase, make_config


class YKBotBuilderTests(BotTestCase):
    def _make_bot(self, **config_overrides):
        return bot.YKBot(make_config(self.tmp_path, **config_overrides))

    def test_build_command_list_excludes_admin_only_by_default(self):
        news_bot = self._make_bot()

        listing = news_bot._build_command_list(admin=False)

        self.assertIn("/news", listing)
        self.assertNotIn("/adduser ", listing)
        self.assertNotIn("Admin commands:", listing)

    def test_build_command_list_includes_admin_section_when_admin_true(self):
        news_bot = self._make_bot()

        listing = news_bot._build_command_list(admin=True)

        self.assertIn("Admin commands:", listing)
        self.assertIn("/adduser <name> <sha256hash>", listing)
        self.assertIn("/adduserteleid", listing)

    def test_build_help_text_reports_correct_session_timeout_minutes(self):
        news_bot = self._make_bot(session_timeout_seconds=1800)

        help_text = news_bot._build_help_text()

        self.assertIn("30 minutes", help_text)

    def test_build_welcome_text_includes_the_given_name(self):
        news_bot = self._make_bot()

        welcome = news_bot._build_welcome_text("alice")

        self.assertIn("Welcome, alice!", welcome)

    def test_build_welcome_text_excludes_admin_commands_for_non_admin(self):
        news_bot = self._make_bot()

        welcome = news_bot._build_welcome_text("alice", admin=False)

        self.assertNotIn("Admin commands:", welcome)

    def test_build_welcome_text_includes_admin_commands_for_admin(self):
        news_bot = self._make_bot()

        welcome = news_bot._build_welcome_text("amy", admin=True)

        self.assertIn("Admin commands:", welcome)
        self.assertIn("/removeuser", welcome)

    def test_maintenance_key_format(self):
        self.assertEqual(bot.YKBot._maintenance_key("amy"), "maintenance:amy")

    def test_news_prompt_requires_dates_reputable_sources_and_verification(self):
        news_bot = self._make_bot()

        prompt = news_bot._build_news_prompt("tokyo", "alice").lower()

        self.assertIn('news digest about "tokyo"', prompt)
        self.assertIn("reputable", prompt)   # only reputable sites
        self.assertIn("verify", prompt)      # extra verification effort
        self.assertIn("date", prompt)        # dates for each item
        self.assertIn("source", prompt)      # sources embedded per item

    def test_strip_news_preamble_removes_text_before_header(self):
        reply = (
            "Now I have enough for the digest, with a natural tie-in.\n\n"
            "News Digest — Japan Travel — 2026-07-17\n\n"
            "Costs are going up\n• stuff"
        )
        cleaned = bot.YKBot._strip_news_preamble(reply)
        self.assertTrue(cleaned.startswith("News Digest — Japan Travel"))
        self.assertNotIn("Now I have enough", cleaned)

    def test_strip_news_preamble_leaves_clean_digest_unchanged(self):
        reply = "News Digest — Cybersecurity — 2026-07-17\n\nBody"
        self.assertEqual(bot.YKBot._strip_news_preamble(reply), reply)

    def test_strip_news_preamble_detects_header_inside_bold_tag(self):
        reply = "Preamble line.\n<b>News Digest — Anime — 2026-07-17</b>\nBody"
        cleaned = bot.YKBot._strip_news_preamble(reply)
        self.assertTrue(cleaned.startswith("<b>News Digest — Anime"))
        self.assertNotIn("Preamble line", cleaned)

    def test_strip_news_preamble_returns_unchanged_when_no_header(self):
        reply = "Some reply with no header marker at all."
        self.assertEqual(bot.YKBot._strip_news_preamble(reply), reply)

    def test_account_dir_creates_directory_and_returns_path(self):
        news_bot = self._make_bot()

        path = news_bot._account_dir("alice")

        self.assertTrue(path.is_dir())
        self.assertEqual(path, self.tmp_path / "accounts" / "alice")

    def test_interests_for_points_at_interests_file_within_account_dir(self):
        news_bot = self._make_bot()

        interests = news_bot._interests_for("alice")
        interests.add("cybersecurity")

        expected_file = self.tmp_path / "accounts" / "alice" / "interests.md"
        self.assertTrue(expected_file.exists())
        self.assertEqual(interests.list(), ["cybersecurity"])

    def test_bus_locations_for_points_at_bus_locations_file_within_account_dir(self):
        news_bot = self._make_bot()

        locations = news_bot._bus_locations_for("alice")
        locations.add("Home", "46009")

        expected_file = self.tmp_path / "accounts" / "alice" / "bus_locations.json"
        self.assertTrue(expected_file.exists())
        self.assertEqual(locations.list(), {"Home": "46009"})


if __name__ == "__main__":
    import unittest

    unittest.main()
