"""Bus arrivals: favourite stops, nearby lookup by location, and live timings.

The first domain extracted from YKBot. Everything shared arrives through
BotServices, so this module never imports or references YKBot.
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ykbot import menus
from ykbot._logging import logger
from ykbot.services import BotServices

_SGT = ZoneInfo("Asia/Singapore")


class BusHandlers:
    """Every /bus and bus-button interaction."""

    def __init__(self, services: BotServices):
        self._svc = services

    async def handle_bus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        account_id = await self._svc.require_session(update)
        if account_id is None:
            return
        await self.open_bus_menu(update, context, account_id)

    async def open_bus_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str
    ) -> None:
        context.user_data["bus_menu"] = {"kind": "fav"}
        locations = await asyncio.to_thread(self._svc.bus_locations_for(account_id).list)
        text, markup = self._build_fav_menu(locations)
        await update.effective_message.reply_text(text, reply_markup=markup)

    def _build_fav_menu(self, locations: dict) -> "tuple[str, InlineKeyboardMarkup]":
        """Pure: takes the saved stops rather than reading them. A sync
        builder cannot await, so the disk read has to happen in the async
        caller — same shape as _build_near_menu and everything in menus.py."""
        buttons = [
            [InlineKeyboardButton(name, callback_data=f"bus_stop:{code}")]
            for name, code in locations.items()
        ]
        buttons.append(
            [InlineKeyboardButton("📍 Send my location", callback_data="bus_request_location")]
        )
        buttons.append(
            [InlineKeyboardButton("➕ Add a stop by code", callback_data="bus_addbycode")]
        )
        buttons.append([menus.HOME_BUTTON])
        return "Pick a stop:", InlineKeyboardMarkup(buttons)

    def _build_near_menu(self, stops: list) -> "tuple[str, InlineKeyboardMarkup]":
        buttons = [
            [
                InlineKeyboardButton(
                    f"{stop['Description']} ({int(stop['distance_m'])}m)",
                    callback_data=f"bus_stop:{stop['BusStopCode']}",
                )
            ]
            for stop in stops
        ]
        buttons.append([menus.HOME_BUTTON])
        return "Nearest stops:", InlineKeyboardMarkup(buttons)

    async def handle_bus_request_location(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if await self._svc.require_session(update) is None:
            await query.answer()
            return

        await query.answer()
        await query.edit_message_text(
            "Reply to this message with your location — tap 📎 (attach) and choose "
            "Location — and I'll show the nearest stops."
        )

    async def handle_bus_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._svc.require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        text = await self._render_arrivals(stop_code)
        markup = await self._arrivals_keyboard_for(stop_code, account_id)
        await query.edit_message_text(text, reply_markup=markup)

    async def _render_arrivals(self, stop_code: str, prefix: str = "") -> str:
        """Arrival times for a stop, plus a refresh timestamp footer. The
        footer both confirms a refresh happened and keeps the message text
        changing, so Telegram never rejects the edit as 'not modified'."""
        text = await self._format_arrivals(stop_code)
        stamp = datetime.now(_SGT).strftime("%H:%M:%S")
        return f"{prefix}{text}\n\n🔄 Updated {stamp}"

    async def handle_bus_refresh(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._svc.require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer("Refreshed")
        stop_code = query.data.split(":", 1)[1]
        text = await self._render_arrivals(stop_code)
        markup = await self._arrivals_keyboard_for(stop_code, account_id)
        await query.edit_message_text(text, reply_markup=markup)

    async def handle_bus_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._svc.require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        menu = context.user_data.get("bus_menu")
        if menu and menu.get("kind") == "near" and menu.get("stops"):
            text, markup = self._build_near_menu(menu["stops"])
        elif menu and menu.get("kind") == "fav":
            locations = await asyncio.to_thread(self._svc.bus_locations_for(account_id).list)
            text, markup = self._build_fav_menu(locations)
        else:
            # Navigation state was lost (e.g. the bot restarted since the menu
            # was shown) — fall back to the always-available preferred menu.
            locations = await asyncio.to_thread(self._svc.bus_locations_for(account_id).list)
            text, markup = self._build_fav_menu(locations)
            text = f"Menu expired — {text[0].lower()}{text[1:]}"
        await query.edit_message_text(text, reply_markup=markup)

    async def handle_bus_fav_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._svc.require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        name = self._describe_stop(stop_code)
        await asyncio.to_thread(self._svc.bus_locations_for(account_id).add, name, stop_code)

        text = await self._render_arrivals(stop_code, prefix=f'⭐ Added "{name}" to preferred stops.\n\n')
        markup = await self._arrivals_keyboard_for(stop_code, account_id)
        await query.edit_message_text(
            text,
            reply_markup=markup,
        )

    async def handle_bus_fav_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._svc.require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        await asyncio.to_thread(self._svc.bus_locations_for(account_id).remove_by_code, stop_code)

        text = await self._render_arrivals(stop_code, prefix="🗑 Removed from preferred stops.\n\n")
        markup = await self._arrivals_keyboard_for(stop_code, account_id)
        await query.edit_message_text(
            text,
            reply_markup=markup,
        )

    async def handle_bus_add_by_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """The /bus "➕ Add a stop by code" button. Arms a one-shot state so the
        user's next plain message is read as a bus-stop code rather than a
        password attempt (see handle_plain_text)."""
        query = update.callback_query
        if await self._svc.require_session(update) is None:
            await query.answer()
            return
        await query.answer()
        context.user_data["awaiting"] = {"kind": "bus_code", "payload": {}}
        await query.edit_message_text(
            "Send me the bus stop's code — the 5-digit number on the sign — and "
            "I'll show you the stop before adding it to your preferred list.",
            reply_markup=menus.back_keyboard("act:bus"),
        )

    async def prompt_add_bus_stop(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str, raw_code: str
    ) -> None:
        """Shows the stop's details + live arrivals for confirmation before
        adding, so the user can check they entered the right code."""
        code = raw_code.strip()
        if not code.isdigit():
            await update.message.reply_text(
                "That doesn't look like a bus stop code — it should be just digits "
                "(e.g. 46009). Open /bus and tap “➕ Add a stop by code” to try again."
            )
            return
        if await asyncio.to_thread(self._svc.bus_locations_for(account_id).has_code, code):
            await update.message.reply_text(
                f'"{self._describe_stop(code)}" is already in your preferred stops.'
            )
            return

        # Remember the fav menu as the origin so the arrivals view's Back button
        # (shown after confirming) has somewhere sensible to return to.
        context.user_data["bus_menu"] = {"kind": "fav"}
        text = (
            "Add this stop to your preferred list?\n\n"
            f"📍 {self._describe_stop(code)}\n\n"
            + await self._format_arrivals(code)
        )
        await update.message.reply_text(text, reply_markup=self._build_add_confirm_keyboard(code))

    def _build_add_confirm_keyboard(self, stop_code: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Add to preferred", callback_data=f"bus_confirmadd:{stop_code}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="bus_cancel_add")],
        ])

    async def handle_bus_confirm_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        account_id = await self._svc.require_session(update)
        if account_id is None:
            await query.answer()
            return

        await query.answer()
        stop_code = query.data.split(":", 1)[1]
        name = self._describe_stop(stop_code)
        await asyncio.to_thread(self._svc.bus_locations_for(account_id).add, name, stop_code)

        text = await self._render_arrivals(stop_code, prefix=f'⭐ Added "{name}" to preferred stops.\n\n')
        markup = await self._arrivals_keyboard_for(stop_code, account_id)
        await query.edit_message_text(text, reply_markup=markup)

    async def handle_bus_cancel_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if await self._svc.require_session(update) is None:
            await query.answer()
            return
        await query.answer()
        await query.edit_message_text("Cancelled — nothing was added to your preferred stops.")

    def _describe_stop(self, stop_code: str) -> str:
        stop = self._svc.stops.find_by_code(stop_code)
        if stop:
            return f"{stop['Description']} ({stop_code})"
        return f"Stop {stop_code}"

    def _build_arrivals_keyboard(self, stop_code: str, is_favorite: bool) -> InlineKeyboardMarkup:
        """Pure: takes whether the stop is already saved rather than looking
        it up, so the caller can do that read off the event loop."""
        if is_favorite:
            fav_button = InlineKeyboardButton(
                "🗑 Remove from preferred", callback_data=f"bus_fav_remove:{stop_code}"
            )
        else:
            fav_button = InlineKeyboardButton(
                "⭐ Add as preferred", callback_data=f"bus_fav_add:{stop_code}"
            )
        nav_row = [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"bus_refresh:{stop_code}"),
            InlineKeyboardButton("⬅️ Back", callback_data="bus_back"),
        ]
        return InlineKeyboardMarkup([nav_row, [fav_button]])

    async def _arrivals_keyboard_for(self, stop_code: str, account_id: str) -> InlineKeyboardMarkup:
        """Async fetcher: does the store read off the event loop, then hands
        plain data to the pure builder."""
        is_favorite = await asyncio.to_thread(
            self._svc.bus_locations_for(account_id).has_code, stop_code
        )
        return self._build_arrivals_keyboard(stop_code, is_favorite)

    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._svc.require_session(update) is None:
            return

        if not self._svc.stops.is_available:
            await update.message.reply_text(
                "Nearby-stop lookup isn't set up yet — run fetch_bus_stops.py first, "
                "or use a preset location via /bus."
            )
            return

        loc = update.message.location
        nearest = self._svc.stops.nearest(loc.latitude, loc.longitude, self._svc.config.bus_nearby_count)
        if not nearest:
            await update.message.reply_text("No bus stops found nearby.")
            return

        context.user_data["bus_menu"] = {"kind": "near", "stops": nearest}
        text, markup = self._build_near_menu(nearest)
        await update.message.reply_text(text, reply_markup=markup)

    async def _format_arrivals(self, stop_code: str) -> str:
        if not self._svc.lta.is_configured:
            return "LTA_API_KEY is not configured — can't fetch arrival times."

        try:
            services = await self._svc.lta.get_arrivals(stop_code)
        except Exception:
            logger.exception("LTA bus arrival lookup failed for stop %s", stop_code)
            return f"Couldn't fetch arrival times for stop {stop_code}. Try again shortly."

        if not services:
            return f"🚌 Stop {stop_code}\nNo bus data available right now."

        lines = [f"🚌 Stop {stop_code}"]
        for svc in services:
            lines.append(f"Svc {svc['service_no']}: " + ", ".join(svc["arrivals"]))
        return "\n".join(lines)
