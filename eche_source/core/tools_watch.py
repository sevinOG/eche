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
# GPU: the frames go to the configured chat model (Ollama is shared). Bounded:
# one watch at a time, at most MAX_FRAMES small frames, ffmpeg timeouts, and a
# per-video cache so a follow-up question does not watch the clip again.
# Sound is not transcribed (Eche has no speech-to-text); the text says so.

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
WATCH_TIMEOUT_S = 180
HISTORY_LOOKBACK = 15     # earlier messages in this channel searched for a video
RESULT_CHARS = 1500       # core.client.lookup_prompt keeps 1500 characters of notes

NO_FFMPEG = "I can't watch videos here: ffmpeg isn't installed."
NO_VIDEO = (
    "I couldn't find a video to watch in this message, the message it replies to, "
    "or the last few messages here."
)
TOO_LARGE = "That video is too large for me to open (25 MB limit)."
NOT_A_VIDEO = (
    "I found an attachment, but it isn't a video file I can open (mp4, mov, webm, mkv)."
)
NO_DOWNLOAD = "I couldn't download that video."
NO_FRAMES = "I couldn't read any frames from that video."
CANT_SEE = "The current chat model can't see images, so I can't watch that video."
BUSY = "I'm already watching a video. Ask again in a moment."
FAILED = "I couldn't watch that video."

_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_STAMP_LINE = re.compile(r"\[\d+:\d{2}\]")

_lock = asyncio.Lock()
_CACHE_MAX = 32
_seen: "OrderedDict[str, str]" = OrderedDict()

_DESCRIPTION = (
    "Watch a video in this Discord chat (attached to this message, in the message it replies to, "
    "or posted in the last few messages) and get a timestamped description of what happens in it. "
    "Use it when they ask what a video shows or what happens in it."
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


def _is_video_attachment(item) -> bool:
    from core.message_media import _content_type, _filename, _is_video_item

    if _is_video_item(item):
        return True
    name = (_filename(item) or "").lower().split("?")[0]
    if name.endswith(_VIDEO_SUFFIXES):
        return True
    kind = (_content_type(item) or "").lower()
    return kind in ("application/mp4", "application/x-matroska")


def _video_refs(message) -> list[tuple[str, object]]:
    """[("attachment", item) | ("embed", url)] for the videos on one message, in order.

    Forwarded messages carry their files on message_snapshots."""
    from core.message_media import _embed_media

    sources = [message] + list(getattr(message, "message_snapshots", None) or [])
    refs: list[tuple[str, object]] = []
    for source in sources:
        for item in list(getattr(source, "attachments", None) or []):
            if _is_video_attachment(item):
                refs.append(("attachment", item))
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
        if not _is_video_attachment(item)
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

    if kind == "attachment":
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


def render(duration: float, described: str, has_audio: bool) -> str:
    lines = [f"Video, {stamp(duration)} long."]
    if duration > MAX_SECONDS:
        lines.append(f"Only the first {stamp(MAX_SECONDS)} was watched.")
    lines.append((described or "").strip())
    lines.append("It has a sound track that was not transcribed." if has_audio else "It has no sound track.")
    text = "\n".join(lines)
    if len(text) > RESULT_CHARS:
        text = text[: RESULT_CHARS - 1].rstrip() + "…"
    return text


async def describe(frames: list[tuple[float, bytes]], ids: dict) -> str:
    """The frames through the chat model. "" when it can't see them. Fail closed on the audit row."""
    from core.chat_media import record_viewed, sha256_hex
    from core.client import call_groq_turn, reset_precleared_images, set_precleared_images
    from core.message_media import image_part

    items = [{"data": blob, "sha256": sha256_hex(blob)} for _t, blob in frames]
    if not record_viewed(ids, source="watch", items=items, kinds=["video"]):
        raise PermissionError("watch audit row could not be written")
    parts = [image_part(blob, "image/jpeg") for _t, blob in frames]
    stamps = ", ".join(f"{i}. [{stamp(t)}]" for i, (t, _b) in enumerate(frames, 1))
    prompt = PROMPT.format(n=len(frames), stamps=stamps)
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


async def watch(video: bytes, mime: str, ids: dict) -> str:
    from core.chat_media import sha256_hex

    sha = sha256_hex(video)
    if sha in _seen:
        _seen.move_to_end(sha)
        return _seen[sha]
    frames, duration, has_audio = await asyncio.to_thread(sample_frames, video, mime)
    described = await describe(frames, ids)
    if not described:
        return ""
    from core.blocklist import REFUSAL

    if described == REFUSAL:
        return REFUSAL
    text = render(duration, described, has_audio)
    _seen[sha] = text
    while len(_seen) > _CACHE_MAX:
        _seen.popitem(last=False)
    return text


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
            text = await asyncio.wait_for(watch(video, mime, _ids(message)), WATCH_TIMEOUT_S)
        except ValueError as exc:
            line = str(exc) if str(exc) in (NO_FFMPEG, NO_FRAMES) else FAILED
            return _said(line, "frames", "error")
        except PermissionError:
            return _said(FAILED, "audit", "error")
        except (asyncio.TimeoutError, TimeoutError):
            return _said(FAILED, "timeout", "error")
        except Exception as exc:
            dprint(f"[watch_video] failed: {type(exc).__name__}")
            return _said(FAILED, "error", "error")
    from core.blocklist import REFUSAL

    if text == REFUSAL:
        return _said(REFUSAL, "blocked")
    if not text:
        return _said(CANT_SEE, "no_vision")
    from core.chat_media import sha256_hex

    detail = f"source: {source}\n{found}\nsha256: {sha256_hex(video)[:16]}\n\n{text}"
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
