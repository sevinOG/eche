"""
opt_in_manager.py V2
Source of truth = Discord categories in home server: memory-{USER_ID}
- opt-in = category exists
- opt-out = category deleted
- hire.py and other cogs pipe through get_valid_members_for_guild()

Keeps opted_in.json only for legacy migration.
"""

import os
import json
import discord
from typing import Set, List, Optional

CONTEXT_CHANNEL_NAME = "context"
ECONOMY_CHANNEL_NAME = "economy"
CATEGORY_PREFIX = "memory-"

# ----------------------------------------------------------------------
# Legacy path resolution (kept for backwards compat)
# ----------------------------------------------------------------------
def _opt_in_path() -> str:
    try:
        from core.paths import user_dir
        root = user_dir()
    except Exception:
        root = os.getcwd()
    return os.path.join(root, "opted_in.json")


def load_opted_in() -> Set[int]:
    """Legacy sync loader - falls back to cwd and returns set."""
    path = _opt_in_path()
    legacy_path = "opted_in.json"
    data_path = path

    if not os.path.exists(path) and os.path.exists(legacy_path):
        data_path = legacy_path
    elif not os.path.exists(path):
        return set()

    try:
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(int(x) for x in data)
    except Exception:
        return set()


def save_opted_in(opted_in_set: Set[int]):
    """Legacy save - keep json in sync for safety."""
    path = _opt_in_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(int(x) for x in opted_in_set), f, indent=2)
    except Exception:
        pass
    # Also save to cwd for older code that reads from there
    try:
        with open("opted_in.json", "w", encoding="utf-8") as f:
            json.dump(sorted(int(x) for x in opted_in_set), f, indent=2)
    except Exception:
        pass


# ----------------------------------------------------------------------
# New system - category parsing
# ----------------------------------------------------------------------
def _parse_memory_id(category: discord.CategoryChannel) -> Optional[int]:
    if not category.name.startswith(CATEGORY_PREFIX):
        return None
    try:
        # memory-286557627612397568 -> 286557627612397568
        raw = category.name.split("-", 1)[1]
        return int(raw)
    except (IndexError, ValueError):
        return None


async def get_home_guild(bot) -> Optional[discord.Guild]:
    home_id_raw = os.getenv("HOME_SERVER_ID") or os.getenv("HOME_GUILD_ID") or os.getenv("HOME_SERVER") or "0"
    try:
        home_id = int(home_id_raw)
    except ValueError:
        return None
    if home_id == 0:
        return None

    guild = bot.get_guild(home_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(home_id)
        except Exception:
            return None
    return guild


async def get_opted_in_ids_from_home(bot) -> Set[int]:
    """
    Source of truth: scan home server categories.
    Returns set of user IDs who have memory-* categories.
    Falls back to json if home guild not found.
    """
    guild = await get_home_guild(bot)
    if guild is None:
        return load_opted_in()

    opted = set()
    for cat in guild.categories:
        uid = _parse_memory_id(cat)
        if uid is not None:
            opted.add(uid)
    return opted


async def get_valid_members_for_guild(bot, guild: discord.Guild) -> List[discord.Member]:
    """
    INTELLIGENT CHECK you asked for:
    1. Get invoker's server (guild param)
    2. Get its users
    3. Intersect with opted-in users from home server context categories
    Returns sorted list of discord.Member
    """
    if guild is None:
        return []

    opted_in_ids = await get_opted_in_ids_from_home(bot)
    if not opted_in_ids:
        return []

    # Ensure member cache is populated - fixes empty dropdown bug
    if not getattr(guild, "chunked", True):
        try:
            await guild.chunk(cache=True)
        except Exception:
            # Fallback: fetch members via API if chunk fails
            try:
                async for member in guild.fetch_members(limit=None):
                    pass
            except Exception:
                pass

    valid: List[discord.Member] = []
    for member in guild.members:
        if member.bot:
            continue
        if member.id in opted_in_ids:
            valid.append(member)

    valid.sort(key=lambda m: m.display_name.lower())
    return valid


async def get_context_channel_for_user(bot, user_id: int) -> Optional[discord.TextChannel]:
    """Helper: get #context channel for a given user from home server."""
    guild = await get_home_guild(bot)
    if guild is None:
        return None
    category = discord.utils.get(guild.categories, name=f"{CATEGORY_PREFIX}{user_id}")
    if not category:
        return None
    return discord.utils.get(category.text_channels, name=CONTEXT_CHANNEL_NAME)


# ----------------------------------------------------------------------
# Public API - keep same names as before so other cogs don't break
# ----------------------------------------------------------------------
async def ensure_user_category(bot, member) -> Optional[discord.CategoryChannel]:
    guild = await get_home_guild(bot)
    if guild is None:
        return None

    category_name = f"{CATEGORY_PREFIX}{member.id}"
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        try:
            category = await guild.create_category(category_name, reason=f"opt-in {member.id}")
        except discord.Forbidden:
            return None

    # Ensure context channel
    context_channel = discord.utils.get(category.text_channels, name=CONTEXT_CHANNEL_NAME)
    if context_channel is None:
        try:
            context_channel = await category.create_text_channel(CONTEXT_CHANNEL_NAME)
            msg = await context_channel.send("CONTEXT DATA\n{}")
            try:
                await msg.pin()
            except Exception:
                pass
        except Exception:
            pass

    # Ensure economy channel
    economy_channel = discord.utils.get(category.text_channels, name=ECONOMY_CHANNEL_NAME)
    if economy_channel is None:
        try:
            economy_channel = await category.create_text_channel(ECONOMY_CHANNEL_NAME)
            msg = await economy_channel.send("BANK DATA\n0\nSTARTER:0")
            try:
                await msg.pin()
            except Exception:
                pass
        except Exception:
            pass

    return category


async def opt_in(bot, member) -> bool:
    """Returns True if newly opted in, False if already was."""
    opted_ids = await get_opted_in_ids_from_home(bot)
    if member.id in opted_ids:
        return False

    await ensure_user_category(bot, member)

    # Keep json in sync for legacy code
    legacy = load_opted_in()
    legacy.add(member.id)
    save_opted_in(legacy)
    return True


async def opt_out(bot, member) -> bool:
    """
    FIXED: Old version only removed from json, left category behind.
    Now deletes the memory-* category.
    """
    opted_ids = await get_opted_in_ids_from_home(bot)
    if member.id not in opted_ids and member.id not in load_opted_in():
        return False

    guild = await get_home_guild(bot)
    if guild is not None:
        category = discord.utils.get(guild.categories, name=f"{CATEGORY_PREFIX}{member.id}")
        if category is not None:
            try:
                # Delete all child channels first (Discord requires empty category)
                for channel in list(category.channels):
                    try:
                        await channel.delete(reason=f"opt-out {member.id}")
                    except Exception:
                        pass
                await category.delete(reason=f"opt-out {member.id}")
            except Exception:
                pass

    # Also remove from legacy json
    legacy = load_opted_in()
    if member.id in legacy:
        legacy.remove(member.id)
        save_opted_in(legacy)

    return True
