import bot
from tests.helpers import BotTestCase, make_config


class ClaudeClientBuildCommandTests(BotTestCase):
    def _client(self, **config_overrides):
        return bot.ClaudeClient(make_config(self.tmp_path, **config_overrides))

    def test_base_flags_present(self):
        cmd = self._client(
            claude_bin="claude",
            claude_model=None,
            claude_permission_mode="bypassPermissions",
        )._build_command("my prompt")

        self.assertEqual(cmd[0], "claude")
        self.assertEqual(cmd[cmd.index("-p") + 1], "my prompt")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "text")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "bypassPermissions")

    def test_no_session_flags_each_call_is_independent(self):
        cmd = self._client()._build_command("hi")

        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--resume", cmd)

    def test_model_flag_included_when_configured(self):
        cmd = self._client(claude_model="opus")._build_command("hi")

        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")

    def test_model_flag_omitted_when_unset(self):
        cmd = self._client(claude_model=None)._build_command("hi")

        self.assertNotIn("--model", cmd)

    def test_lock_for_returns_a_stable_lock_per_key(self):
        client = self._client()

        self.assertIs(client.lock_for(1), client.lock_for(1))
        self.assertIsNot(client.lock_for(1), client.lock_for("maintenance:amy"))
