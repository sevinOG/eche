# personal_recent.py
# Last few full messages for one person, on disk.
# The Discord pin keeps the long-term summary. These files are the rolling
# buffer the prompt always sees. A fold does not empty them.

from __future__ import annotations

import json
import os

KEEP = 3
FOLD_EVERY = 3


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
    return {
        "pending": max(0, pending),
        "stash": [str(line) for line in stash if str(line).strip()] if isinstance(stash, list) else [],
        "messages": (
            [str(line) for line in messages if str(line).strip()][-KEEP:]
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
    """The buffer, oldest first."""
    try:
        return list(_load(int(user_id), side)["messages"])
    except (TypeError, ValueError):
        return []


def push(user_id: int, side: str, text: str) -> tuple[list[str], int]:
    """Append one full message. Returns the buffer and how many are waiting on a fold."""
    body = str(text or "").strip()
    if not body:
        state = _load(int(user_id), side)
        return list(state["messages"]), int(state["pending"])
    state = _load(int(user_id), side)
    state["messages"].append(body)
    state["pending"] = int(state["pending"]) + 1
    # Newest stays in the last slot. The oldest drops once three are stored.
    state["messages"] = state["messages"][-KEEP:]
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
    """Stashed pin lines, then the buffer. Duplicates are skipped."""
    try:
        state = _load(int(user_id), side)
    except (TypeError, ValueError):
        return []
    notes: list[str] = []
    for line in list(state["stash"]) + list(state["messages"]):
        text = str(line).strip()
        if text and text not in notes:
            notes.append(text)
    return notes


def prompt_lines(user_id: int, side: str) -> list[str]:
    """Lines the prompt should still see, including pin lines waiting on a fold."""
    return notes_for_fold(user_id, side)


def has_stash(user_id: int, side: str) -> bool:
    try:
        return bool(_load(int(user_id), side)["stash"])
    except (TypeError, ValueError):
        return False


def clear_folded(user_id: int, side: str) -> None:
    """The summary landed. The three messages stay. The stash does not."""
    state = _load(int(user_id), side)
    state["pending"] = 0
    state["stash"] = []
    state["messages"] = state["messages"][-KEEP:]
    _save(int(user_id), side, state)
