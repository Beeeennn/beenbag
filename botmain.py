import os
import asyncio
import logging
import random
import discord
from discord.ext import commands
import asyncpg
from aiohttp import web
from PIL import Image
import io
import dateparser

from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import string
import secrets
import re
from constants import *
import utils as u
import cc


chat_xp_cd = commands.CooldownMapping.from_cooldown(
    2,                # max tokens
    1800.0,           # per 1800 seconds (30m)
    commands.BucketType.user
)
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

#hold an asyncpg pool here
db_pool: asyncpg.Pool = None

async def hourly_channel_exp_flush():
    await bot.wait_until_ready()
    ch = bot.get_channel(1396194783713824800)
    while True:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT channel_id, exp
                  FROM channel_exp
                 WHERE exp > 0
            """)
            # zero them out
            await conn.execute("UPDATE channel_exp SET exp = 0 WHERE exp > 0")
        # hand each off to your existing gain_exp (which updates DB + roles)
        for record in rows:
            uid = await conn.fetch("""
                                      SELECT discord_id, yt_channel_name
                                      FROM accountinfo
                                      WHERE yt_channel_id = $1                                      
                                      """,record["channel_id"])
            xp  = record["exp"]
            name = uid["name"]
            ch.send(f"Giving **{xp}** exp to **{name}** for watching my stream")
            await asyncio.sleep(1)
            # pass None for ctx so gain_exp just does DB+roles without messaging
            await u.gain_exp(conn,bot,uid["discord_id"], xp, None)
        # wait one hour
        await asyncio.sleep(3600)

async def daily_level_decay():
    tz = ZoneInfo("Europe/London")
    await bot.wait_until_ready()
    ch=bot.get_channel(1396194783713824800)
    while True:
        # compute seconds until next midnight in London
        now = datetime.now(tz)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        delay = (tomorrow - now).total_seconds()
        await asyncio.sleep(delay)
        ch.send("Now removing 1 level from everyone, gotta stay active!")
        # 1) Demote everyone by one level
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT discord_id, exp FROM accountinfo")
        for record in rows:
            user_id = record["discord_id"]
            old_exp = record["exp"]
            old_lvl = u.get_level_from_exp(old_exp)
            if old_lvl <= 0:
                continue  # they’re already at level 0

            # compute new exp so they drop exactly one level
            new_lvl = old_lvl - 1
            if new_lvl > 0:
                new_exp = LEVEL_EXP[new_lvl] - 1
            else:
                new_exp = old_exp

            # write it back
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE accountinfo SET exp = $1 WHERE discord_id = $2",
                    new_exp, user_id
                )

            # 2) Fix up roles in every guild we share
            for guild in bot.guilds:
                member = guild.get_member(user_id)
                if not member:
                    continue
                # remove old milestone role if they had one
                if old_lvl in MILESTONE_ROLES:
                    old_role = discord.utils.get(
                        guild.roles, name=ROLE_NAMES[old_lvl]
                    )
                    if old_role in member.roles:
                        await member.remove_roles(old_role, reason="Daily level decay")
                    if old_lvl > 11:
                        new_role = discord.utils.get(
                        guild.roles, name=ROLE_NAMES[old_lvl-10]
                    )
                        await member.add_roles(new_role, reason="Daily level decay")

                # add new milestone role if needed
                if new_lvl in MILESTONE_ROLES:
                    new_role = discord.utils.get(
                        guild.roles, name=ROLE_NAMES[new_lvl]
                    )
                    if new_role and new_role not in member.roles:
                        await member.add_roles(new_role, reason="Daily level decay")

        # loop back around for the next midnight

async def init_db():
    """Create a connection pool """
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    cc.init_cc(db_pool)
    logging.info("Postgres connected")

# HTTP endpoints
async def handle_ping(request):
    return web.Response(text="pong")


@bot.event
async def on_ready():
    logging.info(f"Bot ready as {bot.user}")
    if not hasattr(bot, "_decay_task"):
        bot._decay_task = bot.loop.create_task(daily_level_decay())
    # Only schedule it once
    if not hasattr(bot, "_channel_exp_task"):
        bot._channel_exp_task = bot.loop.create_task(hourly_channel_exp_flush())
    if not hasattr(bot, "_spawn_task"):
        bot._spawn_task = bot.loop.create_task(spawn_mob_loop())


@bot.event
async def on_command_error(ctx, error):
    # ignore these
    if isinstance(error, (commands.CommandNotFound, commands.CommandOnCooldown)):
        return

    # notify and log the real exception
    await ctx.send(":explosion: (something isn't right here)")
    logging.error(f"Unhandled exception in {ctx.command}: {error}", exc_info=error)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    # auto–eye-roll on every message from that specific user
    if message.channel.id in REACT_CHANNELS:
        if message.author.id == 1381277906017189898:
            try:
                await message.add_reaction("🙄")
            except Exception:
                pass  # ignore rate-limit or other errors
        elif message.author.id == 1376308591115501618:
            try:
                await message.add_reaction("🐈")
            except Exception:
                pass  # ignore rate-limit or other errors
    if message.author.bot:
        return

    # # Regex to match flexible time formats like "9:00 PM GMT", "21:00 UTC", "9pm est"
    # match = re.search(r'(\d{1,2}(:\d{2})?\s*(am|pm)?\s*(gmt|utc|est|pst|cet|cst|ist|aest|pdt|edt|bst|jst)?)', message.content, re.IGNORECASE)
    
    # if match and ":" in match:
    #     text_time = match.group(0)
    #     # Parse the time string with dateparser
    #     dt = dateparser.parse(text_time, settings={'RETURN_AS_TIMEZONE_AWARE': True})
        
    #     if dt:
    #         dt = dt - timedelta(hours=1)
    #         timestamp = int(dt.timestamp())
    #         discord_format = f"<t:{timestamp}:t>"  # Full timestamp format
    #         await message.reply(f"{text_time.strip()} → {discord_format}")
    #     else:
    #         await message.reply("Sorry, I couldn't understand the time you mentioned.")
    # 1) Ensure they exist in accountinfo
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO accountinfo (discord_id)
            VALUES ($1)
            ON CONFLICT (discord_id) DO NOTHING;
            """,
            message.author.id
        )
    user_id = message.author.id
    bucket = chat_xp_cd.get_bucket(message)
    can_gain = bucket.update_rate_limit() is None
    if can_gain:
        await u.gain_exp(conn,bot,user_id,1,message)
        
    # 0) Try to capture any active spawn in this channel
    name = message.content.strip().lower().replace(" ", "")
    now = datetime.utcnow()
    async with db_pool.acquire() as conn:
        # find the oldest not-yet-expired spawn in this channel
        spawn = await conn.fetchrow(
            """
            SELECT spawn_id, mob_name
              FROM active_spawns
             WHERE channel_id = $1
               AND expires_at > $2
             ORDER BY spawn_time
             LIMIT 1
            """,
            message.channel.id, now
        )
        if spawn and name == spawn["mob_name"].lower().replace(" ", ""):
            # Got it first!
            spawn_id = spawn["spawn_id"]
            mob_name = spawn["mob_name"]
            is_golden = (random.randint(1, 20) == 1)
            sac = False
            # 1) Add to the barn (or sacrifice if full)
            #    First ensure the player/barn rows exist:
            await u.ensure_player(conn,message.author.id)
            await conn.execute(
                "INSERT INTO barn_upgrades (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
                message.author.id
            )
            # count current barn occupancy
            occ = await conn.fetchval(
                "SELECT COALESCE(SUM(count),0) FROM barn WHERE user_id = $1",
                message.author.id
            )
            size = await conn.fetchval(
                "SELECT barn_size FROM new_players WHERE user_id = $1",
                message.author.id
            )
            if occ >= size:
                sac = True
                reward = await u.sucsac(message.channel,message.author,mob_name,is_golden,"because the barn was too full",conn)
                note = f"sacrificed for {reward} emeralds (barn is full)."
                
            elif MOBS[mob_name]["hostile"]:
                sac = True
                reward = await u.sucsac(message.channel,message.author,mob_name,is_golden,"because the mob is hostile",conn)
                note = f"this mob is not catchable so it was sacrificed for {reward} emeralds"
                
            else:

                # insert into barn with the golden flag
                await conn.execute(
                    """
                    INSERT INTO barn (user_id, mob_name, is_golden, count)
                    VALUES ($1, $2, $3, 1)
                    ON CONFLICT (user_id, mob_name, is_golden)
                    DO UPDATE SET count = barn.count + 1
                    """,
                    message.author.id, mob_name, is_golden
                )

                note = f"placed in your barn ({occ+1}/{size})."
            # 2) Delete the spawn so no one else can catch it
            await conn.execute(
                "DELETE FROM active_spawns WHERE spawn_id = $1",
                spawn_id
            )

            # look up rarity info
            rarity = MOBS[mob_name]["rarity"]
            rar_info = RARITIES[rarity]
            color    = COLOR_MAP[rar_info["colour"]]
            if sac:
                # build and send the embed
                embed = discord.Embed(
                    title=f"🏆 {message.author.display_name} caught a {'✨ Golden ' if is_golden else ''} {RARITIES[rarity]["name"]} {mob_name}!",
                    description=f"{note}",
                    color=color
                )
                embed.add_field(
                    name="Rarity",
                    value=rar_info["name"].title(),
                    inline=True
                )
                await message.channel.send(embed=embed)
                # skip further processing (so they don’t also run a command)
            return

    await bot.process_commands(message)





#############################################################################################################################################################################





@bot.command(name="give")
async def give(ctx, who: str, *, mob: str):
    await cc.c_give(ctx, who, mob)
@give.error
async def give_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send("❌ Usage: `!give <player> <mob>`")
    raise error



@bot.command(name="craft")
async def craft(ctx, *args):
    await cc.c_craft(ctx, args)
@craft.error
async def craft_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send("❌ Usage: `!craft <tool> [tier]`")
    raise error


@bot.command(name="shop")
async def shop(ctx):
    await cc.c_shop(ctx)


@bot.command(name="breed")
@commands.cooldown(5, 86400, commands.BucketType.user)  # 5 uses per day
async def breed(ctx, *, mob: str):
    await cc.c_breed(ctx,mob)
@breed.error
async def breed_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)
        hrs   = retry // 3600
        mins  = (retry % 3600) // 60
        parts = []
        if hrs:  parts.append(f"{hrs} h")
        if mins: parts.append(f"{mins} m")
        when  = " ".join(parts) or f"{retry}s"
        return await ctx.send(
            f"❌ You’ve used all 5 breeds for today. Try again in {when}."
        )
    raise error

@bot.command(name="update")
async def updates(ctx):
    role_id = 1398063505189507132
    await u.giverole(ctx,role_id,ctx.author)


@bot.command(name="buy")
async def buy(ctx, *args):
    await cc.c_buy(ctx,args)


@bot.command(name="exp", aliases=["experience", "level", "lvl"])
async def exp_cmd(ctx, *, who: str = None):
    await cc.c_exp_cmd(ctx,who)


@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard(ctx):
    await cc.c_leaderboard(ctx,bot)

@bot.command(name="givemob")
@commands.has_permissions(manage_guild=True)
async def givemob(ctx, who: str , mob_name: str, count: int = 1):
    await cc.c_givemob(ctx,who,mob_name,count)

@bot.command(name="sacrifice", aliases=["sac", "kill"])
async def sacrifice(ctx, *, mob_name: str):
    await cc.c_sac(ctx,mob_name)


@bot.command(name="bestiary",aliases =["bs","bes"])
async def bestiary(ctx, *, who: str = None):
    await cc.c_bestiary(ctx,who)

@bot.command(name="chop")
@commands.cooldown(1, 60, commands.BucketType.user)  # 1 use per 60s per user
async def chop(ctx):
    await cc.c_chop(ctx)
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


@bot.command(name="mine")
@commands.cooldown(1, 120, commands.BucketType.user)
async def mine(ctx):
    await cc.c_mine(ctx)
@mine.error
async def mine_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)
        await ctx.send(f"You’re too tired to mine again now! Try again in {retry}s.")
        return
    raise error



@bot.command(name="farm")
@commands.cooldown(1, 120, commands.BucketType.user)
async def farm(ctx):
    await cc.c_farm(ctx)
@farm.error
async def farm_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)
        await ctx.send(f"You’re too tired to farm again now! Try again in {retry}s.")
        return
    raise error


@bot.command(name="inv", aliases=["inventory"])
async def inv(ctx, *, who: str = None):
    await cc.c_inv(ctx,who)

@bot.command(name="barn")
async def barn(ctx, *, who: str = None):
    await cc.c_barn(ctx, who)
    
@bot.command(name="upbarn")
async def upbarn(ctx):
    await cc.c_upbarn(ctx)


def pixelate(img: Image.Image, size: int) -> Image.Image:
    """Downscale to (size×size) then upscale back, nearest-neighbor."""
    # shrink
    small = img.resize((size, size), resample=Image.NEAREST)
    # blow back up to original dims
    return small.resize(img.size, Image.NEAREST)

def zoom_frame_at(src: Image.Image, zoom_frac: float, center: tuple[float,float]) -> Image.Image:
    """
    Crop src to a zoom_frac× window centered at `center` (fractions 0–1),
    then scale back up to full size.
    """
    w, h = src.size
    f = max(0.01, min(zoom_frac, 1.0))
    cw, ch = int(w * f), int(h * f)

    # compute top-left so the window is centered on (cx, cy)
    cx, cy = center
    left = int(cx * w - cw / 2)
    top  = int(cy * h - ch / 2)
    # clamp to image bounds
    left = max(0, min(left, w - cw))
    top  = max(0, min(top, h - ch))

    crop = src.crop((left, top, left + cw, top + ch))
    return crop.resize((w, h), Image.NEAREST)

async def watch_spawn_expiry(spawn_id, channel_id, message_id, mob_name, expires_at):
    # Sleep until the exact expiry time
    now = datetime.utcnow()
    delay = (expires_at - now).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)

    # After sleeping, check if it's still uncaught
    async with db_pool.acquire() as conn:
        still_there = await conn.fetchval(
            "SELECT 1 FROM active_spawns WHERE spawn_id = $1", spawn_id
        )
        if not still_there:
            return  # someone caught it already

        # Remove the DB entry
        await conn.execute(
            "DELETE FROM active_spawns WHERE spawn_id = $1", spawn_id
        )

    # Try to delete the original image message
    channel = bot.get_channel(channel_id)
    if channel:
        try:
            orig = await channel.fetch_message(message_id)
            await orig.delete()
        except discord.NotFound:
            pass

        # Announce the escape
        await channel.send(f"**{mob_name}** escaped, maybe next time")

async def spawn_mob_loop():
    await bot.wait_until_ready()
        # before your loop, compute these once:
    mob_names = list(MOBS.keys())
    rarities = [MOBS[name]["rarity"] for name in mob_names]
    max_r = max(rarities)

    # weight = (max_r + 1) – rarity  → commons get highest weight
    weights = [(2**(max_r + 1-r)) for r in rarities]
    while True:
        try:
            # wait 4–20 minutes
            await asyncio.sleep(random.randint(2*60, 5*60))

            # pick channel & mob
            chan = bot.get_channel(random.choice(SPAWN_CHANNEL_IDS))
            mob = random.choices(mob_names, weights=weights, k=1)[0]
            mob_path = f"assets/mobs/{mob}"
            try:
                if os.path.isdir(mob_path):
                    # It's a folder — pick a random image file inside
                    image_files = [f for f in os.listdir(mob_path) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
                    if not image_files:
                        raise FileNotFoundError("No image files in directory")
                    selected_image = random.choice(image_files)
                    src = Image.open(os.path.join(mob_path, selected_image)).convert("RGBA")
                else:
                    # It's a single image
                    src = Image.open(f"{mob_path}.png").convert("RGBA")
            except FileNotFoundError:
                # fallback to text if image missing
                await chan.send(f"A wild **{mob}** appeared! (no image found)")
            pix = (random.randint(1, 4) == 1)
            alpha = src.split()[-1]           # get the alpha channel
            bbox  = alpha.getbbox()           # returns (left, top, right, bottom) of non-zero pixels

            if bbox:
                left, top, right, bottom = bbox
                # now pick a random center within that box (in pixel coords)
                cx_px = random.randint(left, right)
                cy_px = random.randint(top,  bottom)
                # convert to fractions 0–1
                w, h  = src.size
                center = (cx_px / w, cy_px / h)
            else:
                # fallback to true random if no alpha info
                logging.info("No bbox found")
                center = (random.uniform(0.1, 0.9), random.uniform(0.1, 0.9))
            # send initial 1×1 pixel frame
            frame_sizes = [1, 2, 4, 8, 16, src.size[0]]  # final = full res width
            zoom_levels = [0.01, 0.05, 0.1, 0.2, 0.4, 1.0]


            if pix:
                levels     = frame_sizes
                make_frame = lambda lvl: pixelate(src, lvl)
            else:
                levels = zoom_levels
                # now every frame uses that same center
                make_frame = lambda lvl: zoom_frame_at(src, lvl, center)

            # send first frame
            buf = io.BytesIO()
            make_frame(levels[0]).save(buf, format="PNG")
            buf.seek(0)
            msg = await chan.send(
                "A mob is appearing, say its name to catch it",
                file=discord.File(buf, "spawn.png")
            )
           
            expires = datetime.utcnow() + timedelta(seconds=RARITIES[MOBS[mob]["rarity"]]["stay"])  # give players 5m to catch
            async with db_pool.acquire() as conn:
                record = await conn.fetchrow(
                """
                INSERT INTO active_spawns
                (channel_id, mob_name, message_id, revealed, spawn_time, expires_at)
                VALUES ($1,$2,$3,0,$4,$5)
                RETURNING spawn_id
                """,
                chan.id, mob, msg.id, datetime.utcnow(), expires
            )
            # step through each larger frame
            for lvl in levels[1:]:
                await asyncio.sleep(15)
                buf = io.BytesIO()
                make_frame(lvl).save(buf, format="PNG")
                buf.seek(0)
                await msg.edit(
                    content=f"A mob is appearing, say its name to catch it",
                    attachments=[discord.File(buf, "spawn.png")])
            spawn_id = record["spawn_id"]
            bot.loop.create_task(
                watch_spawn_expiry(spawn_id=spawn_id,  # you'll fetch this below
                                channel_id=chan.id,
                                message_id=msg.id,
                                mob_name=mob,
                                expires_at=expires)
            )
        
        except Exception:
            await asyncio.sleep(60)
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
