import os
import asyncio
import logging
import random
import discord
from discord.ext import commands
import asyncpg
from aiohttp import web
from PIL import Image, ImageOps
import io
import dateparser

from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import string
import secrets
from stronghold import PathButtons
from constants import *
from utils import *
import aiohttp

async def upload_to_catbox(image_bytes: bytes, filename: str = "image.png") -> str:
    """Upload image bytes to catbox.moe and return the URL."""
    url = "https://catbox.moe/user/api.php"
    data = aiohttp.FormData()
    data.add_field("reqtype", "fileupload")
    data.add_field("fileToUpload", image_bytes, filename=filename, content_type="image/png")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to upload to Catbox: {resp.status}")
            return await resp.text()
async def init_cc(dab_pool):
    global db_pool
    db_pool = dab_pool

async def make_link_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

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
async def c_linkyt(ctx, channel_name: str):
    """
    Generate a one-time code to link your YouTube channel.
    Usage: !linkyt <your YouTube channel name>
    """
    user_id = ctx.author.id
    code = await make_link_code(8)
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

async def c_yt(ctx, who = None):
    """
    Show the YouTube channel linked to a user.
    Usage:
      !yt             → your own channel
      !yt @Someone    → their channel
    """
    """Show your current level and progress toward the next level."""
    # Resolve who → Member (or fallback to author)
    if who is None:
        member = ctx.author
    else:
        member = await resolve_member(ctx, who)
        if member is None:
            return await ctx.send("Member not found.")  # or "Member not found."
    user_id = member.id
    # 0) Restrict to LINK_CHANNELS
    if ctx.channel.id not in LINK_CHANNELS:
        return await ctx.send("❌ You can’t do that here.")

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
        if member == ctx.author:
            return await ctx.send(
                "You haven’t linked a YouTube channel! Use `!linkyt <channel name>`."
            )
        else:
            return await ctx.send(f"{member.display_name} hasn’t linked YT yet.")

    # 4) Build URL
    name = row["yt_channel_name"]
    cid  = row["yt_channel_id"]
    if cid:
        url = f"https://www.youtube.com/channel/{cid}"
    else:
        url = f"https://www.youtube.com/c/{name.replace(' ', '')}"

    # 5) Send embed
    embed = discord.Embed(
        title=f"{member.display_name}'s YouTube",
        url=url, color=discord.Color.red()
    )
    embed.add_field(name="Channel Name", value=name or "–", inline=True)
    embed.add_field(name="Link", value=f"[Watch on YouTube]({url})", inline=True)
    await ctx.send(embed=embed)

async def c_give(ctx, who: str, mob: str):
    """
    Usage: !give <player> <mob>
    Attempts to give one <mob> from your barn to <player>.
    If their barn is full, the mob is sacrificed for emeralds instead.
    """
    member = await resolve_member(ctx, who)
    if not member:
        return await ctx.send("There is no user with this name")  # eye‐roll if no such user
    if member.id == ctx.author.id:
        return await ctx.send("❌ You can’t give to yourself.")

    mob_name = mob.title()
    if mob_name not in MOBS:
        return await ctx.send(f"❌ `{mob_name}` isn’t a known mob.")

    user_id   = ctx.author.id
    target_id = member.id

    async with db_pool.acquire() as conn:
        # 2) Fetch target’s barn capacity and current fill
        row = await conn.fetchrow(
            "SELECT barn_size FROM new_players WHERE user_id = $1",
            target_id
        )
        target_size = row["barn_size"] if row else 5
        total_in_barn = await conn.fetchval(
            "SELECT COALESCE(SUM(count), 0) FROM barn WHERE user_id = $1",
            target_id
        )

        # 3) Fetch one mob from your barn (prefer non-golden)
        rec = await conn.fetchrow(
            """
            SELECT is_golden, count
              FROM barn
             WHERE user_id = $1
               AND mob_name = $2
             ORDER BY is_golden ASC
             LIMIT 1
            """,
            user_id, mob_name
        )
        if not rec:
            return await ctx.send(f"❌ You have no **{mob_name}** to give.")
        is_golden = rec["is_golden"]
        have      = rec["count"]

        # 4) Remove it from your barn
        if have > 1:
            await conn.execute(
                """
                UPDATE barn
                   SET count = count - 1
                 WHERE user_id = $1
                   AND mob_name = $2
                   AND is_golden = $3
                """,
                user_id, mob_name, is_golden
            )
        else:
            await conn.execute(
                """
                DELETE FROM barn
                WHERE user_id = $1
                AND mob_name = $2
                AND is_golden = $3
                """,
                user_id, mob_name, is_golden
            )

        # 5) If recipient has room, transfer it
        if total_in_barn < target_size:
            await conn.execute(
                """
                INSERT INTO barn (user_id, mob_name, is_golden, count)
                VALUES ($1, $2, $3, 1)
                ON CONFLICT (user_id, mob_name, is_golden) DO UPDATE
                  SET count = barn.count + 1
                """,
                target_id, mob_name, is_golden
            )
            return await ctx.send(
                f"✅ You gave {'✨ ' if is_golden else ''}**{mob_name}** to {member.mention}!"
            )

        # 6) Otherwise, sacrifice it for emeralds to *you*
        rarity = MOBS[mob_name]["rarity"]
        base   = RARITIES[rarity]["emeralds"]
        reward = base * (2 if is_golden else 1)

        sucsac(ctx,ctx.author,mob_name,is_golden,f"because {member.display_name}'s barn was full",conn)

    await ctx.send(
        f"⚠️ {member.display_name}`s barn is full, so you sacrificed "
        f"{'✨ ' if is_golden else ''}**{mob_name}** for 💠 **{reward}** emeralds!"
    )
async def c_recipe(ctx, args):
    user_id = ctx.author.id
    if not args:
        return await ctx.send("❌ Usage: `!recipe <tool> [tier]`")

    # Build tool name from all but last arg; tier is last arg if 2+ args
    if len(args) == 1:
        tool_raw = args[0]
        tier = None
    else:
        tool_raw = "_".join(args[:-1])
        tier = args[-1].lower()

    tool = tool_raw.replace(" ", "_").lower()

    # If it’s the fishing rod, force tier to “wood”
    if tool in ("fishing_rod", "fishingrod", "fishing","rod"):
        tool = "fishing_rod"

    if tier is None:
        return await ctx.send("❌ You must specify a tier for that tool.")

    key = (tool, tier)
    if key not in CRAFT_RECIPES:
        return await ctx.send("❌ Invalid recipe. Try `!recipe pickaxe iron` or `!recipe totem`.")

    wood_cost, ore_cost, ore_col, uses = CRAFT_RECIPES[key]
    need = [f"**{wood_cost} wood**"]
    if ore_col:
        need.append(f"**{ore_cost} {ore_col}**")
    return await ctx.send(f"You need { ' and '.join(need) } to craft a {tool}.")
    

async def c_craft(ctx, args):
    """
    Usage: !craft <tool> <tier> 
    Usage:
      !craft <tool>              → fishing rod only
      !craft <tool> <tier>       → other tools
    Examples:
      !craft fishing rod
      !craft pickaxe iron
    """
    user_id = ctx.author.id
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
    if tool in ("fishing_rod", "fishingrod", "fishing","rod"):
        tool = "fishing_rod"

    if tool == "totem":
        cost = 2
        tier = "diamond"
        async with db_pool.acquire() as conn:
            await ensure_player(conn,ctx.author.id)
            # Fetch their resources
            ore_have = await get_items(conn, user_id, "diamond")
            if ore_have < cost:
                return await ctx.send(f"❌ You need {cost} diamonds to craft that.")
            await give_items(user_id,"totem", 1, "items", False, conn)
            await take_items(user_id, "diamond", cost, conn)
        return await ctx.send(f"🔨 You crafted a **totem**, you will now be get an extra life in a stronghold. You can only use one per run.")

    if tier is None:
        return await ctx.send("❌ You must specify a tier for that tool.")

    key = (tool, tier)
    if key not in CRAFT_RECIPES:
        return await ctx.send("❌ Invalid recipe. Try `!craft pickaxe iron` or `!craft fishing rod`.")

    wood_cost, ore_cost, ore_col, uses = CRAFT_RECIPES[key]

    async with db_pool.acquire() as conn:
        await ensure_player(conn,ctx.author.id)
        # Fetch their resources
        row = await conn.fetchrow("""SELECT
                MAX(CASE WHEN item_name = 'wood' THEN quantity ELSE 0 END) AS wood,
                MAX(CASE WHEN item_name = $2 THEN quantity ELSE 0 END) AS ore_have
                FROM player_items
                WHERE player_id = $1 AND item_name IN ('wood', $2);""",
                user_id,ore_col)
        wood_have, ore_have = row["wood"], row["ore_have"]

        if wood_have < wood_cost or ore_have < ore_cost:
            need = [f"**{wood_cost} wood**"]
            if ore_col:
                need.append(f"**{ore_cost} {ore_col}**")
            return await ctx.send(f"❌ You need { ' and '.join(need) } to craft that.")

        # Deduct resources
        await take_items(user_id,"wood",wood_cost,conn)
        if ore_col:
            await take_items(user_id,ore_col,ore_cost,conn)

        # Give the tool
        await conn.execute("""
            INSERT INTO tools (user_id, tool_name, tier, uses_left)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, tool_name, tier) DO UPDATE
              SET uses_left = tools.uses_left + EXCLUDED.uses_left;
        """, user_id, tool, tier, uses)

    await ctx.send(f"🔨 You crafted a **{tier.title()} {tool.replace('_',' ').title()}** with {uses} uses!")
async def c_shop(ctx):
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


async def c_breed(ctx, mob: str):
    """Breed a mob (costs wheat & requires 2 in your barn)."""
    user_id = ctx.author.id
    key     = mob.title()

    # 1) Validate mob exists and is non-hostile
    if key not in MOBS:
        return await ctx.send(f"❌ `{mob}` isn’t a valid mob.")
    if MOBS[key]["hostile"]:
        return await ctx.send(f"❌ You can’t breed a hostile mob like **{key}**.")
    
    wheat = RARITIES[MOBS[key]["rarity"]]["wheat"]

    async with db_pool.acquire() as conn:
        await ensure_player(conn, user_id)

        # 2) Check wheat balance
        wheat_have = await get_items(conn, user_id, "wheat")
        if wheat_have < wheat:
            return await ctx.send(
                f"❌ You need **{wheat} wheat** to breed, but only have **{wheat_have}**."
            )

        # 3) Check barn count for that mob (non-golden)
        have = await conn.fetchval(
            """
            SELECT count
              FROM barn
             WHERE user_id=$1 AND mob_name=$2 AND is_golden=false
            """,
            user_id, key
        ) or 0
        if have < 2:
            return await ctx.send(
                f"❌ You need at least **2** **{key}** in your barn to breed, but only have **{have}**."
            )

        # 4) Check barn space
        occupancy = await conn.fetchval(
            "SELECT COALESCE(SUM(count), 0) FROM barn WHERE user_id = $1",
            user_id
        )
        barn_size = await conn.fetchval(
            "SELECT barn_size FROM new_players WHERE user_id = $1",
            user_id
        )
        if occupancy >= barn_size:
            return await ctx.send(
                f"❌ Your barn is full (**{occupancy}/{barn_size}**). Upgrade it before breeding more mobs!"
            )

        # 5) Deduct wheat and breed
        await take_items(user_id, "wheat", wheat, conn)
        new_count = await give_mob(conn, user_id, key)

    # 6) Success
    await ctx.send(
        f"🐣 {ctx.author.mention} bred a **{key}**! "
        f"You now have **{new_count}** **{key}** in your barn."
    )

async def c_farm(ctx):
    """Farm for wheat, better hoe means more wheat."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(conn,ctx.author.id)

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

        await give_items(user_id,"wheat",drop,"resource",False,conn)
        # fetch new total
        total = await get_items(conn,user_id,"wheat")

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


async def c_buy(ctx, args):
    """
    Purchase one or more of an item.
    Usage:
      !buy <item name> [quantity]
    Examples:
      !buy Exp Bottle 5
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
    elif raw_name in ("pack", "mob pack", "mystery mob pack"):
        lookup_name = "mystery animal"
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
        have = await get_items(conn,user_id,"emeralds")

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
        await take_items(user_id,"emeralds",total_cost,conn)

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
    async with db_pool.acquire() as conn:
        if display_name == "Exp Bottle":
            await ctx.send(f"✅ Spent {total_cost} 💠 for an Exp Bottle with **{qty} EXP**! Say **!use Exp Bottle** to use them, you must use them all at once though")
            await give_items(user_id,"Exp Bottle",qty,"items",True,conn)

        elif display_name == "Boss Mob Ticket":
            await ctx.send(
                f"✅ You bought **{qty} Boss Mob Ticket{'s' if qty!=1 else ''}**! "
                "Use `!use Ticket <mob name>` before stream to redeem, this allows you to say the name of the mob during the stream to spawn it, don't worry about typos, it will still be valid."
            )
            await give_items(user_id,"Boss Mob Ticket",qty,"items",True,conn)

        elif display_name == "Mystery Animal":
            await ctx.send(
                f"✅ You bought **{qty} Mystery Mob Pack{'s' if qty!=1 else ''}**! "
                "Use `!use Mob Pack` to redeem"
            )
            await give_items(user_id,"Mystery Mob Pack",qty,"items",True,conn)

        #     got = []
        #     mobs = ([m for m,v in MOBS.items() if not v["hostile"]])
        #     rarities = [MOBS[name]["rarity"] for name in mobs]
        #     max_r = max(rarities)
        #     weights = [(2**(max_r + 1-r)) for r in rarities]
            
        #     for _ in range(qty):
                    
        #         mobs = ([m for m,v in MOBS.items() if not v["hostile"]])
        #         mob = random.choices(mobs, weights=weights, k=1)[0]
        #         got.append(mob)
        #         await conn.execute(
        #             """
        #             INSERT INTO barn (user_id,mob_name,count)
        #             VALUES ($1,$2,1)
        #             ON CONFLICT (user_id,mob_name) DO UPDATE
        #               SET count = barn.count + 1
        #             """, user_id, mob
        #         )
        # # summarize what they got
        # summary = {}
        # for m in got:
        #     summary[m] = summary.get(m, 0) + 1
        # lines = [f"**{cnt}× {name}**" for name,cnt in summary.items()]
        # await ctx.send(f"✅ Mystery pack delivered:\n" + "\n".join(lines))


        elif display_name == "RICH Role":
            await giverole(ctx,1396839599921168585,ctx.author)
            await ctx.send(f"✅ You bought **RICH role** for {total_cost} 💠!, you must be super rich. Be careful not to buy it again")
        else:
            await ctx.send(f"✅ You bought **{qty}× {display_name}** for {total_cost} 💠!")


async def c_exp_cmd(ctx, who: str = None):
    """Show your current level and progress toward the next level."""
    # Resolve who → Member (or fallback to author)
    if who is None:
        member = ctx.author
    else:
        member = await resolve_member(ctx, who)
        if member is None:
            return await ctx.send("Member not found.")  # or "Member not found."

    # Now you’ve got a real Member with .id, .display_name, etc.
    user_id = member.id

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
        title=f"{member.display_name}'s Progress",
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

async def c_leaderboard(ctx,bot):
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

async def c_givemob(ctx, who, mob_name: str, count: int = 1):
    mob_name = mob_name.lower()
    member = await resolve_member(ctx, who)
    
    # Validate mob
    if mob_name.title() not in MOBS:
        return await ctx.send(f"❌ Mob `{mob_name}` not found.")
    
    # Validate count
    if count <= 0:
        return await ctx.send("❌ Count must be greater than 0.")

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO barn (user_id, mob_name, count)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, mob_name) DO UPDATE
              SET count = barn.count + $3
            """,
            member.id, mob_name.title(), count
        )
    
    await ctx.send(f"✅ Gave {count}× `{mob_name}` to {member.mention}.")

async def c_sac(ctx, mob_name: str):
    """
    Sacrifice one mob from your barn for emeralds based on rarity.
    Usage: !sacrifice <mob name>
    """
    user_id = ctx.author.id
    key = mob_name.title()
    # Check for special @beennn sacrifice case
    if mob_name.lower() in ("@beeeenjaminnn", "<@674671907626287151>", "been","beenn"):  # replace with their real user ID
        async with db_pool.acquire() as conn:
            diamond_count = await get_items(conn, user_id, "diamond")
            if diamond_count == 0:
                return await ctx.send("💎 You don’t even have a diamond to take **L**.")
            
            # Remove one diamond
            await take_items(user_id, "diamond", 1, conn)
            await ctx.send(f"💀 You were a fool to think you could sacrifice Beenn, he beat you in combat and took a diamond.")
            return
    # validate mob
    if key not in MOBS:
        return await ctx.send(f"❌ I don’t recognize **{mob_name}**.")
    rarity = MOBS[key]["rarity"]
    rar_info = RARITIES[rarity]
    reward  = rar_info["emeralds"]
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
        await sucsac(ctx,ctx.author,mob_name,is_gold,"",conn)

async def c_bestiary(ctx, who: str = None):
    """Show all mobs you’ve sacrificed, split by Golden vs. normal and by rarity."""

    # Resolve who → Member (or fallback to author)
    if who is None:
        member = ctx.author
    else:
        member = await resolve_member(ctx, who)
        if member is None:
            return await ctx.send("Member not found.")  # or "Member not found."

    # Now you’ve got a real Member with .id, .display_name, etc.
    user_id = member.id
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
        title=f"{member.display_name}'s Sacrifice Bestiary",
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


async def c_chop(ctx):
    """Gain 1 wood every 60s."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(conn,ctx.author.id)
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
        await give_items(user_id,"wood",num,"resource",False,conn)
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
        wood = await get_items(conn,user_id,"wood")

    await ctx.send(
        f"{ctx.author.mention} swung their axe and chopped 🌳 **{num} wood**! "
        f"You now have **{wood}** wood."
    )

async def c_mine(ctx):
    """Mine for cobblestone or ores; better pickaxes yield rarer drops."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(conn,ctx.author.id)
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

        ores = list(table.keys())
        weights = [table[ore]["chance"] for ore in ores]

        # Choose one ore based on weights
        chosen_ore = random.choices(ores, weights=weights, k=1)[0]

        # Get a random amount between min and max for that ore
        drop_info = table[chosen_ore]
        amount = random.randint(drop_info["min"], drop_info["max"])

        # 5) Grant the drop
        await give_items(user_id,chosen_ore,amount,"resource",False,conn)
        # fetch new total
        
        total = await get_items(conn, user_id, chosen_ore)

    # Prepare the final result text
    emojis = {"cobblestone":"🪨","iron":"🔩","gold":"🪙","diamond":"💎"}
    emoji = emojis.get(chosen_ore, "⛏️")
    result = (
        f"{ctx.author.mention} mined with a **{best_tier.title()} Pickaxe** and found "
        f"{emoji} **{amount} {chosen_ore}**! You now have **{total} {chosen_ore}**."
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


async def c_inv(ctx, who: str = None):
    """Show your inventory."""
    # Resolve member
    if who is None:
        member = ctx.author
    else:
        member = await resolve_member(ctx, who)
        if member is None:
            return await ctx.send("Member not found.")

    user_id = member.id

    # Fetch inventory
    async with db_pool.acquire() as conn:
        # 1. Items from new table
        items = await conn.fetch("""
            SELECT item_name, category, quantity
            FROM player_items
            WHERE player_id = $1 AND quantity > 0
        """, user_id)

        # 2. Tools
        tools = await conn.fetch("""
            SELECT tool_name, tier, uses_left
            FROM tools
            WHERE user_id = $1 AND uses_left > 0
        """, user_id)

        # 3. Emeralds (still in old table)
        emerald_row = await conn.fetchrow("""
            SELECT emeralds
            FROM accountinfo
            WHERE discord_id = $1
        """, user_id)
        emeralds = emerald_row["emeralds"] if emerald_row else 0

    # Empty check
    if not items and not tools and not emeralds:
        return await ctx.send(f"{member.mention}, your inventory is empty.")

    # Build embed
    embed = discord.Embed(
        title=f"{member.display_name}'s Inventory",
        color=discord.Color.green()
    )
    if member.avatar:
        embed.set_thumbnail(url=member.avatar.url)

    # Organize items by category
    from collections import defaultdict
    grouped = defaultdict(list)
    for row in items:
        grouped[row["category"]].append((row["item_name"], row["quantity"]))

    # Display resources, crops, mobs, etc.
    emojis = {
        "wood": "🌳", "cobblestone": "🪨", "iron": "🔩", "gold": "🪙", "diamond": "💎",
        "wheat": "🌾",
        "emeralds": "💠"
    }

    for category, entries in grouped.items():
        lines = []
        for name, qty in entries:
            emoji = emojis.get(name.lower(), "📦")
            label = name.replace("_", " ").title()
            lines.append(f"{emoji} **{label}**: {qty}")
        embed.add_field(
            name=category.capitalize(),
            value="\n".join(lines),
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
            tool_lines.append(f"{emoji} **{tier} {name}** — {uses} use{'s' if uses != 1 else ''}")
        embed.add_field(
            name="Tools",
            value="\n".join(tool_lines),
            inline=False
        )
    embed.set_footer(text="Use !shop to spend your emeralds & resources")
    await ctx.send(embed=embed)


async def c_barn(ctx, who: str = None):
    """Show your barn split by Golden vs. normal and by rarity."""
    # Resolve who → Member (or fallback to author)
    if who is None:
        member = ctx.author
    else:
        member = await resolve_member(ctx, who)
        if member is None:
            return await ctx.send("Member not found.")  # or "Member not found."

    # Now you’ve got a real Member with .id, .display_name, etc.
    user_id = member.id

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
            "SELECT barn_size FROM new_players WHERE user_id = $1", user_id
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
        title=f"{member.display_name}'s Barn ({size} slots)",
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

async def c_upbarn(ctx):
    """Upgrades your barn by +1 slot, costing (upgrades + 1) wood."""
    user_id = ctx.author.id

    async with db_pool.acquire() as conn:
        await ensure_player(conn,user_id)
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
            "SELECT barn_size FROM new_players WHERE user_id = $1",
            user_id
        )
        current_size = pl["barn_size"]

        player_wood = await get_items(conn, user_id, "wood")

        if player_wood < next_cost:
            return await ctx.send(
                f"{ctx.author.mention} you need **{next_cost} wood** to upgrade your barn, "
                f"but you only have **{player_wood} wood**."
            )

        # 6) Perform the upgrade
        await take_items(user_id,"wood",next_cost,conn)
        await conn.execute(
            """
            UPDATE barn_upgrades
               SET times_upgraded = times_upgraded + 1
             WHERE user_id = $1
            """,
            user_id
        )

        await conn.fetchrow(
            "UPDATE new_players SET barn_size = barn_size+1 WHERE user_id = $1",
            user_id
        )
        # 7) Fetch post‐upgrade values
        row = await conn.fetchrow(
            "SELECT barn_size FROM new_players WHERE user_id = $1",
            user_id
        )

        new_wood = await get_items(conn, user_id, "wood")
        new_size = row["barn_size"]

    # 8) Report back
    await ctx.send(
        f"{ctx.author.mention} upgraded their barn from **{current_size}** to **{new_size}** slots "
        f"for 🌳 **{next_cost} wood**! You now have **{new_wood} wood**."
    )
async def tint_image(image: Image.Image, tint: tuple[int, int, int]) -> Image.Image:
    """Tint an image with built-in shading (white/gray base), preserving shading."""
    image = image.convert("RGBA")
    width, height = image.size

    result = Image.new("RGBA", (width, height))

    for x in range(width):
        for y in range(height):
            r, g, b, a = image.getpixel((x, y))
            if a == 0:
                result.putpixel((x, y), (0, 0, 0, 0))
                continue

            brightness = r / 255
            tinted = tuple(int(c * brightness) for c in tint)
            result.putpixel((x, y), (*tinted, a))

    return result

async def make_fish(ctx,fish_path: str):

    user_id = ctx.author.id
    # Pick 2 distinct colors
    color_names = random.sample(list(MINECRAFT_COLORS.keys()), 2)
    color1 = MINECRAFT_COLORS[color_names[0]]
    color2 = MINECRAFT_COLORS[color_names[1]]
    typef = random.choice(FISHTYPES)
    async with db_pool.acquire() as conn:
        await ensure_player(conn,ctx.author.id)
        # 1) Fetch all usable rods
        rods = await conn.fetch(
            """
            SELECT tier, uses_left
              FROM tools
             WHERE user_id = $1
               AND tool_name = 'fishing_rod'
               AND uses_left > 0
            """,
            user_id
        )

        if not rods:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(
                "❌ You need a fishing rod with at least 1 use to mine! Craft one with `!craft fishing rod`."
            )
        # 2) Determine your highest tier pickaxe
        owned_tiers = {r["tier"] for r in rods}
        best_tier = None
        for tier in reversed(TIER_ORDER):
            if tier in owned_tiers:
                best_tier = tier
                break        
        # 3) Consume 1 use on that rod
        await conn.execute(
            """
            UPDATE tools
               SET uses_left = uses_left - 1
             WHERE user_id = $1
               AND tool_name = 'fishing_rod'
               AND tier = $2
               AND uses_left > 0
            """,
            user_id,best_tier
        )
        chance = random.randint(0,100)
        if chance>FISHINGCHANCE[best_tier]:
            
            await conn.execute(
                """
                INSERT INTO aquarium (user_id,color1,color2,type)
                VALUES ($1,$2,$3,$4)
                """,
                user_id,color_names[0],color_names[1],typef
            )
        else:
            return await ctx.send("You caught a sea pickle, yuck!!! you throw it back in the ocean")
            
    base_path = f"{fish_path}{typef}/base.png"
    overlay_path = f"{fish_path}{typef}/overlay.png"
    base = Image.open(base_path).convert("RGBA")
    overlay = Image.open(overlay_path).convert("RGBA")

    tinted_base =await tint_image(base, color1)
    tinted_overlay =await tint_image(overlay, color2)

    result = Image.alpha_composite(tinted_base, tinted_overlay)
    # 🔍 Scale up 20× using nearest neighbor to preserve pixel style
    scale = 20
    new_size = (result.width * scale, result.height * scale)
    result = result.resize(new_size, resample=Image.NEAREST)
    buf = io.BytesIO()
    result.save(buf, format="PNG")
    buf.seek(0)
    image_bytes = buf.getvalue()
    async with db_pool.acquire() as conn:
        media_id = await save_image_bytes(conn, image_bytes, "image/png")
    image_url = media_url(media_id)

    embed = discord.Embed(
        description=f"🎣 You used your **{best_tier} fishing rod** to catch a **{color_names[0]} and {color_names[1]} {typef}**!"
    )
    embed.set_image(url=image_url)
    await ctx.send(embed=embed)


async def c_generate_aquarium(ctx, who):
    background_path="assets/fish/aquarium.png"
    # Resolve who → Member (or fallback to author)
    if who is None:
        member = ctx.author
    else:
        member = await resolve_member(ctx, who)
        if member is None:
            return await ctx.send("Member not found.")  # or "Member not found."

    # Now you’ve got a real Member with .id, .display_name, etc.
    user_id = member.id
    async with db_pool.acquire() as conn:

        await conn.execute("""
            DELETE FROM aquarium
            WHERE time_caught < NOW() - INTERVAL '1 day'
        """)
        row = await conn.fetch("""
        SELECT color1, color2, type
        FROM aquarium                 
        WHERE user_id = $1
        ORDER BY time_caught DESC
        LIMIT 30    
                         """,
                         user_id)
    fish_specs = []
    for r in row:
        fish_specs += [[r["color1"],r["color2"],r["type"]]]

    unique_color1 = set(f[0] for f in fish_specs)
    unique_color2 = set(f[1] for f in fish_specs)
    unique_types  = set(f[2] for f in fish_specs)

    food = len(unique_color1) + len(unique_color2) + len(unique_types)
    if len(fish_specs) > 30:
        raise ValueError("You can only place up to 30 fish.")
    aquarium = Image.open(background_path).convert("RGBA")
    width, height = aquarium.size
    fish_size = 12
    edge_buffer = 6
    fish_buffer = 2
    placed_positions = []

    def is_valid_position(x, y):
        for px, py in placed_positions:
            if abs(x - px) < fish_size + fish_buffer and abs(y - py) < fish_size + fish_buffer:
                return False
        return True
    for spec in fish_specs:
        color1_name, color2_name, fish_type = spec
        color1 = MINECRAFT_COLORS.get(color1_name)
        color2 = MINECRAFT_COLORS.get(color2_name)
        if not color1 or not color2:
            print(f"⚠️ Invalid color name: {color1_name} or {color2_name}")
            continue
        base_path = f"assets/fish/{fish_type}/base.png"
        overlay_path = f"assets/fish/{fish_type}/overlay.png"
        if not (os.path.exists(base_path) and os.path.exists(overlay_path)):
            print(f"⚠️ Missing image for fish type: {fish_type}")
            continue
        base = Image.open(base_path).convert("RGBA")
        overlay = Image.open(overlay_path).convert("RGBA")
        tinted_base = await tint_image(base, color1)
        tinted_overlay = await tint_image(overlay, color2)
        fish_image = Image.alpha_composite(tinted_base, tinted_overlay)
        scale = 1
        new_size = (fish_image.width * scale, fish_image.height * scale)
        fish_image = fish_image.resize(new_size, resample=Image.NEAREST)

        # Randomly flip 50% of fish
        if random.choice([True, False]):
            fish_image = ImageOps.mirror(fish_image)

        # Place it
        tries = 0
        while tries < 1000:
            x = random.randint(edge_buffer, width - fish_size*scale - edge_buffer)
            y = random.randint(edge_buffer, height - fish_size*scale - edge_buffer)
            if is_valid_position(x, y):
                aquarium.alpha_composite(fish_image, (x, y))
                placed_positions.append((x, y))
                break
            tries += 1
        else:
            print(f"⚠️ Could not place fish {spec} after 1000 attempts")
    result = aquarium
    scale = 4
    new_size = (result.width * scale, result.height * scale)
    result = result.resize(new_size, resample=Image.NEAREST)
    buf = io.BytesIO()
    result.save(buf, format="PNG")
    buf.seek(0)
    image_bytes = buf.getvalue()
    async with db_pool.acquire() as conn:
        media_id = await save_image_bytes(conn, image_bytes, "image/png")
    image_url = media_url(media_id)

    embed = discord.Embed(
        title=f"{member.display_name}'s Aquarium",
        description=f"Generates **{food}** fish food every 30 minutes with **{len(fish_specs)}** fish"
    )
    embed.set_image(url=image_url)
    await ctx.send(embed=embed)


async def c_use(ctx, bot, item_name, quantity):
    user_id = ctx.author.id
    item_name = item_name.lower()

    if quantity <= 0:
        return await ctx.send("❌ Quantity must be greater than 0.")
    
    # allow "exp" shortcut for "Exp Bottle"
    if item_name in ("exp", "experience"):
        item_name = "exp bottle"
    elif item_name in ("pack", "mob pack", "mystery animal"):
        item_name = "mystery mob pack"
    elif item_name in ("boss ticket","ticket","mob ticket","boss mob"):
        item_name = "boss mob ticket"
    else:
        pass

    async with db_pool.acquire() as conn:
        # Check if they have it and it’s useable
        row = await conn.fetchrow("""
            SELECT quantity, useable
            FROM player_items
            WHERE player_id = $1 AND LOWER(item_name) = $2
        """, user_id, item_name)

        if not row:
            return await ctx.send(f"❌ You don’t have any **{item_name}**.")
        if not row["useable"]:
            return await ctx.send(f"❌ **{item_name}** cannot be used.")
        if row["quantity"] < quantity:
            return await ctx.send(f"❌ You only have {row['quantity']} **{item_name}**.")
        if item_name == "fish food" and quantity%100 != 0:
            return await ctx.send(f"❌ You must put an amount of fish food divisible by 100.")
        # Deduct quantity or delete
        remaining = row["quantity"] - quantity
        if remaining > 0:
            await conn.execute("""
                UPDATE player_items
                SET quantity = $1
                WHERE player_id = $2 AND LOWER(item_name) = $3
            """, remaining, user_id, item_name)
        else:
            await conn.execute("""
                DELETE FROM player_items
                WHERE player_id = $1 AND LOWER(item_name) = $2
            """, user_id, item_name)
    # 🎉 Effect (optional)
    if item_name == "mystery mob pack":
        got = []
        mobs = ([m for m,v in MOBS.items() if not v["hostile"]])
        rarities = [MOBS[name]["rarity"] for name in mobs]
        max_r = max(rarities)
        weights = [(2**(max_r + 1-r)) for r in rarities]
        async with db_pool.acquire() as conn:      
            for _ in range(quantity): 
                is_golden = (random.randint(0,20)==16)             
                mobs = ([m for m,v in MOBS.items() if not v["hostile"]])
                mob = random.choices(mobs, weights=weights, k=1)[0]
                
                await conn.execute(
                    "INSERT INTO barn_upgrades (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
                    user_id
                )
                # count current barn occupancy
                occ = await conn.fetchval(
                    "SELECT COALESCE(SUM(count),0) FROM barn WHERE user_id = $1",
                    user_id
                )
                size = await conn.fetchval(
                    "SELECT barn_size FROM new_players WHERE user_id = $1",
                    user_id
                )
                if occ >= size:
                    sac = True
                    reward = await sucsac(ctx.channel,ctx.author,mob,is_golden,"because the barn was too full",conn)
                    note = f"sacrificed for {reward} emeralds (barn is full)."
                    
                elif MOBS[mob]["hostile"]:
                    sac = True
                    reward = await sucsac(ctx.channel,ctx.author,mob,is_golden,"because the mob is hostile",conn)
                    note = f"this mob is not catchable so it was sacrificed for {reward} emeralds"
                else:
                    await give_mob(conn, user_id, mob)
                    got.append(mob)
                await asyncio.sleep(1)
        # summarize what they got
        summary = {}
        for m in got:
            summary[m] = summary.get(m, 0) + 1
        lines = [f"**{cnt}× {name}**" for name,cnt in summary.items()]
        await ctx.send(f"Mystery pack used:\n" + "\n".join(lines))
    elif item_name == "exp bottle":
        async with db_pool.acquire() as conn:
            await gain_exp(conn,bot,user_id,quantity)
    elif item_name == "fish food":        
        emeralds_to_give = quantity // 100
        async with db_pool.acquire() as conn:
            await give_items(user_id, "emeralds", emeralds_to_give,"emeralds",False,conn)
        await ctx.send(f"💠 You traded {quantity} fish food for {emeralds_to_give} emeralds!")
    elif item_name == "boss mob ticket":
        # ID of the user to ping (as a mention)
        special_user_id = 674671907626287151
        mention = f"<@{special_user_id}>"

        await ctx.send(f"🎫 You used a mob ticket! {mention}, a ticket has been claimed!")
        

    await ctx.send(f"✅ You used {quantity} **{item_name}**!")

async def c_stronghold(ctx):
    async with db_pool.acquire() as conn:
        cobble = await get_items(conn, ctx.author.id, "cobblestone")
        totems = await get_items(conn, ctx.author.id, "totem")
        if cobble < 6:
            return await ctx.send(f"❌ You need 6 cobblestone to enter")
        await take_items(ctx.author.id, "cobblestone", 6, conn)
    view = PathButtons(level=0, collected={}, player_id=ctx.author.id, db_pool=db_pool, totems=totems)
    embed = discord.Embed(
        title="Stronghold - Room 0",
        description="Choose a door to begin your descent...",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed, view=view)
