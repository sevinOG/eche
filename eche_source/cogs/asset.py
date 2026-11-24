# ?asset — make a picture or a video with Grok Imagine.

from __future__ import annotations

import io

import discord
from discord.ext import commands

from core.asset_review import require_owner_review
from core.asset_safety import KILL_SWITCH_OFF, confirm_asset_delivery, require_enabled
from core.debuglog import dprint
from core.imagine import AssetFile, ImagineError, api_key, error_status, plan_asset, refuse_speaker, run_asset
from core.tools_asset import asset_text, audit_request


def _mark_outcome(exc) -> None:
    """The command's completion row says refusal or error, not success, when
    no asset was delivered (live 2026-11-23 03:00 ET: xAI moderation refused
    and the ?asset row still said success)."""
    try:
        from core.audit import mark_command_error, mark_refusal

        status = error_status(exc) if exc is not None else "error"
        if status == "error":
            mark_command_error()
        else:
            mark_refusal()
    except Exception:
        pass


def _clear_outcome() -> None:
    """A delivered asset logs success even when an earlier attempt was blocked
    (live 2026-11-23 20:21:06 ET: the retry posted and the row said refusal)."""
    try:
        from core.audit import consume_command_error, consume_refusal

        consume_refusal()
        consume_command_error()
    except Exception:
        pass


class Asset(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="asset")
    async def asset(self, ctx, *, prompt: str = ""):
        """Make a picture or a video, or reply to one to edit it."""
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
        gate = {}
        blocked = refuse_speaker(is_owner, channel=channel, out=gate, **ids)
        if blocked:
            await ctx.reply(blocked, mention_author=False)
            return
        mode = gate["mode"]
        try:
            # Replying to a picture or video edits it; the command text is
            # the instruction. A reply without media makes a new asset.
            from core.asset_source import source_from_reply

            source = await source_from_reply(getattr(ctx, "message", None))
            audit_request(prompt=prompt, kind="", source="?asset", has_media=source is not None, ids=ids)
            plan = plan_asset(prompt, asset_mode=mode, source=source)
            from core.tools_asset import announce_start, retry_notifier

            await announce_start(channel, getattr(plan, "mode", ""))
            result = await run_asset(
                plan,
                api_key(),
                asset_mode=mode,
                is_owner=bool(gate.get("owner")),
                notify=retry_notifier(channel, getattr(plan, "mode", "")),
                **ids,
            )
            result = await require_owner_review(
                result,
                bot=self.bot,
                origin=ctx,
                prompt=plan.prompt,
                asset_mode=mode,
                channel=channel,
                **ids,
            )
            confirm_asset_delivery(mode, channel, **ids)
            require_enabled(mode, **ids)
        except ImagineError as exc:
            _mark_outcome(exc)
            await ctx.reply(exc.text, mention_author=False)
            return
        except Exception as exc:
            dprint(f"[asset] command failed: {type(exc).__name__}")
            _mark_outcome(None)
            await ctx.reply(asset_text("I couldn't make that."), mention_author=False)
            return
        if not isinstance(result, AssetFile):
            _mark_outcome(None)
            await ctx.reply(asset_text("I couldn't make that."), mention_author=False)
            return
        caption = plan.prompt
        if len(caption) > 120:
            caption = caption[:119].rstrip() + "…"
        try:
            confirm_asset_delivery(mode, channel, **ids)
            require_enabled(mode, **ids)
            file = discord.File(io.BytesIO(result.data), filename=result.filename)
            sent = await ctx.reply(content=asset_text(caption), file=file, mention_author=False)
            # This file passed the hardcoded checks; a reply to it is not rechecked.
            from core.reply_images import record_sent_message

            record_sent_message(sent, result.data)
            from core.asset_review import audit_posted
            audit_posted(**ids)
            _clear_outcome()
        except ImagineError as exc:
            _mark_outcome(exc)
            await ctx.reply(exc.text, mention_author=False)
            return
        except Exception as exc:
            dprint(f"[asset] send failed: {type(exc).__name__}")
            _mark_outcome(None)
            await ctx.reply("I made it, but Discord would not take the file.", mention_author=False)
            return


async def setup(bot):
    await bot.add_cog(Asset(bot))
