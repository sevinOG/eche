"""Media Eche only VIEWS for a chat reply: downloads, shrinking, video frames.

Owner decision 2026-11-18 (21:32 and 21:33 ET): pictures, GIFs and videos
Eche only looks at for a chat reply get no child/age check ("we dont need to
restrict what the bot sees"). Every asset check is unchanged, including
check_source_media on someone else's media before an edit. Each turn that
passes viewed media to chat writes a safety audit row (kinds, counts and
short sha256 prefixes only; never pixels, URLs or tokens). If that row can't
be written, the media is dropped (fail closed on a missing log).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
from collections import OrderedDict

import aiohttp

MAX_IMAGE_DOWNLOAD = 20 * 1024 * 1024
MAX_VIDEO_DOWNLOAD = 25 * 1024 * 1024
MAX_VIDEO_SECONDS = 60
FETCH_TIMEOUT_S = 8
IMAGE_EDGE = 1024
FRAME_EDGE = 768
FRAME_COUNT = 6
MIN_FRAMES = 4
_EXTRACT_FPS = 4  # extract_video_frames samples real video at 4 fps

FETCH_ERROR_NOTE = (
    "A picture or video in the replied-to message couldn't be downloaded "
    "(link error), so you can't see it. Say so; don't guess."
)
TOO_LARGE_NOTE = (
    "A picture or video in the replied-to message is too large to open, so "
    "you can't see it. Say so; don't guess."
)

_IMAGE_LINK = re.compile(r"https?://\S+?\.(?:png|jpe?g|webp|gif)(?:\?\S*)?(?=\s|$|>)", re.I)
_VIDEO_TYPES = ("video/mp4", "video/webm", "video/quicktime")

# Successful downloads, by URL then by sha256. Small, process-local.
_CACHE_MAX = 64
_by_url: "OrderedDict[str, str]" = OrderedDict()
_by_sha: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
_frames_by_sha: "OrderedDict[str, list]" = OrderedDict()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def _remember(table: OrderedDict, key: str, value) -> None:
    table[key] = value
    table.move_to_end(key)
    while len(table) > _CACHE_MAX:
        table.popitem(last=False)


def cache_put(url: str, data: bytes, mime: str) -> str:
    sha = sha256_hex(data)
    _remember(_by_sha, sha, (bytes(data), mime))
    if url:
        _remember(_by_url, url, sha)
    return sha


def cache_get_url(url: str):
    sha = _by_url.get(url or "")
    if sha and sha in _by_sha:
        data, mime = _by_sha[sha]
        return data, mime
    return None


def image_links(text: str) -> list[str]:
    return [m.group(0).rstrip(").,") for m in _IMAGE_LINK.finditer(text or "")]


def is_video_mime(mime: str) -> bool:
    kind = (mime or "").split(";")[0].strip().lower()
    return kind.startswith("video/")


def is_animated(data: bytes, mime: str) -> bool:
    kind = (mime or "").lower()
    if "gif" in kind:
        return data[:6] in (b"GIF87a", b"GIF89a") and data.count(b"\x21\xf9\x04") > 1
    if "webp" in kind:
        return b"ANIM" in data[:128] or b"ANMF" in data
    return False


async def fetch(url: str, limit: int) -> tuple[bytes, str, str]:
    """(data, mime, error). error is "", "too-large", "not-media" or "fetch".

    8 s per attempt. A fetch error (timeout, connection, 5xx) is retried once;
    a 4xx, a non-media type or an oversized file is not.
    """
    if not url:
        return b"", "", "fetch"
    cached = cache_get_url(url)
    if cached is not None:
        return cached[0], cached[1], ""
    last = "fetch"
    for attempt in (1, 2):
        try:
            timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"User-Agent": "Eche/1.0"}) as resp:
                    if resp.status >= 500:
                        last = "fetch"
                        continue
                    if resp.status != 200:
                        return b"", "", "fetch"
                    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if ctype and not (ctype.startswith("image/") or ctype.startswith("video/")):
                        return b"", "", "not-media"
                    size = int(resp.headers.get("Content-Length") or 0)
                    if size > limit:
                        return b"", "", "too-large"
                    data = await resp.content.read(limit + 1)
        except Exception:
            last = "fetch"
            continue
        if len(data) > limit:
            return b"", "", "too-large"
        if not data:
            last = "fetch"
            continue
        mime = ctype or "image/jpeg"
        cache_put(url, data, mime)
        return data, mime, ""
    return b"", "", last


def shrink(data: bytes, mime: str, edge: int = IMAGE_EDGE) -> tuple[bytes, str]:
    """Longest side at most `edge` px, as JPEG when it had to change."""
    try:
        from PIL import Image
    except ImportError:
        return data, mime
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.seek(0)
            w, h = opened.size
            if max(w, h) <= edge and len(data) <= 1_200_000:
                return data, mime
            img = opened.convert("RGB")
            if max(w, h) > edge:
                scale = edge / float(max(w, h))
                img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, mime


def pick_frames(total: int, want: int = FRAME_COUNT) -> list[int]:
    """0-based indexes: start, end, and evenly spaced between (4-6 frames)."""
    if total <= 0:
        return []
    if total <= want:
        return list(range(total))
    step = (total - 1) / float(want - 1)
    return sorted({round(i * step) for i in range(want)})


def _label(index: int, total: int, animated: bool) -> str:
    if animated:
        pct = 0 if total <= 1 else round(100 * index / (total - 1))
        return f"{pct}% through"
    return f"{index / _EXTRACT_FPS:.1f}s"


async def video_frames(data: bytes, mime: str) -> tuple[list[dict], str]:
    """(raw frames [{data, mime, sha256}], timestamp note). Empty on failure."""
    sha = sha256_hex(data)
    cached = _frames_by_sha.get(sha)
    if cached is not None:
        return cached[0], cached[1]
    from core.reply_images import chat_video_frames

    try:
        frames = await asyncio.to_thread(chat_video_frames, data, mime or "video/mp4")
    except Exception:
        frames = []
    if not frames:
        return [], ""
    animated = not is_video_mime(mime)
    if not animated:
        frames = frames[: MAX_VIDEO_SECONDS * _EXTRACT_FPS + 1]
    picks = pick_frames(len(frames))
    out = []
    for i in picks:
        blob, fmime = shrink(frames[i], "image/jpeg", FRAME_EDGE)
        out.append({"data": blob, "mime": fmime, "sha256": sha256_hex(blob)})
    stamps = ", ".join(_label(i, len(frames), animated) for i in picks)
    kind = "GIF" if animated else "video"
    note = f"{kind} frames in order (no audio), at {stamps}."
    _remember(_frames_by_sha, sha, (out, note))
    return out, note


def record_viewed(ids: dict, *, source: str, items: list[dict], kinds: list[str]) -> bool:
    """Safety audit row for viewed media passed to chat. False if it failed."""
    if not items:
        return True
    try:
        from core.audit import record

        record(
            "chat-media",
            outcome="viewed",
            source=source,
            count=str(len(items)),
            kinds=",".join(sorted(set(kinds)))[:80],
            sha256=",".join((i.get("sha256") or sha256_hex(i.get("data") or b""))[:16] for i in items)[:300],
            check="none (owner decision 2026-11-18)",
            safety=True,
            **{k: ids.get(k) for k in ("user_id", "guild_id", "channel_id")},
        )
        return True
    except Exception:
        return False


def prepare_viewed(media, message) -> None:
    """Fit the replied-to pictures and log what chat will view. No checks.

    Own attachments, replied-to pictures and sampled video/GIF frames all go
    to the chat model unchecked (owner decision 2026-11-18). If the safety
    audit row can't be written, every viewed part is dropped.
    """
    from core.message_media import fit_images

    reply_raw = list(getattr(media, "reply_images", None) or [])
    media.reply_parts = fit_images(reply_raw)
    own = list(getattr(media, "images", None) or [])
    frames = list(getattr(media, "video_parts", None) or [])
    if not (own or media.reply_parts or frames):
        return
    ids = dict(
        user_id=getattr(getattr(message, "author", None), "id", None),
        guild_id=getattr(getattr(message, "guild", None), "id", None),
        channel_id=getattr(getattr(message, "channel", None), "id", None),
    )
    items = [{"sha256": r.get("sha256") or "-"} for r in reply_raw]
    items += [{"sha256": "-"} for _ in own + frames]
    kinds = list(getattr(media, "kinds", None) or []) or ["image"]
    if not record_viewed(ids, source="chat", items=items, kinds=kinds):
        media.images = []
        media.reply_parts = []
        media.video_parts = []
