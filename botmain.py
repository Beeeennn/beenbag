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
import uuid
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict, deque
from constants import *
import utils as u
import cc
from stronghold import PathButtons
import re
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
DEFAULT_PREFIX = "bc!"

class BeenBag(commands.Bot):
    async def get_prefix(self, message: discord.Message):
        # Pull from cache; fall back to default
        gid = message.guild.id if message.guild else None
        pref = _prefix_cache.get(gid, DEFAULT_PREFIX)

        # Accept: @mention, configured prefix, and "!" as a safety fallback
        return commands.when_mentioned_or(pref, "!")(self, message)
intents = discord.Intents.default()
intents.message_content = True

def flexible_prefix(bot, message):
    # Always allow "!" as the prefix, ignoring spaces after it
    if message.content.startswith("!"):
        return "!"
    return commands.when_mentioned(bot, message)  # still allow @BotName commands
if not hasattr(globals(), "_prefix_cache"):
    _prefix_cache = {}  # type: dict[int, str]

async def warm_prefix_cache():
    """Load all guild prefixes into memory."""
    global _prefix_cache
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT guild_id, command_prefix FROM guild_settings")
    _prefix_cache = {r["guild_id"]: (r["command_prefix"] or "bc!") for r in rows}

def get_cached_prefix(guild_id: int | None) -> str:
    if guild_id is None:
        return "bc!"
    return _prefix_cache.get(guild_id, "bc!")

def dynamic_prefix(bot: commands.Bot, message: discord.Message):
    configured = get_cached_prefix(message.guild.id if message.guild else None)
    # Allow @mention, the configured prefix, and legacy aliases "!" and "bc!"
    return commands.when_mentioned_or(configured, "!", "bc!")(bot, message)

bot = BeenBag(
    command_prefix=DEFAULT_PREFIX,  # not used, overridden by get_prefix
    case_insensitive=True,
    intents=intents
)
#hold an asyncpg pool here
db_pool: asyncpg.Pool = None

# async def daily_level_decay():
#     tz = ZoneInfo("Europe/London")
#     await bot.wait_until_ready()
#     ch=bot.get_channel(1396194783713824800)
#     while True:
#         # compute seconds until next midnight in London
#         now = datetime.now(tz)
#         tomorrow = (now + timedelta(days=1)).replace(
#             hour=0, minute=0, second=0, microsecond=0
#         )
#         delay = (tomorrow - now).total_seconds()
#         await asyncio.sleep(delay)
#         ch.send("Now removing 1 level from everyone, gotta stay active!")
#         # 1) Demote everyone by one level
#         async with db_pool.acquire() as conn:
#             rows = await conn.fetch("SELECT discord_id, guild_id, exp FROM accountinfo")
#         for record in rows:
#             user_id = record["discord_id"]
#             guild_id = record["guild_id"]
#             old_exp = record["exp"]
#             old_lvl = u.get_level_from_exp(old_exp)
#             if old_lvl <= 0:
#                 continue  # they’re already at level 0

#             # compute new exp so they drop exactly one level
#             new_lvl = old_lvl - 1
#             if new_lvl > 0:
#                 new_exp = LEVEL_EXP[new_lvl] - 1
#             else:
#                 new_exp = old_exp

#             # write it back
#             async with db_pool.acquire() as conn:
#                 await conn.execute(
#                     "UPDATE accountinfo SET exp = $1 WHERE discord_id = $2 AND guild_id = $3",
#                     new_exp, user_id, guild_id
#                 )

#             # 2) Fix up roles in every guild we share
#             for guild in bot.guilds:
#                 member = guild.get_member(user_id)
#                 if not member:
#                     continue
#                 # remove old milestone role if they had one
#                 if old_lvl in MILESTONE_ROLES:
#                     old_role = discord.utils.get(
#                         guild.roles, name=ROLE_NAMES[old_lvl]
#                     )
#                     if old_role in member.roles:
#                         await member.remove_roles(old_role, reason="Daily level decay")
#                     if old_lvl > 11:
#                         new_role = discord.utils.get(
#                         guild.roles, name=ROLE_NAMES[old_lvl-10]
#                     )
#                         await member.add_roles(new_role, reason="Daily level decay")

#                 # add new milestone role if needed
#                 if new_lvl in MILESTONE_ROLES:
#                     new_role = discord.utils.get(
#                         guild.roles, name=ROLE_NAMES[new_lvl]
#                     )
#                     if new_role and new_role not in member.roles:
#                         await member.add_roles(new_role, reason="Daily level decay")

#         # loop back around for the next midnight


async def give_fish_food_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        async with db_pool.acquire() as conn:
            # 0) Keep only fish from the last 24h (global is fine)
            await conn.execute("""
                DELETE FROM aquarium
                WHERE time_caught < NOW() - INTERVAL '1 day'
            """)

            # 1) Pull at most 30 most-recent fish PER (guild, user)
            rows = await conn.fetch("""
                SELECT guild_id, user_id, color1, color2, type
                FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY user_id, guild_id
                               ORDER BY time_caught DESC
                           ) AS rn
                    FROM aquarium
                ) AS ranked
                WHERE rn <= 30
            """)

            # 2) Group fish by (guild_id, user_id)
            from collections import defaultdict
            fish_by_guild_user = defaultdict(list)
            for r in rows:
                key = (r["guild_id"], r["user_id"])
                fish_by_guild_user[key].append((r["color1"], r["color2"], r["type"]))

            # 3) For each (guild, user), compute uniqueness and upsert fish food
            for (guild_id, user_id), fish_list in fish_by_guild_user.items():
                color1s = {f[0] for f in fish_list}
                color2s = {f[1] for f in fish_list}
                types   = {f[2] for f in fish_list}
                total_unique = len(color1s) + len(color2s) + len(types)

                # Upsert into player_items with guild_id in both columns and conflict target
                await conn.execute("""
                    INSERT INTO player_items (guild_id, player_id, item_name, category, quantity, useable)
                    VALUES ($1, $2, 'fish food', 'resource', $3, TRUE)
                    ON CONFLICT (guild_id, player_id, item_name)
                    DO UPDATE SET quantity = player_items.quantity + EXCLUDED.quantity
                """, guild_id, user_id, total_unique)

        print("✅ Fish food distributed.")
        await asyncio.sleep(1800)  # 30 minutes

async def init_db():
    """Create a connection pool """
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    await cc.init_cc(db_pool)
    logging.info("Postgres connected")

# HTTP endpoints
async def handle_ping(request):
    return web.Response(text="pong")


@bot.event
async def on_ready():
    logging.info(f"Bot ready as {bot.user}")
    # if not hasattr(bot, "_decay_task"):
    #     bot._decay_task = bot.loop.create_task(daily_level_decay())
        # load prefixes into cache
    await warm_prefix_cache()
    if not hasattr(bot, "_guild_spawn_tasks"):
        bot._guild_spawn_tasks = {}
    # Start/ensure per‑guild spawners
    start_all_guild_spawn_tasks()
    if not hasattr(bot, "_fishfood_task"):
        bot._fishfood_task = bot.loop.create_task(give_fish_food_task())

@bot.event
async def on_command_error(ctx, error):
    # ignore these
    if isinstance(error, (commands.CommandNotFound, commands.CommandOnCooldown)):
        return

    # notify and log the real exception
    try:
        await ctx.send(":explosion: (something isn't right here)")
    except discord.HTTPException:
        logging.warning("Suppressed send failure due to rate limit.")
    logging.error(f"Unhandled exception in {ctx.command}: {error}", exc_info=error)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    guild_id = message.guild.id
    # auto–eye-roll on every message from that specific user
    # inside on_message, after you computed guild_id
    async with db_pool.acquire() as conn:
        react_ids = await conn.fetchval(
            "SELECT react_channel_ids FROM guild_settings WHERE guild_id=$1",
            guild_id
        )
    react_ids = react_ids or []

    if message.channel.id in react_ids:
        if message.author.id == 1381277906017189898:
            try: await message.add_reaction("🙄")
            except Exception: pass
        elif message.author.id == 1376308591115501618:
            try: await message.add_reaction("🐈")
            except Exception: pass
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
            INSERT INTO accountinfo (discord_id,guild_id)
            VALUES ($1,$2)
            ON CONFLICT (discord_id,guild_id) DO NOTHING;
            """,
            message.author.id,guild_id
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
            await u.ensure_player(conn,message.author.id,guild_id)
            await conn.execute(
                "INSERT INTO barn_upgrades (user_id,guild_id) VALUES ($1,$2) ON CONFLICT DO NOTHING;",
                message.author.id,guild_id
            )
            # count current barn occupancy
            occ = await conn.fetchval(
                "SELECT COALESCE(SUM(count),0) FROM barn WHERE user_id = $1 AND guild_id = $2",
                message.author.id,guild_id
            )
            size = await conn.fetchval(
                "SELECT barn_size FROM new_players WHERE user_id = $1 AND guild_id = $2",
                message.author.id,guild_id
            )
                
            if MOBS[mob_name]["hostile"]:
                sac = True
                reward = await u.sucsac(message.channel,message.author,mob_name,is_golden,"because it can't be captured",conn)
                note = f"this mob is not catchable so it was sacrificed for {reward} emeralds"
            elif occ >= size:
                sac = True
                reward = await u.sucsac(message.channel,message.author,mob_name,is_golden,"because the barn was too full",conn)
                note = f"sacrificed for {reward} emeralds (barn is full)."
                
            else:

                # insert into barn with the golden flag
                await conn.execute(
                    """
                    INSERT INTO barn (user_id, guild_id, mob_name, is_golden, count)
                    VALUES ($1, $4, $2, $3, 1)
                    ON CONFLICT (user_id, mob_name, is_golden)
                    DO UPDATE SET count = barn.count + 1
                    """,
                    message.author.id, mob_name, is_golden, guild_id
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
            if not sac:
                # build and send the embed
                embed = discord.Embed(
                    title=f"🏆 {message.author.display_name} caught a {'✨ Golden ' if is_golden else ''} {RARITIES[rarity]['name']} {mob_name}!",
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


async def handle_get_image(request):
    # URL style: /i/<uuid> or /i/<uuid>.png
    raw = request.match_info.get("id", "")
    media_id = raw.split(".", 1)[0]  # strip optional .png

    try:
        uuid_obj = uuid.UUID(media_id)
    except Exception:
        return web.Response(status=404, text="not found")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT mime, bytes FROM media WHERE id = $1", uuid_obj)
    if not row:
        return web.Response(status=404, text="not found")

    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "ETag": media_id,
        "Content-Disposition": 'inline; filename="image.png"',
    }
    return web.Response(
        body=bytes(row["bytes"]),
        content_type=row["mime"],
        headers=headers
    )


########################################### ADMIN #########################################################################

import re

# Accepts: <#123>, https://discord.com/channels/GUILD/123, or 123
_CHANNEL_TOKEN_RE = re.compile(
    r"(?:<#(?P<m>\d{15,25})>|https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/\d{15,25}/(?P<u>\d{15,25})|\b(?P<i>\d{15,25})\b)"
)

def _extract_first_channel_id(text: str) -> int | None:
    if not text:
        return None
    m = _CHANNEL_TOKEN_RE.search(text)
    if not m:
        return None
    s = m.group("m") or m.group("u") or m.group("i")
    try:
        return int(s)
    except Exception:
        return None

def _resolve_channel_from_text(ctx, text: str | None):
    """Return a TextChannel/Thread from mention/link/id text; or None."""
    cid = _extract_first_channel_id(text or "")
    if not cid:
        return None
    ch = ctx.guild.get_channel(cid) or ctx.bot.get_channel(cid)
    return ch

def _bot_can_send(ctx, ch) -> bool:
    me = ctx.guild.me
    if not me:
        return False
    perms = ch.permissions_for(me)
    # Threads may need send_messages_in_threads
    return perms.view_channel and (getattr(perms, "send_messages_in_threads", False) or perms.send_messages)

async def _array_add(conn, guild_id: int, column: str, value: int):
    # safety: only allow expected columns
    if column not in ("link_channel_ids", "game_channel_ids"):
        raise ValueError("invalid column")
    await conn.execute(
        f"""
        INSERT INTO guild_settings (guild_id, {column})
        VALUES ($1, ARRAY[$2]::bigint[])
        ON CONFLICT (guild_id) DO UPDATE
        SET {column} = (
          SELECT ARRAY(
            SELECT DISTINCT e FROM unnest(coalesce(guild_settings.{column}, '{{}}'::bigint[]) || ARRAY[$2]::bigint[]) AS t(e)
          )
        )
        """,
        guild_id, value
    )

async def _array_remove(conn, guild_id: int, column: str, value: int):
    if column not in ("link_channel_ids", "game_channel_ids"):
        raise ValueError("invalid column")
    await conn.execute(
        f"""
        INSERT INTO guild_settings (guild_id, {column})
        VALUES ($1, '{{}}'::bigint[])
        ON CONFLICT (guild_id) DO UPDATE
        SET {column} = array_remove(coalesce(guild_settings.{column}, '{{}}'::bigint[]), $2)
        """,
        guild_id, value
    )

def parse_channel_ids_any(bot: commands.Bot, msg: discord.Message) -> list[int]:
    ids = set()
    for m in _CHANNEL_TOKEN_RE.finditer(msg.content):
        cid = m.group("m") or m.group("u") or m.group("i")
        if cid:
            try:
                cid_i = int(cid)
            except ValueError:
                continue
            ch = msg.guild.get_channel(cid_i) or bot.get_channel(cid_i)
            if ch and ch.guild.id == msg.guild.id:
                ids.add(cid_i)
    return list(ids)

def parse_one_channel_id_any(bot: commands.Bot, msg: discord.Message) -> int | None:
    ids = parse_channel_ids_any(bot, msg)
    return ids[0] if ids else None
# allow react_channel_ids in the generic array updaters
async def _array_add(conn, guild_id: int, column: str, value: int):
    if column not in ("link_channel_ids", "game_channel_ids", "react_channel_ids"):
        raise ValueError("invalid column")
    await conn.execute(
        f"""
        INSERT INTO guild_settings (guild_id, {column})
        VALUES ($1, ARRAY[$2]::bigint[])
        ON CONFLICT (guild_id) DO UPDATE
        SET {column} = (
          SELECT ARRAY(
            SELECT DISTINCT e FROM unnest(coalesce(guild_settings.{column}, '{{}}'::bigint[]) || ARRAY[$2]::bigint[]) AS t(e)
          )
        )
        """,
        guild_id, value
    )

async def _array_remove(conn, guild_id: int, column: str, value: int):
    if column not in ("link_channel_ids", "game_channel_ids", "react_channel_ids"):
        raise ValueError("invalid column")
    await conn.execute(
        f"""
        INSERT INTO guild_settings (guild_id, {column})
        VALUES ($1, '{{}}'::bigint[])
        ON CONFLICT (guild_id) DO UPDATE
        SET {column} = array_remove(coalesce(guild_settings.{column}, '{{}}'::bigint[]), $2)
        """,
        guild_id, value
    )
def _bot_can_react(ctx, ch) -> bool:
    me = ctx.guild.me
    if not me:
        return False
    perms = ch.permissions_for(me)
    # For threads, Add Reactions is still the key
    return perms.view_channel and perms.add_reactions

@bot.command(name="enablewelcome", aliases=["welcomeon"])
@commands.has_permissions(administrator=True)
async def enablewelcome(ctx):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, welcome_enabled)
            VALUES ($1, TRUE)
            ON CONFLICT (guild_id) DO UPDATE SET welcome_enabled = EXCLUDED.welcome_enabled
            """,
            ctx.guild.id
        )
    await ctx.send("✅ Welcome messages **enabled**. New members will be greeted in the announce channel (if set).")


@bot.command(name="disablewelcome", aliases=["welcomeoff"])
@commands.has_permissions(administrator=True)
async def disablewelcome(ctx):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, welcome_enabled)
            VALUES ($1, FALSE)
            ON CONFLICT (guild_id) DO UPDATE SET welcome_enabled = EXCLUDED.welcome_enabled
            """,
            ctx.guild.id
        )
    await ctx.send("✅ Welcome messages **disabled** for this server.")



@bot.command(name="setupbot", aliases=["setup"])
@commands.has_permissions(administrator=True)
async def setup(ctx):
    guild_id = ctx.guild.id

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    def parse_channel_list(msg):
        return [c.id for c in msg.channel_mentions]

    def validate_prefix(s: str) -> str | None:
        s = s.strip()
        if not s:
            return None
        if len(s) > 8:
            return None
        if any(ch.isspace() for ch in s):
            return None
        return s

    async with db_pool.acquire() as conn:


        
        # 6) Command prefix 
        await ctx.send("**1/7** What **command prefix** should I use? (e.g. `!`, `bc!`, `$`). Type `default` to use `bc!`.")
        msg = await bot.wait_for("message", check=check)
        raw = msg.content.strip()
        if raw.lower() == "default":
            command_prefix = "bc!"
        else:
            command_prefix = validate_prefix(raw)
            if not command_prefix:
                await ctx.send("❌ Invalid prefix. Using default `bc!`.")
                command_prefix = "bc!"

        # 1) Spawn channels
        await ctx.send("**2/7** Mention the **channels for mob spawns** (space/comma separated), or type `none` to skip:")
        msg = await bot.wait_for("message", check=check)
        spawn_channels = parse_channel_ids_any(bot, msg) if msg.content.strip().lower() != "none" else []

        # 2) Announce channel
        await ctx.send("**3/7** Mention the **announce channel** (level ups, welcomes), or type `none` to skip:")
        msg = await bot.wait_for("message", check=check)
        announce_channel_id = parse_one_channel_id_any(bot, msg) if msg.content.strip().lower() != "none" else None

        # 3) Link channels
        await ctx.send("**4/7** Mention the **link channels** (space/comma) where the bot can send links, or type `none` to skip:")
        msg = await bot.wait_for("message", check=check)
        link_channel_ids = parse_channel_ids_any(bot, msg) if msg.content.strip().lower() != "none" else []

        # 4) React channels
        await ctx.send("**5/7** Mention the **react channels** (space/comma) where the bot can auto-react, or type `none` to skip:")
        msg = await bot.wait_for("message", check=check)
        react_channel_ids = parse_channel_ids_any(bot, msg) if msg.content.strip().lower() != "none" else []

        # 5) Game channels
        await ctx.send("**6/7** Mention the **game channels** (space/comma) where game commands are allowed, or type `none` to allow anywhere:")
        msg = await bot.wait_for("message", check=check)
        game_channel_ids = parse_channel_ids_any(bot, msg) if msg.content.strip().lower() != "none" else []

        # 7) Log channel
        await ctx.send("**7/7** Mention the **log channel** (admin logs), or type `none` to skip:")
        msg = await bot.wait_for("message", check=check)
        log_channel_id = parse_one_channel_id_any(bot, msg) if msg.content.strip().lower() != "none" else None

        # Upsert guild_settings (now also stores command_prefix)
        await conn.execute(
            """
            INSERT INTO guild_settings (
                guild_id,
                announce_channel_id,
                link_channel_ids,
                react_channel_ids,
                game_channel_ids,
                log_channel_id,
                command_prefix
            ) VALUES ($1, $2, $3::bigint[], $4::bigint[], $5::bigint[], $6, $7)
            ON CONFLICT (guild_id) DO UPDATE
            SET announce_channel_id = EXCLUDED.announce_channel_id,
                link_channel_ids    = EXCLUDED.link_channel_ids,
                react_channel_ids   = EXCLUDED.react_channel_ids,
                game_channel_ids    = EXCLUDED.game_channel_ids,
                log_channel_id      = EXCLUDED.log_channel_id,
                command_prefix      = EXCLUDED.command_prefix
            """,
            guild_id,
            announce_channel_id,
            link_channel_ids,
            react_channel_ids,
            game_channel_ids,
            log_channel_id,
            command_prefix,
        )

        # Replace spawn channels
        await conn.execute("DELETE FROM guild_spawn_channels WHERE guild_id = $1", guild_id)
        for ch_id in spawn_channels:
            await conn.execute(
                "INSERT INTO guild_spawn_channels (guild_id, channel_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                guild_id, ch_id
            )

    # update cache immediately so it takes effect
    _prefix_cache[guild_id] = command_prefix

    await ctx.send(
        f"✅ Setup complete! Using prefix **`{command_prefix}`**\n"
        "• spawn channels saved\n"
        "• announce channel saved\n"
        "• link channels saved\n"
        "• react channels saved\n"
        "• game channels saved\n"
        "• log channel saved"
    )
    start_guild_spawn_task(guild_id)

# ---------- prefix helpers (place near your other helpers) ----------
DEFAULT_PREFIX = "bc!"

# shared cache you already use; make sure this exists
# _prefix_cache: dict[int, str]  # assumed created earlier

def sanitize_prefix(raw: str) -> str | None:
    """Return a cleaned prefix or None if invalid."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) > 8:       # keep it reasonable
        return None
    if any(ch.isspace() for ch in s):
        return None
    return s

# ---------- setprefix command ----------
@bot.command(name="setprefix", aliases=["prefix"])
@commands.has_permissions(administrator=True)
async def setprefix(ctx, *, new_prefix: str | None = None):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    guild_id = ctx.guild.id

    # Show current prefix if no argument given
    if new_prefix is None:
        current = _prefix_cache.get(guild_id, DEFAULT_PREFIX)
        return await ctx.send(
            f"Current prefix here is **`{current}`**.\n"
            f"Change it with `@{bot.user.name} setprefix <new>` or `{current}setprefix <new>`.\n"
            f"Use `setprefix default` to reset."
        )

    # Handle reset/default
    if new_prefix.lower() in ("default", "reset"):
        prefix = DEFAULT_PREFIX
    else:
        prefix = sanitize_prefix(new_prefix)
        if not prefix:
            return await ctx.send("❌ Invalid prefix. Use 1–8 non-space characters. Example: `!`, `bc!`, `$`")

    # Persist + cache
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, command_prefix)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE
            SET command_prefix = EXCLUDED.command_prefix
            """,
            guild_id, prefix
        )

    _prefix_cache[guild_id] = prefix  # take effect immediately

    await ctx.send(f"✅ Prefix updated to **`{prefix}`**. You can now use `{prefix}help`.")

@bot.command(name="spawnnow")
@commands.has_permissions(administrator=True)
async def spawnnow(ctx):
    try:
        await spawn_once_in_channel(ctx.channel)
    except Exception as e:
        logging.exception("[spawns] spawnnow failed")
        await ctx.send(f"❌ spawn failed: `{type(e).__name__}: {e}`")


@bot.command(name="setlogs", aliases=["setlog", "logs"])
@commands.has_permissions(administrator=True)
async def setlogs(ctx, channel: discord.TextChannel | None = None):
    """
    Set the logs channel for this server.
    Use in a channel with no args to set it to the current channel,
    or mention a channel to target it, e.g. `#logs`.
    """
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = channel or ctx.channel

    # sanity: make sure it's in this guild and bot can post there
    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")
    me = ctx.guild.me
    perms = target.permissions_for(me)
    if not (perms.view_channel and (perms.send_messages or getattr(perms, "send_messages_in_threads", False))):
        return await ctx.send(f"❌ I don’t have permission to post in {target.mention}.")

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, log_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE
            SET log_channel_id = EXCLUDED.log_channel_id
            """,
            ctx.guild.id, target.id
        )

    await ctx.send(f"✅ Log channel set to {target.mention}.")
@setlogs.error
async def setlogs_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        # happens if the provided channel arg couldn't be resolved to a TextChannel
        return await ctx.send(
            f"❌ I couldn't find that channel. "
            f"Use `{ctx.clean_prefix}setlogs` in the target channel, or mention it like `#logs`."
        )
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send("❌ You need the **Administrator** permission to do that.")
    raise error

@bot.command(name="addspawnchannel", aliases=["addspawn", "addspawnhannel"])  # last alias covers the typo just in case
@commands.has_permissions(administrator=True)
async def addspawnchannel(ctx, channel: discord.abc.GuildChannel | None = None):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = channel or ctx.channel

    # Only allow text channels or threads
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")

    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")

    me = ctx.guild.me
    perms = target.permissions_for(me)
    can_send = perms.send_messages or getattr(perms, "send_messages_in_threads", False)
    if not (perms.view_channel and can_send):
        return await ctx.send(f"❌ I don’t have permission to post in {target.mention}.")

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_spawn_channels (guild_id, channel_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            ctx.guild.id, target.id
        )

    # refresh this guild's spawner to pick up the change
    start_guild_spawn_task(ctx.guild.id)

    await ctx.send(f"✅ Added {target.mention} as a spawn channel.")


@addspawnchannel.error
async def addspawnchannel_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        return await ctx.send(
            f"❌ I couldn't find that channel. "
            f"Use `{ctx.clean_prefix}addspawnchannel` in the target channel, or mention it like `#spawns`."
        )
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send("❌ You need the **Administrator** permission to do that.")
    raise error


# REMOVE a spawn channel
@bot.command(name="removespawnchannel", aliases=["removespawn", "delspawn"])
@commands.has_permissions(administrator=True)
async def removespawnchannel(ctx, channel: discord.abc.GuildChannel | None = None):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = channel or ctx.channel

    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")

    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")

    async with db_pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM guild_spawn_channels WHERE guild_id = $1 AND channel_id = $2",
            ctx.guild.id, target.id
        )

    # res is like "DELETE 0" or "DELETE 1"
    deleted = res.endswith("1")

    # refresh this guild's spawner to pick up the change
    start_guild_spawn_task(ctx.guild.id)

    if deleted:
        await ctx.send(f"✅ Removed {target.mention} from spawn channels.")
    else:
        await ctx.send(f"ℹ️ {target.mention} wasn’t a spawn channel.")
    

@removespawnchannel.error
async def removespawnchannel_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        return await ctx.send(
            f"❌ I couldn't find that channel. "
            f"Use `{ctx.clean_prefix}removespawnchannel` here, or mention it like `#spawns`."
        )
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send("❌ You need the **Administrator** permission to do that.")
    raise error

@bot.command(name="addlinkchannel", aliases=["addlink", "addlinkch"])
@commands.has_permissions(administrator=True)
async def addlinkchannel(ctx, *, channel_text: str | None = None):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = _resolve_channel_from_text(ctx, channel_text) or ctx.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")
    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")
    if not _bot_can_send(ctx, target):
        return await ctx.send(f"❌ I don’t have permission to post in {target.mention}.")

    async with db_pool.acquire() as conn:
        await _array_add(conn, ctx.guild.id, "link_channel_ids", target.id)

    await ctx.send(f"✅ Added {target.mention} to **link channels**.")

@bot.command(name="removelinkchannel", aliases=["removelink", "dellink"])
@commands.has_permissions(administrator=True)
async def removelinkchannel(ctx, *, channel_text: str | None = None):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = _resolve_channel_from_text(ctx, channel_text) or ctx.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")
    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")

    async with db_pool.acquire() as conn:
        await _array_remove(conn, ctx.guild.id, "link_channel_ids", target.id)

    await ctx.send(f"✅ Removed {target.mention} from **link channels** (if it was set).")

@bot.command(name="linkchannels", aliases=["listlinks"])
@commands.has_permissions(administrator=True)
async def linkchannels(ctx):
    async with db_pool.acquire() as conn:
        ids = await conn.fetchval(
            "SELECT link_channel_ids FROM guild_settings WHERE guild_id=$1",
            ctx.guild.id
        )
    ids = ids or []
    if not ids:
        return await ctx.send("ℹ️ No link channels configured.")
    mentions = []
    for cid in ids:
        ch = ctx.guild.get_channel(cid) or bot.get_channel(cid)
        mentions.append(ch.mention if ch else f"`{cid}` (missing)")
    await ctx.send("🔗 Link channels:\n• " + "\n• ".join(mentions))

@bot.command(name="addgamechannel", aliases=["addgame", "addgamech"])
@commands.has_permissions(administrator=True)
async def addgamechannel(ctx, *, channel_text: str | None = None):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = _resolve_channel_from_text(ctx, channel_text) or ctx.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")
    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")
    if not _bot_can_send(ctx, target):
        return await ctx.send(f"❌ I don’t have permission to post in {target.mention}.")

    async with db_pool.acquire() as conn:
        await _array_add(conn, ctx.guild.id, "game_channel_ids", target.id)

    await ctx.send(f"✅ Added {target.mention} to **game channels**.")
@bot.command(name="removegamechannel", aliases=["removegame", "delgame"])
@commands.has_permissions(administrator=True)
async def removegamechannel(ctx, *, channel_text: str | None = None):
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = _resolve_channel_from_text(ctx, channel_text) or ctx.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")
    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")

    async with db_pool.acquire() as conn:
        await _array_remove(conn, ctx.guild.id, "game_channel_ids", target.id)

    await ctx.send(f"✅ Removed {target.mention} from **game channels** (if it was set).")
@bot.command(name="gamechannels", aliases=["listgames"])
@commands.has_permissions(administrator=True)
async def gamechannels(ctx):
    async with db_pool.acquire() as conn:
        ids = await conn.fetchval(
            "SELECT game_channel_ids FROM guild_settings WHERE guild_id=$1",
            ctx.guild.id
        )
    ids = ids or []
    if not ids:
        return await ctx.send("ℹ️ No game channels configured. (If empty, game commands are allowed anywhere.)")
    mentions = []
    for cid in ids:
        ch = ctx.guild.get_channel(cid) or bot.get_channel(cid)
        mentions.append(ch.mention if ch else f"`{cid}` (missing)")
    await ctx.send("🎮 Game channels:\n• " + "\n• ".join(mentions))

@bot.command(name="addreactchannel", aliases=["addreact", "addreactch"])
@commands.has_permissions(administrator=True)
async def addreactchannel(ctx, *, channel_text: str | None = None):
    """Add a channel where the bot is allowed to auto-react."""
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = _resolve_channel_from_text(ctx, channel_text) or ctx.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")
    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")
    if not _bot_can_react(ctx, target):
        return await ctx.send(f"❌ I don’t have permission to react in {target.mention}.")

    async with db_pool.acquire() as conn:
        await _array_add(conn, ctx.guild.id, "react_channel_ids", target.id)

    await ctx.send(f"✅ Added {target.mention} to **react channels**.")
@bot.command(name="removereactchannel", aliases=["removerea ct", "delreact"])
@commands.has_permissions(administrator=True)
async def removereactchannel(ctx, *, channel_text: str | None = None):
    """Remove a channel from the bot’s auto-react list."""
    if ctx.guild is None:
        return await ctx.send("❌ This command can only be used in a server.")

    target = _resolve_channel_from_text(ctx, channel_text) or ctx.channel
    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return await ctx.send("❌ Please choose a text channel or a thread.")
    if target.guild.id != ctx.guild.id:
        return await ctx.send("❌ That channel isn’t in this server.")

    async with db_pool.acquire() as conn:
        await _array_remove(conn, ctx.guild.id, "react_channel_ids", target.id)

    await ctx.send(f"✅ Removed {target.mention} from **react channels** (if it was set).")
@bot.command(name="reactchannels", aliases=["listreact"])
@commands.has_permissions(administrator=True)
async def reactchannels(ctx):
    """List configured react channels."""
    async with db_pool.acquire() as conn:
        ids = await conn.fetchval(
            "SELECT react_channel_ids FROM guild_settings WHERE guild_id=$1",
            ctx.guild.id
        )
    ids = ids or []
    if not ids:
        return await ctx.send("ℹ️ No react channels configured.")
    mentions = []
    for cid in ids:
        ch = ctx.guild.get_channel(cid) or bot.get_channel(cid)
        mentions.append(ch.mention if ch else f"`{cid}` (missing)")
    await ctx.send("😄 React channels:\n• " + "\n• ".join(mentions))

#################################################################  CHECKS  #####################################################################################################

@bot.check
async def only_in_game_channels(ctx: commands.Context):
    # DM or system messages -> allow
    if ctx.guild is None:
        return True

    # Only gate commands marked as game_command
    is_game = getattr(ctx.command.callback, "is_game_command", False)
    if not is_game:
        return True

    # Optional: let admins bypass
    if ctx.author.guild_permissions.administrator:
        return True

    async with db_pool.acquire() as conn:
        ids = await conn.fetchval(
            "SELECT game_channel_ids FROM guild_settings WHERE guild_id=$1",
            ctx.guild.id
        )

    # If not configured or empty -> allow everywhere
    if not ids:
        return True

    if ctx.channel.id in ids:
        return True

    try:
        await ctx.send("❌ Use game commands in the designated game channels.")
    except Exception:
        pass
    return False

############################################################## EVENTS #############################################################
@bot.event
async def on_guild_join(guild):
    _prefix_cache[guild.id] = "bc!"  # default until setup runs
    start_guild_spawn_task(guild.id)

@bot.event
async def on_guild_remove(guild):
    stop_guild_spawn_task(guild.id)
@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    # read setting (default TRUE) + preferred channel
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(welcome_enabled, TRUE) AS welcome_enabled,
                   announce_channel_id
            FROM guild_settings
            WHERE guild_id = $1
            """,
            guild.id
        )

    if row and not row["welcome_enabled"]:
        return  # welcomes disabled

    # pick channel: announce_channel_id → system_channel → first text channel we can speak in
    channel = None
    if row and row["announce_channel_id"]:
        channel = guild.get_channel(row["announce_channel_id"])
        if channel and not channel.permissions_for(guild.me).send_messages:
            channel = None
    if channel is None:
        ch = guild.system_channel
        if ch and ch.permissions_for(guild.me).send_messages:
            channel = ch
    if channel is None:
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.view_channel and perms.send_messages:
                channel = ch
                break
    if channel is None:
        return  # nowhere safe to speak

    # compose a simple welcome
    try:
        pref = get_cached_prefix(guild.id) if "get_cached_prefix" in globals() else "bc!"
        await channel.send(
            f"👋 Welcome {member.mention}! Glad to have you in **{guild.name}**.\n"
            f"Try `{pref}help` to see what I can do."
        )
    except Exception:
        logging.exception("Failed to send welcome message")

async def _find_writable_channel(guild: discord.Guild) -> discord.TextChannel | None:
    me = guild.me
    # 1) Prefer the system channel if we can talk there
    if guild.system_channel:
        p = guild.system_channel.permissions_for(me)
        if p.view_channel and p.send_messages:
            return guild.system_channel

    # 2) Otherwise the first text channel we can speak in (by position)
    for ch in sorted(guild.text_channels, key=lambda c: c.position):
        p = ch.permissions_for(me)
        if p.view_channel and p.send_messages:
            return ch

    return None

@bot.event
async def on_guild_join(guild: discord.Guild):
    # Seed default prefix in cache so messages show the right thing
    DEFAULT_PREFIX = "bc!"
    _prefix_cache[guild.id] = DEFAULT_PREFIX  # until setup/setprefix
    start_guild_spawn_task(guild.id)

    # Create a default row so welcome_enabled etc. have defaults (optional but nice)
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO guild_settings (guild_id, command_prefix)
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO NOTHING
                """,
                guild.id, DEFAULT_PREFIX
            )
    except Exception:
        logging.exception("Failed inserting default guild_settings row on join")

    # Pick a channel to speak in
    channel = await _find_writable_channel(guild)
    prefix  = _prefix_cache.get(guild.id, DEFAULT_PREFIX)

    intro = discord.Embed(
        title=f"Thanks for adding {bot.user.name}! 🎉",
        description=(
            f"My prefix here is **`{prefix}`** (you can also mention me).\n\n"
            f"Use `{prefix}setup` to setup some server info (spawn, logs, links, game channels, prefix)\n"
            f"Join the discord at https://discord.gg/St4Asc5hJP to give suggestions, report bugs and ask any questions\n"
            f"Try `{prefix}help` to see everything I can do."
            f"THIS IS NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT. Use `{prefix}credits` for more info"  
        ),
        color=discord.Color.green()
    )

    sent = False
    if channel:
        try:
            await channel.send(embed=intro)
            sent = True
        except Exception:
            logging.exception("Intro send failed in chosen channel")

    # Fallback: DM the guild owner if we couldn't post anywhere
    if not sent:
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            if owner:
                await owner.send(
                    f"Hi! Thanks for inviting **{bot.user.name}** to **{guild.name}**.\n"
                    f"I couldn’t post an intro in the server, likely due to channel permissions.\n\n"
                    f"Use `{prefix}setup` in a channel where I can speak to configure me."
                )
        except Exception:
            logging.exception("Also failed to DM owner after join")

############################################################## USER COMMANDS ################################################################
@bot.command()
async def showprefix(ctx):
    gid = ctx.guild.id if ctx.guild else None
    await ctx.send(f"configured: `{_prefix_cache.get(gid, DEFAULT_PREFIX)}`, used: `{ctx.clean_prefix}`")

@bot.command(name="linkyt")
async def linkyt(ctx, *, channel_name: str):
    await cc.c_linkyt(ctx,channel_name)

@bot.command(name="yt")
async def yt(ctx, *, who = None):
    await cc.c_yt(ctx, who)

@bot.command(name="credits", aliases=["license", "licence", "about"])
async def credits(ctx):
    pref = get_cached_prefix(ctx.guild.id if ctx.guild else None)

    e = discord.Embed(
        title="Attribution & Licensing",
        description=(
            "This bot **does not use any images from community wikis**.\n\n"
            "Media used by the bot falls into these categories:\n"
            "• **Original assets** created for the bot.\n"
            "• **User-submitted content** used with permission.\n"
            "• **Mojang-owned material** (e.g., screenshots/textures) only where permitted by the "
            "**Minecraft Usage Guidelines** and with the required disclaimer."
        ),
        color=discord.Color.blurple()
    )

    e.add_field(
        name="Trademark / Affiliation",
        value=(
            "NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT."
        ),
        inline=False
    )

    e.add_field(
        name="Learn more",
        value=(
            "• Minecraft Usage Guidelines (covers when/how Mojang content may be used).\n"
            f"• Per-file sources: `{pref}source <mob>`"
        ),
        inline=False
    )

    e.set_footer(text="Questions about a specific image? Use the source command above.")
    await ctx.send(embed=e)

# --- helpers to mark game commands ---
def game_command():
    def deco(cmd_func):
        # Use Command.extras to tag it
        setattr(cmd_func, "is_game_command", True)
        return cmd_func
    return deco


@game_command()
@bot.command(name="give")
async def give(ctx, who: str, *, mob: str):
    await cc.c_give(ctx, who, mob)
@give.error
async def give_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f"❌ Usage: `{ctx.clean_prefix}give <player> <mob>`")
    raise error


@game_command()
@bot.command(name="craft")
async def craft(ctx, *args):
    await cc.c_craft(ctx, args)
@craft.error
async def craft_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f"❌ Usage: `{ctx.clean_prefix}craft <tool> [tier]`")
    raise error

@game_command()
@bot.command(name="recipe")
async def recipe(ctx, *args):
    await cc.c_craft(ctx, args)
@craft.error
async def craft_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f"❌ Usage: `{ctx.clean_prefix}recipe <tool> [tier]`")
    raise error

@game_command()
@bot.command(name="shop")
async def shop(ctx):
    await cc.c_shop(ctx)


@game_command()
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


@game_command()
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

@game_command()
@bot.command(name="sacrifice", aliases=["sac", "kill"])
async def sacrifice(ctx, *, mob_name: str):
    await cc.c_sac(ctx,mob_name)

@game_command()
@bot.command(name="bestiary",aliases =["bs","bes"])
async def bestiary(ctx, *, who: str = None):
    await cc.c_bestiary(ctx,who)


@game_command()
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


@game_command()
@bot.command(name="stronghold")
async def stronghold(ctx):
    await ctx.send(f"This feature is currently under development, please try again later (after 12th Aug).:fosh:")
    #await cc.c_stronghold(ctx)

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


@game_command()
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

@game_command()
@bot.command(name="inv", aliases=["inventory"])
async def inv(ctx, *, who: str = None):
    await cc.c_inv(ctx,who)

@game_command()
@bot.command(name="barn")
async def barn(ctx, *, who: str = None):
    await cc.c_barn(ctx, who)

@game_command()
@bot.command(name="upbarn")
async def upbarn(ctx):
    await cc.c_upbarn(ctx)

@game_command()
@bot.command(name="use")
async def use(ctx, *, args:str):
    try:
        *item_parts, qty_str = args.strip().rsplit(" ", 1)
        item_name = " ".join(item_parts).lower()
        quantity = int(qty_str)
        await cc.c_use(ctx,bot,item_name,quantity)
    except ValueError:
        return await ctx.send(f"❌ Use it like `!use item_name quantity` (e.g. `{ctx.clean_prefix}use fish food 100`).")


@commands.cooldown(1, 90, commands.BucketType.user)
@game_command()
@bot.command(name="fish")
async def fish(ctx):
    await cc.make_fish(ctx, "assets/fish/")
@fish.error
async def fish_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry = int(error.retry_after)  # seconds remaining
        await ctx.send(
            f"You are too tired to fish again. Try again in {retry} second{'s' if retry != 1 else ''}."
        )
        return
    # For any other errors, let them bubble up
    raise error


@game_command()
@bot.command(name="aquarium", aliases=["aq"])
async def aquarium(ctx, *, who: str = None):
    await cc.c_generate_aquarium(ctx,who)



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


# store per-guild tasks here
if not hasattr(bot, "_guild_spawn_tasks"):
    bot._guild_spawn_tasks = {}

async def get_spawn_channels_for_guild(guild_id: int):
    """Return a list of channels in this guild where we can spawn."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT channel_id FROM guild_spawn_channels WHERE guild_id = $1",
            guild_id
        )
    chans = []
    for r in rows:
        ch = bot.get_channel(r["channel_id"])
        if not ch:
            continue
        if not isinstance(ch, (discord.TextChannel, discord.Thread)):
            continue
        me = ch.guild.me
        if not me:
            continue
        perms = ch.permissions_for(me)
        if perms.view_channel and perms.send_messages:
            chans.append(ch)
    return chans

async def spawn_once_in_channel(chan):
    # ---- pick a mob exactly like before ----
    mob_names_all = list(MOBS.keys())
    mob_names = [m for m in mob_names_all if m not in NOT_SPAWN_MOBS]
    rarities  = [MOBS[name]["rarity"] for name in mob_names]
    max_r     = max(rarities)
    weights   = [(2 ** (max_r + 1 - r)) for r in rarities]

    mob = random.choices(mob_names, weights=weights, k=1)[0]
    mob_path = f"assets/mobs/{mob}"
    try:
        if os.path.isdir(mob_path):
            imgs = [f for f in os.listdir(mob_path) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
            if not imgs:
                raise FileNotFoundError("No image files in directory")
            src = Image.open(os.path.join(mob_path, random.choice(imgs))).convert("RGBA")
        else:
            src = Image.open(f"{mob_path}.png").convert("RGBA")
    except FileNotFoundError:
        # still send an embed so UX is consistent
        pref = get_cached_prefix(chan.guild.id if chan.guild else None)
        e = discord.Embed(
            title="A mob is appearing!",
            description="(no image found this time) — say its name to catch it",
            color=discord.Color.blurple()
        )
        e.set_footer(text=f"For attribution & licensing, use {pref}credits")
        await chan.send(embed=e)
        return

    # ---- choose a focal point (same as before) ----
    alpha = src.split()[-1]
    bbox  = alpha.getbbox()
    if bbox:
        left, top, right, bottom = bbox
        found = False
        for _ in range(500):
            x = random.randint(left, right - 1)
            y = random.randint(top,  bottom - 1)
            if alpha.getpixel((x, y)) > 0:
                found = True
                break
        if not found:
            x = (left + right) // 2
            y = (top + bottom) // 2
        w, h = src.size
        center = (x / w, y / h)
    else:
        center = (random.uniform(0.1, 0.9), random.uniform(0.1, 0.9))

    # ---- frame creators (unchanged) ----
    def pixelate(img: Image.Image, size: int) -> Image.Image:
        small = img.resize((size, size), resample=Image.NEAREST)
        return small.resize(img.size, Image.NEAREST)

    def zoom_frame_at(src: Image.Image, zoom_frac: float, center_xy: tuple[float, float]) -> Image.Image:
        w, h = src.size
        f  = max(0.01, min(zoom_frac, 1.0))
        cw = int(w * f); ch = int(h * f)
        cx, cy = center_xy
        left = int(cx * w - cw / 2)
        top  = int(cy * h - ch / 2)
        left = max(0, min(left, w - cw))
        top  = max(0, min(top,  h - ch))
        crop = src.crop((left, top, left + cw, top + ch))
        return crop.resize((w, h), Image.NEAREST)

    pix = (random.randint(1, 4) == 1)
    frame_sizes = [1, 2, 4, 8, 16, src.size[0]]
    zoom_levels = [0.01, 0.05, 0.1, 0.2, 0.4, 1.0]
    levels     = frame_sizes if pix else zoom_levels
    make_frame = (lambda lvl: pixelate(src, lvl)) if pix else (lambda lvl: zoom_frame_at(src, lvl, center))

    # ---- build the embed once; image will be provided via attachment ----
    pref = get_cached_prefix(chan.guild.id if chan.guild else None)
    embed = discord.Embed(
        title="A mob is appearing!",
        description=f"Say its name to catch it.",
        color=discord.Color.blurple()
    )
    # IMPORTANT: point the embed image to the attachment filename
    embed.set_image(url="attachment://spawn.png")
    embed.set_footer(text=f"For attribution & licensing, use {pref}credits")

    # first frame
    buf = io.BytesIO()
    make_frame(levels[0]).save(buf, format="PNG")
    buf.seek(0)
    msg = await chan.send(
        embed=embed,
        file=discord.File(buf, "spawn.png")
    )

    # DB insert & expiry
    stay_seconds = RARITIES[MOBS[mob]["rarity"]]["stay"]
    expires = datetime.utcnow() + timedelta(seconds=stay_seconds)

    async with db_pool.acquire() as conn:
        rec = await conn.fetchrow(
            """
            INSERT INTO active_spawns
                (guild_id, channel_id, mob_name, message_id, revealed, spawn_time, expires_at)
            VALUES ($1,$2,$3,$4,0,$5,$6)
            RETURNING spawn_id
            """,
            chan.guild.id, chan.id, mob, msg.id, datetime.utcnow(), expires
        )

    # subsequent frames: replace the attachment, keep the same embed (it still points to attachment://spawn.png)
    for lvl in levels[1:]:
        await asyncio.sleep(15)
        buf = io.BytesIO()
        make_frame(lvl).save(buf, format="PNG")
        buf.seek(0)
        await msg.edit(
            embed=embed,
            attachments=[discord.File(buf, "spawn.png")]
        )

    # schedule expiry watcher
    bot.loop.create_task(
        watch_spawn_expiry(
            spawn_id=rec["spawn_id"],
            channel_id=chan.id,
            message_id=msg.id,
            mob_name=mob,
            expires_at=expires
        )
    )

async def spawn_loop_for_guild(guild_id: int):
    await bot.wait_until_ready()
    while True:
        try:
            channels = await get_spawn_channels_for_guild(guild_id)
            if not channels:
                logging.info(f"[spawns] guild {guild_id}: no valid spawn channels")
            else:
                chan = random.choice(channels)
                logging.info(f"[spawns] guild {guild_id}: spawning in #{chan} ({chan.id})")
                await spawn_once_in_channel(chan)

            # sleep AFTER the attempt
            await asyncio.sleep(random.randint(60, 120))

        except asyncio.CancelledError:
            break
        except Exception:
            logging.exception(f"[spawns] loop error for guild {guild_id}; backing off 10s")
            await asyncio.sleep(10)


def start_guild_spawn_task(guild_id: int):
    stop_guild_spawn_task(guild_id)
    logging.info(f"[spawns] starting loop for guild {guild_id}")
    bot._guild_spawn_tasks[guild_id] = asyncio.create_task(spawn_loop_for_guild(guild_id))


def stop_guild_spawn_task(guild_id: int):
    t = bot._guild_spawn_tasks.pop(guild_id, None)
    if t and not t.done():
        t.cancel()

def start_all_guild_spawn_tasks():
    for g in bot.guilds:
        start_guild_spawn_task(g.id)



async def start_http_server():
    app = web.Application()
    app.router.add_get("/i/{id}", handle_get_image)
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"HTTP server running on port {PORT}")

    return runner  # <-- return runner so you can later call runner.cleanup()


async def main():
    # 1) Init Postgres
    await init_db()

    # 2) Start HTTP server and keep a reference to the runner for cleanup
    runner = await start_http_server()

    # 3) Run the bot, reconnecting on errors
    retry_delay = 5
    try:
        while True:
            try:
                await bot.start(TOKEN)
            except Exception:
                logging.exception(f"Bot disconnected; reconnecting in {retry_delay}s")
                await asyncio.sleep(retry_delay)
    finally:
        # Ensure the aiohttp app is cleaned up properly
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
