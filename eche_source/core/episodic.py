# episodic.py
# Server memory as real episodes.
#
# An episode is one stretch of conversation with Eche in one channel. It ends
# after a 20-minute pause, a change of topic, or episode_fold_after
# turns. A finished episode is summarized once into a record:
#   when (start/end), where (guild, channel), who (participant user ids, never
#   names), what happened, outcome, emotional tone, topics, a salience score,
#   and links to related episodes.
# Records live in memories/{guild id}/episodes.jsonl. Episodes older than a
# week are consolidated nightly into weekly summaries (level 1), and those
# into monthly ones (level 2); low-salience originals are then deleted.
# A reply sees the few episodes that score best on salience, recency, topic
# overlap with the message, and whether the speaker took part.
#
# The old "Episodic {server id}:" pin is now a readable copy of the most
# important recent episodes. Its old prose is migrated once into a level-2
# "history" record (a backup goes to episodes_legacy.json).

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
import uuid
from datetime import datetime

_LOCKS: dict[int, asyncio.Lock] = {}
MAX_STORE = 1900
PROMPT_CHARS = 600
_LINE_CHARS = 140
# Defaults. The live values are Settings → Memory & Prompts → Context & Memory
# (core/context_caps.py): episode_fold_after (longest episode, in turns),
# episode_summary_chars, episode_prompt_chars. The pause that ends an episode
# is ECHE_EPISODE_GAP_MINUTES, else GAP_MINUTES.
FOLD_AFTER = 25  # longest episode, in turns
GAP_MINUTES = 20
# A topic change only ends an episode that has some length, after a short pause.
_SHIFT_MIN_TURNS = 4
_SHIFT_MIN_GAP = 300.0
_TURN_CHARS = 220
_LINK_DAYS = 14
_CONSOLIDATE_DAYS = 7
_MONTHLY_DAYS = 30
_KEEP_SALIENCE = 0.5
_RECENCY_DAYS = 10.0
_MAX_RECORDS = 500
_CLOSE_TRIES = 3
_RECENT_NAME = "recent.txt"
_RECORDS_NAME = "episodes.jsonl"
_OPEN_NAME = "open_episodes.json"
_META_NAME = "episodes_meta.json"
_LEGACY_NAME = "episodes_legacy.json"
_OUTCOMES = ("resolved", "ongoing", "abandoned", "unclear")
_DAY = 86400.0
_BULLETS = ("- ", "* ", "• ", "– ")


def store_names() -> tuple[str, ...]:
    """Files in memories/{guild id}/ (for the memory reset)."""
    return (_RECENT_NAME, _RECORDS_NAME, _OPEN_NAME, _META_NAME, _LEGACY_NAME)


def max_turns() -> int:
    from core.context_caps import get

    return get("episode_fold_after")


def gap_seconds() -> float:
    raw = (os.environ.get("ECHE_EPISODE_GAP_MINUTES") or "").strip()
    try:
        minutes = max(5, min(240, int(float(raw)))) if raw else GAP_MINUTES
    except ValueError:
        minutes = GAP_MINUTES
    return float(minutes) * 60.0

def max_store() -> int:
    """Stored episode size. Never above Discord's room under 2000."""
    from core.context_caps import get

    return min(MAX_STORE, get("episode_summary_chars"))


def prompt_chars() -> int:
    from core.context_caps import get

    return get("episode_prompt_chars")


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


def recent_path(guild_id: int) -> str:
    """memories/{guild id}/recent.txt. One folder per server."""
    from core.paths import memories_dir

    return os.path.join(memories_dir(int(guild_id)), _RECENT_NAME)


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


async def lookup_existing(thread, prefix: str):
    """The episode message if it already exists. Does not create one."""
    from core.discord_store import lookup_record

    return await lookup_record(thread, prefix, allow_history=True)


# ------------------------------------------------------------------ files


def _folder(guild_id: int) -> str:
    from core.paths import memories_dir

    path = memories_dir(int(guild_id))
    os.makedirs(path, exist_ok=True)
    return path


def _file(guild_id: int, name: str) -> str:
    return os.path.join(_folder(guild_id), name)


def _read_json(path: str, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: str, data) -> None:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def read_records(guild_id: int) -> list[dict]:
    path = _file(guild_id, _RECORDS_NAME)
    if not os.path.isfile(path):
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("id") and row.get("summary"):
                    out.append(row)
    except OSError:
        return []
    return out


def write_records(guild_id: int, records: list[dict]) -> None:
    path = _file(guild_id, _RECORDS_NAME)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def _load_open(guild_id: int) -> dict:
    data = _read_json(_file(guild_id, _OPEN_NAME), {})
    data.setdefault("open", {})
    data.setdefault("closing", [])
    if not isinstance(data["open"], dict):
        data["open"] = {}
    if not isinstance(data["closing"], list):
        data["closing"] = []
    return data


def _save_open(guild_id: int, data: dict) -> None:
    _write_json(_file(guild_id, _OPEN_NAME), data)


def _meta(guild_id: int) -> dict:
    return _read_json(_file(guild_id, _META_NAME), {})


def _save_meta(guild_id: int, meta: dict) -> None:
    _write_json(_file(guild_id, _META_NAME), meta)


def guilds_on_disk() -> list[int]:
    from core.paths import memories_dir

    base = memories_dir()
    if not os.path.isdir(base):
        return []
    return [int(name) for name in os.listdir(base) if name.isdigit() and os.path.isdir(os.path.join(base, name))]


# ------------------------------------------------------------------ helpers


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _day(ts: float) -> str:
    return datetime.fromtimestamp(float(ts)).astimezone().strftime("%Y-%m-%d")


def terms(text: str) -> set[str]:
    from core.memory_store import terms as _terms

    return _terms(text)


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _clip_text(text: str, limit: int) -> str:
    body = " ".join(str(text or "").split())
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "…"


def _turn_terms(turn: dict) -> set[str]:
    return terms(f"{turn.get('text') or ''} {turn.get('reply') or ''}")


def _record_terms(record: dict) -> set[str]:
    return terms(" ".join(record.get("topics") or []) + " " + str(record.get("summary") or ""))


def is_boundary(episode: dict, turn: dict) -> bool:
    """True when this turn starts a new episode in the channel."""
    turns = episode.get("turns") or []
    if not turns:
        return False
    gap = float(turn.get("at") or 0) - float(episode.get("end") or 0)
    if gap >= gap_seconds():
        return True
    if len(turns) >= _SHIFT_MIN_TURNS and gap >= _SHIFT_MIN_GAP:
        recent: set[str] = set()
        for prior in turns[-3:]:
            recent |= _turn_terms(prior)
        fresh = terms(turn.get("text") or "")
        if len(fresh) >= 2 and recent and not (fresh & recent):
            return True
    return False


_EMOTION = re.compile(
    r"(?i)\b(?:love|hate|angry|furious|sad|crying|excited|thrilled|scared|worried|upset|"
    r"amazing|awful|terrible|thank you so much|sorry)\b|!!|\b[A-Z]{4,}\b"
)
_REMEMBER = re.compile(r"(?i)\b(?:remember|don't forget|do not forget|keep in mind)\b")


def score_salience(model_score, episode: dict) -> float:
    """Model judgment plus signals the code can see."""
    try:
        base = max(0.0, min(1.0, float(model_score)))
    except (TypeError, ValueError):
        base = 0.3
    turns = episode.get("turns") or []
    texts = " ".join(str(t.get("text") or "") for t in turns)
    size = min(len(turns) / 12.0, 1.0)
    multi = 1.0 if len(set(episode.get("participants") or [])) > 1 else 0.0
    tools = 1.0 if any(t.get("tools") for t in turns) else 0.0
    emotion = 1.0 if _EMOTION.search(texts) else 0.0
    remember = 1.0 if _REMEMBER.search(texts) else 0.0
    value = 0.45 * base + 0.15 * size + 0.1 * multi + 0.1 * tools + 0.1 * emotion + 0.1 * remember
    return round(max(0.0, min(1.0, value)), 3)


def _labels(participants: list[int]) -> dict[str, int]:
    return {f"P{i + 1}": int(uid) for i, uid in enumerate(participants)}


def _transcript(episode: dict) -> str:
    from core.content_filter import clean_memory_text

    by_id = {uid: label for label, uid in _labels(episode.get("participants") or []).items()}
    rows: list[str] = []
    for turn in episode.get("turns") or []:
        who = by_id.get(int(turn.get("author") or 0), "P?")
        text = clean_memory_text(_clip_text(turn.get("text") or "", _TURN_CHARS))
        if text:
            rows.append(f"{who}: {text}")
        tools = [str(t) for t in (turn.get("tools") or []) if str(t).strip()]
        if tools:
            rows.append(f"[Eche used {', '.join(tools)}]")
        reply = clean_memory_text(_clip_text(turn.get("reply") or "", _TURN_CHARS))
        if reply:
            rows.append(f"Eche: {reply}")
    return "\n".join(rows)


def _parse_json(raw: str) -> dict | None:
    from core.client import drop_safety_preamble

    text = drop_safety_preamble(raw or "").strip()
    if not text or text.upper().startswith("ERROR"):
        return None
    try:
        from core.blocklist import REFUSAL
        from core.guardrails import is_refusal_text

        if text == REFUSAL or is_refusal_text(text):
            return None
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _known_names(bot) -> list[str]:
    names: list[str] = []
    for guild in list(getattr(bot, "guilds", None) or [])[:20]:
        for member in list(getattr(guild, "members", None) or [])[:5000]:
            for attr in ("display_name", "global_name", "nick"):
                value = getattr(member, attr, None)
                if value and str(value)[:1].isupper():
                    names.append(str(value))
    return names


def clean_summary(text: str, known_names=()) -> str:
    """An episode summary as stored: scrubbed, filtered, names replaced."""
    from core.content_filter import clean_memory_text
    from core.memory_privacy import redact_names
    from core.summary_safety import scrub_summary

    body = " ".join(str(text or "").split())
    body = scrub_summary(body)
    body = clean_memory_text(body)
    body = redact_names(body, (), known_names)
    return _clip_text(body, 320)


def _clean_topics(raw, known_names=()) -> list[str]:
    from core.content_filter import clean_memory_text
    from core.memory_privacy import entry_problem

    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        word = clean_memory_text(" ".join(str(item or "").split()))[:40].strip()
        if not word:
            continue
        # Topics are often lower case: check the title-cased form for names too.
        if entry_problem(word, (), known_names) or entry_problem(word.title(), (), known_names):
            continue
        word = word.lower()
        if word not in out:
            out.append(word)
    return out[:5]


async def _summarize_json(prompt: str, limit: int = 1200) -> dict | None:
    from core.client import call_groq_raw
    from core.context_caps import summary_tokens
    from core.summarizer_prompt import SUMMARIZER_TEMPERATURE, get_summarizer_model
    from core.summary_safety import with_summary_rule

    raw = await call_groq_raw(
        with_summary_rule(prompt),
        model=get_summarizer_model(),
        max_completion_tokens=summary_tokens(limit),
        temperature=SUMMARIZER_TEMPERATURE,
    )
    return _parse_json(raw)


# ------------------------------------------------------------------ record + close


def _turn(text: str, author_id, reply: str, tools, at: float) -> dict:
    from core.content_filter import clean_memory_text

    return {
        "at": float(at),
        "author": int(author_id or 0),
        "text": clean_memory_text(_clip_text(text, _TURN_CHARS)),
        "reply": clean_memory_text(_clip_text(reply, _TURN_CHARS)),
        "tools": [str(t) for t in (tools or []) if str(t).strip()][:5],
    }


def _new_episode(guild_id: int, channel_id, turn: dict) -> dict:
    return {
        "id": "ep_" + uuid.uuid4().hex[:12],
        "guild": int(guild_id),
        "channel": int(channel_id or 0),
        "start": turn["at"],
        "end": turn["at"],
        "participants": [],
        "turns": [],
    }


def _add_turn(episode: dict, turn: dict) -> None:
    episode["turns"].append(turn)
    episode["end"] = turn["at"]
    author = int(turn.get("author") or 0)
    if author and author not in episode["participants"]:
        episode["participants"].append(author)


def _tools_from_lines(lines) -> list[str]:
    out = []
    for line in lines or []:
        match = re.search(r"past tool:\s*(\S+)", str(line))
        if match and match.group(1) not in out:
            out.append(match.group(1))
    return out


async def append_episodic(
    bot,
    guild_id,
    guild_name,
    lines: list[str] | None = None,
    *,
    channel_id=None,
    author_id=None,
    text: str | None = None,
    reply: str = "",
    tools: list[str] | None = None,
    at: float | None = None,
) -> None:
    """Add one turn to this channel's open episode. A boundary closes the
    previous episode first; a closed episode is summarized into a record."""
    if not guild_id:
        return
    guild_id = int(guild_id)
    now = time.time() if at is None else float(at)
    if text is None:
        joined = " ".join(str(line) for line in (lines or []) if "past tool:" not in str(line))
        text = re.sub(r"^\d{4}-\d{2}-\d{2}\s+past Dialog:?\s*", "", joined).strip()
    if tools is None:
        tools = _tools_from_lines(lines)
    if not (text or "").strip() and not tools:
        return
    await migrate_if_needed(bot, guild_id)
    turn = _turn(text or "", author_id, reply, tools, now)
    to_close: list[dict] = []
    async with _lock(guild_id):
        state = _load_open(guild_id)
        key = str(int(channel_id or 0))
        episode = state["open"].get(key)
        if episode and is_boundary(episode, turn):
            to_close.append(state["open"].pop(key))
            episode = None
        # Other channels that went quiet close too.
        for other in list(state["open"]):
            if other != key and now - float(state["open"][other].get("end") or 0) >= gap_seconds():
                to_close.append(state["open"].pop(other))
        if episode is None:
            episode = _new_episode(guild_id, channel_id, turn)
        _add_turn(episode, turn)
        if len(episode["turns"]) >= max_turns():
            state["open"].pop(key, None)
            to_close.append(episode)
        else:
            state["open"][key] = episode
        for item in to_close:
            item.setdefault("tries", 0)
        state["closing"].extend(to_close)
        _save_open(guild_id, state)
    if to_close:
        await close_pending(bot, guild_id, guild_name)


async def close_pending(bot, guild_id: int, guild_name=None, *, now: float | None = None) -> int:
    """Summarize every finished episode waiting in the open file."""
    guild_id = int(guild_id)
    async with _lock(guild_id):
        state = _load_open(guild_id)
        waiting = list(state["closing"])
        state["closing"] = []
        _save_open(guild_id, state)
    if not waiting:
        return 0
    stored = 0
    retry: list[dict] = []
    known = _known_names(bot)
    for episode in waiting:
        record = None
        try:
            record = await summarize_episode(episode, known)
        except Exception as exc:
            print(f"[episodic] episode summary failed: {type(exc).__name__}", flush=True)
        if record is None:
            episode["tries"] = int(episode.get("tries") or 0) + 1
            if episode["tries"] < _CLOSE_TRIES:
                retry.append(episode)
            else:
                print(f"[episodic] gave up on an episode after {_CLOSE_TRIES} tries", flush=True)
            continue
        async with _lock(guild_id):
            records = read_records(guild_id)
            link_related(record, records)
            records.append(record)
            write_records(guild_id, _trim(records, time.time() if now is None else now))
        stored += 1
        print(
            f"[episodic] episode stored for guild {guild_id}: {len(episode.get('turns') or [])} turns, "
            f"salience {record['salience']}",
            flush=True,
        )
    if retry:
        async with _lock(guild_id):
            state = _load_open(guild_id)
            state["closing"].extend(retry)
            _save_open(guild_id, state)
    if stored:
        await write_mirror(bot, guild_id, guild_name)
    return stored


async def summarize_episode(episode: dict, known_names=()) -> dict | None:
    """One record for a finished episode. None leaves it for a later try."""
    from core.summarizer_prompt import build_episode_prompt

    turns = episode.get("turns") or []
    if not turns:
        return None
    transcript = _transcript(episode)
    if not transcript:
        return None
    start, end = float(episode.get("start") or 0), float(episode.get("end") or 0)
    when = f"{_iso(start)} to {_iso(end)}"
    data = await _summarize_json(build_episode_prompt(transcript, when, len(turns)))
    if not data:
        return None
    summary = clean_summary(data.get("summary"), known_names)
    if not summary:
        return None
    outcome = str(data.get("outcome") or "").strip().lower()
    tone = " ".join(re.findall(r"[a-z]+", str(data.get("tone") or "").lower())[:2])
    return {
        "id": episode.get("id") or "ep_" + uuid.uuid4().hex[:12],
        "level": 0,
        "guild": int(episode.get("guild") or 0),
        "channel": int(episode.get("channel") or 0),
        "start": _iso(start),
        "end": _iso(end),
        "participants": [int(p) for p in episode.get("participants") or []],
        "labels": _labels(episode.get("participants") or []),
        "turns": len(turns),
        "summary": summary,
        "outcome": outcome if outcome in _OUTCOMES else "unclear",
        "tone": tone,
        "topics": _clean_topics(data.get("topics"), known_names),
        "salience": score_salience(data.get("salience"), episode),
        "related": [],
        "sources": [],
    }


def link_related(record: dict, records: list[dict]) -> None:
    """Up to three related episodes from the last two weeks. Being linked to
    raises an older episode's salience a little (it came up again)."""
    mine = _record_terms(record)
    start = _ts(record.get("start"))
    people = set(record.get("participants") or [])
    scored = []
    for other in records:
        if other.get("id") == record.get("id"):
            continue
        if start - _ts(other.get("end")) > _LINK_DAYS * _DAY:
            continue
        score = _overlap(mine, _record_terms(other))
        if people & set(other.get("participants") or []) and score > 0:
            score += 0.1
        if score >= 0.2:
            scored.append((score, other))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    record["related"] = [other["id"] for _s, other in scored[:3]]
    for _s, other in scored[:3]:
        other["salience"] = round(min(1.0, float(other.get("salience") or 0) + 0.05), 3)
        links = list(other.get("related") or [])
        if record["id"] not in links:
            other["related"] = (links + [record["id"]])[-5:]


def _trim(records: list[dict], now: float) -> list[dict]:
    if len(records) <= _MAX_RECORDS:
        return records
    ranked = sorted(records, key=lambda r: _keep_score(r, now), reverse=True)
    keep = {r["id"] for r in ranked[:_MAX_RECORDS]}
    return [r for r in records if r["id"] in keep]


def _keep_score(record: dict, now: float) -> float:
    age = max(0.0, now - _ts(record.get("end"))) / _DAY
    return float(record.get("salience") or 0) * math.exp(-age / 60.0) + 0.2 * int(record.get("level") or 0)


# ------------------------------------------------------------------ retrieval


def score(record: dict, message_terms: set[str], speaker_id, channel_id, now: float) -> float:
    """salience × recency + topic overlap + took part + same channel."""
    age = max(0.0, now - _ts(record.get("end"))) / _DAY
    value = float(record.get("salience") or 0) * math.exp(-age / _RECENCY_DAYS)
    if message_terms:
        shared = len(message_terms & _record_terms(record))
        value += 0.5 * min(1.0, shared / max(1, min(len(message_terms), 6)))
    if speaker_id and int(speaker_id) in set(record.get("participants") or []):
        value += 0.3
    if channel_id and int(channel_id) == int(record.get("channel") or 0):
        value += 0.1
    return value


def _speaker_view(record: dict, speaker_id) -> str:
    """Labels become "this person" (the speaker) or "another member"."""
    labels = record.get("labels") or {}

    def swap(match):
        uid = labels.get(match.group(0))
        if uid is not None and speaker_id and int(uid) == int(speaker_id):
            return "this person"
        return "another member"

    return re.sub(r"\bP\d+\b", swap, str(record.get("summary") or ""))


def render_line(record: dict, speaker_id=None, channel_id=None, limit: int = 300) -> str:
    when = _day(_ts(record.get("end"))) if record.get("end") else ""
    level = int(record.get("level") or 0)
    if level == 1:
        when = f"week of {_day(_ts(record.get('start')))}"
    elif level >= 2:
        when = "earlier" if not record.get("start") else f"until {_day(_ts(record.get('end')))}"
    where = ""
    if channel_id and record.get("channel"):
        where = " here" if int(channel_id) == int(record["channel"]) else " in another channel"
    notes = ", ".join(x for x in (record.get("outcome") if record.get("outcome") != "unclear" else "", record.get("tone")) if x)
    body = _clip_prose(_speaker_view(record, speaker_id), limit) or ""
    tail = f" ({notes})" if notes else ""
    return f"- {when}{where}: {body}{tail}".strip()


def recall(guild_id, speaker_id=None, channel_id=None, message: str = "", *, limit: int | None = None, now: float | None = None, top: int = 3) -> str:
    """The best-matching past episodes, one line each, inside the prompt room."""
    from core.content_filter import clean_memory_text

    if not guild_id:
        return ""
    room = prompt_chars() if limit is None else int(limit)
    now = time.time() if now is None else float(now)
    records = read_records(int(guild_id))
    if not records:
        return ""
    wanted = terms(message)
    ranked = sorted(records, key=lambda r: score(r, wanted, speaker_id, channel_id, now), reverse=True)
    lines: list[str] = []
    used = 0
    for record in ranked:
        if len(lines) >= top:
            break
        line = clean_memory_text(render_line(record, speaker_id, channel_id))
        extra = len(line) + (1 if lines else 0)
        if used + extra > room:
            line = clean_memory_text(render_line(record, speaker_id, channel_id, limit=max(60, room - used - 40)))
            extra = len(line) + (1 if lines else 0)
            if used + extra > room:
                continue
        lines.append(line)
        used += extra
    return "\n".join(lines)


async def recall_for_prompt(bot, guild_id, speaker_id=None, channel_id=None, message: str = "") -> str:
    if not guild_id:
        return ""
    try:
        await migrate_if_needed(bot, int(guild_id))
    except Exception as exc:
        print(f"[episodic] migration check failed: {type(exc).__name__}", flush=True)
    return recall(guild_id, speaker_id, channel_id, message)


# ------------------------------------------------------------------ pin mirror


def mirror_text(records: list[dict], room: int, now: float | None = None) -> str:
    """Most important recent episodes for the readable pin."""
    now = time.time() if now is None else float(now)
    ranked = sorted(records, key=lambda r: score(r, set(), None, None, now), reverse=True)
    lines: list[str] = []
    used = 0
    for record in ranked:
        line = re.sub(r"\bP\d+\b", "a member", render_line(record, None, None, limit=260))
        extra = len(line) + (1 if lines else 0)
        if used + extra > room:
            continue
        lines.append(line)
        used += extra
    return "\n".join(lines)


async def write_mirror(bot, guild_id: int, guild_name=None) -> bool:
    from core.discord_store import THREAD_EPISODIC, ensure_bot_thread, write_pinned

    try:
        thread = await ensure_bot_thread(bot, THREAD_EPISODIC)
    except Exception:
        thread = None
    if thread is None:
        return False
    heading = episode_heading(guild_id, guild_name)
    overhead = len(heading) + len("\nSummary:\n") + 1
    prose = mirror_text(read_records(guild_id), max(100, max_store() - overhead)) or "(none yet)"
    packed = render_summary(heading, prose)
    if len(packed) > max_store():
        return False
    try:
        written, note = await write_pinned(thread, episode_prefix(guild_id), packed)
    except Exception as exc:
        print(f"[episodic] pin copy not written: {type(exc).__name__}", flush=True)
        return False
    return written is not None and note != "pin-failed"


# ------------------------------------------------------------------ migration


def _read_legacy_recent(guild_id: int) -> list[str]:
    path = recent_path(guild_id)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    except OSError:
        return []


def history_record(guild_id: int, prose: str, now: float, known_names=()) -> dict | None:
    summary = clean_summary(tidy_summary(prose).replace("\n", " "), known_names)
    if not summary:
        return None
    topic_words = sorted(terms(summary), key=lambda w: -len(w))[:5]
    return {
        "id": "ep_history_" + uuid.uuid4().hex[:8],
        "level": 2,
        "guild": int(guild_id),
        "channel": 0,
        "start": "",
        "end": _iso(now),
        "participants": [],
        "labels": {},
        "turns": 0,
        "summary": summary,
        "outcome": "unclear",
        "tone": "",
        "topics": _clean_topics(topic_words, known_names),
        "salience": 0.4,
        "related": [],
        "sources": ["legacy-pin"],
    }


async def migrate_if_needed(bot, guild_id: int) -> bool:
    """Once per server: the old prose pin becomes one level-2 history record.

    A backup of the old pin and any waiting recent.txt lines goes to
    episodes_legacy.json. A pin that cannot be read is tried again later.
    """
    guild_id = int(guild_id)
    meta = _meta(guild_id)
    if meta.get("migrated"):
        return False
    async with _lock(guild_id):
        meta = _meta(guild_id)
        if meta.get("migrated"):
            return False
        content = ""
        if bot is not None:
            from core.discord_store import THREAD_EPISODIC, ensure_bot_thread, read_pinned

            thread = await ensure_bot_thread(bot, THREAD_EPISODIC)
            if thread is not None:
                message, pins_ok = await read_pinned(thread, episode_prefix(guild_id))
                if not pins_ok:
                    return False
                content = getattr(message, "content", "") or "" if message is not None else ""
        prose, _log = split_episode(episode_body(content, episode_prefix(guild_id)))
        lines = _read_legacy_recent(guild_id)
        now = time.time()
        if content or lines:
            _write_json(_file(guild_id, _LEGACY_NAME), {"pin": content, "recent": lines, "at": _iso(now)})
        if prose:
            record = history_record(guild_id, prose, now, _known_names(bot))
            if record:
                records = read_records(guild_id)
                records.append(record)
                write_records(guild_id, records)
        path = recent_path(guild_id)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        meta["migrated"] = True
        meta["migrated_at"] = _iso(now)
        _save_meta(guild_id, meta)
        print(f"[episodic] migrated the old episode pin for guild {guild_id}", flush=True)
        return True


# ------------------------------------------------------------------ maintenance


async def close_idle(bot, guild_id: int, *, now: float | None = None) -> int:
    """Close open episodes that went quiet, then summarize what is waiting."""
    guild_id = int(guild_id)
    now = time.time() if now is None else float(now)
    async with _lock(guild_id):
        state = _load_open(guild_id)
        moved = 0
        for key in list(state["open"]):
            if now - float(state["open"][key].get("end") or 0) >= gap_seconds():
                item = state["open"].pop(key)
                item.setdefault("tries", 0)
                state["closing"].append(item)
                moved += 1
        waiting = bool(state["closing"])
        _save_open(guild_id, state)
    if waiting:
        await close_pending(bot, guild_id, None, now=now)
    return moved


def _relabel(record: dict, mapping: dict[int, str]) -> str:
    labels = record.get("labels") or {}

    def swap(match):
        uid = labels.get(match.group(0))
        return mapping.get(int(uid), "someone") if uid is not None else "someone"

    return re.sub(r"\bP\d+\b", swap, str(record.get("summary") or ""))


def _clusters(records: list[dict]) -> list[list[dict]]:
    groups: list[tuple[set[str], list[dict]]] = []
    for record in sorted(records, key=lambda r: _ts(r.get("start") or r.get("end"))):
        words = _record_terms(record)
        best = None
        for group in groups:
            if _overlap(words, group[0]) >= 0.15:
                best = group
                break
        if best is None:
            groups.append((set(words), [record]))
        else:
            best[0].update(words)
            best[1].append(record)
    return [members for _w, members in groups]


async def _merge_cluster(members: list[dict], level: int, known_names=()) -> dict | None:
    from core.summarizer_prompt import build_consolidate_prompt

    people: list[int] = []
    for record in members:
        for uid in record.get("participants") or []:
            if int(uid) not in people:
                people.append(int(uid))
    mapping = {uid: f"P{i + 1}" for i, uid in enumerate(people)}
    rows = []
    for record in members:
        when = _day(_ts(record.get("end"))) if record.get("end") else "earlier"
        rows.append(f"- {when} ({record.get('outcome') or 'unclear'}, {record.get('tone') or 'neutral'}): {_relabel(record, mapping)}")
    starts = [_ts(r.get("start")) for r in members if r.get("start")]
    ends = [_ts(r.get("end")) for r in members if r.get("end")]
    span = f"{_day(min(starts or ends))} to {_day(max(ends))}" if ends else "older"
    data = await _summarize_json(build_consolidate_prompt("\n".join(rows), span), 1400)
    if not data:
        return None
    summary = clean_summary(data.get("summary"), known_names)
    if not summary:
        return None
    outcome = str(data.get("outcome") or "").strip().lower()
    top = max(float(r.get("salience") or 0) for r in members)
    ids = {r["id"] for r in members}
    related: list[str] = []
    for record in members:
        for rid in record.get("related") or []:
            if rid not in ids and rid not in related:
                related.append(rid)
    return {
        "id": "ep_" + uuid.uuid4().hex[:12],
        "level": level,
        "guild": int(members[0].get("guild") or 0),
        "channel": int(members[0].get("channel") or 0) if len({r.get("channel") for r in members}) == 1 else 0,
        "start": _iso(min(starts)) if starts else "",
        "end": _iso(max(ends)) if ends else "",
        "participants": people,
        "labels": {label: uid for uid, label in mapping.items()},
        "turns": sum(int(r.get("turns") or 0) for r in members),
        "summary": summary,
        "outcome": outcome if outcome in _OUTCOMES else "unclear",
        "tone": " ".join(re.findall(r"[a-z]+", str(data.get("tone") or "").lower())[:2]),
        "topics": _clean_topics(data.get("topics"), known_names),
        "salience": round(min(1.0, top * 0.9 + 0.05 * (len(members) - 1)), 3),
        "related": related[:5],
        "sources": sorted(ids),
    }


def _bucket(record: dict, level: int) -> str:
    when = datetime.fromtimestamp(_ts(record.get("start") or record.get("end"))).astimezone()
    if level == 1:
        year, week, _ = when.isocalendar()
        return f"{year}-W{week:02d}"
    return when.strftime("%Y-%m")


async def consolidate(bot, guild_id: int, *, now: float | None = None) -> int:
    """Weekly summaries of episodes older than a week, monthly summaries of
    weekly ones older than a month. Low-salience originals go afterwards.
    A failed summary leaves its episodes for the next night."""
    guild_id = int(guild_id)
    now = time.time() if now is None else float(now)
    known = _known_names(bot)
    made = 0
    for source_level, target_level, age_days in ((0, 1, _CONSOLIDATE_DAYS), (1, 2, _MONTHLY_DAYS)):
        async with _lock(guild_id):
            records = read_records(guild_id)
        old = [
            r for r in records
            if int(r.get("level") or 0) == source_level
            and not r.get("consolidated_into")
            and now - _ts(r.get("end")) > age_days * _DAY
        ]
        buckets: dict[str, list[dict]] = {}
        for record in old:
            buckets.setdefault(_bucket(record, target_level), []).append(record)
        for bucket in buckets.values():
            for members in _clusters(bucket):
                if len(members) < 2:
                    lone = members[0]
                    if float(lone.get("salience") or 0) < 0.25:
                        await _drop_ids(guild_id, {lone["id"]})
                    else:
                        await _mark(guild_id, {lone["id"]}, "self")
                    continue
                merged = await _merge_cluster(members, target_level, known)
                if merged is None:
                    continue
                ids = {r["id"] for r in members}
                async with _lock(guild_id):
                    current = read_records(guild_id)
                    out = []
                    for record in current:
                        if record["id"] in ids:
                            if float(record.get("salience") or 0) < _KEEP_SALIENCE:
                                continue
                            record["consolidated_into"] = merged["id"]
                        out.append(record)
                    out.append(merged)
                    write_records(guild_id, _trim(out, now))
                made += 1
    meta = _meta(guild_id)
    meta["consolidated_at"] = _iso(now)
    _save_meta(guild_id, meta)
    if made:
        await write_mirror(bot, guild_id, None)
    return made


async def _drop_ids(guild_id: int, ids: set[str]) -> None:
    async with _lock(guild_id):
        write_records(guild_id, [r for r in read_records(guild_id) if r["id"] not in ids])


async def _mark(guild_id: int, ids: set[str], value: str) -> None:
    async with _lock(guild_id):
        records = read_records(guild_id)
        for record in records:
            if record["id"] in ids:
                record["consolidated_into"] = value
        write_records(guild_id, records)


def consolidated_at(guild_id: int) -> float:
    return _ts(_meta(int(guild_id)).get("consolidated_at") or 0)
