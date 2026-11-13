# core/tools_asset.py
# The model calls this when someone asks for a picture, a video, or an edit.
# Who may run it is Media settings, not this file.

from __future__ import annotations

import io
import re

import discord

from core.asset_review import require_owner_review
from core.asset_safety import confirm_asset_delivery, require_enabled
from core.debuglog import dprint
from core.imagine import (
    AssetFile,
    ImagineError,
    api_key,
    plan_asset,
    refuse_speaker,
    run_asset,
)
from core.tools import ACCESS_ANYONE, Tool, ToolResult, announcement, register


_DESCRIPTION = (
    "Make a picture or a video with Grok Imagine when they ask for one. "
    "prompt is their wording, copied through — do not soften, sanitize, or rewrite for filters. "
    "kind is image or video when they named one. Leave kind empty to decide from the prompt. "
    "Editing an attached or replied-to file is not available. A thread of those files is the thread tool."
)


def _ids(message) -> dict:
    author = getattr(message, "author", None)
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    return dict(
        user_id=getattr(author, "id", None),
        guild_id=getattr(guild, "id", None),
        channel_id=getattr(channel, "id", None),
    )


async def _allowed(ctx) -> tuple[bool, str]:
    is_owner = False
    try:
        from core.admin_tools import author_is_owner
        is_owner = await author_is_owner(ctx.bot, getattr(ctx.message, "author", None))
    except Exception as exc:
        dprint(f"[asset] owner check failed: {exc}")
        is_owner = False
    message = getattr(ctx, "message", None)
    channel = getattr(message, "channel", None)
    gate = {}
    blocked = refuse_speaker(is_owner, channel=channel, out=gate, **_ids(message))
    if blocked:
        return False, blocked
    ctx._asset_mode = gate["mode"]
    return True, ""


def asset_prompt(arguments: dict | None, message_text: str) -> str:
    """What to make or change. The user's words fill in when the call left them out."""
    args = arguments if isinstance(arguments, dict) else {}
    prompt = " ".join(str(args.get("prompt") or "").split())
    if prompt:
        return prompt
    text = re.sub(r"<@!?\d+>", " ", message_text or "")
    return " ".join(text.split())


def asset_text(message: str) -> str:
    """Eche used *asset*! and then the line that goes with the file."""
    body = (message or "").strip()
    line = announcement("asset")
    if not body:
        return line
    return f"{line}\n{body}"


async def _post(message, result: AssetFile, caption: str) -> None:
    if not isinstance(result, AssetFile):
        raise ImagineError("I couldn't make that.")
    file = discord.File(io.BytesIO(result.data), filename=result.filename)
    await message.reply(content=asset_text(caption), file=file, mention_author=False)


async def make_asset(ctx, arguments: dict) -> ToolResult:
    ok, why = await _allowed(ctx)
    if not ok:
        return ToolResult(text=why, fence=False)
    message = getattr(ctx, "message", None)
    user_text = getattr(message, "content", "") or ""
    prompt = asset_prompt(arguments, user_text)
    from core.guardrails import softened_prompt

    if softened_prompt(prompt, user_text):
        prompt = asset_prompt({}, user_text)
    kind = str((arguments or {}).get("kind") or "").strip().lower()
    if kind not in ("image", "video"):
        kind = ""
    ids = _ids(message)
    channel = getattr(message, "channel", None)
    mode = getattr(ctx, "_asset_mode", None)
    try:
        plan = plan_asset(prompt, kind=kind, asset_mode=mode)
        result = await run_asset(plan, api_key(), asset_mode=mode, **ids)
        if not isinstance(result, AssetFile):
            raise ImagineError("I couldn't make that.")
        result = await require_owner_review(
            result,
            bot=getattr(ctx, "bot", None),
            origin=ctx,
            prompt=plan.prompt,
            asset_mode=mode,
            channel=channel,
            **ids,
        )
        confirm_asset_delivery(mode, channel, **ids)
        require_enabled(mode, **ids)
    except ImagineError as exc:
        return ToolResult(text=exc.text, fence=False, detail=exc.text)
    except Exception as exc:
        dprint(f"[asset] failed: {type(exc).__name__}")
        return ToolResult(text="I couldn't make that.", fence=False, detail=type(exc).__name__)
    caption = plan.prompt
    if len(caption) > 120:
        caption = caption[:119].rstrip() + "…"
    try:
        confirm_asset_delivery(mode, channel, **ids)
        require_enabled(mode, **ids)
        await _post(ctx.message, result, caption)
        from core.asset_review import audit_posted
        audit_posted(**ids)
    except Exception as exc:
        dprint(f"[asset] send failed: {type(exc).__name__}")
        return ToolResult(
            text="I made it, but Discord would not take the file.",
            fence=False,
            detail=type(exc).__name__,
        )
    mode = result.mode if isinstance(result, AssetFile) else plan.mode
    size = len(result.data) if isinstance(result, AssetFile) else 0
    return ToolResult(
        text=caption,
        fence=False,
        quiet=True,
        detail=f"mode: {mode}\nbytes: {size}",
    )


def register_asset_tool() -> None:
    register(
        Tool(
            name="asset",
            description=_DESCRIPTION,
            handler=make_asset,
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "What the picture or video should show.",
                    },
                    "kind": {
                        "type": "string",
                        "description": "image or video. Leave empty when the prompt already says which.",
                    },
                },
                "required": ["prompt"],
            },
            access=ACCESS_ANYONE,
        )
    )


register_asset_tool()
