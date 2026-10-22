"""
Haikyu!! VOBACA!! BREAK Card Lookup Discord Bot (Slash Commands)

Commands:
  /card <name>          - Search for a card by name (shows first result)
  /card <name> [page]   - Show a specific result when multiple matches exist

Setup:
  1. Install deps:  pip install "discord.py>=2.0" requests
  2. Set environment variable:
       Windows PowerShell:  $env:DISCORD_TOKEN = "your_bot_token_here"
       Windows CMD:         set DISCORD_TOKEN=your_bot_token_here
  3. Run:  python bot.py

  On first run the slash command is synced globally (can take up to 1 hour to
  appear in Discord). To sync instantly to a test server, set TEST_GUILD_ID
  to your server's ID in the config section below.
"""

import io
import math
import os
import requests
from typing import Optional, List

import discord
from discord import app_commands

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
    "Karasuno":           discord.Colour.from_rgb(254, 121, 2),
    "Nekoma":             discord.Colour.from_rgb(192, 0,   0),
    "Nekoma Junior High": discord.Colour.from_rgb(160, 0,   0),
    "Aoba Johsai":        discord.Colour.from_rgb(0,   112, 192),
    "Fukurodani":         discord.Colour.from_rgb(0,   32,  96),
    "Inarizaki":          discord.Colour.from_rgb(128, 0,   128),
    "Shiratorizawa":      discord.Colour.from_rgb(89,  30,  145),
    "Date Tech":          discord.Colour.from_rgb(0,   176, 80),
    "Itachiyama":         discord.Colour.from_rgb(0,   70,  127),
    "Nohebi":             discord.Colour.from_rgb(85,  107, 47),
    "Johzenji":           discord.Colour.from_rgb(255, 192, 0),
    "Kakugawa":           discord.Colour.from_rgb(165, 42,  42),
    "Kamomedai":          discord.Colour.from_rgb(150, 150, 150),
    "Ohgi Minami":        discord.Colour.from_rgb(255, 102, 0),
    "Sarukawa Tech":      discord.Colour.from_rgb(70,  130, 180),
    "Tokonami":           discord.Colour.from_rgb(64,  64,  64),
    "Tsubakihara":        discord.Colour.from_rgb(0,   128, 128),
    "Wakutani Minami":    discord.Colour.from_rgb(200, 100, 50),
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
    set: Optional[str] = None,
) -> List[dict]:
    """
    Fetch all cards matching the query from the API, then filter to only those
    whose name contains the query (case-insensitive). Optionally further filter
    by rarity (e.g. "SR") and/or set code (e.g. "P01").
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

    # Filter: name must contain the query
    query_lower = query.lower()
    items = [i for i in items if query_lower in i.get("name", "").lower()]

    # Filter: rarity must match exactly (case-insensitive)
    if rarity:
        rarity_upper = rarity.upper()
        items = [i for i in items if i.get("vobarity", "").upper() == rarity_upper]

    # Filter: product_type must contain the set code (case-insensitive)
    # e.g. "P01" matches "HV-P01-EN", "D01" matches "HV-D01-EN"
    if set:
        set_upper = set.upper()
        items = [i for i in items if set_upper in i.get("product_type", "").upper()]

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

        embed.add_field(
            name="Included in",
            value=f"{card.get('product_type', '')} {card.get('product_name', '')}".strip() or "-",
            inline=False,
        )

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
    set="Set to filter by",
    verbose="Include card text in the embed (defaults to false)"
)
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
    app_commands.Choice(name="DP",      value="DP")
])
@app_commands.choices(set=[
    app_commands.Choice(name="The Dumpster Battle",         value="P01"),
    app_commands.Choice(name="Karasuno Starter Deck",       value="D01"),
    app_commands.Choice(name="Nekoma Starter Deck",         value="D02"),
    app_commands.Choice(name="Promos",                      value="PR")
])

async def card_slash(
    interaction: discord.Interaction,
    name: str,
    rarity: Optional[str] = None,
    set: Optional[str] = None,
    verbose: Optional[bool] = False
):
    # Defer immediately — API calls can take a moment
    await interaction.response.defer()

    try:
        raw_results = search_cards(name, rarity=rarity, set=set)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code >= 500:
            # Session may have gone stale — reset it and try once more
            global _session
            _session = None
            try:
                raw_results = search_cards(name, rarity=rarity, set=set)
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
        if set:
            filters.append(f"set **{set.upper()}**")
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No bot token found.")
        print("Set the DISCORD_TOKEN environment variable before running.")
        print("  PowerShell:  $env:DISCORD_TOKEN = 'your-token-here'")
        print("  CMD:         set DISCORD_TOKEN=your-token-here")
        raise SystemExit(1)
    bot.run(TOKEN)
