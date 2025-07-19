import os
import discord
from discord.ext import commands
import asyncio
import logging
from aiohttp import web
import json

# Configure logging for visibility
logging.basicConfig(level=logging.INFO)

# Read token and port from environment variables
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment variable not set")
PORT = int(os.getenv("PORT", 8080))

# File for persisting hi counts
HI_COUNTS_FILE = os.getenv("HI_COUNTS_FILE", "hi_counts.json")

# Load existing hi counts or initialize empty
try:
    with open(HI_COUNTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        hi_counts = {int(k): v for k, v in data.items()}
except (FileNotFoundError, json.JSONDecodeError):
    hi_counts = {}

# Helper to save counts
def save_hi_counts():
    try:
        with open(HI_COUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in hi_counts.items()}, f, indent=2)
    except Exception as e:
        logging.exception(f"Failed to save hi counts: {e}")

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"Bot ready as {bot.user}")

@bot.event
async def on_message(message):
    # Ignore self messages
    if message.author == bot.user:
        return

    # Check for 'hi' prefix
    if message.content.lower().startswith("hi"):
        user_id = message.author.id
        hi_counts[user_id] = hi_counts.get(user_id, 0) + 1
        save_hi_counts()
        count = hi_counts[user_id]
        await message.channel.send(
            f"Hi, {message.author.mention}! You've said hi {count} time{'s' if count != 1 else ''}."
        )

    # Allow commands to work
    await bot.process_commands(message)

@bot.command(name="hi_leaderboard")
async def hi_leaderboard(ctx):
    if not hi_counts:
        await ctx.send("No one has said hi yet!")
        return
    sorted_counts = sorted(hi_counts.items(), key=lambda item: item[1], reverse=True)
    lines = []
    for user_id, count in sorted_counts:
        user = bot.get_user(user_id)
        name = user.name if user else str(user_id)
        lines.append(f"{name}: {count}")
    await ctx.send("**Hi Leaderboard**\n" + "\n".join(lines))

# Simple HTTP server to keep alive on hosts
async def handle_ping(request):
    return web.Response(text="pong")

async def start_http_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logging.info(f"HTTP server running on port {PORT}")

async def main():
    await start_http_server()
    retry_delay = 5
    while True:
        try:
            await bot.start(TOKEN)
        except Exception:
            logging.exception(f"Disconnected; reconnecting in {retry_delay}s")
            await asyncio.sleep(retry_delay)

if __name__ == "__main__":
    asyncio.run(main())
