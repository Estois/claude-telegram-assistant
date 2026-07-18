"""The YKBot orchestrator: wires the Telegram handlers to the services.

This is the one stateful class; every ``self._x`` it touches is set up in
``__init__`` right here, so the whole object reads top-to-bottom in one file.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional, Union

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ykbot._logging import logger
from ykbot.accounts import AccountStore, AllowlistStore
from ykbot.bus import BusLocationsStore, BusStopsCache, LtaBusClient
from ykbot.claude import ClaudeClient, ClaudeSessionStore
from ykbot.config import Config
from ykbot.formatting import CommandSpec, TelegramFormatter
from ykbot.interests import InterestsStore
from ykbot.news_history import NewsHistoryStore
from ykbot.trivia import FALLBACK_TRIVIA, TriviaBook

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


class YKBot:
    """Wires the Telegram handlers together with the services above."""

    INTERESTS_FILENAME = "interests.md"
    BUS_LOCATIONS_FILENAME = "bus_locations.json"
    NEWS_HISTORY_FILENAME = "news_history.jsonl"

    USAGE_LIMIT_PATTERN = re.compile(r"usage limit", re.IGNORECASE)
    USAGE_LIMIT_RESET_PATTERN = re.compile(r"reset[s]?\s+(?:at|on|in)?\s*([^.\n]+)", re.IGNORECASE)

    def __init__(self, config: Config):
        self._config = config
        self._accounts = AccountStore(config.accounts_file, config.session_timeout_seconds)
        self._allowlist = AllowlistStore(config.env_file, config.allowed_user_ids)
        self._sessions = ClaudeSessionStore(config.sessions_file)
        self._claude = ClaudeClient(config, self._sessions)
        self._trivia = TriviaBook(config.trivia_cache_file, config.trivia_state_file, FALLBACK_TRIVIA)
        self._lta = LtaBusClient(config.lta_api_key)
        self._bus_stops = BusStopsCache(config.bus_stops_cache_file)
        self._command_specs = self._build_command_specs()
        self._reorganize_task: Optional[asyncio.Task] = None

    def _account_dir(self, account_id: str) -> Path:
        path = self._config.accounts_dir / account_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _interests_for(self, account_id: str) -> InterestsStore:
        return InterestsStore(self._account_dir(account_id) / self.INTERESTS_FILENAME)

    def _news_history_for(self, account_id: str) -> NewsHistoryStore:
        return NewsHistoryStore(self._account_dir(account_id) / self.NEWS_HISTORY_FILENAME)

    def _recent_theme_counts(self, account_id: str) -> list:
        """Recent /news request themes with counts for this account, or an
        empty list when learning is disabled. Shared by the daily reorganize
        job and the manual /compileinterests command so they behave the same."""
        if not self._config.news_learning_enabled:
            return []
        return self._news_history_for(account_id).theme_counts(
            self._config.news_history_window_days,
            self._config.news_history_max_entries,
        )

    def _bus_locations_for(self, account_id: str) -> BusLocationsStore:
        return BusLocationsStore(self._account_dir(account_id) / self.BUS_LOCATIONS_FILENAME)

    @staticmethod
    def _maintenance_key(account_id: str) -> str:
        """Synthetic ClaudeClient/session key for an account's background
        interests-reorganization conversation — never a real Telegram chat
        id, so it can't collide with one."""
        return f"maintenance:{account_id}"

    def run(self) -> None:
        self._warn_about_missing_config()
        self._build_application().run_polling()

    def _build_command_specs(self) -> list:
        """Single source of truth for every user-facing command: what
        registers it with Telegram (_build_application) and what describes
        it in /help and the post-unlock welcome message
        (_build_command_list) both read from this same list, so a new
        command only needs to be added here once."""
        return [
            CommandSpec(
                "news", "/news <subject>",
                "news digest on a subject, biased toward your interests", self.handle_news,
            ),
            CommandSpec(
                "addinterests", "/addinterests <topic>",
                "add an interest (word, sentence, or paragraph)", self.handle_add_interests,
            ),
            CommandSpec(
                "getinterests", "/getinterests",
                "list your interests with their index", self.handle_get_interests,
            ),
            CommandSpec(
                "updateinterests", "/updateinterests <index> <topic>",
                "revise or rewrite an interest (Claude decides which)", self.handle_update_interests,
            ),
            CommandSpec(
                "compileinterests", "/compileinterests",
                "merge duplicate interests and add retrieval detail now (runs automatically too)",
                self.handle_compile_interests,
            ),
            CommandSpec(
                "bus", "/bus",
                "bus arrival times, from a preset stop or your live location", self.handle_bus,
            ),
            CommandSpec(
                "endsession", "/endsession",
                "end the current session now", self.handle_end_session,
            ),
            CommandSpec(
                "clearclaudememory", "/clearclaudememory",
                "clear Claude's memory of this conversation and start fresh", self.handle_clear_claude_memory,
            ),
            CommandSpec("help", "/help", "show this message", self.handle_help),
            CommandSpec(
                "adduser", "/adduser <name> <sha256hash>",
                "create an account, or add a password to an existing one", self.handle_add_user,
                admin_only=True,
            ),
            CommandSpec(
                "removeuser", "/removeuser <name>",
                "delete an account's password(s) from accounts.hash", self.handle_remove_user,
                admin_only=True,
            ),
            CommandSpec(
                "adduserteleid", "/adduserteleid <telegram_user_id>",
                "allow a Telegram user id to message the bot", self.handle_add_user_tele_id,
                admin_only=True,
            ),
            CommandSpec(
                "getusers", "/getusers",
                "list accounts and whether each has an active session", self.handle_get_users,
                admin_only=True,
            ),
        ]

    def _build_command_list(self, admin: bool = False) -> str:
        lines = [
            f"{spec.usage} — {spec.description}"
            for spec in self._command_specs
            if not spec.admin_only
        ]
        if admin:
            admin_lines = [
                f"{spec.usage} — {spec.description}"
                for spec in self._command_specs
                if spec.admin_only
            ]
            if admin_lines:
                lines += ["", "Admin commands:"] + admin_lines
        return "\n".join(lines)

    def _build_help_text(self, admin: bool = False) -> str:
        minutes = self._config.session_timeout_seconds // 60
        return (
            "Every command needs an unlocked session — send the password as a plain "
            f"message first (lasts {minutes} minutes). Interests and preferred bus "
            "stops belong to whichever account your password unlocked.\n\n"
            "Commands:\n" + self._build_command_list(admin)
        )

    def _build_welcome_text(self, name: str, admin: bool = False) -> str:
        minutes = self._config.session_timeout_seconds // 60
        return (
            f"👋 Welcome, {name}! Session active for {minutes} minutes.\n\n"
            "Commands:\n" + self._build_command_list(admin) + "\n\n"
            "Anything I don't recognize — an unknown command, or any plain message — "
            "gets you a random animal fact instead of an error, just for fun."
        )

    def _build_application(self) -> Application:
        app = (
            Application.builder()
            .token(self._config.telegram_token)
            .post_init(self._on_startup)
            .post_shutdown(self._on_shutdown)
            .build()
        )
        # /start isn't in _command_specs — it's just a generic greeting, not
        # a documented capability, so it stays out of /help and the welcome
        # message's command list.
        app.add_handler(CommandHandler("start", self.handle_start))
        for spec in self._command_specs:
            app.add_handler(CommandHandler(spec.name, spec.handler))
        app.add_handler(CallbackQueryHandler(self.handle_bus_callback, pattern=r"^bus_stop:"))
        app.add_handler(CallbackQueryHandler(self.handle_bus_fav_add, pattern=r"^bus_fav_add:"))
        app.add_handler(CallbackQueryHandler(self.handle_bus_fav_remove, pattern=r"^bus_fav_remove:"))
        app.add_handler(
            CallbackQueryHandler(self.handle_bus_request_location, pattern=r"^bus_request_location$")
        )
        app.add_handler(MessageHandler(filters.LOCATION, self.handle_location))
        app.add_handler(MessageHandler(filters.COMMAND, self.handle_unknown_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_plain_text))
        return app

    def _warn_about_missing_config(self) -> None:
        if self._allowlist.ids is None:
            logger.warning(
                "TELEGRAM_ALLOWED_USER_IDS is not set - anyone who messages this bot can run "
                "Claude Code with permission checks bypassed (%s). Set it before exposing this bot.",
                self._config.claude_permission_mode,
            )
        if not self._accounts.is_configured:
            logger.warning(
                "No accounts configured in %s — every command will stay locked until you add one.",
                self._config.accounts_file,
            )
        if not self._lta.is_configured:
            logger.warning(
                "LTA_API_KEY is not set — /bus will not be able to fetch arrival times."
            )

    def _is_authorized(self, update: Update) -> bool:
        authorized = self._allowlist.is_allowed(update.effective_user.id)
        if not authorized:
            logger.warning("Ignoring message from unauthorized user %s", update.effective_user.id)
        return authorized

    def _is_admin(self, update: Update, account_id: str) -> bool:
        """Admin commands require both the designated admin account AND the
        designated admin's own Telegram user id — either alone isn't
        enough, so knowing the admin account's password doesn't grant
        admin powers from a different Telegram user."""
        return (
            account_id == self._config.admin_account_id
            and update.effective_user.id == self._config.admin_telegram_user_id
        )

    async def _require_session(self, update: Update) -> Optional[str]:
        """Every command runs behind the password gate. The one exception is
        handle_plain_text, which is the unlock mechanism itself — gating it
        too would make it impossible to ever unlock. Checks authorization
        first; unauthorized senders get silence, so the bot doesn't confirm
        its own existence to strangers. A locked-but-authorized sender gets
        the same random animal-fact reply as an unrecognized command or
        wrong password, never a "locked" notice — that string would confirm
        the command exists and the bot uses a password gate, information an
        attacker probing the bot shouldn't get for free. Returns the
        unlocked account id, or None if the caller should stop (already
        replied with trivia in that case)."""
        if not self._is_authorized(update):
            return None
        account_id = self._accounts.account_for(update.effective_chat.id)
        if account_id is None:
            # effective_message (not .message) so this also works for
            # callback-query updates, where .message is None but
            # .callback_query.message holds the original message.
            await update.effective_message.reply_text(self._trivia.next_fact())
            return None
        return account_id

    async def _require_admin(self, update: Update) -> Optional[str]:
        """Like _require_session, but for the admin-only commands. Non-admin
        callers (wrong account, wrong Telegram user, or not even unlocked)
        get the same trivia reply as an unrecognized command — no hint that
        an admin command exists, let alone which check failed."""
        account_id = await self._require_session(update)
        if account_id is None:
            return None
        if not self._is_admin(update, account_id):
            await update.message.reply_text(self._trivia.next_fact())
            return None
        return account_id

    def _build_news_prompt(self, subject: str, account_id: str) -> str:
        interests_file = self._account_dir(account_id) / self.INTERESTS_FILENAME
        return (
            f"Silently read {interests_file} for context on my personal interests — "
            "do not mention this file, its contents, or whether it has been filled in "
            "anywhere in your reply. "
            f'Write a news digest about "{subject}", covering the most significant recent '
            "developments, biased toward my documented interests where they're relevant to "
            "this subject (don't force a connection that isn't there). "
            "Output ONLY the digest itself — no preamble, no meta-commentary about your "
            "process or sources of context. "
            "Format it for a Telegram message using only these HTML tags: <b>, <i>, <u>, "
            '<s>, <code>, <pre>, and <a href="URL">text</a>. Do not use Markdown syntax '
            "(no **, no #, no [text](url)). Use short paragraphs and simple bullet points "
            "(•) for scanability. Escape any literal <, >, or & characters that aren't part "
            "of a tag."
        )

    @staticmethod
    def _build_update_interest_prompt(old_entry: str, new_input: str) -> str:
        return (
            "I'm editing one entry in a personal interests list used to bias news "
            f'digests. Existing entry: "{old_entry}"\n'
            f'New input: "{new_input}"\n\n'
            "Decide whether the new input is meant to add detail to the existing "
            "entry or fully replace it, and produce the resulting entry text "
            "accordingly. Reply with ONLY the final entry text as a single plain-text "
            "paragraph — no quotes, no labels, no explanation, no formatting, nothing "
            "else."
        )

    @staticmethod
    def _build_reorganize_prompt(entries: list, theme_counts: list) -> str:
        numbered = "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))
        history_block = ""
        if theme_counts:
            lines = "\n".join(
                f"- {subject} (requested {count}x)" for subject, count in theme_counts
            )
            history_block = (
                "\n\nSeparately, here are the topics I've recently asked /news to cover, "
                f"with how many times I asked for each:\n{lines}\n\n"
                "Fold a topic from this request history into the interests list ONLY if "
                "it recurs (requested more than once) or is clearly significant — never "
                "add a one-off request. Do not remove or override any interest already "
                "in the list above."
            )
        return (
            "Here is my current news-interests list, used to bias what /news digests "
            f"cover:\n\n{numbered}\n"
            + history_block
            + "\n\nReorganize this list: merge entries that are duplicates or clearly the "
            "same underlying topic, and where it would help a news-search assistant "
            "retrieve more relevant results, add a short blurb of extra detail to an "
            "entry — specific sub-topics, entities, or angles reasonably implied by what's "
            "already there. Don't invent interests the list doesn't imply, and don't drop "
            "any distinct topic. "
            "Reply with ONLY the resulting list, renumbered from 1, one entry per line, in "
            "the exact form '<number>. <entry text>' — plain text, no extra commentary, no "
            "headers, no markdown."
        )

    # -- Command handlers -------------------------------------------------

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        await update.message.reply_text("Hi!")

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        await update.message.reply_text(self._build_help_text(self._is_admin(update, account_id)))

    async def handle_clear_claude_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        self._sessions.clear(update.effective_chat.id)
        await update.message.reply_text("Cleared Claude's memory.")

    async def handle_end_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        ended = self._accounts.lock(update.effective_chat.id)
        await update.message.reply_text("Session ended." if ended else "No active session.")

    async def handle_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        if not context.args:
            await update.message.reply_text("Usage: /news <subject>\nExample: /news cybersecurity")
            return

        subject = " ".join(context.args)
        if self._config.news_learning_enabled:
            try:
                self._news_history_for(account_id).append(subject)
            except Exception:
                # Logging the subject must never break the digest reply.
                logger.exception("Failed to log /news subject for %s", account_id)
        reply = await self._run_claude(update, context, self._build_news_prompt(subject, account_id))
        if reply is not None:
            await self._send_formatted(update, reply)

    async def handle_add_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: /addinterests <topic>\nExample: /addinterests cybersecurity, "
                "especially supply chain attacks"
            )
            return
        topic = " ".join(context.args)
        index = self._interests_for(account_id).add(topic)
        await update.message.reply_text(f"Added as interest #{index}.")

    async def handle_get_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        entries = self._interests_for(account_id).list()
        if not entries:
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return
        await update.message.reply_text(self._format_interests_listing(entries))

    async def handle_update_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /updateinterests <index> <topic>\nExample: /updateinterests 2 "
                "also mention zero-day exploits"
            )
            return

        index_arg, *topic_parts = context.args
        if not index_arg.isdigit():
            await update.message.reply_text("Index must be a number. See /getinterests for valid indices.")
            return

        index = int(index_arg)
        interests = self._interests_for(account_id)
        entries = interests.list()
        if not entries:
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return
        if not 1 <= index <= len(entries):
            await update.message.reply_text(
                f"No interest #{index}. Valid range: 1-{len(entries)}. See /getinterests."
            )
            return

        topic = " ".join(topic_parts)
        prompt = self._build_update_interest_prompt(entries[index - 1], topic)
        new_text = await self._run_claude(update, context, prompt)
        if new_text is None:
            return

        interests.update(index, new_text)
        await update.message.reply_text(f"Updated interest #{index}.")

    async def handle_compile_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manual trigger for the same reorganization the background job
        runs periodically: merge duplicate/overlapping interests and add
        retrieval-helpful detail."""
        account_id = await self._require_session(update)
        if account_id is None:
            return

        interests = self._interests_for(account_id)
        entries = interests.list()
        if len(entries) < 2:
            await update.message.reply_text("Need at least 2 interests to reorganize. See /getinterests.")
            return

        theme_counts = self._recent_theme_counts(account_id)
        prompt = self._build_reorganize_prompt(entries, theme_counts)
        raw_reply = await self._run_claude(
            update, context, prompt, chat_id=self._maintenance_key(account_id)
        )
        if raw_reply is None:
            return

        new_entries = InterestsStore.parse_entries(raw_reply)
        if not new_entries:
            await update.message.reply_text("Reorganize returned nothing usable — no changes made.")
            return

        interests.replace_all(new_entries)
        summary = f"🗂 Reorganized interests: {len(entries)} → {len(new_entries)} entries."
        await update.message.reply_text(
            f"{summary}\n\n{self._format_interests_listing(new_entries)}"
        )

    @staticmethod
    def _format_interests_listing(entries: list) -> str:
        return "\n".join(f"{i}. {entry}" for i, entry in enumerate(entries, start=1))

    # -- Admin commands -----------------------------------------------------

    async def handle_add_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_admin(update) is None:
            return

        if not context.args or len(context.args) != 2:
            await update.message.reply_text("Usage: /adduser <name> <sha256passwordhash>")
            return

        name, password_hash = context.args
        if not AccountStore.ACCOUNT_ID_PATTERN.match(name):
            await update.message.reply_text(
                "Name must be letters, digits, - or _ only (it's used as a folder name)."
            )
            return
        if not re.fullmatch(r"[0-9a-fA-F]{64}", password_hash):
            await update.message.reply_text(
                "That doesn't look like a SHA256 hex digest (64 hex characters). "
                "Generate one with: printf '%s' 'password' | sha256sum"
            )
            return

        created = self._accounts.add_hash(name, password_hash)
        await update.message.reply_text(
            f"Created account '{name}' with 1 password."
            if created
            else f"Added a password to existing account '{name}'."
        )

    async def handle_remove_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_admin(update) is None:
            return

        if not context.args or len(context.args) != 1:
            await update.message.reply_text("Usage: /removeuser <name>")
            return

        name = context.args[0]
        if name == self._config.admin_account_id:
            await update.message.reply_text("Refusing to remove the admin account.")
            return

        removed = self._accounts.remove_account(name)
        await update.message.reply_text(
            f"Removed account '{name}' from {self._config.accounts_file.name}."
            if removed
            else f"No account named '{name}'."
        )

    async def handle_add_user_tele_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_admin(update) is None:
            return

        if not context.args or len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("Usage: /adduserteleid <telegram_user_id>")
            return

        user_id = int(context.args[0])
        added = self._allowlist.add(user_id)
        await update.message.reply_text(
            f"Added {user_id} to the allowlist." if added else f"{user_id} is already allowed."
        )

    async def handle_get_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_admin(update) is None:
            return

        await update.message.reply_text(self._format_user_listing())

    def _format_user_listing(self) -> str:
        account_ids = self._accounts.list_ids()
        if not account_ids:
            return "No accounts configured."
        return "\n".join(
            f"{account_id} — {'🟢 active' if self._accounts.has_active_session(account_id) else '🔒 locked'}"
            for account_id in account_ids
        )

    async def handle_bus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        locations = self._bus_locations_for(account_id).list()
        buttons = [
            [InlineKeyboardButton(name, callback_data=f"bus_stop:{code}")]
            for name, code in locations.items()
        ]
        buttons.append(
            [InlineKeyboardButton("📍 Send my location", callback_data="bus_request_location")]
        )
        await update.message.reply_text("Pick a stop:", reply_markup=InlineKeyboardMarkup(buttons))

    async def handle_bus_request_location(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if await self._require_session(update) is None:
            await query.answer()
            return

        await query.answer()
        await query.edit_message_text(
            "Reply to this message with your location — tap 📎 (attach) and choose "
            "Location — and I'll show the nearest stops."
        )

    async def handle_bus_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        text = await self._format_arrivals(stop_code)
        await query.edit_message_text(text, reply_markup=self._build_arrivals_keyboard(stop_code, account_id))

    async def handle_bus_fav_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        name = self._describe_stop(stop_code)
        self._bus_locations_for(account_id).add(name, stop_code)

        text = await self._format_arrivals(stop_code)
        await query.edit_message_text(
            f'⭐ Added "{name}" to preferred stops.\n\n{text}',
            reply_markup=self._build_arrivals_keyboard(stop_code, account_id),
        )

    async def handle_bus_fav_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        self._bus_locations_for(account_id).remove_by_code(stop_code)

        text = await self._format_arrivals(stop_code)
        await query.edit_message_text(
            f"🗑 Removed from preferred stops.\n\n{text}",
            reply_markup=self._build_arrivals_keyboard(stop_code, account_id),
        )

    def _describe_stop(self, stop_code: str) -> str:
        stop = self._bus_stops.find_by_code(stop_code)
        if stop:
            return f"{stop['Description']} ({stop_code})"
        return f"Stop {stop_code}"

    def _build_arrivals_keyboard(self, stop_code: str, account_id: str) -> InlineKeyboardMarkup:
        if self._bus_locations_for(account_id).has_code(stop_code):
            fav_button = InlineKeyboardButton(
                "🗑 Remove from preferred", callback_data=f"bus_fav_remove:{stop_code}"
            )
        else:
            fav_button = InlineKeyboardButton(
                "⭐ Add as preferred", callback_data=f"bus_fav_add:{stop_code}"
            )
        return InlineKeyboardMarkup([[fav_button]])

    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return

        if not self._bus_stops.is_available:
            await update.message.reply_text(
                "Nearby-stop lookup isn't set up yet — run fetch_bus_stops.py first, "
                "or use a preset location via /bus."
            )
            return

        loc = update.message.location
        nearest = self._bus_stops.nearest(loc.latitude, loc.longitude, self._config.bus_nearby_count)
        if not nearest:
            await update.message.reply_text("No bus stops found nearby.")
            return

        buttons = [
            [
                InlineKeyboardButton(
                    f"{stop['Description']} ({int(stop['distance_m'])}m)",
                    callback_data=f"bus_stop:{stop['BusStopCode']}",
                )
            ]
            for stop in nearest
        ]
        await update.message.reply_text("Nearest stops:", reply_markup=InlineKeyboardMarkup(buttons))

    async def _format_arrivals(self, stop_code: str) -> str:
        if not self._lta.is_configured:
            return "LTA_API_KEY is not configured — can't fetch arrival times."

        try:
            services = await self._lta.get_arrivals(stop_code)
        except Exception:
            logger.exception("LTA bus arrival lookup failed for stop %s", stop_code)
            return f"Couldn't fetch arrival times for stop {stop_code}. Try again shortly."

        if not services:
            return f"🚌 Stop {stop_code}\nNo bus data available right now."

        lines = [f"🚌 Stop {stop_code}"]
        for svc in services:
            lines.append(f"Svc {svc['service_no']}: " + ", ".join(svc["arrivals"]))
        return "\n".join(lines)

    async def handle_unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        # Unrecognized commands get trivia rather than an error, same as a
        # wrong password — see handle_plain_text.
        await update.message.reply_text(self._trivia.next_fact())

    async def handle_plain_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Non-command text is always treated as a password attempt, never
        as a prompt to relay to Claude."""
        if not self._is_authorized(update):
            return

        chat_id = update.effective_chat.id
        attempt = (update.message.text or "").strip()

        account_id = self._accounts.try_unlock(chat_id, attempt)
        if account_id is not None:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            except Exception:
                logger.exception("Failed to delete password message")
            name = update.effective_user.full_name or "there"
            admin = self._is_admin(update, account_id)
            await context.bot.send_message(chat_id=chat_id, text=self._build_welcome_text(name, admin))
            return

        # Wrong password (or none configured yet) — no hint that this was an
        # auth attempt, just a generic reply.
        await update.message.reply_text(self._trivia.next_fact())

    # -- Claude dispatch ----------------------------------------------------

    @classmethod
    def _usage_limit_reset_notice(cls, text: str) -> Optional[str]:
        """If text (an error message, or Claude's own reply — e.g. explaining
        it couldn't finish a web search/scrape because it hit its usage
        limit mid-task) mentions a usage limit, returns a short "resets
        <when>" fragment pulled from Claude's own wording, so we never have
        to hardcode a reset schedule or timezone ourselves. Falls back to
        "soon" if a limit is mentioned but no reset time could be parsed out
        of it. Returns None if text doesn't look like a usage-limit message
        at all."""
        if not cls.USAGE_LIMIT_PATTERN.search(text):
            return None
        match = cls.USAGE_LIMIT_RESET_PATTERN.search(text)
        return match.group(1).strip() if match else "soon"

    @staticmethod
    def _build_usage_limit_message(reset_notice: str) -> str:
        return (
            f"⏳ Claude's usage limit has been reached — it resets {reset_notice}. "
            "Please try again after that."
        )

    async def _reply_to_claude_failure(self, update: Update, exc: Exception) -> None:
        reset_notice = self._usage_limit_reset_notice(str(exc))
        if reset_notice is not None:
            await update.message.reply_text(self._build_usage_limit_message(reset_notice))
        else:
            await update.message.reply_text(f"Something went wrong: {exc}")

    async def _run_claude(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        prompt: str,
        *,
        chat_id: Optional[Union[int, str]] = None,
    ) -> Optional[str]:
        """Runs prompt through Claude and returns the raw reply. Uses the
        Telegram chat's own Claude session by default; pass chat_id to
        target a different one (e.g. an account's maintenance session).
        Returns None if a busy/usage-limit/error message was already sent to
        the user and the caller should stop.

        Usage-limit detection lives only on the failure path (the CLI exits
        non-zero when it hits a limit — its authoritative signal). A
        successful reply is never second-guessed, so a legitimate digest
        that merely discusses usage limits as a topic is passed through
        untouched instead of being mistaken for a limit notice."""
        telegram_chat_id = update.effective_chat.id
        claude_chat_id = chat_id if chat_id is not None else telegram_chat_id

        lock = self._claude.lock_for(claude_chat_id)
        if lock.locked():
            await update.message.reply_text("Still working on your last request — hang on.")
            return None

        async with lock:
            keep_typing = asyncio.create_task(self._keep_typing(context, telegram_chat_id))
            try:
                return await self._claude.run(claude_chat_id, prompt)
            except Exception as exc:
                logger.exception("claude invocation failed")
                await self._reply_to_claude_failure(update, exc)
                return None
            finally:
                keep_typing.cancel()

    async def _send_formatted(self, update: Update, text: str) -> None:
        """Sends Claude's reply, chunked for Telegram's message limit. Tries
        HTML formatting first and falls back to plain text per chunk if the
        markup doesn't parse — LLM-generated tags aren't guaranteed
        well-formed, so a broken reply must never mean a lost reply."""
        for chunk in TelegramFormatter.chunk(text):
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
            except BadRequest:
                logger.warning("Telegram rejected HTML formatting, sending chunk as plain text")
                await update.message.reply_text(chunk)

    @staticmethod
    async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        try:
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    # -- Background interests maintenance ------------------------------

    async def _on_startup(self, app: Application) -> None:
        interval = self._config.interests_reorganize_interval_seconds
        if interval > 0:
            self._reorganize_task = asyncio.create_task(self._reorganize_loop(interval))

    async def _on_shutdown(self, app: Application) -> None:
        if self._reorganize_task:
            self._reorganize_task.cancel()

    async def _reorganize_loop(self, interval_seconds: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    await self._reorganize_interests()
                except Exception:
                    logger.exception("Interests reorganization failed")
        except asyncio.CancelledError:
            pass

    async def _reorganize_interests(self) -> None:
        for account_id in self._accounts.list_ids():
            try:
                await self._reorganize_account(account_id)
            except Exception:
                logger.exception("Interests reorganization failed for account %s", account_id)

    async def _reorganize_account(self, account_id: str) -> None:
        # Shares the maintenance lock with /compileinterests (for this
        # account) so the two never run the same Claude session concurrently.
        # The result is logged, not broadcast: pushing one account's
        # reorganize summary to every allowlisted Telegram user would leak
        # that account's id and interest counts across users. The owner
        # sees the fresh list on their next /getinterests.
        maintenance_key = self._maintenance_key(account_id)
        async with self._claude.lock_for(maintenance_key):
            interests = self._interests_for(account_id)
            entries = interests.list()
            theme_counts = self._recent_theme_counts(account_id)

            # Nothing to do without either enough interests to reorganize or any
            # request history to learn from.
            if len(entries) < 2 and not theme_counts:
                return

            prompt = self._build_reorganize_prompt(entries, theme_counts)
            raw_reply = await self._claude.run(maintenance_key, prompt)
            new_entries = InterestsStore.parse_entries(raw_reply)
            if not new_entries:
                logger.warning(
                    "Interests reorganize for %s returned nothing parseable, leaving its interests.md untouched",
                    account_id,
                )
            else:
                interests.replace_all(new_entries)
                logger.info(
                    "Reorganized interests for %s: %d -> %d entries",
                    account_id, len(entries), len(new_entries),
                )

            # Prune the log to the window after the daily pass (the manual
            # /compileinterests path deliberately does not prune).
            if self._config.news_learning_enabled:
                self._news_history_for(account_id).prune(
                    self._config.news_history_window_days,
                    self._config.news_history_max_entries,
                )


def main() -> None:
    YKBot(Config.from_env()).run()

