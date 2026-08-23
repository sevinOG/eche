from __future__ import annotations

import json
import os
import time
from typing import Any


def _history_path() -> str:
    try:
        from core.paths import memories_dir

        base = os.path.join(memories_dir(), "local")
    except Exception:
        base = os.path.join(os.getcwd(), "memories", "local")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "history.jsonl")


def ensure_local_dir() -> str:
    path = _history_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def append_turn(role: str, content: str) -> None:
    role = (role or "").strip().lower()
    if role not in ("user", "assistant"):
        role = "user"
    content = (content or "").strip()
    if not content:
        return
    path = ensure_local_dir()
    row = {"role": role, "content": content, "ts": time.time()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_turns(limit: int | None = None) -> list[dict[str, Any]]:
    path = _history_path()
    if not os.path.isfile(path):
        return []
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("content"):
                rows.append(obj)
    if limit is not None and limit > 0:
        rows = rows[-limit:]
    return rows


def clear_turns() -> None:
    path = _history_path()
    if os.path.isfile(path):
        open(path, "w", encoding="utf-8").close()