# core/asset_message.py
# The picture, gif, or video on this message, or on the message it replies to.
# A bare ?asset can also use the newest image already in the channel.
# The model does not get to supply a URL. Embed bytes are read only from Discord's CDN.

from __future__ import annotations

import asyncio
import re
import urllib.parse
import urllib.request

from core.imagine import MAX_BYTES, ImagineError, SourceMedia, mime_for

# How far ?asset looks when the command does not reply to a file.
_HISTORY_LIMIT = 50

_DISCORD_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
_IMAGES_EXT = re.compile(r"^images-ext-\d+\.discordapp\.net$")


def _discord_media_url(url: str) -> bool:
    """True for https on Discord's own image and attachment hosts."""
    parts = urllib.parse.urlsplit(url or "")
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    return host in _DISCORD_HOSTS or bool(_IMAGES_EXT.match(host))


def _check_discord_url(url: str) -> str:
    if not _discord_media_url(url):
        raise ImagineError("I couldn't read the file on that message.")
    return url


class _StayOnDiscord(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when it stays on a Discord media host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_discord_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _name_from_url(url: str) -> str:
    path = urllib.parse.urlsplit(url or "").path
    name = path.rsplit("/", 1)[-1]
    return urllib.parse.unquote(name)


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    return ""


def _fetch_url(url: str) -> tuple[bytes, str]:
    """Download one Discord-hosted file. No other host is opened."""
    _check_discord_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Eche/2.1.0"})
    opener = urllib.request.build_opener(_StayOnDiscord)
    try:
        with opener.open(req, timeout=30) as resp:
            _check_discord_url(resp.geturl())
            header = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            chunks = []
            total = 0
            while True:
                piece = resp.read(64 * 1024)
                if not piece:
                    break
                total += len(piece)
                if total > MAX_BYTES:
                    raise ImagineError("That file is too big to send to Imagine.")
                chunks.append(piece)
    except ImagineError:
        raise
    except Exception as exc:
        raise ImagineError("I couldn't read the file on that message.") from exc
    data = b"".join(chunks)
    if not data:
        raise ImagineError("I couldn't read the file on that message.")
    mime = mime_for(header, _name_from_url(url)) or _sniff_mime(data)
    return data, mime


def _media_url(media) -> str:
    if media is None:
        return ""
    proxy = str(getattr(media, "proxy_url", "") or "").strip()
    direct = str(getattr(media, "url", "") or "").strip()
    return proxy or direct


def _embed_urls(embed, *, images_only: bool) -> list[str]:
    """Picture first. A gifv or video embed leads with the clip."""
    image = _media_url(getattr(embed, "image", None))
    video = _media_url(getattr(embed, "video", None))
    thumb = _media_url(getattr(embed, "thumbnail", None))
    if images_only:
        urls = [image] if image else [thumb]
        return [url for url in urls if url]
    kind = str(getattr(embed, "type", "") or "").casefold()
    if kind in {"gifv", "video"} and video:
        ordered = (video, image)
    else:
        ordered = (image, video)
    return [url for url in ordered if url]


def _bodies(message):
    """The message itself, then a forwarded snapshot carried on it."""
    yield message
    for snap in list(getattr(message, "message_snapshots", None) or []):
        yield snap


async def _read(attachment, *, images_only: bool = False) -> SourceMedia | None:
    filename = str(getattr(attachment, "filename", "") or "")
    mime = mime_for(str(getattr(attachment, "content_type", "") or ""), filename)
    if not mime:
        return None
    if images_only and mime.startswith("video/"):
        return None
    size = getattr(attachment, "size", None)
    if isinstance(size, int) and size > MAX_BYTES:
        raise ImagineError("That file is too big to send to Imagine.")
    data = await attachment.read()
    if not isinstance(data, (bytes, bytearray)):
        raise ImagineError("Could not read that file.")
    if len(data) > MAX_BYTES:
        raise ImagineError("That file is too big to send to Imagine.")
    return SourceMedia(data=bytes(data), mime=mime)


async def _usable(attachments, *, images_only: bool = False) -> tuple[SourceMedia | None, bool]:
    """The first picture, gif, or video, and whether any file was attached.

    A text file or a pdf is skipped. A picture or video that is too big
    still raises from _read. History search passes images_only so a video
    is left for a later picture on that same message.
    """
    saw = False
    for attachment in attachments or []:
        saw = True
        source = await _read(attachment, images_only=images_only)
        if source is not None:
            return source, True
    return None, saw


async def _embed_media(embed, *, images_only: bool) -> SourceMedia | None:
    """A picture, gif, or video Discord is hosting on this embed."""
    tried = False
    too_big = False
    for url in _embed_urls(embed, images_only=images_only):
        if not _discord_media_url(url):
            continue
        tried = True
        try:
            data, mime = await asyncio.to_thread(_fetch_url, url)
        except ImagineError as exc:
            if exc.text.startswith("That file is too big"):
                too_big = True
            continue
        if not data or not mime or (images_only and mime.startswith("video/")):
            continue
        return SourceMedia(data=data, mime=mime)
    if images_only or not tried:
        return None
    if too_big:
        raise ImagineError("That file is too big to send to Imagine.")
    raise ImagineError("I couldn't read the file on that message.")


async def _from_message(message, *, images_only: bool = False) -> tuple[SourceMedia | None, bool]:
    saw = False
    for body in _bodies(message):
        media, body_saw = await _usable(getattr(body, "attachments", None), images_only=images_only)
        saw = saw or body_saw
        if media is not None:
            return media, True
        for embed in list(getattr(body, "embeds", None) or []):
            media = await _embed_media(embed, images_only=images_only)
            if media is not None:
                return media, saw
    return None, saw


async def _resolve_reply(message, ref):
    replied = getattr(ref, "resolved", None)
    if replied is not None:
        return replied
    channel = getattr(message, "channel", None)
    fetch = getattr(channel, "fetch_message", None)
    if fetch is None:
        raise ImagineError("I couldn't read the message you replied to.")
    try:
        replied = await fetch(getattr(ref, "message_id", None))
    except Exception as exc:
        raise ImagineError("I couldn't read the message you replied to.") from exc
    if replied is None:
        raise ImagineError("I couldn't read the message you replied to.")
    return replied


async def _recent_image(message) -> SourceMedia | None:
    """Newest picture or gif in this channel, before the command."""
    channel = getattr(message, "channel", None)
    history = getattr(channel, "history", None)
    if history is None:
        return None
    try:
        older = history(limit=_HISTORY_LIMIT, before=message)
    except Exception:
        return None
    try:
        async for item in older:
            try:
                found, _saw = await _from_message(item, images_only=True)
            except ImagineError:
                continue
            if found is not None:
                return found
    except Exception:
        return None
    return None


async def source_from_message(message, *, recent_image: bool = False) -> SourceMedia | None:
    """Picture, gif, or video for Imagine.

    The command's own file wins, then the message it replies to. A file
    Discord hosted on an embed counts, including a gif. recent_image is
    ?asset only: with no reply, use the newest image already in the channel.
    """
    own, own_files = await _from_message(message)
    if own is not None:
        return own
    ref = getattr(message, "reference", None)
    message_id = getattr(ref, "message_id", None) if ref is not None else None
    if message_id:
        replied = await _resolve_reply(message, ref)
        other, _other_files = await _from_message(replied)
        if other is not None:
            return other
        if own_files:
            raise ImagineError("Attach a picture or a video, or describe what to make.")
        return None
    if own_files:
        raise ImagineError("Attach a picture or a video, or describe what to make.")
    if recent_image:
        return await _recent_image(message)
    return None
