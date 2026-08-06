# opt_in_manager.py

import json
import os
import discord

CONTEXT_CHANNEL_NAME = "context"
ECONOMY_CHANNEL_NAME = "economy"


def _opt_in_path() -> str:
    """Always resolve opted_in.json under the writable user/project root."""
    try:
        from core.paths import user_dir
        root = user_dir()
    except Exception:
        try:
            from core.paths import user_dir
            root = user_dir()
        except Exception:
            root = os.getcwd()
    return os.path.join(root, "opted_in.json")


def load_opted_in():
    path = _opt_in_path()
    if not os.path.exists(path):
        # Legacy: also check cwd for older installs
        if os.path.exists("opted_in.json"):
            path = "opted_in.json"
        else:
            return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(int(x) for x in data)
    except Exception:
        return set()


def save_opted_in(opted_in_set):
    path = _opt_in_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(int(x) for x in opted_in_set), f, indent=2)


async def ensure_user_category(bot, member):
    guild = bot.get_guild(int(os.getenv("HOME_SERVER_ID")))
    if guild is None:
        return None

    category_name = f"memory-{member.id}"

    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name)

    context_channel = discord.utils.get(category.text_channels, name=CONTEXT_CHANNEL_NAME)
    if context_channel is None:
        context_channel = await category.create_text_channel(CONTEXT_CHANNEL_NAME)
        msg = await context_channel.send("CONTEXT DATA\n{}")
        await msg.pin()

    economy_channel = discord.utils.get(category.text_channels, name=ECONOMY_CHANNEL_NAME)
    if economy_channel is None:
        economy_channel = await category.create_text_channel(ECONOMY_CHANNEL_NAME)
        msg = await economy_channel.send("BANK DATA\n0\nSTARTER:0")
        await msg.pin()

    return category


async def opt_in(bot, member):
    opted_in = load_opted_in()

    if member.id in opted_in:
        return False  # already opted in

    await ensure_user_category(bot, member)

    opted_in.add(member.id)
    save_opted_in(opted_in)
    return True


async def opt_out(member):
    opted_in = load_opted_in()

    if member.id not in opted_in:
        return False

    opted_in.remove(member.id)
    save_opted_in(opted_in)
    return True
