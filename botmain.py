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
async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_post("/give_his", handle_give_his)
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
