# personal_recent.py
# Last few full messages for one person, on disk.
# The Discord pin keeps the long-term summary. These files are the rolling
# buffer the prompt always sees. A fold does not empty them.

from __future__ import annotations

import json
import os

# Defaults. The live values are Settings → Memory & Prompts → Context & Memory
# (memory_recent_messages, memory_fold_every). The quiet time is
# ECHE_MEMORY_FOLD_IDLE_MINUTES, else FOLD_IDLE_MINUTES.
KEEP = 3
# The long-term memory is updated after this many of the person's messages,
# or once the chat has been quiet for FOLD_IDLE_MINUTES. The fold sees both
# sides of the conversation.
FOLD_EVERY = 8
FOLD_IDLE_MINUTES = 30
# Messages waiting on a fold are kept even past KEEP, up to this many, so a
# slow fold setting or a failing summarizer does not drop them.
_PENDING_CEILING = 40


def keep() -> int:
    """Full messages the prompt sees for one side."""
    from core.context_caps import get

    return get("memory_recent_messages")


def fold_every() -> int:
    """New messages before the long-term memory is updated."""
    from core.context_caps import get

    return get("memory_fold_every")


def fold_idle_seconds() -> float:
    """Quiet time after which waiting messages are folded anyway."""
    raw = (os.environ.get("ECHE_MEMORY_FOLD_IDLE_MINUTES") or "").strip()
    try:
        minutes = max(5, min(240, int(float(raw)))) if raw else FOLD_IDLE_MINUTES
    except ValueError:
        minutes = FOLD_IDLE_MINUTES
    return float(minutes) * 60.0


def clip_message(text: str, cap: int | None = None) -> str:
    """One buffered message. Settings → One remembered message; 0 keeps it whole."""
    body = str(text or "").strip()
    if cap is None:
        from core.context_caps import get

        cap = get("memory_message_chars")
    cap = int(cap)
    if cap <= 0 or len(body) <= cap:
        return body
    return body[: max(1, cap - 1)].rstrip() + "…"


def _held(pending: int) -> int:
    """Buffer length: what the prompt sees, or what the next fold needs."""
    return max(keep(), min(max(0, int(pending)), _PENDING_CEILING))


def _directory(user_id: int) -> str:
    """memories/user-{id}/. Separate from memories/{guild id}/recent.txt."""
    from core.paths import memories_dir

    path = os.path.join(memories_dir(), f"user-{int(user_id)}")
    os.makedirs(path, exist_ok=True)
    return path


def _path(user_id: int, side: str) -> str:
    name = "bot.json" if side == "bot" else "user.json"
    return os.path.join(_directory(user_id), name)


def _empty() -> dict:
    return {"pending": 0, "stash": [], "messages": [], "times": [], "last_at": 0.0}


def _now() -> float:
    import time

    return time.time()


def _load(user_id: int, side: str) -> dict:
    path = _path(user_id, side)
    if not os.path.isfile(path):
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    messages = data.get("messages")
    stash = data.get("stash")
    try:
        pending = int(data.get("pending") or 0)
    except (TypeError, ValueError):
        pending = 0
    pending = max(0, pending)
    rows: list[str] = []
    stamps: list[float] = []
    raw_times = data.get("times") if isinstance(data.get("times"), list) else []
    if isinstance(messages, list):
        # Older files have no times. Those messages count as time 0.
        offset = len(messages) - len(raw_times)
        for index, line in enumerate(messages):
            if not str(line).strip():
                continue
            rows.append(str(line))
            at = raw_times[index - offset] if 0 <= index - offset < len(raw_times) else 0.0
            try:
                stamps.append(float(at or 0.0))
            except (TypeError, ValueError):
                stamps.append(0.0)
    held = _held(pending)
    try:
        last_at = float(data.get("last_at") or 0.0)
    except (TypeError, ValueError):
        last_at = 0.0
    return {
        "pending": pending,
        "stash": [str(line) for line in stash if str(line).strip()] if isinstance(stash, list) else [],
        "messages": rows[-held:],
        "times": stamps[-held:],
        "last_at": last_at,
    }


def _save(user_id: int, side: str, state: dict) -> None:
    path = _path(user_id, side)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def messages(user_id: int, side: str) -> list[str]:
    """The newest messages the prompt sees, oldest first."""
    try:
        return list(_load(int(user_id), side)["messages"])[-keep():]
    except (TypeError, ValueError):
        return []


def push(user_id: int, side: str, text: str) -> tuple[list[str], int]:
    """Append one full message. Returns the buffer and how many are waiting on a fold."""
    from core.content_filter import clean_memory_text

    body = clip_message(clean_memory_text(str(text or "")))
    if not body:
        state = _load(int(user_id), side)
        return list(state["messages"]), int(state["pending"])
    state = _load(int(user_id), side)
    now = _now()
    state["messages"].append(body)
    state["times"].append(now)
    state["last_at"] = now
    state["pending"] = int(state["pending"]) + 1
    # Newest stays in the last slot. The oldest drops once the buffer is full
    # and it is no longer waiting on a fold.
    held = _held(state["pending"])
    state["messages"] = state["messages"][-held:]
    state["times"] = state["times"][-held:]
    _save(int(user_id), side, state)
    path = _path(int(user_id), side)
    print(
        f"[memory] {path} has {len(state['messages'])} message(s), "
        f"{state['pending']} since the last summary",
        flush=True,
    )
    return list(state["messages"]), int(state["pending"])


def add_stash(user_id: int, side: str, lines: list[str]) -> None:
    """Keep pin lines that are leaving the Discord message, until a fold takes them."""
    from core.content_filter import clean_lines

    incoming = clean_lines(lines)
    if not incoming:
        return
    state = _load(int(user_id), side)
    have = set(state["stash"])
    for line in incoming:
        if line not in have:
            state["stash"].append(line)
            have.add(line)
    _save(int(user_id), side, state)


def notes_for_fold(user_id: int, side: str) -> list[str]:
    """Stashed pin lines, then the messages since the last fold. Duplicates are skipped.

    With the defaults (keep 3, fold every 3) that is the whole buffer. When
    the buffer keeps more than a fold needs, messages already folded stay out.
    """
    try:
        state = _load(int(user_id), side)
    except (TypeError, ValueError):
        return []
    pending = int(state["pending"])
    buffer = list(state["messages"])
    fresh = buffer[-max(pending, min(keep(), fold_every())):] if buffer else []
    notes: list[str] = []
    for line in list(state["stash"]) + fresh:
        text = str(line).strip()
        if text and text not in notes:
            notes.append(text)
    return notes


def pending_rows(user_id: int, side: str) -> list[tuple[float, str]]:
    """(time, text) for stashed pin lines and messages waiting on a fold.

    Stashed lines from an older pin have no time and sort first.
    """
    try:
        state = _load(int(user_id), side)
    except (TypeError, ValueError):
        return []
    pending = int(state["pending"])
    rows: list[tuple[float, str]] = []
    seen: set[str] = set()
    for line in state["stash"]:
        text = str(line).strip()
        if text and text not in seen:
            rows.append((0.0, text))
            seen.add(text)
    if pending > 0:
        tail = list(zip(state["times"], state["messages"]))[-pending:]
        for at, line in tail:
            text = str(line).strip()
            if text and text not in seen:
                rows.append((float(at or 0.0), text))
                seen.add(text)
    return rows


def pending_count(user_id: int, side: str) -> int:
    try:
        return int(_load(int(user_id), side)["pending"])
    except (TypeError, ValueError):
        return 0


def last_at(user_id: int, side: str) -> float:
    try:
        return float(_load(int(user_id), side)["last_at"])
    except (TypeError, ValueError):
        return 0.0


def users_waiting() -> list[int]:
    """Every person with messages waiting on a fold (for the idle sweep)."""
    from core.paths import memories_dir

    base = memories_dir()
    out: list[int] = []
    if not os.path.isdir(base):
        return out
    for name in os.listdir(base):
        if not name.startswith("user-"):
            continue
        try:
            uid = int(name[len("user-"):])
        except ValueError:
            continue
        if pending_count(uid, "user") > 0 or pending_count(uid, "bot") > 0 or has_stash(uid, "user") or has_stash(uid, "bot"):
            out.append(uid)
    return out


def prompt_lines(user_id: int, side: str) -> list[str]:
    """Lines the prompt should still see: pin lines waiting on a fold, then the
    newest messages (Settings → Recent messages kept)."""
    try:
        state = _load(int(user_id), side)
    except (TypeError, ValueError):
        return []
    lines: list[str] = []
    for line in list(state["stash"]) + list(state["messages"])[-keep():]:
        text = str(line).strip()
        if text and text not in lines:
            lines.append(text)
    return lines


def has_stash(user_id: int, side: str) -> bool:
    try:
        return bool(_load(int(user_id), side)["stash"])
    except (TypeError, ValueError):
        return False


def clear_folded(user_id: int, side: str, count: int | None = None) -> None:
    """The memory landed. The newest messages stay. The stash does not.

    count is how many waiting messages the fold took. Messages that arrived
    while the fold ran stay waiting. None clears every waiting message.
    """
    state = _load(int(user_id), side)
    if count is None:
        state["pending"] = 0
    else:
        state["pending"] = max(0, int(state["pending"]) - max(0, int(count)))
    state["stash"] = []
    held = max(keep(), state["pending"])
    state["messages"] = state["messages"][-held:]
    state["times"] = state["times"][-held:]
    _save(int(user_id), side, state)
