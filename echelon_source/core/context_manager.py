# context_manager.py

import discord
import os

# Always load HOME_SERVER_ID safely with a fallback
HOME_SERVER_ID = int(os.getenv("HOME_SERVER_ID", "0"))


def get_home_guild(bot):
    return bot.get_guild(HOME_SERVER_ID)


async def ensure_context_channel(bot, guild, user_id, username=None):
    """
    Ensures a memory category, context channel, and pinned message exist
    for a user (NOT Sevin).
    """

    category_name = f"memory-{user_id}"
    category = discord.utils.get(guild.categories, name=category_name)

    if not category:
        print(f"[context_manager] Creating category: {category_name}")
        category = await guild.create_category(category_name)

    channel_name = "context"
    channel = discord.utils.get(category.channels, name=channel_name)

    if not channel:
        print(f"[context_manager] Creating context channel for {user_id}")
        channel = await category.create_text_channel(channel_name)

    pins = await channel.pins()

    if pins:
        return channel, pins[0]

    # ⭐ SAFE DISPLAY NAME (fixes weird username bug)
    member = guild.get_member(user_id)
    user = member or await bot.fetch_user(user_id)

    display_name = (
        member.nick
        or user.global_name
        or user.username
    )

    header = f"Context for {display_name}:\n"

    print(f"[context_manager] Creating pinned message for {user_id}")
    msg = await channel.send(header)
    await msg.pin()

    return channel, msg


async def update_context(bot, guild, user_id, message_text, username=None):
    """
    Appends a user's message inside the New: section.
    Triggers summarization every 3rd message, just like bot memory.
    """

    channel, pinned = await ensure_context_channel(bot, guild, user_id, username)
    if not channel or not pinned:
        print(f"[context_manager] ERROR: Could not update user context for {user_id}.")
        return

    # Determine header
    member = guild.get_member(user_id) if guild else None
    user = member or await bot.fetch_user(user_id)
    display_name = username or (member.nick or user.global_name or user.username) if (member or user) else str(user_id)
    header = f"Context for {display_name}:\n\n"

    content = pinned.content or header

    # Ensure structure exists
    if "Summary:" not in content or "New:" not in content:
        content = (
            header +
            "Summary:\n(none yet)\n\nNew:\n"
        )

    try:
        summary_start = content.index("Summary:") + len("Summary:")
        new_start = content.index("New:")
    except ValueError:
        content = (
            header +
            "Summary:\n(none yet)\n\nNew:\n"
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
        print(f"[context_manager] WARNING: User context exceeded limit before summarization for {user_id}. Resetting.")
        new_content = (
            header +
            "Summary:\n(none yet)\n\nNew:\n" +
            f"USER: {message_text}\n"
        )

    try:
        await pinned.edit(content=new_content)
    except Exception as e:
        print(f"[context_manager] ERROR editing pinned message for {user_id}: {e}")

    # Check message count in New: section and trigger summarizer every 3rd message
    try:
        new_lines = [line for line in new_section.splitlines() if line.strip() and not line.startswith("New:")]
        print(f"[context_manager] User {user_id} lines in New: section: {len(new_lines)}")
        if len(new_lines) >= 3:
            print(f"[context_manager] Reached 3+ messages in user {user_id} New:, triggering summarizer.")
            from core.context_summarizer import summarize_context
            await summarize_context(
                bot,
                guild,
                user_id,
                username
            )
    except Exception as e:
        print(f"[context_manager] ERROR checking user summarization trigger for {user_id}: {e}")
