# context_manager.py
# User memory channels live on the HOME guild.
# The user does NOT need to be a member of that guild — we only need their id/name.

import discord
import os

from core.debuglog import dprint

# Always load HOME_SERVER_ID safely with a fallback
HOME_SERVER_ID = int(os.getenv("HOME_SERVER_ID", "0"))


def get_home_guild(bot):
    return bot.get_guild(HOME_SERVER_ID)


def _display_name(member, user, username=None, user_id=None) -> str:
    """
    Best-effort display name.
    member may be None (user not in home guild / not in cache).
    user may be a Member or User from fetch_user.
    """
    if username:
        return str(username)

    if member is not None:
        nick = getattr(member, "nick", None)
        if nick:
            return nick
        disp = getattr(member, "display_name", None)
        if disp:
            return disp

    if user is not None:
        global_name = getattr(user, "global_name", None)
        if global_name:
            return global_name
        uname = getattr(user, "username", None)
        if uname:
            return uname

    return str(user_id) if user_id is not None else "unknown"


async def ensure_context_channel(bot, guild, user_id, username=None):
    """
    Ensures a memory category, context channel, and pinned message exist
    for a user.

    guild must be the home guild (channel creation target).
    The target user does NOT need to be a member of that guild.
    """
    if guild is None:
        dprint(
            "[context_manager] guild is None — check HOME_SERVER_ID / bot is in home server"
        )
        return None, None

    category_name = f"memory-{user_id}"
    category = discord.utils.get(guild.categories, name=category_name)

    if not category:
        dprint(f"[context_manager] Creating category: {category_name}")
        try:
            category = await guild.create_category(category_name)
        except Exception as e:
            dprint(f"[context_manager] ERROR creating category {category_name}: {e}")
            return None, None

    channel_name = "context"
    channel = discord.utils.get(category.channels, name=channel_name)

    if not channel:
        dprint(f"[context_manager] Creating context channel for {user_id}")
        try:
            channel = await category.create_text_channel(channel_name)
        except Exception as e:
            dprint(f"[context_manager] ERROR creating channel for {user_id}: {e}")
            return None, None

    try:
        pins = await channel.pins()
    except Exception as e:
        dprint(f"[context_manager] ERROR fetching pins for {user_id}: {e}")
        pins = []

    if pins:
        return channel, pins[0]

    # Resolve a name without requiring membership in the home guild
    member = guild.get_member(user_id) if guild else None
    user = member
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception as e:
            dprint(f"[context_manager] fetch_user({user_id}) failed: {e}")
            user = None

    display_name = _display_name(member, user, username=username, user_id=user_id)
    header = f"Context for {display_name}:\n"

    dprint(f"[context_manager] Creating pinned message for {user_id} ({display_name})")
    try:
        msg = await channel.send(header)
        await msg.pin()
    except Exception as e:
        dprint(f"[context_manager] ERROR creating/pinning context for {user_id}: {e}")
        return channel, None

    return channel, msg


async def update_context(bot, guild, user_id, message_text, username=None):
    """
    Appends a user's message inside the New: section.
    Triggers summarization every 3rd message, just like bot memory.

    Works even when the user is not a member of the home guild.
    """
    if guild is None:
        dprint(
            f"[context_manager] ERROR: guild is None — cannot update context for {user_id}. "
            f"Set HOME_SERVER_ID and ensure the bot is in that server."
        )
        return

    channel, pinned = await ensure_context_channel(bot, guild, user_id, username)
    if not channel or not pinned:
        dprint(f"[context_manager] ERROR: Could not update user context for {user_id}.")
        return

    # Resolve display name (membership optional)
    member = guild.get_member(user_id) if guild else None
    user = member
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            user = None

    display_name = _display_name(member, user, username=username, user_id=user_id)
    header = f"Context for {display_name}:\n\n"

    content = pinned.content or header

    # Ensure structure exists
    if "Summary:" not in content or "New:" not in content:
        content = (
            header
            + "Summary:\n(none yet)\n\nNew:\n"
        )

    try:
        summary_start = content.index("Summary:") + len("Summary:")
        new_start = content.index("New:")
    except ValueError:
        content = (
            header
            + "Summary:\n(none yet)\n\nNew:\n"
        )
        summary_start = content.index("Summary:") + len("Summary:")
        new_start = content.index("New:")

    before_summary = content[:summary_start]
    after_summary = content[summary_start:new_start]
    new_section = content[new_start:]

    if not new_section.endswith("\n"):
        new_section += "\n"

    new_section = new_section + f"USER: {message_text}\n"

    new_content = before_summary + after_summary + new_section

    if len(new_content) > 1990:
        dprint(
            f"[context_manager] WARNING: User context exceeded limit before "
            f"summarization for {user_id}. Resetting."
        )
        new_content = (
            header
            + "Summary:\n(none yet)\n\nNew:\n"
            + f"USER: {message_text}\n"
        )

    try:
        await pinned.edit(content=new_content)
    except Exception as e:
        dprint(f"[context_manager] ERROR editing pinned message for {user_id}: {e}")

    # Check message count in New: section and trigger summarizer every 3rd message
    try:
        new_lines = [
            line
            for line in new_section.splitlines()
            if line.strip() and not line.startswith("New:")
        ]
        dprint(
            f"[context_manager] User {user_id} lines in New: section: {len(new_lines)}"
        )
        if len(new_lines) >= 3:
            dprint(
                f"[context_manager] Reached 3+ messages in user {user_id} New:, "
                f"triggering summarizer."
            )
            from core.context_summarizer import summarize_context

            await summarize_context(
                bot,
                guild,
                user_id,
                username,
            )
    except Exception as e:
        dprint(
            f"[context_manager] ERROR checking user summarization trigger "
            f"for {user_id}: {e}"
        )