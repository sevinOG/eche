# builder.py 1

from core.admin_tools import configured_owner_ids
from core.context_manager import ensure_context_channel, get_home_guild
from core.bot_memory import ensure_bot_memory_channel
from core.today import today_stamp

async def load_user_context(bot, user_id, username):
    """
    ALWAYS load user context from the HOME SERVER.
    Username MUST be the global username (member.name), never nickname.
    Truncated to avoid Groq "prompt too long".
    """
    guild = get_home_guild(bot)
    from core.discord_store import refresh_record

    channel, pinned = await ensure_context_channel(bot, guild, user_id, username)
    if not pinned:
        return "none"
    pinned = await refresh_record(pinned)
    content = (pinned.content or "").strip()
    if len(content) > 1500:
        content = content[-1500:]
    return content


async def load_bot_context(bot, user_id, username=None):
    """
    Load this user's copy of the bot's self-context from the home server.
    Truncated to avoid Groq "prompt too long".
    """
    from core.discord_store import refresh_record

    channel, pinned = await ensure_bot_memory_channel(bot, user_id, username)
    if not pinned:
        return "none"
    pinned = await refresh_record(pinned)
    content = (pinned.content or "").strip()
    if len(content) > 1500:
        content = content[-1500:]
    return content


_PAST_USER = "Past user memory. Do not answer this on its own.\n"
_PAST_BOT = "Past your memory. Do not answer this on its own.\n"
_PAST_SERVER = "Past server talk. Do not answer this on its own.\n"


def _past_pin(content: str) -> str:
    """Prompt copy. Stored pins keep their Summary: and New: headers."""
    out = []
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped == "Summary:":
            out.append("Past summary:")
        elif stripped == "New:":
            out.append("Past messages:")
        else:
            out.append(line)
    return "\n".join(out)


def render_stack(
    today: str,
    user_context: str,
    bot_context: str,
    latest: str,
    episode: str = "",
) -> str:
    """User turn only. Voice and tool rules live in the one system message."""
    parts = [
        f"Today: {today}",
        _PAST_USER + _past_pin(user_context),
        _PAST_BOT + _past_pin(bot_context),
    ]
    episode = (episode or "").strip()
    if episode:
        parts.append(_PAST_SERVER + episode)
    parts.append(latest)
    return "\n\n".join(parts).strip()


async def build_prompt(
    bot,
    guild,
    user_id,
    username,
    user_message,
    media_note: str = "",
    image_count: int = 0,
    server_id=None,
):
    """
    User turn for Eche: today, the two memory pins, this server's episode, and this message.
    Personal memory is always loaded from the home server.
    `server_id` is the guild the person is speaking in.

    username MUST be the user's global username (member.name),
    not nickname, not display_name.
    """
    user_context = await load_user_context(bot, user_id, username)
    bot_context = await load_bot_context(bot, user_id, username)
    episode = ""
    if server_id:
        from core.episodic import load_episodic, prompt_episode

        episode = prompt_episode(await load_episodic(bot, server_id))
    speaker = _speaker(username, user_id)
    latest = _latest_message(speaker, user_message, media_note, image_count)
    return _fit_prompt(
        render_stack(today_stamp(), user_context, bot_context, latest, episode)
    )


def _latest_message(speaker: str, user_message: str, media_note: str, image_count: int) -> str:
    """One block for the turn to answer. Pictures are named as part of it."""
    words = (user_message or "").strip() or "(no text)"
    lines = [
        "=== MOST RECENT MESSAGE ===",
        f"{speaker}:",
        words,
    ]
    note = (media_note or "").strip()
    if note:
        lines.extend(["", "Attached to this message:", note])
    count = int(image_count or 0)
    if count > 0:
        word = "picture" if count == 1 else "pictures"
        lines.extend([
            "",
            f"{count} {word} follow this message. They belong to this turn, not the memory above.",
        ])
    return "\n".join(lines)


def _speaker(username: str, user_id) -> str:
    """Owner tag only when this id is saved in Settings → Security."""
    name = (username or "").strip() or "user"
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return name
    if uid and uid in configured_owner_ids():
        return f"{name} [owner]"
    return name


def _fit_prompt(prompt: str, limit: int = 3500) -> str:
    """Stay under the cap. The latest message and server episode are kept first.

    Older personal memory fills whatever room is left. A long attachment note
    is shortened before the episode block is dropped.
    """
    if len(prompt) <= limit:
        return prompt
    marker = "=== MOST RECENT MESSAGE ==="
    idx = prompt.find(marker)
    if idx < 0:
        return prompt[:limit]
    tail = prompt[idx:]
    head = prompt[:idx].rstrip()
    episode = ""
    label = _PAST_SERVER
    # Only the block this builder appended. The same words inside a memory
    # pin stay part of that pin and can still be shortened.
    at = head.rfind("\n\n" + label)
    if at >= 0:
        candidate = head[at + 2 + len(label) :].strip()
        if candidate and "\n\n" not in candidate:
            episode = candidate
            head = head[:at].rstrip()
    middle = f"{label}{episode}\n\n" if episode else ""
    if len(middle) >= limit:
        middle = middle[: limit - 1].rstrip() + "\n"
        return middle
    message_room = limit - len(middle)
    if len(tail) > message_room:
        tail = tail[:message_room]
    gap = "\n\n" if head and middle else ("\n" if head else "")
    budget = limit - len(middle) - len(tail) - len(gap)
    if budget < 0:
        budget = 0
    if len(head) > budget:
        head = head[:budget].rstrip()
    if not head:
        return f"{middle}{tail}"
    return f"{head}{gap}{middle}{tail}"
