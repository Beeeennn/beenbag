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

async def init_util(dab_pool):
    pass
async def giverole(ctx, id, user):
    role = ctx.guild.get_role(id)
    if not role:
        return logging.info("⚠️ Role not found in this server.")

    if role in user.roles:
        return logging.info("user already has role.")
    try:
        await user.add_roles(role)
    except discord.Forbidden:
        logging.info("❌ I don't have permission to give that role.")
    except Exception as e:
        logging.info(f"❌ Something went wrong: `{e}`")

async def sucsac(ctx, user, mob_name: str, is_gold, note, conn):
    """
    gives the correct reward for a mob and all of its emeralds
    """
    user_id = user.id
    key = mob_name.title()

    rarity = MOBS[key]["rarity"]
    rar_info = RARITIES[rarity]
    reward  = rar_info["emeralds"]
    color   = COLOR_MAP[rar_info["colour"]]

    #check sword
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
        title=f"🗡️ {user.display_name} sacrificed a {'✨ Golden ' if is_gold else ''} {key} {note}",
        description=f"You gained 💠 **{reward} Emerald{'s' if reward!=1 else ''}**!",
        color=color
    )
    embed.add_field(name="Rarity", value=rar_info["name"].title(), inline=True)
    if is_gold:
        embed.set_footer(text="Golden mobs drop double emeralds!")
    await ctx.send(embed=embed)
    return reward

async def resolve_member(ctx: commands.Context, query: str) -> discord.Member | None:
    """
    Resolve a string to a Member by:
      1) MemberConverter (handles mentions, IDs, name#disc, nicknames)
      2) guild.fetch_member() for raw IDs
      3) case‐insensitive match on display_name or name
    """
    # 1) try the built-in converter
    try:
        return await commands.MemberConverter().convert(ctx, query)
    except commands.BadArgument:
        pass

    guild = ctx.guild
    if not guild:
        return None

    q = query.strip()

    # 2) raw mention or ID
    m = re.match(r"<@!?(?P<id>\d+)>$", q)
    if m:
        uid = int(m.group("id"))
    elif q.isdigit():
        uid = int(q)
    else:
        uid = None

    if uid is not None:
        # a) cached?
        member = guild.get_member(uid)
        if member:
            return member
        # b) fetch from API
        try:
            return await guild.fetch_member(uid)
        except discord.NotFound:
            return None

    # 3) name or display_name (case‐insensitive)
    ql = q.lower()
    for m in guild.members:
        if m.display_name.lower() == ql or m.name.lower() == ql:
            return m

    return None
def get_level_from_exp(exp: int) -> int:
    # find the highest level whose threshold is <= exp
    lvl = 0
    for level, req in LEVEL_EXP.items():
        if exp >= req and level > lvl:
            lvl = level
    return lvl

async def gain_exp(conn, bot, user_id: int, exp_gain: int, message: discord.Message = None):
    # 1) Update experience in DB
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

async def ensure_player(conn, user_id):
        await conn.execute(
            "INSERT INTO new_players (user_id) VALUES ($1) ON CONFLICT DO NOTHING;",
            user_id
        )

async def take_items(user_id: int, item: str, amount: int,conn):
    # Check current quantity
    row = await conn.fetchrow("""
        SELECT quantity FROM player_items
        WHERE player_id = $1 AND item_name = $2
    """, user_id, item)

    if not row or row["quantity"] < amount:
        raise ValueError(f"User {user_id} does not have enough of '{item}' (has {row['quantity'] if row else 0})")

    new_qty = row["quantity"] - amount

    if new_qty > 0:
        await conn.execute("""
            UPDATE player_items
            SET quantity = $1
            WHERE player_id = $2 AND item_name = $3
        """, new_qty, user_id, item)
    else:
        await conn.execute("""
            DELETE FROM player_items
            WHERE player_id = $1 AND item_name = $2
        """, user_id, item)

async def give_items(user_id: int, item: str, amount: int, cat, useable, conn):
    # Check if item already exists
    row = await conn.fetchrow("""
        SELECT quantity FROM player_items
        WHERE player_id = $1 AND item_name = $2
    """, user_id, item)

    if row:
        new_qty = row["quantity"] + amount
        await conn.execute("""
            UPDATE player_items
            SET quantity = $1
            WHERE player_id = $2 AND item_name = $3
        """, new_qty, user_id, item)
    else:
        await conn.execute("""
            INSERT INTO player_items (player_id, item_name, category, quantity, useable)
            VALUES ($1, $2, $3, $4, $5)
        """, user_id, item, cat, amount, useable)

async def get_items(conn,user_id, item):

    row = await conn.fetchrow("""
        SELECT quantity FROM player_items
        WHERE player_id = $1 AND item_name = $2
    """, user_id, item)
    if not row:
        return 0
    else:
        return row["quantity"]

async def give_mob(conn,user_id, mob):
    key = mob.title()
    await conn.execute(
        """
        INSERT INTO barn (user_id, mob_name, is_golden, count)
        VALUES ($1, $2, false, 1)
        ON CONFLICT (user_id, mob_name, is_golden)
        DO UPDATE SET count = barn.count + 1;
        """,
        user_id, key
    )
    # 7) Fetch new total
    new_count = await conn.fetchval(
        """
        SELECT count
            FROM barn
            WHERE user_id=$1 AND mob_name=$2 AND is_golden=false
        """,
        user_id, key
    )
    return new_count
