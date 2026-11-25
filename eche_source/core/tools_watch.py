# core/tools_watch.py
# watch_video: a timestamped look at one Discord video, on request.
#
# Ported from Waerhouse's watch_video (waerhouse/watch_video.py and the
# watch_video tool) for Eche as a Discord bot. Eche already shows the chat
# model 4-6 frames of one video in the message itself (core/chat_media). This
# tool is for "what happens in that video": it finds a video in this message,
# the message it replies to, or the last few messages in this channel, picks
# frames where the picture changes (ffmpeg scene score, topped up evenly) over
# the first ten minutes, and has the chat model describe them in order with
# timestamps. The description goes back to the chat model, which answers.
#
# Media safety: the frames are media Eche only VIEWS for a chat reply, so they
# take the same path as the video frames on a chat turn (core/chat_media,
# owner decision 2026-11-18): a safety audit row first (kinds, counts, short
# sha256 prefixes; never pixels, URLs or tokens), and nothing is sent if that
# row can't be written. The description itself still goes through the chat
# call's output check. Nothing here makes, edits or posts a picture or video.
#
# Seeing: the frames go to a local vision model through Ollama first
# (qwen3-vl:4b-instruct, the same default as Waerhouse's vision helper), and
# to the configured chat model only when that is missing or fails. Local
# first because it is free, does not touch the provider quota, and a small
# purpose-built VL model is steadier at "[m:ss] what is visible" than a chat
# model; the chat model stays as the fallback so a PC without the model still
# watches. Ollama is shared with Waerhouse: localhost only, one request,
# keep_alive 0 (the model leaves VRAM as soon as it answers, and an explicit
# unload follows a timeout), at most MAX_FRAMES small frames, a timeout.
# ECHE_WATCH_VISION_MODEL picks another tag ("off" skips local); ECHE_WATCH_VISION
# = local / chat pins one route.
#
# Hearing: core/watch_listen (faster-whisper on the CPU, optional). It runs
# at the same time as the vision call, after the audit row.
#
# Bounded: one watch at a time, ffmpeg timeouts, and a per-video cache so a
# follow-up question does not watch the clip again.

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict

from core.debuglog import dprint
from core.tools import ACCESS_ANYONE, Tool, ToolContext, ToolResult, register

TOOL_NAME = "watch_video"

MAX_FRAMES = 6            # Eche's per-turn frame count (core/chat_media.FRAME_COUNT)
MIN_FRAMES = 3
MAX_SECONDS = 600.0       # longer clips are watched over their first ten minutes
FRAME_EDGE = 448          # longest side of a frame; 6 x 448 px stays near the vision budget
SCENE_THRESHOLD = 0.3
SCENE_TIMEOUT_S = 45
FRAME_TIMEOUT_S = 15
PROBE_TIMEOUT_S = 20
DESCRIBE_TOKENS = 450
WATCH_TIMEOUT_S = 240     # frames + (local vision | chat fallback) alongside the transcript
LOCAL_VISION_MODEL = "qwen3-vl:4b-instruct"   # Waerhouse's DEFAULT_LOCAL_CHECK_MODEL / vision helper
LOCAL_VISION_TIMEOUT_S = 90                   # includes loading a 4B model onto the card
LOCAL_UNLOAD_TIMEOUT_S = 5
LOCAL_KEEP_ALIVE = 0                          # unload right after the answer: the card is shared
LOCAL_NUM_CTX = 4096                          # 6 frames at 448 px are ~1.2k image tokens
OLLAMA_DEFAULT = "http://127.0.0.1:11434"
HISTORY_LOOKBACK = 15     # earlier messages in this channel searched for a video
RESULT_CHARS = 1500       # core.client.lookup_prompt keeps 1500 characters of notes

NO_FFMPEG = "I can't watch videos here: ffmpeg isn't installed."
NO_VIDEO = (
    "I couldn't find a video or audio clip in this message, the message it replies to, "
    "or the last few messages here."
)
TOO_LARGE = "That clip is too large for me to open (25 MB limit)."
NOT_A_VIDEO = (
    "I found an attachment, but it isn't a video or audio file I can open "
    "(mp4, mov, webm, mkv, mp3, m4a, ogg/opus, wav, flac)."
)
NO_LISTEN = (
    "I can't listen to audio clips here: local speech-to-text (faster-whisper) isn't installed."
)
LISTEN_OFF = "Listening to audio clips is turned off here."
LISTEN_LOADING = "My speech-to-text model is still downloading. Ask again in a minute."
NO_SOUND = "I couldn't find any sound in that clip."
LISTEN_FAILED = "I couldn't transcribe that audio clip."
NO_DOWNLOAD = "I couldn't download that video."
NO_FRAMES = "I couldn't read any frames from that video."
CANT_SEE = (
    "I can't watch that video: the local vision model isn't available and the chat model can't see images."
)
BUSY = "I'm already watching a video. Ask again in a moment."
FAILED = "I couldn't watch that video: something went wrong while I was processing it."
TIMED_OUT = "I couldn't watch that video: it took too long to process."
NO_AUDIT = "I couldn't watch that video: my safety log couldn't be written, so I didn't open it."

_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_STAMP_LINE = re.compile(r"\[\d+:\d{2}\]")

_lock = asyncio.Lock()
_CACHE_MAX = 32
_seen: "OrderedDict[str, str]" = OrderedDict()

_DESCRIPTION = (
    "Watch a video or listen to an audio clip (mp3, m4a, ogg/opus, Discord voice messages, wav, flac...) "
    "in this Discord chat: attached to this message, in the message it replies to, or posted in the "
    "last few messages. A video gets a timestamped description of what happens, with what is said "
    "when the sound can be transcribed; an audio clip gets its length and a timestamped transcript. "
    "Use it when they ask what a video shows, what happens in it, or what is said in a video or audio clip."
)


# --- frame picking (pure; ported from Waerhouse watch_video.py) ------------------------------

def stamp(seconds: float) -> str:
    seconds = max(0, int(round(float(seconds))))
    return f"{seconds // 60}:{seconds % 60:02d}"


def even_times(duration: float, count: int) -> list[float]:
    """`count` timestamps at the middle of equal slices (never exactly the last frame)."""
    count = max(1, int(count))
    duration = max(0.0, float(duration))
    return [round((index + 0.5) * duration / count, 2) for index in range(count)]


def pick_times(duration: float, scenes: list[float], max_frames: int = MAX_FRAMES) -> list[float]:
    """The first moment, scene changes (thinned to fit), topped up evenly to MIN_FRAMES.

    Sorted, capped at MAX_FRAMES, no two frames within 0.4 s."""
    max_frames = max(1, min(int(max_frames), MAX_FRAMES))
    duration = min(float(duration), MAX_SECONDS)
    if duration <= 0:
        return [0.0]
    if not scenes:
        return even_times(duration, min(max_frames, max(MIN_FRAMES, int(duration // 2) or 1)))
    chosen = [0.0] + [t for t in scenes if 0.4 < t < duration]
    if len(chosen) > max_frames:
        step = (len(chosen) - 1) / (max_frames - 1) if max_frames > 1 else 1
        chosen = [chosen[int(round(i * step))] for i in range(max_frames)]
    if len(chosen) < MIN_FRAMES:
        for t in even_times(duration, MIN_FRAMES):
            if all(abs(t - c) > 0.5 for c in chosen):
                chosen.append(t)
    out: list[float] = []
    for t in sorted(chosen):
        if not out or t - out[-1] >= 0.4:
            out.append(t)
    return out[:max_frames]


# --- ffmpeg ----------------------------------------------------------------------------------

_ffmpeg_seen: list = []  # [(monotonic time, path)]: looked up at most once a minute


def find_ffmpeg() -> str:
    import time

    now = time.monotonic()
    if _ffmpeg_seen and now - _ffmpeg_seen[0][0] < 60:
        return _ffmpeg_seen[0][1]
    found = ""
    try:
        from core.paths import find_ffmpeg as _find

        found = _find() or ""
    except Exception:
        found = ""
    found = found or shutil.which("ffmpeg") or ""
    _ffmpeg_seen[:] = [(now, found)]
    return found


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False, **kwargs)


def probe(ffmpeg: str, path: str) -> tuple[float, bool, bool]:
    """(duration seconds, has_video, has_audio) from ffmpeg's stream summary. 0 s fails closed."""
    proc = _run([ffmpeg, "-hide_banner", "-i", path], PROBE_TIMEOUT_S)
    text = (proc.stderr or b"").decode("utf-8", "replace")
    duration = 0.0
    match = _DURATION.search(text)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    has_video = bool(re.search(r"Stream #\S+.*Video:", text))
    has_audio = bool(re.search(r"Stream #\S+.*Audio:", text))
    return duration, has_video, has_audio


def scene_times(ffmpeg: str, path: str, duration: float) -> list[float]:
    """Timestamps where the picture changes a lot. [] when ffmpeg can't tell in time."""
    try:
        proc = _run(
            [ffmpeg, "-hide_banner", "-i", path, "-t", str(int(MAX_SECONDS)), "-vf",
             f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-an", "-f", "null", "-"],
            SCENE_TIMEOUT_S,
        )
    except Exception:
        return []
    text = (proc.stderr or b"").decode("utf-8", "replace")
    return [round(float(m.group(1)), 2) for m in _PTS.finditer(text) if float(m.group(1)) < duration]


def frame_at(ffmpeg: str, path: str, t: float) -> bytes:
    scale = f"scale='if(gt(iw,ih),{FRAME_EDGE},-2)':'if(gt(iw,ih),-2,{FRAME_EDGE})'"
    try:
        proc = _run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path,
             "-frames:v", "1", "-vf", scale, "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
            FRAME_TIMEOUT_S,
        )
    except Exception:
        return b""
    data = bytes(proc.stdout or b"")
    return data if data.startswith(b"\xff\xd8") else b""


def sample_frames(video: bytes, mime: str = "video/mp4") -> tuple[list[tuple[float, bytes]], float, bool]:
    """([(seconds, jpeg)], duration, has_audio). Raises ValueError with a user line."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError(NO_FFMPEG)
    suffix = ".webm" if "webm" in (mime or "") else ".mov" if "quicktime" in (mime or "") else ".mp4"
    folder = tempfile.mkdtemp(prefix="eche-watch-")
    try:
        path = os.path.join(folder, "clip" + suffix)
        with open(path, "wb") as fh:
            fh.write(bytes(video))
        duration, has_video, has_audio = probe(ffmpeg, path)
        if not has_video or duration <= 0:
            raise ValueError(NO_FRAMES)
        scenes = scene_times(ffmpeg, path, min(duration, MAX_SECONDS))
        frames: list[tuple[float, bytes]] = []
        for t in pick_times(duration, scenes):
            blob = frame_at(ffmpeg, path, t)
            if blob:
                frames.append((t, blob))
        if not frames:
            raise ValueError(NO_FRAMES)
        return frames, duration, has_audio
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# --- finding the video on Discord ------------------------------------------------------------
#
# The model's message_id is only a HINT. Live 2026-11-25 14:36/14:37 ET: the model copied the
# ATTACHMENT id out of the attachment url in the prompt (cdn.../attachments/<channel>/<attachment>/
# name) and passed it as message_id; fetch_message 404'd and the old code searched nothing else, so a
# video attached to the very message that asked was "not found". Now a hint is tried first (as a
# message in THIS channel, or as an attachment id on the messages searched), and the automatic search
# (this message, the replied-to message, recent history) always runs after it.

_SNOWFLAKE = re.compile(r"(?<!\d)(\d{15,22})(?!\d)")
# Discord phone uploads: .mov (video/quicktime), sometimes no content type at all.
_VIDEO_SUFFIXES = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".3gp")


# Audio clips (Discord voice messages are voice-message.ogg, audio/ogg; Opus inside).
_AUDIO_SUFFIXES = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".wma",
                   ".aif", ".aiff", ".amr", ".weba", ".mka")
_AUDIO_MIME_BY_SUFFIX = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".opus": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac", ".wma": "audio/x-ms-wma",
    ".aif": "audio/aiff", ".aiff": "audio/aiff", ".amr": "audio/amr", ".weba": "audio/webm", ".mka": "audio/x-matroska",
}
_SUFFIX_BY_AUDIO_MIME = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac",
    "audio/ogg": ".ogg", "audio/opus": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/webm": ".webm", "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff", "audio/amr": ".amr", "audio/x-ms-wma": ".wma", "audio/x-matroska": ".mka",
}


def _name_and_type(item) -> tuple[str, str]:
    from core.message_media import _content_type, _filename

    return (_filename(item) or "").lower().split("?")[0], (_content_type(item) or "").lower()


def _is_audio_attachment(item) -> bool:
    """audio/* (audio/webm included), or a missing/generic type with an audio file name."""
    name, kind = _name_and_type(item)
    if kind.startswith("audio/"):
        return True
    if kind.startswith("video/"):
        return False
    return name.endswith(_AUDIO_SUFFIXES)


def _is_video_attachment(item) -> bool:
    from core.message_media import _content_type, _filename, _is_video_item

    if _is_audio_attachment(item):
        return False
    if _is_video_item(item):
        return True
    name = (_filename(item) or "").lower().split("?")[0]
    if name.endswith(_VIDEO_SUFFIXES):
        return True
    kind = (_content_type(item) or "").lower()
    return kind in ("application/mp4", "application/x-matroska")


def _video_refs(message) -> list[tuple[str, object]]:
    """[("attachment", item) | ("audio", item) | ("embed", url)] for the clips on one message, in order.

    Forwarded messages carry their files on message_snapshots."""
    from core.message_media import _embed_media

    sources = [message] + list(getattr(message, "message_snapshots", None) or [])
    refs: list[tuple[str, object]] = []
    for source in sources:
        for item in list(getattr(source, "attachments", None) or []):
            if _is_video_attachment(item):
                refs.append(("attachment", item))
            elif _is_audio_attachment(item):
                refs.append(("audio", item))
        for embed in list(getattr(source, "embeds", None) or []):
            url, is_video = _embed_media(embed)
            if not is_video and str(getattr(embed, "type", "") or "") == "gifv":
                video = getattr(embed, "video", None)
                url = str(getattr(video, "proxy_url", "") or getattr(video, "url", "") or "").strip()
                is_video = bool(url)
            if url and is_video:
                refs.append(("embed", url))
    return refs


def _other_files(message) -> int:
    sources = [message] + list(getattr(message, "message_snapshots", None) or [])
    return sum(
        1
        for source in sources
        for item in list(getattr(source, "attachments", None) or [])
        if not _is_video_attachment(item) and not _is_audio_attachment(item)
    )


_JUMP = re.compile(r"channels/(?:\d+|@me)/\d+/(\d{15,22})")
_ATTACHMENT_URL = re.compile(r"attachments/\d+/(\d{15,22})/")


def hint_ids(wanted: str) -> list[int]:
    """Ids in the model's message_id: the message of a jump link, the attachment of an attachment url,
    else every bare snowflake (at most four, so a junk hint costs at most four lookups)."""
    text = wanted or ""
    picked = _JUMP.findall(text) + _ATTACHMENT_URL.findall(text)
    if not picked:
        picked = _SNOWFLAKE.findall(text)
    return list(dict.fromkeys(int(m) for m in picked))[:4]


async def _auto_candidates(message):
    """("trigger"|"reply"|"history", message), best first."""
    yield "trigger", message
    from core.message_media import _referenced

    try:
        replied = await _referenced(message)
    except Exception:
        replied = None
    if replied is not None:
        yield "reply", replied
    history = getattr(getattr(message, "channel", None), "history", None)
    if history is None:
        return
    try:
        async for earlier in history(limit=HISTORY_LOOKBACK, before=message):
            yield "history", earlier
    except Exception as exc:
        dprint(f"[watch_video] history read failed: {type(exc).__name__}")


async def _hinted_messages(message, ids: list[int]):
    """Messages in THIS channel named by the hint (never another channel or server)."""
    fetch = getattr(getattr(message, "channel", None), "fetch_message", None)
    if fetch is None:
        return
    for msg_id in ids:
        if msg_id == getattr(message, "id", None):
            continue
        try:
            found = await fetch(msg_id)
        except Exception:
            found = None
        if found is not None:
            yield found


async def _download(kind: str, ref) -> tuple[bytes, str, str]:
    """(data, mime, reason). reason: "", "too_large" or "download"."""
    from core.chat_media import MAX_VIDEO_DOWNLOAD, fetch

    if kind in ("attachment", "audio"):
        size = int(getattr(ref, "size", 0) or 0)
        if size > MAX_VIDEO_DOWNLOAD:
            return b"", "", "too_large"
        from core.message_media import _content_type, _filename, _read_bytes

        data, err = await _read_bytes(ref)
        if err or not data:
            return b"", "", "download"
        if len(data) > MAX_VIDEO_DOWNLOAD:
            return b"", "", "too_large"
        mime = _content_type(ref) or ""
        if kind == "audio":
            if not mime.startswith("audio/"):
                name = (_filename(ref) or "").lower().split("?")[0]
                mime = next((m for suf, m in _AUDIO_MIME_BY_SUFFIX.items() if name.endswith(suf)), "audio/mpeg")
            return data, mime, ""
        if not mime.startswith("video/"):
            name = (_filename(ref) or "").lower()
            mime = "video/quicktime" if name.endswith(".mov") else "video/webm" if name.endswith(".webm") else "video/mp4"
        return data, mime, ""
    data, mime, err = await fetch(str(ref), MAX_VIDEO_DOWNLOAD)
    if err == "too-large":
        return b"", "", "too_large"
    if err or not data:
        return b"", "", "download"
    return data, mime or "video/mp4", ""


_REASON_LINES = {"too_large": TOO_LARGE, "download": NO_DOWNLOAD}


async def find_video(message, wanted: str = "", stats: dict | None = None) -> tuple[bytes, str, str, str]:
    """(data, mime, source, error line). source is attachment/embed for the log only.

    `stats` (optional) is filled with counts for the log: never urls, ids or text."""
    stats = stats if stats is not None else {}
    stats.update(hint="none", messages=0, videos=0, too_large=0, download=0, other_files=0, where="")
    ids = hint_ids(wanted)
    if ids:
        stats["hint"] = "unmatched"
    searched: set = set()

    async def search(candidate, where: str):
        key = getattr(candidate, "id", None)
        if key is not None and key in searched:
            return None
        if key is not None:
            searched.add(key)
        stats["messages"] += 1
        refs = _video_refs(candidate)
        stats["other_files"] += _other_files(candidate)
        if ids:
            if key in ids:
                stats["hint"] = "message"
            named = [r for r in refs if r[0] == "attachment" and getattr(r[1], "id", None) in ids]
            if named:
                stats["hint"] = "attachment"
                refs = named + [r for r in refs if r not in named]
        for kind, ref in refs:
            stats["videos"] += 1
            data, mime, reason = await _download(kind, ref)
            if data:
                stats["where"] = where
                return data, mime, kind
            stats[reason] = stats.get(reason, 0) + 1
        return None

    if ids:
        # An attachment id named by the hint on this message or its reply wins outright.
        async for where, candidate in _auto_candidates(message):
            if where == "history":
                break
            if any(getattr(item, "id", None) in ids for item in getattr(candidate, "attachments", None) or []):
                hit = await search(candidate, where)
                if hit:
                    return (*hit, "")
        async for candidate in _hinted_messages(message, ids):
            hit = await search(candidate, "hint")
            if hit:
                return (*hit, "")
    async for where, candidate in _auto_candidates(message):
        hit = await search(candidate, where)
        if hit:
            return (*hit, "")
    for reason in ("too_large", "download"):
        if stats.get(reason):
            return b"", "", "", _REASON_LINES[reason]
    return b"", "", "", NOT_A_VIDEO if stats["other_files"] else NO_VIDEO


def find_line(stats: dict) -> str:
    """One log line about the search. Counts and labels only."""
    keys = ("hint", "messages", "videos", "too_large", "download", "other_files", "where")
    return "find: " + " ".join(f"{k}={stats.get(k, '') or ('-' if k in ('where',) else stats.get(k, 0))}" for k in keys)


# --- describing ------------------------------------------------------------------------------

PROMPT = (
    "You are watching a video, given as {n} frames in order with their timestamps: {stamps}. "
    "Describe what happens over time: one short line per frame in the form "
    "\"[m:ss] what is visible or happening\", then one line starting \"Overall:\" summarising it. "
    "Say what you see, not what you guess. No preamble."
)


def render(duration: float, described: str, has_audio: bool, heard_line: str = "") -> str:
    """Length, what was seen, what was heard. The heard line is kept whole; the description is clipped."""
    head = [f"Video, {stamp(duration)} long."]
    if duration > MAX_SECONDS:
        head.append(f"Only the first {stamp(MAX_SECONDS)} was watched.")
    tail = heard_line or (
        "It has a sound track that was not transcribed." if has_audio else "It has no sound track."
    )
    room = RESULT_CHARS - len("\n".join(head)) - len(tail) - 2
    body = (described or "").strip()
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + "…"
    return "\n".join(head + [body, tail])


def vision_model() -> str:
    raw = (os.environ.get("ECHE_WATCH_VISION_MODEL") or "").strip()
    if raw.lower() in ("0", "off", "false", "no", "none"):
        return ""
    return raw[:100] or LOCAL_VISION_MODEL


def vision_order() -> list[str]:
    """Which models look at the frames, in order. Default: local, then the chat model."""
    raw = (os.environ.get("ECHE_WATCH_VISION") or "").strip().lower()
    if raw == "local":
        return ["local"]
    if raw == "chat":
        return ["chat"]
    return ["local", "chat"]


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def ollama_base() -> str:
    """Eche's Ollama URL (OLLAMA_API_URL) reduced to scheme://host:port, only when it is this PC.

    Anything else falls back to the local default: frames never leave the machine on this route."""
    from urllib.parse import urlsplit

    raw = (os.environ.get("OLLAMA_API_URL") or "").strip()
    if raw:
        try:
            parts = urlsplit(raw)
            if parts.scheme in ("http", "https") and (parts.hostname or "").lower() in _LOOPBACK:
                from core.local_queue import ip_literal

                # An IP literal: aiohttp then never resolves (no executor thread for DNS).
                host = ip_literal(parts.hostname or "")
                host = host if ":" not in host else f"[{host}]"
                return f"{parts.scheme}://{host}:{parts.port or 11434}"
        except ValueError:
            pass
    return OLLAMA_DEFAULT


async def _ollama_chat(base: str, body: dict, timeout: float) -> tuple[int, object]:
    """POST {base}/api/chat. (status, parsed json | text)."""
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(base + "/api/chat", json=body) as resp:
            if resp.status == 200:
                return resp.status, await resp.json(content_type=None)
            return resp.status, (await resp.text())[:300]


async def _unload_local(base: str, model: str) -> None:
    try:
        await _ollama_chat(base, {"model": model, "messages": [], "keep_alive": 0}, LOCAL_UNLOAD_TIMEOUT_S)
    except Exception:
        pass


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


async def describe_local(frames: list[tuple[float, bytes]], prompt: str) -> tuple[str, str]:
    """(text, reason). reason is "" on success, else off / missing / unreachable / busy / timeout /
    http_N / error / no_stamps. Never raises."""
    import base64

    model = vision_model()
    if not model:
        return "", "off"
    base = ollama_base()
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [base64.b64encode(blob).decode("ascii") for _t, blob in frames],
        }],
        "stream": False,
        "keep_alive": LOCAL_KEEP_ALIVE,
        "options": {"temperature": 0.2, "num_predict": DESCRIBE_TOKENS, "num_ctx": LOCAL_NUM_CTX},
    }
    from core import local_queue

    try:
        # Waits its turn behind other local model jobs (core/local_queue); the queue makes room on the card.
        status, data = await local_queue.run_local(
            base, model, lambda: _ollama_chat(base, body, LOCAL_VISION_TIMEOUT_S),
            label="watch", run_timeout=LOCAL_VISION_TIMEOUT_S + 5,
        )
    except local_queue.QueueBusy:
        return "", "busy"
    except (asyncio.TimeoutError, TimeoutError, local_queue.QueueTimeout):
        await _unload_local(base, model)
        return "", "timeout"
    except (ConnectionError, OSError):
        return "", "unreachable"
    except Exception as exc:
        name = type(exc).__name__
        return "", "unreachable" if "Connect" in name else "error"
    if status == 404 or (status != 200 and "not found" in str(data).lower()):
        return "", "missing"
    if status != 200:
        return "", f"http_{status}"
    message = (data or {}).get("message") if isinstance(data, dict) else None
    text = _THINK.sub("", str((message or {}).get("content") or "")).strip()
    if not _STAMP_LINE.search(text):
        return "", "no_stamps"
    return text, ""


async def _blocked(text: str) -> bool:
    """The chat output check, in its image-turn form (the strict one), for text no chat call checked."""
    from core import client as C

    token = C._request_images.set(True)
    try:
        return bool(await C._output_blocked(text))
    finally:
        C._request_images.reset(token)


async def describe_chat(frames: list[tuple[float, bytes]], prompt: str, ids: dict) -> str:
    """The frames through the chat model. "" when it can't see them."""
    from core.client import call_groq_turn, reset_precleared_images, set_precleared_images
    from core.message_media import image_part

    parts = [image_part(blob, "image/jpeg") for _t, blob in frames]
    token = set_precleared_images(parts)
    try:
        turn = await call_groq_turn(
            prompt,
            user_id=ids.get("user_id"),
            max_completion_tokens=DESCRIBE_TOKENS,
            tools=None,
            include_tool_note=False,
            images=parts,
            job="watch",
        )
    finally:
        reset_precleared_images(token)
    if turn.quota:
        raise TimeoutError("rate limited")
    text = (turn.reply or "").strip()
    from core.blocklist import REFUSAL

    if text == REFUSAL:
        return REFUSAL
    # A model that couldn't see the frames answers without timestamps.
    return text if _STAMP_LINE.search(text) else ""


async def describe(frames: list[tuple[float, bytes]], ids: dict, notes: list[str] | None = None) -> str:
    """The frames, described, by the first route in vision_order() that answers. "" when none can.

    The caller has already written the audit row. `notes` gets one short line per route tried."""
    notes = notes if notes is not None else []
    stamps = ", ".join(f"{i}. [{stamp(t)}]" for i, (t, _b) in enumerate(frames, 1))
    prompt = PROMPT.format(n=len(frames), stamps=stamps)
    from core.blocklist import REFUSAL

    for route in vision_order():
        if route == "local":
            text, reason = await describe_local(frames, prompt)
            if text:
                if await _blocked(text):
                    notes.append("vision: local blocked")
                    return REFUSAL
                notes.append(f"vision: local model={vision_model()}")
                return text
            notes.append(f"vision: local {reason}")
            dprint(f"[watch_video] local vision unavailable: {reason}")
            continue
        text = await describe_chat(frames, prompt, ids)
        notes.append("vision: chat" + ("" if text else " cannot_see"))
        if text:
            return text
    return ""


async def hear(video: bytes, mime: str, has_audio: bool):
    """core.watch_listen.Heard for this clip, off the event loop. Never raises."""
    from core import watch_listen as L

    if not has_audio:
        return L.Heard(L.NO_AUDIO)
    suffix = ".webm" if "webm" in (mime or "") else ".mov" if "quicktime" in (mime or "") else ".mp4"
    try:
        return await asyncio.to_thread(L.transcribe, video, suffix)
    except Exception as exc:
        return L.Heard(L.FAILED, note=type(exc).__name__)


async def watch(video: bytes, mime: str, ids: dict, notes: list[str] | None = None) -> str:
    from core import watch_listen as L
    from core.chat_media import record_viewed, sha256_hex

    notes = notes if notes is not None else []
    sha = sha256_hex(video)
    if sha in _seen:
        _seen.move_to_end(sha)
        notes.append("cache: hit")
        return _seen[sha]
    frames, duration, has_audio = await asyncio.to_thread(sample_frames, video, mime)
    # Audit row first, fail closed: nothing is looked at or listened to without it.
    items = [{"data": blob, "sha256": sha256_hex(blob)} for _t, blob in frames]
    if not record_viewed(ids, source="watch", items=items, kinds=["video"]):
        raise PermissionError("watch audit row could not be written")
    # GPU (vision) and CPU (Whisper) work side by side.
    described, heard = await asyncio.gather(describe(frames, ids, notes), hear(video, mime, has_audio))
    notes.append(L.log_line(heard))
    if not described:
        return ""
    from core.blocklist import REFUSAL

    if described == REFUSAL:
        return REFUSAL
    line = L.heard_line(heard, has_audio, stamp)
    text = render(duration, described, has_audio, line)
    if heard.state == L.HEARD and heard.segments and await _blocked(line):
        notes.append("audio: blocked")
        return REFUSAL
    if heard.state not in (L.LOADING,):  # a later watch should get the transcript
        _seen[sha] = text
        while len(_seen) > _CACHE_MAX:
            _seen.popitem(last=False)
    return text


def probe_bytes(data: bytes, suffix: str) -> tuple[float, bool, bool]:
    """(duration, has_video, has_audio) of a clip held in memory."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError(NO_FFMPEG)
    folder = tempfile.mkdtemp(prefix="eche-listen-")
    try:
        path = os.path.join(folder, "clip" + suffix)
        with open(path, "wb") as handle:
            handle.write(data)
        return probe(ffmpeg, path)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def listen_clip(data: bytes, mime: str, ids: dict, notes: list[str]) -> tuple[str, str]:
    """An audio clip: (text, status). status "" means text is the result for the model.

    Same caps (25 MB on download, the first ten minutes), the same audit row (kinds=audio) before
    anything is decoded for meaning, the same output check on the transcript. No frames, no vision."""
    from core import watch_listen as L
    from core.blocklist import REFUSAL
    from core.chat_media import record_viewed, sha256_hex

    sha = sha256_hex(data)
    if sha in _seen:
        _seen.move_to_end(sha)
        notes.append("cache: hit")
        return _seen[sha], ""
    suffix = _SUFFIX_BY_AUDIO_MIME.get((mime or "").split(";")[0].strip().lower(), ".audio")
    duration, _has_video, has_audio = await asyncio.to_thread(probe_bytes, data, suffix)
    if not has_audio:
        notes.append("audio: no_audio")
        return NO_SOUND, "no_audio"
    if not record_viewed(ids, source="watch", items=[{"data": data, "sha256": sha}], kinds=["audio"]):
        raise PermissionError("watch audit row could not be written")
    heard = await asyncio.to_thread(L.transcribe, data, suffix)
    notes.append(L.log_line(heard))
    failures = {
        L.NOT_INSTALLED: (NO_LISTEN, "no_whisper"),
        L.OFF: (LISTEN_OFF, "listen_off"),
        L.LOADING: (LISTEN_LOADING, "loading"),
        L.NO_AUDIO: (NO_SOUND, "no_audio"),
    }
    if heard.state in failures:
        return failures[heard.state]
    if heard.state != L.HEARD:
        return LISTEN_FAILED, "transcribe"
    line = L.heard_line(heard, True, stamp)
    if heard.segments and await _blocked(line):
        notes.append("audio: blocked")
        return REFUSAL, "blocked"
    head = f"Audio clip, {stamp(duration)} long." if duration > 0 else "Audio clip."
    if duration > L.AUDIO_SECONDS:
        head += f" Only the first {stamp(L.AUDIO_SECONDS)} was transcribed."
    text = f"{head}\n{line}"[:RESULT_CHARS]
    _seen[sha] = text
    while len(_seen) > _CACHE_MAX:
        _seen.popitem(last=False)
    return text, ""


def _ids(message) -> dict:
    return dict(
        user_id=getattr(getattr(message, "author", None), "id", None),
        guild_id=getattr(getattr(message, "guild", None), "id", None),
        channel_id=getattr(getattr(message, "channel", None), "id", None),
    )


def _said(text: str, status: str, outcome: str = "refusal") -> ToolResult:
    return ToolResult(text=text, fence=False, status=status, outcome=outcome, detail=f"status: {status}")


async def watch_video(ctx: ToolContext, arguments: dict) -> ToolResult:
    message = getattr(ctx, "message", None)
    if message is None:
        return _said(NO_VIDEO, "no_message", "error")
    if not find_ffmpeg():
        return _said(NO_FFMPEG, "no_ffmpeg", "error")
    if _lock.locked():
        return _said(BUSY, "busy")
    wanted = str((arguments or {}).get("message_id") or "").strip()[:200]
    async with _lock:
        try:
            stats: dict = {}
            video, mime, source, error = await find_video(message, wanted, stats)
            found = find_line(stats)
            dprint(f"[watch_video] {found}")
            if error:
                status = {
                    NO_VIDEO: "not_found",
                    NOT_A_VIDEO: "unsupported",
                    TOO_LARGE: "too_large",
                    NO_DOWNLOAD: "download",
                }.get(error, "not_found")
                result = _said(error, status)
                result.detail = f"status: {status}\n{found}"
                return result
            notes: list[str] = []
            if source == "audio":
                text, status = await asyncio.wait_for(
                    listen_clip(video, mime, _ids(message), notes), WATCH_TIMEOUT_S
                )
                if status:
                    outcome = "refusal" if status in ("blocked", "no_audio") else "error"
                    result = _said(text, status, outcome)
                    result.detail = f"status: {status}\nsource: audio\n{found}\n" + "\n".join(notes)
                    return result
            else:
                text = await asyncio.wait_for(watch(video, mime, _ids(message), notes), WATCH_TIMEOUT_S)
        except ValueError as exc:
            line = str(exc) if str(exc) in (NO_FFMPEG, NO_FRAMES) else FAILED
            return _said(line, "frames", "error")
        except PermissionError:
            return _said(NO_AUDIT, "audit", "error")
        except (asyncio.TimeoutError, TimeoutError):
            return _said(TIMED_OUT, "timeout", "error")
        except Exception as exc:
            dprint(f"[watch_video] failed: {type(exc).__name__}")
            return _said(FAILED, "error", "error")
    from core.blocklist import REFUSAL

    if text == REFUSAL:
        result = _said(REFUSAL, "blocked")
        result.detail = "status: blocked\n" + "\n".join(notes)
        return result
    if not text:
        result = _said(CANT_SEE, "no_vision")
        result.detail = "status: no_vision\n" + "\n".join(notes)
        return result
    from core.chat_media import sha256_hex

    route = "\n".join(notes)
    detail = f"source: {source}\n{found}\n{route}\nsha256: {sha256_hex(video)[:16]}\n\n{text}"
    return ToolResult(text=text, for_model=True, fence=False, detail=detail)


def register_watch_tool() -> None:
    register(
        Tool(
            name=TOOL_NAME,
            description=_DESCRIPTION,
            handler=watch_video,
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "Only when they point at a specific earlier message in this channel: "
                                       "its message link. Not an attachment url or id. Leave out when "
                                       "the video is on their message, the message they replied to, or "
                                       "is the newest one here.",
                    },
                },
                "required": [],
            },
            access=ACCESS_ANYONE,
            available=lambda: bool(find_ffmpeg()),
        )
    )


register_watch_tool()
