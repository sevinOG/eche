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
        return "none", []
    pinned = await refresh_record(pinned)
    from core.context_manager import parse_pin_sections

    _label, summary, recent = parse_pin_sections(pinned.content or "", "")
    prose = (summary or "").strip()
    if not prose or prose == "(none yet)":
        prose = "none"
    return prose, [str(line).strip() for line in recent if str(line).strip()]


async def load_bot_context(bot, user_id, username=None):
    """
    Load this user's copy of the bot's self-context from the home server.
    Truncated to avoid Groq "prompt too long".
    """
    from core.discord_store import refresh_record

    channel, pinned = await ensure_bot_memory_channel(bot, user_id, username)
    if not pinned:
        return "none", []
    pinned = await refresh_record(pinned)
    from core.context_manager import parse_pin_sections

    _label, summary, recent = parse_pin_sections(pinned.content or "", "")
    prose = (summary or "").strip()
    if not prose or prose == "(none yet)":
        prose = "none"
    return prose, [str(line).strip() for line in recent if str(line).strip()]


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
    user_context, user_pin = await load_user_context(bot, user_id, username)
    bot_context, bot_pin = await load_bot_context(bot, user_id, username)
    from core.personal_recent import prompt_lines

    user_file = prompt_lines(user_id, "user") if user_id else []
    bot_file = prompt_lines(user_id, "bot") if user_id else []
    user_recent = _merge_rows(user_pin, user_file)
    bot_recent = _merge_rows(bot_pin, bot_file)
    episode = ""
    if server_id:
        from core.episodic import load_episodic, prompt_episode

        episode = prompt_episode(await load_episodic(bot, server_id))
    speaker = _speaker(username, user_id)
    latest = _latest_message(speaker, user_message, media_note, image_count)
    return fit_memory_prompt(
        today_stamp(),
        user_context,
        user_recent,
        bot_context,
        bot_recent,
        episode,
        latest,
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


_RECENT_LABELS = ("Recent user messages:", "Recent replies:")


def _recent_at(prompt: str) -> int:
    spots = [prompt.find(label) for label in _RECENT_LABELS]
    spots = [spot for spot in spots if spot >= 0]
    if not spots:
        return -1
    return min(spots)


def _clip_end(text: str, limit: int) -> str:
    """Shorten from the end on a sentence boundary. No ellipsis."""
    text = (text or "").rstrip()
    if len(text) <= limit:
        return text
    if limit < 1:
        return ""
    window = text[:limit].rstrip()
    cut = max(window.rfind("."), window.rfind("!"), window.rfind("?"))
    if cut >= 40:
        return window[: cut + 1].rstrip()
    return window


def _filled(rows) -> list[str]:
    return [str(row).strip() for row in (rows or []) if str(row).strip()]


def _merge_rows(*groups) -> list[str]:
    """Older pin lines first, then the local file. A repeated line is kept once."""
    merged: list[str] = []
    for group in groups:
        for row in _filled(group):
            if row not in merged:
                merged.append(row)
    return merged


def _summary_block(label: str, summary: str) -> str:
    """Long-term prose only, clipped the same way as the server episode."""
    from core.episodic import PROMPT_CHARS, _clip_prose

    prose = " ".join((summary or "").split())
    if not prose or prose == "none":
        prose = "none"
    else:
        prose = _clip_prose(prose, PROMPT_CHARS) or "none"
    return f"{label}{prose}"


def _recent_block(title: str, rows: list[str]) -> str:
    if not rows:
        return ""
    return f"{title} Do not answer these on their own.\n" + "\n\n".join(rows)


def render_memory_prompt(
    today: str,
    user_summary: str,
    user_rows: list[str],
    bot_summary: str,
    bot_rows: list[str],
    episode: str,
    latest: str,
) -> str:
    """Summaries first. The rolling messages sit with the turn being answered."""
    head = "\n\n".join(
        [
            f"Today: {today}",
            _summary_block(_PAST_USER, user_summary),
            _summary_block(_PAST_BOT, bot_summary),
        ]
    )
    tail_parts = []
    recent_bits = [
        bit
        for bit in (
            _recent_block("Recent user messages:", user_rows),
            _recent_block("Recent replies:", bot_rows),
        )
        if bit
    ]
    if recent_bits:
        tail_parts.append("\n\n".join(recent_bits))
    episode = (episode or "").strip()
    if episode:
        tail_parts.append(_PAST_SERVER + episode)
    tail_parts.append((latest or "").strip())
    tail = "\n\n".join(part for part in tail_parts if part)
    if not tail:
        return head.strip()
    return f"{head}\n\n{tail}".strip()


def fit_memory_prompt(
    today: str,
    user_summary: str,
    user_rows: list[str],
    bot_summary: str,
    bot_rows: list[str],
    episode: str,
    latest: str,
    limit: int = 3500,
) -> str:
    """Keep the latest turn and the rolling messages. Shorten the clouds first."""
    user_kept = _filled(user_rows)
    bot_kept = _filled(bot_rows)
    while True:
        text = render_memory_prompt(
            today,
            user_summary,
            user_kept,
            bot_summary,
            bot_kept,
            episode,
            latest,
        )
        if len(text) <= limit:
            return text
        at = _recent_at(text)
        tail = text[at:] if at >= 0 else ""
        if len(tail) > limit and user_kept:
            user_kept.pop(0)
            continue
        if len(tail) > limit and bot_kept:
            bot_kept.pop(0)
            continue
        return _fit_prompt(text, limit)


def _fit_prompt(prompt: str, limit: int = 3500) -> str:
    """Stay under the cap. The latest message and server episode are kept first.

    Older personal memory fills whatever room is left. A long attachment note
    is shortened before the episode block is dropped. When the rolling buffer
    is marked, that buffer stays and the long-term clouds are shortened.
    """
    if len(prompt) <= limit:
        return prompt
    recent_at = _recent_at(prompt)
    if recent_at >= 0:
        head = prompt[:recent_at].rstrip()
        tail = prompt[recent_at:]
        if len(tail) >= limit:
            return tail[-limit:]
        budget = limit - len(tail) - 2
        if budget < 1:
            return tail
        if len(head) > budget:
            head = _clip_end(head, budget)
        if not head:
            return tail
        return f"{head}\n\n{tail}"
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
