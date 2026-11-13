# core/asset_limits.py
# Per-user and per-guild hourly caps, and a 24-hour lockout after a block.

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

USER_HOURLY_CAP = 10
GUILD_HOURLY_CAP = 30
LOCKOUT_SECONDS = 24 * 60 * 60
WINDOW_SECONDS = 60 * 60

USER_CAP_REFUSAL = "The hourly asset limit for this user has been reached."
GUILD_CAP_REFUSAL = "The hourly asset limit for this server has been reached."
LOCKOUT_REFUSAL = "Asset generation is locked for this user for 24 hours."
DM_REFUSAL = "Asset generation is not available in direct messages."


def _path(root: str | None = None) -> str:
    if not root:
        try:
            from core.secrets import _user_root

            root = _user_root()
        except Exception:
            root = os.getcwd()
    folder = os.path.join(root, "config")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "asset_limits.json")


def _load(root: str | None = None) -> dict:
    path = _path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"users": {}, "guilds": {}, "lockouts": {}}


def _save(data: dict, root: str | None = None) -> None:
    path = _path(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _now() -> float:
    return time.time()


def _prune(stamps: list, now: float, window: float = WINDOW_SECONDS) -> list:
    return [s for s in stamps if isinstance(s, (int, float)) and now - float(s) < window]


def is_locked(user_id, *, root: str | None = None) -> bool:
    if user_id is None:
        return False
    data = _load(root)
    until = (data.get("lockouts") or {}).get(str(user_id))
    try:
        return float(until) > _now()
    except (TypeError, ValueError):
        return False


def lockout_user(user_id, *, root: str | None = None) -> None:
    if user_id is None:
        return
    data = _load(root)
    lockouts = data.setdefault("lockouts", {})
    lockouts[str(user_id)] = _now() + LOCKOUT_SECONDS
    _save(data, root)


def lockout_until(user_id, *, root: str | None = None) -> str:
    data = _load(root)
    until = (data.get("lockouts") or {}).get(str(user_id))
    try:
        ts = float(until)
    except (TypeError, ValueError):
        return ""
    if ts <= _now():
        return ""
    stamp = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return stamp.strftime("%Y-%m-%d %H:%M")


def rate_refusal(user_id, guild_id, *, root: str | None = None) -> str | None:
    """A refusal if the user or guild is over the hourly cap. Does not count."""
    now = _now()
    data = _load(root)
    if user_id is not None:
        stamps = _prune((data.get("users") or {}).get(str(user_id), []), now)
        if len(stamps) >= USER_HOURLY_CAP:
            return USER_CAP_REFUSAL
    if guild_id is not None:
        stamps = _prune((data.get("guilds") or {}).get(str(guild_id), []), now)
        if len(stamps) >= GUILD_HOURLY_CAP:
            return GUILD_CAP_REFUSAL
    return None


def record_use(user_id, guild_id, *, root: str | None = None) -> None:
    now = _now()
    data = _load(root)
    if user_id is not None:
        users = data.setdefault("users", {})
        users[str(user_id)] = _prune(users.get(str(user_id), []), now) + [now]
    if guild_id is not None:
        guilds = data.setdefault("guilds", {})
        guilds[str(guild_id)] = _prune(guilds.get(str(guild_id), []), now) + [now]
    _save(data, root)


def in_direct_message(guild_id) -> bool:
    return guild_id is None
