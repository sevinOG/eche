# core/tools_admin.py
# Owner-only moderation tools. They are registered always, and the admin
# toggle plus the owner check decide whether the model can see or run them.

from __future__ import annotations

import re
from datetime import timedelta

import discord

from core.debuglog import dprint
from core.tools import ACCESS_OWNER, Tool, ToolContext, ToolResult, register

_SNOWFLAKE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")
_DURATION = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*"
    r"(s|sec|secs|second|seconds|m|min|mins|minute|minutes|"
    r"h|hr|hrs|hour|hours|d|day|days)\s*$",
    re.IGNORECASE,
)
_CLEAR = {"0", "off", "clear", "remove", "none", "lift", "stop", "untimeout"}
_EVERYONE = {"all", "everyone", "here", "@everyone", "@here", "server", "the server"}
_MAX_TIMEOUT = 28 * 24 * 60 * 60
_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}
_UNIT_LABEL = {
    1: ("second", "seconds"),
    60: ("minute", "minutes"),
    3600: ("hour", "hours"),
    86400: ("day", "days"),
}

_USER = {
    "type": "string",
    "description": "One member in this server: their user id, a mention, or their exact username. Never everyone.",
}
_REASON = {
    "type": "string",
    "description": "Short reason the owner gave. Leave empty if they did not give one.",
}


def parse_timeout(text: str) -> tuple[timedelta | None, str | None, str]:
    """
    (duration, error, label).

    A duration of None with no error means remove the timeout.
    """
    raw = " ".join((text or "").replace("\u2019", "'").split()).casefold()
    if not raw:
        return None, "Tell me a duration like 10m, 1h, or 2d.", ""
    if raw in _CLEAR:
        return None, None, "removed"
    match = _DURATION.fullmatch(raw)
    if match:
        amount = float(match.group(1))
        unit = _UNITS[match.group(2).casefold()]
        seconds = amount * unit
    elif raw.isdigit():
        amount = int(raw)
        unit = 60
        seconds = amount * unit
    else:
        return None, "Tell me a duration like 10m, 1h, or 2d.", ""
    if amount <= 0:
        return None, None, "removed"
    if seconds > _MAX_TIMEOUT:
        return None, "A timeout can be at most 28 days.", ""
    singular, plural = _UNIT_LABEL[unit]
    shown = int(amount) if float(amount).is_integer() else amount
    word = singular if shown == 1 else plural
    return timedelta(seconds=seconds), None, f"{shown} {word}"


def _target_text(arguments: dict) -> str:
    for key in ("user", "member", "name", "username", "id"):
        value = arguments.get(key)
        if isinstance(value, bool):
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _as_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().casefold()
    if text in {"0", "false", "no", "off", "unmute", "unmuted"}:
        return False
    if text in {"1", "true", "yes", "on", "mute", "muted"}:
        return True
    return default


def _audit_reason(author, reason) -> str:
    who = getattr(author, "name", None) or str(getattr(author, "id", "owner"))
    extra = " ".join(str(reason or "").split())
    if len(extra) > 180:
        extra = extra[:179].rstrip() + "…"
    line = f"Eche for {who}"
    if extra:
        line = f"{line}: {extra}"
    return line[:512]


def _label(member) -> str:
    name = getattr(member, "name", None) or "that member"
    user_id = getattr(member, "id", None)
    if user_id:
        return f"{name} ({user_id})"
    return str(name)


async def resolve_member(guild, text: str):
    """Return (member, error). member is None when error is set."""
    raw = (text or "").strip()
    if not raw or raw.casefold() in _EVERYONE:
        return None, "Name one member. I won't do that to the whole server."

    snowflake = None
    if raw.isdigit() or raw.startswith("<@"):
        match = _SNOWFLAKE.search(raw)
        if match:
            snowflake = int(match.group(1))
    if snowflake is not None:
        member = guild.get_member(snowflake)
        if member is None:
            fetch = getattr(guild, "fetch_member", None)
            if fetch is not None:
                try:
                    member = await fetch(snowflake)
                except Exception:
                    member = None
        if member is None:
            return None, "I couldn't find that member in this server."
        return member, None

    wanted = raw.casefold()
    found = {}
    for member in getattr(guild, "members", []) or []:
        names = (
            getattr(member, "name", None),
            getattr(member, "global_name", None),
            getattr(member, "nick", None),
        )
        if any((item or "").casefold() == wanted for item in names):
            found[member.id] = member
    if len(found) == 1:
        return next(iter(found.values())), None
    if len(found) > 1:
        return None, "More than one member has that name. Use their id."
    return None, "I couldn't find that member in this server."


def _blocked(ctx: ToolContext, guild, member) -> str | None:
    me = getattr(guild, "me", None)
    bot_user = getattr(ctx.bot, "user", None)
    bot_id = getattr(bot_user, "id", None)
    author = getattr(ctx.message, "author", None)
    author_id = getattr(author, "id", None)
    target_id = getattr(member, "id", None)
    if bot_id and target_id == bot_id:
        return "I won't do that to myself."
    if author_id and target_id == author_id:
        return "I won't do that to you."
    try:
        from core.admin_tools import configured_owner_ids
        owner_ids = set(configured_owner_ids())
    except Exception:
        owner_ids = set()
    if target_id in owner_ids:
        return "I won't do that to an owner."
    if getattr(guild, "owner_id", None) == target_id:
        return "That's the server owner. Discord won't allow it."
    if me is None:
        return "I can't see my own member record in this server."
    if getattr(guild, "owner_id", None) != getattr(me, "id", None):
        try:
            if member.top_role >= me.top_role:
                return "Their role is above mine, so Discord won't let me."
        except Exception:
            pass
    return None


def _missing_perm(guild, perm: str, label: str) -> str | None:
    me = getattr(guild, "me", None)
    perms = getattr(me, "guild_permissions", None) if me is not None else None
    if perms is None:
        return None
    if not getattr(perms, perm, False):
        return f"I need the {label} permission in this server."
    return None


async def _member_action(ctx: ToolContext, arguments: dict, *, perm: str, perm_label: str):
    """Shared checks. Returns (guild, member, reason) or (None, None, ToolResult)."""
    message = ctx.message
    guild = getattr(message, "guild", None)
    if guild is None:
        return None, None, ToolResult(text="Ask for that in the server, not in a DM.")
    missing = _missing_perm(guild, perm, perm_label)
    if missing:
        return None, None, ToolResult(text=missing)
    target = _target_text(arguments or {})
    if not target:
        return None, None, ToolResult(text="Name the member you want.")
    member, err = await resolve_member(guild, target)
    if member is None:
        return None, None, ToolResult(text=err or "I couldn't find that member in this server.")
    blocked = _blocked(ctx, guild, member)
    if blocked:
        return None, None, ToolResult(text=blocked)
    reason = _audit_reason(getattr(message, "author", None), (arguments or {}).get("reason"))
    return guild, member, reason


def _discord_failed(action: str, exc: Exception) -> ToolResult:
    dprint(f"[tools] {action} failed: {exc}")
    if isinstance(exc, discord.Forbidden):
        return ToolResult(text="Discord refused that. My role needs to be higher, with the right permission.")
    return ToolResult(text="Discord didn't accept that.")


async def kick_member(ctx: ToolContext, arguments: dict) -> ToolResult:
    guild, member, reason = await _member_action(
        ctx, arguments, perm="kick_members", perm_label="Kick Members"
    )
    if guild is None:
        return reason
    try:
        await member.kick(reason=reason)
    except (discord.Forbidden, discord.HTTPException) as exc:
        return _discord_failed("kick", exc)
    return ToolResult(text=f"Kicked {_label(member)}.")


async def ban_member(ctx: ToolContext, arguments: dict) -> ToolResult:
    message = ctx.message
    guild = getattr(message, "guild", None)
    if guild is None:
        return ToolResult(text="Ask for that in the server, not in a DM.")
    missing = _missing_perm(guild, "ban_members", "Ban Members")
    if missing:
        return ToolResult(text=missing)
    target = _target_text(arguments or {})
    if not target:
        return ToolResult(text="Name the member you want.")
    member, err = await resolve_member(guild, target)
    if err and err.startswith("Name one member"):
        return ToolResult(text=err)
    if member is not None:
        blocked = _blocked(ctx, guild, member)
        if blocked:
            return ToolResult(text=blocked)
    elif _SNOWFLAKE.search(target) and (target.isdigit() or target.startswith("<@")):
        user_id = int(_SNOWFLAKE.search(target).group(1))
        bot_id = getattr(getattr(ctx.bot, "user", None), "id", None)
        author_id = getattr(getattr(message, "author", None), "id", None)
        try:
            from core.admin_tools import configured_owner_ids
            owner_ids = set(configured_owner_ids())
        except Exception:
            owner_ids = set()
        if user_id in owner_ids or user_id in {bot_id, author_id, getattr(guild, "owner_id", None)}:
            return ToolResult(text="I won't ban that user.")
        member = discord.Object(id=user_id)
    else:
        return ToolResult(text=err or "I couldn't find that member in this server.")

    days = arguments.get("delete_days", 0) if arguments else 0
    try:
        days = int(days or 0)
    except (TypeError, ValueError):
        days = 0
    days = max(0, min(7, days))
    reason = _audit_reason(getattr(message, "author", None), (arguments or {}).get("reason"))
    try:
        await guild.ban(member, reason=reason, delete_message_seconds=days * 86400)
    except (discord.Forbidden, discord.HTTPException) as exc:
        return _discord_failed("ban", exc)
    return ToolResult(text=f"Banned {_label(member)}.")


async def timeout_member(ctx: ToolContext, arguments: dict) -> ToolResult:
    guild, member, reason = await _member_action(
        ctx, arguments, perm="moderate_members", perm_label="Moderate Members"
    )
    if guild is None:
        return reason
    delta, error, label = parse_timeout(str((arguments or {}).get("duration") or ""))
    if error:
        return ToolResult(text=error)
    try:
        await member.timeout(delta, reason=reason)
    except (discord.Forbidden, discord.HTTPException) as exc:
        return _discord_failed("timeout", exc)
    if delta is None:
        return ToolResult(text=f"Removed the timeout on {_label(member)}.")
    return ToolResult(text=f"Timed out {_label(member)} for {label}.")


async def mute_member(ctx: ToolContext, arguments: dict) -> ToolResult:
    guild, member, reason = await _member_action(
        ctx, arguments, perm="mute_members", perm_label="Mute Members"
    )
    if guild is None:
        return reason
    muted = _as_bool((arguments or {}).get("muted"), True)
    voice = getattr(member, "voice", None)
    if voice is None or getattr(voice, "channel", None) is None:
        return ToolResult(
            text="They're not in a voice channel, so a server mute won't apply. A timeout will stop them from chatting."
        )
    try:
        await member.edit(mute=muted, reason=reason)
    except (discord.Forbidden, discord.HTTPException) as exc:
        return _discord_failed("mute", exc)
    if muted:
        return ToolResult(text=f"Server muted {_label(member)} in voice.")
    return ToolResult(text=f"Unmuted {_label(member)} in voice.")


def register_admin_tools() -> None:
    register(Tool(
        name="kick",
        description=(
            "Kick one member from this server. Call only when the owner explicitly "
            "asks you to kick that member. Do not kick the owner, yourself, or everyone."
        ),
        handler=kick_member,
        parameters={
            "type": "object",
            "properties": {"user": _USER, "reason": _REASON},
            "required": ["user"],
        },
        access=ACCESS_OWNER,
    ))
    register(Tool(
        name="ban",
        description=(
            "Ban one member from this server. Call only when the owner explicitly "
            "asks you to ban that member. delete_days is how many days of their "
            "messages to delete, from 0 to 7. Use 0 unless they asked to delete messages."
        ),
        handler=ban_member,
        parameters={
            "type": "object",
            "properties": {
                "user": _USER,
                "reason": _REASON,
                "delete_days": {
                    "type": "integer",
                    "description": "Days of messages to delete, 0 through 7. Default 0.",
                },
            },
            "required": ["user"],
        },
        access=ACCESS_OWNER,
    ))
    register(Tool(
        name="timeout",
        description=(
            "Timeout one member so they cannot chat. Call only when the owner "
            "explicitly asks, and only with a duration they gave, such as 10m, 1h, "
            "or 2d. Maximum 28 days. Use duration off to remove a timeout."
        ),
        handler=timeout_member,
        parameters={
            "type": "object",
            "properties": {
                "user": _USER,
                "duration": {
                    "type": "string",
                    "description": "How long, such as 10m, 1h, or 2d. Use off to remove it.",
                },
                "reason": _REASON,
            },
            "required": ["user", "duration"],
        },
        access=ACCESS_OWNER,
    ))
    register(Tool(
        name="mute",
        description=(
            "Server-mute or unmute one member in voice. They must be in a voice "
            "channel. Call only when the owner explicitly asks. This does not stop "
            "them from typing; use timeout for that. Set muted to false to unmute."
        ),
        handler=mute_member,
        parameters={
            "type": "object",
            "properties": {
                "user": _USER,
                "muted": {
                    "type": "boolean",
                    "description": "True to server-mute, false to unmute. Default true.",
                },
                "reason": _REASON,
            },
            "required": ["user"],
        },
        access=ACCESS_OWNER,
    ))


register_admin_tools()
