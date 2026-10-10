# core/tools_context.py
# First chat tool: read the speaker's own pinned context.
# Same lookup as the owner ?context_raw command. It does not invoke that
# command, and it will not read a different member even if the model asks.

from __future__ import annotations

import re

from core.context_manager import read_raw_context
from core.tools import ACCESS_ANYONE, Tool, ToolContext, ToolResult, register

# Narrow backup for the phrasings people actually use. The model is still
# the one that chooses the tool for anything this does not match.
_OWN_CONTEXT_ASK = re.compile(
    r"(?:please[, ]+|hey[, ]+|eche[, ]+|can you |could you |will you |just )*"
    r"(?:"
    r"(?:show(?: me)?|see|view|display|read|pull|dump|gimme|give me|let me see)"
    r"(?:\s+\w+){0,6}\s+my context"
    r"|"
    r"(?:what(?:'s|s| is| does)|how(?:'s| is| does))"
    r"(?:\s+\w+){0,6}\s+my context"
    r"|"
    r"(?:my|raw) context"
    r")"
    r"(?:\s+look(?:s|ing)?(?:\s+like)?)?"
    r"(?:\s+please|\s+thanks)?"
    r"\s*\??\s*$",
    re.IGNORECASE,
)
_NOT_OWN_CONTEXT = re.compile(
    r"\b(?:clear|delete|forget|wipe|overwrite|erase|reset|his|her|their)\b",
    re.IGNORECASE,
)


def asks_for_own_context(text: str) -> bool:
    """True when this message is the speaker asking to see their own context."""
    cleaned = " ".join((text or "").replace("\u2019", "'").split())
    if not cleaned or len(cleaned) > 160:
        return False
    if _NOT_OWN_CONTEXT.search(cleaned):
        return False
    return _OWN_CONTEXT_ASK.fullmatch(cleaned) is not None

_CONTEXT_RAW = (
    "Read this speaker's own pinned context, and only when they ask to see it."
)


async def show_own_context(ctx: ToolContext, arguments: dict) -> ToolResult:
    """Always the speaker. Model arguments are ignored on purpose."""
    del arguments
    author = ctx.message.author
    content = await read_raw_context(
        ctx.bot,
        author.id,
        getattr(author, "name", None),
    )
    if content is None:
        return ToolResult(text="I couldn't read your context.")
    if not content.strip():
        return ToolResult(text="(no context stored yet)")
    return ToolResult(text=content)


def register_builtin_tools() -> None:
    register(
        Tool(
            name="context_raw",
            description=_CONTEXT_RAW,
            handler=show_own_context,
            parameters={"type": "object", "properties": {}},
            access=ACCESS_ANYONE,
        )
    )


register_builtin_tools()
