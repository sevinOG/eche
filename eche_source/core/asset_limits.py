# core/asset_limits.py
# Per-user and per-guild hourly caps, and a 24-hour lockout after a block.

from __future__ import annotations

import json
import os
import threading
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
LIMITS_UNAVAILABLE = "Asset generation is temporarily unavailable."

_LOCK = threading.Lock()
_corrupt_roots: set[str] = set()


class LimitsCorrupt(Exception):
    """The limits file exists but could not be parsed. Fail closed."""


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
    if not os.path.exists(path):
        return {"users": {}, "guilds": {}, "lockouts": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _corrupt_roots.discard(path)
            return data
    except Exception:
        pass
    _corrupt_roots.add(path)
    raise LimitsCorrupt(path)


def _save(data: dict, root: str | None = None) -> None:
    path = _path(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    _corrupt_roots.discard(path)


def _now() -> float:
    return time.time()


def _prune(stamps: list, now: float, window: float = WINDOW_SECONDS) -> list:
    return [s for s in stamps if isinstance(s, (int, float)) and now - float(s) < window]


def is_locked(user_id, *, root: str | None = None) -> bool:
    if user_id is None:
        return False
    try:
        data = _load(root)
    except LimitsCorrupt:
        return True
    until = (data.get("lockouts") or {}).get(str(user_id))
    try:
        return float(until) > _now()
    except (TypeError, ValueError):
        return False


def lockout_user(user_id, *, root: str | None = None) -> None:
    if user_id is None:
        return
    try:
        data = _load(root)
    except LimitsCorrupt:
        return
    lockouts = data.setdefault("lockouts", {})
    lockouts[str(user_id)] = _now() + LOCKOUT_SECONDS
    _save(data, root)



MEDIA_FILTER_STRIKE_WINDOW = WINDOW_SECONDS
MEDIA_FILTER_STRIKES_TO_LOCK = 2


def note_media_filter_block(user_id, *, root: str | None = None) -> bool:
    """Record a media-filter hit. Lock only after two hits in an hour."""
    if user_id is None:
        return False
    try:
        data = _load(root)
    except LimitsCorrupt:
        return False
    now = _now()
    hits = data.setdefault("media_filter_hits", {})
    key = str(user_id)
    stamps = _prune(list(hits.get(key) or []), now, MEDIA_FILTER_STRIKE_WINDOW)
    stamps.append(now)
    hits[key] = stamps
    _save(data, root)
    return len(stamps) >= MEDIA_FILTER_STRIKES_TO_LOCK


def lockout_until(user_id, *, root: str | None = None) -> str:
    try:
        data = _load(root)
    except LimitsCorrupt:
        return ""
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
    try:
        data = _load(root)
    except LimitsCorrupt:
        return LIMITS_UNAVAILABLE
    now = _now()
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
    try:
        data = _load(root)
    except LimitsCorrupt:
        return
    now = _now()
    if user_id is not None:
        users = data.setdefault("users", {})
        users[str(user_id)] = _prune(users.get(str(user_id), []), now) + [now]
    if guild_id is not None:
        guilds = data.setdefault("guilds", {})
        guilds[str(guild_id)] = _prune(guilds.get(str(guild_id), []), now) + [now]
    _save(data, root)


def reserve_use(user_id, guild_id, *, root: str | None = None) -> str | None:
    """Atomically refuse or take a cap slot at the start of an attempt."""
    with _LOCK:
        try:
            data = _load(root)
        except LimitsCorrupt:
            return LIMITS_UNAVAILABLE
        if user_id is not None:
            until = (data.get("lockouts") or {}).get(str(user_id))
            try:
                if float(until) > _now():
                    return LOCKOUT_REFUSAL
            except (TypeError, ValueError):
                pass
        refusal = rate_refusal(user_id, guild_id, root=root)
        if refusal:
            return refusal
        record_use(user_id, guild_id, root=root)
        return None


def in_direct_message(guild_id) -> bool:
    return guild_id is None
