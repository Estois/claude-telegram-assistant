"""Claude Code CLI subprocess runner and its per-chat session store."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional, Union

from ykbot._logging import logger
from ykbot.config import Config


class ClaudeSessionStore:
    """Persists chat_id -> Claude session id mappings to disk. chat_id is
    usually a real Telegram chat id, but the background/manual interests
    maintenance jobs pass a synthetic string key instead (one per account,
    so each account's reorganize pass gets its own Claude conversation)."""

    def __init__(self, path: Path):
        self._path = path

    def get(self, chat_id: Union[int, str]) -> Optional[str]:
        return self._read().get(str(chat_id))

    def set(self, chat_id: Union[int, str], session_id: str) -> None:
        sessions = self._read()
        sessions[str(chat_id)] = session_id
        self._write(sessions)

    def clear(self, chat_id: Union[int, str]) -> bool:
        sessions = self._read()
        if sessions.pop(str(chat_id), None) is None:
            return False
        self._write(sessions)
        return True

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            # Degrade gracefully rather than crash every command until the
            # file is hand-fixed — a fresh _write repairs it. Matches how
            # TriviaBook/BusStopsCache/BusLocationsStore treat corruption.
            logger.exception("%s is corrupt", self._path)
            return {}

    def _write(self, sessions: dict) -> None:
        self._path.write_text(json.dumps(sessions, indent=2))



class ClaudeClient:
    """Runs prompts through the Claude Code CLI, keeping one session per chat
    so follow-up commands continue the same conversation."""

    def __init__(self, config: Config, sessions: ClaudeSessionStore):
        self._config = config
        self._sessions = sessions
        self._locks: dict = {}

    def lock_for(self, chat_id: Union[int, str]) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def run(self, chat_id: Union[int, str], prompt: str) -> str:
        session_id = self._sessions.get(chat_id)
        is_new_session = session_id is None
        if is_new_session:
            session_id = str(uuid.uuid4())

        cmd = self._build_command(prompt, session_id, is_new_session)
        logger.info("Running claude for chat %s (session %s)", chat_id, session_id)

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

        # Only persist once claude confirms success, so a failed first turn
        # doesn't leave behind a dead session id.
        self._sessions.set(chat_id, session_id)
        return stdout.decode(errors="replace").strip()

    def _build_command(self, prompt: str, session_id: str, is_new_session: bool) -> list:
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
        cmd += ["--session-id", session_id] if is_new_session else ["--resume", session_id]
        return cmd

