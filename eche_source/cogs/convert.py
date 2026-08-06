# convert.py — Video/GIF → GIF Converter (Attachments Only, Reply Support)
# Converts a video or GIF into a new GIF with <frames> total frames (1–24).
# Rejects messages with multiple attachments.

import discord
from discord.ext import commands
import asyncio
import os
import subprocess
import math
import tempfile


class Convert(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------
    # Helper: Run FFmpeg asynchronously
    # ---------------------------------------------------------
    async def run_ffmpeg(self, args):
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout, stderr

    # ---------------------------------------------------------
    # Helper: Get total frame count of the video/GIF
    # ---------------------------------------------------------
    async def get_frame_count(self, input_path):
        args = [
            "-i", input_path,
            "-map", "0:v:0",
            "-c", "copy",
            "-f", "null",
            "-"
        ]
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()

        # Parse frame count from FFmpeg stderr
        for line in stderr.decode().split("\n"):
            if "frame=" in line:
                try:
                    return int(line.split("frame=")[1].split()[0])
                except:
                    pass
        return None

    # ---------------------------------------------------------
    # Helper: Get attachment from reply or own message
    # ---------------------------------------------------------
    async def get_target_attachment(self, ctx):
        """
        Returns a single attachment from either:
        - The replied-to message
        - Or the user's own message
        Rejects if multiple attachments are found.
        """

        # Check if user replied to a message
        ref = ctx.message.reference
        if ref:
            try:
                replied_msg = await ctx.channel.fetch_message(ref.message_id)
            except:
                return None, "Couldn't fetch the replied-to message."

            if replied_msg.attachments:
                if len(replied_msg.attachments) > 1:
                    return None, "That message has multiple attachments. Pick one."
                return replied_msg.attachments[0], None

        # Fallback: use the user's own attachment
        if ctx.message.attachments:
            if len(ctx.message.attachments) > 1:
                return None, "You attached multiple files. Pick one."
            return ctx.message.attachments[0], None

        return None, "Attach a video/GIF or reply to a message with one."

    # ---------------------------------------------------------
    # COMMAND: ?convert gif <frames>
    # ---------------------------------------------------------
    @commands.command(name="convert")
    async def convert(self, ctx, mode: str = None, frames: int = None):
        """
        Convert an attached or replied-to video/GIF into a GIF with <frames> total frames.
        Usage: ?convert gif 12
        """

        # Validate mode
        if mode != "gif":
            return await ctx.send("Usage: `?convert gif <1-24>`")

        # Validate frame count
        if frames is None or not (1 <= frames <= 30):
            return await ctx.send("Frame count must be between **1 and 30**.")

        # Get attachment from reply or own message
        attachment, error = await self.get_target_attachment(ctx)
        if error:
            return await ctx.send(error)

        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input")
            palette_path = os.path.join(tmpdir, "palette.png")
            output_path = os.path.join(tmpdir, "output.gif")

            # Download attachment
            await attachment.save(input_path)

            # Get total frames
            total_frames = await self.get_frame_count(input_path)
            if not total_frames:
                return await ctx.send("Couldn't read video/GIF frame count.")

            # Compute frame interval
            interval = max(1, math.floor(total_frames / frames))

            await ctx.send(
                f"Converting **{attachment.filename}** → GIF\n"
                f"Extracting **{frames}** frames (interval: {interval})…"
            )

            # ---------------------------------------------------------
            # Step 1: Generate palette
            # ---------------------------------------------------------
            palette_args = [
                "-i", input_path,
                "-vf", f"select='not(mod(n\\,{interval}))',palettegen",
                "-y", palette_path
            ]
            code, _, _ = await self.run_ffmpeg(palette_args)
            if code != 0:
                return await ctx.send("Palette generation failed.")

            # ---------------------------------------------------------
            # Step 2: Generate GIF using palette
            # ---------------------------------------------------------
            gif_args = [
                "-i", input_path,
                "-i", palette_path,
                "-lavfi",
                f"select='not(mod(n\\,{interval}))'[x];[x][1:v]paletteuse",
                "-y", output_path
            ]
            code, _, _ = await self.run_ffmpeg(gif_args)
            if code != 0:
                return await ctx.send("GIF conversion failed.")

            # Send GIF
            await ctx.send(
                f"Here’s your **{frames}-frame GIF**:",
                file=discord.File(output_path, filename="converted.gif")
            )


async def setup(bot):
    await bot.add_cog(Convert(bot))
