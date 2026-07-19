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
        )._build_command("my prompt", web_search=False)

        self.assertEqual(cmd[0], "claude")
        self.assertEqual(cmd[cmd.index("-p") + 1], "my prompt")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "text")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "bypassPermissions")

    def test_no_session_flags_each_call_is_independent(self):
        cmd = self._client()._build_command("hi", web_search=False)

        self.assertNotIn("--session-id", cmd)
        self.assertNotIn("--resume", cmd)

    def test_model_flag_included_when_configured(self):
        cmd = self._client(claude_model="opus")._build_command("hi", web_search=False)

        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")

    def test_model_flag_omitted_when_unset(self):
        cmd = self._client(claude_model=None)._build_command("hi", web_search=False)

        self.assertNotIn("--model", cmd)

    def test_no_tools_without_web_search(self):
        # Security boundary: no filesystem/shell tools are ever available.
        cmd = self._client()._build_command("hi", web_search=False)

        self.assertIn("--tools", cmd)
        tools = cmd[cmd.index("--tools") + 1 :]
        self.assertEqual(tools, [""])  # empty = no tools at all
        for banned in ("Bash", "Write", "Edit", "Read", "Glob", "Grep"):
            self.assertNotIn(banned, cmd)

    def test_only_web_tools_with_web_search(self):
        cmd = self._client()._build_command("hi", web_search=True)

        tools = cmd[cmd.index("--tools") + 1 :]
        self.assertEqual(tools, ["WebSearch", "WebFetch"])
        for banned in ("Bash", "Write", "Edit", "Read"):
            self.assertNotIn(banned, cmd)

    def test_tools_flag_is_last_so_variadic_does_not_swallow_others(self):
        cmd = self._client(claude_model="opus")._build_command("hi", web_search=True)

        self.assertEqual(cmd.index("--tools"), len(cmd) - 3)  # --tools WebSearch WebFetch

    def test_lock_for_returns_a_stable_lock_per_key(self):
        client = self._client()

        self.assertIs(client.lock_for(1), client.lock_for(1))
        self.assertIsNot(client.lock_for(1), client.lock_for("maintenance:amy"))
