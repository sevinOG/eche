# episodic.py
# One summary per server, in the home bot channel's episodic thread.
# The last few raw lines live in memories/{guild id}/recent.txt.
# This process only remembers the Discord message id.

from __future__ import annotations

import asyncio
import os

_LOCKS: dict[int, asyncio.Lock] = {}
# Defaults. The live values are Settings → Memory & Prompts → Context & Memory
# (core/context_caps.py): episode_summary_chars, episode_prompt_chars,
# episode_fold_after.
MAX_STORE = 1900
# Condense before the pin sits against the cap. A full-size rewrite is cut
# off by the completion budget while still looking short enough to store.
_SOFT_STORE = 1500
PROMPT_CHARS = 600
_LINE_CHARS = 140
FOLD_AFTER = 5
# A turn can add two lines as the file crosses five. Keep those until the fold.
_RECENT_CAP = 8
_RECENT_NAME = "recent.txt"


def max_store() -> int:
    """Stored episode size. Never above Discord's room under 2000."""
    from core.context_caps import get

    return min(MAX_STORE, get("episode_summary_chars"))


def soft_store() -> int:
    """Condense once the pin reaches this size. Same ratio as 1500 of 1900."""
    return max_store() * _SOFT_STORE // MAX_STORE


def prompt_chars() -> int:
    from core.context_caps import get

    return get("episode_prompt_chars")


def fold_after() -> int:
    from core.context_caps import get

    return get("episode_fold_after")


def recent_cap() -> int:
    """Lines the local file holds. A turn can add two lines past the fold point."""
    return max(_RECENT_CAP, fold_after() + 3)


_BULLETS = ("- ", "* ", "• ", "– ")


def _bullet_body(line: str) -> str | None:
    for mark in _BULLETS:
        if line.startswith(mark):
            return line[len(mark):].strip()
    return None


def is_bulleted(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return bool(lines) and sum(1 for line in lines if _bullet_body(line) is not None) * 2 >= len(lines)


def tidy_summary(text: str) -> str:
    """One line of prose, or one "- " line per bullet. Extra spaces go."""
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    if not is_bulleted("\n".join(lines)):
        return " ".join(lines)
    out: list[str] = []
    for line in lines:
        body = _bullet_body(line)
        if body is None:
            # A wrapped bullet continues on the next line.
            if out:
                out[-1] = f"{out[-1]} {line}"
                continue
            body = line
        if body:
            out.append(f"- {body}")
    return "\n".join(out)


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
    return tidy_summary("\n".join(summary_lines)), log


def render_summary(heading: str, summary: str) -> str:
    """Discord copy of the summary. The raw lines stay in the local file.

    This does not chop the prose. The caller condenses a summary that will
    not fit instead of ending it mid-sentence.
    """
    from core.content_filter import clean_memory_text

    prose = clean_memory_text(tidy_summary(summary))
    return "\n".join([heading.rstrip(), "Summary:", prose]).rstrip() + "\n"


def summary_room(heading: str) -> int:
    """Characters of prose that still fit under the Discord message cap."""
    overhead = len((heading or "").rstrip()) + len("\nSummary:\n") + 1
    return max(1, max_store() - overhead)


def summary_fits(heading: str, summary: str) -> bool:
    return len(render_summary(heading, summary)) <= max_store()


def _message_len(heading: str, summary: str) -> int:
    return len(render_summary(heading, summary or ""))


def _crowded(heading: str, summary: str) -> bool:
    """The pin is full enough that the next full-size rewrite will be cut off."""
    return bool((summary or "").strip()) and _message_len(heading, summary) >= soft_store()


def _cut_off(summary: str) -> bool:
    """A long reply that does not end on a sentence was stopped mid-thought.

    A bullet list counts as cut off when most bullets end with a stop and the
    last one does not. A list that never uses stops is taken as finished.
    """
    body = tidy_summary(summary)
    if len(body) < 400:
        return False
    if is_bulleted(body):
        lines = body.splitlines()
        stopped = sum(1 for line in lines[:-1] if line.rstrip()[-1:] in ".!?")
        if len(lines) > 1 and stopped * 2 >= len(lines) - 1:
            return lines[-1].rstrip()[-1:] not in ".!?"
        return False
    return body[-1] not in ".!?"


def needs_condense(heading: str, folded: str) -> bool:
    """True when the summary must be shortened before it is stored."""
    if not (folded or "").strip():
        return False
    if not summary_fits(heading, folded) or _crowded(heading, folded):
        return True
    return _cut_off(folded)


def condense_room(heading: str) -> int:
    """Character budget for the shorten pass. Smaller than a full pin."""
    return max(200, summary_room(heading) // 2)


def _ready_summary(heading: str, text: str) -> bool:
    """Finished prose that still has room under the soft cap."""
    return bool(
        text
        and summary_fits(heading, text)
        and not _crowded(heading, text)
        and not _cut_off(text)
    )


def stored_episode(heading: str, fresh: str, condensed: str) -> str:
    """Prose to store. A pass that runs past the pin is cut on a sentence.

    A finished summary under the soft cap wins. A cut-off reply keeps its
    last finished sentence. When the reply never reached a sentence, a
    word clip under the soft cap is stored so the pin can move.
    """
    fresh = fresh or ""
    condensed = condensed or ""
    ready = [text for text in (condensed, fresh) if _ready_summary(heading, text)]
    if ready:
        return max(ready, key=len)
    short = condense_room(heading)
    room = summary_room(heading)
    finished: list[str] = []
    for limit in (short, room):
        for text in (condensed, fresh):
            clipped = _clip_prose(text, limit, finish=True)
            if (
                clipped
                and len(clipped) >= 80
                and summary_fits(heading, clipped)
                and not _cut_off(clipped)
                and not _crowded(heading, clipped)
            ):
                finished.append(clipped)
    if finished:
        return max(finished, key=len)
    for text in (condensed, fresh):
        clipped = _clip_prose(text, short, finish=False)
        if (
            clipped
            and len(clipped) >= 80
            and summary_fits(heading, clipped)
            and not _crowded(heading, clipped)
        ):
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
    return lines[-recent_cap():]


def write_recent(guild_id: int, lines: list[str]) -> None:
    """Rewrite the file so it is exactly these lines."""
    path = recent_path(guild_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    kept = [line for line in lines if line.strip()][-recent_cap():]
    text = ("\n".join(kept) + "\n") if kept else ""
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def push_recent(guild_id: int, new_lines: list[str]) -> list[str]:
    """Add lines. Returns what the file now holds, including a turn that crosses five."""
    from core.content_filter import clean_memory_text

    rows = read_recent(guild_id)
    for line in new_lines:
        text = " ".join(clean_memory_text(str(line or "")).split())
        if text:
            rows.append(text)
    rows = rows[-recent_cap():]
    write_recent(guild_id, rows)
    return rows


def clear_recent(guild_id: int) -> None:
    write_recent(guild_id, [])


def episode_summary_text(raw: str) -> str:
    """Prose from the summarizer. An error string is not a summary."""
    from core.client import drop_safety_preamble

    from core.summary_safety import scrub_summary

    text = tidy_summary(scrub_summary(drop_safety_preamble(raw or "")))
    if not text or text.upper().startswith("ERROR"):
        return ""
    try:
        from core.blocklist import REFUSAL
        from core.guardrails import is_refusal_text

        if text == REFUSAL or is_refusal_text(text):
            return ""
    except Exception:
        if text == "Nope. Not going there.":
            return ""
    low = text.lower()
    # The condense prompt names its sections. A reply that opens with that
    # label is still the summary. A second copy of the label is the prompt.
    for label in ("existing summary:", "new summary:", "summary:"):
        idx = low.find(label)
        if 0 <= idx <= 48:
            text = tidy_summary(text[idx + len(label) :])
            low = text.lower()
            break
    if not text or "existing summary:" in low or "new summary:" in low:
        return ""
    return text


_EPISODE_KEEP = (
    "Keep: subjects people raised, decisions or plans the server made, "
    "recurring topics, and tools that ran."
)
def _slur_rule(text: str) -> str:
    from core.content_filter import slur_rule

    return slur_rule(text)


_EPISODE_DROP = (
    "Drop: who said what, greetings, small talk, play-by-play, quotes, numbers, "
    "links, and guesses. "
    "No slurs, no Discord ids or long id numbers, and no @mentions. "
    "Do not record anything that was made up, wrong, or a refusal. "
    "Drop requests that were answered, refused, or are stale."
)
_EPISODE_FORMAT = (
    'Format: short "- " bullets, one subject each, each ending with a period. '
    "No headings, no intro."
)


def fold_prompt(summary: str, recent: list[str], limit: int) -> str:
    """Previous summary plus the local file. Not the chat prompt."""
    from core.content_filter import clean_lines, clean_memory_text

    prose = clean_memory_text(tidy_summary(summary)) or "(none yet)"
    rows = "\n".join(clean_lines(recent)) or "(none)"
    room = max(1, int(limit))
    return (
        "Update this server's episode summary. Reply with the updated summary only.\n"
        f"{_EPISODE_KEEP}\n"
        f"{_slur_rule(_EPISODE_DROP)}\n"
        "Merge the recent lines in. Keep older subjects that still matter; drop finished ones.\n"
        f"{_EPISODE_FORMAT}\n"
        f"Length: at most {room} characters. Shorter is better.\n\n"
        f"Summary:\n{prose}\n\n"
        f"Recent:\n{rows}"
    )


def condense_prompt(existing: str, fresh: str, limit: int) -> str:
    """Second pass. The existing episode plus the summary that did not fit."""
    from core.content_filter import clean_memory_text

    prose = clean_memory_text(tidy_summary(existing)) or "(none yet)"
    segment = clean_memory_text(tidy_summary(fresh)) or "(none)"
    room = max(1, int(limit))
    return (
        "Shorten this server's episode summary. Reply with the shortened summary only.\n"
        "Combine the existing summary and the new summary. "
        f"{_EPISODE_KEEP} Merge duplicates. Drop anything trivial or finished.\n"
        f"{_slur_rule(_EPISODE_DROP)}\n"
        f"{_EPISODE_FORMAT}\n"
        f"Length: at most {room} characters.\n\n"
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


def _clip_prose(text: str, limit: int, *, finish: bool = False) -> str:
    """Shorten on a sentence boundary. A stump is not given an ellipsis.

    finish drops a long reply that was cut off before a sentence, instead of
    storing the broken tail.
    """
    text = tidy_summary(text)
    if not text:
        return ""
    if len(text) <= limit and not (finish and _cut_off(text)):
        return text
    window = text[:limit].rstrip()
    if is_bulleted(text):
        # Whole bullets first. A bullet cut mid-way is dropped.
        if len(text) > limit and text[limit : limit + 1] == "\n":
            return window
        line_end = window.rfind("\n")
        if line_end >= 40:
            return window[:line_end].rstrip()
    cut = max(window.rfind("."), window.rfind("!"), window.rfind("?"))
    # A prompt clip ignores a tiny first sentence and keeps a longer word
    # clip. A stored episode keeps that sentence: the other choice is to
    # leave the pin unchanged.
    floor = 40 if finish else max(40, min(limit, len(window)) // 3)
    if cut >= floor:
        return window[: cut + 1].strip()
    if finish:
        return ""
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

    from core.content_filter import clean_memory_text

    summary, _log = split_episode(body)
    # No guild ids, mentions, or slurs in the server block of the prompt.
    text = clean_memory_text(tidy_summary(drop_safety_preamble(summary)))
    if not text:
        return ""
    return _clip_prose(text, prompt_chars())


async def _episode_message(thread, prefix: str):
    """The pinned episode as Discord has it now. A cache copy is not the pin."""
    from core.discord_store import read_pinned

    message, pins_ok = await read_pinned(thread, prefix)
    if not pins_ok:
        return None
    return message


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
    from core.client import call_groq_raw
    from core.summary_safety import with_summary_rule
    from core.context_caps import summary_tokens
    from core.debuglog import dprint
    from core.summarizer_prompt import get_summarizer_model

    try:
        raw = await call_groq_raw(
            with_summary_rule(fold_prompt(summary, recent, limit)),
            model=get_summarizer_model(),
            max_completion_tokens=summary_tokens(limit),
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
    from core.client import call_groq_raw
    from core.summary_safety import with_summary_rule
    from core.context_caps import summary_tokens
    from core.debuglog import dprint
    from core.summarizer_prompt import get_summarizer_model

    try:
        raw = await call_groq_raw(
            with_summary_rule(condense_prompt(previous, fresh, limit)),
            model=get_summarizer_model(),
            max_completion_tokens=summary_tokens(limit),
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
    from core.discord_store import THREAD_EPISODIC, ensure_bot_thread, read_pinned, write_pinned

    guild_id = int(guild_id)
    async with _lock(guild_id):
        recent = push_recent(guild_id, fresh)
        if len(recent) < fold_after():
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
        message, pins_ok = await read_pinned(thread, prefix)
        if not pins_ok:
            print(
                f"[episodic] could not read pins; keeping {len(recent)} lines",
                flush=True,
            )
            return
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
        if needs_condense(heading, folded):
            print(
                f"[episodic] summary is {_message_len(heading, folded)} characters; condensing",
                flush=True,
            )
            condensed = await _condense_stored(previous, folded, condense_room(heading))
            chosen = stored_episode(heading, folded, condensed)
        if not chosen:
            print(
                f"[episodic] summary did not fit; keeping {len(recent)} lines",
                flush=True,
            )
            return
        packed = render_summary(heading, chosen)
        if len(packed) > max_store():
            print(
                f"[episodic] summary is {len(packed)} characters; keeping {len(recent)} lines",
                flush=True,
            )
            return
        written, note = await write_pinned(thread, prefix, packed)
        if written is None or note == "pin-failed":
            print(
                f"[episodic] summary was not written ({note}); keeping {len(recent)} lines",
                flush=True,
            )
            return
        clear_recent(guild_id)
        print(
            f"[episodic] summary stored for guild {guild_id} on message {getattr(written, 'id', '?')}",
            flush=True,
        )


async def lookup_existing(thread, prefix: str):
    """The episode message if it already exists. Does not create one."""
    from core.discord_store import lookup_record

    return await lookup_record(thread, prefix, allow_history=True)
