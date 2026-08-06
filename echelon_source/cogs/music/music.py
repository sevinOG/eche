import discord
from discord.ext import commands
import asyncio
import re
from cogs.music.music_queue_storage import load_queue, save_queue

try:
    import yt_dlp
except ImportError:  # portable build missing optional dep — cog still loads
    yt_dlp = None


PIPED_BASE = "https://piped.video"

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
    "noplaylist": True,
    "ignoreerrors": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
}

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

        # Search via yt-dlp
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if info and "entries" in info and info["entries"]:
                return info["entries"][0]["id"]

        return None

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

        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(piped_url, download=False)
            if not info:
                return None, None

            return info, info.get("url")

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
        if not self.queue_loaded:
            self.queue = await load_queue(self.bot, ctx.guild.id)
            self.queue_loaded = True

    async def update_queue_message(self, ctx):
        await save_queue(self.bot, ctx.guild.id, self.queue)

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

        # ALWAYS build a valid query
        query = self.build_query(self.current)
        if not query:
            await ctx.send("❌ Invalid queue entry. Skipping.")
            return await self.play_next(ctx)

        # Extract audio URL from Piped
        info, audio_url = self.extract_audio_url(query)

        if not info or not audio_url:
            await ctx.send(f"❌ Could not extract audio for {query}. Skipping.")
            return await self.play_next(ctx)

        # Update metadata
        self.current["artist"] = info.get("uploader", "Unknown")
        self.current["title"] = info.get("title", "Unknown Title")
        self.current["duration"] = info.get("duration") or 0

        await self.send_now_playing(ctx, self.current)

        source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)

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

        info, audio_url = self.extract_audio_url(query)

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