import os
import asyncio
import logging

import discord
from discord.ext import commands
import asyncpg
from aiohttp import web

# Configure logging
logging.basicConfig(level=logging.INFO)

# Read required env vars
TOKEN        = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_TOKEN  = os.getenv("ADMIN_TOKEN")
PORT         = int(os.getenv("PORT", 8080))

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment variable not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set")

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# We'll hold an asyncpg pool here
db_pool: asyncpg.Pool = None

async def init_db():
    """Create a connection pool and ensure the hi_counts table exists."""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS hi_counts (
                user_id   BIGINT PRIMARY KEY,
                hi_count  INT     NOT NULL DEFAULT 0
            );
        """)
    logging.info("Postgres connected and hi_counts table ready")

@bot.event
async def on_ready():
    logging.info(f"Bot ready as {bot.user}")

# HTTP endpoints
async def handle_ping(request):
    return web.Response(text="pong")


CRAFT_RECIPES = {
    # tool        tier      wood   ore_count  ore_column    uses
    ("pickaxe",   "wood"):    (1,    3,      "wood", 10),
    ("pickaxe",   "stone"):   (1,    3,      "cobblestone", 10),
    ("pickaxe",   "iron"):    (1,    3,      "iron",        10),
    ("pickaxe",   "gold"):    (1,    3,      "gold",        10),
    ("pickaxe",   "diamond"): (1,    3,      "diamond",     10),

    ("hoe",       "wood"):    (1,    2,      "wood", 10),
    ("hoe",       "stone"):   (1,    2,      "cobblestone", 10),
    ("hoe",       "iron"):    (1,    2,      "iron",        10),
    ("hoe",       "gold"):    (1,    2,      "gold",        10),
    ("hoe",       "diamond"): (1,    2,      "diamond",     10),

    ("fishing_rod", "wood"):  (3,    0,      None,          10),
    ("fishing_rod", "stone"): (3,    0,      None,          10),
    ("fishing_rod", "iron"):  (3,    0,      None,          10),
    ("fishing_rod", "gold"):  (3,    0,      None,          10),
    ("fishing_rod", "diamond"):(3,   0,      None,          10),

    ("sword",     "wood"):    (1,    2,      "wood", 3),
    ("sword",     "stone"):   (1,    2,      "cobblestone", 3),
    ("sword",     "iron"):    (1,    2,      "iron",        3),
    ("sword",     "gold"):    (1,    2,      "gold",        3),
    ("sword",     "diamond"): (1,    2,      "diamond",     3),
}

@bot.command(name="craft")
async def craft(ctx, *args):
    """
    Usage:
      !craft <tool>              → fishing rod only
      !craft <tool> <tier>       → other tools
    Examples:
      !craft fishing rod
      !craft pickaxe iron
    """
    if not args:
        return await ctx.send("❌ Usage: `!craft <tool> [tier]`")

    # Build tool name from all but last arg; tier is last arg if 2+ args
    if len(args) == 1:
        tool_raw = args[0]
        tier = None
    else:
        tool_raw = "_".join(args[:-1])
        tier = args[-1].lower()

    tool = tool_raw.replace(" ", "_").lower()

    # If it’s the fishing rod, force tier to “wood”
    if tool in ("fishing_rod", "fishingrod", "fishing"):
        tier = "wood"

    if tier is None:
        return await ctx.send("❌ You must specify a tier for that tool.")

    key = (tool, tier)
    if key not in CRAFT_RECIPES:
        return await ctx.send("❌ Invalid recipe. Try `!craft pickaxe iron` or `!craft fishing rod`.")

    wood_cost, ore_cost, ore_col, uses = CRAFT_RECIPES[key]

    if tier == "wood":
        wood_cost += ore_cost
        ore_cost == 0
        ore_col = None
        
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        # Ensure player row
        await conn.execute(
            "INSERT INTO players (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
            user_id
        )
        # Fetch their resources
        query = f"SELECT wood, {ore_col or '0'} as ore_have FROM players WHERE user_id = $1;"
        row = await conn.fetchrow(query, user_id)
        wood_have, ore_have = row["wood"], row["ore_have"]

        if wood_have < wood_cost or ore_have < ore_cost:
            need = [f"**{wood_cost} wood**"]
            if ore_col:
                need.append(f"**{ore_cost} {ore_col}**")
            return await ctx.send(f"❌ You need { ' and '.join(need) } to craft that.")

        # Deduct resources
        updates = ["wood = wood - $1"]
        params = [wood_cost]
        if ore_col:
            updates.append(f"{ore_col} = {ore_col} - $2")
            params.append(ore_cost)
        params.append(user_id)
        await conn.execute(
            f"UPDATE players SET {', '.join(updates)} WHERE user_id = ${len(params)};",
            *params
        )

        # Give the tool
        await conn.execute("""
            INSERT INTO tools (user_id, tool_name, tier, uses_left)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, tool_name, tier) DO UPDATE
              SET uses_left = tools.uses_left + EXCLUDED.uses_left;
        """, user_id, tool, tier, uses)

    await ctx.send(f"🔨 You crafted a **{tier.title()} {tool.replace('_',' ').title()}** with {uses} uses!")

@craft.error
async def craft_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send("❌ Usage: `!craft <tool> [tier]`")
    raise error

@bot.command(name="chop")
@commands.cooldown(1, 240, commands.BucketType.user)  # 1 use per 240s per user
async def chop(ctx):
    """Gain 1 wood every 60s."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        # ensure the player record exists
        await conn.execute(
            "INSERT INTO players (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
            user_id
        )

        # grant 1 wood
        await conn.execute(
            "UPDATE players SET wood = wood + 1 WHERE user_id = $1;",
            user_id
        )

        # fetch the updated wood count
        row = await conn.fetchrow(
            "SELECT wood FROM players WHERE user_id = $1;",
            user_id
        )

    wood = row["wood"]
    await ctx.send(
        f"{ctx.author.mention} swung their axe and chopped 🌳 **1 wood**! "
        f"You now have **{wood}** wood."
    )
@chop.error
async def chop_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)  # seconds remaining
        await ctx.send(
            f"This command is on cooldown. Try again in {retry} second{'s' if retry != 1 else ''}."
        )
        return
    # For any other errors, let them bubble up
    raise error

@bot.command(name="inv", aliases=["inventory"])
async def inv(ctx):
    """Show user’s inventory in a rich embed."""
    user_id = ctx.author.id

    # 1) Fetch their row
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT wood, cobblestone, iron, gold, diamond,
                   wheat
            FROM players
            WHERE user_id = $1
        """, user_id)

    if not row:
        return await ctx.send(f"{ctx.author.mention}, your inventory is empty.")

    # 2) Build the embed
    embed = discord.Embed(
        title=f"{ctx.author.display_name}'s Inventory",
        color=discord.Color.blue()
    )
    # add their avatar
    if ctx.author.avatar:
        embed.set_thumbnail(url=ctx.author.avatar.url)

    # 3) List of stats you want to show (key, emoji, friendly name)
    stats = [
        ("wood",       "🌳", "Wood"),
        ("cobblestone","🪨", "Cobblestone"),
        ("iron",       "🔩", "Iron"),
        ("gold",       "🪙", "Gold"),
        ("diamond",    "💎", "Diamond"),
        ("wheat",      "🌾", "Wheat")
    ]

    # 4) Add a field for each non-zero stat
    for key, emoji, name in stats:
        val = row[key]
        if val and val > 0:
            embed.add_field(
                name=f"{emoji} {name}",
                value=str(val),
                inline=True
            )

    # 5) Footer or timestamp
    embed.set_footer(text="Use !shop to spend your emeralds")

    # 6) Send the embed
    await ctx.send(embed=embed)

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"HTTP server running on port {PORT}")

async def main():
    # 1) Init Postgres
    await init_db()
    # 2) Start HTTP server
    await start_http_server()
    # 3) Run the bot, reconnecting on errors
    retry_delay = 5
    while True:
        try:
            await bot.start(TOKEN)
        except Exception:
            logging.exception(f"Bot disconnected; reconnecting in {retry_delay}s")
            await asyncio.sleep(retry_delay)

if __name__ == "__main__":
    asyncio.run(main())
