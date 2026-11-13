# ?asset — make a picture or a video with Grok Imagine.

from __future__ import annotations

import io

import discord
from discord.ext import commands

from core.asset_review import require_owner_review
from core.asset_safety import KILL_SWITCH_OFF, require_enabled
from core.debuglog import dprint
from core.imagine import AssetFile, ImagineError, api_key, plan_asset, refuse_speaker, run_asset
from core.tools_asset import asset_text


class Asset(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="asset")
    async def asset(self, ctx, *, prompt: str = ""):
        """Make a picture or a video."""
        is_owner = False
        try:
            from core.admin_tools import author_is_owner
            is_owner = await author_is_owner(self.bot, ctx.author)
        except Exception as exc:
            dprint(f"[asset] owner check failed: {exc}")
        author = getattr(ctx, "author", None)
        guild = getattr(ctx, "guild", None)
        channel = getattr(getattr(ctx, "message", None), "channel", None) or getattr(ctx, "channel", None)
        ids = dict(
            user_id=getattr(author, "id", None),
            guild_id=getattr(guild, "id", None),
            channel_id=getattr(channel, "id", None),
        )
        blocked = refuse_speaker(is_owner, **ids)
        if blocked:
            await ctx.reply(blocked, mention_author=False)
            return
        try:
            plan = plan_asset(prompt)
            result = await run_asset(plan, api_key(), **ids)
            result = await require_owner_review(result, bot=self.bot, origin=ctx, **ids)
            require_enabled()
        except ImagineError as exc:
            await ctx.reply(exc.text, mention_author=False)
            return
        except Exception as exc:
            dprint(f"[asset] command failed: {type(exc).__name__}")
            await ctx.reply(asset_text("I couldn't make that."), mention_author=False)
            return
        if not isinstance(result, AssetFile):
            await ctx.reply(asset_text("I couldn't make that."), mention_author=False)
            return
        caption = plan.prompt
        if len(caption) > 120:
            caption = caption[:119].rstrip() + "…"
        try:
            require_enabled()
            file = discord.File(io.BytesIO(result.data), filename=result.filename)
            await ctx.reply(content=asset_text(caption), file=file, mention_author=False)
        except ImagineError as exc:
            await ctx.reply(exc.text, mention_author=False)
            return
        except Exception as exc:
            dprint(f"[asset] send failed: {type(exc).__name__}")
            await ctx.reply("I made it, but Discord would not take the file.", mention_author=False)
            return


async def setup(bot):
    await bot.add_cog(Asset(bot))
