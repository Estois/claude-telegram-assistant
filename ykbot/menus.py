"""Pure inline-keyboard builders for the button menu.

Each function maps plain data to a telegram.InlineKeyboardMarkup — no self,
no stores — so they're unit-testable in isolation. The async handlers that
use them live on YKBot in app.py (mirrors how app.py keeps
_build_arrivals_keyboard focused)."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def top_level_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🚌 Bus", callback_data="act:bus"),
         InlineKeyboardButton("📰 News", callback_data="act:news")],
        [InlineKeyboardButton("⭐ Interests", callback_data="menu:interests"),
         InlineKeyboardButton("🔗 Summarize", callback_data="act:summarize")],
        [InlineKeyboardButton("🔒 End session", callback_data="act:endsession")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Admin", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def interests_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 List", callback_data="act:getinterests"),
         InlineKeyboardButton("➕ Add", callback_data="act:addinterest")],
        [InlineKeyboardButton("✏️ Update", callback_data="int_pick:upd"),
         InlineKeyboardButton("🗑 Remove", callback_data="int_pick:rm")],
        [InlineKeyboardButton("🗂 Compile", callback_data="act:compile")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:root")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Get users", callback_data="act:getusers")],
        [InlineKeyboardButton("🗑 Remove user", callback_data="adm_pick_rm")],
        [InlineKeyboardButton("🆔 Allow Telegram id", callback_data="act:adduserteleid")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:root")],
    ])


def interest_pick_keyboard(entries: list, action: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{i}. {entry[:40]}", callback_data=f"int_{action}:{i}")]
        for i, entry in enumerate(entries, start=1)
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:interests")])
    return InlineKeyboardMarkup(rows)


def account_pick_keyboard(accounts: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(name, callback_data=f"adm_rm:{name}")]
        for name in accounts
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=f"cfm:{token}"),
        InlineKeyboardButton("❌ No", callback_data=f"cxl:{token}"),
    ]])
