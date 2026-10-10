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

import aiohttp

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
    return _clip(bytes(data).decode("utf-8", errors="replace"), _TEXT_CLIP)


async def _read_image(item, seen: set[str]) -> dict | None:
    name = _filename(item)
    if not _is_image_name(name, _content_type(item)):
        return None
    url = str(getattr(item, "url", "") or "").strip()
    if url and url in seen:
        return None
    size = int(getattr(item, "size", 0) or 0)
    if size > _MAX_IMAGE_BYTES:
        return None
    reader = getattr(item, "read", None)
    if reader is None:
        return None
    try:
        data = await reader()
    except Exception:
        return None
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > _MAX_IMAGE_BYTES:
        return None
    if url:
        seen.add(url)
    return {"data": bytes(data), "mime": _image_mime(item)}


async def _fetch_image(url: str, seen: set[str]) -> dict | None:
    if not url or url in seen:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"User-Agent": "Eche/1.0"}) as resp:
                if resp.status != 200:
                    return None
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype and not ctype.startswith("image/"):
                    return None
                data = await resp.content.read(_MAX_IMAGE_BYTES + 1)
    except Exception:
        return None
    if not data or len(data) > _MAX_IMAGE_BYTES:
        return None
    seen.add(url)
    mime = ctype if ctype.startswith("image/") else "image/jpeg"
    return {"data": data, "mime": mime}


async def _consume(source, label: str, blocks: list[str], images: list, labels: list[str], seen: set[str], saw_image: list, *, include_text: bool, include_embeds: bool = True) -> None:
    pieces: list[str] = []
    if include_text:
        content = _clip(str(getattr(source, "content", "") or ""), 400)
        if content:
            pieces.append(content)
    for item in list(getattr(source, "attachments", None) or []):
        body = await _read_text(item)
        too_large = int(getattr(item, "size", 0) or 0) > _MAX_IMAGE_BYTES and _is_image_name(
            _filename(item), _content_type(item)
        )
        pieces.extend(attachment_lines(item, body, too_large=too_large))
        labels.append(_filename(item))
        if _is_image_name(_filename(item), _content_type(item)):
            saw_image.append(True)
        if len(images) < _MAX_IMAGES:
            part = await _read_image(item, seen)
            if part is not None:
                images.append(part)
    if include_embeds:
        for embed in list(getattr(source, "embeds", None) or []):
            lines = embed_lines(embed)
            if not lines:
                continue
            pieces.append("Embed:\n" + "\n".join(lines))
            title = _clip(str(getattr(embed, "title", "") or ""), 80) or "embed"
            labels.append(title)
            if embed_image_url(embed):
                saw_image.append(True)
            if len(images) < _MAX_IMAGES:
                part = await _fetch_image(embed_image_url(embed), seen)
                if part is not None:
                    images.append(part)
    for sticker in list(getattr(source, "stickers", None) or []):
        name = str(getattr(sticker, "name", "") or "sticker").strip()
        pieces.append(f"Sticker: {name}")
        labels.append(name)
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
    await _consume(
        refreshed, "Sent just now", blocks, images, labels, seen, saw_image, include_text=False
    )
    # A reply's link preview is not a file the person attached. Keep a short
    # clip of that message for this turn only, and do not store the card.
    reply_blocks: list[str] = []
    replied = await _referenced(refreshed)
    if replied is not None and getattr(replied, "id", None) != getattr(refreshed, "id", None):
        await _consume(
            replied,
            "Replied-to message",
            reply_blocks,
            images,
            labels,
            seen,
            saw_image,
            include_text=True,
            include_embeds=False,
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
        )
    own = "\n\n".join(blocks).strip()
    reply_note = "\n\n".join(reply_blocks).strip()
    text = "\n\n".join(part for part in (own, reply_note) if part)
    if text and saw_image:
        text = "If you cannot see an image, say so. Do not guess what is in it.\n" + text
    if text:
        text = _clip(text, _MEDIA_CLIP)
    images = _fit_images(images)
    summary = ", ".join(labels[:4])
    if len(labels) > 4:
        summary += f", +{len(labels) - 4}"
    # Reply text stays on this turn. It is not written into the memory pin.
    return DiscordMedia(
        text=text,
        memory=_clip(own, _MEMORY_CLIP) if own else "",
        summary=summary,
        images=images,
        message=refreshed,
    )
