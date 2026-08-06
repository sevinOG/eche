# memory_file_manager.py
# Emotional / summary memory files under the portable user_dir.
# Layout:
#   memories/{user_id}.txt              (legacy flat)
#   memories/{server_id}/{user_id}.txt  (preferred, per-server)

from __future__ import annotations

import os


def _root_memories() -> str:
    try:
        from core.paths import memories_dir
        return memories_dir()
    except Exception:
        path = os.path.join(os.getcwd(), "memories")
        os.makedirs(path, exist_ok=True)
        return path


def _path(user_id, server_id=None):
    """Resolve memory file path; prefer per-server when provided."""
    if server_id is not None and str(server_id).strip():
        try:
            from core.paths import memories_dir
            base = memories_dir(server_id)
        except Exception:
            base = os.path.join(_root_memories(), str(server_id))
            os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{user_id}.txt")

    # Flat legacy + fallback
    base = _root_memories()
    flat = os.path.join(base, f"{user_id}.txt")
    if os.path.isfile(flat):
        return flat
    # Search server subfolders
    if os.path.isdir(base):
        for name in os.listdir(base):
            sub = os.path.join(base, name, f"{user_id}.txt")
            if os.path.isfile(sub):
                return sub
    return flat


def _ensure_dir_for(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)


def create_memory_file(user_id, username, server_id=None):
    path = _path(user_id, server_id)
    if os.path.exists(path):
        return
    _ensure_dir_for(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "SUMMARY:\n"
            f"User {user_id} ({username}) initialized.\n\n"
            "CODES:\n"
        )


def memory_file_exists(user_id, server_id=None):
    return os.path.exists(_path(user_id, server_id))


def append_memory(user_id, emo_code, server_id=None):
    path = _path(user_id, server_id)
    if not os.path.exists(path):
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{emo_code}\n")


def reset_message_count(user_id, server_id=None):
    return


def load_memory_summary(user_id, server_id=None):
    path = _path(user_id, server_id)
    if not os.path.exists(path):
        return ""

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    summary_lines = []
    in_summary = False

    for line in lines:
        stripped = line.strip()

        if stripped == "SUMMARY:":
            in_summary = True
            continue

        if stripped == "CODES:":
            break

        if in_summary:
            summary_lines.append(line)

    return "".join(summary_lines).strip()


def write_memory_summary(user_id, new_summary, server_id=None):
    path = _path(user_id, server_id)
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    in_summary = False

    for line in lines:
        stripped = line.strip()

        if stripped == "SUMMARY:":
            new_lines.append("SUMMARY:\n")
            new_lines.append(new_summary.strip() + "\n\n")
            in_summary = True
            continue

        if stripped == "CODES:":
            new_lines.append("CODES:\n")
            in_summary = False
            continue

        if not in_summary:
            new_lines.append(line)

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def load_raw_memory(user_id, server_id=None):
    path = _path(user_id, server_id)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""
