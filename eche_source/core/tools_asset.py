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
    "Make a picture or a video with Grok Imagine, or edit one. "
    "Only call this when THIS message itself asks for a picture, a video, or a change to one. "
    "Never call it for reactions, complaints, jokes, or follow-ups such as \"what the heck\", "
    "\"lol\", or \"stop\"; answer those in chat. "
    "After a blocked, filtered, capped, or failed try, never call it again unless they explicitly "
    "ask again in a new message (\"try again\", \"redo\", or a new request). "
    "When their message replies to a picture or video and asks for a change to it "
    "(\"make her hair red\", \"give him a hat\", \"make it night\"), call this: the replied-to "
    "file is used as the source automatically, and prompt is the change. "
    "When they only ask about the replied-to file, answer in chat instead. "
    "prompt is their wording, copied through — do not soften, sanitize, or rewrite for filters. "
    "kind is image or video when they named one for a new file. Leave kind empty to decide from the prompt. "
    "A thread of files is the thread tool."
)


COMMAND_MESSAGE_SKIPPED = "That message is a ? command; the command handles it."
NO_REQUEST_DECLINED = "That message does not ask for a picture or video, so nothing was made."
# The chat turn is re-asked without the asset tool after a decline. Without
# this note the model saw an image request and no asset tool, and told the
# user the asset tool "isn't loaded" (live, 2026-11-14 08:33 ET).
DECLINED_MODEL_NOTE = (
    "Note for this reply only: the asset tool is loaded and working, but it did "
    "not run for this message because the message does not clearly ask for a "
    "picture or video (for example \"make ...\", \"draw ...\", \"generate ...\", "
    "or \"try again\"). Do not say the asset tool is unavailable, missing, broken, "
    "or not loaded. Reply briefly in character, and if they seem to want a "
    "picture, ask them to say what to make."
)


def declined_turn_system(admin_text: str = "") -> str:
    """extra_system for the chat re-ask after a declined asset call."""
    return "\n\n".join(part for part in ((admin_text or "").strip(), DECLINED_MODEL_NOTE) if part)

_OUTCOME_FOR_STATUS = {
    "safety_block": "refusal",
    "provider_filtered": "refusal",
    "capped": "refusal",
    "not_allowed": "refusal",
    "error": "error",
}


def audit_request(*, prompt: str, kind: str, source: str, has_media: bool, ids: dict) -> None:
    """One audit line per asset request: the prompt, kind, and where it came from.

    source is "chat" (the model's tool call) or "?asset" (the command).
    The prompt goes through the audit log's secret redaction like every line.
    """
    try:
        from core.audit import record

        record(
            "asset-request",
            source=source,
            kind=kind or "auto",
            reply_media="1" if has_media else "0",
            prompt=" ".join(str(prompt or "").split())[:600],
            **ids,
        )
    except Exception:
        pass


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
    # A message with a registered ?command line belongs to that command only.
    # The chat path already skips it; this keeps the tool from ever running
    # (and using a cap slot) for it.
    message = getattr(ctx, "message", None)
    try:
        from core.command_lines import is_command_message

        if is_command_message(getattr(ctx, "bot", None), message):
            return False, COMMAND_MESSAGE_SKIPPED
    except Exception:
        return False, COMMAND_MESSAGE_SKIPPED
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
    ctx._asset_owner = bool(gate.get("owner"))
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
    sent = await message.reply(content=asset_text(caption), file=file, mention_author=False)
    # This file passed the hardcoded checks; a reply to it is not rechecked.
    from core.reply_images import record_sent_message

    record_sent_message(sent, result.data)


def _status_result(exc, text: str | None = None) -> ToolResult:
    from core.imagine import RETRY_RULE, error_status

    status = error_status(exc)
    shown = exc.text if text is None else text
    return ToolResult(
        text=shown,
        fence=False,
        detail=f"status: {status}\n{shown}\n{RETRY_RULE}",
        status=status,
        outcome=_OUTCOME_FOR_STATUS.get(status, "error"),
    )


async def make_asset(ctx, arguments: dict) -> ToolResult:
    message = getattr(ctx, "message", None)
    user_text = getattr(message, "content", "") or ""
    raw_prompt = " ".join(str((arguments or {}).get("prompt") or "").split())
    # Only a message that itself asks for something runs the tool. This uses
    # no cap slot and posts nothing; the model's chat reply is used instead.
    from core.asset_intent import decline_reason, strip_added_youthful_styles

    declined = decline_reason(raw_prompt, user_text)
    if declined:
        try:
            from core.audit import record

            # A short excerpt of the declined message, so a decline can be
            # diagnosed from the log (the prompt is already logged on runs).
            excerpt = " ".join(str(user_text or "").split())[:200]
            record("asset-request", source="chat", outcome="declined", reason=declined,
                   message=excerpt, **_ids(message))
        except Exception:
            pass
        return ToolResult(text=NO_REQUEST_DECLINED, fence=False, declined=True, outcome="declined")
    ok, why = await _allowed(ctx)
    if not ok:
        if why == COMMAND_MESSAGE_SKIPPED:
            return ToolResult(text=why, fence=False, status="not_allowed", outcome="refusal")
        from core.imagine import ImagineError as _IE

        return _status_result(_IE(why))
    prompt = asset_prompt(arguments, user_text)
    from core.guardrails import softened_prompt

    if softened_prompt(prompt, user_text):
        prompt = asset_prompt({}, user_text)
    prompt = strip_added_youthful_styles(prompt, user_text) or asset_prompt({}, user_text)
    kind = str((arguments or {}).get("kind") or "").strip().lower()
    if kind not in ("image", "video"):
        kind = ""
    ids = _ids(message)
    channel = getattr(message, "channel", None)
    mode = getattr(ctx, "_asset_mode", None)
    try:
        # A reply to a picture or video edits it, exactly like ?asset sent
        # as a reply: same source checks (Eche's own recorded outputs are
        # not rechecked), output checks, caps, refunds, and approval.
        from core.asset_source import source_from_reply

        source = await source_from_reply(message)
        audit_request(prompt=prompt, kind=kind, source="chat", has_media=source is not None, ids=ids)
        plan = plan_asset(prompt, kind="" if source is not None else kind, asset_mode=mode, source=source)
        result = await run_asset(
            plan, api_key(), asset_mode=mode, is_owner=bool(getattr(ctx, "_asset_owner", False)), **ids
        )
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
        return _status_result(exc)
    except Exception as exc:
        dprint(f"[asset] failed: {type(exc).__name__}")
        from core.imagine import ImagineError as _IE

        return _status_result(_IE("Something went wrong on my end, so nothing was made.", status="error"))
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
            status="error",
            outcome="error",
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
