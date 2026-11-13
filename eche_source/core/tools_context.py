# core/tools_context.py
# Chat tool: show the speaker what Eche remembers about them.
#
# Anyone can use it, but only when they explicitly ask to see their memory
# or what Eche remembers about them. A pasted link or "what do you think"
# is not enough. It only ever reads the speaker's own pin. Model arguments
# are ignored, so it cannot be pointed at another member. The text is
# filtered (no mentions, ids, or slurs) before it is posted.

from __future__ import annotations

from core.context_manager import parse_pin_sections, read_raw_context
from core.tools import ACCESS_ANYONE, Tool, ToolContext, ToolResult, register

TOOL_NAME = "context_raw"

_DESCRIPTION = (
    "Show the speaker what you remember about them: their own stored memory. "
    "Use it only when they explicitly ask to see their memory, context, or "
    "what you know or remember about them. "
    "It only reads the speaker's own memory, never anyone else's. "
    "Do not use it for a pasted link, an image, or a question about something they just sent."
)

NOT_A_MEMORY_REQUEST = (
    "The user did not ask to see their memory. "
    "Answer their latest message normally. Do not mention or quote their stored memory."
)

NOTHING_STORED = "I don't have anything stored about you yet."
UNREADABLE = "I couldn't read your memory right now."
_INTRO = "What I remember about you:"


def _break_pings(text: str) -> str:
    """Belt and braces after the filter: nothing in the reply can ping."""
    text = (text or "").replace("```", "'''")
    text = text.replace("<@", "<@\u200b").replace("<#", "<#\u200b")
    text = text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    return text


def own_memory_text(content: str | None) -> str:
    """The reply for one pin. Summary only, filtered. None means unreadable."""
    if content is None:
        return UNREADABLE
    from core.content_filter import clean_memory_text

    _label, summary, _recent = parse_pin_sections(content or "", "")
    prose = clean_memory_text((summary or "").strip())
    if not prose or prose in ("(none yet)", "(summary unavailable)"):
        return NOTHING_STORED
    return f"{_INTRO}\n{_break_pings(prose)}"


async def show_own_context(ctx: ToolContext, arguments: dict) -> ToolResult:
    """Always the speaker. Model arguments are ignored on purpose."""
    del arguments
    from core.guardrails import asks_for_memory

    content = getattr(ctx.message, "content", "") or ""
    if not asks_for_memory(content):
        return ToolResult(text=NOT_A_MEMORY_REQUEST, fence=False, for_model=True)
    author = ctx.message.author
    stored = await read_raw_context(
        ctx.bot,
        author.id,
        getattr(author, "name", None),
    )
    return ToolResult(text=own_memory_text(stored), fence=False)


def register_builtin_tools() -> None:
    register(
        Tool(
            name=TOOL_NAME,
            description=_DESCRIPTION,
            handler=show_own_context,
            parameters={"type": "object", "properties": {}},
            access=ACCESS_ANYONE,
        )
    )


register_builtin_tools()
