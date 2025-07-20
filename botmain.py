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








@bot.command(name="chop")
@commands.cooldown(1, 240, commands.BucketType.user)  # 1 use per 60s per user
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
    """Show user’s inventory."""
    user_id = ctx.author.id

    # Fetch all their resources, tools and other stats
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT wood, cobblestone, iron, gold, diamond,
                   wheat
            FROM players
            WHERE user_id = $1
            """,
            user_id
        )

    # If they don’t even have a row yet
    if not row:
        return await ctx.send(f"{ctx.author.mention}, your inventory is empty.")

    # Build a list of non-zero items with emojis
    items = [
        ("wood", "🌳"),
        ("cobblestone", "🪨"),
        ("iron", "🔩"),
        ("gold", "🪙"),
        ("diamond", "💎"),
        ("wheat", "🌾")
    ]
    lines = []
    for key, emoji in items:
        val = row[key]
        if val and val > 0:
            # Pretty-print the field name
            name = key.replace("_", " ").title()
            lines.append(f"{emoji} **{name}**: {val}")

    # Send the message
    await ctx.send(
        f"{ctx.author.mention}'s Inventory:\n" +
        ("\n".join(lines) if lines else "Nothing in your inventory yet.")
    )

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
