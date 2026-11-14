# core/reply_images.py
# Pictures in a message someone replies to, so the chat model can see them.
#
# Owner request 2026-11-13 22:02 ET:
# - Pictures Eche itself posted as asset outputs passed the hardcoded child
#   checks when they were made. Their attachment IDs and sha256 are recorded
#   at posting time, and a reply to them is not checked again.
# - Anyone else's picture gets the hardcoded image checks (pixel classifier,
#   then age vision: Grok vision with the local vision model as backup) once.
#   The verdict is cached by sha256. Any error fails closed: not shown.
# - The reply text gets no text filter, and no model text check: these
#   pictures are marked pre-cleared so the chat request takes the text-only
#   path (core/client.py precleared_images).
# - Size and type limits are core/message_media's (images only, 1.2 MB).
# - The pictures go to whatever chat model is configured. A model without
#   vision just does not see them; the file names are still in the note.

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time

_LOCK = threading.Lock()
POSTED_FILE = "eche_posted_media.json"
CHECKED_FILE = "reply_image_checks.json"
_MAX_POSTED = 5000
_MAX_CHECKED = 5000
CHECK_TTL_SECONDS = 30 * 24 * 60 * 60
WITHHELD_NOTE = "A replied-to picture was not shown (it did not pass the image check)."


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(bytes(data or b"")).hexdigest()


def _path(name: str) -> str:
    try:
        from core.secrets import _user_root

        root = _user_root()
    except Exception:
        root = os.getcwd()
    # Tests must not leave files in the source tree.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        source = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        if os.path.abspath(root) == source:
            root = os.environ.get("ECHE_TEST_LIMITS_ROOT") or tempfile.mkdtemp(prefix="eche-reply-img-")
            os.environ["ECHE_TEST_LIMITS_ROOT"] = root
    folder = os.path.join(root, "config")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name)


def _load(name: str) -> dict:
    try:
        with open(_path(name), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(name: str, data: dict) -> None:
    path = _path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _trim(table: dict, limit: int) -> dict:
    if len(table) <= limit:
        return table
    newest = sorted(table.items(), key=lambda kv: float((kv[1] or {}).get("ts", 0) if isinstance(kv[1], dict) else kv[1] or 0))
    return dict(newest[-limit:])


# ---------------------------------------------------------------------------
# Eche's own asset outputs
# ---------------------------------------------------------------------------

def record_eche_post(data: bytes | None = None, attachment_ids=()) -> None:
    """Remember an asset output Eche posted (already checked when made)."""
    now = time.time()
    with _LOCK:
        table = _load(POSTED_FILE)
        shas = table.setdefault("sha256", {})
        ids = table.setdefault("attachment_ids", {})
        if data:
            shas[sha256_hex(data)] = now
        for aid in attachment_ids or ():
            if aid is not None:
                ids[str(aid)] = now
        table["sha256"] = _trim(shas, _MAX_POSTED)
        table["attachment_ids"] = _trim(ids, _MAX_POSTED)
        try:
            _save(POSTED_FILE, table)
        except Exception:
            pass


def record_sent_message(sent, data: bytes | None = None) -> None:
    """record_eche_post for a sent discord.Message carrying the file."""
    try:
        ids = [getattr(a, "id", None) for a in list(getattr(sent, "attachments", None) or [])]
    except Exception:
        ids = []
    record_eche_post(data, ids)


def is_eche_post(sha: str = "", attachment_id=None) -> bool:
    table = _load(POSTED_FILE)
    if attachment_id is not None and str(attachment_id) in (table.get("attachment_ids") or {}):
        return True
    return bool(sha) and sha in (table.get("sha256") or {})


# ---------------------------------------------------------------------------
# Everyone else's pictures: checked once, cached by sha256
# ---------------------------------------------------------------------------

def _key(sha: str, adult: bool) -> str:
    return f"{sha}:{'adult' if adult else 'sfw'}"


def cached_verdict(sha: str, adult: bool = False) -> str | None:
    entry = (_load(CHECKED_FILE).get("checks") or {}).get(_key(sha, adult))
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("ts") or 0) > CHECK_TTL_SECONDS:
        return None
    verdict = entry.get("v")
    return verdict if verdict in ("pass", "block") else None


def _store_verdict(sha: str, adult: bool, verdict: str) -> None:
    with _LOCK:
        table = _load(CHECKED_FILE)
        checks = table.setdefault("checks", {})
        checks[_key(sha, adult)] = {"v": verdict, "ts": time.time()}
        table["checks"] = _trim(checks, _MAX_CHECKED)
        try:
            _save(CHECKED_FILE, table)
        except Exception:
            pass


def _is_error(verdict: str) -> bool:
    v = str(verdict or "")
    return v.endswith("-error") or "unavailable" in v or "unsupported" in v


async def _run_image_check(data: bytes, mime: str, *, mode, ids: dict) -> str:
    """'pass', 'block', or 'error'. Pixels only, never the reply text.

    The same hardcoded checks as an asset file: the pixel classifier, then
    age vision (Grok vision, local vision model as backup). Every frame of
    an animated picture. An error is not a verdict: it is not cached, and
    the picture is not shown (fail closed).
    """
    from core.asset_safety import (
        _audit,
        _image_part,
        _is_animated_image,
        adult_output_ignore_lg4,
        extract_video_frames,
    )
    from core.blocklist import note
    from core.classifier import age_vision_async, classify_media_async

    if _is_animated_image(mime, data):
        frames = [(frame, "image/jpeg") for frame in extract_video_frames(data, mime)]
        if not frames:
            return "error"
    else:
        frames = [(data, mime or "image/jpeg")]
    for frame, frame_mime in frames:
        part = _image_part(frame, frame_mime)
        if part is None:
            return "error"
        try:
            verdict = await classify_media_async([part], ignore_lg4=adult_output_ignore_lg4(mode))
        except Exception:
            verdict = "classifier-error"
        if not verdict:
            try:
                verdict = await age_vision_async([part], adult=bool(getattr(mode, "adult", False)))
            except Exception:
                verdict = "age-vision-error"
        if verdict:
            if _is_error(verdict):
                return "error"
            note(f"reply-image-{verdict}")
            try:
                _audit("refusal", category=f"reply-image-{verdict}", mode=mode, **ids)
            except Exception:
                pass
            return "block"
    return "pass"


def _reply_mode(channel):
    from core.asset_safety import mode_for_channel, read_asset_mode

    return mode_for_channel(read_asset_mode(observe=False), channel)


async def clear_reply_image(item: dict, *, ids: dict, channel=None) -> bool:
    """True when this replied-to picture may go to the chat model."""
    data = item.get("data") or b""
    if not data:
        return False
    sha = item.get("sha256") or sha256_hex(data)
    if is_eche_post(sha, item.get("attachment_id")):
        _audit_check(ids, "eche-post", sha)
        return True
    try:
        mode = _reply_mode(channel)
    except Exception:
        return False
    adult = bool(getattr(mode, "adult", False))
    cached = cached_verdict(sha, adult)
    if cached is not None:
        _audit_check(ids, f"cached-{cached}", sha)
        return cached == "pass"
    verdict = await _run_image_check(bytes(data), str(item.get("mime") or "image/jpeg"), mode=mode, ids=ids)
    if verdict in ("pass", "block"):
        _store_verdict(sha, adult, verdict)
    _audit_check(ids, verdict, sha)
    return verdict == "pass"


def _audit_check(ids: dict, outcome: str, sha: str) -> None:
    try:
        from core.audit import record

        record(
            "api",
            service="reply-image-check",
            outcome=outcome,
            sha256=sha[:16],
            **{k: ids.get(k) for k in ("user_id", "guild_id", "channel_id")},
        )
    except Exception:
        pass


async def cleared_reply_parts(media, message) -> tuple[list, bool]:
    """Fitted image parts for the replied-to pictures that passed, and whether any was withheld."""
    raw = list(getattr(media, "reply_images", None) or [])
    if not raw:
        return [], False
    author = getattr(message, "author", None)
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    ids = dict(
        user_id=getattr(author, "id", None),
        guild_id=getattr(guild, "id", None),
        channel_id=getattr(channel, "id", None),
    )
    passed = []
    withheld = False
    for item in raw:
        try:
            ok = await clear_reply_image(item, ids=ids, channel=channel)
        except Exception:
            ok = False
        if ok:
            passed.append(item)
        else:
            withheld = True
    from core.message_media import fit_images

    return fit_images(passed), withheld
