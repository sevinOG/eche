# core/asset_source.py
# Reply targeting for ?asset: the picture or video in the message a ?asset
# command replies to becomes the source of an edit. Only the replied-to
# message is read, only Discord's own media hosts are fetched, and the file
# must pass type, size and length limits before any check or edit runs.
# The child checks on the source run later, in the asset choke point,
# before anything is sent to the provider.

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from urllib.parse import urlparse

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
VIDEO_TYPES = frozenset({"video/mp4"})
MAX_IMAGE_SOURCE_BYTES = 10_000_000
MAX_VIDEO_SOURCE_BYTES = 20_000_000
# The provider edits clips up to 8.7 seconds.
MAX_VIDEO_SOURCE_SECONDS = 8.7
ALLOWED_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
FETCH_TIMEOUT = 20

UNSUPPORTED = "Only JPEG, PNG or WebP pictures and MP4 videos can be edited."
TOO_BIG = "That file is too big to edit."
TOO_LONG = "That video is too long to edit. Use a clip of 8 seconds or less."
UNREADABLE = "I couldn't read the file you replied to, so nothing was edited."

_EXT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


@dataclass
class AssetSource:
    """One source file for an edit. The bytes are never printed or logged."""

    data: bytes = field(repr=False)
    mime: str
    kind: str  # "image" or "video"
    name: str = ""
    # Discord attachment ID, when the file came from one. Lets the edit path
    # recognise Eche's own recorded asset outputs (core/reply_images).
    attachment_id: object = None


@dataclass
class _Candidate:
    name: str
    mime: str
    size: int
    attachment: object = None
    url: str = ""


def _error(text: str):
    from core.imagine import ImagineError

    return ImagineError(text)


def _type_of(name: str, content_type: str) -> str:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype.startswith(("image/", "video/")):
        return ctype
    ext = os.path.splitext((name or "").split("?", 1)[0].lower())[1]
    return _EXT_TYPES.get(ext, "")


def _is_media(mime: str) -> bool:
    return mime.startswith(("image/", "video/"))


def _host_ok(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_HOSTS


def _candidates(message) -> list[_Candidate]:
    out: list[_Candidate] = []
    for item in list(getattr(message, "attachments", None) or []):
        name = str(getattr(item, "filename", "") or "")
        mime = _type_of(name, str(getattr(item, "content_type", "") or ""))
        if _is_media(mime):
            out.append(_Candidate(name, mime, int(getattr(item, "size", 0) or 0), attachment=item))
    for embed in list(getattr(message, "embeds", None) or []):
        for attr in ("video", "image", "thumbnail"):
            part = getattr(embed, attr, None)
            url = str(getattr(part, "proxy_url", "") or getattr(part, "url", "") or "")
            if not url:
                continue
            mime = _type_of(url, "")
            if not mime:
                mime = "video/mp4" if attr == "video" else "image/jpeg"
            out.append(_Candidate(os.path.basename(urlparse(url).path), mime, 0, url=url))
            break
    return out


async def replied_message(message):
    """The message this one replies to, or None."""
    ref = getattr(message, "reference", None)
    if ref is None:
        return None
    resolved = getattr(ref, "resolved", None)
    if resolved is not None and hasattr(resolved, "attachments"):
        return resolved
    fetch = getattr(getattr(message, "channel", None), "fetch_message", None)
    message_id = getattr(ref, "message_id", None)
    if fetch is None or not message_id:
        return None
    try:
        return await fetch(message_id)
    except Exception:
        return None


def replied_has_media(replied) -> bool:
    return bool(replied is not None and _candidates(replied))


async def _fetch(url: str, limit: int) -> bytes:
    if not _host_ok(url):
        raise _error(UNSUPPORTED)
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"User-Agent": "Eche/2.1.0"}, allow_redirects=False) as resp:
                if resp.status != 200:
                    raise _error(UNREADABLE)
                data = await resp.content.read(limit + 1)
    except Exception as exc:
        from core.imagine import ImagineError

        if isinstance(exc, ImagineError):
            raise
        raise _error(UNREADABLE) from exc
    if len(data) > limit:
        raise _error(TOO_BIG)
    return data


def _sniff(data: bytes) -> str:
    from core.imagine import _sniff_media_mime

    return _sniff_media_mime(data)


def video_seconds(data: bytes) -> float | None:
    """Real duration from ffprobe, or None (callers fail closed)."""
    from core.asset_safety import probe_duration

    with tempfile.TemporaryDirectory(prefix="eche-src-") as raw:
        path = os.path.join(raw, "src.mp4")
        with open(path, "wb") as fh:
            fh.write(data)
        return probe_duration(path)


async def source_from_reply(message) -> AssetSource | None:
    """The edit source from the replied-to message.

    None when the message is not a reply or the reply has no picture or
    video. Raises ImagineError when there is media but it cannot be used.
    """
    replied = await replied_message(message)
    if replied is None:
        return None
    found = _candidates(replied)
    if not found:
        return None
    pick = found[0]
    if pick.mime in IMAGE_TYPES:
        kind, limit = "image", MAX_IMAGE_SOURCE_BYTES
    elif pick.mime in VIDEO_TYPES:
        kind, limit = "video", MAX_VIDEO_SOURCE_BYTES
    else:
        raise _error(UNSUPPORTED)
    if pick.size and pick.size > limit:
        raise _error(TOO_BIG)
    if pick.attachment is not None:
        url = str(getattr(pick.attachment, "url", "") or "")
        if url and not _host_ok(url):
            raise _error(UNSUPPORTED)
        reader = getattr(pick.attachment, "read", None)
        if reader is None:
            raise _error(UNREADABLE)
        try:
            data = await reader()
        except Exception as exc:
            raise _error(UNREADABLE) from exc
    else:
        data = await _fetch(pick.url, limit)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise _error(UNREADABLE)
    data = bytes(data)
    if len(data) > limit:
        raise _error(TOO_BIG)
    sniffed = _sniff(data)
    allowed = IMAGE_TYPES if kind == "image" else VIDEO_TYPES
    if sniffed not in allowed:
        raise _error(UNSUPPORTED)
    if kind == "video" and data[8:12] == b"qt  ":
        # QuickTime .mov shares the MP4 box layout; the provider wants MP4.
        raise _error(UNSUPPORTED)
    if kind == "video":
        seconds = await asyncio.to_thread(video_seconds, data)
        if seconds is None:
            raise _error(UNREADABLE)
        if seconds > MAX_VIDEO_SOURCE_SECONDS:
            raise _error(TOO_LONG)
    return AssetSource(
        data=data,
        mime=sniffed,
        kind=kind,
        name=pick.name,
        attachment_id=getattr(pick.attachment, "id", None) if pick.attachment is not None else None,
    )
