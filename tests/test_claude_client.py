import bot
from tests.helpers import BotTestCase, make_config


class ClaudeClientBuildCommandTests(BotTestCase):
    def _client(self, **config_overrides):
        config = make_config(self.tmp_path, **config_overrides)
        sessions = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")
        return bot.ClaudeClient(config, sessions)

    def test_new_session_uses_session_id_flag(self):
        cmd = self._client()._build_command("hi", "sess-123", is_new_session=True)

        self.assertIn("--session-id", cmd)
        self.assertEqual(cmd[cmd.index("--session-id") + 1], "sess-123")
        self.assertNotIn("--resume", cmd)

    def test_existing_session_uses_resume_flag(self):
        cmd = self._client()._build_command("hi", "sess-123", is_new_session=False)

        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--resume") + 1], "sess-123")
        self.assertNotIn("--session-id", cmd)

    def test_model_flag_included_when_configured(self):
        cmd = self._client(claude_model="claude-opus-4-8")._build_command(
            "hi", "s", is_new_session=True
        )

        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-4-8")

    def test_base_flags_present_and_model_omitted_when_unset(self):
        cmd = self._client(
            claude_model=None,
            claude_bin="claude",
            claude_permission_mode="bypassPermissions",
        )._build_command("my prompt", "s", is_new_session=True)

        self.assertEqual(cmd[0], "claude")
        self.assertEqual(cmd[cmd.index("-p") + 1], "my prompt")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "text")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "bypassPermissions")
        self.assertNotIn("--model", cmd)

    def test_lock_for_returns_a_stable_lock_per_chat(self):
        client = self._client()

        self.assertIs(client.lock_for(1), client.lock_for(1))
        self.assertIsNot(client.lock_for(1), client.lock_for(2))
