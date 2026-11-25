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
    Structured memory (memory_store) is used once it exists; until then the
    older prose on the pin.
    """
    structured = _structured(user_id, "user")
    if structured is not None:
        return structured, []
    guild = get_home_guild(bot)
    from core.discord_store import fetch_live

    channel, pinned = await ensure_context_channel(bot, guild, user_id, username)
    if not pinned:
        return "none", []
    try:
        pinned = await fetch_live(pinned)
    except Exception as exc:
        from core.debuglog import dprint

        dprint(f"[builder] user context fetch failed: {exc}")
        return "none", []
    if pinned is None:
        return "none", []
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
    structured = _structured(user_id, "bot")
    if structured is not None:
        return structured, []
    from core.discord_store import fetch_live

    channel, pinned = await ensure_bot_memory_channel(bot, user_id, username)
    if not pinned:
        return "none", []
    try:
        pinned = await fetch_live(pinned)
    except Exception as exc:
        from core.debuglog import dprint

        dprint(f"[builder] bot context fetch failed: {exc}")
        return "none", []
    if pinned is None:
        return "none", []
    from core.context_manager import parse_pin_sections

    _label, summary, recent = parse_pin_sections(pinned.content or "", "")
    prose = (summary or "").strip()
    if not prose or prose == "(none yet)":
        prose = "none"
    return prose, [str(line).strip() for line in recent if str(line).strip()]


def _structured(user_id, side: str) -> str | None:
    """Rendered JSON memory for the prompt, or None when there is none yet."""
    try:
        from core import memory_store

        profile = memory_store.load(int(user_id), side)
    except Exception:
        return None
    if profile is None:
        return None
    from core.context_caps import get

    skip = ()
    if side == "bot":
        try:
            from core.standing import skip_texts
            skip = skip_texts(profile)
        except Exception:
            skip = ()
    return memory_store.prompt_text(
        profile, side, get("memory_summary_prompt_chars"), skip_texts=skip
    ) or "none"


_BACKGROUND = "Background. Use this only when the message at the end needs it."
_ABOUT_USER = "About this person:\n"
# The bot pin is your own record of what you said and did with this person.
_ABOUT_BOT = "Your own past outputs with this person (what you said and did; stay consistent):\n"
_THIS_SERVER = "This server:\n"


def _episodic_in_prompt() -> bool:
    """The Memory & Prompts checkbox. Blank stays on. Storage is unchanged."""
    try:
        from core.secrets import public_value
        from core.tools import prompt_choice
        return prompt_choice(public_value("prompt_episodic"), default=True)
    except Exception:
        return True


async def build_prompt(
    bot,
    guild,
    user_id,
    username,
    user_message,
    media_note: str = "",
    image_count: int = 0,
    server_id=None,
    channel_id=None,
):
    """
    User turn for Eche: today, the two memory pins, this server's episode, and this message.
    Personal memory is always loaded from the home server.
    `server_id` is the guild the person is speaking in.

    username MUST be the user's global username (member.name),
    not nickname, not display_name.
    """
    standing = ""
    try:
        from core.standing import instructions_for_prompt
        standing = instructions_for_prompt(user_id, user_message)
    except Exception:
        standing = ""
    user_context, user_pin = await load_user_context(bot, user_id, username)
    bot_context, bot_pin = await load_bot_context(bot, user_id, username)
    from core.personal_recent import prompt_lines

    user_file = prompt_lines(user_id, "user") if user_id else []
    bot_file = prompt_lines(user_id, "bot") if user_id else []
    user_recent = _merge_rows(user_pin, user_file)
    bot_recent = _merge_rows(bot_pin, bot_file)
    episode = ""
    if server_id and _episodic_in_prompt():
        from core.episodic import recall_for_prompt

        try:
            episode = await recall_for_prompt(bot, server_id, user_id, channel_id, user_message)
        except Exception as exc:
            from core.debuglog import dprint

            dprint(f"[builder] episode recall failed: {exc}")
            episode = ""
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
        standing=standing,
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


_RECENT_THEM = "Recent messages from them:"
_RECENT_YOU = "Your own recent replies to them, as you posted them:"
_RECORD_NOTE = (
    "(A bracketed [I used ...] line is the app's record of a tool that really ran. "
    "Never write one yourself.)"
)
_RECENT_LABELS = (_RECENT_THEM, _RECENT_YOU)


def _recent_at(prompt: str) -> int:
    spots = [prompt.find(label) for label in _RECENT_LABELS]
    try:
        from core.standing import PROMPT_LABEL
        spots.append(prompt.find(PROMPT_LABEL))
    except Exception:
        pass
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


def summary_prompt_chars() -> int:
    """Settings → Context & Memory → Long-term summary in the prompt."""
    from core.context_caps import get

    return get("memory_summary_prompt_chars")


def memory_prompt_chars() -> int:
    """Settings → Context & Memory → Whole memory prompt."""
    from core.context_caps import get

    return get("memory_prompt_chars")


def _summary_block(label: str, summary: str, limit: int | None = None) -> str:
    """Long-term summary only, clipped the same way as the server episode."""
    from core.episodic import _clip_prose

    prose = " ".join((summary or "").split())
    if not prose or prose == "none":
        prose = "none"
    else:
        room = summary_prompt_chars() if limit is None else int(limit)
        prose = _clip_prose(summary, room) or "none"
    return f"{label}{prose}"


def _recent_block(title: str, rows: list[str]) -> str:
    if not rows:
        return ""
    return f"{title}\n" + "\n\n".join(rows)


def render_memory_prompt(
    today: str,
    user_summary: str,
    user_rows: list[str],
    bot_summary: str,
    bot_rows: list[str],
    episode: str,
    latest: str,
    standing: str = "",
) -> str:
    """Summaries first. The rolling messages sit with the turn being answered.

    Memory text is filtered here: no mentions, ids, or slurs. The latest
    message is the person's own words and is left as written.
    """
    from core.content_filter import clean_lines, clean_memory_text

    user_summary = clean_memory_text(user_summary or "") or "none"
    bot_summary = clean_memory_text(bot_summary or "") or "none"
    user_rows = clean_lines(user_rows)
    bot_rows = clean_lines(bot_rows)
    # Eche's own old "I don't have eyes" / "watching it now" lines and typed
    # "[Watched ...]" records are not repeated back to it (live 2026-11-25).
    from core.watch_auto import scrub_rows, scrub_text

    bot_rows = scrub_rows(bot_rows)
    if any("[I used " in row for row in bot_rows):
        bot_rows = [*bot_rows, _RECORD_NOTE]
    bot_summary = scrub_text(bot_summary) or "none"
    episode = clean_memory_text(episode or "")
    head = "\n\n".join(
        [
            f"Today: {today}",
            _BACKGROUND,
            _summary_block(_ABOUT_USER, user_summary),
            _summary_block(_ABOUT_BOT, bot_summary),
        ]
    )
    tail_parts = []
    recent_bits = [
        bit
        for bit in (
            _recent_block(_RECENT_THEM, user_rows),
            _recent_block(_RECENT_YOU, bot_rows),
        )
        if bit
    ]
    if recent_bits:
        tail_parts.append("\n\n".join(recent_bits))
    episode = (episode or "").strip()
    if episode:
        tail_parts.append(_THIS_SERVER + episode)
    block = (standing or "").strip()
    if block:
        tail_parts.append(block)
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
    limit: int | None = None,
    standing: str = "",
) -> str:
    """Keep the latest turn and the rolling messages. Shorten the clouds first.

    limit defaults to Settings → Context & Memory → Whole memory prompt.
    """
    if limit is None:
        limit = memory_prompt_chars()
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
            standing=standing,
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
    label = _THIS_SERVER
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
