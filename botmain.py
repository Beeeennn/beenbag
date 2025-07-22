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
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import string
import secrets
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
RARITIES ={
    1:{"colour":"white","name":"common","wheat":5,"emeralds":1,"stay":180},
    2:{"colour":"green","name":"uncommon","wheat":10,"emeralds":2,"stay":160},
    3:{"colour":"blue","name":"rare","wheat":15,"emeralds":3,"stay":120},
    4:{"colour":"purple","name":"epic","wheat":25,"emeralds":5,"stay":60},
    5:{"colour":"red","name":"legendary","wheat":40,"emeralds":10,"stay":40}
}
COLOR_MAP = {
    "white":  discord.Color.light_grey(),
    "green":  discord.Color.green(),
    "blue":   discord.Color.blue(),
    "purple": discord.Color.purple(),
    "red":    discord.Color.red(),
}
SWORDS = {
    None:0,
    "wood":0,
    "stone":0,
    "iron":1,
    "gold":2,
    "diamond":4
    }
# List of your mob names, matching files in assets/ (e.g. assets/Zombie.png) -----------------------------------------------------------------------------------------------------------
MOBS = {"Zombie":{"rarity":1,"hostile":True},
        "Enderman":{"rarity":3,"hostile":True},
        "Cow":{"rarity":1,"hostile":False},
        "Chicken":{"rarity":1,"hostile":False},
        "Armadillo":{"rarity":2,"hostile":False},
        "Cod":{"rarity":1,"hostile":True},
        "Axolotl":{"rarity":2,"hostile":False},
        "Dolphin":{"rarity":2,"hostile":False},
        "Camel":{"rarity":2,"hostile":False},
        "Donkey":{"rarity":1,"hostile":False},
        "Frog":{"rarity":3,"hostile":False},
        "Fox":{"rarity":2,"hostile":False},
        "Snow Fox":{"rarity":4,"hostile":False},
        "Glow Squid":{"rarity":2,"hostile":True},
        "Goat":{"rarity":3,"hostile":False},
        "Hoglin":{"rarity":4,"hostile":True},
        "Horse":{"rarity":1,"hostile":False},
        "Llama":{"rarity":2,"hostile":False},
        "Mooshroom":{"rarity":4,"hostile":False},
        "Ocelot":{"rarity":3,"hostile":False},
        "Panda":{"rarity":3,"hostile":False},
        "Brown Panda":{"rarity":4,"hostile":False},
        "Parrot":{"rarity":2,"hostile":False},
        "Pig":{"rarity":1,"hostile":False},
        "Sheep":{"rarity":1,"hostile":False},
        "Polar Bear":{"rarity":1,"hostile":True},
        "Pufferfish":{"rarity":2,"hostile":True},
        "Salmon":{"rarity":1,"hostile":True},
        "Squid":{"rarity":1,"hostile":True},
        "Strider":{"rarity":2,"hostile":False},
        "Tropical Fish":{"rarity":3,"hostile":True},
        "Turtle":{"rarity":1,"hostile":False},
        "Wolf":{"rarity":1,"hostile":False},
        "Cat":{"rarity":1,"hostile":False},
        "Allay":{"rarity":3,"hostile":False},
        "Bat":{"rarity":2,"hostile":True},
        "Mule":{"rarity":1,"hostile":False},
        "Skeleton Horse":{"rarity":4,"hostile":True},
        "Sniffer":{"rarity":5,"hostile":False},
        "Snow Golem":{"rarity":4,"hostile":True},
        "Tadpole":{"rarity":1,"hostile":False},
        "Bee":{"rarity":1,"hostile":False},
        "Cave Spider":{"rarity":1,"hostile":True},
        "Drowned":{"rarity":1,"hostile":True},
        "Iron Golem":{"rarity":3,"hostile":True},
        "Piglin":{"rarity":2,"hostile":True},
        "Spider":{"rarity":1,"hostile":True},
        "Zombie Pigman":{"rarity":1,"hostile":True},
        "Sea Pickle":{"rarity":5,"hostile":False},
        "Blaze":{"rarity":2,"hostile":True},
        "Bogged":{"rarity":1,"hostile":True},
        "Breeze":{"rarity":3,"hostile":True},
        "Creaking":{"rarity":4,"hostile":True},
        "Crepper":{"rarity":1,"hostile":True},
        "Elder Guardian":{"rarity":5,"hostile":True},
        "Ender Dragon":{"rarity":5,"hostile":True},
        "Evoker":{"rarity":3,"hostile":True},
        "Ghast":{"rarity":1,"hostile":True},
        "Guardian":{"rarity":2,"hostile":True},
        "Husk":{"rarity":1,"hostile":True},
        "Magma Cube":{"rarity":1,"hostile":True},
        "Phantom":{"rarity":1,"hostile":True},
        "Pillager":{"rarity":1,"hostile":True},
        "Ravager":{"rarity":2,"hostile":True},
        "Shulker":{"rarity":2,"hostile":True},
        "Silverfish":{"rarity":2,"hostile":True},
        "Skeleton":{"rarity":1,"hostile":True},
        "Slime":{"rarity":1,"hostile":True},
        "Stray":{"rarity":2,"hostile":True},
        "Vex":{"rarity":3,"hostile":True},
        "Warden":{"rarity":5,"hostile":True},
        "Witch":{"rarity":1,"hostile":True},
        "Wither":{"rarity":5,"hostile":True},
        "Wither Skeleton":{"rarity":2,"hostile":True},
        "Zoglin":{"rarity":3,"hostile":True},
        "Zombie Villager":{"rarity":1,"hostile":True},
        "Copper Golem":{"rarity":3,"hostile":True},
        "Happy Ghast":{"rarity":3,"hostile":True}
        }

# Define an ordering for tiers so we can pick the best one
TIER_ORDER = ["wood", "stone", "iron", "gold", "diamond"]
# cumulative exp required for each level
LEVEL_EXP = {
    1:   7,    2:  16,   3:  27,   4:  40,   5:   55,
    6:  72,    7:  91,   8: 112,   9: 135,  10:  160,
    11: 187,   12: 216,  13: 247,  14: 280,  15:  315,
    16: 352,   17: 394,  18: 441,  19: 493,  20:  550,
    21: 612,   22: 679,  23: 751,  24: 828,  25:  910,
    26: 997,   27:1089,  28:1186, 29:1288, 30: 1395,
    31:1507,   32:1628,  33:1758, 34:1897, 35: 2045,
    36:2202,   37:2368,  38:2543, 39:2727, 40: 2920,
    41:3122,   42:3333,  43:3553, 44:3782, 45: 4020,
    46:4267,   47:4523,  48:4788, 49:5062, 50: 5345,
    51:5637,   52:5938,  53:6248, 54:6567, 55: 6895,
    56:7232,   57:7578,  58:7933, 59:8297, 60:8670
}

# which levels should get roles
MILESTONE_ROLES = [10,20,30,40,50]

ROLE_NAMES = {
    10:"Iron",
    20:"Gold",
    30:"Diamond",
    40:"Netherite"
}
# We'll hold an asyncpg pool here
db_pool: asyncpg.Pool = None
def make_link_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

async def daily_level_decay():
    tz = ZoneInfo("Europe/London")
    await bot.wait_until_ready()
    while True:
        # compute seconds until next midnight in London
        now = datetime.now(tz)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        delay = (tomorrow - now).total_seconds()
        await asyncio.sleep(delay)

        # 1) Demote everyone by one level
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT discord_id, exp FROM accountinfo")
        for record in rows:
            user_id = record["discord_id"]
            old_exp = record["exp"]
            old_lvl = get_level_from_exp(old_exp)
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
async def safe_dm(user: discord.User, content: str, *, retry: int = 3):
    """
    Send user a DM, reusing their DMChannel and retrying on the 40003 error.
    Returns True on success, False on permanent failure.
    """
    # 1) Get or create the DM channel
    dm = user.dm_channel
    if dm is None:
        dm = await user.create_dm()

    # 2) Attempt to send, with retries if rate-limited
    for attempt in range(retry):
        try:
            await dm.send(content)
            return True
        except discord.HTTPException as e:
            # 40003 = opening DMs too fast
            if e.code == 40003 and attempt < retry - 1:
                await asyncio.sleep(1 + attempt)  # back-off
                continue
            # any other error or no more retries
            break

    return False
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

# HTTP endpoints
async def handle_ping(request):
    return web.Response(text="pong")

@bot.command(name="linkyt")
async def linkyt(ctx, *, channel_name: str):
    """
    Generate a one-time code to link your YouTube channel.
    Usage: !linkyt <your YouTube channel name>
    """
    user_id = ctx.author.id
    code = make_link_code(8)
    expires = datetime.utcnow() + timedelta(hours=3)
    channel_name = channel_name.removeprefix("@")
    # store (or update) in pending_links
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pending_links
                (discord_id, yt_channel_id, code, expires_at)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (discord_id) DO UPDATE
              SET yt_channel_id = EXCLUDED.yt_channel_id,
                  code            = EXCLUDED.code,
                  expires_at      = EXCLUDED.expires_at;
            """,
            user_id, channel_name.lower(), code, expires
        )

    # DM them the code

    sent = await safe_dm(
    ctx.author,
    f"🔗 **YouTube Link Code** 🔗\n"
    f"Channel: **{channel_name}**\n"
    f"Your code is: `{code}`\n\n"
    "Please type `!link <code>` in one of my **livestreams** within 3 hours to complete linking."
        
    )
    if sent:
        await ctx.send(f"{ctx.author.mention}, check your DMs for the code!")
    else:
        await ctx.send(
            f"{ctx.author.mention}, I couldn’t DM you right now—please try again later."
        )

    try:
        await ctx.author.send(
            f"🔗 **YouTube Link Code** 🔗\n"
            f"Channel: **{channel_name}**\n"
            f"Your code is: `{code}`\n\n"
            "Please type `!link <code>` in one of my livestreams within 3 hours to complete linking."
        )
        await ctx.send(f"{ctx.author.mention}, I’ve DMed you your linking code!")
    except discord.Forbidden:
        await ctx.send(
            f"{ctx.author.mention} I couldn’t DM you—please enable DMs from server members and try again (Content and social -> Social Permissions -> Direct Messages) You can turn it back off after.")


# IDs of channels where !yt is permitted
LINK_CHANNELS = [1395577501916336128, 1396194783713824800]

@bot.command(name="yt")
async def yt(ctx, member: discord.Member = None):
    """
    Show the YouTube channel linked to a user.
    Usage:
      !yt             → your own channel
      !yt @Someone    → their channel
    """
    # 0) Restrict to LINK_CHANNELS
    if ctx.channel.id not in LINK_CHANNELS:
        return await ctx.send("❌ You can’t do that here.")

    # 1) Determine whose data to look up
    target  = member or ctx.author
    user_id = target.id

    # 2) Fetch from accountinfo
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT yt_channel_name, yt_channel_id
              FROM accountinfo
             WHERE discord_id = $1
            """,
            user_id
        )

    # 3) No link yet?
    if not row or (not row["yt_channel_name"] and not row["yt_channel_id"]):
        if target == ctx.author:
            return await ctx.send(
                "You haven’t linked a YouTube channel! Use `!linkyt <channel name>`."
            )
        else:
            return await ctx.send(f"{target.display_name} hasn’t linked YT yet.")

    # 4) Build URL
    name = row["yt_channel_name"]
    cid  = row["yt_channel_id"]
    if cid:
        url = f"https://www.youtube.com/channel/{cid}"
    else:
        url = f"https://www.youtube.com/c/{name.replace(' ', '')}"

    # 5) Send embed
    embed = discord.Embed(
        title=f"{target.display_name}'s YouTube",
        url=url, color=discord.Color.red()
    )
    embed.add_field(name="Channel Name", value=name or "–", inline=True)
    embed.add_field(name="Link", value=f"[Watch on YouTube]({url})", inline=True)
    await ctx.send(embed=embed)
async def hourly_channel_exp_flush():
    await bot.wait_until_ready()
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
            uid = await conn.fetchval("""
                                      SELECT discord_id
                                      FROM accountinfo
                                      WHERE yt_channel_id = $1                                      
                                      """,record["channel_id"])
            xp  = record["exp"]
            # pass None for ctx so gain_exp just does DB+roles without messaging
            await gain_exp(uid, xp, None)
        # wait one hour
        await asyncio.sleep(3600)

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
async def ensure_player(user_id):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO players (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
            user_id
        )

def get_level_from_exp(exp: int) -> int:
    # find the highest level whose threshold is <= exp
    lvl = 0
    for level, req in LEVEL_EXP.items():
        if exp >= req and level > lvl:
            lvl = level
    return lvl
ANNOUNCE_CHANNEL_ID = 1396194783713824800
# 2exp for chat every half hour (100/day), streams = 1exp every 2 minutes (60-150)/day, shop 1 emerald = 1 exp
async def gain_exp(user_id: int, exp_gain: int, message: discord.Message = None):
    # 1) Update experience in DB
    async with db_pool.acquire() as conn:
        old_exp = await conn.fetchval(
            "SELECT experience FROM accountinfo WHERE discord_id = $1", user_id
        ) or 0
        new_exp = old_exp + exp_gain
        await conn.execute(
            """
            UPDATE accountinfo
               SET experience = $1,
                   overallexp   = overallexp + $2
             WHERE discord_id = $3
            """,
            new_exp, exp_gain, user_id
        )

    # 2) Compute old & new levels
    old_lvl = get_level_from_exp(old_exp)
    new_lvl = get_level_from_exp(new_exp)

    # 3) If leveled up, adjust roles
    if new_lvl > old_lvl:
        # find the announce channel
        announce_ch = bot.get_channel(ANNOUNCE_CHANNEL_ID)

        # you need a guild/member to add/remove roles
        # prefer the guild from `message`, else search all bot.guilds
        if message and message.guild:
            guild = message.guild
        else:
            # fallback: pick the first guild where the user is a member
            guild = None
            for g in bot.guilds:
                if g.get_member(user_id):
                    guild = g
                    break

        member = guild.get_member(user_id) if guild else None

        # 3a) remove previous milestone role (if any)
        for lvl in MILESTONE_ROLES:
            if lvl == new_lvl:
                prev = lvl - 10
                if prev in ROLE_NAMES and member:
                    old_role = discord.utils.get(guild.roles, name=ROLE_NAMES[prev])
                    if old_role in member.roles:
                        await member.remove_roles(old_role, reason="Leveled up")

        # 3b) add new milestone role (if it's one)
        if new_lvl in MILESTONE_ROLES and member:
            new_role = discord.utils.get(guild.roles, name=ROLE_NAMES[new_lvl])
            if new_role:
                await member.add_roles(new_role, reason="Leveled up")

        # 4) Craft the announcement text
        mention = member.mention if member else f"<@{user_id}>"
        text = f"🎉 {mention} leveled up to **Level {new_lvl}**!"

        # 5) Send it in the announce channel (fallback to message.channel)
        if announce_ch:
            await announce_ch.send(text)
        elif message:
            await message.channel.send(text)
@bot.event
async def on_message(message):
    if message.author.bot:
        return

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
        await gain_exp(user_id, 1,message)
        
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
            # 1) Add to the barn (or sacrifice if full)
            #    First ensure the player/barn rows exist:
            await conn.execute(
                "INSERT INTO players (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
                message.author.id
            )
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
                "SELECT barn_size FROM players WHERE user_id = $1",
                message.author.id
            )
            if occ >= size:
                #no room → sacrifice for exp
                rarity = MOBS[mob_name]["rarity"]
                rar_info = RARITIES[rarity]
                reward  = rar_info["emeralds"]
                if is_golden:
                    reward *= 2
                swords = await conn.fetch(
                    """
                    SELECT tier, uses_left
                    FROM tools
                    WHERE user_id = $1
                    AND tool_name = 'sword'
                    AND uses_left > 0
                    """,
                    user_id
                )
                owned_tiers = {r["tier"] for r in swords}
                best_tier = None
                for tier in reversed(TIER_ORDER):
                    if tier in owned_tiers:
                        best_tier = tier
                        break
                
                num = SWORDS[best_tier]
                reward += num
                await conn.execute(
                    """
                    UPDATE tools
                    SET uses_left = uses_left - 1
                    WHERE user_id = $1
                    AND tool_name = 'sword'
                    AND tier = $2
                    AND uses_left > 0
                    """,
                    user_id, best_tier
                )

                await conn.execute(
                    "UPDATE accountinfo SET emeralds = emeralds + $1 WHERE discord_id = $2",
                    reward, message.author.id
                )
                 # record in sacrifice_history
                await conn.execute(
                    """
                    INSERT INTO sacrifice_history
                    (discord_id, mob_name, is_golden, rarity)
                    VALUES ($1,$2,$3,$4)
                    """,
                    user_id, mob_name, is_golden, rarity)
                note = f"sacrificed for {reward} emeralds (barn is full)."
                
            elif MOBS[mob_name]["hostile"]:
                #no room → sacrifice for exp
                rarity = MOBS[mob_name]["rarity"]
                rar_info = RARITIES[rarity]
                reward  = rar_info["emeralds"]
                if is_golden:
                    reward *= 2

                swords = await conn.fetch(
                    """
                    SELECT tier, uses_left
                    FROM tools
                    WHERE user_id = $1
                    AND tool_name = 'sword'
                    AND uses_left > 0
                    """,
                    user_id
                )
                owned_tiers = {r["tier"] for r in swords}
                best_tier = None
                for tier in reversed(TIER_ORDER):
                    if tier in owned_tiers:
                        best_tier = tier
                        break
                
                num = SWORDS[best_tier]
                reward += num
                await conn.execute(
                    """
                    UPDATE tools
                    SET uses_left = uses_left - 1
                    WHERE user_id = $1
                    AND tool_name = 'sword'
                    AND tier = $2
                    AND uses_left > 0
                    """,
                    user_id, best_tier
                )
                await conn.execute(
                    "UPDATE accountinfo SET emeralds = emeralds + $1 WHERE discord_id = $2",
                    reward, message.author.id
                )
                # record in sacrifice_history
                await conn.execute(
                    """
                    INSERT INTO sacrifice_history
                    (discord_id, mob_name, is_golden, rarity)
                    VALUES ($1,$2,$3,$4)
                    """,
                    user_id, mob_name, is_golden, rarity)
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
CRAFT_RECIPES = {
    # tool        tier      wood   ore_count  ore_column    uses
    ("pickaxe",   "wood"):    (4,    0,      None, 10),
    ("pickaxe",   "stone"):   (1,    3,      "cobblestone", 10),
    ("pickaxe",   "iron"):    (1,    3,      "iron",        10),
    ("pickaxe",   "gold"):    (1,    3,      "gold",        10),
    ("pickaxe",   "diamond"): (1,    3,      "diamond",     10),

    ("hoe",       "wood"):    (4,    0,      None, 10),
    ("hoe",       "stone"):   (1,    2,      "cobblestone", 10),
    ("hoe",       "iron"):    (1,    2,      "iron",        10),
    ("hoe",       "gold"):    (1,    2,      "gold",        10),
    ("hoe",       "diamond"): (1,    2,      "diamond",     10),

    ("fishing_rod", "wood"):  (3,    0,      None,          10),
    ("fishing_rod", "stone"): (3,    0,      None,          10),
    ("fishing_rod", "iron"):  (3,    0,      None,          10),
    ("fishing_rod", "gold"):  (3,    0,      None,          10),
    ("fishing_rod", "diamond"):(3,   0,      None,          10),

    ("sword",     "iron"):    (1,    2,      "iron",        3),
    ("sword",     "gold"):    (1,    2,      "gold",        3),
    ("sword",     "diamond"): (1,    2,      "diamond",     3),

    ("axe",     "wood"):    (4,    0,      None, 5),
    ("axe",     "stone"):   (1,    3,      "cobblestone", 10),
    ("axe",     "iron"):    (1,    3,      "iron",        5),
    ("axe",     "gold"):    (1,    3,      "gold",        10),
    ("axe",     "diamond"): (1,    3,      "diamond",     10),
}

@bot.command(name="craft")
async def craft(ctx, *args):
    """
    Usage: !craft <tool> <tier> 
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
        tool = "fishing_rod"

    if tier is None:
        return await ctx.send("❌ You must specify a tier for that tool.")

    key = (tool, tier)
    if key not in CRAFT_RECIPES:
        return await ctx.send("❌ Invalid recipe. Try `!craft pickaxe iron` or `!craft fishing rod`.")

    wood_cost, ore_cost, ore_col, uses = CRAFT_RECIPES[key]

    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(ctx.author.id)
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

@bot.command(name="shop")
async def shop(ctx):
    """List all items you can buy in the shop."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT item_id, name, description, price_emeralds, purchase_limit
              FROM shop_items
             ORDER BY item_id
        """)
    embed = discord.Embed(title="🏪 Shop", color=discord.Color.gold())
    for r in rows:
        limit = "unlimited" if r["purchase_limit"] is None else str(r["purchase_limit"])
        embed.add_field(
            name=f"{r['name']} — {r['price_emeralds']} 💠",
            value=f"{r['description']}\nLimit: {limit} per 24 h",
            inline=False
        )
    await ctx.send(embed=embed)
@bot.command(name="buy")
async def buy(ctx, *args):
    """
    Purchase one or more of an item.
    Usage:
      !buy <item name> [quantity]
    Examples:
      !buy Exp\ Bottle 5
      !buy exp 100
    """
    if not args:
        return await ctx.send("❌ Usage: `!buy <item name> [quantity]`")

    # 1) Parse quantity if last arg is an integer
    try:
        qty = int(args[-1])
        name_parts = args[:-1]
    except ValueError:
        qty = 1
        name_parts = args

    if qty < 1:
        return await ctx.send("❌ Quantity must be at least 1.")

    raw_name = " ".join(name_parts).strip().lower()

    # allow "exp" shortcut for "Exp Bottle"
    if raw_name in ("exp", "experience"):
        lookup_name = "exp bottle"
    else:
        lookup_name = raw_name

    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        # 2) Look up the item
        item = await conn.fetchrow(
            """
            SELECT item_id, name, price_emeralds, purchase_limit
              FROM shop_items
             WHERE LOWER(name) = $1
            """,
            lookup_name
        )
        if not item:
            return await ctx.send(f"❌ No shop item named **{raw_name}**.")

        item_id      = item["item_id"]
        display_name = item["name"]
        cost_each    = item["price_emeralds"]
        limit        = item["purchase_limit"]  # None = unlimited

        total_cost = cost_each * qty

        # 3) Check emerald balance
        have = await conn.fetchval(
            "SELECT emeralds FROM accountinfo WHERE discord_id = $1",
            user_id
        ) or 0
        if have < total_cost:
            return await ctx.send(
                f"❌ You need {total_cost} 💠 but only have {have}."
            )

        # 4) Enforce daily limit (for Exp Bottle only, or any limited item)
        if limit is not None:
            since = datetime.utcnow() - timedelta(hours=24)
            bought = await conn.fetchval(
                """
                SELECT COUNT(*) FROM purchase_history
                 WHERE user_id = $1
                   AND item_id = $2
                   AND purchased_at > $3
                """,
                user_id, item_id, since
            )
            if bought + qty > limit:
                return await ctx.send(
                    f"❌ You can only buy {limit}/{limit} **{display_name}** per 24 h."
                )

        # 5) Deduct emeralds
        await conn.execute(
            "UPDATE accountinfo SET emeralds = emeralds - $1 WHERE discord_id = $2",
            total_cost, user_id
        )
        # 6) Log each purchase for history
        for _ in range(qty):
            await conn.execute(
                "INSERT INTO purchase_history (user_id, item_id) VALUES ($1,$2)",
                user_id, item_id
            )
        # 7) Update your cumulative purchases (e.g. boss tickets)
        await conn.execute("""
            INSERT INTO shop_purchases (user_id,item_id,quantity)
            VALUES ($1,$2,$3)
            ON CONFLICT (user_id,item_id) DO UPDATE
              SET quantity = shop_purchases.quantity + $3
        """, user_id, item_id, qty)

    # 8) Grant the effect
    if display_name == "Exp Bottle":
        # award qty EXP at once
        await gain_exp(user_id, qty, ctx)
        await ctx.send(f"✅ Spent {total_cost} 💠 for **{qty} EXP**!")
    elif display_name == "Boss Mob Ticket":
        await ctx.send(
            f"✅ You bought **{qty} Boss Mob Ticket{'s' if qty!=1 else ''}**! "
            "Use `!use_ticket` to redeem."
        )
    elif display_name == "Mystery Animal":
        got = []
        async with db_pool.acquire() as conn:
            for _ in range(qty):
                mob = random.choice([m for m,v in MOBS.items() if not v["hostile"]])
                got.append(mob)
                await conn.execute(
                    """
                    INSERT INTO barn (user_id,mob_name,count)
                    VALUES ($1,$2,1)
                    ON CONFLICT (user_id,mob_name) DO UPDATE
                      SET count = barn.count + 1
                    """, user_id, mob
                )
        # summarize what they got
        summary = {}
        for m in got:
            summary[m] = summary.get(m, 0) + 1
        lines = [f"**{cnt}× {name}**" for name,cnt in summary.items()]
        await ctx.send(f"✅ Mystery pack delivered:\n" + "\n".join(lines))
    elif display_name == "RICH Role":
        role = discord.utils.get(ctx.guild.roles, name="RICH")
        if role:
            await ctx.author.add_roles(role, reason="Purchased RICH role")
            await ctx.send("✅ You are now **RICH**!")
        else:
            await ctx.send("❌ Could not find a `RICH` role on this server.")
    else:
        await ctx.send(f"✅ You bought **{qty}× {display_name}** for {total_cost} 💠!")
@bot.command(name="exp", aliases=["experience", "level", "lvl"])
async def exp_cmd(ctx):
    """Show your current level and progress toward the next level."""
    user_id = ctx.author.id

    # 1) Fetch their total exp from accountinfo
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT experience FROM accountinfo WHERE discord_id = $1",
            user_id
        )
    total_exp = row["experience"] if row else 0

    # 2) Compute current & next levels
    current_level = get_level_from_exp(total_exp)
    max_level     = max(LEVEL_EXP.keys())

    if current_level < max_level:
        next_level = current_level + 1
        req_current = LEVEL_EXP.get(current_level, 0)
        req_next    = LEVEL_EXP[next_level]
        exp_into    = total_exp - req_current
        exp_needed  = req_next - total_exp
        # progress percentage
        pct = int(exp_into / (req_next - req_current) * 100)
    else:
        next_level = None

    # 3) Build an embed
    embed = discord.Embed(
        title=f"{ctx.author.display_name}'s Progress",
        color=discord.Color.gold()
    )
    embed.add_field(name="🎖️ Level", value=str(current_level), inline=True)
    embed.add_field(name="💯 Total EXP", value=str(total_exp), inline=True)

    if next_level:
        embed.add_field(
            name=f"➡️ EXP to Level {next_level}",
            value=f"{exp_needed} EXP ({pct}% there)",
            inline=False
        )
    else:
        embed.add_field(
            name="🏆 Max Level",
            value="You have reached the highest level!",
            inline=False
        )

    await ctx.send(embed=embed)
@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard(ctx):
    """Show the top 10 users by overall EXP, plus your own rank."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        # 1) Top 10 overall EXP
        top_rows = await conn.fetch(
            """
            SELECT discord_id, overallexp
              FROM accountinfo
             ORDER BY overallexp DESC
             LIMIT 10
            """
        )
        # 2) Get invoking user’s total EXP
        user_row = await conn.fetchrow(
            "SELECT overallexp FROM accountinfo WHERE discord_id = $1",
            user_id
        )
        user_exp = user_row["overallexp"] if user_row else 0

        # 3) Compute their rank (1-based)
        higher_count = await conn.fetchval(
            "SELECT COUNT(*) FROM accountinfo WHERE overallexp > $1",
            user_exp
        )
        user_rank = higher_count + 1

    # 4) Build the embed
    embed = discord.Embed(
        title="🌟 Overall EXP Leaderboard",
        color=discord.Color.gold()
    )

    lines = []
    pos = 1
    for record in top_rows:
        uid  = record["discord_id"]
        exp  = record["overallexp"]
        # Try to get a guild Member for nickname, else fetch a User
        member = ctx.guild.get_member(uid)
        if member:
            name = member.display_name
        else:
            try:
                user = await bot.fetch_user(uid)
                name = f"{user.name}#{user.discriminator}"
            except:
                name = f"<Unknown {uid}>"
        lines.append(f"**#{pos}** {name} — {exp} EXP")
        pos += 1

    embed.description = "\n".join(lines)
    # 5) Add your own position
    embed.add_field(
        name="Your Position",
        value=f"#{user_rank} — {user_exp} EXP",
        inline=False
    )

    await ctx.send(embed=embed)

@bot.command(name="sacrifice", aliases=["sac", "kill"])
async def sacrifice(ctx, *, mob_name: str):
    """
    Sacrifice one mob from your barn for emeralds based on rarity.
    Usage: !sacrifice <mob name>
    """
    user_id = ctx.author.id
    key = mob_name.title()

    # validate mob
    if key not in MOBS:
        return await ctx.send(f"❌ I don’t recognize **{mob_name}**.")

    rarity = MOBS[key]["rarity"]
    rar_info = RARITIES[rarity]
    reward  = rar_info["emeralds"]
    color   = COLOR_MAP[rar_info["colour"]]

    async with db_pool.acquire() as conn:
        # check barn
        rec = await conn.fetchrow(
            """
            SELECT count, is_golden
              FROM barn
             WHERE user_id=$1 AND mob_name=$2
             ORDER BY is_golden DESC
             LIMIT 1
            """,
            user_id, key
        )

        if not rec:
            return await ctx.send(f"❌ You have no **{key}** to sacrifice.")
        have     = rec["count"]
        is_gold  = rec["is_golden"]
        if have > 1:
            await conn.execute(
                """
                UPDATE barn
                   SET count = count - 1
                 WHERE user_id=$1 AND mob_name=$2 AND is_golden=$3
                """,
                user_id, key, is_gold
            )
        else:
            await conn.execute(
                """
                DELETE FROM barn
                 WHERE user_id=$1 AND mob_name=$2 AND is_golden=$3
                """,
                user_id, key, is_gold
            )

        # remove one
        swords = await conn.fetch(
            """
            SELECT tier, uses_left
              FROM tools
             WHERE user_id = $1
               AND tool_name = 'sword'
               AND uses_left > 0
            """,
            user_id
        )
        owned_tiers = {r["tier"] for r in swords}
        best_tier = None
        for tier in reversed(TIER_ORDER):
            if tier in owned_tiers:
                best_tier = tier
                break
        if is_gold:
            reward*=2
        num = SWORDS[best_tier]
        reward += num
        await conn.execute(
            """
            UPDATE tools
               SET uses_left = uses_left - 1
             WHERE user_id = $1
               AND tool_name = 'sword'
               AND tier = $2
               AND uses_left > 0
            """,
            user_id, best_tier
        )
        if have > 1:
            await conn.execute(
                "UPDATE barn SET count = count - 1 WHERE user_id=$1 AND mob_name=$2",
                user_id, key
            )
        else:
            await conn.execute(
                "DELETE FROM barn WHERE user_id=$1 AND mob_name=$2",
                user_id, key
            )
        # grant emeralds
        await conn.execute(
            "UPDATE accountinfo SET emeralds = emeralds + $1 WHERE discord_id = $2",
            reward, user_id
        )
        # record in sacrifice_history
        await conn.execute(
            """
            INSERT INTO sacrifice_history
               (discord_id, mob_name, is_golden, rarity)
            VALUES ($1,$2,$3,$4)
            """,
            user_id, key, is_gold, rarity
        )

    # send embed
    embed = discord.Embed(
        title=f"🗡️ {ctx.author.display_name} sacrificed a {'✨ Golden ' if is_gold else ''} {key}",
        description=f"You gained 💠 **{reward} Emerald{'s' if reward!=1 else ''}**!",
        color=color
    )
    embed.add_field(name="Rarity", value=rar_info["name"].title(), inline=True)
    if is_gold:
        embed.set_footer(text="Golden mobs drop double emeralds!")
    await ctx.send(embed=embed)
AXEWOOD = {None:1,"wood":2,"stone":2,"iron":3,"gold":3,"diamond":4}


@bot.command(name="bestiary",aliases =["bs","bes"])
async def bestiary(ctx):
    """Show all mobs you’ve sacrificed, split by Golden vs. normal and by rarity."""
    user_id = ctx.author.id
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT mob_name, is_golden, rarity, COUNT(*) AS cnt
              FROM sacrifice_history
             WHERE discord_id = $1
             GROUP BY is_golden, rarity, mob_name
             ORDER BY is_golden DESC, rarity ASC, mob_name
            """,
            user_id
        )

    # organize: data[gold_flag][rarity] = [(mob, cnt), ...]
    data = {True: {}, False: {}}
    for r in rows:
        g = r["is_golden"]
        rar = r["rarity"]
        data[g].setdefault(rar, []).append((r["mob_name"], r["cnt"]))

    embed = discord.Embed(
        title=f"{ctx.author.display_name}'s Sacrifice Bestiary",
        color=discord.Color.teal()
    )

    def add_section(gold_flag, title):
        section = data[gold_flag]
        if not section:
            return
        # header for this group
        embed.add_field(name=title, value="​", inline=False)
        for rar in sorted(section):
            info = RARITIES[rar]
            label = f"{info['name'].title()} [{rar}]"
            lines = [f"• **{name}** × {cnt}" for name, cnt in section[rar]]
            embed.add_field(name=label, value="\n".join(lines), inline=False)

    # golden first
    add_section(True, "✨ Golden Sacrificed Mobs ✨")
    # then normal
    add_section(False, "Sacrificed Mobs")

    await ctx.send(embed=embed)
@bot.command(name="chop")
@commands.cooldown(1, 120, commands.BucketType.user)  # 1 use per 240s per user
async def chop(ctx):
    """Gain 1 wood every 120s."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(ctx.author.id)
        # 1) Fetch all usable pickaxes
        axes = await conn.fetch(
            """
            SELECT tier, uses_left
              FROM tools
             WHERE user_id = $1
               AND tool_name = 'axe'
               AND uses_left > 0
            """,
            user_id
        )
        owned_tiers = {r["tier"] for r in axes}
        best_tier = None
        for tier in reversed(TIER_ORDER):
            if tier in owned_tiers:
                best_tier = tier
                break
        
        num = AXEWOOD[best_tier]
        # grant 1 wood
        await conn.execute(
            "UPDATE players SET wood = wood + $1 WHERE user_id = $2;",
            num, user_id
        )
        await conn.execute(
            """
            UPDATE tools
               SET uses_left = uses_left - 1
             WHERE user_id = $1
               AND tool_name = 'axe'
               AND tier = $2
               AND uses_left > 0
            """,
            user_id, best_tier
        )
        # fetch the updated wood count
        row = await conn.fetchrow(
            "SELECT wood FROM players WHERE user_id = $1;",
            user_id
        )

    wood = row["wood"]
    await ctx.send(
        f"{ctx.author.mention} swung their axe and chopped 🌳 **{num} wood**! "
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

# Define weighted drop tables per pickaxe tier
DROP_TABLES = {
    "wood":    {"cobblestone": 80, "iron": 15, "gold": 4,  "diamond": 1},
    "stone":   {"cobblestone": 70, "iron": 20, "gold": 8, "diamond": 2},
    "iron":    {"cobblestone": 50, "iron": 30, "gold": 16, "diamond": 4},
    "gold":    {"cobblestone": 40, "iron": 30, "gold": 22, "diamond": 8},
    "diamond": {"cobblestone": 20, "iron": 35, "gold": 30, "diamond": 15},
}



@bot.command(name="mine")
@commands.cooldown(1, 120, commands.BucketType.user)
async def mine(ctx):
    """Mine for cobblestone or ores; better pickaxes yield rarer drops."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(ctx.author.id)
        # 1) Fetch all usable pickaxes
        pickaxes = await conn.fetch(
            """
            SELECT tier, uses_left
              FROM tools
             WHERE user_id = $1
               AND tool_name = 'pickaxe'
               AND uses_left > 0
            """,
            user_id
        )

        if not pickaxes:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(
                "❌ You need a pickaxe with at least 1 use to mine! Craft one with `!craft pickaxe wood`."
            )

        # 2) Determine your highest tier pickaxe
        owned_tiers = {r["tier"] for r in pickaxes}
        best_tier = None
        for tier in reversed(TIER_ORDER):
            if tier in owned_tiers:
                best_tier = tier
                break

        # 3) Consume 1 use on that pickaxe
        await conn.execute(
            """
            UPDATE tools
               SET uses_left = uses_left - 1
             WHERE user_id = $1
               AND tool_name = 'pickaxe'
               AND tier = $2
               AND uses_left > 0
            """,
            user_id, best_tier
        )

        # 4) Pick a drop according to your tier’s table
        table = DROP_TABLES[best_tier]
        ores, weights = zip(*table.items())
        drop = random.choices(ores, weights=weights, k=1)[0]

        # 5) Grant the drop
        await conn.execute(
            f"UPDATE players SET {drop} = {drop} + 1 WHERE user_id = $1;",
            user_id
        )
        # fetch new total
        row = await conn.fetchrow(
            f"SELECT {drop} FROM players WHERE user_id = $1;",
            user_id
        )
        total = row[drop]

    # Prepare the final result text
    emojis = {"cobblestone":"🪨","iron":"🔩","gold":"🪙","diamond":"💎"}
    emoji = emojis.get(drop, "⛏️")
    result = (
        f"{ctx.author.mention} mined with a **{best_tier.title()} Pickaxe** and found "
        f"{emoji} **1 {drop}**! You now have **{total} {drop}**."
    )

    # --- 2) Play the animation ---
    frames = [
        "⛏️ Mining... [░░░░░]",
        "⛏️ Mining... [▓░░░░]",
        "⛏️ Mining... [▓▓░░░]",
        "⛏️ Mining... [▓▓▓░░]",
        "⛏️ Mining... [▓▓▓▓░]",
        "⛏️ Mining... [▓▓▓▓▓]",
    ]
    msg = await ctx.send(f"{ctx.author.mention} {frames[0]}")
    for frame in frames[1:]:
        await asyncio.sleep(0.5)
        await msg.edit(content=f"{ctx.author.mention} {frame}")

    # --- 3) Show the result ---
    await asyncio.sleep(0.5)
    await msg.edit(content=result)

@mine.error
async def mine_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)
        await ctx.send(f"You’re too tired to mine again now! Try again in {retry}s.")
        return
    raise error

WHEAT_DROP ={None: 2,
            "wood":   3,
            "stone":   4,
            "iron":    5,
            "gold":    6,
            "diamond": 7
}

@bot.command(name="farm")
@commands.cooldown(1, 120, commands.BucketType.user)
async def farm(ctx):
    """Farm for wheat, better hoe means more wheat."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(ctx.author.id)

        # 1) Fetch all usable pickaxes
        pickaxes = await conn.fetch(
            """
            SELECT tier, uses_left
              FROM tools
             WHERE user_id = $1
               AND tool_name = 'hoe'
               AND uses_left > 0
            """,
            user_id
        )
        # 2) Determine your highest tier hoe
        owned_tiers = {r["tier"] for r in pickaxes}
        best_tier = None
        for tier in reversed(TIER_ORDER):
            if tier in owned_tiers:
                best_tier = tier
                break

        # 3) Consume 1 use on that hoe
        if best_tier:
            await conn.execute(
                """
                UPDATE tools
                SET uses_left = uses_left - 1
                WHERE user_id = $1
                AND tool_name = 'hoe'
                AND tier = $2
                AND uses_left > 0
                """,
                user_id, best_tier
            )

        # 4) Pick a drop according to your tier’s table
        avg = WHEAT_DROP[best_tier]
        drop = random.randint(avg-1,avg+1)

        # 5) Grant the drop
        await conn.execute(
            f"UPDATE players SET wheat = wheat + {drop} WHERE user_id = $1;",
            user_id
        )
        # fetch new total
        row = await conn.fetchrow(
            f"SELECT wheat FROM players WHERE user_id = $1;",
            user_id
        )
        total = row["wheat"]

    # Prepare the final result text
    if best_tier:
        result = (
            f"{ctx.author.mention} farmed with a **{best_tier.title()} Hoe** and found "
            f"🌾 **{drop} Wheat**! You now have **{total} Wheat**."
        )
    else:
        result = (
            f"{ctx.author.mention} farmed by **hand** and found "
            f"🌾 **{drop} Wheat**! You now have **{total} Wheat**."
        )

    # --- 2) Play the animation ---
    frames = [
        "⛏️ farming... [⛏️🌾🌾🌾🌾]",
        "⛏️ farming... [🌿⛏️🌾🌾🌾]",
        "⛏️ farming... [🌿🌿⛏️🌾🌾]",
        "⛏️ farming... [🌿🌿🌿⛏️🌾]",
        "⛏️ farming... [🌿🌿🌿🌿⛏️]",
        "⛏️ farming... [🌿🌿🌿🌿🌿]",
    ]
    msg = await ctx.send(f"{ctx.author.mention} {frames[0]}")
    for frame in frames[1:]:
        await asyncio.sleep(0.5)
        await msg.edit(content=f"{ctx.author.mention} {frame}")

    # --- 3) Show the result ---
    await asyncio.sleep(0.5)
    await msg.edit(content=result)

@farm.error
async def farm_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)
        await ctx.send(f"You’re too tired to farm again now! Try again in {retry}s.")
        return
    raise error
@bot.command(name="inv", aliases=["inventory"])
async def inv(ctx):
    """Show your inventory."""
    user_id = ctx.author.id

    # 1) Fetch their row
    async with db_pool.acquire() as conn:
        player = await conn.fetchrow("""
            SELECT wood, cobblestone, iron, gold, diamond,
                   wheat
            FROM players
            WHERE user_id = $1
        """, user_id)

        tools = await conn.fetch("""
            SELECT tool_name, tier, uses_left
            FROM tools
            WHERE user_id = $1 AND uses_left > 0                                 
                                 """,user_id)
        
        emerald_row = await conn.fetchrow("""
            SELECT emeralds
            FROM accountinfo
            WHERE discord_id = $1                                 
                                 """,user_id)
        emeralds = emerald_row["emeralds"] if emerald_row else 0
        # If they don’t even have a players row yet
    if not player and not tools and not emeralds:
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
    res_lines = []
    if player:
        for key, emoji, label in stats:
            val = player[key]
            if val and val > 0:
                res_lines.append(f"{emoji} **{label}**: {val}")
    if res_lines:
        embed.add_field(
            name="Resources",
            value="\n".join(res_lines),
            inline=False
        )

    # Tools section
    if tools:
        tool_lines = []
        for record in tools:
            name = record["tool_name"].replace("_", " ").title()
            tier = record["tier"].title()
            uses = record["uses_left"]
            emoji = {
                "Axe": "🪓",
                "Pickaxe": "⛏️",
                "Hoe": "🌱",
                "Fishing Rod": "🎣",
                "Sword": "⚔️"
            }.get(name, "🛠️")
            tool_lines.append(f"{emoji} **{tier} {name}** — {uses} use{'s' if uses!=1 else ''}")
        embed.add_field(
            name="Tools",
            value="\n".join(tool_lines),
            inline=False
        )
    if emeralds:
        embed.add_field(
            name="💠Emeralds",
            value=str(emeralds),
            inline=False
        )

    embed.set_footer(text="Use !shop to spend your emeralds & coins")
    await ctx.send(embed=embed)

@bot.command(name="barn")
async def barn(ctx):
    """Show your barn split by Golden vs. normal and by rarity."""
    user_id = ctx.author.id

    # 1) Fetch barn entries
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT mob_name, is_golden, count
              FROM barn
             WHERE user_id = $1 AND count > 0
             ORDER BY is_golden DESC, mob_name
            """,
            user_id
        )
        # fetch barn size & next upgrade cost if you still want those
        size_row = await conn.fetchrow(
            "SELECT barn_size FROM players WHERE user_id = $1", user_id
        )
        size = size_row["barn_size"] if size_row else 5

    # 2) Organize by gold flag → rarity → list of (mob, count)
    data = {True: {}, False: {}}
    for r in rows:
        g    = r["is_golden"]
        name = r["mob_name"]
        cnt  = r["count"]
        rar  = MOBS[name]["rarity"]
        data[g].setdefault(rar, []).append((name, cnt))

    # 3) Build embed
    embed = discord.Embed(
        title=f"{ctx.author.display_name}'s Barn ({size} slots)",
        color=discord.Color.green()
    )
    embed.set_footer(text="Use !upbarn to expand your barn.")

    def add_section(is_gold: bool, header: str):
        section = data[is_gold]
        if not section:
            return
        # Section header
        embed.add_field(name=header, value="​", inline=False)
        # For each rarity in ascending order
        for rar in sorted(section):
            info = RARITIES[rar]
            # e.g. “Common [1]”
            field_name = f"{info['name'].title()} [{rar}]"
            lines = [
                f"• **{n}** × {c}"
                for n, c in section[rar]
            ]
            embed.add_field(
                name=field_name,
                value="\n".join(lines),
                inline=False
            )

    # 4) Golden first, then normal
    add_section(True,  "✨ Golden Mobs ✨")
    add_section(False, "Mobs")

    await ctx.send(embed=embed)
    
@bot.command(name="upbarn")
async def upbarn(ctx):
    """Upgrades your barn by +1 slot, costing (upgrades + 1) wood."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        # 1) Ensure player row exists
        await conn.execute(
            "INSERT INTO players (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
            user_id
        )
        # 2) Ensure barn_upgrades row exists
        await conn.execute(
            "INSERT INTO barn_upgrades (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
            user_id
        )

        # 3) Get how many times they’ve upgraded
        up = await conn.fetchrow(
            "SELECT times_upgraded FROM barn_upgrades WHERE user_id = $1",
            user_id
        )
        times_upgraded = up["times_upgraded"]

        # 4) Compute next upgrade cost
        next_cost = (times_upgraded + 1) * 3

        # 5) Check they have enough wood
        pl = await conn.fetchrow(
            "SELECT wood, barn_size FROM players WHERE user_id = $1",
            user_id
        )
        player_wood = pl["wood"]
        current_size = pl["barn_size"]

        if player_wood < next_cost:
            return await ctx.send(
                f"{ctx.author.mention} you need **{next_cost} wood** to upgrade your barn, "
                f"but you only have **{player_wood} wood**."
            )

        # 6) Perform the upgrade
        await conn.execute(
            """
            UPDATE players
               SET wood = wood - $1,
                   barn_size = barn_size + 1
             WHERE user_id = $2
            """,
            next_cost, user_id
        )
        await conn.execute(
            """
            UPDATE barn_upgrades
               SET times_upgraded = times_upgraded + 1
             WHERE user_id = $1
            """,
            user_id
        )

        # 7) Fetch post‐upgrade values
        row = await conn.fetchrow(
            "SELECT wood, barn_size FROM players WHERE user_id = $1",
            user_id
        )
        new_wood = row["wood"]
        new_size = row["barn_size"]

    # 8) Report back
    await ctx.send(
        f"{ctx.author.mention} upgraded their barn from **{current_size}** to **{new_size}** slots "
        f"for 🌳 **{next_cost} wood**! You now have **{new_wood} wood**."
    )

# Spawn channels
SPAWN_CHANNEL_IDS = [1396534538498343002, 1396534603854123088,1396534658656763974,1396534732682035250]

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
    weights = [(4**(max_r + 1-r)) for r in rarities]
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
                    content=f"A mob is appearing, say its name to catch it ({lvl})",
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
