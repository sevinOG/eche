# core/imagine.py
# Grok Imagine for ?asset and the asset tool.
# The host is fixed. A prompt cannot point this at another server.
# The API key is read when a request runs and is not written to the log.

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://api.x.ai/v1"
IMAGE_MODEL = "grok-imagine-image-2.0"
VIDEO_MODEL = "grok-imagine-video-1.5-lite"
VIDEO_EDIT_MODEL = "grok-imagine-video"
MAX_PROMPT = 2000
MAX_SECONDS = 8
DEFAULT_SECONDS = 6
MAX_BYTES = 8_000_000
VIDEO_WAIT_SECONDS = 8 * 60
_POLL_SECONDS = 5

_IMAGE_ASPECTS = (
    "19.5:9",
    "9:19.5",
    "16:9",
    "9:16",
    "21:9",
    "20:9",
    "9:20",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "5:2",
    "1:1",
)
_VIDEO_ASPECTS = (
    "16:9",
    "9:16",
    "21:9",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "5:2",
    "1:1",
)
_VIDEO_WORDS = ("video", "clip", "animation", "animate", "movie", "gif")
# clip and movie are kind words only when they lead the prompt.
# "a paper clip" and "a movie poster" stay pictures.
_VIDEO_BODY = ("video", "animation", "animate", "gif")
_IMAGE_WORDS = ("image", "picture", "photo", "drawing")
_AUDIO_WORDS = ("song", "audio", "sound", "voiceover", "music")
_SECONDS = re.compile(
    r"\b(\d{1,2})\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)

_IMAGE_MIME = {
    "image/jpeg": "asset.jpg",
    "image/jpg": "asset.jpg",
    "image/png": "asset.png",
    "image/webp": "asset.webp",
    "image/gif": "asset.gif",
}
_VIDEO_MIME = {
    "video/mp4": "asset.mp4",
    "video/webm": "asset.webm",
    "video/quicktime": "asset.mov",
    "video/mpeg": "asset.mpeg",
}
_SUFFIX_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}


@dataclass
class SourceMedia:
    data: bytes
    mime: str

    @property
    def kind(self) -> str:
        if self.mime in _VIDEO_MIME:
            return "video"
        return "image"


@dataclass
class AssetPlan:
    mode: str
    prompt: str
    seconds: int = DEFAULT_SECONDS
    aspect: str = ""
    source: SourceMedia | None = None


@dataclass
class AssetFile:
    data: bytes
    filename: str
    mime: str
    mode: str


class ImagineError(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def api_key() -> str:
    saved = ""
    try:
        from core.secrets import load_all
        saved = str(load_all().get("xai_api_key") or "").strip()
    except Exception:
        saved = ""
    return saved or (os.getenv("XAI_API_KEY") or "").strip()


def _flag(stored) -> bool:
    return str(stored or "").strip().lower() in ("1", "true", "yes", "on")


def asset_audience() -> str:
    """all, unless Owner only is on."""
    owner_only = False
    try:
        from core.secrets import load_all
        owner_only = _flag(load_all().get("asset_owner_only"))
    except Exception:
        owner_only = _flag(os.getenv("ECHE_ASSET_OWNER_ONLY"))
    return "owner" if owner_only else "all"


def speaker_allowed(is_owner: bool) -> bool:
    if asset_audience() == "owner":
        return bool(is_owner)
    return True


def refusal(is_owner: bool) -> str:
    del is_owner
    return "Only the owner can ask for assets."


def mime_for(content_type: str, filename: str) -> str:
    raw = (content_type or "").split(";", 1)[0].strip().lower()
    if raw in _IMAGE_MIME or raw in _VIDEO_MIME:
        return "image/jpeg" if raw == "image/jpg" else raw
    suffix = ""
    name = (filename or "").lower()
    dot = name.rfind(".")
    if dot >= 0:
        suffix = name[dot:]
    return _SUFFIX_MIME.get(suffix, "")


def filename_for(mime: str, fallback: str = "asset.bin") -> str:
    return _IMAGE_MIME.get(mime) or _VIDEO_MIME.get(mime) or fallback


def _words(text: str) -> str:
    return " ".join((text or "").split())


def _leading_kind(text: str) -> tuple[str, str]:
    parts = text.split(None, 1)
    if not parts:
        return "", ""
    head = parts[0].casefold().strip(".,:;")
    rest = parts[1] if len(parts) > 1 else ""
    if head in _VIDEO_WORDS:
        return "video", rest
    if head in _IMAGE_WORDS:
        return "image", rest
    return "", text


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    folded = f" {text.casefold()} "
    return any(f" {word} " in folded for word in words)


def _aspect(text: str, allowed: tuple[str, ...]) -> str:
    folded = text.casefold()
    for ratio in allowed:
        token = re.escape(ratio.casefold())
        if re.search(rf"(?<![\d.]){token}(?![\d.])", folded):
            return ratio
    return ""


def _seconds(text: str) -> int:
    match = _SECONDS.search(text or "")
    if not match:
        return DEFAULT_SECONDS
    try:
        value = int(match.group(1))
    except ValueError:
        return DEFAULT_SECONDS
    if value < 1:
        return 1
    if value > MAX_SECONDS:
        return MAX_SECONDS
    return value


def plan_asset(text: str, *, kind: str = "", source: SourceMedia | None = None) -> AssetPlan:
    """Decide image, video, edit, or image-to-video. Raises ImagineError when the ask is not one of those."""
    raw = _words(text)
    asked, body = _leading_kind(raw)
    explicit = (kind or "").strip().casefold()
    if explicit in ("image", "video"):
        asked = explicit
    elif not asked:
        if _has_word(raw, _VIDEO_BODY):
            asked = "video"
        elif _has_word(raw, _IMAGE_WORDS):
            asked = "image"
    prompt = _words(body if asked and body != raw else raw)
    if explicit in ("image", "video") and asked == explicit:
        # A tool kind is not part of the prompt. A leading kind word was already removed.
        prompt = _words(raw)
        lead, rest = _leading_kind(raw)
        if lead == explicit:
            prompt = _words(rest)
    if not prompt and source is None:
        raise ImagineError("Say what to make. `?asset a red paper boat`")
    if not prompt and source is not None:
        raise ImagineError("Say what to change.")
    if len(prompt) > MAX_PROMPT:
        prompt = prompt[:MAX_PROMPT].rstrip()
    visual = asked in ("image", "video") or _has_word(prompt, _VIDEO_WORDS + _IMAGE_WORDS)
    if source is None and not visual and _has_word(prompt, _AUDIO_WORDS):
        raise ImagineError(
            "Imagine makes a picture or a video. Say which, and what it should show."
        )
    if not asked:
        asked = "video" if source is not None and source.kind == "video" else "image"
    if asked == "video":
        if source is not None and source.kind == "video":
            mode = "video_edit"
        elif source is not None and source.kind == "image":
            mode = "image_video"
        else:
            mode = "video"
        aspects = _VIDEO_ASPECTS
    else:
        if source is not None and source.kind == "video":
            raise ImagineError(
                "That file is a video. Ask for a video, or attach a picture."
            )
        if source is not None and source.kind == "image":
            mode = "image_edit"
        else:
            mode = "image"
        aspects = _IMAGE_ASPECTS
    return AssetPlan(
        mode=mode,
        prompt=prompt,
        seconds=_seconds(raw) if mode in ("video", "image_video") else DEFAULT_SECONDS,
        aspect=_aspect(raw, aspects),
        source=source if mode != "image" else None,
    )


def _data_uri(source: SourceMedia) -> str:
    encoded = base64.b64encode(source.data).decode("ascii")
    return f"data:{source.mime};base64,{encoded}"


def request_for(plan: AssetPlan) -> tuple[str, dict]:
    """POST path and JSON body. No network."""
    if plan.mode == "image":
        body: dict = {"model": IMAGE_MODEL, "prompt": plan.prompt}
        if plan.aspect:
            body["aspect_ratio"] = plan.aspect
        return "/images/generations", body
    if plan.mode == "image_edit":
        body = {
            "model": IMAGE_MODEL,
            "prompt": plan.prompt,
            "image": {"url": _data_uri(plan.source), "type": "image_url"},
        }
        if plan.aspect:
            body["aspect_ratio"] = plan.aspect
        return "/images/edits", body
    if plan.mode == "video_edit":
        return "/videos/edits", {
            "model": VIDEO_EDIT_MODEL,
            "prompt": plan.prompt,
            "video": {"url": _data_uri(plan.source)},
        }
    body = {
        "model": VIDEO_MODEL,
        "prompt": plan.prompt,
        "duration": plan.seconds,
        "resolution": "480p",
    }
    if plan.aspect:
        body["aspect_ratio"] = plan.aspect
    if plan.mode == "image_video":
        body["image"] = {"url": _data_uri(plan.source)}
    return "/videos/generations", body


def _error_text(payload: dict, status: int) -> str:
    err = payload.get("error") if isinstance(payload, dict) else None
    message = ""
    if isinstance(err, dict):
        message = str(err.get("message") or err.get("code") or "")
    elif isinstance(err, str):
        message = err
    message = " ".join(message.split())
    if not message:
        message = f"Imagine returned {status}."
    if len(message) > 180:
        message = message[:179].rstrip() + "…"
    return message


def _filtered(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("respect_moderation") is False:
        return True
    video = payload.get("video")
    if isinstance(video, dict) and video.get("respect_moderation") is False:
        return True
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("respect_moderation") is False:
                return True
    return False


def image_url(payload: dict) -> str:
    if _filtered(payload):
        raise ImagineError("That one was filtered.")
    data = payload.get("data") if isinstance(payload, dict) else None
    item = data[0] if isinstance(data, list) and data else {}
    if not isinstance(item, dict):
        item = {}
    url = str(item.get("url") or "").strip()
    if url:
        return url
    raw = item.get("b64_json") or item.get("image")
    if isinstance(raw, str) and raw.strip():
        return "b64:" + raw.strip()
    raise ImagineError("Imagine did not return an image.")


def video_start_id(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ImagineError("Imagine did not start the video.")
    request_id = str(payload.get("request_id") or "").strip()
    if request_id:
        return request_id
    raise ImagineError(_error_text(payload, 200))


def video_poll(payload: dict) -> tuple[str, str]:
    """pending, done plus a url, failed, or expired."""
    if not isinstance(payload, dict):
        return "failed", "Imagine did not finish the video."
    status = str(payload.get("status") or "").strip().lower()
    if status in ("", "pending"):
        return "pending", ""
    if status == "done":
        if _filtered(payload):
            raise ImagineError("That one was filtered.")
        video = payload.get("video") if isinstance(payload.get("video"), dict) else {}
        url = str(video.get("url") or "").strip()
        if not url:
            raise ImagineError("Imagine did not return a video.")
        return "done", url
    if status == "expired":
        return "expired", "That video request expired."
    err = payload.get("error")
    if isinstance(err, dict):
        return "failed", _error_text(payload, 200)
    return "failed", "Imagine did not finish the video."


def http_json(method: str, url: str, key: str, body: dict | None = None, timeout: int = 120) -> tuple[int, dict]:
    data = None
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "Eche/2.1.0",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200) or 200
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    except Exception as exc:
        raise ImagineError("Could not reach Imagine.") from exc
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    if status >= 400:
        raise ImagineError(_error_text(payload if isinstance(payload, dict) else {}, status))
    if not isinstance(payload, dict):
        raise ImagineError("Imagine returned an unexpected response.")
    return status, payload


def _host_name(url: str) -> str:
    return (urllib.parse.urlsplit(url or "").hostname or "").casefold().rstrip(".")


def _xai_host(host: str) -> bool:
    name = (host or "").casefold().rstrip(".")
    return name == "x.ai" or name.endswith(".x.ai")


def _check_download_url(url: str, previous_host: str = "") -> str:
    """https on an x.ai host. A redirect has to stay on that same host."""
    parts = urllib.parse.urlsplit(url or "")
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme != "https" or not _xai_host(host) or parts.username or parts.password:
        raise ImagineError("Imagine returned a file from a server I will not use.")
    prior = (previous_host or "").casefold().rstrip(".")
    if prior and host != prior:
        raise ImagineError("Imagine returned a file from a server I will not use.")
    return url


class _StayOnHost(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when it stays on the same https x.ai host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_download_url(newurl, previous_host=_host_name(req.full_url))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def http_bytes(url: str, limit: int = MAX_BYTES, timeout: int = 120) -> tuple[bytes, str]:
    """Download one finished file. Only https on an x.ai host, or a b64: image."""
    if url.startswith("b64:"):
        raw = url[4:]
        if len(raw) > ((limit + 2) // 3) * 4 + 8:
            raise ImagineError("That file is too big to attach.")
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise ImagineError("Imagine returned an image I could not read.") from exc
        if not data:
            raise ImagineError("Imagine returned an image I could not read.")
        if len(data) > limit:
            raise ImagineError("That file is too big to attach.")
        mime = _sniff_image_mime(data) or "image/jpeg"
        return data, mime
    _check_download_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Eche/2.1.0"})
    opener = urllib.request.build_opener(_StayOnHost)
    try:
        with opener.open(req, timeout=timeout) as resp:
            final = resp.geturl()
            _check_download_url(final, previous_host=_host_name(url))
            mime = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            chunks = []
            total = 0
            while True:
                piece = resp.read(64 * 1024)
                if not piece:
                    break
                total += len(piece)
                if total > limit:
                    return b"", mime
                chunks.append(piece)
    except ImagineError:
        raise
    except Exception as exc:
        raise ImagineError("Could not download the finished file.") from exc
    return b"".join(chunks), mime


async def run_asset(plan: AssetPlan, key: str) -> AssetFile | str:
    """Make one asset. A string result is a temporary URL that was too large to attach."""
    if not key:
        raise ImagineError("Add a Grok API key in Media settings.")
    path, body = request_for(plan)
    timeout = 180 if plan.mode.startswith("video") or plan.mode == "image_video" else 120

    def _post():
        return http_json("POST", API + path, key, body, timeout=timeout)

    _status, payload = await asyncio.to_thread(_post)
    if plan.mode in ("image", "image_edit"):
        url = image_url(payload)
    else:
        request_id = video_start_id(payload)
        deadline = asyncio.get_running_loop().time() + VIDEO_WAIT_SECONDS
        url = ""
        while True:
            def _poll(rid=request_id):
                return http_json("GET", f"{API}/videos/{rid}", key, None, timeout=60)

            _status, polled = await asyncio.to_thread(_poll)
            state, found = video_poll(polled)
            if state == "done":
                url = found
                break
            if state in ("failed", "expired"):
                raise ImagineError(found or "Imagine did not finish the video.")
            if asyncio.get_running_loop().time() >= deadline:
                raise ImagineError("That video took too long. Try a shorter one.")
            await asyncio.sleep(_POLL_SECONDS)
    data, mime = await asyncio.to_thread(http_bytes, url)
    if not data:
        return url
    if not mime or mime == "application/octet-stream":
        mime = "video/mp4" if "video" in plan.mode else "image/jpeg"
    return AssetFile(data=data, filename=filename_for(mime), mime=mime, mode=plan.mode)
