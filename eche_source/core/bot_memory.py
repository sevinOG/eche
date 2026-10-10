# bot_memory.py
# The bot's self context for one user lives in that user's context thread,
# beside the user's own context message.

from core.context_manager import (
    HOME_SERVER_ID,
    _compact_cloud_if_due,
    fit_memory_pin,
    get_home_guild,
    parse_pin_sections,
)
from core.debuglog import dprint
from core.discord_store import bot_memory_header
from core.memory_lines import stored_line


def _label(username, user_id) -> str:
    """Same name the user pin uses: the global username, else the id."""
    name = " ".join(str(username or "").split())
    if name:
        return name
    if user_id is not None:
        return str(user_id)
    return "them"


async def ensure_bot_memory_channel(bot, user_id, username=None):
    """
    The bot's self-context message for this user, in their context thread.
    Same Summary / New shape as the user pin, under its own title.
    Returns (thread, message). Both are None when the home guild is missing.
    """
    guild = get_home_guild(bot)
    if guild is None:
        dprint(
            "[bot_memory] guild is None — check HOME_SERVER_ID "
            f"({HOME_SERVER_ID}) / bot is in that server"
        )
        return None, None
    if user_id is None:
        dprint("[bot_memory] user_id is required — self context is per user")
        return None, None

    from core.discord_store import THREAD_CONTEXT, adopt_bot_record, ensure_user_thread

    thread = await ensure_user_thread(guild, user_id, THREAD_CONTEXT, username)
    if thread is None:
        return None, None
    message = await adopt_bot_record(thread, _label(username, user_id))
    if message is None:
        return thread, None
    return thread, message


def _memory_line(reply_text: str) -> str:
    """One New: line. The caller already wrote the short sentence."""
    return stored_line(reply_text)


def bot_lines_in_new_section(content: str) -> list[str]:
    """Lines in New:, ignoring the long-term cloud. Older BOT: lines count too."""
    _header, _summary, recent = parse_pin_sections(content or "", "")
    return [line for line in recent if line.strip()]


async def log_bot_event(bot, user_id, reply_text, username=None):
    """
    Appends one short line inside this user's self-context New: section.

    Does not fold. Call this only after the Discord reply is already
    out, and call archive_bot_recents_if_due after this write. A fold on
    that turn consumes the recent lines. Nothing is appended after it.
    """

    # 1. Ensure the user's context thread + bot message exist
    channel, pinned = await ensure_bot_memory_channel(bot, user_id, username)
    if not channel or not pinned:
        dprint("ERROR: Could not update bot memory.")
        return

    from core.discord_store import refresh_record

    header = bot_memory_header(_label(username, user_id))
    pinned = await refresh_record(pinned)
    label, summary, recent = parse_pin_sections(pinned.content or header, header)
    recent.append(_memory_line(reply_text).strip())
    summary = await _compact_cloud_if_due(label, summary)
    new_content = fit_memory_pin(label, summary, recent)
    if len(new_content) > 1990:
        dprint("WARNING: Bot memory still exceeds the pin limit after fitting.")

    try:
        from core.discord_store import edit_record
        await edit_record(pinned, new_content)
    except Exception as e:
        dprint("ERROR editing bot memory:", e)


async def archive_bot_recents_if_due(bot, user_id, username=None):
    """After the reply is sent, fold three stored lines into that user's cloud.

    The next reply then becomes the first line in an empty New: block.
    A failed summary leaves the lines in New: so they are not dropped.
    """
    from core.context_summarizer import ARCHIVE_AFTER_MESSAGES, summarize_context

    guild = get_home_guild(bot)
    channel, pinned = await ensure_bot_memory_channel(bot, user_id, username)
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
        user_id,
        username,
        override_header=bot_memory_header(_label(username, user_id)),
        keep_recent=0,
    )


async def remember_bot(bot, user_id, username, text: str) -> None:
    """Store one full reply in this user's local buffer."""
    from core.context_manager import get_home_guild, remember_side

    await remember_side(
        bot,
        get_home_guild(bot),
        user_id,
        username,
        text,
        side="bot",
    )
