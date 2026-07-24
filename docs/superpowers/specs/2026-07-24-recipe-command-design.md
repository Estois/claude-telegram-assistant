# /recipe — a personal recipe box (storage, not generation)

## Problem

There is no way to save your own recipes in the bot today. This is
deliberately **not** a recipe *finder* like `/news`/`/research` are for
articles — the user pastes a recipe they already have (copied off a webpage,
typed out, OCR'd from a photo, a rambling half-formatted description) and
wants the bot to keep it, clean it up, and let them pull it back up later.
No web search, no Claude "inventing" a recipe from a subject name.

## Scope

Four new commands plus a menu submenu, all gated behind the existing session
lock like every other command:

- `/addrecipe` — paste a recipe, get back a saved, Claude-cleaned copy.
- `/getrecipes` — list saved recipes by index + title.
- `/updaterecipe <n>` — replace a saved recipe by re-pasting.
- `/removerecipe <n>` — delete by index, confirm first.

**Must be reachable via button, not just slash command** — confirmed with
the user explicitly for `/addrecipe` in particular (this was the trigger for
writing this spec instead of just implementing the slash command). The
🍳 Recipes submenu button covers all four.

Out of scope: recipe generation/suggestion, web search, ingredient-based
suggestions ("what can I make with X"), dietary/allergy filtering, tags or
categories, structured fields (separate ingredients/steps arrays) — the
stored body is one formatted text blob. Any of these are natural fast-follows
once the base feature is in daily use, not blockers for v1.

## Design

### Storage — `RecipeStore` (`ykbot/recipes.py`)

New file, sibling to `ykbot/interests.py`, same shape as `InterestsStore`
(list / add / update / remove by 1-based index, entries renumber after a
removal) but storing **structured entries instead of plain strings** —
`interests.md`'s one-line-per-entry format can't hold a multi-line
ingredients+steps body. Backed by `accounts/<id>/recipes.json`:

```json
[
  {"name": "Grandma's Banana Bread", "body": "<b>Ingredients</b>\n• ..."},
  {"name": "Weeknight Fried Rice", "body": "..."}
]
```

```python
class RecipeStore:
    def __init__(self, path: Path): ...
    def list(self) -> list[dict]: ...             # [{"name","body"}, ...]
    def add(self, name: str, body: str) -> int: ...        # -> 1-based index
    def update(self, index: int, name: str, body: str) -> bool: ...
    def remove(self, index: int) -> Optional[dict]: ...    # removed entry or None
```

Only the Claude-cleaned version is ever stored — no raw-paste fallback field
(confirmed with the user: keeps the storage shape and `/viewrecipe` display
to one field, and a bad interpretation is fixed by re-pasting through
`/updaterecipe`, which re-runs the same interpret pass).

`YKBot._recipes_for(account_id)` follows the exact existing
`_interests_for(account_id)` pattern (per-account path resolution, no
caching — data files are hot per the standing runbook).

### Interpretation prompt

`_build_recipe_interpret_prompt(raw_text)` — one-shot, **`web_tools=False`**
(this is not a lookup, it's reformatting input the user already gave us; no
sandboxing question to weigh, cheapest Claude call shape in the bot).

Instructions:
- Extract a short, natural title for the recipe. If the input doesn't
  suggest one, generate a plain descriptive one from its contents (e.g.
  "Chicken Fried Rice") — never leave it blank.
- Reformat into a clean **ingredients list** and **numbered steps**, plus
  any notes actually present in the input (servings, prep/cook time,
  substitution notes) — omit sections with nothing to put in them.
- **Hard constraint, stated explicitly and strongly:** do not invent,
  guess, or "improve" any ingredient, quantity, or step not present in the
  input. This is the user's own recipe — fidelity to what they pasted
  matters more here than it does in `/news`/`/research`'s sourcing
  guardrails, because there is no source to corroborate against; the input
  *is* the source. If a quantity or step is genuinely ambiguous in the
  input, keep it as close to the original wording as possible rather than
  resolving the ambiguity with a guess.
- If the input contains no recognizable recipe at all (empty, gibberish, an
  unrelated message), reply with the literal marker `NOT_A_RECIPE` and
  nothing else, so the bot can detect it and ask the user to try again
  instead of saving garbage.
- Otherwise, respond starting with `Title: <title>` on the first line, a
  blank line, then the formatted body — same Telegram-HTML-only formatting
  rules already used everywhere else (`<b>`, `<i>`, `<u>`, `<s>`, `<code>`,
  `<pre>`; no Markdown; escape stray `<`/`>`/`&`).

### Response parsing

`_parse_recipe_reply(reply: str) -> Optional[tuple[str, str]]`:
- Returns `None` if the reply is (after stripping) exactly `NOT_A_RECIPE`,
  or doesn't start with `Title: `.
- Otherwise splits the first line's remainder off as the title, and
  everything after the following blank line as the body; returns
  `(title, body)`.

Deliberately **not** built on the existing `_strip_preamble(reply, marker)`
helper — that helper drops everything *before* a marker line and keeps
everything after, which fits `"News Digest"`/`"Research Findings"` headers
that are followed immediately by content. Here the title itself is data we
need to extract, not a marker to discard, so it needs its own small parser.

### Add flow

- **Slash:** `/addrecipe` (no args — the point is to accept a raw paste,
  not require a name upfront; confirmed with the user, this maximizes how
  freeform the input can be) → `handle_add_recipe` → arms
  `context.user_data["awaiting"] = {"kind": "recipe_raw", "payload": {}}`,
  replies "Paste the recipe you want to save (ingredients and steps, any
  format):".
- **Button:** 🍳 Recipes → ➕ Add (`act:addrecipe`) arms the identical
  `awaiting` slot — same mechanism `act:addinterest`/`act:news` already use,
  no new plumbing.
- **Capture:** new `_consume_awaiting` branch, `elif kind == "recipe_raw"`,
  calls `_do_add_recipe(update, context, account_id, text, from_menu)`.
- **`_do_add_recipe`:** runs `_run_claude(..., web_tools=False,
  processing_notice=None)` with the interpret prompt. On `None` (failed
  call), `_run_claude` has already sent the error/usage notice — nothing
  further to do. On success, parse via `_parse_recipe_reply`:
  - `None` → reply "I couldn't find a recipe in that — try pasting the
    ingredients and steps again." (no save, `awaiting` already cleared by
    the capture dispatch, same as every other capture branch).
  - `(title, body)` → `RecipeStore.add(title, body)` → reply confirming,
    e.g. `Saved recipe #3: <b>Grandma's Banana Bread</b>`, with
    `menus.home_keyboard()` if `from_menu` else no markup (same rule as
    every other capture-delivered result).

No `processing_notice` — the existing convention (see the 2026-07-21
handoff) is that it's opt-in per feature and was deliberately scoped to
`/news`/`/research` only; a local reformatting call is fast enough (no web
tools) that it wasn't asked for here, matching `/summarize`'s precedent of
also skipping it despite being `web_tools=True`. Revisit only if it proves
too slow in practice.

### List / view / update / remove flow

Mirrors `handle_interest_pick` / `handle_interest_select` /
`handle_confirm` exactly, with a third pick-action added (`view`, not just
`rm`/`upd`) because the user asked for tap-to-view specifically:

- `/getrecipes` and 📋 List (`act:getrecipes`) both call a shared
  `_do_get_recipes`: no recipes → "No recipes saved yet."; else a plain
  numbered title list (**not** bodies — keeps it scannable, matches
  `/getinterests`).
- `menus.recipes_keyboard()`:
  ```
  📋 List        ➕ Add
  👁 View         ✏️ Update
  🗑 Remove
  ⬅️ Back
  ```
- `menus.recipe_pick_keyboard(entries, action)` — same shape as
  `interest_pick_keyboard`, callback data `f"rcp_{action}:{i}"`, labels
  truncated the same way (`entry["name"][:40]`).
- `handle_recipe_pick` (pattern `^rcp_pick:`) shows the pick list for
  `view`/`upd`/`rm` (mirrors `handle_interest_pick`).
- `handle_recipe_select` (pattern `^rcp_(view|upd|rm):`):
  - `view` → edits the message to show the full `name` + `body` inline,
    with a Back button to the recipes submenu — no confirm step, this is
    non-destructive.
  - `upd` → arms `context.user_data["awaiting"] = {"kind": "update_recipe",
    "payload": {"index": index}}`, prompts "Paste the replacement recipe
    for #<n> (I'll clean it up the same way):".
  - `rm` → `menus.confirm_keyboard(f"rcp_rm:{index}")`, same "Delete recipe
    #<n>: "<title>"?" phrasing as `/removeinterest`.
- `handle_confirm` gets one new `elif token.startswith("rcp_rm:")` branch
  calling `_do_remove_recipe(..., from_menu=True)`.
- `update_recipe` capture branch in `_consume_awaiting` re-runs the same
  interpret prompt + parse as add, then `RecipeStore.update(index, title,
  body)` — **the title can change** if Claude infers a different one from
  the new paste; this is accepted, matching how `/updateinterests` already
  replaces text wholesale rather than preserving anything from the old
  entry.

### Menu layout

Adding a fifth top-level action. Proposed:

```
🚌 Bus        📰 News
🔬 Research   🔗 Summarize
🍳 Recipes    ⭐ Interests
🔒 End session                 (own row — was paired with Interests before)
🛠 Admin                        (admin sessions only)
```

Flagged for review the same way the `/research` menu rearrangement was:
pairing the two "manage a personal list" actions (Recipes, Interests)
together reads more consistently than the current Interests+End-session
pairing, but it does mean End session moves to its own row. Open to a
different arrangement if this doesn't feel right.

### Command registration

New `CommandSpec`s, category `"Recipes"` (own `/help` section):

```python
CommandSpec("addrecipe", "/addrecipe", "paste a recipe to save (I'll clean it up)",
            self.handle_add_recipe, category="Recipes"),
CommandSpec("getrecipes", "/getrecipes", "list your saved recipes",
            self.handle_get_recipes, category="Recipes"),
CommandSpec("updaterecipe", "/updaterecipe <n>", "replace a saved recipe by pasting a new one",
            self.handle_update_recipe, category="Recipes"),
CommandSpec("removerecipe", "/removerecipe <n>", "delete a saved recipe",
            self.handle_remove_recipe, category="Recipes"),
```

### Where the code lives

`app.py`, not a new `ykbot/handlers/` module — interests/news/admin/menu are
all still in `app.py` too; the handler-extraction split (`bus.py` was the
pilot) is a "partly done" migration per the 2026-07-20 handoff, and recipes
shouldn't jump ahead of that ordering. `RecipeStore` itself is its own file
(`ykbot/recipes.py`), same as `InterestsStore` gets `ykbot/interests.py`.

## Deliberate non-goals

- No raw-paste fallback storage (confirmed with the user — cleaned version
  only).
- No recipe search/generation/suggestion — this command family only stores
  and retrieves what the user already has.
- No tags, categories, servings-as-a-field, or structured
  ingredients/steps arrays — the body is one formatted blob.
- No per-account data migration concerns — this is new, empty data for
  every account.

## Security

Identical posture to every other command: `_require_session` gates all four
handlers and every new callback handler re-checks it (button visibility is
never authorization, per existing convention). `web_tools=False` — the
interpret prompt never gets `WebSearch`/`WebFetch`, so it can't be used as a
detour to the web-capable sandbox. The only new prompt-injection-shaped
surface is: could a malicious paste make Claude ignore the "don't invent
content" instruction and emit something harmful? The blast radius is the
same as any other stored text field in this bot (interests, news-history
subjects) — it's rendered back only to the same account via Telegram-HTML
formatting, never executed, never shown to other accounts.

## Testing

Suite must stay green at 100% line coverage
(`python3 -m unittest discover -s tests -t .`), reusing `tests/helpers.py`
doubles.

- `RecipeStore`: add/list/update/remove-by-index, renumbering after
  removal, out-of-range index handling — same test shape as
  `tests/test_interests.py` for `InterestsStore`.
- `_build_recipe_interpret_prompt`: mentions the anti-invention constraint,
  the `NOT_A_RECIPE` escape hatch, the `Title:` marker format.
- `_parse_recipe_reply`: valid `Title: ...` + body parses correctly;
  `NOT_A_RECIPE` and malformed replies both return `None`.
- `_do_add_recipe`: successful parse saves and confirms with the right
  index; `NOT_A_RECIPE`/unparseable reply does not save, sends the
  try-again message; failed Claude call sends nothing extra (relies on
  `_run_claude`'s own error path, already covered elsewhere).
- Menu wiring: `act:addrecipe` arms the right `awaiting` slot; `rcp_pick:*`
  shows the right pick list per action; `rcp_view:*` shows full text
  without a confirm step; `rcp_rm:*` requires confirm before deleting;
  `rcp_upd:*` arms the update capture with the right index.
- `handle_confirm`'s new `rcp_rm:` branch; `_consume_awaiting`'s two new
  branches (`recipe_raw`, `update_recipe`).
- `_build_command_specs`/`/help`: all four commands appear under their own
  "Recipes:" category.
- `FakeUpdate(callback_only=True)` reused for every new callback-handler
  test, per the existing established gap this double exists to close.

## Deployment

A code change: full standing runbook (verify while the old bot runs →
/proc-scan kill → relaunch → confirm one healthy instance, no traceback).
Live smoke test: `/addrecipe` then paste a real recipe (confirm a clean
save); `/menu` → 🍳 Recipes → ➕ Add → paste (confirm 🏠 also present this
time); 📋 List → tap a name (confirm full text shown); ✏️ Update on that
same entry with a different paste (confirm the title can change); 🗑 Remove
→ confirm → gone, remaining entries renumbered.
