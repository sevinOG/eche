# core/tools_asset.py
# The model calls this when someone asks for a picture, a video, or an edit.
# Who may run it is Media settings, not this file.

from __future__ import annotations

import io

import discord

from core.asset_message import source_from_message
from core.debuglog import dprint
from core.imagine import (
    AssetFile,
    ImagineError,
    api_key,
    plan_asset,
    refusal,
    run_asset,
    speaker_allowed,
)
from core.tools import ACCESS_ANYONE, Tool, ToolResult, announcement, register


_DESCRIPTION = (
    "Make a picture or a video with Grok Imagine when they ask for one. "
    "prompt is what to make or how to change an attached picture or video. "
    "kind is image or video when they named one. Leave kind empty to decide from the prompt."
)


async def _allowed(ctx) -> tuple[bool, str]:
    is_owner = False
    try:
        from core.admin_tools import author_is_owner
        is_owner = await author_is_owner(ctx.bot, getattr(ctx.message, "author", None))
    except Exception as exc:
        dprint(f"[asset] owner check failed: {exc}")
        is_owner = False
    if speaker_allowed(is_owner):
        return True, ""
    return False, refusal(is_owner)


def asset_text(message: str) -> str:
    """Eche used *asset*! and then the line that goes with the file."""
    body = (message or "").strip()
    line = announcement("asset")
    if not body:
        return line
    return f"{line}\n{body}"


async def _post(message, result: AssetFile | str, caption: str) -> None:
    if isinstance(result, str):
        await message.reply(
            asset_text(
                f"{caption}\nThe file is too big to attach. It is here for a short time:\n{result}"
            ),
            mention_author=False,
        )
        return
    file = discord.File(io.BytesIO(result.data), filename=result.filename)
    await message.reply(content=asset_text(caption), file=file, mention_author=False)


async def make_asset(ctx, arguments: dict) -> ToolResult:
    ok, why = await _allowed(ctx)
    if not ok:
        return ToolResult(text=why, fence=False)
    prompt = " ".join(str((arguments or {}).get("prompt") or "").split())
    kind = str((arguments or {}).get("kind") or "").strip().lower()
    if kind not in ("image", "video"):
        kind = ""
    try:
        source = await source_from_message(ctx.message)
        plan = plan_asset(prompt, kind=kind, source=source)
        key = api_key()
        result = await run_asset(plan, key)
    except ImagineError as exc:
        return ToolResult(text=exc.text, fence=False, detail=exc.text)
    except Exception as exc:
        dprint(f"[asset] failed: {type(exc).__name__}")
        return ToolResult(text="I couldn't make that.", fence=False, detail=type(exc).__name__)
    caption = plan.prompt
    if len(caption) > 120:
        caption = caption[:119].rstrip() + "…"
    try:
        await _post(ctx.message, result, caption)
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
                        "description": "What the picture or video should show, or the change to make.",
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
