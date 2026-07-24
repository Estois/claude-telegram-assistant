"""Pure inline-keyboard builders for the button menu.

Each function maps plain data to a telegram.InlineKeyboardMarkup — no self,
no stores — so they're unit-testable in isolation. The async handlers that
use them live on YKBot in app.py (mirrors how app.py keeps
_build_arrivals_keyboard focused)."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

HOME_BUTTON = InlineKeyboardButton("🏠 Menu", callback_data="home")


def top_level_keyboard(is_admin: bool, is_closed: bool = False) -> InlineKeyboardMarkup:
    """is_closed adds the 🍳 Recipes row for a Closed-group session. It must
    stay indistinguishable from any other conditional row (compare to
    is_admin below) — nothing about its presence or absence should look
    different from an ordinary feature a given account does or doesn't
    have; the Closed group itself must never be a visible concept."""
    rows = [
        [InlineKeyboardButton("🚌 Bus", callback_data="act:bus"),
         InlineKeyboardButton("📰 News", callback_data="act:news")],
        [InlineKeyboardButton("🔬 Research", callback_data="act:research"),
         InlineKeyboardButton("🔗 Summarize", callback_data="act:summarize")],
        [InlineKeyboardButton("⭐ Interests", callback_data="menu:interests"),
         InlineKeyboardButton("🔒 End session", callback_data="act:endsession")],
    ]
    if is_closed:
        rows.append([InlineKeyboardButton("🍳 Recipes", callback_data="menu:recipes")])
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


def recipes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 List", callback_data="act:getrecipes"),
         InlineKeyboardButton("➕ Add", callback_data="act:addrecipe")],
        [InlineKeyboardButton("👁 View", callback_data="rcp_pick:view"),
         InlineKeyboardButton("✏️ Update", callback_data="rcp_pick:upd")],
        [InlineKeyboardButton("🗑 Remove", callback_data="rcp_pick:rm")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:root")],
    ])


def recipe_pick_keyboard(entries: list, action: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{i}. {entry['name'][:40]}", callback_data=f"rcp_{action}:{i}")]
        for i, entry in enumerate(entries, start=1)
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:recipes")])
    return InlineKeyboardMarkup(rows)


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


def back_keyboard(target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=target)]])


def home_keyboard() -> InlineKeyboardMarkup:
    """A lone 🏠 Menu button for results produced from the button menu, so
    they aren't dead ends. Its callback_data is deliberately NOT menu:root:
    that one navigates by editing the message in place, which would destroy
    the very result this button is attached to."""
    return InlineKeyboardMarkup([[HOME_BUTTON]])


def research_followup_keyboard(from_menu: bool) -> InlineKeyboardMarkup:
    """Depth/breadth buttons go on every /research finding regardless of how
    it was reached; the 🏠 row only when the research thread started from
    the button menu, matching every other result message in this bot."""
    rows = [[
        InlineKeyboardButton("🔍 More depth", callback_data="research:depth"),
        InlineKeyboardButton("🌐 More breadth", callback_data="research:breadth"),
    ]]
    if from_menu:
        rows.append([HOME_BUTTON])
    return InlineKeyboardMarkup(rows)
