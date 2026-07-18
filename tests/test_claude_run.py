import asyncio
from unittest import mock

import bot
from tests.helpers import AsyncBotTestCase, make_config


class FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return


async def _raise_timeout(coro, timeout):
    coro.close()  # avoid "coroutine was never awaited" noise
    raise asyncio.TimeoutError


class ClaudeRunTests(AsyncBotTestCase):
    def _client(self, **overrides):
        config = make_config(self.tmp_path, **overrides)
        self.sessions = bot.ClaudeSessionStore(self.tmp_path / "sessions.json")
        return bot.ClaudeClient(config, self.sessions)

    def _patch_exec(self, proc):
        captured = {}

        async def fake_exec(*args, **kwargs):
            captured["cmd"] = args
            return proc

        return mock.patch("ykbot.claude.asyncio.create_subprocess_exec", fake_exec), captured

    async def test_success_returns_stripped_stdout_and_persists_session(self):
        client = self._client()
        patcher, _ = self._patch_exec(FakeProc(stdout=b"  the digest  \n"))

        with patcher:
            result = await client.run(chat_id=7, prompt="hello")

        self.assertEqual(result, "the digest")
        self.assertIsNotNone(self.sessions.get(7))  # persisted only after success

    async def test_nonzero_exit_raises_with_stderr_and_no_session_persisted(self):
        client = self._client()
        patcher, _ = self._patch_exec(FakeProc(stderr=b"boom", returncode=1))

        with patcher:
            with self.assertRaises(RuntimeError) as ctx:
                await client.run(chat_id=7, prompt="hello")

        self.assertIn("boom", str(ctx.exception))
        self.assertIsNone(self.sessions.get(7))

    async def test_existing_session_is_resumed_and_kept(self):
        client = self._client()
        self.sessions.set(7, "existing-session")
        patcher, captured = self._patch_exec(FakeProc(stdout=b"ok"))

        with patcher:
            await client.run(chat_id=7, prompt="hello")

        self.assertIn("--resume", captured["cmd"])
        self.assertIn("existing-session", captured["cmd"])
        self.assertEqual(self.sessions.get(7), "existing-session")

    async def test_timeout_kills_process_and_raises(self):
        client = self._client(claude_timeout_seconds=1)
        proc = FakeProc()
        patcher, _ = self._patch_exec(proc)

        with patcher, mock.patch("ykbot.claude.asyncio.wait_for", new=_raise_timeout):
            with self.assertRaises(RuntimeError) as ctx:
                await client.run(chat_id=7, prompt="hello")

        self.assertIn("timed out", str(ctx.exception))
        self.assertTrue(proc.killed)
