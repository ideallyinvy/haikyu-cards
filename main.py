"""
Haikyu!! VOBACA!! BREAK Card Lookup Discord Bot (Slash Commands)

Commands:
  /card <name>          - Search for a card by name (shows first result)
  /card <name> [page]   - Show a specific result when multiple matches exist
  /decklist             - Build a decklist interactively, then post an image

Setup:
  1. Install deps:  pip install "discord.py>=2.0" requests Pillow
  2. Set environment variable:
       Windows PowerShell:  $env:DISCORD_TOKEN = "your_bot_token_here"
       Windows CMD:         set DISCORD_TOKEN=your_bot_token_here
  3. Run:  python bot.py

  On first run the slash command is synced globally (can take up to 1 hour to
  appear in Discord). To sync instantly to a test server, set TEST_GUILD_ID
  to your server's ID in the config section below.
"""

import asyncio
import io
import math
import os
import string
import requests
from typing import Optional, List

import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────────────────────

TOKEN         = os.environ.get("DISCORD_TOKEN", "")
# Optional: set to your test guild ID (int) for instant slash command sync.
# Leave as None to sync globally (takes up to 1 hour to propagate).
TEST_GUILD_ID: Optional[int] = None  # e.g. 123456789012345678

API_URL       = "https://www.takaratomy.co.jp/products/haikyuvobacabreak/english/cardlist/itemsearch.php"
IMAGE_BASE    = "https://www.takaratomy.co.jp/products/haikyuvobacabreak/english/cardlist/card"
CARD_PAGE_URL = "https://www.takaratomy.co.jp/products/haikyuvobacabreak/english/cardlist/card.html"

CARD_LIST_PAGE = "https://www.takaratomy.co.jp/products/haikyuvobacabreak/english/cardlist/"

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": CARD_LIST_PAGE,
}

# Persistent session — visits the card list page on first use to pick up any
# required session cookies, then reuses the same connection for all API calls.
_session: Optional[requests.Session] = None

def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        # Warm up: visit the card list page so the server sets any required
        # cookies (e.g. Cloudflare cf_clearance, PHPSESSID, etc.)
        _session.get(
            CARD_LIST_PAGE,
            headers={
                "User-Agent": API_HEADERS["User-Agent"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=15,
        )
    return _session

AFFILIATION_COLOURS = {
    "Karasuno":           discord.Colour.from_rgb(239, 109, 30),
    "Nekoma":             discord.Colour.from_rgb(222, 2,   38),
    "Nekoma Junior High": discord.Colour.from_rgb(222, 2,   38),
    "Aoba Johsai":        discord.Colour.from_rgb(1,   161, 159),
    "Fukurodani":         discord.Colour.from_rgb(168, 135, 87),
    "Inarizaki":          discord.Colour.from_rgb(168, 15,  33),
    "Shiratorizawa":      discord.Colour.from_rgb(109, 18,  128),
    "Date Tech":          discord.Colour.from_rgb(0,   98,  38),
    "Itachiyama":         discord.Colour.from_rgb(248, 237, 37),
    "Nohebi":             discord.Colour.from_rgb(4,   159, 96),
    "Johzenji":           discord.Colour.from_rgb(255, 192, 0),
    "Kakugawa":           discord.Colour.from_rgb(87,  182, 50),
    "Kamomedai":          discord.Colour.from_rgb(127, 206, 237),
    "Ohgi Minami":        discord.Colour.from_rgb(27,  33,  137),
    "Sarukawa Tech":      discord.Colour.from_rgb(1,   58,  144),
    "Tokonami":           discord.Colour.from_rgb(2,   166, 195),
    "Tsubakihara":        discord.Colour.from_rgb(250, 228, 153),
    "Wakutani Minami":    discord.Colour.from_rgb(234, 123, 155),
}
DEFAULT_COLOUR = discord.Colour.greyple()


def affiliation_colour(affiliation: str) -> discord.Colour:
    if not affiliation or affiliation == "-":
        return DEFAULT_COLOUR
    school = affiliation.replace("\u00b7", "·").split("·")[0].strip()
    return AFFILIATION_COLOURS.get(school, DEFAULT_COLOUR)

# ── API helpers ───────────────────────────────────────────────────────────────

def search_cards(
    query: str,
    rarity: Optional[str] = None,
    set_code: Optional[str] = None,
    card_number: Optional[str] = None,
) -> List[dict]:
    """
    Fetch all cards matching the query from the API, then filter to only those
    whose name contains the query (case-insensitive). Optionally further filter
    by rarity (e.g. "SR"), set code (e.g. "P01"), and/or card number (e.g. "002").
    """
    session = get_session()
    first   = session.get(API_URL, params={"word": query, "p": 1}, headers=API_HEADERS, timeout=15)
    first.raise_for_status()
    data  = first.json()
    items = data.get("items", [])
    total = data.get("count", 0)

    for page in range(2, math.ceil(total / 20) + 1):
        resp = session.get(API_URL, params={"word": query, "p": page}, headers=API_HEADERS, timeout=15)
        resp.raise_for_status()
        items.extend(resp.json().get("items", []))

    # Filter: name must contain every query token as a whole word, in any order
    # (case-insensitive) — e.g. "Hinata Shoyo" matches "Shoyo Hinata".
    # The middle dot (·) is treated as a token separator, not part of a token.
    # Punctuation is stripped from token boundaries so trailing periods, question
    # marks, etc. don't block matches (e.g. "challengers" matches "CHALLENGERS.").
    _strip = string.punctuation + "\u2026"
    query_tokens = {t.strip(_strip) for t in query.lower().split()}
    query_tokens.discard("")
    items = [
        i for i in items
        if query_tokens.issubset(
            {t.strip(_strip) for t in i.get("name", "").lower().replace("\u00b7", " ").split()}
        )
    ]

    # Filter: rarity must match exactly (case-insensitive)
    if rarity:
        rarity_upper = rarity.upper()
        items = [i for i in items if i.get("vobarity", "").upper() == rarity_upper]

    # Filter: product_type must contain the set code (case-insensitive)
    # e.g. "P01" matches "HV-P01-EN", "D01" matches "HV-D01-EN"
    if set_code:
        set_upper = set_code.upper()
        items = [i for i in items if set_upper in i.get("product_type", "").upper()]

    # Filter: card number must match (3-digit, zero-padded)
    # e.g. "2" → "002", matches card_no "HV-P01-002-EN"
    if card_number:
        padded = card_number.strip().zfill(3)
        items = [i for i in items if i.get("card_no", "").split("-")[2] == padded]

    return items


def deduplicate_cards(items: List[dict]) -> List[dict]:
    """
    Collapse parallel variants: keep one entry per card_no.
    Prefer the standard print over the parallel art variant.
    """
    seen  = {}
    order = []

    for item in items:
        card_no   = item["card_no"]
        vobarity  = item["vobarity"]
        is_parallel = vobarity.endswith("P") and len(vobarity) > 1

        if card_no not in seen:
            seen[card_no] = item
            order.append(card_no)
        elif not is_parallel:
            seen[card_no] = item  # prefer standard over parallel

    return [seen[k] for k in order]


def image_url(card: dict) -> str:
    return f"{IMAGE_BASE}/{card['card_no']}-{card['image_end']}.webp"


def card_page_url(card: dict) -> str:
    return f"{CARD_PAGE_URL}?no={card['card_no']}&voba={card['vobarity']}"


def fetch_image_bytes(url: str) -> Optional[bytes]:
    """Download a card image, returning raw bytes or None on failure."""
    try:
        r = get_session().get(url, headers=API_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.content
    except requests.RequestException:
        pass
    return None


# ── Embed builder ─────────────────────────────────────────────────────────────

def build_card_embed(card: dict, verbose) -> discord.Embed:
    """Build a rich embed for a single card entry."""
    name     = card.get("name", "Unknown")
    vobarity = card.get("vobarity", "")
    colour   = affiliation_colour(card.get("affiliation", ""))

    title = f"{name}  [{vobarity}]"

    embed = discord.Embed(title=title, colour=colour, url=card_page_url(card))

    if (verbose):

        category = card.get("category", "")

        embed.add_field(name="Category",  value=card.get("category", "-"),  inline=True)
        embed.add_field(name="Vobarity",  value=vobarity or "-",            inline=True)
        embed.add_field(name="Card No.",  value=card.get("card_no", "-"),   inline=True)

        affiliation = card.get("affiliation", "-") or "-"
        pos_ruby    = (card.get("position_ruby", "") or "").strip()
        pos_display = pos_ruby if pos_ruby else (card.get("position", "") or "-")

        embed.add_field(name="Affiliation", value=affiliation, inline=True)
        if category == "CHARACTER":
            embed.add_field(name="Position", value=pos_display, inline=True)

        skill = (card.get("skill", "") or "").strip()
        if skill and skill != "-":
           embed.add_field(name="Skill", value=skill[:1024], inline=False)

        annotation = (card.get("annotation", "") or "").strip()
        if annotation and annotation != "-":
            embed.add_field(name="Annotation", value=annotation[:1024], inline=False)

        if category == "CHARACTER":
            stats = []
            for stat in ("serve", "block", "receive", "toss", "attack"):
                val = card.get(stat, "-") or "-"
                stats.append(f"**{stat.capitalize()}**: {val}")
            embed.add_field(name="Stats", value="  |  ".join(stats), inline=False)

        illus        = card.get("illustrator", "") or ""
        copyright_   = card.get("copyright", "") or ""
        footer_parts = []
        if illus:
            footer_parts.append(f"Illus. {illus}")
        if copyright_:
            footer_parts.append(copyright_)
        if footer_parts:
            embed.set_footer(text="  ".join(footer_parts))

    return embed


# ── Bot setup ─────────────────────────────────────────────────────────────────

class CardBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if TEST_GUILD_ID:
            guild = discord.Object(id=TEST_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Slash commands synced to test guild {TEST_GUILD_ID}")
        else:
            await self.tree.sync()
            print("Slash commands synced globally (may take up to 1 hour to appear)")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print("------")


bot = CardBot()


# ── /card command ─────────────────────────────────────────────────────────────

@bot.tree.command(name="card", description="Look up a Haikyu!! VOBACA!! BREAK card by name")
@app_commands.describe(
    name="Card name to search for (e.g. Shoyo Hinata)",
    rarity="Rarity to filter by",
    set_code="Set to filter by",
    card_number="Card number (3 digits, e.g. 002)",
    verbose="Include card text in the embed (defaults to false)"
)
@app_commands.rename(set_code="set")
@app_commands.choices(rarity=[
    app_commands.Choice(name="SEC",     value="SEC"),
    app_commands.Choice(name="SR",      value="SR"),
    app_commands.Choice(name="SRP",     value="SRP"),
    app_commands.Choice(name="R",       value="R"),
    app_commands.Choice(name="RP",      value="RP"),
    app_commands.Choice(name="UC",      value="UC"),
    app_commands.Choice(name="UCP",     value="UCP"),
    app_commands.Choice(name="UT",      value="UT"),
    app_commands.Choice(name="UTP",     value="UTP"),
    app_commands.Choice(name="C",       value="C"),
    app_commands.Choice(name="CP",      value="CP"),
    app_commands.Choice(name="D",       value="D"),
    app_commands.Choice(name="DP",      value="DP"),
    app_commands.Choice(name="P",       value="P")
])
@app_commands.choices(set_code=[
    app_commands.Choice(name="The Dumpster Battle",         value="P01"),
    app_commands.Choice(name="Karasuno Starter Deck",       value="D01"),
    app_commands.Choice(name="Nekoma Starter Deck",         value="D02"),
    app_commands.Choice(name="Promos",                      value="PR")
])

async def card_slash(
    interaction: discord.Interaction,
    name: str,
    rarity: Optional[str] = None,
    set_code: Optional[str] = None,
    card_number: Optional[str] = None,
    verbose: Optional[bool] = False
):
    # Defer immediately — API calls can take a moment
    await interaction.response.defer()

    try:
        raw_results = search_cards(name, rarity=rarity, set_code=set_code, card_number=card_number)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code >= 500:
            # Session may have gone stale — reset it and try once more
            global _session
            _session = None
            try:
                raw_results = search_cards(name, rarity=rarity, set_code=set_code, card_number=card_number)
            except Exception as e2:
                await interaction.followup.send(f"Error fetching card data: {e2}")
                return
        else:
            await interaction.followup.send(f"Error fetching card data: {e}")
            return
    except Exception as e:
        await interaction.followup.send(f"Error fetching card data: {e}")
        return

    if not raw_results:
        # Build a helpful message showing which filters were active
        filters = []
        if rarity:
            filters.append(f"rarity **{rarity.upper()}**")
        if set_code:
            filters.append(f"set **{set_code.upper()}**")
        if card_number:
            filters.append(f"card number **{card_number.strip().zfill(3)}**")
        filter_str = f" with {' and '.join(filters)}" if filters else ""
        await interaction.followup.send(f'No cards found for **"{name}"**{filter_str}.')
        return

    results = deduplicate_cards(raw_results)
    card    = results[0]

    embed = build_card_embed(card, verbose)

    # Attach card image
    img_bytes = fetch_image_bytes(image_url(card))
    if img_bytes:
        filename = f"{card['card_no']}-{card['image_end']}.webp"
        file     = discord.File(io.BytesIO(img_bytes), filename=filename)
        embed.set_image(url=f"attachment://{filename}")
        await interaction.followup.send(embed=embed, file=file)
    else:
        embed.set_image(url=image_url(card))
        await interaction.followup.send(embed=embed)


# ── /decklist command ─────────────────────────────────────────────────────────

MAX_DECK_SIZE = 40
DECKLIST_COLS = 10          # thumbnails per row in the generated image
THUMB_WIDTH   = 200         # thumbnail width in pixels

# In-memory decklist sessions: user_id -> session dict
# session = {
#     "cards":       {card_no: {"card": dict, "count": int}},  # ordered dict
#     "rarity":      Optional[str],   # current rarity filter
#     "set_code":     Optional[str],   # current set filter
#     "total":        int,             # sum of all counts
#     "decklist_msg": Optional[discord.Message],  # message to update
# }
_decklists: dict[int, dict] = {}

# Select-menu options (shared between the decklist view's filters)
DECKLIST_RARITY_OPTIONS = [
    discord.SelectOption(label="None", value="none"),
    discord.SelectOption(label="SEC", value="SEC"),
    discord.SelectOption(label="SR",  value="SR"),
    discord.SelectOption(label="SRP", value="SRP"),
    discord.SelectOption(label="R",   value="R"),
    discord.SelectOption(label="RP",  value="RP"),
    discord.SelectOption(label="UC",  value="UC"),
    discord.SelectOption(label="UCP", value="UCP"),
    discord.SelectOption(label="UT",  value="UT"),
    discord.SelectOption(label="UTP", value="UTP"),
    discord.SelectOption(label="C",   value="C"),
    discord.SelectOption(label="CP",  value="CP"),
    discord.SelectOption(label="D",   value="D"),
    discord.SelectOption(label="DP",  value="DP"),
    discord.SelectOption(label="P",   value="P"),
]
DECKLIST_SET_OPTIONS = [
    discord.SelectOption(label="None",                  value="none"),
    discord.SelectOption(label="The Dumpster Battle",    value="P01"),
    discord.SelectOption(label="Karasuno Starter Deck",  value="D01"),
    discord.SelectOption(label="Nekoma Starter Deck",    value="D02"),
    discord.SelectOption(label="Promos",                 value="PR"),
]


# ── Decklist helpers ─────────────────────────────────────────────────────────

def add_card_to_decklist(user_id: int, card: dict, quantity: int) -> dict:
    """Add a card (or increase its count) in the user's decklist session."""
    session = _decklists.get(user_id)
    if not session:
        return {"error": "No active decklist session. Use /decklist to start."}
    if session["total"] + quantity > MAX_DECK_SIZE:
        remaining = MAX_DECK_SIZE - session["total"]
        return {"error": f"Cannot add {quantity} — deck has {session['total']}/{MAX_DECK_SIZE} cards. Only {remaining} more can be added."}
    card_no = card["card_no"]
    if card_no in session["cards"]:
        session["cards"][card_no]["count"] += quantity
    else:
        session["cards"][card_no] = {"card": card, "count": quantity}
    session["total"] += quantity
    return {"success": True}


def remove_card_from_decklist(user_id: int, card_no: str) -> dict:
    """Remove one copy of a card from the user's decklist session."""
    session = _decklists.get(user_id)
    if not session:
        return {"error": "No active decklist session."}
    if card_no not in session["cards"]:
        return {"error": "Card not in decklist."}
    session["cards"][card_no]["count"] -= 1
    session["total"] -= 1
    if session["cards"][card_no]["count"] <= 0:
        del session["cards"][card_no]
    return {"success": True}


def build_decklist_embed(session: dict, user_name: str = "") -> discord.Embed:
    """Build an embed summarizing the current decklist state."""
    total = session["total"]
    embed = discord.Embed(
        title=f"📋 Decklist ({total}/{MAX_DECK_SIZE} cards)",
        colour=discord.Colour.green() if total == MAX_DECK_SIZE else discord.Colour.blurple(),
    )
    if user_name:
        embed.set_author(name=user_name)

    if not session["cards"]:
        embed.description = "*Your decklist is empty. Click **Add Card** to start building.*"
    else:
        lines = []
        for card_no, item in session["cards"].items():
            card = item["card"]
            name     = card.get("name", "Unknown")
            vobarity = card.get("vobarity", "")
            count    = item["count"]
            lines.append(f"**{name}** [{vobarity}] ×{count}")
        text = "\n".join(lines)
        if len(text) > 4096:
            text = text[:4090] + "\n…"
        embed.description = text

    embed.set_footer(text="Use the buttons and dropdowns below to manage your decklist")
    return embed


def generate_decklist_image(cards: dict) -> bytes:
    """
    Composite every unique card thumbnail into a grid image with a count
    badge on each card.

    ``cards`` is the session's ``cards`` dict: ``{card_no: {"card": dict, "count": int}}``
    Returns PNG image bytes.
    """
    items = list(cards.values())
    if not items:
        img = Image.new("RGBA", (400, 100), (25, 25, 25, 255))
        draw = ImageDraw.Draw(img)
        draw.text((120, 40), "Empty decklist", fill=(200, 200, 200, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    n    = len(items)
    rows = math.ceil(n / DECKLIST_COLS)

    # Load a font for the count badge
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 28)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except Exception:
            font = ImageFont.load_default()

    # Resampling filter (Pillow >= 9 uses Image.Resampling)
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    # Download + resize every card image
    thumbnails: list[tuple[Image.Image, int]] = []
    for item in items:
        card  = item["card"]
        count = item["count"]
        img_bytes = fetch_image_bytes(image_url(card))
        if img_bytes:
            img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
            ratio  = THUMB_WIDTH / img.width
            thumb_h = int(img.height * ratio)
            img = img.resize((THUMB_WIDTH, thumb_h), resample)
        else:
            # Placeholder for failed downloads
            img = Image.new("RGBA", (THUMB_WIDTH, int(THUMB_WIDTH * 1.395)), (60, 60, 60, 255))
            d = ImageDraw.Draw(img)
            d.text((10, 10), "No image", fill=(200, 200, 200, 255))
        thumbnails.append((img, count))

    tw, th = thumbnails[0][0].size

    PADDING  = 8
    BADGE_R  = 24           # badge circle radius
    CELL_W   = tw + PADDING
    CELL_H   = th + PADDING
    canvas_w = DECKLIST_COLS * CELL_W + PADDING
    canvas_h = rows * CELL_H + PADDING

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (25, 25, 25, 255))
    draw   = ImageDraw.Draw(canvas)

    for i, (img, count) in enumerate(thumbnails):
        col = i % DECKLIST_COLS
        row = i // DECKLIST_COLS
        x = PADDING + col * CELL_W
        y = PADDING + row * CELL_H
        canvas.paste(img, (x, y))

        # Count badge — bottom-right corner
        cx = x + tw - BADGE_R - 6
        cy = y + th - BADGE_R - 6
        draw.ellipse(
            [cx - BADGE_R, cy - BADGE_R, cx + BADGE_R, cy + BADGE_R],
            fill=(0, 0, 0, 210),
            outline=(255, 255, 255, 255),
            width=3,
        )
        draw.text((cx, cy), str(count), fill=(255, 255, 255, 255), font=font, anchor="mm")

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


async def refresh_decklist(user_id: int) -> bool:
    """
    Try to edit the existing decklist message with the current session state.
    Returns True on success, False if the message could not be edited.
    """
    session = _decklists.get(user_id)
    if not session:
        return False
    msg = session.get("decklist_msg")
    if not msg:
        return False
    embed = build_decklist_embed(session)
    view  = DecklistView(user_id)
    try:
        await msg.edit(embed=embed, view=view)
        return True
    except Exception:
        return False


# ── Decklist views & modal ─────────────────────────────────────────────────────

class DecklistView(discord.ui.View):
    """Main interactive view for the /decklist command."""

    def __init__(self, user_id: int):
        super().__init__(timeout=1200)  # 20-minute session timeout
        self.user_id = user_id
        # Reflect currently-selected filters as the default option
        session  = _decklists.get(user_id, {})
        rarity   = session.get("rarity") or "none"
        set_code = session.get("set_code") or "none"
        for opt in self.rarity_select.options:
            opt.default = (opt.value == rarity)
        for opt in self.set_select.options:
            opt.default = (opt.value == set_code)

    async def on_timeout(self):
        """Clean up the session and disable the builder message on expiry."""
        session = _decklists.pop(self.user_id, None)
        msg = session.get("decklist_msg") if session else None
        if msg:
            try:
                await msg.edit(
                    content="⏰ Session expired — this decklist timed out after 20 minutes. Use /decklist to start a new one.",
                    embed=None,
                    view=None,
                )
            except Exception:
                pass

    # ── Filter dropdowns (rows 0-1) ───────────────────────────────────────────

    @discord.ui.select(
        placeholder="Rarity filter",
        options=DECKLIST_RARITY_OPTIONS,
        min_values=1, max_values=1,
        row=0,
    )
    async def rarity_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        _decklists[self.user_id]["rarity"] = None if select.values[0] == "none" else select.values[0]
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Set filter",
        options=DECKLIST_SET_OPTIONS,
        min_values=1, max_values=1,
        row=1,
    )
    async def set_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        _decklists[self.user_id]["set_code"] = None if select.values[0] == "none" else select.values[0]
        await interaction.response.defer()

    # ── Action buttons (row 2) ────────────────────────────────────────────────

    @discord.ui.button(label="Add Card", style=discord.ButtonStyle.primary, row=2)
    async def add_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Remember which message the view is attached to so we can edit it later
        _decklists[self.user_id]["decklist_msg"] = interaction.message
        await interaction.response.send_modal(AddCardModal(self.user_id))

    @discord.ui.button(label="Remove Card", style=discord.ButtonStyle.danger, row=2)
    async def remove_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = _decklists.get(self.user_id, {})
        if not session.get("cards"):
            await interaction.response.send_message(
                "Your decklist is empty — nothing to remove.", ephemeral=True
            )
            return
        _decklists[self.user_id]["decklist_msg"] = interaction.message
        view = RemoveCardView(self.user_id)
        await interaction.response.send_message(
            "Select a card to remove (one copy):", view=view, ephemeral=True
        )

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.success, row=2)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = _decklists.get(self.user_id, {})
        if not session.get("cards"):
            await interaction.response.send_message(
                "Your decklist is empty. Add cards first!", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if not interaction.channel:
            await interaction.followup.send(
                "Cannot post the decklist — this channel type is not supported.", ephemeral=True
            )
            return

        # Generate the composite image (CPU-bound → run in executor)
        loop      = asyncio.get_running_loop()
        img_bytes = await loop.run_in_executor(None, generate_decklist_image, session["cards"])

        # Post the finished decklist publicly
        file  = discord.File(io.BytesIO(img_bytes), filename="decklist.png")
        embed = discord.Embed(
            title=f"📋 Decklist by {interaction.user.display_name}",
            colour=discord.Colour.blurple(),
        )
        embed.set_image(url="attachment://decklist.png")
        card_lines = "\n".join(
            f"**{c['card']['name']}** [{c['card']['vobarity']}] ×{c['count']}"
            for c in session["cards"].values()
        )
        embed.description = (card_lines or "Empty")[:4096]
        embed.set_footer(text=f"{session['total']} cards")
        await interaction.channel.send(embed=embed, file=file)

        # Clean up
        _decklists.pop(self.user_id, None)
        await interaction.followup.send("✅ Decklist posted to the channel!", ephemeral=True)
        # Disable the builder view on the old message
        msg = session.get("decklist_msg")
        if msg:
            try:
                await msg.edit(content="✅ Decklist completed and posted.", embed=None, view=None)
            except Exception:
                pass


class AddCardModal(discord.ui.Modal):
    """Modal for entering card search parameters and quantity."""

    name = discord.ui.TextInput(
        label="Card Name",
        placeholder="e.g. Shoyo Hinata",
        required=True,
    )
    card_number = discord.ui.TextInput(
        label="Card Number (optional, 3 digits)",
        placeholder="e.g. 002",
        required=False,
        max_length=3,
    )
    quantity = discord.ui.TextInput(
        label="Quantity (1–40)",
        placeholder="e.g. 3",
        required=True,
        max_length=2,
    )

    def __init__(self, user_id: int):
        super().__init__(title="Add Card to Decklist")
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Parse quantity
        try:
            qty = int(self.quantity.value.strip())
        except ValueError:
            await interaction.followup.send("Quantity must be a number.", ephemeral=True)
            return
        if qty < 1 or qty > MAX_DECK_SIZE:
            await interaction.followup.send(
                f"Quantity must be between 1 and {MAX_DECK_SIZE}.", ephemeral=True
            )
            return

        name_val     = self.name.value.strip()
        card_num_val  = self.card_number.value.strip() or None

        session = _decklists.get(self.user_id)
        if not session:
            await interaction.followup.send(
                "No active decklist session. Use /decklist to start.", ephemeral=True
            )
            return

        # Check deck limit before searching
        if session["total"] + qty > MAX_DECK_SIZE:
            remaining = MAX_DECK_SIZE - session["total"]
            await interaction.followup.send(
                f"Cannot add {qty} — deck has {session['total']}/{MAX_DECK_SIZE} cards. "
                f"Only {remaining} more can be added.",
                ephemeral=True,
            )
            return

        # Search using the active filters from the session
        rarity   = session.get("rarity")
        set_code = session.get("set_code")
        try:
            raw_results = search_cards(
                name_val, rarity=rarity, set_code=set_code, card_number=card_num_val
            )
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            # Connection dropped or server error — reset the session and retry once
            if isinstance(e, requests.HTTPError) and (e.response is None or e.response.status_code < 500):
                await interaction.followup.send(f"Error searching: {e}", ephemeral=True)
                return
            global _session
            _session = None
            try:
                raw_results = search_cards(
                    name_val, rarity=rarity, set_code=set_code, card_number=card_num_val
                )
            except Exception as e2:
                await interaction.followup.send(f"Error searching: {e2}", ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(f"Error searching: {e}", ephemeral=True)
            return

        if not raw_results:
            filters = []
            if rarity:      filters.append(f"rarity **{rarity}**")
            if set_code:    filters.append(f"set **{set_code}**")
            if card_num_val: filters.append(f"card number **{card_num_val.zfill(3)}**")
            filter_str = f" with {' and '.join(filters)}" if filters else ""
            await interaction.followup.send(
                f'No cards found for **"{name_val}"**{filter_str}.', ephemeral=True
            )
            return

        results = deduplicate_cards(raw_results)

        if len(results) == 1:
            card = results[0]
            add_card_to_decklist(self.user_id, card, qty)
            # Update the decklist message
            if not await refresh_decklist(self.user_id):
                # Edit failed — send a fresh decklist message as a followup
                embed = build_decklist_embed(_decklists[self.user_id])
                view  = DecklistView(self.user_id)
                new_msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                _decklists[self.user_id]["decklist_msg"] = new_msg
            await interaction.followup.send(
                f"✅ Added **{card['name']}** [{card['vobarity']}] ×{qty}", ephemeral=True
            )
        else:
            # Multiple matches — let the user pick
            view = CardSelectView(self.user_id, results, qty)
            await interaction.followup.send(
                "Multiple cards found — select one:", view=view, ephemeral=True
            )


class CardSelectView(discord.ui.View):
    """View shown when a search returns multiple unique cards."""

    def __init__(self, user_id: int, results: list[dict], quantity: int):
        super().__init__(timeout=120)
        self.add_item(CardSelect(user_id, results, quantity))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class CardSelect(discord.ui.Select):
    """Dropdown for choosing one card from search results."""

    def __init__(self, user_id: int, results: list[dict], quantity: int):
        self.user_id  = user_id
        self.results  = results
        self.quantity = quantity
        options = []
        for card in results[:25]:  # Discord max 25 options
            label = f"{card.get('name', '?')} [{card.get('vobarity', '')}]"
            options.append(discord.SelectOption(
                label=label[:100],
                value=card["card_no"],
                description=card.get("card_no", ""),
            ))
        super().__init__(placeholder="Select a card…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        card_no = self.values[0]
        card    = next(c for c in self.results if c["card_no"] == card_no)

        result = add_card_to_decklist(self.user_id, card, self.quantity)
        if result.get("error"):
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        # Confirm selection on this message
        await interaction.response.edit_message(
            content=f"✅ Added **{card['name']}** [{card['vobarity']}] ×{self.quantity}",
            view=None,
        )
        # Update the main decklist message
        if not await refresh_decklist(self.user_id):
            embed   = build_decklist_embed(_decklists[self.user_id])
            view    = DecklistView(self.user_id)
            new_msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            _decklists[self.user_id]["decklist_msg"] = new_msg


class RemoveCardView(discord.ui.View):
    """View shown when the user wants to remove a card."""

    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.add_item(RemoveCardSelect(user_id))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class RemoveCardSelect(discord.ui.Select):
    """Dropdown listing current decklist cards for removal."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        session = _decklists.get(user_id, {})
        options = []
        for card_no, item in session.get("cards", {}).items():
            card  = item["card"]
            count = item["count"]
            label = f"{card.get('name', '?')} [{card.get('vobarity', '')}] ({count}×)"
            options.append(discord.SelectOption(
                label=label[:100],
                value=card_no,
                description=f"Remove one copy ({count} in deck)",
            ))
        super().__init__(placeholder="Select a card to remove…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        card_no = self.values[0]
        session = _decklists.get(self.user_id, {})
        card    = session["cards"].get(card_no, {}).get("card", {})
        old_count = session["cards"].get(card_no, {}).get("count", 0)

        result = remove_card_from_decklist(self.user_id, card_no)
        if result.get("error"):
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        # Confirm on this message
        await interaction.response.edit_message(
            content=f"🗑️ Removed one copy of **{card.get('name', '?')}** [{card.get('vobarity', '')}] "
                    f"({old_count}→{old_count - 1})",
            view=None,
        )
        # Update the main decklist message
        if not await refresh_decklist(self.user_id):
            embed   = build_decklist_embed(_decklists[self.user_id])
            view    = DecklistView(self.user_id)
            new_msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            _decklists[self.user_id]["decklist_msg"] = new_msg


# ── /decklist slash command ───────────────────────────────────────────────────

class DecklistResetView(discord.ui.View):
    """Shown when the user calls /decklist while a session is already active."""

    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id

    @discord.ui.button(label="Start Over", style=discord.ButtonStyle.danger)
    async def start_over(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Expire the old builder message
        old_session = _decklists.pop(self.user_id, None)
        old_msg     = old_session.get("decklist_msg") if old_session else None
        if old_msg:
            try:
                await old_msg.edit(
                    content="🗑️ Session discarded — a new decklist was started.",
                    embed=None,
                    view=None,
                )
            except Exception:
                pass

        # Create a fresh session
        _decklists[self.user_id] = {
            "cards":        {},
            "rarity":       None,
            "set_code":     None,
            "total":        0,
            "decklist_msg": None,
        }
        embed = build_decklist_embed(_decklists[self.user_id], interaction.user.display_name)
        view  = DecklistView(self.user_id)
        await interaction.response.edit_message(content=None, embed=embed, view=view)
        msg = await interaction.original_response()
        _decklists[self.user_id]["decklist_msg"] = msg


@bot.tree.command(name="decklist", description="Build a decklist interactively and generate a decklist image")
async def decklist_slash(interaction: discord.Interaction):
    """Start an interactive decklist-building session (ephemeral)."""
    user_id = interaction.user.id

    # Warn if a session is already active
    if user_id in _decklists:
        existing = _decklists[user_id]
        total    = existing.get("total", 0)
        await interaction.response.send_message(
            f"You already have an active decklist session ({total}/{MAX_DECK_SIZE} cards). "
            f"Finish or use the existing builder above.\n"
            f"If you want to start fresh, click **Start Over** below.",
            view=DecklistResetView(user_id),
            ephemeral=True,
        )
        return

    # Create a new session
    _decklists[user_id] = {
        "cards":        {},
        "rarity":       None,
        "set_code":     None,
        "total":        0,
        "decklist_msg": None,
    }

    embed = build_decklist_embed(_decklists[user_id], interaction.user.display_name)
    view  = DecklistView(user_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # Store the message so we can edit it later
    msg = await interaction.original_response()
    _decklists[user_id]["decklist_msg"] = msg


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No bot token found.")
        print("Set the DISCORD_TOKEN environment variable before running.")
        print("  PowerShell:  $env:DISCORD_TOKEN = 'your-token-here'")
        print("  CMD:         set DISCORD_TOKEN=your-token-here")
        raise SystemExit(1)
    bot.run(TOKEN)
