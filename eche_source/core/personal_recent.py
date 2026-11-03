# personal_recent.py
# Last few full messages for one person, on disk.
# The Discord pin keeps the long-term summary. These files are the rolling
# buffer the prompt always sees. A fold does not empty them.

from __future__ import annotations

import json
import os

# Defaults. The live values are Settings → Memory & Prompts → Context & Memory
# (memory_recent_messages, memory_fold_every).
KEEP = 3
FOLD_EVERY = 3
# Messages waiting on a fold are kept even past KEEP, up to this many, so a
# slow fold setting or a failing summarizer does not drop them.
_PENDING_CEILING = 40


def keep() -> int:
    """Full messages the prompt sees for one side."""
    from core.context_caps import get

    return get("memory_recent_messages")


def fold_every() -> int:
    """New messages before the long-term summary is updated."""
    from core.context_caps import get

    return get("memory_fold_every")


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
    return {"pending": 0, "stash": [], "messages": []}


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
    return {
        "pending": pending,
        "stash": [str(line) for line in stash if str(line).strip()] if isinstance(stash, list) else [],
        "messages": (
            [str(line) for line in messages if str(line).strip()][-_held(pending):]
            if isinstance(messages, list)
            else []
        ),
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
    body = clip_message(text)
    if not body:
        state = _load(int(user_id), side)
        return list(state["messages"]), int(state["pending"])
    state = _load(int(user_id), side)
    state["messages"].append(body)
    state["pending"] = int(state["pending"]) + 1
    # Newest stays in the last slot. The oldest drops once the buffer is full
    # and it is no longer waiting on a fold.
    state["messages"] = state["messages"][-_held(state["pending"]):]
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
    incoming = [str(line).strip() for line in lines if str(line).strip()]
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


def clear_folded(user_id: int, side: str) -> None:
    """The summary landed. The newest messages stay. The stash does not."""
    state = _load(int(user_id), side)
    state["pending"] = 0
    state["stash"] = []
    state["messages"] = state["messages"][-keep():]
    _save(int(user_id), side, state)
