# episodic.py
# One summary per server, in the home bot channel's episodic thread.
# The last few raw lines live in memories/{guild id}/recent.txt.
# This process only remembers the Discord message id.

from __future__ import annotations

import asyncio
import os

_LOCKS: dict[int, asyncio.Lock] = {}
MAX_STORE = 1900
PROMPT_CHARS = 600
_LINE_CHARS = 140
FOLD_AFTER = 5
# A turn can add two lines as the file crosses five. Keep those until the fold.
_RECENT_CAP = 8
_RECENT_NAME = "recent.txt"


def _lock(guild_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[guild_id] = lock
    return lock


def episode_prefix(guild_id: int) -> str:
    return f"Episodic {int(guild_id)}:"


def episode_heading(guild_id: int, guild_name: str | None = None) -> str:
    prefix = episode_prefix(guild_id)
    name = " ".join(str(guild_name or "").split())
    if not name:
        return prefix
    heading = f"{prefix} {name}"
    if len(heading) > 100:
        heading = heading[:99].rstrip() + "…"
    return heading


def episode_body(content: str, prefix: str) -> str:
    """The lines under the server heading."""
    text = content or ""
    if text.startswith(prefix):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def split_episode(body: str) -> tuple[str, list[str]]:
    """Summary prose and log lines. An older flat message is all log."""
    text = body or ""
    if "Summary:" not in text and "Log:" not in text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "", lines
    summary_lines: list[str] = []
    log: list[str] = []
    mode = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "Summary:":
            mode = "summary"
            continue
        if stripped == "Log:":
            mode = "log"
            continue
        if not stripped:
            continue
        if mode == "log":
            log.append(stripped)
        elif mode == "summary" and stripped not in ("(none yet)", "(none)"):
            summary_lines.append(stripped)
    return " ".join(summary_lines).strip(), log


def render_summary(heading: str, summary: str) -> str:
    """Discord copy of the summary. The raw lines stay in the local file.

    This does not chop the prose. The caller condenses a summary that will
    not fit instead of ending it mid-sentence.
    """
    prose = " ".join((summary or "").split())
    return "\n".join([heading.rstrip(), "Summary:", prose]).rstrip() + "\n"


def summary_room(heading: str) -> int:
    """Characters of prose that still fit under the Discord message cap."""
    overhead = len((heading or "").rstrip()) + len("\nSummary:\n") + 1
    return max(1, MAX_STORE - overhead)


def summary_fits(heading: str, summary: str) -> bool:
    return len(render_summary(heading, summary)) <= MAX_STORE


def stored_episode(heading: str, fresh: str, condensed: str) -> str:
    """Prose to store. A pass that runs past the pin is cut on a sentence."""
    for text in (fresh, condensed):
        if text and summary_fits(heading, text):
            return text
    room = summary_room(heading)
    for text in (condensed, fresh):
        clipped = _clip_prose(text or "", room)
        if clipped and summary_fits(heading, clipped):
            return clipped
    return ""


def recent_path(guild_id: int) -> str:
    """memories/{guild id}/recent.txt. One folder per server."""
    from core.paths import memories_dir

    return os.path.join(memories_dir(int(guild_id)), _RECENT_NAME)


def read_recent(guild_id: int) -> list[str]:
    """The lines waiting to be summarized."""
    path = recent_path(guild_id)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip()]
    return lines[-_RECENT_CAP:]


def write_recent(guild_id: int, lines: list[str]) -> None:
    """Rewrite the file so it is exactly these lines."""
    path = recent_path(guild_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    kept = [line for line in lines if line.strip()][-_RECENT_CAP:]
    text = ("\n".join(kept) + "\n") if kept else ""
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def push_recent(guild_id: int, new_lines: list[str]) -> list[str]:
    """Add lines. Returns what the file now holds, including a turn that crosses five."""
    rows = read_recent(guild_id)
    for line in new_lines:
        text = " ".join(str(line or "").split())
        if text:
            rows.append(text)
    rows = rows[-_RECENT_CAP:]
    write_recent(guild_id, rows)
    return rows


def clear_recent(guild_id: int) -> None:
    write_recent(guild_id, [])


def episode_summary_text(raw: str) -> str:
    """Prose from the summarizer. An error string is not a summary."""
    from core.client import drop_safety_preamble

    text = " ".join(drop_safety_preamble(raw or "").split())
    if not text or text.upper().startswith("ERROR"):
        return ""
    low = text.lower()
    if "existing summary:" in low or "new summary:" in low:
        return ""
    return text


def fold_prompt(summary: str, recent: list[str], limit: int) -> str:
    """Previous summary plus the local file. Not the chat prompt."""
    prose = " ".join((summary or "").split()) or "(none yet)"
    rows = "\n".join(line for line in recent if str(line).strip()) or "(none)"
    room = max(1, int(limit))
    return (
        "Summarize this server's public episode. Reply with the summary only.\n"
        "Finished prose about the server, not notes to yourself.\n"
        "Update the summary with the recent lines. Keep earlier topics that still matter.\n"
        "Keep topics people raised and tools that ran.\n"
        "Drop greetings, agreements, and one-off reactions.\n"
        "Plain prose. No bullet list. "
        f"Stay under {room} characters.\n\n"
        f"Summary:\n{prose}\n\n"
        f"Recent:\n{rows}"
    )


def condense_prompt(existing: str, fresh: str, limit: int) -> str:
    """Second pass. The existing episode plus the summary that did not fit."""
    prose = " ".join((existing or "").split()) or "(none yet)"
    segment = " ".join((fresh or "").split()) or "(none)"
    room = max(1, int(limit))
    return (
        "Condense this server episode into one summary. Reply with the summary only.\n"
        "Use the existing summary and the new summary. Keep topics people raised and tools that ran.\n"
        "Drop greetings, agreements, and one-off reactions.\n"
        "Plain prose. No bullet list. "
        f"Stay under {room} characters.\n\n"
        f"Existing summary:\n{prose}\n\n"
        f"New summary:\n{segment}"
    )


def _clip_line(day: str, sentence: str) -> str:
    line = f"{day} {sentence}".strip()
    if len(line) > _LINE_CHARS:
        line = line[: _LINE_CHARS - 1].rstrip() + "…"
    return line


def ask_line(text: str, day: str) -> str:
    """What was said. No Discord name."""
    clip = " ".join(str(text or "").split())
    if clip in ("", "(no text)", "(no content)"):
        sentence = "past Dialog."
    else:
        if len(clip) > 80:
            clip = clip[:79].rstrip() + "…"
        sentence = f"past Dialog: {clip}"
    return _clip_line(day, sentence)


def act_line(tool: str, arguments: dict | None, day: str) -> str:
    """One public tool use. The name only."""
    del arguments
    kind = " ".join(str(tool or "").split()).lower() or "tool"
    return _clip_line(day, f"past tool: {kind}")


def turn_lines(
    user_text: str,
    day: str,
    tool_lines: list[str] | None = None,
) -> list[str]:
    """Every message is recorded. Tool lines follow it."""
    acts = [line for line in (tool_lines or []) if str(line).strip()]
    return [ask_line(user_text, day), *acts]


def _clip_prose(text: str, limit: int) -> str:
    """Shorten on a sentence boundary. A stump is not given an ellipsis."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    window = text[:limit].rstrip()
    cut = max(window.rfind("."), window.rfind("!"), window.rfind("?"))
    if cut >= max(40, limit // 3):
        return window[: cut + 1].strip()
    space = window.rfind(" ")
    if space >= 40:
        return window[:space].rstrip()
    return window


def same_episode_text(left: str, right: str) -> bool:
    """Discord may drop the trailing newline. The prose is what matters."""
    return (left or "").strip() == (right or "").strip()


def prompt_episode(body: str) -> str:
    """The summary prose only. Raw lines stay in the local file."""
    from core.client import drop_safety_preamble

    summary, _log = split_episode(body)
    text = " ".join(drop_safety_preamble(summary).split())
    if not text:
        return ""
    return _clip_prose(text, PROMPT_CHARS)


async def _episode_message(thread, prefix: str):
    """The pinned episode. History is used only after a pin list with no match."""
    from core.discord_store import find_pinned_record, lookup_record, refresh_record

    message = await find_pinned_record(thread, prefix)
    if message is None:
        message = await lookup_record(thread, prefix, allow_history=True)
    if message is None:
        return None
    fresh = await refresh_record(message)
    return fresh or message


async def load_episodic(bot, guild_id) -> str:
    """This server's episode lines, or empty when it has none yet."""
    if not guild_id:
        return ""
    from core.discord_store import THREAD_EPISODIC, ensure_bot_thread

    thread = await ensure_bot_thread(bot, THREAD_EPISODIC)
    if thread is None:
        return ""
    prefix = episode_prefix(guild_id)
    message = await _episode_message(thread, prefix)
    if message is None:
        return ""
    return episode_body(getattr(message, "content", "") or "", prefix)


async def _fold_stored(summary: str, recent: list[str], limit: int) -> str:
    """Summarizer pass over the local file. Empty means try on a later turn."""
    from core.client import call_groq_raw, tokens_for
    from core.debuglog import dprint
    from core.summarizer_prompt import get_summarizer_model

    try:
        raw = await call_groq_raw(
            fold_prompt(summary, recent, limit),
            model=get_summarizer_model(),
            max_completion_tokens=tokens_for(limit),
        )
    except Exception as exc:
        print(f"[episodic] summary failed: {exc}", flush=True)
        dprint(f"[episodic] summary failed: {exc}")
        return ""
    text = episode_summary_text(raw)
    if not text:
        dprint("[episodic] summary returned no usable text")
    return text


async def _condense_stored(previous: str, fresh: str, limit: int) -> str:
    """Second summarizer pass. Empty means the first text stays unstored."""
    from core.client import call_groq_raw, tokens_for
    from core.debuglog import dprint
    from core.summarizer_prompt import get_summarizer_model

    try:
        raw = await call_groq_raw(
            condense_prompt(previous, fresh, limit),
            model=get_summarizer_model(),
            max_completion_tokens=tokens_for(limit),
        )
    except Exception as exc:
        print(f"[episodic] condense failed: {exc}", flush=True)
        dprint(f"[episodic] condense failed: {exc}")
        return ""
    text = episode_summary_text(raw)
    if not text:
        dprint("[episodic] condense returned no usable text")
    return text


async def append_episodic(bot, guild_id, guild_name, lines: list[str]) -> None:
    """Write the line into this server's file. Summarize every five lines."""
    fresh = [line for line in lines if line and str(line).strip()]
    if not guild_id or not fresh:
        return
    from core.discord_store import (
        THREAD_EPISODIC,
        edit_record,
        ensure_bot_thread,
        ensure_record,
        refresh_record,
    )

    guild_id = int(guild_id)
    async with _lock(guild_id):
        recent = push_recent(guild_id, fresh)
        print(
            f"[episodic] {recent_path(guild_id)} has {len(recent)} line(s)",
            flush=True,
        )
        if len(recent) < FOLD_AFTER:
            return
        print(
            f"[episodic] summarizing {len(recent)} lines for guild {guild_id}",
            flush=True,
        )
        thread = await ensure_bot_thread(bot, THREAD_EPISODIC)
        if thread is None:
            print(
                f"[episodic] no episodic thread; keeping {len(recent)} lines",
                flush=True,
            )
            return
        prefix = episode_prefix(guild_id)
        heading = episode_heading(guild_id, guild_name)
        message = await _episode_message(thread, prefix)
        current = getattr(message, "content", "") or "" if message is not None else ""
        previous, _log = split_episode(episode_body(current, prefix))
        room = summary_room(heading)
        folded = await _fold_stored(previous, recent, room)
        if not folded:
            print(
                f"[episodic] summary returned no text; keeping {len(recent)} lines",
                flush=True,
            )
            return
        chosen = folded
        if not summary_fits(heading, folded):
            print(
                f"[episodic] summary is {len(folded)} characters; condensing",
                flush=True,
            )
            condensed = await _condense_stored(previous, folded, room)
            chosen = stored_episode(heading, folded, condensed)
        if not chosen:
            print(
                f"[episodic] summary did not fit; keeping {len(recent)} lines",
                flush=True,
            )
            return
        packed = render_summary(heading, chosen)
        if message is None:
            message = await ensure_record(thread, prefix, packed)
            if message is None:
                print(
                    f"[episodic] could not store the summary; keeping {len(recent)} lines",
                    flush=True,
                )
                return
            live = await refresh_record(message)
            message = live or message
            current = getattr(message, "content", "") or ""
        elif not same_episode_text(packed, current):
            await edit_record(message, packed)
            live = await refresh_record(message)
            # A failed re-read returns the same object. Its text was copied
            # locally by the edit and is not proof the pin changed.
            if live is None or live is message:
                print(
                    f"[episodic] summary was not written to the pin; keeping {len(recent)} lines",
                    flush=True,
                )
                return
            message = live
            current = getattr(message, "content", "") or ""
        if not same_episode_text(packed, current):
            print(
                f"[episodic] summary was not written to the pin; keeping {len(recent)} lines",
                flush=True,
            )
            return
        if not getattr(message, "pinned", False):
            try:
                await message.pin()
            except Exception as exc:
                mid = getattr(message, "id", "?")
                print(
                    f"[episodic] summary is on message {mid} but it is not pinned: {exc}",
                    flush=True,
                )
        clear_recent(guild_id)
        print(
            f"[episodic] summary stored for guild {guild_id} on message {getattr(message, 'id', '?')}",
            flush=True,
        )


async def lookup_existing(thread, prefix: str):
    """The episode message if it already exists. Does not create one."""
    from core.discord_store import lookup_record

    return await lookup_record(thread, prefix, allow_history=True)
