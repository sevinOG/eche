"""Turn Discord attachments, embeds, and forwards into model input.

Text models get a transcript. Image bytes ride along as vision parts and are
dropped later if that model cannot see them. Pictures are scaled down first:
a full-size Discord photo is thousands of vision tokens and overflows the
4096 context Ollama loads on an 8 GB GPU.
"""
from __future__ import annotations

import asyncio
import base64
import io
import re
from dataclasses import dataclass, field

import aiohttp  # noqa: F401  (tests patch message_media.aiohttp)

try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None

_URL = re.compile(r"https?://", re.I)
_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_TEXT_SUFFIXES = (
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".log",
    ".py",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".toml",
)
_MAX_IMAGE_BYTES = 1_200_000
_MAX_IMAGES = 3
# One video or GIF per turn is watched (4-6 frames).
_MAX_VIDEOS = 1
# qwen3.5 vision: patch 16 merged by 2, so one token is a 32px square.
# Ollama's VRAM default context is 4096, and the chat prompt already uses
# about 2000 of those. The pictures on one turn share this many tokens.
_VISION_PATCH = 32
_IMAGE_TOKEN_BUDGET = 1024
_MAX_EDGE = 1024
_MAX_TEXT_FILE = 48_000
_TEXT_CLIP = 1000
_MEDIA_CLIP = 1600
_MEMORY_CLIP = 500
_EMBED_WAIT_S = 1.25


@dataclass
class DiscordMedia:
    """What a chat turn should add for one Discord message."""

    text: str = ""
    memory: str = ""
    summary: str = ""
    images: list = field(default_factory=list)
    message: object = None
    # Raw pictures from the replied-to message ({data, mime, sha256,
    # attachment_id}). Not sent until core/reply_images clears them.
    reply_images: list = field(default_factory=list)
    # Image parts for the replied-to pictures that passed (set by the chat
    # listener). Pre-cleared: they do not put the request on the image path.
    reply_parts: list = field(default_factory=list)
    # Sampled video/GIF frames (fitted image parts) and their timestamp note.
    video_parts: list = field(default_factory=list)
    # Kinds of viewed media, for the chat-media audit row.
    kinds: list = field(default_factory=list)


def _cap(key: str, default: int) -> int:
    """Settings → Context & Memory. The constant above is the default."""
    try:
        from core.context_caps import get

        return get(key)
    except Exception:
        return default


def _clip(text: str, limit: int) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _size_label(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _filename(item) -> str:
    return str(getattr(item, "filename", "") or "file")


def _content_type(item) -> str:
    return str(getattr(item, "content_type", "") or "").split(";")[0].strip().lower()


def _is_image_name(name: str, content_type: str) -> bool:
    if content_type in _IMAGE_TYPES or content_type.startswith("image/"):
        return True
    low = name.lower()
    return low.endswith(_IMAGE_SUFFIXES)


def _is_text_file(item) -> bool:
    ctype = _content_type(item)
    if ctype.startswith("text/") or ctype in {"application/json", "application/xml"}:
        return True
    return _filename(item).lower().endswith(_TEXT_SUFFIXES)


def _image_mime(item) -> str:
    ctype = _content_type(item)
    if ctype in _IMAGE_TYPES:
        return "image/jpeg" if ctype == "image/jpg" else ctype
    low = _filename(item).lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".webp"):
        return "image/webp"
    if low.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def image_part(data: bytes, mime: str) -> dict:
    """OpenAI-style image part. Groq and Ollama both accept this shape."""
    encoded = base64.standard_b64encode(data).decode("ascii")
    safe = mime if mime in _IMAGE_TYPES else "image/jpeg"
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{safe};base64,{encoded}"},
    }


def _longest_edge(count: int) -> int:
    """Pixel cap so `count` pictures stay inside the shared token budget."""
    count = max(1, min(int(count or 1), _MAX_IMAGES))
    per = max(1, _IMAGE_TOKEN_BUDGET // count)
    side = int(per ** 0.5)
    return min(_MAX_EDGE, max(_VISION_PATCH, side * _VISION_PATCH))


def _rgb_image(opened):
    if opened.mode == "RGB":
        return opened.copy()
    if opened.mode in ("RGBA", "LA") or (opened.mode == "P" and "transparency" in opened.info):
        rgba = opened.convert("RGBA")
        background = _PILImage.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return opened.convert("RGB")


def _shrink_image(data: bytes, mime: str, longest_edge: int) -> tuple[bytes, str]:
    """Scale one picture so its longest side is at most `longest_edge` pixels."""
    if _PILImage is None or not data:
        return data, mime
    try:
        with _PILImage.open(io.BytesIO(data)) as opened:
            opened.seek(0)
            width, height = opened.size
            if width < 1 or height < 1:
                return data, mime
            if max(width, height) <= longest_edge and len(data) <= _MAX_IMAGE_BYTES:
                return data, mime
            image = _rgb_image(opened)
            if max(width, height) > longest_edge:
                scale = longest_edge / float(max(width, height))
                image = image.resize(
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    _PILImage.Resampling.LANCZOS,
                )
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=85, optimize=True)
            out = buf.getvalue()
    except Exception:
        return data, mime
    if not out:
        return data, mime
    return out, "image/jpeg"


def fit_images(raw_images: list) -> list:
    """Shrink raw {data, mime} pictures into image parts for the chat model."""
    return _fit_images(raw_images)


def _fit_images(raw_images: list) -> list:
    if not raw_images:
        return []
    edge = _longest_edge(len(raw_images))
    fitted = []
    for item in raw_images:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, (bytes, bytearray)) or not data:
            continue
        mime = str(item.get("mime") or "image/jpeg")
        shrunk, out_mime = _shrink_image(bytes(data), mime, edge)
        if shrunk and len(shrunk) <= _MAX_IMAGE_BYTES:
            fitted.append(image_part(shrunk, out_mime))
    return fitted


def _fit_frames(frames: list) -> list:
    """Video frames are already 768 px JPEGs; wrap them as image parts."""
    out = []
    for item in frames:
        data = item.get("data") if isinstance(item, dict) else None
        if isinstance(data, (bytes, bytearray)) and data:
            out.append(image_part(bytes(data), str(item.get("mime") or "image/jpeg")))
    return out


def embed_lines(embed) -> list[str]:
    """Plain lines for one Discord embed. Empty when the embed has no text."""
    lines: list[str] = []
    title = _clip(str(getattr(embed, "title", "") or ""), 200)
    if title:
        lines.append(title)
    author = getattr(embed, "author", None)
    author_name = str(getattr(author, "name", "") or "").strip()
    if author_name:
        lines.append(f"author: {_clip(author_name, 120)}")
    description = _clip(str(getattr(embed, "description", "") or ""), 500)
    if description:
        lines.append(description)
    url = str(getattr(embed, "url", "") or "").strip()
    if url:
        lines.append(f"url: {url}")
    fields = list(getattr(embed, "fields", None) or [])
    for field_item in fields[:6]:
        name = _clip(str(getattr(field_item, "name", "") or ""), 80)
        value = _clip(str(getattr(field_item, "value", "") or ""), 200)
        if name or value:
            lines.append(f"{name}: {value}".strip(": "))
    footer = getattr(embed, "footer", None)
    footer_text = str(getattr(footer, "text", "") or "").strip()
    if footer_text:
        lines.append(f"footer: {_clip(footer_text, 160)}")
    image = getattr(embed, "image", None)
    thumb = getattr(embed, "thumbnail", None)
    video = getattr(embed, "video", None)
    image_url = str(getattr(image, "url", "") or "").strip()
    thumb_url = str(getattr(thumb, "url", "") or "").strip()
    video_url = str(getattr(video, "url", "") or "").strip()
    if image_url:
        lines.append(f"image: {image_url}")
    elif thumb_url:
        lines.append(f"thumbnail: {thumb_url}")
    if video_url:
        lines.append(f"video: {video_url}")
    return lines


def embed_image_url(embed) -> str:
    image = getattr(embed, "image", None)
    proxy = str(getattr(image, "proxy_url", "") or "").strip()
    direct = str(getattr(image, "url", "") or "").strip()
    return proxy or direct


def attachment_lines(item, body: str = "", too_large: bool = False) -> list[str]:
    name = _filename(item)
    kind = _content_type(item) or "file"
    size = int(getattr(item, "size", 0) or 0)
    lines = [f"Attachment: {name} ({kind}, {_size_label(size)})"]
    url = str(getattr(item, "url", "") or "").strip()
    if url:
        lines.append(f"url: {url}")
    if too_large and _is_image_name(name, kind):
        lines.append("image not included (over 1.2 MB)")
    if body:
        lines.append(body)
    return lines


async def _refresh_embeds(message):
    """Link previews arrive a moment after the message event."""
    if getattr(message, "embeds", None):
        return message
    if not _URL.search(str(getattr(message, "content", "") or "")):
        return message
    channel = getattr(message, "channel", None)
    fetch = getattr(channel, "fetch_message", None)
    if fetch is None:
        return message
    await asyncio.sleep(_EMBED_WAIT_S)
    try:
        fresh = await fetch(message.id)
    except Exception:
        return message
    return fresh or message


async def _referenced(message):
    ref = getattr(message, "reference", None)
    if ref is None:
        return None
    resolved = getattr(ref, "resolved", None)
    if resolved is not None and getattr(resolved, "id", None):
        # discord.py uses a deleted-message sentinel that is not a Message.
        if getattr(resolved, "content", None) is not None or getattr(resolved, "embeds", None):
            if hasattr(resolved, "attachments"):
                return resolved
    fetch = getattr(getattr(message, "channel", None), "fetch_message", None)
    message_id = getattr(ref, "message_id", None)
    if fetch is None or not message_id:
        return None
    try:
        return await fetch(message_id)
    except Exception:
        return None


async def _read_text(item) -> str:
    if not _is_text_file(item):
        return ""
    size = int(getattr(item, "size", 0) or 0)
    if size <= 0 or size > _MAX_TEXT_FILE:
        return ""
    reader = getattr(item, "read", None)
    if reader is None:
        return ""
    try:
        data = await reader()
    except Exception:
        return ""
    if not isinstance(data, (bytes, bytearray)):
        return ""
    return _clip(bytes(data).decode("utf-8", errors="replace"), _cap("attachment_text_chars", _TEXT_CLIP))


def _is_video_item(item) -> bool:
    from core.chat_media import is_video_mime

    name = _filename(item).lower()
    return is_video_mime(_content_type(item)) or name.endswith((".mp4", ".webm", ".mov"))


async def _read_bytes(item) -> tuple[bytes, str]:
    """Attachment bytes with one retry. (data, error)."""
    reader = getattr(item, "read", None)
    if reader is None:
        return b"", "fetch"
    for _attempt in (1, 2):
        try:
            data = await reader()
        except Exception:
            continue
        if isinstance(data, (bytes, bytearray)) and data:
            return bytes(data), ""
    return b"", "fetch"


async def _read_image(item, seen: set[str], errors: list | None = None) -> dict | None:
    """A picture (shrunk to 1024 px) or a video/GIF ({"video": True}).

    Up to 20 MB for pictures and 25 MB for videos (core/chat_media). The
    picture is scaled before it is used anywhere.
    """
    from core.chat_media import (
        MAX_IMAGE_DOWNLOAD,
        MAX_VIDEO_DOWNLOAD,
        is_animated,
        shrink,
        sha256_hex,
    )

    name = _filename(item)
    video = _is_video_item(item)
    if not video and not _is_image_name(name, _content_type(item)):
        return None
    url = str(getattr(item, "url", "") or "").strip()
    if url and url in seen:
        return None
    size = int(getattr(item, "size", 0) or 0)
    limit = MAX_VIDEO_DOWNLOAD if video else MAX_IMAGE_DOWNLOAD
    if size > limit:
        if errors is not None:
            errors.append("too-large")
        return None
    data, err = await _read_bytes(item)
    if err or len(data) > limit:
        if errors is not None:
            errors.append(err or "too-large")
        return None
    if url:
        seen.add(url)
    mime = _content_type(item) or ("video/mp4" if video else _image_mime(item))
    if not video:
        mime = _image_mime(item)
    sha = sha256_hex(data)
    if video or is_animated(data, mime):
        return {"video": True, "data": data, "mime": mime, "sha256": sha}
    small, small_mime = shrink(data, mime)
    if len(small) > _MAX_IMAGE_BYTES:
        # Could not be scaled (no Pillow or an unreadable file): too big to send.
        if errors is not None:
            errors.append("too-large")
        return None
    return {
        "data": small,
        "mime": small_mime,
        "sha256": sha,
        "attachment_id": getattr(item, "id", None),
    }


async def _fetch_image(url: str, seen: set[str], errors: list | None = None, *, video: bool = False) -> dict | None:
    """A linked picture or video. 8 s per try, one retry on a fetch error."""
    from core.chat_media import (
        MAX_IMAGE_DOWNLOAD,
        MAX_VIDEO_DOWNLOAD,
        fetch,
        is_animated,
        is_video_mime,
        shrink,
        sha256_hex,
    )

    if not url or url in seen:
        return None
    data, mime, err = await fetch(url, MAX_VIDEO_DOWNLOAD if video else MAX_IMAGE_DOWNLOAD)
    if err:
        if errors is not None and err != "not-media":
            errors.append(err)
        return None
    seen.add(url)
    sha = sha256_hex(data)
    if video or is_video_mime(mime) or is_animated(data, mime):
        return {"video": True, "data": data, "mime": mime or "video/mp4", "sha256": sha}
    small, small_mime = shrink(data, mime if mime.startswith("image/") else "image/jpeg")
    if len(small) > _MAX_IMAGE_BYTES:
        if errors is not None:
            errors.append("too-large")
        return None
    return {"data": small, "mime": small_mime, "sha256": sha}


def _embed_media(embed) -> tuple[str, bool]:
    """(url, is_video) for an embed: Tenor/Giphy video first, then image, then thumbnail."""
    video = getattr(embed, "video", None)
    vurl = str(getattr(video, "proxy_url", "") or getattr(video, "url", "") or "").strip()
    if vurl and (vurl.lower().split("?")[0].endswith((".mp4", ".webm")) or "tenor" in vurl or "giphy" in vurl):
        return vurl, True
    url = embed_image_url(embed)
    if url:
        return url, False
    thumb = getattr(embed, "thumbnail", None)
    turl = str(getattr(thumb, "proxy_url", "") or getattr(thumb, "url", "") or "").strip()
    return turl, False


async def _consume(source, label: str, blocks: list[str], images: list, labels: list[str], seen: set[str], saw_image: list, *, include_text: bool, include_embeds: bool = True, load_images: bool = True, raw_out: list | None = None, videos: list | None = None, errors: list | None = None, links: bool = False) -> None:
    pieces: list[str] = []
    target = raw_out if raw_out is not None else images

    def _take(part) -> None:
        if part is None:
            return
        if part.get("video"):
            if videos is not None and len(videos) < _MAX_VIDEOS:
                videos.append(part)
            return
        if len(target) < _MAX_IMAGES:
            target.append(part)

    if include_text:
        content = _clip(str(getattr(source, "content", "") or ""), 400)
        if content:
            pieces.append(content)
    for item in list(getattr(source, "attachments", None) or []):
        body = await _read_text(item)
        from core.chat_media import MAX_IMAGE_DOWNLOAD

        too_large = int(getattr(item, "size", 0) or 0) > MAX_IMAGE_DOWNLOAD and _is_image_name(
            _filename(item), _content_type(item)
        )
        pieces.extend(attachment_lines(item, body, too_large=too_large))
        labels.append(_filename(item))
        if _is_image_name(_filename(item), _content_type(item)) or _is_video_item(item):
            saw_image.append(True)
        if load_images:
            _take(await _read_image(item, seen, errors))
    if include_embeds:
        for embed in list(getattr(source, "embeds", None) or []):
            lines = embed_lines(embed)
            url, is_video = _embed_media(embed)
            if not lines and not url:
                continue
            if lines:
                pieces.append("Embed:\n" + "\n".join(lines))
            title = _clip(str(getattr(embed, "title", "") or ""), 80) or "embed"
            labels.append(title)
            if url:
                saw_image.append(True)
                if load_images:
                    _take(await _fetch_image(url, seen, errors, video=is_video))
    if links and load_images:
        from core.chat_media import image_links

        for url in image_links(str(getattr(source, "content", "") or ""))[:_MAX_IMAGES]:
            if url in seen:
                continue
            saw_image.append(True)
            _take(await _fetch_image(url, seen, errors))
    for sticker in list(getattr(source, "stickers", None) or []):
        name = str(getattr(sticker, "name", "") or "sticker").strip()
        pieces.append(f"Sticker: {name}")
        labels.append(name)
        surl = str(getattr(sticker, "url", "") or "").strip()
        if load_images and surl and surl.lower().split("?")[0].endswith((".png", ".gif", ".webp")):
            saw_image.append(True)
            _take(await _fetch_image(surl, seen, errors))
    if not pieces:
        return
    blocks.append(label + ":\n" + "\n".join(pieces))


async def collect_discord_media(message) -> DiscordMedia:
    """Transcript plus up to three images from this message, a reply, or a forward."""
    refreshed = await _refresh_embeds(message)
    blocks: list[str] = []
    images: list = []
    labels: list[str] = []
    seen: set[str] = set()
    saw_image: list[bool] = []
    videos_own: list = []
    errors_own: list = []
    await _consume(
        refreshed, "Sent just now", blocks, images, labels, seen, saw_image, include_text=False,
        videos=videos_own, errors=errors_own,
    )
    reply_blocks: list[str] = []
    reply_raw: list = []
    videos: list = list(videos_own)
    errors: list = list(errors_own)
    replied = await _referenced(refreshed)
    if replied is not None and getattr(replied, "id", None) != getattr(refreshed, "id", None):
        # The replied-to message: attachments, link embeds (Tenor/Giphy video
        # or preview, image links, social cards), bare image links, and any
        # forward inside it. Viewed for chat only: no child/age check (owner
        # decision 2026-11-18). asset edits keep check_source_media.
        await _consume(
            replied,
            "Replied-to message",
            reply_blocks,
            images,
            labels,
            seen,
            saw_image,
            include_text=True,
            include_embeds=True,
            raw_out=reply_raw,
            videos=videos,
            errors=errors,
            links=True,
        )
        for snap in list(getattr(replied, "message_snapshots", None) or []):
            await _consume(
                snap,
                "Forward inside the replied-to message",
                reply_blocks,
                images,
                labels,
                seen,
                saw_image,
                include_text=True,
                raw_out=reply_raw,
                videos=videos,
                errors=errors,
                links=True,
            )
    for snap in list(getattr(refreshed, "message_snapshots", None) or []):
        await _consume(
            snap,
            "Forwarded message",
            blocks,
            images,
            labels,
            seen,
            saw_image,
            include_text=True,
            videos=videos,
            errors=errors,
            links=True,
        )
    video_parts: list = []
    video_note = ""
    kinds: list[str] = []
    if videos:
        from core.chat_media import video_frames

        frames, video_note = await video_frames(videos[0]["data"], videos[0]["mime"])
        if frames:
            video_parts = _fit_frames(frames)
            kinds.append("gif" if "gif" in (videos[0]["mime"] or "") or "webp" in (videos[0]["mime"] or "") else "video")
        else:
            errors.append("frames")
    own = "\n\n".join(blocks).strip()
    reply_note = "\n\n".join(reply_blocks).strip()
    text = "\n\n".join(part for part in (own, reply_note) if part)
    from core.chat_media import FETCH_ERROR_NOTE, TOO_LARGE_NOTE

    notes = []
    if video_note:
        notes.append(video_note)
    if "fetch" in errors or "frames" in errors:
        notes.append(FETCH_ERROR_NOTE)
    if "too-large" in errors:
        notes.append(TOO_LARGE_NOTE)
    if notes:
        text = "\n".join([*notes, text]).strip()
    if text and saw_image:
        text = "If you cannot see an image, say so. Do not guess what is in it.\n" + text
    if text:
        text = _clip(text, _cap("media_prompt_chars", _MEDIA_CLIP))
    images = _fit_images(images)
    summary = ", ".join(labels[:4])
    if len(labels) > 4:
        summary += f", +{len(labels) - 4}"
    # Reply text stays on this turn. It is not written into the memory pin.
    return DiscordMedia(
        text=text,
        memory=_clip(own, _cap("media_memory_chars", _MEMORY_CLIP)) if own else "",
        summary=summary,
        images=images,
        message=refreshed,
        reply_images=reply_raw,
        video_parts=video_parts,
        kinds=kinds + (["image"] if images else []) + (["reply-image"] if reply_raw else []),
    )
