# bot_memory.py

import discord
from core.context_manager import HOME_SERVER_ID, get_home_guild
from core.debuglog import dprint
from core.today import today_day

BOT_HEADER = "Self Conversation Data (Group Setting):\n\n"


async def ensure_bot_memory_channel(bot):
    """
    Ensures the bot's memory ALWAYS lives in the HOME SERVER.
    """
    guild = get_home_guild(bot)
    if guild is None:
        dprint(
            "[bot_memory] guild is None — check HOME_SERVER_ID "
            f"({HOME_SERVER_ID}) / bot is in that server"
        )
        return None, None

    category_name = "bot-memory"
    category = discord.utils.get(guild.categories, name=category_name)
    if not category:
        dprint("Creating category:", category_name)
        category = await guild.create_category(category_name)

    channel_name = "context"
    channel = discord.utils.get(category.channels, name=channel_name)
    if not channel:
        dprint("Creating bot context channel")
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
    dprint("Creating pinned bot context message")
    msg = await channel.send(
        BOT_HEADER +
        "Summary:\n(none yet)\n\nNew:\n"
    )
    await msg.pin()
    return channel, msg


def bot_lines_in_new_section(content: str) -> list[str]:
    """BOT: lines stored in the recent block, ignoring the long-term summary."""
    text = content or ""
    if "New:" not in text:
        return []
    new_block = text.split("New:", 1)[1]
    return [line for line in new_block.splitlines() if line.startswith("BOT:")]


async def log_bot_event(bot, reply_text):
    """
    Appends the bot's reply inside the New: section.

    Does not summarize. Call archive_bot_recents_if_due after the Discord
    reply has already been sent.
    """

    # 1. Ensure channel + pinned exist
    channel, pinned = await ensure_bot_memory_channel(bot)
    if not channel or not pinned:
        dprint("ERROR: Could not update bot memory.")
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
        dprint("ERROR: Bot context malformed.")
        return

    before_summary = content[:summary_start]
    after_summary = content[summary_start:new_start]
    new_section = content[new_start:]

    # -----------------------------------------------------
    # 4. Insert BOT line inside the New: section
    # -----------------------------------------------------
    if not new_section.endswith("\n"):
        new_section += "\n"

    new_section = new_section + f"BOT: [{today_day()}] {reply_text}\n"

    # -----------------------------------------------------
    # 5. Rebuild pinned message
    # -----------------------------------------------------
    new_content = before_summary + after_summary + new_section

    # Safety: avoid Discord 2000-char limit
    if len(new_content) > 1990:
        dprint("WARNING: Bot memory exceeded limit before summarization. Resetting.")
        new_content = (
            BOT_HEADER +
            "Summary:\n(none yet)\n\nNew:\n" +
            f"BOT: [{today_day()}] {reply_text}\n"
        )

    try:
        await pinned.edit(content=new_content)
    except Exception as e:
        dprint("ERROR editing bot memory:", e)


async def archive_bot_recents_if_due(bot):
    """After the reply is sent, fold two stored BOT lines into long-term memory.

    The next reply then becomes the first line in an empty New: block.
    A failed summary leaves the lines in New: so they are not dropped.
    """
    from core.context_summarizer import ARCHIVE_AFTER_MESSAGES, summarize_context

    guild = get_home_guild(bot)
    channel, pinned = await ensure_bot_memory_channel(bot)
    if not channel or not pinned:
        return

    lines = bot_lines_in_new_section(pinned.content or "")
    dprint(f"[bot_memory] Recent BOT lines before archive: {len(lines)}")
    if len(lines) < ARCHIVE_AFTER_MESSAGES:
        return

    dprint(f"[bot_memory] Archiving {len(lines)} recent BOT lines into the long-term block.")
    await summarize_context(
        bot,
        guild,
        bot.user.id,
        None,
        override_header=BOT_HEADER,
        keep_recent=0,
    )
