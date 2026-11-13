# ?asset — make a picture or a video with Grok Imagine.

from __future__ import annotations

import io

import discord
from discord.ext import commands

from core.asset_message import source_from_message
from core.debuglog import dprint
from core.imagine import AssetFile, ImagineError, api_key, check_prompt, plan_asset, refusal, run_asset, speaker_allowed
from core.tools_asset import asset_text
from core.chat_panel import author_label, inbound, send_visible, edit_visible


class Asset(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="asset")
    async def asset(self, ctx, *, prompt: str = ""):
        """Make a picture or a video. Reply to a file to change that file."""
        try:
            inbound(author_label(getattr(ctx, "author", None)), f"?asset {prompt}".strip())
        except Exception:
            pass
        is_owner = False
        try:
            from core.admin_tools import author_is_owner
            is_owner = await author_is_owner(self.bot, ctx.author)
        except Exception as exc:
            dprint(f"[asset] owner check failed: {exc!r}")
        if not speaker_allowed(is_owner):
            await send_visible(ctx, refusal(is_owner), sender=ctx.reply, mention_author=False)
            return
        try:
            # The block check runs before the source file is even fetched.
            author = getattr(ctx, "author", None)
            channel = getattr(getattr(ctx, "message", None), "channel", None) or getattr(ctx, "channel", None)
            check_prompt(
                prompt,
                user_id=getattr(author, "id", None),
                channel_id=getattr(channel, "id", None),
            )
            source = await source_from_message(ctx.message)
            plan = plan_asset(prompt, source=source)
        except ImagineError as exc:
            await send_visible(ctx, exc.text, sender=ctx.reply, mention_author=False)
            return
        key = api_key()
        if not key:
            await send_visible(ctx, "Add a Grok API key in Media settings.", sender=ctx.reply, mention_author=False)
            return
        status_text = (
            "Making that video." if plan.mode.startswith("video") or plan.mode == "image_video" else "Making that."
        )
        status = await send_visible(ctx, status_text, sender=ctx.reply, mention_author=False)
        try:
            result = await run_asset(plan, key)
        except ImagineError as exc:
            edited = asset_text(exc.text)
            await edit_visible(status, edited)
            return
        except Exception as exc:
            dprint(f"[asset] command failed: {type(exc).__name__}")
            edited = asset_text("I couldn't make that.")
            await edit_visible(status, edited)
            return
        caption = plan.prompt
        if len(caption) > 120:
            caption = caption[:119].rstrip() + "…"
        try:
            if isinstance(result, str):
                await send_visible(
                    ctx,
                    asset_text(
                        f"{caption}\nThe file is too big to attach. It is here for a short time:\n{result}"
                    ),
                    sender=ctx.reply,
                    mention_author=False,
                )
            else:
                file = discord.File(io.BytesIO(result.data), filename=result.filename)
                await send_visible(
                    ctx,
                    asset_text(caption),
                    sender=ctx.reply,
                    file=file,
                    mention_author=False,
                )
        except Exception as exc:
            dprint(f"[asset] send failed: {type(exc).__name__}")
            edited = "I made it, but Discord would not take the file."
            await edit_visible(status, edited)
            return
        try:
            await status.delete()
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(Asset(bot))
