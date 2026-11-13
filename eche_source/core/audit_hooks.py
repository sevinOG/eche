# Auto-instrument listeners and View/Modal callbacks so a success-path
# audit record is written when they run. Completeness tests discover the
# same hooks at runtime.

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

# Error-only hooks are not required to write a success-path record.
LISTENER_SUCCESS_ALLOWLIST: dict[str, str] = {
    "on_error": "error-only hook; success is not applicable",
    "on_command_error": "error-only hook; success is on_command_completion",
    "on_app_command_error": "error-only hook; success is on_app_command_completion",
}

# Background / GUI names discovered by AST that are not invoked as a live
# loop. Each entry must have a reason. Names with a probe in
# BACKGROUND_PROBES are checked by calling the probe instead.
BACKGROUND_ALLOWLIST: dict[str, str] = {}


def _safe_log(kind: str, event: str, **extra: Any) -> None:
    try:
        from core.audit import activity

        activity(event, kind=kind, outcome=extra.pop("outcome", "success"), **extra)
    except Exception:
        pass


def _ids_from_args(args: tuple[Any, ...]) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    for item in args:
        author = getattr(item, "author", None) or getattr(item, "user", None)
        guild = getattr(item, "guild", None)
        channel = getattr(item, "channel", None)
        if author is not None or guild is not None or channel is not None:
            if getattr(author, "id", None) is not None:
                ids["user_id"] = author.id
            if getattr(guild, "id", None) is not None:
                ids["guild_id"] = guild.id
            if getattr(channel, "id", None) is not None:
                ids["channel_id"] = channel.id
            if ids:
                return ids
        # Guild object passed directly (join/leave).
        if getattr(item, "members", None) is not None and getattr(item, "id", None) is not None:
            ids["guild_id"] = item.id
            return ids
        # Member / voice state.
        if getattr(item, "guild", None) is not None and getattr(item, "id", None) is not None:
            ids.setdefault("user_id", item.id)
            g = getattr(item, "guild", None)
            if getattr(g, "id", None) is not None:
                ids["guild_id"] = g.id
            return ids
    return ids


def wrap_callable(fn: Callable, event: str, kind: str) -> Callable:
    if fn is None or getattr(fn, "_audit_wrapped", False):
        return fn

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapped(*args, **kwargs):
            _safe_log(kind, event, method=getattr(fn, "__name__", event), **_ids_from_args(args))
            return await fn(*args, **kwargs)

        async_wrapped._audit_wrapped = True  # type: ignore[attr-defined]
        return async_wrapped

    @functools.wraps(fn)
    def sync_wrapped(*args, **kwargs):
        _safe_log(kind, event, method=getattr(fn, "__name__", event), **_ids_from_args(args))
        return fn(*args, **kwargs)

    sync_wrapped._audit_wrapped = True  # type: ignore[attr-defined]
    return sync_wrapped


def instrument_listeners(bot) -> None:
    """Wrap Eche.on_* hooks and every loaded cog listener."""
    try:
        from core.bot import Eche
    except Exception:
        Eche = type(bot)
    for name in dir(Eche):
        if not name.startswith("on_") or name in LISTENER_SUCCESS_ALLOWLIST:
            continue
        fn = getattr(Eche, name, None)
        if not callable(fn) or getattr(fn, "_audit_wrapped", False):
            continue
        try:
            setattr(Eche, name, wrap_callable(fn, name, "listener"))
        except Exception:
            pass
    for cog in list(getattr(bot, "cogs", {}) and bot.cogs.values() or []):
        cls = type(cog)
        listeners = list(getattr(cls, "__cog_listeners__", []) or [])
        if not listeners:
            try:
                listeners = [(ev, fn.__name__) for ev, fn in cog.get_listeners()]
            except Exception:
                listeners = []
        for ev, method_name in listeners:
            if ev in LISTENER_SUCCESS_ALLOWLIST:
                continue
            fn = getattr(cls, method_name, None)
            if not callable(fn) or getattr(fn, "_audit_wrapped", False):
                continue
            try:
                setattr(cls, method_name, wrap_callable(fn, ev, "listener"))
            except Exception:
                pass


def _all_subclasses(cls) -> list[type]:
    out: list[type] = []
    try:
        children = list(cls.__subclasses__())
    except Exception:
        return out
    for sub in children:
        out.append(sub)
        out.extend(_all_subclasses(sub))
    return out


def iter_view_callbacks() -> list[tuple[type, str, Callable]]:
    """Discover View/Modal on_submit, on_timeout, and component callbacks."""
    import discord
    from discord.ui import Modal, View

    found: list[tuple[type, str, Callable]] = []
    seen: set[tuple[type, str]] = set()
    for cls in _all_subclasses(View) + _all_subclasses(Modal):
        # Class-defined hooks.
        for name in ("on_submit", "on_timeout"):
            fn = vars(cls).get(name)
            if callable(fn) and (cls, name) not in seen:
                seen.add((cls, name))
                found.append((cls, name, fn))
        for name, attr in list(vars(cls).items()):
            callback = getattr(attr, "callback", None)
            if callable(callback) and (cls, name) not in seen:
                # Skip Item objects that point at the default empty callback.
                if getattr(callback, "__name__", "") in {"_callback", "callback"} and callback.__qualname__.startswith("Item"):
                    continue
                seen.add((cls, name))
                found.append((cls, name, callback))
    return found


def instrument_views() -> None:
    import discord  # noqa: F401
    from discord.ui import Item

    for cls, name, fn in iter_view_callbacks():
        if getattr(fn, "_audit_wrapped", False):
            continue
        wrapped = wrap_callable(fn, f"{cls.__name__}.{name}", "view")
        attr = vars(cls).get(name)
        if callable(attr) and attr is fn:
            try:
                setattr(cls, name, wrapped)
            except Exception:
                pass
        elif hasattr(attr, "callback"):
            try:
                attr.callback = wrapped
            except Exception:
                pass


def instrument_bot(bot) -> None:
    instrument_listeners(bot)
    instrument_views()


def discover_listener_events(bot, eche_cls=None) -> set[str]:
    events: set[str] = set()
    extra = getattr(bot, "extra_events", {}) or {}
    for ev, funcs in extra.items():
        if funcs:
            events.add(ev)
    for cog in getattr(bot, "cogs", {}) and bot.cogs.values() or []:
        try:
            for ev, _fn in cog.get_listeners():
                events.add(ev)
        except Exception:
            pass
    if eche_cls is not None:
        for name in dir(eche_cls):
            if name.startswith("on_") and callable(getattr(eche_cls, name, None)):
                if name in {
                    "on_command_completion",
                    "on_command_error",
                    "on_message",
                    "on_message_edit",
                    "on_app_command_completion",
                    "on_app_command_error",
                    "on_error",
                    "on_ready",
                    "on_disconnect",
                    "on_resumed",
                    "on_guild_join",
                    "on_guild_remove",
                    "on_voice_state_update",
                } or name in extra:
                    events.add(name)
    return events
