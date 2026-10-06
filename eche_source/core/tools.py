# core/tools.py
# Chat tools Eche can run when a person asks in conversation.
#
# The model chooses a tool. This module runs it. A tool must not be faked by
# typing a ?command into the channel.
#
# Owner admin tools (mute, timeout, kick, ban) register here with
# access="owner". They are offered only when Admin tools is on and the
# speaker is the Owner ID from Settings (the application owner when that
# field is blank). Their prompt text lives in config/admin_tools.md and is
# injected on that owner's turn. Handlers receive the real Discord message.
# A tool that is only about the speaker must use message.author and ignore
# any user id the model supplies.

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from core.debuglog import dprint

ACCESS_ANYONE = "anyone"
ACCESS_OWNER = "owner"

DISCORD_LIMIT = 2000
_FENCE_OVERHEAD = 8  # ```\n + \n```

_TOOL_BLOCK = re.compile(r"<tool_call>(.*?)</tool_call>", re.IGNORECASE | re.DOTALL)
_FN_TAG = re.compile(r"<function=([A-Za-z0-9_\-]+)>", re.IGNORECASE)
_TYPED_COMMAND = re.compile(
    r"[?!]?(?:context_raw|contet_raw)(?:\s+\S+)?",
    re.IGNORECASE,
)


@dataclass
class ToolContext:
    """The Discord turn a tool is allowed to act on."""

    bot: object
    message: object


@dataclass
class ToolResult:
    """User-facing result. `text` is shown after the announcement line."""

    text: str = ""
    ran: bool = False
    name: str = ""
    # Context is fenced so a stored mention is not pinged. A lookup answer is not.
    fence: bool = True
    # Debug text for the main-window log. Empty means show `text`.
    detail: str = ""
    # When set, chat gets a model reply written from `text`, not `text` itself.
    for_model: bool = False


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[[ToolContext, dict], Awaitable[ToolResult]]
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    # "anyone" — any speaker. The handler still limits what they can touch.
    # "owner" — bot owner only. Checked again at execute time.
    access: str = ACCESS_ANYONE


_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _TOOLS[tool.name] = tool


def registered_names() -> set[str]:
    return set(_TOOLS)


def _admin_visible(tool: Tool, *, speaker_is_owner: bool, admin_enabled: bool) -> bool:
    if tool.access != ACCESS_OWNER:
        return True
    return bool(speaker_is_owner and admin_enabled)


def visible_names(*, speaker_is_owner: bool, admin_enabled: bool) -> set[str]:
    """Tool names the model is allowed to see on this turn."""
    return {
        tool.name
        for tool in _TOOLS.values()
        if _admin_visible(tool, speaker_is_owner=speaker_is_owner, admin_enabled=admin_enabled)
    }


def _resolve(name: str) -> Tool | None:
    if name in _TOOLS:
        return _TOOLS[name]
    folded = (name or "").casefold()
    for key, tool in _TOOLS.items():
        if key.casefold() == folded:
            return tool
    return None


def specs_for(*, speaker_is_owner: bool, admin_enabled: bool = False) -> list[dict]:
    """OpenAI-style tool list. Admin tools are omitted unless the toggle is on."""
    specs = []
    for tool in _TOOLS.values():
        if not _admin_visible(
            tool, speaker_is_owner=speaker_is_owner, admin_enabled=admin_enabled
        ):
            continue
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    specs.sort(key=lambda spec: spec["function"]["name"])
    return specs


async def execute(name: str, arguments: dict | None, ctx: ToolContext) -> ToolResult:
    """Run one tool. Unknown tools and owner tools from non-owners do not run."""
    tool = _resolve(name or "")
    if tool is None:
        return ToolResult(name=name or "", ran=False)

    if tool.access == ACCESS_OWNER:
        try:
            from core.admin_tools import admin_tools_enabled
            if not admin_tools_enabled():
                return ToolResult(name=tool.name, ran=False)
        except Exception as e:
            dprint(f"[tools] admin toggle check failed for {tool.name}: {e}")
            return ToolResult(name=tool.name, ran=False)
        allowed = False
        try:
            from core.admin_tools import author_is_owner
            allowed = await author_is_owner(ctx.bot, getattr(ctx.message, "author", None))
        except Exception as e:
            dprint(f"[tools] owner check failed for {tool.name}: {e}")
            allowed = False
        if not allowed:
            return ToolResult(name=tool.name, ran=False)

    try:
        result = await tool.handler(ctx, arguments or {})
    except Exception as e:
        dprint(f"[tools] {tool.name} failed: {e}")
        return ToolResult(name=tool.name, text="I couldn't finish that.", ran=True)

    if result is None:
        result = ToolResult(text="")
    result.name = tool.name
    result.ran = True
    return result


_LOG_CAP = 8000


def log_detail(arguments: dict | None, result: ToolResult) -> str:
    """What the log pane shows under a tool row. Not sent to Discord."""
    args = arguments if isinstance(arguments, dict) else {}
    try:
        shown = json.dumps(args, ensure_ascii=False)
    except Exception:
        shown = str(args)
    if len(shown) > 500:
        shown = shown[:499].rstrip() + "…"
    body = (result.detail or result.text or "").strip() or "(no output)"
    if len(body) > _LOG_CAP:
        body = body[: _LOG_CAP - 1].rstrip() + "…"
    return f"arguments: {shown}\n\n{body}"


def announcement(name: str) -> str:
    """The only tool-use line shown in chat. Not a raw tool trace."""
    return f"Eche used *{name}*!"


def format_tool_messages(name: str, body: str, *, fence: bool = True) -> list[str]:
    """
    Announcement, then the tool body.

    A fenced body is shown verbatim and does not ping anyone mentioned inside
    it. An unfenced body is the answer itself. Each returned string fits in
    one message.
    """
    line = announcement(name)
    text = (body or "").strip()
    if not text:
        return [line]
    if not fence:
        combined = f"{line}\n{text}"
        if len(combined) <= DISCORD_LIMIT:
            return [combined]
        return [line, *_split_text(text, DISCORD_LIMIT)]

    text = text.replace("```", "'''")
    room = DISCORD_LIMIT - _FENCE_OVERHEAD
    fenced = [f"```\n{piece}\n```" for piece in _split_text(text, room)]
    combined = f"{line}\n{fenced[0]}"
    if len(combined) <= DISCORD_LIMIT:
        return [combined, *fenced[1:]]
    return [line, *fenced]


def _split_text(text: str, size: int) -> list[str]:
    if size < 1:
        size = 1
    parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + size, n)
        if end < n:
            cut = text.rfind("\n", i, end)
            if cut > i:
                end = cut + 1
        if end <= i:
            end = min(i + size, n)
        parts.append(text[i:end])
        i = end
    return parts


def calls_from_model_text(text: str, names: set[str] | None = None) -> list[dict]:
    """
    If the model typed a tool call or the ?context_raw command instead of
    using the tool API, recover the call so it can be executed.

    Ordinary sentences are left alone, including ones that mention context.
    """
    known = names if names is not None else registered_names()
    raw = (text or "").strip()
    if not raw or not known:
        return []

    folded = {n.casefold(): n for n in known}
    blocks = _TOOL_BLOCK.findall(raw)
    found: list[dict] = []
    for block in blocks:
        found.extend(_calls_in_blob(block, folded))
    if found:
        return found

    candidate = raw
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    if candidate.startswith("{") and candidate.endswith("}"):
        found = _calls_in_blob(candidate, folded)
        if found:
            return found

    compact = re.sub(r"\s+", " ", raw).strip()
    if _TYPED_COMMAND.fullmatch(compact) and "context_raw" in folded:
        return [{"name": folded["context_raw"], "arguments": {}}]
    return []


def _calls_in_blob(blob: str, folded: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for match in _FN_TAG.finditer(blob or ""):
        canon = folded.get(match.group(1).casefold())
        if canon:
            out.append({"name": canon, "arguments": {}})
    if out:
        return out

    try:
        data = json.loads(blob)
    except Exception:
        return []

    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool")
        if not isinstance(name, str):
            continue
        canon = folded.get(name.casefold())
        if not canon:
            continue
        args = item.get("arguments", item.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        out.append({"name": canon, "arguments": args})
    return out
