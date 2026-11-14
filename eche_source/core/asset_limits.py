# core/asset_limits.py
# Per-user and per-guild hourly caps. There is no strike or lockout system
# (removed 2026-11-14 at the owner's request); every check still blocks.

from __future__ import annotations

import json
import os
import tempfile
import threading
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class HourlyCaps:
    """Hourly ?asset caps. Images and videos are counted separately."""

    user_images: int
    user_videos: int
    guild_images: int
    guild_videos: int

    @property
    def images(self) -> tuple[int, int]:
        return (self.user_images, self.guild_images)

    @property
    def videos(self) -> tuple[int, int]:
        return (self.user_videos, self.guild_videos)


# The one place the hourly caps are set. They are the same with owner
# approval on or off; the bot owner has none. A future plan could supply a
# different HourlyCaps here; only the standard one exists today.
STANDARD_CAPS = HourlyCaps(user_images=10, user_videos=5, guild_images=40, guild_videos=15)

USER_HOURLY_CAP = STANDARD_CAPS.user_images
GUILD_HOURLY_CAP = STANDARD_CAPS.guild_images
WINDOW_SECONDS = 60 * 60

USER_CAP_REFUSAL = "The hourly asset limit for this user has been reached."
VIDEO_USER_CAP_REFUSAL = "The hourly video limit for this user has been reached."
VIDEO_GUILD_CAP_REFUSAL = "The hourly video limit for this server has been reached."
GUILD_CAP_REFUSAL = "The hourly asset limit for this server has been reached."
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
    # Official tests must not leave asset_limits.json in the source tree.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            source = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
            if os.path.abspath(root) == source:
                tmp = os.environ.get("ECHE_TEST_LIMITS_ROOT")
                if not tmp:
                    tmp = tempfile.mkdtemp(prefix="eche-limits-")
                    os.environ["ECHE_TEST_LIMITS_ROOT"] = tmp
                root = tmp
        except Exception:
            pass
    folder = os.path.join(root, "config")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "asset_limits.json")


def _load(root: str | None = None) -> dict:
    path = _path(root)
    if not os.path.exists(path):
        return {"users": {}, "guilds": {}}
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


# Fields from the removed strike/lockout system. Ignored on read and dropped
# the next time the file is written.
RETIRED_FIELDS = ("lockouts", "lockout_reasons", "media_filter_hits", "age_block_hits")


def _save(data: dict, root: str | None = None) -> None:
    for key in RETIRED_FIELDS:
        data.pop(key, None)
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


def minutes_until_slot(stamps: list, cap: int, now: float | None = None) -> int:
    """Whole minutes until one more slot frees in the hourly window."""
    now = _now() if now is None else now
    live = sorted(float(s) for s in _prune(list(stamps or []), now))
    if not live or len(live) < cap:
        return 0
    oldest_blocking = live[len(live) - cap] if cap > 0 else live[0]
    return max(1, math.ceil((oldest_blocking + WINDOW_SECONDS - now) / 60))


def _with_reset(text: str, stamps: list, cap: int, now: float) -> str:
    minutes = minutes_until_slot(stamps, cap, now)
    if minutes <= 0:
        return text
    return f"{text} It resets in {minutes} min."


def rate_refusal(user_id, guild_id, *, root: str | None = None, caps: tuple[int, int] | None = None) -> str | None:
    """A refusal if the user or guild is over the hourly image cap. Does not count."""
    try:
        data = _load(root)
    except LimitsCorrupt:
        return LIMITS_UNAVAILABLE
    now = _now()
    if caps is not None:
        user_cap, guild_cap = caps
    else:
        try:
            from core.asset_safety import hourly_caps
            user_cap, guild_cap = hourly_caps()
        except Exception:
            user_cap, guild_cap = USER_HOURLY_CAP, GUILD_HOURLY_CAP
    if user_id is not None:
        stamps = _prune((data.get("users") or {}).get(str(user_id), []), now)
        if len(stamps) >= user_cap:
            return _with_reset(USER_CAP_REFUSAL, stamps, user_cap, now)
    if guild_id is not None:
        stamps = _prune((data.get("guilds") or {}).get(str(guild_id), []), now)
        if len(stamps) >= guild_cap:
            return _with_reset(GUILD_CAP_REFUSAL, stamps, guild_cap, now)
    return None


def video_rate_refusal(user_id, guild_id, caps: tuple[int, int], *, root: str | None = None) -> str | None:
    """A refusal if the user or guild is over the hourly video cap. Does not count."""
    try:
        data = _load(root)
    except LimitsCorrupt:
        return LIMITS_UNAVAILABLE
    now = _now()
    user_cap, guild_cap = caps
    if user_id is not None:
        stamps = _prune((data.get("video_users") or {}).get(str(user_id), []), now)
        if len(stamps) >= user_cap:
            return _with_reset(VIDEO_USER_CAP_REFUSAL, stamps, user_cap, now)
    if guild_id is not None:
        stamps = _prune((data.get("video_guilds") or {}).get(str(guild_id), []), now)
        if len(stamps) >= guild_cap:
            return _with_reset(VIDEO_GUILD_CAP_REFUSAL, stamps, guild_cap, now)
    return None


def no_slot_refusal(
    user_id,
    guild_id,
    *,
    root: str | None = None,
    caps: tuple[int, int] | None = None,
    video_caps: tuple[int, int] | None = None,
) -> str | None:
    """Early gate before the kind is known: refuse only when neither an image
    nor a video slot is left. The exact check is reserve_use."""
    image = rate_refusal(user_id, guild_id, root=root, caps=caps)
    if not image or video_caps is None:
        return image
    video = video_rate_refusal(user_id, guild_id, video_caps, root=root)
    return image if video else None


def _buckets(video: bool) -> tuple[tuple[str, str], ...]:
    # Images and videos have separate buckets; a video uses no image slot.
    if video:
        return (("video_users", "video_guilds"),)
    return (("users", "guilds"),)


def record_use(user_id, guild_id, *, root: str | None = None, video: bool = False) -> None:
    try:
        data = _load(root)
    except LimitsCorrupt:
        return
    now = _now()
    for user_bucket, guild_bucket in _buckets(video):
        if user_id is not None:
            users = data.setdefault(user_bucket, {})
            users[str(user_id)] = _prune(users.get(str(user_id), []), now) + [now]
        if guild_id is not None:
            guilds = data.setdefault(guild_bucket, {})
            guilds[str(guild_id)] = _prune(guilds.get(str(guild_id), []), now) + [now]
    _save(data, root)


def release_use(user_id, guild_id, *, root: str | None = None, video: bool = False) -> None:
    """Give back the newest cap slot after a blocked, refused, or failed attempt."""
    with _LOCK:
        try:
            data = _load(root)
        except LimitsCorrupt:
            return
        now = _now()
        changed = False
        pairs = []
        for user_bucket, guild_bucket in _buckets(video):
            pairs += [(user_bucket, user_id), (guild_bucket, guild_id)]
        for bucket, key in pairs:
            if key is None:
                continue
            table = data.setdefault(bucket, {})
            stamps = _prune(table.get(str(key), []), now)
            if stamps:
                stamps.remove(max(stamps))
                changed = True
            table[str(key)] = stamps
        if changed:
            _save(data, root)


def reserve_use(
    user_id,
    guild_id,
    *,
    root: str | None = None,
    caps: tuple[int, int] | None = None,
    video_caps: tuple[int, int] | None = None,
) -> str | None:
    """Atomically refuse or take a cap slot at the start of an attempt.

    With video_caps, the attempt is a video and is checked and counted only
    against the video caps. Otherwise it is an image under caps.
    """
    with _LOCK:
        try:
            data = _load(root)
        except LimitsCorrupt:
            return LIMITS_UNAVAILABLE
        if video_caps is not None:
            refusal = video_rate_refusal(user_id, guild_id, video_caps, root=root)
        else:
            refusal = rate_refusal(user_id, guild_id, root=root, caps=caps)
        if refusal:
            return refusal
        record_use(user_id, guild_id, root=root, video=video_caps is not None)
        return None


def in_direct_message(guild_id) -> bool:
    return guild_id is None


CAP_REFUSALS = (
    USER_CAP_REFUSAL,
    GUILD_CAP_REFUSAL,
    VIDEO_USER_CAP_REFUSAL,
    VIDEO_GUILD_CAP_REFUSAL,
)


def is_cap_refusal(text: str) -> bool:
    return any(str(text or "").startswith(base) for base in CAP_REFUSALS)
