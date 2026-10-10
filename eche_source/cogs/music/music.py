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


def ydl_options() -> dict:
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
        # False so a failed video raises DownloadError instead of a None search
        # entry. Age-gate wording still arrives on the logger, not that exception.
        "ignoreerrors": False,
        "geo_bypass": True,
        "nocheckcertificate": True,
        # A signed-in YouTube request has no playable formats until the player
        # challenge is solved. Node is installed; deno is not.
        "js_runtimes": {"node": {}},
        "remote_components": ["ejs:github"],
    }
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        opts["js_runtimes"] = {"node": {"path": node}}
    cookie = youtube_cookie_file()
    if cookie:
        opts["cookiefile"] = cookie
    return opts


@contextmanager
def youtube_dl():
    """Open yt-dlp without letting it rewrite the saved cookie file."""
    if yt_dlp is None:
        raise TrackError(
            "Music needs `yt-dlp` in this portable build. "
            "Rebuild with `pip install -r requirements.txt` then `install.bat`."
        )
    opts = ydl_options()
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


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.current = None
        self.vc = None
        self.playing = False
        self.queue_loaded = False

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

        # Direct URL
        match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", query)
        if match:
            return match.group(1)

        if yt_dlp is None:
            return None

        # Search via yt-dlp. A None entry used to raise TypeError here.
        notes = None
        try:
            with youtube_dl() as ydl:
                notes = ydl._eche_notes
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
        except TrackError:
            raise
        except Exception as exc:
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
    def extract_audio_url(self, query):
        if yt_dlp is None:
            return None, None

        video_id = self.extract_video_id(query)
        if not video_id:
            return None, None

        piped_url = self.build_piped_url(video_id)

        notes = None
        try:
            with youtube_dl() as ydl:
                notes = ydl._eche_notes
                info = ydl.extract_info(piped_url, download=False)
        except TrackError:
            raise
        except Exception as exc:
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

        if self.vc is None or not self.vc.is_connected():
            self.vc = await ctx.author.voice.channel.connect()
        return True

    # ---------------------------------------------------------
    # Load queue once
    # ---------------------------------------------------------
    async def ensure_queue_loaded(self, ctx):
        # Queue is now always stored in the bot's HOME server (to avoid permission errors in other servers).
        # Voice playback still happens in the server where the user ran the command.
        if not self.queue_loaded:
            try:
                self.queue = await load_queue(self.bot)
            except Exception as e:
                await ctx.send(f"⚠️ Queue error: {e}")
                self.queue = []
            self.queue_loaded = True

    async def update_queue_message(self, ctx):
        try:
            await save_queue(self.bot, self.queue)
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
        if not self.queue:
            self.playing = False
            self.current = None
            await self.update_queue_message(ctx)
            return

        self.playing = True
        self.current = self.queue.pop(0)
        await self.update_queue_message(ctx)

        ffmpeg_path = await self._resolve_ffmpeg(ctx)
        if not ffmpeg_path:
            self.queue.insert(0, self.current)
            self.current = None
            self.playing = False
            await self.update_queue_message(ctx)
            return

        # ALWAYS build a valid query
        query = self.build_query(self.current)
        if not query:
            await ctx.send("❌ Invalid queue entry. Skipping.")
            return await self.play_next(ctx)

        # Extract audio URL from Piped
        try:
            info, audio_url = self.extract_audio_url(query)
        except TrackError as err:
            await ctx.send(str(err))
            return await self.play_next(ctx)

        if not info or not audio_url:
            await ctx.send(f"❌ Could not extract audio for {query}. Skipping.")
            return await self.play_next(ctx)

        # Update metadata
        self.current["artist"] = info.get("uploader", "Unknown")
        self.current["title"] = info.get("title", "Unknown Title")
        self.current["duration"] = info.get("duration") or 0

        await self.send_now_playing(ctx, self.current)

        try:
            # Full path. discord.py's default is the bare name "ffmpeg", which
            # raises "ffmpeg was not found" when the target PC has none on PATH.
            ff_dir = os.path.dirname(ffmpeg_path)
            current_path = os.environ.get("PATH", "")
            if ff_dir and os.path.isdir(ff_dir) and ff_dir not in current_path:
                os.environ["PATH"] = ff_dir + os.pathsep + current_path
            source = discord.FFmpegPCMAudio(audio_url, executable=ffmpeg_path, **FFMPEG_OPTIONS)
        except Exception as e:
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
            self.queue.insert(0, self.current)
            self.current = None
            self.playing = False
            await self.update_queue_message(ctx)
            return

        def after_playback(error):
            asyncio.run_coroutine_threadsafe(
                self.play_next(ctx),
                self.bot.loop
            )

        self.vc.play(source, after=after_playback)

    # ---------------------------------------------------------
    # COMMAND: play
    # ---------------------------------------------------------
    @commands.command()
    async def play(self, ctx, *, query=None):
        await self.ensure_queue_loaded(ctx)

        if yt_dlp is None:
            return await ctx.send(
                "Music needs `yt-dlp` in this portable build. "
                "Rebuild with `pip install -r requirements.txt` then `install.bat`."
            )

        if not await self.ensure_vc(ctx):
            return

        if query is None:
            if self.playing:
                return await ctx.send("Already playing.")
            if not self.queue:
                return await ctx.send("Queue is empty.")
            await ctx.send("Resuming playback…")
            return await self.play_next(ctx)

        try:
            info, audio_url = self.extract_audio_url(query)
        except TrackError as err:
            return await ctx.send(str(err))

        if not info:
            return await ctx.send("No results found.")

        entry = {
            "artist": info.get("uploader", "Unknown"),
            "title": info.get("title", "Unknown Title"),
            "duration": info.get("duration") or 0,
            "url": query,  # always store the original query
        }

        self.queue.append(entry)
        await self.update_queue_message(ctx)

        if not self.playing:
            await self.play_next(ctx)
        else:
            await ctx.send(f"Added: **{entry['artist']} | {entry['title']}**")

    @commands.command()
    async def skip(self, ctx):
        if self.vc and self.vc.is_playing():
            self.vc.stop()
            await ctx.send("Skipped.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def stop(self, ctx):
        await self.ensure_queue_loaded(ctx)

        if self.vc:
            self.queue.clear()
            await self.update_queue_message(ctx)
            self.vc.stop()
            await ctx.send("Stopped and cleared queue.")

    @commands.command()
    async def queue(self, ctx):
        await self.ensure_queue_loaded(ctx)

        if not self.queue:
            return await ctx.send("Queue is empty.")

        msg = "\n".join(
            f"{i+1}. {item['artist']} | {item['title']}"
            for i, item in enumerate(self.queue)
        )
        await ctx.send(f"**Current Queue:**\n{msg}")


async def setup(bot):
    await bot.add_cog(Music(bot))