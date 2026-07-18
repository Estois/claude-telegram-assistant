import os
import unittest
from unittest import mock

import bot


class ConfigNewsLearningTests(unittest.TestCase):
    def test_defaults_when_env_unset(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t"}, clear=True):
            config = bot.Config.from_env()
        self.assertTrue(config.news_learning_enabled)
        self.assertEqual(config.news_history_window_days, 60)
        self.assertEqual(config.news_history_max_entries, 100)

    def test_reads_env_overrides(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "t",
            "NEWS_LEARNING_ENABLED": "false",
            "NEWS_HISTORY_WINDOW_DAYS": "30",
            "NEWS_HISTORY_MAX_ENTRIES": "50",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = bot.Config.from_env()
        self.assertFalse(config.news_learning_enabled)
        self.assertEqual(config.news_history_window_days, 30)
        self.assertEqual(config.news_history_max_entries, 50)


if __name__ == "__main__":
    unittest.main()
