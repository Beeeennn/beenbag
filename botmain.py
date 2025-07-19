# hello_bot.py
import os, logging, asyncio
import discord
from discord.ext import commands
from aiohttp import web

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN in env")
PORT = int(os.getenv("PORT", "8080"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user}")

@bot.event
async def on_message(msg):
    if msg.author == bot.user:
        return
    if msg.content.lower().startswith("hi"):
        await msg.channel.send(f"Hi, {msg.author.mention}!")
    await bot.process_commands(msg)

# HTTP server for uptime ping
async def handle_ping(req): return web.Response(text="pong")
async def start_http():
    app = web.Application(); app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info(f"HTTP on port {PORT}")

async def main():
    await start_http()
    while True:
        try:
            await bot.start(TOKEN)
        except Exception:
            logging.exception("Crash—reconnecting in 5s")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
