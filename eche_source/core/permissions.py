"""Owner and Admin tiers.

Admin is today's list: Settings -> Security "Owner IDs" (ECHE_OWNER_ID), or the
Discord application owner when that field is blank. Eche.is_owner and every
@commands.is_owner() keep meaning Admin.

Owner is one Discord user above Admin, and only exists when a host (Waerhouse)
runs Eche:

* Hosted means the host said so: the bot class sets ``owner_tier_source`` (a
  path), or the ECHE_OWNER_TIER_FILE environment variable is present (even
  blank). Removing the file never turns hosted back into standalone.
* The file is ECHE_OWNER_TIER_FILE (or owner_tier_source). A relative path is
  resolved against Eche's backend folder (core.paths.package_root(): the folder
  holding core/ when run from source, the folder holding Eche.exe when frozen).
  When hosted with no path given, the file is ``waerhouse_runtime.json`` in that
  backend folder.
* The file must be a regular file (not a link), at most 64 KB, a JSON object
  whose ``owner_uid`` is exactly 17-20 digits (string or integer; booleans,
  floats, mentions and spaces are refused). The Owner must also be an Admin.
* It is re-read at most every 2 seconds, and only when its mtime or size changed.
* Fail closed: missing, unreadable, malformed, or anything raising means no
  Owner, so every Owner-only power is refused. Admin powers are not affected.
* Standalone (not hosted): Owner is the same as Admin, which is the behaviour
  before this tier existed.

The UID is never logged; only the state (ok / missing / invalid) when it changes.
"""
from __future__ import annotations

import json
import os
import re
import stat
import threading
import time

from discord.ext import commands

ENV_FILE = "ECHE_OWNER_TIER_FILE"
DEFAULT_FILE = "waerhouse_runtime.json"
KEY = "owner_uid"
MAX_BYTES = 64 * 1024
REFRESH_SECONDS = 2.0
_UID = re.compile(r"[0-9]{17,20}")

_lock = threading.Lock()
_cache: dict = {"path": None, "sig": None, "checked": None, "uid": None, "state": None}
_last_logged: str | None = None
_class_source: object = None  # the bot class's owner_tier_source, set by register_bot


def register_bot(bot) -> None:
    """Remember the host's owner_tier_source (class or instance attribute)."""
    global _class_source
    _class_source = getattr(bot, "owner_tier_source", None)


def _source(bot=None):
    if bot is not None:
        value = getattr(bot, "owner_tier_source", None)
        if value is not None:
            return value
    return _class_source


def hosted(bot=None) -> bool:
    return _source(bot) is not None or ENV_FILE in os.environ


def backend_dir() -> str:
    try:
        from core.paths import package_root

        return package_root()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def owner_file(bot=None) -> str | None:
    """The owner file's absolute path, or None when standalone."""
    if not hosted(bot):
        return None
    raw = os.environ.get(ENV_FILE)
    if raw is None or not str(raw).strip():
        src = _source(bot)
        raw = src if isinstance(src, (str, os.PathLike)) else ""
    text = os.fspath(raw).strip() if raw else ""
    if not text:
        text = DEFAULT_FILE
    if not os.path.isabs(text):
        text = os.path.join(backend_dir(), text)
    return os.path.abspath(text)


def parse_uid(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not _UID.fullmatch(value):
        return None
    return int(value)


def _read(path: str) -> tuple[int | None, str]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "invalid"
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        return None, "invalid"
    try:
        with open(path, "rb") as f:
            data = json.loads(f.read(MAX_BYTES + 1).decode("utf-8"))
    except Exception:
        return None, "invalid"
    if not isinstance(data, dict):
        return None, "invalid"
    uid = parse_uid(data.get(KEY))
    return (uid, "ok") if uid else (None, "invalid")


def _signature(path: str):
    try:
        info = os.lstat(path)
        return (info.st_mtime_ns, info.st_size, info.st_mode)
    except OSError:
        return None


def _log_state(state: str) -> None:
    global _last_logged
    if state == _last_logged:
        return
    _last_logged = state
    try:
        from core.audit import record

        record("owner-tier", state=state)
    except Exception:
        pass


def _lookup(bot=None, *, now: float | None = None) -> tuple[int | None, str]:
    path = owner_file(bot)
    if path is None:
        return None, "standalone"
    now = time.monotonic() if now is None else now
    try:
        with _lock:
            fresh = (
                _cache["path"] == path
                and _cache["checked"] is not None
                and now - _cache["checked"] < REFRESH_SECONDS
            )
            if not fresh:
                sig = _signature(path)
                if not (_cache["path"] == path and sig is not None and sig == _cache["sig"]):
                    uid, state = _read(path)
                    _cache.update(path=path, sig=sig, uid=uid, state=state)
                _cache["checked"] = now
            uid, state = _cache["uid"], _cache["state"]
    except Exception:
        uid, state = None, "invalid"
    _log_state(state)
    return uid, state


def owner_uid(bot=None) -> int | None:
    """The Owner's Discord id when hosted and the file is good; None otherwise."""
    return _lookup(bot)[0]


def owner_state(bot=None) -> str:
    """'standalone', 'ok', 'missing' or 'invalid'."""
    return _lookup(bot)[1]


async def is_admin(bot, user) -> bool:
    from core.admin_tools import author_is_owner

    try:
        return bool(await author_is_owner(bot, user))
    except Exception:
        return False


async def is_owner(bot, user) -> bool:
    """Owner tier. Standalone: same as Admin. Hosted: the one id in the owner file, and an Admin."""
    if user is None:
        return False
    try:
        if not hosted(bot):
            return await is_admin(bot, user)
        uid = owner_uid(bot)
        if not uid or getattr(user, "id", None) != uid:
            return False
        return await is_admin(bot, user)
    except Exception:
        return False


def protected_ids(bot=None) -> set[int]:
    """Ids moderation tools never act on: every Admin id and the Owner."""
    ids: set[int] = set()
    try:
        from core.admin_tools import configured_owner_ids

        ids.update(configured_owner_ids())
    except Exception:
        pass
    try:
        uid = owner_uid(bot)
        if uid:
            ids.add(uid)
    except Exception:
        pass
    return ids


class NotOwnerTier(commands.NotOwner):
    """Refused: an Owner-only command, and this person is not the Owner."""


def owner_only():
    """Command check: Owner tier only (Admin when standalone)."""

    async def predicate(ctx) -> bool:
        if await is_owner(ctx.bot, getattr(ctx, "author", None)):
            return True
        raise NotOwnerTier("Owner only.")

    return commands.check(predicate)


def _reset_for_tests() -> None:
    global _last_logged, _class_source
    with _lock:
        _cache.update(path=None, sig=None, checked=None, uid=None, state=None)
    _last_logged = None
    _class_source = None
