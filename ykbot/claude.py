"""Claude Code CLI subprocess runner.

Each command is an independent one-shot ``claude -p`` invocation — there is no
session continuity between calls, so ``/news`` and the interest commands never
carry context over from a previous request. Auth comes from the Claude Code
CLI's own login (``~/.claude``), covered by the user's Claude subscription —
no per-token API billing.
"""

import asyncio
from typing import Union

from ykbot._logging import logger
from ykbot.config import Config


class ClaudeClient:
    """Runs one-shot prompts through the Claude Code CLI."""

    def __init__(self, config: Config):
        self._config = config
        self._locks: dict = {}

    def lock_for(self, chat_id: Union[int, str]) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def run(self, prompt: str) -> str:
        cmd = self._build_command(prompt)
        logger.info("Running claude (model=%s)", self._config.claude_model or "default")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self._config.claude_workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._config.claude_timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"claude timed out after {self._config.claude_timeout_seconds}s")

        if proc.returncode != 0:
            error_output = stderr.decode(errors="replace").strip()
            raise RuntimeError(error_output or f"claude exited with code {proc.returncode}")

        return stdout.decode(errors="replace").strip()

    def _build_command(self, prompt: str) -> list:
        cmd = [
            self._config.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "text",
            "--permission-mode",
            self._config.claude_permission_mode,
        ]
        if self._config.claude_model:
            cmd += ["--model", self._config.claude_model]
        return cmd
