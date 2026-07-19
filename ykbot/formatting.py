"""Telegram message chunking and the command descriptor."""

from dataclasses import dataclass
from typing import Callable


class TelegramFormatter:
    """Splits long text into Telegram-sized chunks, preferring to break on
    paragraph boundaries so HTML tags aren't split across messages."""

    MESSAGE_LIMIT = 4000

    @classmethod
    def chunk(cls, text: str) -> list:
        if not text:
            return ["(no output)"]
        if len(text) <= cls.MESSAGE_LIMIT:
            return [text]

        chunks = []
        current = ""
        for paragraph in text.split("\n\n"):
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= cls.MESSAGE_LIMIT:
                current = candidate
                continue

            if current:
                chunks.append(current)
            current = paragraph
            while len(current) > cls.MESSAGE_LIMIT:
                chunks.append(current[: cls.MESSAGE_LIMIT])
                current = current[cls.MESSAGE_LIMIT :]

        if current:
            chunks.append(current)
        return chunks



@dataclass(frozen=True)
class CommandSpec:
    """One registered command: what it's called, how it's registered with
    Telegram, and how it's described in /help and the post-unlock welcome
    message — a single source of truth so a new command shows up in both
    places just by being added to YKBot._build_command_specs, with
    nothing to keep in sync by hand. admin_only commands are registered
    like any other (the handler itself enforces the admin check) but are
    only listed for an admin session."""

    name: str
    usage: str
    description: str
    handler: Callable
    category: str = "General"
    admin_only: bool = False

