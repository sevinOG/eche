"""User-facing progress lines: "Eche used *tool*!", "Making that!", retrying.

Owner-approved 2026-11-18 21:20 ET: each tool use is its own message so
users can see Eche is working. The lines are fixed strings built from the
tool's registered name only. Never prompt text, arguments, filtered content
or secrets. Rate-safe: a repeat of the same line in the same channel within
COOLDOWN_SECONDS is skipped, and a channel gets at most CHANNEL_MAX lines per
CHANNEL_WINDOW seconds. A failed send never stops the tool.
"""

from __future__ import annotations

import time

COOLDOWN_SECONDS = 10.0
CHANNEL_WINDOW = 60.0
CHANNEL_MAX = 6

# Friendlier names for the registry tools. Anything else shows its
# registered name, which is a fixed identifier, never user text.
_DISPLAY = {
    "duckduckgo": "search",
    "context_raw": "context",
}

# "Making that!" was removed (owner 2026-11-18 22:06 ET): the separate
# "Eche used *asset*!" line already says it.
RETRYING = "That one didn't pass, trying again..."

_sent: dict[tuple[str, str], float] = {}
_window: dict[str, list[float]] = {}


def display_name(name: str) -> str:
    key = (name or "").strip()
    shown = _DISPLAY.get(key, key) or "a tool"
    # Registered names are short identifiers; keep it that way.
    return "".join(ch for ch in shown if ch.isalnum() or ch in "_-")[:32] or "a tool"


def tool_line(name: str) -> str:
    return f"Eche used *{display_name(name)}*!"


def _allowed(channel_id: str, line: str, now: float) -> bool:
    last = _sent.get((channel_id, line))
    if last is not None and now - last < COOLDOWN_SECONDS:
        return False
    recent = [t for t in _window.get(channel_id, []) if now - t < CHANNEL_WINDOW]
    if len(recent) >= CHANNEL_MAX:
        _window[channel_id] = recent
        return False
    recent.append(now)
    _window[channel_id] = recent
    _sent[(channel_id, line)] = now
    return True


async def post_line(channel, line: str, *, kind: str = "tool", tool: str = "") -> bool:
    """Send one fixed progress line to the channel. Returns True if sent."""
    if channel is None or not line:
        return False
    channel_id = str(getattr(channel, "id", "") or "")
    if not _allowed(channel_id, line, time.monotonic()):
        _log(channel, "skipped", kind, tool)
        return False
    try:
        try:
            import discord

            mentions = discord.AllowedMentions.none()
        except Exception:
            mentions = None
        if mentions is not None:
            await channel.send(line, allowed_mentions=mentions)
        else:
            await channel.send(line)
    except Exception as exc:
        _log(channel, "error", kind, tool, error_type=type(exc).__name__)
        return False
    _log(channel, "success", kind, tool)
    return True


async def announce_tool(channel, name: str) -> bool:
    return await post_line(channel, tool_line(name), kind="tool", tool=display_name(name))


def _log(channel, outcome: str, kind: str, tool: str, **extra) -> None:
    try:
        from core.audit import safe_activity

        guild = getattr(channel, "guild", None)
        safe_activity(
            "announce",
            outcome=outcome,
            kind=kind,
            tool=tool,
            guild_id=getattr(guild, "id", None),
            channel_id=getattr(channel, "id", None),
            **extra,
        )
    except Exception:
        pass
