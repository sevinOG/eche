# core/admin_tools.py
# Owner admin prompt. The main-window toggle decides if this file is visible.
# Off: the markdown is not read into the prompt, and owner tools are not offered.

from __future__ import annotations

import os
import re

_MARKER = "<!-- inject -->"
_ON = ("1", "true", "yes", "on")
_SNOWFLAKE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")

_FALLBACK = """# Admin tools

This file is the owner-only admin prompt. Eche receives the text under the
inject marker only while Admin tools is checked, and only on a turn with the
owner. With the toggle off, this file is not read into the prompt and mute,
timeout, kick, and ban are left out of the tool list.

The owner is the Owner ID in Settings → Security. Leave the marker line as it is.

<!-- inject -->
Call mute, timeout, kick, or ban only when the owner explicitly asks for that
action on one member of this server. Do not pick a target they did not name.
Do not act on the owner, on yourself, or on everyone. A timeout needs a
duration they gave, at most 28 days. Mute is a voice-channel server mute.
Timeout stops them from chatting. If you cannot find the member, say so.
"""


def _user_root() -> str:
    try:
        from core.paths import ensure_user_layout
        return ensure_user_layout()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def admin_tools_path() -> str:
    return os.path.join(_user_root(), "config", "admin_tools.md")


def ensure_admin_tools_file() -> str:
    """Create the markdown if this install does not have one yet."""
    path = admin_tools_path()
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_FALLBACK)
    return path


def parse_owner_id(raw: str | None) -> int:
    """One Discord user id, or 0 when the text has none."""
    text = (raw or "").strip()
    if not text:
        return 0
    if text.isdigit():
        if 17 <= len(text) <= 20:
            return int(text)
        return 0
    match = _SNOWFLAKE.search(text)
    if match:
        return int(match.group(1))
    return 0


def parse_owner_ids(raw: str | None) -> list[int]:
    """Comma-separated user ids. Invalid pieces are skipped. Order is kept."""
    text = (raw or "").strip()
    if not text:
        return []
    found: list[int] = []
    seen: set[int] = set()
    for part in text.split(","):
        parsed = parse_owner_id(part)
        if parsed and parsed not in seen:
            seen.add(parsed)
            found.append(parsed)
    return found


def configured_owner_raw() -> str | None:
    """Saved Owner ID. None when settings could not be read."""
    try:
        from core.secrets import load_all
        data = load_all(_user_root())
        if "owner_id" in data:
            return str(data.get("owner_id") or "")
    except Exception:
        return None
    return None


def configured_owner_ids() -> list[int]:
    """Saved owner ids. Empty when the field is blank or nothing in it parses."""
    raw = configured_owner_raw()
    if raw is None:
        raw = os.getenv("ECHE_OWNER_ID") or ""
    return parse_owner_ids(raw)


def configured_owner_id() -> int:
    ids = configured_owner_ids()
    return ids[0] if ids else 0


def owner_match(author_id: int | None, configured_raw: str, app_owner: bool) -> bool:
    """
    A filled-in Owner ID list must include this person. A blank setting uses
    the Discord application owner. A non-empty value with no valid id matches
    nobody.
    """
    raw = (configured_raw or "").strip()
    if raw:
        return author_id in parse_owner_ids(raw)
    return bool(app_owner)


async def author_is_owner(bot, author) -> bool:
    """True when this person may see and run admin tools."""
    if author is None:
        return False
    raw = configured_owner_raw()
    if raw is None:
        raw = os.getenv("ECHE_OWNER_ID") or ""
    if (raw or "").strip():
        return owner_match(getattr(author, "id", None), raw, False)
    is_owner = getattr(bot, "is_owner", None)
    if is_owner is None:
        return False
    try:
        return bool(await is_owner(author))
    except Exception:
        return False


def flag_on(stored: str | None, env: str = "") -> bool:
    """A saved value wins, including an explicit off. Empty falls back to env."""
    if stored is not None and str(stored).strip() != "":
        return str(stored).strip().lower() in _ON
    return (env or "").strip().lower() in _ON


def admin_tools_enabled() -> bool:
    """Live settings read, so the main-window toggle applies without a restart."""
    stored: str | None = None
    try:
        from core.secrets import load_all
        data = load_all(_user_root())
        if "admin_tools" in data:
            stored = data.get("admin_tools") or ""
    except Exception:
        stored = None
    return flag_on(stored, os.getenv("ECHE_ADMIN_TOOLS") or "")


def injection_body(text: str) -> str:
    """Prompt text under the inject marker. The notes above it are not sent."""
    raw = text or ""
    idx = raw.find(_MARKER)
    if idx == -1:
        return ""
    return raw[idx + len(_MARKER) :].strip()


def admin_injection(*, enabled: bool | None = None) -> str:
    """
    Markdown to add to the owner's turn.

    Empty when the toggle is off, so the model is not given this file.
    """
    if enabled is None:
        enabled = admin_tools_enabled()
    if not enabled:
        return ""
    path = admin_tools_path()
    try:
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return ""
    return injection_body(text)
