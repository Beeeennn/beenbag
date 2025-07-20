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
        "Brown Panda":{"rarity":5,"hostile":False},
        "Parrot":{"rarity":2,"hostile":False},
        "Pig":{"rarity":1,"hostile":False},
        "Sheep":{"rarity":1,"hostile":False}        
        }

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

@bot.event
async def on_ready():
    logging.info(f"Bot ready as {bot.user}")

    # Only schedule it once
    if not hasattr(bot, "_spawn_task"):
        bot._spawn_task = bot.loop.create_task(spawn_mob_loop())
@bot.event
async def on_command_error(ctx, error):
    # Ignore “unknown command” errors
    if isinstance(error, commands.CommandNotFound):
        return

    # Let cooldown handlers do their thing
    if isinstance(error, commands.CommandOnCooldown):
        # If you have per‐command error handlers (like chop_error) they’ll run instead.
        return

    # For everything else, send an explosion and log
    await ctx.send(":explosion:")
    logging.exception(f"Unhandled command error in {ctx.command}:")
async def ensure_player(user_id):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO players (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
            user_id
        )

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
    # 0) Try to capture any active spawn in this channel
    name = message.content.strip().lower()
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
        if spawn and name == spawn["mob_name"].lower():
            # Got it first!
            spawn_id = spawn["spawn_id"]
            mob_name = spawn["mob_name"]

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
                await conn.execute(
                    "UPDATE players SET emeralds = emeralds + $1 WHERE user_id = $2",
                    reward, message.author.id
                )
                
                note = f"sacrificed for emeralds (barn is full)."
                
            elif MOBS[mob_name]["hostile"]:
                #no room → sacrifice for exp
                rarity = MOBS[mob_name]["rarity"]
                rar_info = RARITIES[rarity]
                reward  = rar_info["emeralds"]
                await conn.execute(
                    "UPDATE players SET emeralds = emeralds + $1 WHERE user_id = $2",
                    reward, message.author.id
                )
                
            else:

                await conn.execute(
                    """
                    INSERT INTO barn (user_id, mob_name, count)
                    VALUES ($1,$2,1)
                    ON CONFLICT (user_id,mob_name) DO UPDATE
                      SET count = barn.count + 1
                    """,
                    message.author.id, mob_name
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
                title=f"🏆 {message.author.display_name} caught a {RARITIES[rarity]["name"]} {mob_name}!",
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

    ("sword",     "wood"):    (4,    0,      None, 3),
    ("sword",     "stone"):   (1,    2,      "cobblestone", 3),
    ("sword",     "iron"):    (1,    2,      "iron",        3),
    ("sword",     "gold"):    (1,    2,      "gold",        3),
    ("sword",     "diamond"): (1,    2,      "diamond",     3),
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

@bot.command(name="sacrifice")
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
        have = await conn.fetchval(
            "SELECT count FROM barn WHERE user_id=$1 AND mob_name=$2",
            user_id, key
        ) or 0
        if have < 1:
            return await ctx.send(f"❌ You have no **{key}** to sacrifice.")
        # remove one
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
            "UPDATE players SET emeralds = emeralds + $1 WHERE user_id = $2",
            reward, user_id
        )

    # send embed
    embed = discord.Embed(
        title=f"🗡️ {ctx.author.display_name} sacrificed a {key}",
        description=f"You gained 💠 **{reward} Emerald{'s' if reward!=1 else ''}**!",
        color=color
    )
    embed.add_field(name="Rarity", value=rar_info["name"].title(), inline=True)
    await ctx.send(embed=embed)
    
@bot.command(name="chop")
@commands.cooldown(1, 120, commands.BucketType.user)  # 1 use per 240s per user
async def chop(ctx):
    """Gain 1 wood every 120s."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(ctx.author.id)

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

# Define weighted drop tables per pickaxe tier
DROP_TABLES = {
    "wood":    {"cobblestone": 80, "iron": 15, "gold": 4,  "diamond": 1},
    "stone":   {"cobblestone": 70, "iron": 20, "gold": 8, "diamond": 2},
    "iron":    {"cobblestone": 50, "iron": 30, "gold": 16, "diamond": 4},
    "gold":    {"cobblestone": 40, "iron": 30, "gold": 22, "diamond": 8},
    "diamond": {"cobblestone": 20, "iron": 35, "gold": 30, "diamond": 15},
}

# Define an ordering for tiers so we can pick the best one
TIER_ORDER = ["wood", "stone", "iron", "gold", "diamond"]

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
        await ctx.send(f"You’re still mining! Try again in {retry}s.")
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
        await ctx.send(f"You’re still farming! Try again in {retry}s.")
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
            WHERE user_id = $1                                 
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
    """Show your barn: mobs broken out by rarity, with colored embed."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        # fetch barn contents
        rows = await conn.fetch(
            "SELECT mob_name, count FROM barn WHERE user_id=$1 AND count>0",
            user_id
        )
        # fetch current size & next upgrade (if you want to include it)
        size = await conn.fetchval(
            "SELECT barn_size FROM players WHERE user_id=$1",
            user_id
        ) or 5

    if not rows:
        return await ctx.send("Your barn is empty!")

    # organize by rarity
    by_rarity = {}
    for r in range(1, 6):
        by_rarity[r] = []

    for record in rows:
        name  = record["mob_name"]
        cnt   = record["count"]
        rar   = MOBS[name]["rarity"]
        by_rarity[rar].append(f"• **{name}** × {cnt}")

    # pick the highest rarity present for the embed color
    highest = max((r for r, lst in by_rarity.items() if lst), default=1)
    embed_color = COLOR_MAP[ RARITIES[highest]["colour"] ]

    embed = discord.Embed(
        title=f"{ctx.author.display_name}'s Barn ({size} slots)",
        color=embed_color
    )
    embed.set_footer(text="Use !upbarn to expand your barn.")

    # Add a field per rarity that actually has mobs
    for rar, lines in by_rarity.items():
        if not lines:
            continue
        info = RARITIES[rar]
        # e.g. “Common (1)”
        field_name = f"{info['name'].title()} [{rar}]"
        embed.add_field(name=field_name, value="\n".join(lines), inline=False)

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
        await channel.send(f"😢 The wild **{mob_name}** ran away…")

async def spawn_mob_loop():
    await bot.wait_until_ready()
        # before your loop, compute these once:
    mob_names = list(MOBS.keys())
    rarities = [MOBS[name]["rarity"] for name in mob_names]
    max_r = max(rarities)

    # weight = (max_r + 1) – rarity  → commons get highest weight
    weights = [(2**(max_r + 1)) - r for r in rarities]
    while True:
        # wait 4–20 minutes
        await asyncio.sleep(random.randint(1*60, 2*60))

        # pick channel & mob
        chan = bot.get_channel(random.choice(SPAWN_CHANNEL_IDS))
        mob = random.choices(mob_names, weights=weights, k=1)[0]
        try:
            src = Image.open(f"assets/mobs/{mob}.png").convert("RGBA")
        except FileNotFoundError:
            # fallback to text if image missing
            return await chan.send(f"A wild **{mob}** appeared! (no image found)")

        # send initial 1×1 pixel frame
        frame_sizes = [1, 2, 4, 8, 16, src.size[0]]  # final = full res width
        b = io.BytesIO()
        pixelate(src, frame_sizes[0]).save(b, format="PNG")
        b.seek(0)
        msg = await chan.send("A mob is appearing, say it's name to catch it", file=discord.File(b, f"spawn.png"))

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
        spawn_id = record["spawn_id"]
        bot.loop.create_task(
            watch_spawn_expiry(spawn_id=spawn_id,  # you'll fetch this below
                            channel_id=chan.id,
                            message_id=msg.id,
                            mob_name=mob,
                            expires_at=expires)
        )
                # step through each larger frame
        for size in frame_sizes[1:]:
            await asyncio.sleep(10)  # pause between frames
            b = io.BytesIO()
            pixelate(src, size).save(b, format="PNG")
            b.seek(0)
            await msg.edit(content="A mob is appearing, say it's name to catch it", attachments=[discord.File(b, f"spawn.png")])

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
