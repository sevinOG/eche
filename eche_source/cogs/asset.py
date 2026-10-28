# ?asset — make a picture or a video with Grok Imagine.

from __future__ import annotations

import io

import discord
from discord.ext import commands

from core.asset_message import source_from_message
from core.debuglog import dprint
from core.imagine import AssetFile, ImagineError, api_key, plan_asset, refusal, run_asset, speaker_allowed
from core.tools_asset import asset_text


class Asset(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="asset")
    async def asset(self, ctx, *, prompt: str = ""):
        """Make a picture or a video. Reply to a file, or `?asset` uses the last image in the channel."""
        is_owner = False
        try:
            from core.admin_tools import author_is_owner
            is_owner = await author_is_owner(self.bot, ctx.author)
        except Exception as exc:
            dprint(f"[asset] owner check failed: {exc}")
        if not speaker_allowed(is_owner):
            await ctx.reply(refusal(is_owner), mention_author=False)
            return
        try:
            source = await source_from_message(ctx.message, recent_image=True)
            plan = plan_asset(prompt, source=source)
        except ImagineError as exc:
            await ctx.reply(exc.text, mention_author=False)
            return
        key = api_key()
        if not key:
            await ctx.reply("Add a Grok API key in Media settings.", mention_author=False)
            return
        status = await ctx.reply(
            "Making that video." if plan.mode.startswith("video") or plan.mode == "image_video" else "Making that.",
            mention_author=False,
        )
        try:
            result = await run_asset(plan, key)
        except ImagineError as exc:
            await status.edit(content=asset_text(exc.text))
            return
        except Exception as exc:
            dprint(f"[asset] command failed: {type(exc).__name__}")
            await status.edit(content=asset_text("I couldn't make that."))
            return
        caption = plan.prompt
        if len(caption) > 120:
            caption = caption[:119].rstrip() + "…"
        try:
            if isinstance(result, str):
                await ctx.reply(
                    asset_text(
                        f"{caption}\nThe file is too big to attach. It is here for a short time:\n{result}"
                    ),
                    mention_author=False,
                )
            else:
                file = discord.File(io.BytesIO(result.data), filename=result.filename)
                await ctx.reply(content=asset_text(caption), file=file, mention_author=False)
        except Exception as exc:
            dprint(f"[asset] send failed: {type(exc).__name__}")
            await status.edit(content="I made it, but Discord would not take the file.")
            return
        try:
            await status.delete()
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(Asset(bot))
