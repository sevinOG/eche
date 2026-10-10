"""
opt_in_manager.py

Source of truth = a channel in the home server category ``bot memory``:
    user-{USER_ID}
- opt-in = that channel exists
- opt-out = that channel is deleted
- hire.py and other cogs pipe through get_valid_members_for_guild()

Threads inside the channel hold context, bank, items, and workers.
opted_in.json is still written so older readers stay in sync.
"""

import os
import json
import discord
from typing import Set, List, Optional

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


async def get_home_guild(bot) -> Optional[discord.Guild]:
    from core.home_id import parse_home_server_id
    home_id_raw = os.getenv("HOME_SERVER_ID") or os.getenv("HOME_GUILD_ID") or os.getenv("HOME_SERVER") or "0"
    home_id = parse_home_server_id(home_id_raw)
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
    Source of truth: user-{id} channels in the bot memory category.
    Falls back to json if the home guild is not found.
    """
    from core.discord_store import list_user_ids

    guild = await get_home_guild(bot)
    if guild is None:
        return load_opted_in()
    return list_user_ids(guild)


async def get_valid_members_for_guild(bot, guild: discord.Guild) -> List[discord.Member]:
    """
    INTELLIGENT CHECK you asked for:
    1. Get invoker's server (guild param)
    2. Get its users
    3. Intersect with users who have a channel in bot memory
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


async def get_context_channel_for_user(bot, user_id: int):
    """Helper: the context thread for a user, if their channel already exists."""
    from core.discord_store import THREAD_CONTEXT, find_thread, user_channel

    guild = await get_home_guild(bot)
    if guild is None:
        return None
    channel = user_channel(guild, user_id)
    if channel is None:
        return None
    return await find_thread(channel, THREAD_CONTEXT)


# ----------------------------------------------------------------------
# Public API - keep same names as before so other cogs don't break
# ----------------------------------------------------------------------
async def ensure_user_category(bot, member):
    """Create the user's channel plus context and bank threads. Name kept for callers."""
    from core.context_manager import ensure_context_channel
    from core.discord_store import (
        BANK_HEADER,
        THREAD_BANK,
        ensure_record,
        ensure_user_thread,
        user_channel,
    )

    guild = await get_home_guild(bot)
    if guild is None:
        return None

    username = getattr(member, "name", None)
    _thread, _message = await ensure_context_channel(bot, guild, member.id, username)
    bank_thread = await ensure_user_thread(guild, member.id, THREAD_BANK, username)
    if bank_thread is not None:
        await ensure_record(bank_thread, BANK_HEADER, "BANK DATA\n0\nSTARTER:0")
    return user_channel(guild, member.id)


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
    """Deletes the user's channel in bot memory. Threads go with the channel."""
    from core.discord_store import delete_user_channel

    opted_ids = await get_opted_in_ids_from_home(bot)
    if member.id not in opted_ids and member.id not in load_opted_in():
        return False

    guild = await get_home_guild(bot)
    if guild is not None:
        await delete_user_channel(guild, member.id)

    # Also remove from legacy json
    legacy = load_opted_in()
    if member.id in legacy:
        legacy.remove(member.id)
        save_opted_in(legacy)

    return True
