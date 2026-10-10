# context_manager.py
# User memory lives on the HOME guild, in bot memory / user-{id} / context.
# The user does NOT need to be a member of that guild — we only need their id/name.

import os

from core.debuglog import dprint
from core.home_id import home_server_id_from_env

# Always load HOME_SERVER_ID safely with a fallback.
# A pasted server-icon URL still resolves to the guild id inside it.
HOME_SERVER_ID = home_server_id_from_env()


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
    Ensures the user's context thread and both pinned messages exist.

    The thread lives at bot memory / user-{id} / context. It holds the user's
    context message and the bot's self-context message for that user.

    guild must be the home guild. The target user does not need to be a member.
    Returns (thread, user_context_message).
    """
    if guild is None:
        dprint(
            "[context_manager] guild is None — check HOME_SERVER_ID / bot is in home server"
        )
        return None, None

    from core.discord_store import (
        THREAD_CONTEXT,
        USER_CONTEXT_HEADER,
        adopt_bot_record,
        ensure_record,
        ensure_user_thread,
    )

    thread = await ensure_user_thread(guild, user_id, THREAD_CONTEXT, username)
    if thread is None:
        return None, None

    member = guild.get_member(user_id) if guild else None
    user = member
    if user is None and username is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception as e:
            dprint(f"[context_manager] fetch_user({user_id}) failed: {e}")
            user = None

    display_name = _display_name(member, user, username=username, user_id=user_id)
    header = f"Context for {display_name}:\n"
    user_message = await ensure_record(thread, USER_CONTEXT_HEADER, header)
    await adopt_bot_record(thread, display_name)
    if user_message is None:
        dprint(f"[context_manager] ERROR creating context record for {user_id}")
    return thread, user_message


async def read_raw_context(bot, user_id, username=None) -> str | None:
    """
    Full pinned memory for one user on the home guild.

    None when the pin cannot be read. The caller decides who `user_id` is.
    Chat tools must pass the speaker, not a model-supplied id.
    """
    guild = get_home_guild(bot)
    _channel, pinned = await ensure_context_channel(bot, guild, user_id, username)
    if pinned is None:
        return None
    return pinned.content or ""


def _user_line(message_text: str) -> str:
    """One New: line. The caller already wrote the short sentence."""
    from core.memory_lines import stored_line

    return stored_line(message_text)


def _recent_head(line: str) -> str:
    """The USER/BOT date tag, kept when a recent line has to be shortened."""
    if not (line.startswith("USER:") or line.startswith("BOT:")):
        return ""
    end = line.find("]")
    if end == -1 or end > 48:
        return ""
    end += 1
    if end < len(line) and line[end] == " ":
        end += 1
    return line[:end]


def _shorten_recent(line: str, limit: int) -> str:
    line = " ".join(line.split())
    if len(line) <= limit:
        return line
    if limit < 8:
        return line[:limit]
    head = _recent_head(line)
    if len(head) >= limit - 4:
        return line[: limit - 1].rstrip() + "…"
    room = limit - len(head) - 1
    return head + line[len(head) : len(head) + room].rstrip() + "…"


def fit_memory_pin(header: str, summary: str, recent: list[str]) -> str:
    """Build pin text that stays within Discord's 2000-character edit limit.

    `header` is everything through the ``Summary:\\n`` label. Recent lines are
    shortened before any long-term fact is removed. A fact line is dropped
    only when the pin is still over the limit, longest first, so one retold
    paragraph does not push out the rest of the cloud.
    """
    from core.client import drop_safety_preamble

    summary = drop_safety_preamble(summary or "") or "(none yet)"
    lines = [ln.strip() for ln in recent if ln and ln.strip()]

    def assemble(summary_text: str, kept: list[str]) -> str:
        body = ("\n".join(kept) + "\n") if kept else ""
        return f"{header}{summary_text}\n\nNew:\n{body}"

    def shrink_recents(kept: list[str], floor: int) -> None:
        while len(assemble(summary, kept)) > 1990 and any(len(ln) > floor for ln in kept):
            idx = max(range(len(kept)), key=lambda i: len(kept[i]))
            target = max(floor, len(kept[idx]) - 60)
            kept[idx] = _shorten_recent(kept[idx], target)

    if len(assemble(summary, lines)) <= 1990:
        return assemble(summary, lines)

    shrink_recents(lines, 96)
    if len(assemble(summary, lines)) <= 1990:
        return assemble(summary, lines)

    parts = [part.strip() for part in summary.splitlines() if part.strip()]
    while len(parts) > 1 and len(assemble("\n".join(parts), lines)) > 1990:
        idx = max(range(len(parts)), key=lambda i: len(parts[i]))
        parts.pop(idx)
    summary = "\n".join(parts) if parts else "(none yet)"
    if len(assemble(summary, lines)) <= 1990:
        return assemble(summary, lines)

    shrink_recents(lines, 48)
    if len(assemble(summary, lines)) <= 1990:
        return assemble(summary, lines)

    while len(lines) > 1 and len(assemble(summary, lines)) > 1990:
        lines.pop(0)
    if len(assemble(summary, lines)) <= 1990:
        return assemble(summary, lines)

    kept = lines[-1:] if lines else []
    room = 1990 - len(assemble("", kept))
    if room < 12:
        summary = "(none yet)"
    elif len(summary) > room:
        summary = summary[: room - 1].rstrip() + "…"
    text = assemble(summary, kept)
    if len(text) > 1990:
        text = text[:1989].rstrip()
        if not text.endswith("\n"):
            text += "\n"
    return text


def parse_pin_sections(content: str, fallback_header: str) -> tuple[str, str, list[str]]:
    """Return (header through ``Summary:\\n``, summary, recent lines).

    The header is ready for ``fit_memory_pin``. A pin with no Summary block
    comes back empty under ``fallback_header``.
    """
    fallback = fallback_header or ""
    if fallback and not fallback.endswith("\n"):
        fallback += "\n"
    header, body, recognized = split_memory_pin(content or "", fallback)
    if not recognized:
        label = fallback if fallback.endswith("\n") else fallback + "\n"
        return label + "Summary:\n", "(none yet)", []
    if not header.endswith("\n"):
        header += "\n"
    summary = "(none yet)"
    recent: list[str] = []
    new_at = body.rfind("\nNew:")
    if body.startswith("Summary:") and new_at >= 0:
        summary = body[len("Summary:") : new_at].strip() or "(none yet)"
        recent_body = body[new_at + len("\nNew:") :]
        recent = [
            line.strip()
            for line in recent_body.splitlines()
            if line.strip() and line.strip() != "New:"
        ]
    elif body.startswith("Summary:"):
        summary = body[len("Summary:") :].strip() or "(none yet)"
    return header + "Summary:\n", summary, recent


def split_memory_pin(content: str, fallback_header: str) -> tuple[str, str, bool]:
    """Return (header, body, recognized).

    A pin whose title is not the caller's header is still recognized when it
    has a Summary block. The caller keeps that title and does not replace the
    message with an empty one.
    """
    text = content or ""
    fallback = fallback_header or ""
    if fallback and text.startswith(fallback):
        return fallback, text[len(fallback) :].lstrip("\n"), True
    index = text.find("Summary:")
    if index >= 0:
        header = text[:index]
        if not header.endswith("\n"):
            header += "\n"
        return header, text[index:].lstrip("\n"), True
    return fallback, "", False


def user_lines_in_new_section(content: str) -> list[str]:
    """Lines in New:, ignoring the long-term cloud. Older USER: lines count too."""
    _header, _summary, recent = parse_pin_sections(content or "", "")
    return [line for line in recent if line.strip()]


async def update_context(bot, guild, user_id, message_text, username=None):
    """
    Appends one short line inside the New: section.

    Does not fold. Call this only after the Discord reply is already out.
    archive_user_recents_if_due then folds every third stored line into the
    long-term cloud and clears New:. Nothing is appended after that fold.

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

    from core.discord_store import refresh_record

    display_name = _display_name(member, user, username=username, user_id=user_id)
    header = f"Context for {display_name}:\n\n"
    pinned = await refresh_record(pinned)
    label, summary, recent = parse_pin_sections(pinned.content or header, header)
    recent.append(_user_line(message_text).strip())
    summary = await _compact_cloud_if_due(label, summary)
    new_content = fit_memory_pin(label, summary, recent)

    try:
        from core.discord_store import edit_record
        await edit_record(pinned, new_content)
    except Exception as e:
        print(f"[context_manager] ERROR editing pinned message for {user_id}: {e}")


async def _compact_cloud_if_due(label: str, summary: str) -> str:
    """Compact the cloud before the pin hits Discord's character limit."""
    from core.context_summarizer import compact_cloud, pin_would_crowd

    if not pin_would_crowd(label, summary):
        return summary
    return await compact_cloud(summary)


async def archive_user_recents_if_due(bot, guild, user_id, username=None):
    """After a reply, fold three stored lines into the long-term cloud.

    The next message then becomes the first line in an empty New: block.
    A failed summary leaves the lines in New: so they are not dropped.
    """
    if guild is None:
        return

    from core.context_summarizer import ARCHIVE_AFTER_MESSAGES, summarize_context

    channel, pinned = await ensure_context_channel(bot, guild, user_id, username)
    if not channel or not pinned:
        return

    lines = user_lines_in_new_section(pinned.content or "")
    dprint(
        f"[context_manager] User {user_id} recent USER lines before archive: {len(lines)}"
    )
    if len(lines) < ARCHIVE_AFTER_MESSAGES:
        return

    dprint(
        f"[context_manager] Archiving {len(lines)} recent USER lines "
        f"for {user_id} into the long-term block."
    )
    await summarize_context(
        bot,
        guild,
        user_id,
        username,
        keep_recent=0,
    )