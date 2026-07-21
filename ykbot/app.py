"""The YKBot orchestrator: wires the Telegram handlers to the services.

This is the one stateful class; every ``self._x`` it touches is set up in
``__init__`` right here, so the whole object reads top-to-bottom in one file.
"""

import asyncio
import logging
import re
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from ykbot._logging import logger
from ykbot.accounts import AccountStore, AllowlistStore
from ykbot.bus import BusLocationsStore, BusStopsCache, LtaBusClient
from ykbot.claude import ClaudeClient
from ykbot.config import Config
from ykbot.formatting import CommandSpec, TelegramFormatter
from ykbot.interests import InterestsStore
from ykbot.message_log import MessageLogStore
from ykbot.news_history import NewsHistoryStore
from ykbot import menus
from ykbot.handlers.bus import BusHandlers
from ykbot.services import BotServices
from ykbot.trivia import FALLBACK_TRIVIA, TriviaBook

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILENAME = "bot.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def configure_logging(log_dir: Path) -> None:
    """Sets up logging for the running bot. Called from main(), never at
    import time: configuring the root logger as an import side effect would
    hijack logging for anything that imports ykbot — including the test
    suite, which imports it 443 times over.

    Two things worth not undoing:

    - httpx logs every request URL at INFO, and the Telegram API URL carries
      the bot token in its path, so INFO-level httpx logging writes the live
      credential to the log on every poll. WARNING keeps genuine transport
      failures without the secret.
    - The file handler rotates. Logs were previously appended by the shell
      (``>> bot.log``), which cannot rotate and had grown past 18k lines.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    handler = RotatingFileHandler(
        log_dir / LOG_FILENAME, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    # force=True because basicConfig is a silent no-op once the root logger
    # has any handler, which would leave the bot logging wherever a stray
    # earlier call happened to point it. An entry point should own this.
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


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
        self._claude = ClaudeClient(config)
        self._trivia = TriviaBook(config.trivia_cache_file, config.trivia_state_file, FALLBACK_TRIVIA)
        self._lta = LtaBusClient(config.lta_api_key)
        self._bus_stops = BusStopsCache(config.bus_stops_cache_file)
        self._message_log = MessageLogStore(config.message_logs_dir)
        self._reorganize_task: Optional[asyncio.Task] = None

        self.services = BotServices(
            config=self._config,
            accounts=self._accounts,
            allowlist=self._allowlist,
            claude=self._claude,
            trivia=self._trivia,
            lta=self._lta,
            stops=self._bus_stops,
            message_log=self._message_log,
            interests_for=self._interests_for,
            bus_locations_for=self._bus_locations_for,
            news_history_for=self._news_history_for,
            require_session=self._require_session,
            require_admin=self._require_admin,
            is_admin=self._is_admin,
            reply_result=self._reply_result,
            send_formatted=self._send_formatted,
            run_claude=self._run_claude,
            safe_edit=self._safe_edit,
        )
        self._bus = BusHandlers(self.services)
        self._command_specs = self._build_command_specs()

    def _account_dir(self, account_id: str) -> Path:
        path = self._config.accounts_dir / account_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _interests_for(self, account_id: str) -> InterestsStore:
        return InterestsStore(self._account_dir(account_id) / self.INTERESTS_FILENAME)

    def _news_history_for(self, account_id: str) -> NewsHistoryStore:
        return NewsHistoryStore(self._account_dir(account_id) / self.NEWS_HISTORY_FILENAME)

    async def _recent_theme_counts(self, account_id: str) -> list:
        """Recent /news request themes with counts for this account, or an
        empty list when learning is disabled. Shared by the daily reorganize
        job and the manual /compileinterests command so they behave the same.

        Async because it reads the history file: the read goes to a worker
        thread so it doesn't stall the event loop."""
        if not self._config.news_learning_enabled:
            return []
        return await asyncio.to_thread(
            self._news_history_for(account_id).theme_counts,
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
                "menu", "/menu", "show the button menu", self.handle_menu,
                category="Menu",
            ),
            CommandSpec(
                "news", "/news <subject>",
                "news digest on a subject, biased toward your interests", self.handle_news,
                category="News",
            ),
            CommandSpec(
                "summarize", "/summarize <url>",
                "summarize a web page", self.handle_summarize,
                category="Tools",
            ),
            CommandSpec(
                "addinterests", "/addinterests <topic>",
                "add an interest (word, sentence, or paragraph)", self.handle_add_interests,
                category="Interests",
            ),
            CommandSpec(
                "getinterests", "/getinterests",
                "list your interests with their index", self.handle_get_interests,
                category="Interests",
            ),
            CommandSpec(
                "updateinterests", "/updateinterests <index> <topic>",
                "revise or rewrite an interest (Claude decides which)", self.handle_update_interests,
                category="Interests",
            ),
            CommandSpec(
                "removeinterest", "/removeinterest <index>",
                "delete an interest by its index", self.handle_remove_interest,
                category="Interests",
            ),
            CommandSpec(
                "compileinterests", "/compileinterests",
                "merge duplicate interests and add retrieval detail now (runs automatically too)",
                self.handle_compile_interests,
                category="Interests",
            ),
            CommandSpec(
                "bus", "/bus",
                "bus arrival times, from a preset stop or your live location", self._bus.handle_bus,
                category="Bus",
            ),
            CommandSpec(
                "endsession", "/endsession",
                "end the current session now", self.handle_end_session,
                category="Account",
            ),
            CommandSpec(
                "help", "/help", "show this message", self.handle_help,
                category="Account",
            ),
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
        """The full command reference, grouped by category. Categories appear
        in the order their first command is defined, so /help stays scannable
        as commands are added."""
        groups: dict = {}
        for spec in self._command_specs:
            if spec.admin_only:
                continue
            groups.setdefault(spec.category, []).append(f"{spec.usage} — {spec.description}")
        sections = []
        for category, entries in groups.items():
            sections.append(f"{category}:\n" + "\n".join(entries))
        if admin:
            admin_lines = [
                f"{spec.usage} — {spec.description}"
                for spec in self._command_specs
                if spec.admin_only
            ]
            if admin_lines:
                sections.append("Admin:\n" + "\n".join(admin_lines))
        return "\n\n".join(sections)

    def _build_help_text(self, admin: bool = False) -> str:
        minutes = self._config.session_timeout_seconds // 60
        return (
            "Every command needs an unlocked session — send the password as a plain "
            f"message first (lasts {minutes} minutes). Interests and preferred bus "
            "stops belong to whichever account your password unlocked.\n\n"
            + self._build_command_list(admin)
        )

    def _build_welcome_text(self, name: str) -> str:
        """Deliberately short and constant-size — the full command list lives
        in /help, so adding commands never clutters this greeting."""
        minutes = self._config.session_timeout_seconds // 60
        return (
            f"👋 Welcome, {name}! Session active for {minutes} minutes.\n\n"
            "Tap a button below to get started, or send /help for the full command list.\n\n"
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
        # Runs before every other handler (lower group) so it sees each
        # message. It logs only already-unlocked chats, so the unlock message
        # itself — still locked at this point — is never logged (see
        # _log_message); passwords therefore never reach disk.
        app.add_handler(TypeHandler(Update, self._log_message), group=-1)
        # /start isn't in _command_specs — it's just a generic greeting, not
        # a documented capability, so it stays out of /help and the welcome
        # message's command list.
        app.add_handler(CommandHandler("start", self.handle_start))
        for spec in self._command_specs:
            app.add_handler(CommandHandler(spec.name, spec.handler))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_callback, pattern=r"^bus_stop:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_refresh, pattern=r"^bus_refresh:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_back, pattern=r"^bus_back$"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_fav_add, pattern=r"^bus_fav_add:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_fav_remove, pattern=r"^bus_fav_remove:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_confirm_add, pattern=r"^bus_confirmadd:"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_cancel_add, pattern=r"^bus_cancel_add$"))
        app.add_handler(CallbackQueryHandler(self._bus.handle_bus_add_by_code, pattern=r"^bus_addbycode$"))
        app.add_handler(
            CallbackQueryHandler(self._bus.handle_bus_request_location, pattern=r"^bus_request_location$")
        )
        app.add_handler(CallbackQueryHandler(self.handle_menu_nav, pattern=r"^menu:"))
        app.add_handler(CallbackQueryHandler(self.handle_menu_home, pattern=r"^home$"))
        app.add_handler(CallbackQueryHandler(self.handle_menu_bus, pattern=r"^act:bus$"))
        app.add_handler(CallbackQueryHandler(
            self.handle_menu_action,
            pattern=r"^act:(getinterests|compile|endsession|news|summarize|addinterest)$"))
        app.add_handler(CallbackQueryHandler(self.handle_interest_pick, pattern=r"^int_pick:"))
        app.add_handler(CallbackQueryHandler(self.handle_interest_select, pattern=r"^int_(rm|upd):"))
        app.add_handler(CallbackQueryHandler(
            self.handle_admin_action, pattern=r"^act:(getusers|adduserteleid)$"))
        app.add_handler(CallbackQueryHandler(self.handle_admin_pick_remove, pattern=r"^adm_pick_rm$"))
        app.add_handler(CallbackQueryHandler(self.handle_admin_select_remove, pattern=r"^adm_rm:"))
        app.add_handler(CallbackQueryHandler(self.handle_confirm, pattern=r"^cfm:"))
        app.add_handler(CallbackQueryHandler(self.handle_cancel, pattern=r"^cxl:"))
        app.add_handler(MessageHandler(filters.LOCATION, self._bus.handle_location))
        app.add_handler(MessageHandler(filters.COMMAND, self.handle_unknown_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_plain_text))
        app.add_error_handler(self.handle_error)
        return app

    async def handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Last-resort net for exceptions no handler caught. Without this
        registered, python-telegram-bot logs 'No error handlers are
        registered' and the user's button or command simply does nothing —
        a silent failure is worse than an error message.

        The notice is deliberately generic: it says nothing about which
        command failed, so it can't be used to probe which commands exist.
        Delivery is best-effort — if replying also fails (the chat is gone,
        Telegram is down), log it and give up rather than recursing."""
        logger.exception("Unhandled exception while processing update", exc_info=context.error)
        message = getattr(update, "effective_message", None)
        if message is None:
            return
        try:
            await message.reply_text("Something went wrong on my end. Try again in a moment.")
        except Exception:
            logger.exception("Failed to deliver the error notice")

    def _warn_about_missing_config(self) -> None:
        if self._allowlist.ids is None:
            logger.warning(
                "TELEGRAM_ALLOWED_USER_IDS is not set — the allowlist fails closed, so the "
                "bot will ignore EVERY message until you add at least your own Telegram user "
                "id to it in .env."
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
            await update.effective_message.reply_text(self._trivia.next_fact())
            return None
        return account_id

    @staticmethod
    def _interests_block(interests: list) -> str:
        if not interests:
            return ""
        return (
            "For context, here are my personal interests (do not mention this list, "
            "its contents, or whether it is empty anywhere in your reply):\n"
            + "\n".join(f"- {entry}" for entry in interests)
            + "\n\n"
        )

    def _build_news_prompt(self, subject: str, interests: list) -> str:
        window_days = self._config.news_recency_days
        today = date.today()
        cutoff = today - timedelta(days=window_days)
        return (
            self._interests_block(interests)
            + f'Write a news digest about "{subject}", covering only developments from the '
            f"past {window_days} days, biased toward my documented interests where they're "
            "relevant to this subject (don't force a connection that isn't there). "
            f"Today's date is {today.isoformat()}. Only include items dated on or after "
            f"{cutoff.isoformat()}; exclude anything older than that, even if it is still "
            "widely discussed or you would otherwise consider it significant. If you cannot "
            f"find enough verifiable developments from the past {window_days} days, say so "
            "plainly rather than reaching further back or padding with older context. "
            "Take extra care with sourcing: only use information you can verify against "
            "reputable, established sources — major news organizations, official or primary "
            "sources, and well-known publications. Do NOT rely on blogs, forums, social-media "
            "posts, content farms, or obscure/unknown sites. Corroborate each item across "
            "more than one reputable source where you can, and leave out anything you cannot "
            "verify or that reads like rumor or speculation. "
            "Label every item with the date it happened or was reported, in (YYYY-MM-DD) "
            "form. End every item with its source(s) as clickable links in the form "
            '<a href="URL">Publication</a>; if an item draws on more than one source, list '
            "them all. "
            "Begin the reply immediately with the digest header line, which MUST start with "
            f'the literal words "News Digest" (for example: <b>News Digest — {subject} — '
            "YYYY-MM-DD</b>). Put NOTHING before that header: no preamble, no lead-in "
            "sentence, no meta-commentary about your process or sources of context. "
            "Format it for a Telegram message using only these HTML tags: <b>, <i>, <u>, "
            '<s>, <code>, <pre>, and <a href="URL">text</a>. Do not use Markdown syntax '
            "(no **, no #, no [text](url)). Use short paragraphs and simple bullet points "
            "(•) for scanability. Escape any literal <, >, or & characters that aren't part "
            "of a tag."
        )

    def _build_research_prompt(
        self, subject: str, interests: list, mode: str, previous_findings: Optional[str] = None,
    ) -> str:
        today = date.today()
        sourcing_rules = (
            "Take extra care with sourcing: only use information you can verify against "
            "reputable, established sources — major news organizations, official or primary "
            "sources, and well-known publications. Do NOT rely on blogs, forums, social-media "
            "posts, content farms, or obscure/unknown sites. Corroborate each item across "
            "more than one reputable source where you can, and leave out anything you cannot "
            "verify or that reads like rumor or speculation. Label every dated item in "
            "(YYYY-MM-DD) form and end every section with its source(s) as clickable links "
            'in the form <a href="URL">Publication</a>.'
        )
        formatting_rules = (
            "Begin the reply immediately with the findings header line, which MUST start "
            f'with the literal words "Research Findings" (for example: <b>Research Findings '
            f'— {subject}</b>). Put NOTHING before that header. Format it for a Telegram '
            "message using only these HTML tags: <b>, <i>, <u>, <s>, <code>, <pre>, and "
            '<a href="URL">text</a>. Do not use Markdown syntax. Use short paragraphs, '
            "clear section headers via <b>, and simple bullet points (•) for scanability. "
            "Escape any literal <, >, or & characters that aren't part of a tag."
        )

        if mode == "initial":
            return (
                self._interests_block(interests)
                + f'Research "{subject}" thoroughly and produce a detailed findings report — '
                "not just a quick recent-news digest, but a deeper investigation: relevant "
                "background and context, the current state of the topic, multiple angles or "
                "perspectives where relevant, and notable nuance or disagreement between "
                "sources. Biased toward my documented interests where they're relevant to "
                "this subject (don't force a connection that isn't there). "
                f"Today's date is {today.isoformat()}. You are not limited to recent "
                "developments the way a news digest is — pull in older foundational "
                "background where it helps understanding, though still highlight what's "
                "currently most significant. "
                + sourcing_rules + " " + formatting_rules
            )

        direction = (
            "The user wants MORE DEPTH than the previous pass: more granular detail, more "
            "corroborating sources per claim, and explicit caveats, edge cases, or "
            "disagreements between sources that the previous pass glossed over."
            if mode == "depth" else
            "The user wants MORE BREADTH than the previous pass: additional related angles, "
            "adjacent subtopics, perspectives or stakeholders not yet covered, and a wider "
            "variety of source types."
        )
        return (
            self._interests_block(interests)
            + f'You previously researched "{subject}" and produced this findings report:\n\n'
            f"{previous_findings}\n\n"
            f"{direction} Produce a full REPLACEMENT report — not a diff and not just new "
            "material tacked on — that supersedes the previous pass. "
            + sourcing_rules + " " + formatting_rules
        )

    @staticmethod
    def _build_summarize_prompt(url: str) -> str:
        return (
            f"Fetch the web page at {url} and write a concise summary of its main "
            "points — the key takeaways someone would want without reading the whole "
            "page. If the page can't be fetched or has no readable content, say so "
            "briefly instead of guessing. Output ONLY the summary, no preamble or "
            "meta-commentary. "
            "Format it for a Telegram message using only these HTML tags: <b>, <i>, <u>, "
            '<s>, <code>, <pre>, and <a href="URL">text</a>. Do not use Markdown syntax '
            "(no **, no #, no [text](url)). Use short paragraphs and simple bullet points "
            "(•) for scanability. Escape any literal <, >, or & characters that aren't part "
            "of a tag."
        )

    @staticmethod
    def _strip_preamble(reply: str, marker: str) -> str:
        """Drop any preamble the model emits before the first line whose
        visible text — after leading whitespace and any opening HTML tags —
        starts with `marker`. Returns the reply unchanged if no such line
        exists, rather than risk dropping real content."""
        lines = reply.splitlines(keepends=True)
        marker_len = len(marker)
        for i, line in enumerate(lines):
            visible = re.sub(r"^\s*(?:<[^>]+>\s*)*", "", line)
            if visible[:marker_len].lower() == marker.lower():
                return "".join(lines[i:])
        return reply

    @staticmethod
    def _strip_news_preamble(reply: str) -> str:
        """Thin wrapper kept for the existing call site and existing tests.
        See _strip_preamble for the actual logic."""
        return YKBot._strip_preamble(reply, "News Digest")

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

    def _menu_markup(self, update: Update, account_id: str) -> InlineKeyboardMarkup:
        return menus.top_level_keyboard(self._is_admin(update, account_id))

    async def handle_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        await update.effective_message.reply_text(
            "What would you like to do?", reply_markup=self._menu_markup(update, account_id)
        )

    async def handle_menu_nav(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        # Any navigation away from a "please type X" prompt cancels the
        # pending capture — otherwise a stale awaiting slot could silently
        # swallow the user's next, unrelated message.
        context.user_data.pop("awaiting", None)
        target = query.data.split(":", 1)[1]
        if target == "interests":
            await query.edit_message_text("Interests:", reply_markup=menus.interests_keyboard())
        elif target == "admin":
            if not self._is_admin(update, account_id):
                await query.edit_message_text(self._trivia.next_fact())
                return
            await query.edit_message_text("Admin:", reply_markup=menus.admin_keyboard())
        else:  # root
            await query.edit_message_text(
                "What would you like to do?", reply_markup=self._menu_markup(update, account_id)
            )

    async def handle_menu_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Posts a FRESH menu message rather than editing. The 🏠 button sits
        on result messages, so editing would replace the result the user just
        asked for — the opposite of what the button is for."""
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        context.user_data.pop("awaiting", None)
        await update.effective_message.reply_text(
            "What would you like to do?", reply_markup=self._menu_markup(update, account_id)
        )

    async def handle_menu_bus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        context.user_data.pop("awaiting", None)
        await self._bus.open_bus_menu(update, context, account_id)

    async def handle_menu_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        action = query.data.split(":", 1)[1]
        if action == "getinterests":
            await self._do_show_interests(update, account_id, from_menu=True)
        elif action == "compile":
            await self._do_compile_interests(update, context, account_id, from_menu=True)
        elif action == "endsession":
            await query.edit_message_text(
                "End your session now?", reply_markup=menus.confirm_keyboard("endsession")
            )
        elif action == "news":
            context.user_data["awaiting"] = {"kind": "news_subject", "payload": {}}
            await query.edit_message_text(
                "Send the subject for your news digest:",
                reply_markup=menus.back_keyboard("menu:root"),
            )
        elif action == "summarize":
            context.user_data["awaiting"] = {"kind": "summarize_url", "payload": {}}
            await query.edit_message_text(
                "Send the URL to summarize:", reply_markup=menus.back_keyboard("menu:root")
            )
        elif action == "addinterest":
            context.user_data["awaiting"] = {"kind": "add_interest", "payload": {}}
            await query.edit_message_text(
                "Send the interest to add (a word, sentence, or paragraph):",
                reply_markup=menus.back_keyboard("menu:interests"),
            )

    async def handle_interest_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        action = query.data.split(":", 1)[1]  # "rm" or "upd"
        entries = await asyncio.to_thread(self._interests_for(account_id).list)
        if not entries:
            await query.edit_message_text(
                "No interests saved yet.", reply_markup=menus.interests_keyboard()
            )
            return
        verb = "remove" if action == "rm" else "update"
        await query.edit_message_text(
            f"Pick an interest to {verb}:",
            reply_markup=menus.interest_pick_keyboard(entries, action),
        )

    async def handle_interest_select(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        action, index_str = query.data.split(":")[0].split("_")[1], query.data.split(":", 1)[1]
        index = int(index_str)
        if action == "rm":
            entry = await asyncio.to_thread(self._interests_for(account_id).list)
            label = entry[index - 1] if 1 <= index <= len(entry) else f"#{index}"
            await query.edit_message_text(
                f'Delete interest #{index}: "{label}"?',
                reply_markup=menus.confirm_keyboard(f"int_rm:{index}"),
            )
        else:  # upd
            context.user_data["awaiting"] = {"kind": "update_interest", "payload": {"index": index}}
            await query.edit_message_text(
                f"Send the new text for interest #{index} "
                "(I'll decide whether to revise or replace it):",
                reply_markup=menus.back_keyboard("menu:interests"),
            )

    async def handle_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._require_session(update)
        if account_id is None:
            await query.answer()
            return
        await query.answer()
        token = query.data.split(":", 1)[1]
        if token == "endsession":
            self._accounts.lock(update.effective_chat.id)
            await query.edit_message_text("Session ended.")
        elif token.startswith("int_rm:"):
            index = int(token.split(":", 1)[1])
            await self._do_remove_interest(update, account_id, index, from_menu=True)
        elif token.startswith("adm_rm:"):
            if await self._require_admin(update) is None:
                return
            await self._do_remove_user(update, token.split(":", 1)[1], from_menu=True)

    async def handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if await self._require_session(update) is None:
            await query.answer()
            return
        await query.answer()
        await query.edit_message_text("Cancelled.")

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

        await self._do_news(update, context, account_id, " ".join(context.args))

    async def _do_news(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        subject: str,
        from_menu: bool = False,
    ) -> None:
        if self._config.news_learning_enabled:
            try:
                await asyncio.to_thread(self._news_history_for(account_id).append, subject)
            except Exception:
                # Logging the subject must never break the digest reply.
                logger.exception("Failed to log /news subject for %s", account_id)
        interests = await asyncio.to_thread(self._interests_for(account_id).list)
        reply = await self._run_claude(
            update, context, self._build_news_prompt(subject, interests), web_tools=True
        )
        if reply is not None:
            await self._send_formatted(update, self._strip_news_preamble(reply), from_menu)

    async def handle_summarize(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /summarize <url>\nExample: /summarize https://example.com/article"
            )
            return

        url = context.args[0]
        if not self._is_fetchable_url(url):
            await update.message.reply_text(
                "Please give a single public http(s) URL to summarize."
            )
            return

        await self._do_summarize(update, context, url)

    async def _do_summarize(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        url: str,
        from_menu: bool = False,
    ) -> None:
        reply = await self._run_claude(
            update, context, self._build_summarize_prompt(url), web_tools=True
        )
        if reply is not None:
            await self._send_formatted(update, reply, from_menu)

    @staticmethod
    def _is_fetchable_url(url: str) -> bool:
        """A public http(s) URL. Rejects non-http(s) schemes and hosts that
        point at the local machine / private networks / cloud metadata, so an
        authenticated user can't turn /summarize into an SSRF probe of
        internal services on the host."""
        match = re.match(r"^https?://(\[[^\]]+\]|[^/:\s]+)", url, re.IGNORECASE)
        if not match:
            return False
        host = match.group(1).lower().rstrip(".")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]  # unwrap [IPv6]
        if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".internal"):
            return False
        # Block cloud metadata + IPv6 loopback/link-local/unique-local +
        # private/loopback/link-local IPv4 ranges. Real domains pass through.
        if host in ("169.254.169.254", "::1", "::"):
            return False
        blocked_prefixes = (
            "127.", "10.", "169.254.", "192.168.", "fe80", "fc", "fd",
            *(f"172.{n}." for n in range(16, 32)),
        )
        return not host.startswith(blocked_prefixes)

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
        await self._do_add_interest(update, account_id, " ".join(context.args))

    async def _do_add_interest(
        self, update: Update, account_id: str, text: str, from_menu: bool = False
    ) -> None:
        index = await asyncio.to_thread(self._interests_for(account_id).add, text)
        await self._reply_result(update, f"Added as interest #{index}.", from_menu)

    async def handle_get_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return
        await self._do_show_interests(update, account_id)

    async def _do_show_interests(
        self, update: Update, account_id: str, from_menu: bool = False
    ) -> None:
        entries = await asyncio.to_thread(self._interests_for(account_id).list)
        if not entries:
            await self._reply_result(
                update, "No interests saved yet. Add one with /addinterests <topic>.", from_menu
            )
            return
        await self._reply_result(update, self._format_interests_listing(entries), from_menu)

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
        entries = await asyncio.to_thread(interests.list)
        if not entries:
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return
        if not 1 <= index <= len(entries):
            await update.message.reply_text(
                f"No interest #{index}. Valid range: 1-{len(entries)}. See /getinterests."
            )
            return

        await self._do_update_interest(update, context, account_id, index, " ".join(topic_parts))

    async def _do_update_interest(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        index: int,
        text: str,
        from_menu: bool = False,
    ) -> None:
        interests = self._interests_for(account_id)
        entries = await asyncio.to_thread(interests.list)
        prompt = self._build_update_interest_prompt(entries[index - 1], text)
        new_text = await self._run_claude(update, context, prompt)
        if new_text is None:
            return

        await asyncio.to_thread(interests.update, index, new_text)
        await self._reply_result(update, f"Updated interest #{index}.", from_menu)

    async def handle_remove_interest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._require_session(update)
        if account_id is None:
            return

        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text(
                "Usage: /removeinterest <index>\nExample: /removeinterest 2\n"
                "See /getinterests for the indices."
            )
            return

        interests = self._interests_for(account_id)
        if not await asyncio.to_thread(interests.list):
            await update.message.reply_text("No interests saved yet. Add one with /addinterests <topic>.")
            return

        await self._do_remove_interest(update, account_id, int(context.args[0]))

    async def _do_remove_interest(
        self, update: Update, account_id: str, index: int, from_menu: bool = False
    ) -> None:
        interests = self._interests_for(account_id)
        removed = await asyncio.to_thread(interests.remove, index)
        if removed is None:
            remaining = await asyncio.to_thread(interests.list)
            await self._reply_result(
                update,
                f"No interest #{index}. Valid range: 1-{len(remaining)}. See /getinterests.",
                from_menu,
            )
            return
        await self._reply_result(update, f'Removed interest #{index}: "{removed}"', from_menu)

    async def handle_compile_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manual trigger for the same reorganization the background job
        runs periodically: merge duplicate/overlapping interests and add
        retrieval-helpful detail."""
        account_id = await self._require_session(update)
        if account_id is None:
            return
        await self._do_compile_interests(update, context, account_id)

    async def _do_compile_interests(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        account_id: str,
        from_menu: bool = False,
    ) -> None:
        interests = self._interests_for(account_id)
        entries = await asyncio.to_thread(interests.list)
        theme_counts = await self._recent_theme_counts(account_id)
        if len(entries) < 2 and not theme_counts:
            await self._reply_result(
                update, "Need at least 2 interests to reorganize. See /getinterests.", from_menu
            )
            return

        prompt = self._build_reorganize_prompt(entries, theme_counts)
        raw_reply = await self._run_claude(
            update, context, prompt, chat_id=self._maintenance_key(account_id)
        )
        if raw_reply is None:
            return

        new_entries = InterestsStore.parse_entries(raw_reply)
        if not new_entries:
            await self._reply_result(
                update, "Reorganize returned nothing usable — no changes made.", from_menu
            )
            return

        await asyncio.to_thread(interests.replace_all, new_entries)
        summary = f"🗂 Reorganized interests: {len(entries)} → {len(new_entries)} entries."
        await self._reply_result(
            update, f"{summary}\n\n{self._format_interests_listing(new_entries)}", from_menu
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
        if not AccountStore.ACCOUNT_ID_PATTERN.match(name) or len(name) > 32:
            await update.message.reply_text(
                "Name must be letters, digits, - or _ only, max 32 characters "
                "(it's used as a folder name)."
            )
            return
        if not re.fullmatch(r"[0-9a-fA-F]{64}", password_hash):
            await update.message.reply_text(
                "That doesn't look like a SHA256 hex digest (64 hex characters). "
                "Generate one with: printf '%s' 'password' | sha256sum"
            )
            return

        created = await asyncio.to_thread(self._accounts.add_hash, name, password_hash)
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

        await self._do_remove_user(update, context.args[0])

    async def _do_remove_user(self, update: Update, name: str, from_menu: bool = False) -> None:
        if name == self._config.admin_account_id:
            await self._reply_result(update, "Refusing to remove the admin account.", from_menu)
            return
        removed = await asyncio.to_thread(self._accounts.remove_account, name)
        await self._reply_result(
            update,
            f"Removed account '{name}'." if removed else f"No account named '{name}'.",
            from_menu,
        )

    async def handle_add_user_tele_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_admin(update) is None:
            return

        if not context.args or len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("Usage: /adduserteleid <telegram_user_id>")
            return

        await self._do_add_user_tele_id(update, context.args[0])

    async def _do_add_user_tele_id(
        self, update: Update, raw: str, from_menu: bool = False
    ) -> None:
        raw = raw.strip()
        if not raw.isdigit():
            await self._reply_result(update, "That isn't a numeric Telegram user id.", from_menu)
            return
        user_id = int(raw)
        added = self._allowlist.add(user_id)
        await self._reply_result(
            update,
            f"Added {user_id} to the allowlist." if added else f"{user_id} is already allowed.",
            from_menu,
        )

    async def handle_get_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_admin(update) is None:
            return

        account_ids = await asyncio.to_thread(self._accounts.list_ids)
        await update.message.reply_text(self._format_user_listing(account_ids))

    def _format_user_listing(self, account_ids: list) -> str:
        """Pure: takes the account ids rather than reading them. Session
        state is in-memory, so has_active_session stays a direct call."""
        if not account_ids:
            return "No accounts configured."
        return "\n".join(
            f"{account_id} — {'🟢 active' if self._accounts.has_active_session(account_id) else '🔒 locked'}"
            for account_id in account_ids
        )

    @staticmethod
    async def _safe_edit(query, text: str, reply_markup=None) -> None:
        """Edits a callback message, tolerating Telegram's "message is not
        modified" error. A menu screen can carry the very button that renders
        it (Get users sits inside the admin keyboard), so re-tapping produces
        a byte-identical edit — which Telegram rejects. Nothing is wrong when
        that happens; the screen is simply already correct."""
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise

    async def handle_admin_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if await self._require_admin(update) is None:
            await query.answer()
            return
        await query.answer()
        action = query.data.split(":", 1)[1]
        if action == "getusers":
            account_ids = await asyncio.to_thread(self._accounts.list_ids)
            await self._safe_edit(
                query, self._format_user_listing(account_ids), reply_markup=menus.admin_keyboard()
            )
        elif action == "adduserteleid":
            context.user_data["awaiting"] = {"kind": "add_user_tele_id", "payload": {}}
            await query.edit_message_text(
                "Send the Telegram user id to allow:",
                reply_markup=menus.back_keyboard("menu:admin"),
            )

    async def handle_admin_pick_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if await self._require_admin(update) is None:
            await query.answer()
            return
        await query.answer()
        account_ids = await asyncio.to_thread(self._accounts.list_ids)
        accounts = [a for a in account_ids if a != self._config.admin_account_id]
        if not accounts:
            await self._safe_edit(query, "No removable accounts.", reply_markup=menus.admin_keyboard())
            return
        await query.edit_message_text(
            "Pick an account to remove:", reply_markup=menus.account_pick_keyboard(accounts)
        )

    async def handle_admin_select_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if await self._require_admin(update) is None:
            await query.answer()
            return
        await query.answer()
        name = query.data.split(":", 1)[1]
        await query.edit_message_text(
            f"Remove account '{name}'?", reply_markup=menus.confirm_keyboard(f"adm_rm:{name}")
        )

    async def handle_unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._require_session(update) is None:
            return
        # Unrecognized commands get trivia rather than an error, same as a
        # wrong password — see handle_plain_text.
        await update.message.reply_text(self._trivia.next_fact())

    async def _consume_awaiting(self, update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
        """If a text-capture is armed AND the chat is unlocked, handle the
        text and return True. If locked, clear the slot and return False so
        the caller treats the message as a password attempt (indistinguishable
        from a wrong guess — no capture leaks past the lock)."""
        awaiting = context.user_data.pop("awaiting", None)
        if not awaiting:
            return False
        account_id = self._accounts.account_for(chat_id)
        if account_id is None:
            return False  # locked: fall through to the password path
        text = update.message.text or ""
        kind = awaiting["kind"]
        if kind == "bus_code":
            await self._bus.prompt_add_bus_stop(update, context, account_id, text)
        elif kind == "news_subject":
            await self._do_news(update, context, account_id, text.strip(), from_menu=True)
        elif kind == "summarize_url":
            url = text.strip()
            if not self._is_fetchable_url(url):
                await self._reply_result(
                    update, "Please give a single public http(s) URL to summarize.", from_menu=True
                )
            else:
                await self._do_summarize(update, context, url, from_menu=True)
        elif kind == "add_interest":
            await self._do_add_interest(update, account_id, text.strip(), from_menu=True)
        elif kind == "add_user_tele_id":
            if self._is_admin(update, account_id):
                await self._do_add_user_tele_id(update, text, from_menu=True)
        elif kind == "update_interest":
            index = awaiting["payload"]["index"]
            entries = await asyncio.to_thread(self._interests_for(account_id).list)
            if 1 <= index <= len(entries):
                await self._do_update_interest(
                    update, context, account_id, index, text.strip(), from_menu=True
                )
            else:
                await self._reply_result(
                    update, f"Interest #{index} no longer exists. See /getinterests.", from_menu=True
                )
        return True

    async def handle_plain_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Non-command text is treated as a password attempt, never as a prompt
        to relay to Claude — except a one-shot bus-code entry armed by the
        /bus "add a stop by code" button, when the chat is already unlocked."""
        if not self._is_authorized(update):
            return

        chat_id = update.effective_chat.id

        # Text-capture armed by a menu button (bus code, news subject, etc.).
        # Only consumed while unlocked; otherwise it's a password attempt.
        if await self._consume_awaiting(update, context, chat_id):
            return

        # Brute-force throttle: after too many recent failures, ignore attempts
        # (even correct ones) for a cooldown. Reply with trivia either way so a
        # throttled chat is indistinguishable from a wrong guess.
        if self._accounts.unlock_retry_after(chat_id) > 0:
            await update.message.reply_text(self._trivia.next_fact())
            return

        attempt = (update.message.text or "").strip()
        account_id = self._accounts.try_unlock(chat_id, attempt)
        if account_id is not None:
            self._accounts.clear_failed_unlocks(chat_id)
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            except Exception:
                logger.exception("Failed to delete password message")
            name = update.effective_user.full_name or "there"
            await context.bot.send_message(
                chat_id=chat_id,
                text=self._build_welcome_text(name),
                reply_markup=menus.top_level_keyboard(self._is_admin(update, account_id)),
            )
            return

        # Wrong password (or none configured yet) — record the failure and give
        # no hint that this was an auth attempt, just a generic reply.
        self._accounts.register_failed_unlock(chat_id)
        await update.message.reply_text(self._trivia.next_fact())

    async def _log_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Appends each incoming message to a per-user file. Registered in an
        earlier handler group so it runs before the command handlers.

        Only messages from an already-unlocked chat are logged: the unlock
        message (a password) is still locked when this runs, so passwords and
        wrong guesses never reach disk. Callback-query updates carry no
        message and are skipped."""
        if not self._config.message_logging_enabled:
            return
        message = update.message
        if message is None:
            return
        if self._accounts.account_for(update.effective_chat.id) is None:
            return
        user = update.effective_user
        text = message.text if message.text is not None else "[non-text message]"
        await asyncio.to_thread(self._message_log.log, user.username, user.id, text)

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
            await update.effective_message.reply_text(self._build_usage_limit_message(reset_notice))
        else:
            # Don't leak CLI stderr / internal paths to the user — the full
            # exception is already logged server-side in _run_claude.
            await update.effective_message.reply_text(
                "⚠️ Something went wrong reaching Claude. Please try again in a bit."
            )

    async def _run_claude(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        prompt: str,
        *,
        chat_id: Optional[Union[int, str]] = None,
        web_tools: bool = False,
    ) -> Optional[str]:
        """Runs prompt through Claude and returns the raw reply. Each call is
        an independent one-shot invocation. Serializes per lock key (the
        Telegram chat by default; pass chat_id to target a different one, e.g.
        an account's maintenance key). Returns None if a busy/usage-limit/error
        message was already sent to the user and the caller should stop.

        Usage-limit detection lives only on the failure path (the CLI exits
        non-zero when it hits a limit — its authoritative signal). A
        successful reply is never second-guessed, so a legitimate digest
        that merely discusses usage limits as a topic is passed through
        untouched instead of being mistaken for a limit notice."""
        telegram_chat_id = update.effective_chat.id
        lock_key = chat_id if chat_id is not None else telegram_chat_id

        lock = self._claude.lock_for(lock_key)
        if lock.locked():
            await update.effective_message.reply_text("Still working on your last request — hang on.")
            return None

        async with lock:
            keep_typing = asyncio.create_task(self._keep_typing(context, telegram_chat_id))
            try:
                return await self._claude.run(prompt, web_tools=web_tools)
            except Exception as exc:
                logger.exception("claude invocation failed")
                await self._reply_to_claude_failure(update, exc)
                return None
            finally:
                keep_typing.cancel()

    async def _reply_result(
        self, update: Update, text: str, from_menu: bool, **kwargs
    ) -> None:
        """Sends a result reply, attaching the 🏠 Menu button when the action
        was started from the button menu. Slash-command results stay bare — if
        you're already typing commands you don't need a button to reach the
        menu. Always replies via effective_message: results from a button flow
        can arrive on an update whose .message is None."""
        await update.effective_message.reply_text(
            text,
            reply_markup=menus.home_keyboard() if from_menu else None,
            **kwargs,
        )

    async def _send_formatted(
        self, update: Update, text: str, from_menu: bool = False,
        last_chunk_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Sends Claude's reply, chunked for Telegram's message limit. Tries
        HTML formatting first and falls back to plain text per chunk if the
        markup doesn't parse — LLM-generated tags aren't guaranteed
        well-formed, so a broken reply must never mean a lost reply.

        The button goes on the LAST chunk only, so a multi-chunk reply ends
        with one button row rather than one per message. last_chunk_markup,
        when given, replaces the default "home button only if from_menu"
        logic entirely — used by /research to attach its depth/breadth row
        (plus 🏠 when appropriate) instead of the plain home button."""
        chunks = TelegramFormatter.chunk(text)
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            if not last:
                markup = None
            elif last_chunk_markup is not None:
                markup = last_chunk_markup
            else:
                markup = menus.home_keyboard() if from_menu else None
            try:
                await update.effective_message.reply_text(
                    chunk, parse_mode=ParseMode.HTML, reply_markup=markup
                )
            except BadRequest:
                logger.warning("Telegram rejected HTML formatting, sending chunk as plain text")
                await update.effective_message.reply_text(chunk, reply_markup=markup)

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
        account_ids = await asyncio.to_thread(self._accounts.list_ids)
        for account_id in account_ids:
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
            entries = await asyncio.to_thread(interests.list)
            theme_counts = await self._recent_theme_counts(account_id)

            # Nothing to do without either enough interests to reorganize or any
            # request history to learn from.
            if len(entries) < 2 and not theme_counts:
                return

            prompt = self._build_reorganize_prompt(entries, theme_counts)
            raw_reply = await self._claude.run(prompt)
            new_entries = InterestsStore.parse_entries(raw_reply)
            if not new_entries:
                logger.warning(
                    "Interests reorganize for %s returned nothing parseable, leaving its interests.md untouched",
                    account_id,
                )
            else:
                await asyncio.to_thread(interests.replace_all, new_entries)
                logger.info(
                    "Reorganized interests for %s: %d -> %d entries",
                    account_id, len(entries), len(new_entries),
                )

            # Prune the log to the window after the daily pass (the manual
            # /compileinterests path deliberately does not prune).
            if self._config.news_learning_enabled:
                await asyncio.to_thread(
                    self._news_history_for(account_id).prune,
                    self._config.news_history_window_days,
                    self._config.news_history_max_entries,
                )


def main() -> None:
    config = Config.from_env()
    configure_logging(config.base_dir)
    YKBot(config).run()

