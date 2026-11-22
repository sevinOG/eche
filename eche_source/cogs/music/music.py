# ============================================================
# ABSOLUTE TOP: FORCE FFMPEG BEFORE ANY DISCORD IMPORT
# discord.player does shutil.which + Popen at import time in some paths.
# This must run before "import discord" below.
# ============================================================
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

from core.paths import ensure_user_layout, find_ffmpeg

# A track that ends this soon after it started did not really play (live
# 2026-11-20 22:27/22:28 ET: googlevideo answered 403 and ffmpeg exited at once,
# while the audit row still said success).
EARLY_EXIT_S = 3.0


def playback_failure(
    error=None,
    *,
    exit_code=None,
    elapsed=None,
    stream_error: str = "",
    stopped: bool = False,
) -> str:
    """Why a finished track failed, or "" when it played normally.

    A user ?skip/?stop is never a failure. Otherwise any player error, an
    error from the yt-dlp stream, a non-zero ffmpeg exit, or a track that
    ended within EARLY_EXIT_S seconds counts.
    """
    if stopped:
        return ""
    if error:
        return f"{type(error).__name__}: {error}"[:500]
    if stream_error:
        return str(stream_error)[:500]
    if exit_code not in (None, 0):
        return f"ffmpeg exited with code {exit_code}"
    if elapsed is not None and elapsed < EARLY_EXIT_S:
        return f"playback ended after {elapsed:.1f}s"
    return ""


def audit_after_playback(
    ctx=None,
    error=None,
    *,
    exit_code=None,
    elapsed=None,
    stderr: str = "",
    stream_error: str = "",
    stopped: bool = False,
    stream_format: str = "",
    mode: str = "",
) -> str:
    """Audit hook for the voice client after_playback callback.

    Returns the failure reason ("" when the track played normally).
    """
    reason = playback_failure(
        error, exit_code=exit_code, elapsed=elapsed, stream_error=stream_error, stopped=stopped
    )
    try:
        from core.audit import safe_activity
        safe_activity(
            "music-after-playback",
            outcome="error" if reason else "success",
            error_type=type(error).__name__ if error else "",
            reason=reason,
            exit_code=exit_code,
            elapsed_s=round(float(elapsed), 1) if elapsed is not None else None,
            stderr=" | ".join(part for part in (str(stream_error or "")[-400:], str(stderr or "")[-800:]) if part)
            if reason else "",
            stopped=True if stopped else None,
            format=stream_format,
            mode=mode,
            user_id=getattr(getattr(ctx, "author", None), "id", None),
            guild_id=getattr(getattr(ctx, "guild", None), "id", None),
            channel_id=getattr(getattr(ctx, "channel", None), "id", None),
        )
    except Exception:
        pass
    return reason


class StderrTail:
    """Writable sink for ffmpeg's stderr: keeps the last few KB.

    It has no fileno(), so discord.py reads ffmpeg's stderr on a thread and
    writes it here instead of discarding it.
    """

    def __init__(self, limit: int = 4096):
        self.limit = limit
        self._buf = bytearray()

    def write(self, data) -> int:
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        self._buf.extend(data or b"")
        if len(self._buf) > self.limit:
            del self._buf[: len(self._buf) - self.limit]
        return len(data or b"")

    def flush(self) -> None:
        pass

    def text(self) -> str:
        return bytes(self._buf).decode("utf-8", "replace").strip()


_ffmpeg_path = find_ffmpeg()
if _ffmpeg_path:
    _ff_dir = os.path.dirname(_ffmpeg_path)
    if _ff_dir and os.path.isdir(_ff_dir):
        current = os.environ.get("PATH", "")
        if _ff_dir not in current:
            os.environ["PATH"] = _ff_dir + os.pathsep + current
    _ = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

import discord
from discord.ext import commands
import asyncio
import re
import shlex
import time
from cogs.music.music_queue_storage import load_queue, save_queue

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    import nacl
    import nacl.secret
    import nacl.public
    import discord.voice_client
    import discord.opus
    import discord.player
except ImportError:
    pass


PIPED_BASE = "https://piped.video"

# yt-dlp prints these on an age gate. With ignoreerrors they never became the
# chat message: the search entry was None and entries[0]["id"] raised TypeError.
_AGE_MARKERS = (
    "confirm your age",
    "age-restricted",
    "age restricted",
    "sign in to confirm",
    "inappropriate for some users",
    "age_verification",
    "age_check",
    "age-verification",
)


class TrackError(Exception):
    """Message safe to send in Discord. No traceback and no cookie contents."""


class _YtdlNotes:
    """Keep yt-dlp screen lines so an age-gate notice can be classified.

    Quiet mode drops those lines, and they are not part of the DownloadError.
    Lines that look like cookie material are discarded.
    """

    def __init__(self):
        self.lines = []

    def debug(self, msg):
        self._add(msg)

    info = warning = error = debug

    def _add(self, msg):
        if not msg:
            return
        text = " ".join(str(msg).split())
        if not text:
            return
        lowered = text.lower()
        if any(token in lowered for token in ("login_info", "sapisid", "__secure-", "hsid=", "ssid=")):
            return
        if len(text) > 300:
            text = text[:300]
        self.lines.append(text)
        if len(self.lines) > 30:
            del self.lines[:-30]

    def text(self) -> str:
        return "\n".join(self.lines)


def looks_age_restricted(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _AGE_MARKERS)


def age_restriction_message(has_cookies: bool) -> str:
    if has_cookies:
        return (
            "That video is age-restricted, and the saved cookies did not unlock it. "
            "Replace cookies/ytcookies.txt with a fresh export from a signed-in browser."
        )
    return (
        "That video is age-restricted. Save a signed-in YouTube export as "
        "cookies/ytcookies.txt. Settings can open that folder."
    )


def _info_age_locked(info) -> bool:
    """True when metadata says the video needs a sign-in and nothing can play."""
    if not isinstance(info, dict):
        return False
    if info.get("url"):
        return False
    formats = info.get("formats") or info.get("requested_formats") or []
    if any(isinstance(fmt, dict) and fmt.get("url") for fmt in formats):
        return False
    age = info.get("age_limit")
    try:
        if age is not None and int(age) >= 18:
            return True
    except (TypeError, ValueError):
        pass
    availability = str(info.get("availability") or "").lower()
    if availability in {"needs_auth", "premium_only", "needs_premium"}:
        return True
    reason = " ".join(
        str(info.get(key) or "")
        for key in ("playability_status", "reason", "availability")
    )
    return looks_age_restricted(reason)


def _best_failure_text(text: str) -> str:
    """Pick the line worth showing. Skip playlist chatter and cookie material."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if looks_age_restricted(line):
            return line
        if "nonetype" in lowered:
            return line
    for line in lines:
        lowered = line.lower()
        if (
            lowered.startswith("error:")
            or "requested format" in lowered
            or "no video formats" in lowered
            or "private video" in lowered
            or "video unavailable" in lowered
        ):
            return line
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("[download]") or lowered.startswith("[info]"):
            continue
        return line
    return ""


def _clean_failure(text: str) -> str:
    cleaned = " ".join((text or "").split())
    cleaned = re.sub(r"^(?:ERROR:\s*)+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*Use --list-formats\b.*$", "", cleaned, flags=re.IGNORECASE).strip()
    lowered = cleaned.lower()
    if any(token in lowered for token in ("login_info", "sapisid", "__secure-", "cookiefile")):
        return (
            "The YouTube cookie file could not be read. "
            "Replace cookies/ytcookies.txt with a fresh export from a signed-in browser."
        )
    if not cleaned or "nonetype" in lowered:
        return "Could not get that track."
    if len(cleaned) > 160:
        cleaned = cleaned[:157].rstrip() + "..."
    return f"Could not get that track: {cleaned}"


def _empty_search(text: str) -> bool:
    useful = _best_failure_text(text)
    lowered = (useful or text or "").lower()
    return any(
        phrase in lowered
        for phrase in ("no video results", "did not match any", "no entries")
    )


def track_failure_message(
    text: str = "",
    *,
    has_cookies: bool,
    missing_entry: bool = False,
    info=None,
) -> str | None:
    """Discord line for a failed extract. None means the search was simply empty."""
    blob = text or ""
    if looks_age_restricted(blob) or _info_age_locked(info):
        return age_restriction_message(has_cookies)
    if _empty_search(blob):
        return None
    useful = _best_failure_text(blob)
    none_type = "nonetype" in blob.lower()
    if useful and not none_type:
        return _clean_failure(useful)
    if (none_type or missing_entry) and not has_cookies:
        return age_restriction_message(False)
    if none_type or missing_entry:
        return "Could not get that track."
    return None


def _first_search_entry(info):
    """Return (entry, missing). A None entry is a swallowed extract, not an empty search."""
    if not isinstance(info, dict):
        return None, False
    entries = info.get("entries")
    if not entries:
        return None, False
    try:
        entry = entries[0]
    except Exception:
        return None, True
    if not isinstance(entry, dict):
        return None, True
    return entry, False


def youtube_cookie_file() -> str | None:
    """Netscape cookie file, if one is actually on disk.

    The layout folder is checked first. A build of Eche.exe that lives under
    the source tree still stores its data in the source tree, so a file placed
    only beside that exe is accepted too.
    """
    candidates = [
        os.path.join(ensure_user_layout(), "cookies", "ytcookies.txt"),
    ]
    exe = os.path.abspath(sys.executable or "")
    if os.path.basename(exe).lower().startswith("eche"):
        candidates.append(os.path.join(os.path.dirname(exe), "cookies", "ytcookies.txt"))
    for path in candidates:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def _node_binary() -> str | None:
    """Node binary for yt-dlp's player challenge. The GUI PATH can miss it."""
    found = shutil.which("node") or shutil.which("node.exe")
    if found and os.path.isfile(found):
        return found
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs", "node.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "nodejs", "node.exe"),
        os.path.join(local, "Programs", "node", "node.exe") if local else "",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


# YouTube player clients (yt-dlp 2026.10.26). Only clients whose https audio
# needs no PO token are used: tv, tv_downgraded, tv_samsung, visionos and
# web_embedded (PO Token Guide + INNERTUBE_CLIENTS). web_safari is last: its
# https formats need a PO token, but its HLS (m3u8) audio does not, so it is
# the HLS fallback. Avoided (GVS PO token for https): web, mweb, ios, android,
# android_vr, tv_simply, web_music, web_creator. With cookies yt-dlp skips
# clients that can't take them (visionos, android_vr), so the cookie lists
# lead with the tv clients, which need cookies to avoid DRM-only formats.
PLAYER_CLIENTS_COOKIES = ("tv", "tv_samsung", "web_embedded", "web_safari")
PLAYER_CLIENTS_NO_COOKIES = ("visionos", "tv_samsung", "web_safari")
RETRY_PLAYER_CLIENTS_COOKIES = ("tv_downgraded", "web_embedded", "tv_samsung", "web_safari")
RETRY_PLAYER_CLIENTS_NO_COOKIES = ("tv_samsung", "web_embedded", "visionos", "web_safari")
# Kept for callers/tests of the PR #28 name: the logged-out retry list.
RETRY_PLAYER_CLIENTS = RETRY_PLAYER_CLIENTS_NO_COOKIES
PO_TOKEN_CLIENTS = frozenset({"web", "mweb", "ios", "android", "android_vr", "tv_simply",
                              "web_music", "web_creator"})


def player_clients(*, cookies: bool, retry: bool = False) -> tuple:
    if retry:
        return RETRY_PLAYER_CLIENTS_COOKIES if cookies else RETRY_PLAYER_CLIENTS_NO_COOKIES
    return PLAYER_CLIENTS_COOKIES if cookies else PLAYER_CLIENTS_NO_COOKIES


# Progressive (single-file http/https) audio first, so it can be piped. Only
# when a video has nothing progressive does yt-dlp pick HLS, and then ffmpeg
# opens the m3u8 itself (live 2026-11-21: piping a playlist into ffmpeg's
# stdin failed with "Not detecting m3u8/hls ... Invalid data").
PROGRESSIVE_FORMAT = "bestaudio[protocol^=http][protocol!*=m3u8][protocol!*=dash]/bestaudio[protocol=https]"
FORMAT_SELECTOR = PROGRESSIVE_FORMAT + "/bestaudio/best[acodec!=none]"


def ydl_options(*, use_cookies: bool = False, player_client=None) -> dict:
    opts = {
        "format": FORMAT_SELECTOR,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
        # False so a failed video raises DownloadError instead of a None search
        # entry. Age-gate wording still arrives on the logger, not that exception.
        "ignoreerrors": False,
        "geo_bypass": True,
        "nocheckcertificate": True,
        # IPv4 only. The stream URL is signed for the IP that extracted it;
        # on 5G/CGNAT an IPv6 or second path for the audio fetch gets 403.
        "source_address": "0.0.0.0",
    }
    node = _node_binary()
    if node:
        # Deno is not installed. A signed-in request has no playable formats
        # until this runtime solves the player challenge.
        opts["js_runtimes"] = {"node": {"path": node}}
        opts["remote_components"] = ["ejs:github"]
    # Cookies without that solution leave only storyboard images, and
    # bestaudio then raises "Requested format is not available" for ordinary
    # songs, so cookies are only used together with node.
    if use_cookies and node:
        cookie = youtube_cookie_file()
        if cookie:
            opts["cookiefile"] = cookie
    if player_client:
        opts["extractor_args"] = {"youtube": {"player_client": list(player_client)}}
    return opts


@contextmanager
def youtube_dl(*, use_cookies: bool = False, player_client=None):
    """Open yt-dlp without letting it rewrite the saved cookie file."""
    if yt_dlp is None:
        raise TrackError(
            "Music needs `yt-dlp` in this portable build. "
            "Rebuild with `pip install -r requirements.txt` then `install.bat`."
        )
    opts = ydl_options(use_cookies=use_cookies, player_client=player_client)
    notes = _YtdlNotes()
    opts["logger"] = notes
    temp_cookie = None
    cookie = opts.get("cookiefile")
    if cookie:
        fd, temp_cookie = tempfile.mkstemp(prefix="eche-yt-", suffix=".txt")
        os.close(fd)
        shutil.copyfile(cookie, temp_cookie)
        opts["cookiefile"] = temp_cookie
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl._eche_notes = notes
            yield ydl
    finally:
        if temp_cookie and os.path.isfile(temp_cookie):
            os.remove(temp_cookie)


def _playable_info(info):
    """Video dict that carries a stream URL, whether it is a search entry or not."""
    if not isinstance(info, dict):
        return None
    entries = info.get("entries")
    if entries:
        try:
            entry = entries[0]
        except Exception:
            return None
        info = entry if isinstance(entry, dict) else None
    if not isinstance(info, dict):
        return None
    if info.get("url"):
        return info
    formats = info.get("formats") or info.get("requested_formats") or []
    if any(isinstance(fmt, dict) and fmt.get("url") for fmt in formats):
        return info
    return None


def _extract_once(url, *, use_cookies: bool, player_client=None):
    notes = None
    try:
        with youtube_dl(use_cookies=use_cookies, player_client=player_client) as ydl:
            notes = ydl._eche_notes
            return ydl.extract_info(url, download=False), notes, None
    except TrackError:
        raise
    except Exception as exc:
        return None, notes, exc


def _mark_cookies(info, used: bool):
    if isinstance(info, dict):
        info["_eche_cookies"] = bool(used)
    return info


def extract_youtube(url, *, retry: bool = False):
    """Extract one video for playback.

    Cookies (cookies/ytcookies.txt, with node for the player challenge) are
    used on the first attempt when present (live 2026-11-21: every logged-out
    first attempt got 403). If that pass can't play, the other mode is tried
    once (logged-out after cookies, so a stale cookie file can't break music).
    retry=True is the second try after a refused stream: the same order with
    a different set of player clients.
    Returns (info, notes, error). error is None when a stream URL was found.
    The info dict carries _eche_cookies so the audio fetch uses the same
    session settings.
    """
    use = bool(youtube_cookie_file() and _node_binary())
    info, notes, exc = _extract_once(url, use_cookies=use, player_client=player_clients(cookies=use, retry=retry))
    playable = _playable_info(info) if exc is None else None
    if playable and not _info_age_locked(playable):
        return _mark_cookies(info, use), notes, None

    blob = f"{notes.text() if notes else ''}\n{exc or ''}"
    if not use or _empty_search(blob):
        return info, notes, exc

    info2, notes2, exc2 = _extract_once(url, use_cookies=False,
                                        player_client=player_clients(cookies=False, retry=retry))
    playable2 = _playable_info(info2) if exc2 is None else None
    if playable2 and not _info_age_locked(playable2):
        return _mark_cookies(info2, False), notes2, None
    if playable:
        return _mark_cookies(info, use), notes, None

    merged = _YtdlNotes()
    if notes is not None:
        merged.lines.extend(notes.lines)
    if notes2 is not None:
        merged.lines.extend(notes2.lines)
    return info2 if info2 is not None else info, merged, exc2 if exc2 is not None else exc

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID_RE = re.compile(r"^(?:PL|UU|FL|RD|OL)[A-Za-z0-9_-]{10,}$")
_PLAYLIST_URL_RE = re.compile(r"(?:[?&]list=|/playlist\?)", re.IGNORECASE)
PLAYLIST_REFUSAL = (
    "That's a playlist. Send a single video link, a video ID, or search terms."
)


def is_playlist_query(query: str) -> bool:
    """True for a playlist URL or a bare playlist id, not a single video."""
    raw = (query or "").strip()
    if not raw:
        return False
    if _VIDEO_ID_RE.fullmatch(raw):
        return False
    if _PLAYLIST_URL_RE.search(raw):
        return True
    return bool(_PLAYLIST_ID_RE.fullmatch(raw))


def bare_video_id(query: str) -> str | None:
    """An 11-character video id, either alone or in a watch URL."""
    raw = (query or "").strip()
    if _VIDEO_ID_RE.fullmatch(raw):
        return raw
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", raw)
    if match:
        return match.group(1)
    return None


def track_label(info: dict | None, fallback: str = "") -> str:
    if not isinstance(info, dict):
        return fallback
    title = str(info.get("title") or "").strip()
    artist = str(info.get("uploader") or info.get("artist") or "").strip()
    if title and artist and artist.lower() not in ("unknown", "unknown title"):
        return f"{artist} | {title}"
    return title or fallback


def playback_decision(vc_playing: bool, vc_paused: bool, has_current: bool, has_queue: bool) -> str:
    """What ?play with no query should do: already, resume, start, or empty."""
    if vc_playing:
        return "already"
    if vc_paused and has_current:
        return "resume"
    if has_current or has_queue:
        return "start"
    return "empty"


FFMPEG_OPTIONS = {
    "before_options": (
        "-nostdin "
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5 "
        "-reconnect_at_eof 1"
    ),
    "options": "-vn"
}
# yt-dlp fetches the audio in this process and pipes it to ffmpeg's stdin, so
# the fetch uses yt-dlp's own session, headers, cookies and IPv4 address.
PIPE_FFMPEG_OPTIONS = {"before_options": "", "options": "-vn"}


def direct_ffmpeg_options(info: dict | None) -> dict:
    """ffmpeg options for opening the URL itself (fallback when the pipe can't
    be set up): yt-dlp's request headers and user agent go along."""
    headers = dict((info or {}).get("http_headers") or {})
    agent = str(headers.pop("User-Agent", "") or "")
    before = FFMPEG_OPTIONS["before_options"]
    if agent:
        before += " -user_agent " + shlex.quote(agent)
    if headers:
        joined = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k and v is not None)
        before += " -headers " + shlex.quote(joined)
    return {"before_options": before, "options": FFMPEG_OPTIONS["options"]}


class StreamError(Exception):
    """The audio stream could not be fetched (for example HTTP 403)."""


class YtdlStream:
    """File-like audio stream fetched through yt-dlp for ffmpeg's stdin.

    Ranged requests of CHUNK bytes, like yt-dlp's own YouTube downloader.
    Any fetch error ends the stream and is kept in .error for the log.
    `opener(url, headers)` returns a response with read()/close(); by default
    it is yt-dlp's urlopen with the same options as the extraction.
    """

    CHUNK = 10 * 1024 * 1024

    def __init__(self, url: str, headers: dict | None = None, *, use_cookies: bool = False,
                 filesize: int | None = None, opener=None, chunk: int | None = None):
        self.url = url
        self.headers = dict(headers or {})
        self.use_cookies = use_cookies
        self.filesize = int(filesize) if filesize else None
        self.chunk = int(chunk or self.CHUNK)
        self.error = ""
        self.bytes_read = 0
        self._opener = opener
        self._stack = None
        self._resp = None
        self._chunk_left = 0
        self._chunk_end = None
        self._done = False
        # Mid-stream transport errors (live 21:34 ET: ConnectionResetError
        # 10054) reopen the range at the current byte, at most this often.
        self.resumes = 0

    # -- opening -------------------------------------------------------
    def _default_opener(self):
        from contextlib import ExitStack
        from yt_dlp.networking import Request

        if self._stack is None:
            self._stack = ExitStack()
            self._ydl = self._stack.enter_context(youtube_dl(use_cookies=self.use_cookies))

        def open_url(url, headers):
            return self._ydl.urlopen(Request(url, headers=headers))
        return open_url

    MAX_RESUMES = 2

    def _open_next(self, resume_end: int | None = None) -> bool:
        if self.filesize is not None and self.bytes_read >= self.filesize:
            return False
        start = self.bytes_read
        end = resume_end if resume_end is not None else start + self.chunk - 1
        if self.filesize is not None:
            end = min(end, self.filesize - 1)
        if end < start:
            return False
        headers = {**self.headers, "Range": f"bytes={start}-{end}"}
        opener = self._opener or self._default_opener()
        try:
            self._resp = opener(self.url, headers)
        except Exception as exc:
            status = getattr(exc, "status", None) or getattr(getattr(exc, "response", None), "status", None)
            if status == 416 and start > 0:
                return False  # asked past the end: done
            raise StreamError(_stream_error_text(exc, status)) from exc
        self._chunk_left = end - start + 1
        self._chunk_end = end
        if getattr(self._resp, "status", 206) == 200:
            # The server ignored Range and sent the whole file: read to EOF.
            self._chunk_left = 1 << 62
            self._chunk_end = None
        return True

    def _resume(self, exc) -> bool:
        """Reopen the current range at the byte we reached, once or twice."""
        if self.resumes >= self.MAX_RESUMES or self.bytes_read <= 0 or self._chunk_end is None:
            return False
        self.resumes += 1
        end = self._chunk_end
        self._close_resp()
        try:
            return self._open_next(resume_end=end)
        except StreamError as err:
            self.error = f"{_stream_error_text(exc, getattr(exc, 'status', None))}; resume failed: {err}"
            return False

    def prime(self) -> "YtdlStream":
        """Open the first chunk now, so a refused stream fails before ffmpeg starts."""
        if self._resp is None and not self._done:
            if not self._open_next():
                self._done = True
        return self

    # -- reading -------------------------------------------------------
    def read(self, size: int = -1) -> bytes:
        while not self._done:
            try:
                if self._resp is None and not self._open_next():
                    self._done = True
                    break
                want = size if size and size > 0 else 65536
                data = self._resp.read(min(want, self._chunk_left) if self._chunk_left > 0 else want)
            except StreamError as exc:
                self.error = str(exc)
                self._done = True
                break
            except Exception as exc:
                if self._resume(exc):
                    continue
                if not self.error:
                    self.error = _stream_error_text(exc, getattr(exc, "status", None))
                self._done = True
                break
            if data:
                self.bytes_read += len(data)
                self._chunk_left -= len(data)
                if self._chunk_left <= 0:
                    self._close_resp()
                return data
            # This range is finished. A short range means the file ended.
            short = self._chunk_left > 0
            self._close_resp()
            if short:
                self._done = True
        return b""

    def _close_resp(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def close(self) -> None:
        self._done = True
        self._close_resp()
        stack, self._stack = self._stack, None
        if stack is not None:
            try:
                stack.close()
            except Exception:
                pass


def _stream_error_text(exc, status=None) -> str:
    if status:
        reason = getattr(exc, "reason", None) or getattr(getattr(exc, "response", None), "reason", None) or ""
        return f"HTTP {status} {reason}".strip()
    return f"{type(exc).__name__}: {exc}"[:300]


def open_stream(info: dict, audio_url: str, *, opener=None) -> YtdlStream:
    """A primed YtdlStream for this track. Raises StreamError when refused."""
    info = info or {}
    filesize = info.get("filesize") or None
    chunk = ((info.get("downloader_options") or {}).get("http_chunk_size")) or None
    stream = YtdlStream(
        audio_url,
        info.get("http_headers") or {},
        use_cookies=bool(info.get("_eche_cookies")),
        filesize=filesize,
        opener=opener,
        chunk=chunk,
    )
    try:
        return stream.prime()
    except Exception:
        stream.close()
        raise


def _format_client(fmt: dict) -> str:
    match = re.search(r"[?&]c=([A-Za-z0-9_]+)", str((fmt or {}).get("url") or ""))
    return match.group(1) if match else ""


def format_label(fmt: dict) -> str:
    """'251/https/TVHTML5' for logs."""
    fmt = fmt or {}
    parts = [str(fmt.get("format_id") or "?"), str(fmt.get("protocol") or "?")]
    client = _format_client(fmt)
    if client:
        parts.append(client)
    return "/".join(parts)


def is_hls_format(fmt: dict) -> bool:
    proto = str((fmt or {}).get("protocol") or "").lower()
    return "m3u8" in proto


def is_progressive_format(fmt: dict) -> bool:
    """A single http(s) file: safe to pipe. Not HLS, not DASH, not storyboards."""
    fmt = fmt or {}
    proto = str(fmt.get("protocol") or "").lower()
    if not fmt.get("url") or not proto.startswith("http"):
        return False
    if "m3u8" in proto or "dash" in proto:
        return False
    if str(fmt.get("acodec") or "") == "none":
        return False
    return True


def _has_audio(fmt: dict) -> bool:
    return str((fmt or {}).get("acodec") or "") != "none"


def _audio_only(fmt: dict) -> bool:
    return _has_audio(fmt) and str((fmt or {}).get("vcodec") or "none") == "none"


def _rate(fmt: dict) -> float:
    for key in ("abr", "tbr"):
        try:
            if fmt.get(key):
                return float(fmt[key])
        except Exception:
            pass
    return 0.0


MAX_PIPE_TRIES = 4
# Innertube client names (the c= URL parameter) whose https files need a PO token.
_PO_TOKEN_C = frozenset({"WEB", "MWEB", "IOS", "ANDROID", "ANDROID_VR", "TVHTML5_SIMPLY",
                         "WEB_REMIX", "WEB_CREATOR"})


def _po_token_format(fmt: dict) -> bool:
    return _format_client(fmt).upper() in _PO_TOKEN_C


def stream_candidates(info: dict) -> tuple[list, list]:
    """(progressive, hls) formats to try, best first.

    yt-dlp's chosen format leads its group. Progressive: audio-only by bitrate,
    then muxed files (format 18). HLS: audio-only, then muxed, by bitrate.
    Formats from PO-token clients are left out.
    """
    info = info or {}
    chosen = dict(info) if info.get("url") else None
    formats = [f for f in (info.get("formats") or []) if isinstance(f, dict) and f.get("url")]

    progressive, hls, seen = [], [], set()

    def add(group, fmt):
        url = fmt.get("url")
        if url in seen:
            return
        seen.add(url)
        group.append(fmt)

    later = []  # progressive files from PO-token clients: likely 403, tried last
    if chosen is not None:
        if is_progressive_format(chosen):
            add(later if _po_token_format(chosen) else progressive, chosen)
        elif is_hls_format(chosen):
            add(hls, chosen)
    prog = [f for f in formats if is_progressive_format(f)]
    ordered = sorted((f for f in prog if _audio_only(f)), key=_rate, reverse=True) + \
        sorted((f for f in prog if not _audio_only(f)), key=_rate, reverse=True)
    for fmt in ordered:
        add(later if _po_token_format(fmt) else progressive, fmt)
    progressive.extend(later)
    hl = [f for f in formats if is_hls_format(f) and _has_audio(f)]
    for fmt in sorted((f for f in hl if _audio_only(f)), key=_rate, reverse=True):
        add(hls, fmt)
    for fmt in sorted((f for f in hl if not _audio_only(f)), key=_rate):
        add(hls, fmt)  # muxed HLS: lowest video first, the audio is what matters
    return progressive, hls


def open_track(info: dict, *, opener=None):
    """How to play this track: ("pipe", YtdlStream, fmt) or ("hls", None, fmt).

    Progressive formats are tried in order (each try is one ranged request);
    the first that answers is piped. When none answers, or there are none,
    an HLS format goes straight to ffmpeg. Raises StreamError when nothing
    is playable; the message lists what was tried.
    """
    info = info or {}
    progressive, hls = stream_candidates(info)
    tried = []
    last = ""
    for fmt in progressive[:MAX_PIPE_TRIES]:
        merged = {**fmt, "_eche_cookies": info.get("_eche_cookies")}
        if not merged.get("http_headers"):
            merged["http_headers"] = info.get("http_headers") or {}
        try:
            return "pipe", open_stream(merged, fmt["url"], opener=opener), fmt
        except StreamError as err:
            last = str(err)
            tried.append(f"{format_label(fmt)}: {err}")
    if hls:
        return "hls", None, hls[0]
    if not tried:
        raise StreamError("no playable audio format")
    raise StreamError(f"{last} (tried {'; '.join(tried)})"[:500])


def hls_ffmpeg_options(fmt: dict | None) -> dict:
    """ffmpeg opens an HLS (m3u8) playlist itself with its hls demuxer and
    yt-dlp's request headers. Forcing -f hls skips ffmpeg's extension sniff
    ("Not detecting m3u8/hls with non standard extension")."""
    headers = dict((fmt or {}).get("http_headers") or {})
    agent = str(headers.pop("User-Agent", "") or "")
    before = "-nostdin"
    if agent:
        before += " -user_agent " + shlex.quote(agent)
    if headers:
        joined = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k and v is not None)
        before += " -headers " + shlex.quote(joined)
    # A fresh googlevideo playlist answers its first segment requests with
    # 403 now and then; retrying a segment recovers (box test 2026-11-21:
    # 8 segment 403s, 0 skipped with retries; 13 skipped without).
    before += " -f hls -seg_max_retry 10"
    return {"before_options": before, "options": "-vn"}


def refused_message(title: str, reason: str) -> str:
    label = f"**{title}**" if title else "that track"
    if "403" in (reason or ""):
        return (f"\u274c YouTube refused the audio stream for {label} (HTTP 403 Forbidden), "
                "even after a second try. Skipping.")
    return f"\u274c Couldn't play {label}: {reason or 'playback stopped right away'}. Skipping."


def _ffmpeg_exit_code(source) -> int | None:
    """ffmpeg's exit code when it has exited on its own; None while running."""
    proc = getattr(source, "_process", None)
    poll = getattr(proc, "poll", None)
    if poll is None:
        return None
    try:
        code = poll()
        if code is None:
            wait = getattr(proc, "wait", None)
            if wait is not None:
                code = wait(timeout=1)
        return code
    except Exception:
        return None


def _lookup_video_info(query: str):
    """Metadata for a single video id or URL. None when it cannot be resolved."""
    if is_playlist_query(query):
        return None
    video_id = bare_video_id(query)
    url = f"https://www.youtube.com/watch?v={video_id}" if video_id else query
    try:
        info, _notes, exc = extract_youtube(url)
    except TrackError:
        return None
    if exc is not None:
        return None
    return _playable_info(info)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.current = None
        self.vc = None
        self.playing = False
        self.queue_loaded = False
        self._load_error = None
        self._queue_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._starting = False
        # Set by ?skip / ?stop so the ended track is not counted as a failure.
        self._user_stopped = False

    # ---------------------------------------------------------
    # ALWAYS build a valid search query
    # ---------------------------------------------------------
    def build_query(self, entry):
        # New entries always have a query
        if entry.get("url"):
            return entry["url"]

        # Old queue entries: rebuild query from metadata
        artist = entry.get("artist") or ""
        title = entry.get("title") or ""
        query = f"{artist} {title}".strip()

        return query if query else None

    # ---------------------------------------------------------
    # Extract YouTube video ID safely
    # ---------------------------------------------------------
    def extract_video_id(self, query):
        if not query or not isinstance(query, str):
            return None

        video_id = bare_video_id(query)
        if video_id:
            return video_id

        if yt_dlp is None:
            return None

        # Search via yt-dlp. A None entry used to raise TypeError here.
        # Logged-out first: signed-in cookies without a solved challenge make
        # bestaudio fail for ordinary songs.
        try:
            info, notes, exc = extract_youtube(f"ytsearch1:{query}")
        except TrackError:
            raise
        if exc is not None:
            print("MUSIC EXTRACT:", _clean_failure(str(exc)))
            self._raise_track_failure(notes, exc)
            return None

        entry, missing = _first_search_entry(info)
        if missing or (isinstance(entry, dict) and not entry.get("id")):
            self._raise_track_failure(notes, missing_entry=True, info=entry)
            return None
        if not entry:
            return None
        return entry.get("id")

    # ---------------------------------------------------------
    # Build Piped URL
    # ---------------------------------------------------------
    def build_piped_url(self, video_id):
        return f"{PIPED_BASE}/watch?v={video_id}"

    # ---------------------------------------------------------
    # Extract REAL audio URL from Piped
    # ---------------------------------------------------------
    def extract_audio_url(self, query, retry: bool = False):
        if yt_dlp is None:
            return None, None

        video_id = self.extract_video_id(query)
        if not video_id:
            return None, None

        piped_url = self.build_piped_url(video_id)

        try:
            info, notes, exc = extract_youtube(piped_url, retry=retry)
        except TrackError:
            raise
        if exc is not None:
            print("MUSIC EXTRACT:", _clean_failure(str(exc)))
            self._raise_track_failure(notes, exc)
            return None, None

        if not info:
            self._raise_track_failure(notes, missing_entry=True)
            return None, None
        if _info_age_locked(info) or (
            looks_age_restricted(notes.text() if notes else "") and not info.get("url")
        ):
            self._raise_track_failure(notes, info=info)
            return None, None
        return info, info.get("url")

    def _raise_track_failure(self, notes, exc=None, *, missing_entry=False, info=None):
        parts = []
        if notes is not None:
            parts.append(notes.text())
        if exc is not None:
            parts.append(str(exc))
        message = track_failure_message(
            "\n".join(parts),
            has_cookies=youtube_cookie_file() is not None,
            missing_entry=missing_entry,
            info=info,
        )
        if message:
            raise TrackError(message)

    # ---------------------------------------------------------
    # Ensure VC
    # ---------------------------------------------------------
    async def ensure_vc(self, ctx):
        if ctx.author.voice is None:
            await ctx.send("You need to be in a voice channel.")
            return False

        # One join at a time: a second ?play waits for the first join instead of
        # starting another handshake. connect_voice bounds the wait, retries once
        # after dropping a half-open client, logs the missing handshake stage,
        # and tells the channel when Discord never finishes the join.
        from core.voice_connect import connect_voice

        async with self._connect_lock:
            if self.vc is not None and self.vc.is_connected():
                return True
            vc = await connect_voice(ctx)
            if vc is None:
                self.vc = None
                return False
            self.vc = vc
        return True

    # ---------------------------------------------------------
    # Load queue once
    # ---------------------------------------------------------
    async def _load_queue_once(self):
        # The pin lives on the home server. Voice still plays where the command ran.
        if self.queue_loaded:
            return
        self._load_error = None
        try:
            self.queue = await load_queue(self.bot)
        except Exception as exc:
            self.queue = []
            self._load_error = exc
        self.queue_loaded = True

    async def ensure_queue_loaded(self, ctx):
        async with self._queue_lock:
            await self._load_queue_once()
            err = self._load_error
        if err:
            await ctx.send(f"⚠️ Queue error: {err}")

    async def update_queue_message(self, ctx):
        try:
            await save_queue(self.bot, self.queue, self.current)
        except Exception as e:
            await ctx.send(f"⚠️ Failed to save queue: {e}")

    # ---------------------------------------------------------
    # Format duration
    # ---------------------------------------------------------
    def format_duration(self, seconds):
        if not seconds:
            return "0:00"
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"

    async def send_now_playing(self, ctx, entry):
        duration = self.format_duration(entry.get("duration"))
        artist = entry.get("artist", "Unknown")
        title = entry.get("title", "Unknown Title")

        await ctx.send(
            f"🎶 **Now Playing:**\n"
            f"**{artist} | {title}**\n"
            f"⏱️ `{duration}`"
        )

    async def _resolve_ffmpeg(self, ctx):
        """Bundled or system ffmpeg, otherwise the one-time standalone download."""
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            return ffmpeg_path
        await ctx.send(
            "Downloading FFmpeg once (about 80 MB) so ?play works on this PC..."
        )
        from core.ffmpeg_fetch import ensure_ffmpeg, last_error

        ffmpeg_path = await asyncio.to_thread(ensure_ffmpeg)
        if ffmpeg_path:
            return ffmpeg_path
        detail = last_error() or (
            "Install ffmpeg from https://ffmpeg.org and restart Eche, "
            "or place ffmpeg.exe at C:\\ffmpeg\\bin\\ffmpeg.exe."
        )
        await ctx.send(f"❌ FFmpeg playback error: ffmpeg was not found.\n{detail}")
        return None

    # ---------------------------------------------------------
    # PLAY NEXT — fully hardened
    # ---------------------------------------------------------
    async def play_next(self, ctx):
        async with self._queue_lock:
            # An early playback callback must not pop the next song or clear the pin
            # while this start is still resolving the current one.
            if self._starting:
                return
            voice = self.vc
            if voice is not None and getattr(voice, "is_playing", lambda: False)():
                return
            if not self.queue:
                self.playing = False
                self.current = None
                await self.update_queue_message(ctx)
                return
            self._starting = True
            self.playing = True
            self.current = self.queue.pop(0)
            await self.update_queue_message(ctx)

        try:
            await self._start_current(ctx)
        finally:
            self._starting = False

    async def _start_current(self, ctx):
        ffmpeg_path = await self._resolve_ffmpeg(ctx)
        if not ffmpeg_path:
            async with self._queue_lock:
                if self.current is not None:
                    self.queue.insert(0, self.current)
                self.current = None
                self.playing = False
                await self.update_queue_message(ctx)
            return

        current = self.current
        if current is None:
            return
        query = self.build_query(current)
        if not query:
            await ctx.send("❌ Invalid queue entry. Skipping.")
            async with self._queue_lock:
                self.current = None
                self._starting = False
            return await self.play_next(ctx)

        # The lookup blocks. Run it off the bot loop so more ?play commands can
        # record themselves in the thread while this song is still resolving.
        retry = bool(current.get("_stream_retry"))
        try:
            info, audio_url = await asyncio.to_thread(self.extract_audio_url, query, retry)
        except TrackError as err:
            await ctx.send(str(err))
            async with self._queue_lock:
                self.current = None
                self._starting = False
            return await self.play_next(ctx)

        if not info or not audio_url:
            await ctx.send(f"❌ Could not extract audio for {query}. Skipping.")
            async with self._queue_lock:
                self.current = None
                self._starting = False
            return await self.play_next(ctx)

        current = self.current
        if current is None:
            return

        current["artist"] = info.get("uploader", "Unknown")
        current["title"] = info.get("title", "Unknown Title")
        current["duration"] = info.get("duration") or 0
        async with self._queue_lock:
            if self.current is not current:
                return
            await self.update_queue_message(ctx)

        if self.current is not current or not self.playing:
            return

        # A progressive file is fetched by yt-dlp itself (same session,
        # headers, cookies and IPv4 address as the extraction) and piped into
        # ffmpeg; its first chunk is opened here, so a refused stream (403) is
        # caught before anything is announced, and the next progressive format
        # is tried. Only when no progressive format answers does an HLS
        # playlist go straight to ffmpeg (never through the pipe).
        stream = None
        mode = "pipe"
        fmt = info
        try:
            mode, stream, fmt = await asyncio.to_thread(open_track, info)
        except StreamError as err:
            audit_after_playback(ctx, None, stream_error=str(err), elapsed=0.0, mode="none")
            return await self._track_failed(ctx, current, str(err))
        except Exception as err:
            # The pipe could not be set up at all (for example an old yt-dlp
            # without its networking module): ffmpeg opens the URL with
            # yt-dlp's headers instead.
            print(f"MUSIC STREAM: pipe unavailable ({type(err).__name__}); direct URL", flush=True)
            stream, mode, fmt = None, "direct", info
        stream_format = format_label(fmt)

        await self.send_now_playing(ctx, current)

        stderr_tail = StderrTail()
        try:
            # Full path. discord.py's default is the bare name "ffmpeg", which
            # raises "ffmpeg was not found" when the target PC has none on PATH.
            ff_dir = os.path.dirname(ffmpeg_path)
            current_path = os.environ.get("PATH", "")
            if ff_dir and os.path.isdir(ff_dir) and ff_dir not in current_path:
                os.environ["PATH"] = ff_dir + os.pathsep + current_path
            if stream is not None:
                source = discord.FFmpegPCMAudio(
                    stream, pipe=True, executable=ffmpeg_path, stderr=stderr_tail, **PIPE_FFMPEG_OPTIONS
                )
            elif mode == "hls":
                source = discord.FFmpegPCMAudio(
                    fmt["url"], executable=ffmpeg_path, stderr=stderr_tail, **hls_ffmpeg_options(fmt)
                )
            else:
                source = discord.FFmpegPCMAudio(
                    audio_url, executable=ffmpeg_path, stderr=stderr_tail, **direct_ffmpeg_options(info)
                )
        except Exception as e:
            if stream is not None:
                stream.close()
            import shutil
            from core.ffmpeg_fetch import last_error
            detail = last_error()
            extra = f"\n{detail}" if detail else ""
            await ctx.send(
                f"❌ FFmpeg playback error: {e}\n"
                f"ffmpeg path: {ffmpeg_path}\n"
                f"which(ffmpeg) at play: {shutil.which('ffmpeg')}\n"
                f"file exists: {os.path.isfile(ffmpeg_path)}"
                f"{extra}"
            )
            async with self._queue_lock:
                if self.current is current:
                    self.queue.insert(0, current)
                    self.current = None
                    self.playing = False
                    await self.update_queue_message(ctx)
            return

        if self.current is not current or not self.playing or self.vc is None:
            if stream is not None:
                stream.close()
            return

        started = time.monotonic()
        self._user_stopped = False

        def after_playback(error):
            stopped = bool(self._user_stopped)
            elapsed = time.monotonic() - started
            exit_code = None if stopped else _ffmpeg_exit_code(source)
            stream_error = getattr(stream, "error", "") if stream is not None else ""
            if stream is not None:
                stream.close()
            reason = audit_after_playback(
                ctx,
                error,
                exit_code=exit_code,
                elapsed=elapsed,
                stderr=stderr_tail.text(),
                stream_error=stream_error,
                stopped=stopped,
                stream_format=stream_format,
                mode=mode,
            )
            early = bool(reason) and elapsed < EARLY_EXIT_S
            nxt = self._track_failed(ctx, current, reason) if early else self.play_next(ctx)
            asyncio.run_coroutine_threadsafe(nxt, self.bot.loop)

        try:
            self.vc.play(source, after=after_playback)
        except Exception as exc:
            await ctx.send(f"❌ Could not start playback: {exc}")
            async with self._queue_lock:
                if self.current is current:
                    self.queue.insert(0, current)
                    self.current = None
                    self.playing = False
                    await self.update_queue_message(ctx)

    async def _track_failed(self, ctx, entry, reason: str):
        """A track failed right away: re-extract once with cookies and another
        YouTube client and replay it; after that, say so and move on."""
        if not entry.get("_stream_retry"):
            entry["_stream_retry"] = True
            async with self._queue_lock:
                if self.current is entry or self.current is None:
                    self.queue.insert(0, entry)
                    self.current = None
                self._starting = False
            return await self.play_next(ctx)
        entry.pop("_stream_retry", None)
        try:
            await ctx.send(refused_message(str(entry.get("title") or ""), reason))
        except Exception:
            pass
        async with self._queue_lock:
            if self.current is entry:
                self.current = None
            self._starting = False
        return await self.play_next(ctx)

    # ---------------------------------------------------------
    # COMMAND: play
    # ---------------------------------------------------------
    @commands.command()
    async def play(self, ctx, *, query=None):
        if yt_dlp is None:
            return await ctx.send(
                "Music needs `yt-dlp` in this portable build. "
                "Rebuild with `pip install -r requirements.txt` then `install.bat`."
            )

        if not await self.ensure_vc(ctx):
            return
        from core.announce import announce_tool

        await announce_tool(getattr(ctx, "channel", None), "play")

        # Record the song before the slow lookup. Several ?play commands can
        # then all show up in the music-queue thread while the first one resolves.
        if query is None:
            async with self._queue_lock:
                await self._load_queue_once()
                vc = self.vc
                playing = bool(vc and getattr(vc, "is_playing", lambda: False)())
                paused = bool(vc and getattr(vc, "is_paused", lambda: False)())
                has_current = self.current is not None
                has_queue = bool(self.queue)
                decision = playback_decision(playing, paused, has_current, has_queue)
                if decision == "start" and self.current is not None:
                    self.queue.insert(0, self.current)
                    self.current = None
                if decision in ("resume", "start"):
                    self.playing = True
            if decision == "already":
                return await ctx.send("Already playing.")
            if decision == "empty":
                return await ctx.send("Queue is empty.")
            if decision == "resume":
                try:
                    self.vc.resume()
                except Exception:
                    await ctx.send("Resuming playback…")
                    return await self.play_next(ctx)
                return await ctx.send("Resuming playback…")
            await ctx.send("Resuming playback…")
            return await self.play_next(ctx)

        if is_playlist_query(query):
            return await ctx.send(PLAYLIST_REFUSAL)

        label = query
        if bare_video_id(query) or "://" in query:
            info = None
            try:
                info = await asyncio.to_thread(_lookup_video_info, query)
            except Exception:
                info = None
            resolved = track_label(info, "")
            label = resolved or ""

        entry = {
            "artist": "",
            "title": label or query,
            "duration": 0,
            "url": query,
        }
        async with self._queue_lock:
            await self._load_queue_once()
            self.queue.append(entry)
            start = not self.playing
            if start:
                self.playing = True
            await self.update_queue_message(ctx)
            err = self._load_error
        if err:
            await ctx.send(f"⚠️ Queue error: {err}")
        if start:
            await self.play_next(ctx)
        elif label:
            await ctx.send(f"Added: **{label}**")
        else:
            await ctx.send("Added to the queue. I'll show the title when it plays.")

    @commands.command()
    async def skip(self, ctx):
        vc = self.vc
        paused = bool(vc and getattr(vc, "is_paused", lambda: False)())
        playing = bool(vc and getattr(vc, "is_playing", lambda: False)())
        if playing or paused:
            self._user_stopped = True
            vc.stop()
            await ctx.send("Skipped.")
        elif self.current or self.playing or self.queue:
            # Stale flag: nothing is actually on the voice client.
            async with self._queue_lock:
                self.current = None
                self.playing = bool(self.queue)
            if self.playing:
                await ctx.send("Skipped.")
                return await self.play_next(ctx)
            await ctx.send("Nothing is playing.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def stop(self, ctx):
        async with self._queue_lock:
            await self._load_queue_once()
            self.queue.clear()
            self.current = None
            self.playing = False
            await self.update_queue_message(ctx)
        if self.vc:
            self._user_stopped = True
            self.vc.stop()
        await ctx.send("Stopped and cleared queue.")

    @commands.command()
    async def queue(self, ctx):
        await self.ensure_queue_loaded(ctx)

        lines = []
        if self.current:
            artist = self.current.get("artist") or "Unknown"
            title = self.current.get("title") or "Unknown Title"
            lines.append(f"▶ {artist} | {title}")
        lines.extend(
            f"{i+1}. {item.get('artist') or 'Unknown'} | {item.get('title') or 'Unknown Title'}"
            for i, item in enumerate(self.queue)
        )
        if not lines:
            return await ctx.send("Queue is empty.")
        await ctx.send("**Current Queue:**\n" + "\n".join(lines))


async def setup(bot):
    await bot.add_cog(Music(bot))