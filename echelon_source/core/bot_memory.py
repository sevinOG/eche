# bot_memory.py

import discord
from core.context_manager import get_home_guild
from core.context_summarizer import summarize_context

BOT_HEADER = "Self Conversation Data (Group Setting):\n\n"


async def ensure_bot_memory_channel(bot):
    """
    Ensures the bot's memory ALWAYS lives in the HOME SERVER.
    """
    guild = get_home_guild(bot)

    category_name = "bot-memory"
    category = discord.utils.get(guild.categories, name=category_name)
    if not category:
        print("Creating category:", category_name)
        category = await guild.create_category(category_name)

    channel_name = "context"
    channel = discord.utils.get(category.channels, name=channel_name)
    if not channel:
        print("Creating bot context channel")
        channel = await category.create_text_channel(channel_name)

    pins = await channel.pins()
    if pins:
        # Ensure header spacing is correct
        pinned = pins[0]
        if not pinned.content.startswith(BOT_HEADER):
            fixed = BOT_HEADER + pinned.content.split("Summary:", 1)[-1]
            await pinned.edit(content=fixed)
        return channel, pins[0]

    # Create a clean pinned message with correct spacing
    print("Creating pinned bot context message")
    msg = await channel.send(
        BOT_HEADER +
        "Summary:\n(none yet)\n\nNew:\n"
    )
    await msg.pin()
    return channel, msg


async def log_bot_event(bot, reply_text):
    """
    Appends the bot's reply INSIDE the New: section.
    Tracks message count to trigger summarization every 3rd message.
    """

    guild = get_home_guild(bot)

    # 1. Ensure channel + pinned exist
    channel, pinned = await ensure_bot_memory_channel(bot)
    if not channel or not pinned:
        print("ERROR: Could not update bot memory.")
        return

    content = pinned.content or BOT_HEADER

    # -----------------------------------------------------
    # 2. Ensure structure exists
    # -----------------------------------------------------
    if "Summary:" not in content or "New:" not in content:
        content = (
            BOT_HEADER +
            "Summary:\n(none yet)\n\nNew:\n"
        )

    # -----------------------------------------------------
    # 3. Slice into sections
    # -----------------------------------------------------
    try:
        summary_start = content.index("Summary:") + len("Summary:")
        new_start = content.index("New:")
    except ValueError:
        print("ERROR: Bot context malformed.")
        return

    before_summary = content[:summary_start]
    after_summary = content[summary_start:new_start]
    new_section = content[new_start:]

    # -----------------------------------------------------
    # 4. Insert BOT line inside the New: section
    # -----------------------------------------------------
    if not new_section.endswith("\n"):
        new_section += "\n"

    new_section = new_section + f"BOT: {reply_text}\n"

    # -----------------------------------------------------
    # 5. Rebuild pinned message
    # -----------------------------------------------------
    new_content = before_summary + after_summary + new_section

    # Safety: avoid Discord 2000-char limit
    if len(new_content) > 1990:
        print("WARNING: Bot memory exceeded limit before summarization. Resetting.")
        new_content = (
            BOT_HEADER +
            "Summary:\n(none yet)\n\nNew:\n" +
            f"BOT: {reply_text}\n"
        )

    try:
        await pinned.edit(content=new_content)
    except Exception as e:
        print("ERROR editing bot memory:", e)

    # -----------------------------------------------------
    # 6. Check message count in New: section and trigger summarizer every 3rd message
    # -----------------------------------------------------
    try:
        new_lines = [line for line in new_section.splitlines() if line.strip() and not line.startswith("New:")]
        print(f"[bot_memory] Current lines in New: section: {len(new_lines)}")
        if len(new_lines) >= 3:
            print(f"[bot_memory] Reached 3+ messages in New:, triggering summarizer.")
            await summarize_context(
                bot,
                guild,
                bot.user.id,
                None,
                override_header=BOT_HEADER
            )
    except Exception as e:
        print(f"[bot_memory] ERROR checking summarization trigger: {e}")
