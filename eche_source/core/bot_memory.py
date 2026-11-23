# bot_memory.py
# The bot's self context for one user lives in that user's context thread,
# beside the user's own context message.

from core.context_manager import (
    HOME_SERVER_ID,
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


async def remember_bot(bot, user_id, username, text: str) -> None:
    """Store one full reply in this user's local buffer. Both sides of the
    turn are stored now, so this is where a due memory update starts."""
    from core.context_manager import get_home_guild, remember_side

    await remember_side(
        bot,
        get_home_guild(bot),
        user_id,
        username,
        text,
        side="bot",
    )
