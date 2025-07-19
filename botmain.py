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

@bot.event
async def on_message(message):
    # 1) Ignore bots (including ourselves)
    if message.author.bot:
        return

    # 2) If someone says “hi”, bump their count
    if message.content.lower().startswith("hi"):
        user_id = message.author.id
        # UPSERT: +1 to hi_count
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO hi_counts (user_id, hi_count)
                VALUES ($1, 1)
                ON CONFLICT (user_id) DO UPDATE
                  SET hi_count = hi_counts.hi_count + 1;
            """, user_id)
            # Fetch the new total
            row = await conn.fetchrow(
                "SELECT hi_count FROM hi_counts WHERE user_id = $1",
                user_id
            )
            count = row["hi_count"]

        # 3) Reply with the updated count
        await message.channel.send(
            f"Hi, {message.author.mention}! "
            f"You've said hi {count} time{'s' if count != 1 else ''}."
        )

    # 4) Allow commands to run
    await bot.process_commands(message)

@bot.command(name="hi_leaderboard")
async def hi_leaderboard(ctx):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, hi_count FROM hi_counts ORDER BY hi_count DESC"
        )
    if not rows:
        return await ctx.send("No one’s said hi yet!")

    lines = []
    for record in rows:
        uid   = record["user_id"]
        count = record["hi_count"]

        # Try to resolve to a Member for a nickname
        member = ctx.guild.get_member(uid)
        if member:
            name = member.display_name
        else:
            try:
                user = await bot.fetch_user(uid)
                name = f"{user.name}#{user.discriminator}"
            except:
                name = f"<Unknown {uid}>"

        lines.append(f"**{name}** — {count} hi’s")

    await ctx.send("\n".join(lines))

# HTTP endpoints
async def handle_ping(request):
    return web.Response(text="pong")

async def handle_give_his(request):
    # Auth
    auth = request.headers.get("Authorization", "")
    if not ADMIN_TOKEN or auth != f"Bearer {ADMIN_TOKEN}":
        return web.Response(status=401, text="Unauthorized")

    # Params
    try:
        user_id = int(request.query["user_id"])
        amount  = int(request.query["amount"])
    except (KeyError, ValueError):
        return web.Response(status=400, text="Missing or invalid user_id/amount")

    # UPSERT with arbitrary amount
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO hi_counts (user_id, hi_count)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
              SET hi_count = hi_counts.hi_count + $2;
        """, user_id, amount)
        row = await conn.fetchrow(
            "SELECT hi_count FROM hi_counts WHERE user_id = $1",
            user_id
        )
        new_count = row["hi_count"]

    return web.json_response({"user_id": user_id, "new_count": new_count})

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
