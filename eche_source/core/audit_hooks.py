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
BACKGROUND_ALLOWLIST: dict[str, str] = {
    "_watch_asset_mode": "polls read_asset_mode; toggle changes are audited and probed via save_all",
}

# Qt slots that are UI chrome only. Operator slots must be reviewed and probed
# instead. Each entry needs a reason.
QT_SLOT_ALLOWLIST: dict[str, str] = {
    "_bootstrap": "window layout bootstrap",
    "_browse_data_folder": "folder picker dialog",
    "_browse_files": "file picker; install is probed via _on_drop_files",
    "_browse_project": "folder picker dialog",
    "_copy": "copy-to-clipboard UI",
    "_on_admin_tools_toggled": "persist is probed via save_all",
    "_on_default_tools_toggled": "persist is probed via save_all",
    "_on_fetched": "async fetch finished UI callback",
    "_on_finished": "worker finished UI callback",
    "_on_item_clicked": "list navigation",
    "_on_kind_selected": "dropdown selection UI",
    "_on_local_err": "desktop chat error display",
    "_on_local_reply": "desktop chat reply display; turns are probed via reply_local",
    "_on_local_send": "desktop chat send click; turns are probed via reply_local",
    "_on_local_worker_done": "desktop chat worker finished UI",
    "_on_memory_access_done": "memory-access worker finished UI; action is probed via unlock_memory",
    "_on_nav": "settings section navigation",
    "_on_provider_backend_changed": "provider dropdown UI",
    "_on_reader_finished": "bot log reader finished UI",
    "_on_reasoning_mode_picked": "reasoning dropdown UI",
    "_on_reasoning_toggled": "reasoning checkbox UI",
    "_on_reset_memory_done": "memory-wipe worker finished UI; action is probed via wipe_memory",
    "_on_saved": "economy-window UI callback; bank write is probed via _save",
    "_on_security_admin_toggled": "persist is probed via save_all",
    "_on_settings_saved": "settings save finished UI; persist is probed via save_all",
    "_on_update_log": "updater log display",
    "_on_update_mode": "update-mode dropdown UI",
    "_on_update_ready": "updater finished UI; handoff is probed via launch_handoff",
    "_on_user": "user-picker UI",
    "_on_users": "user-list UI",
    "_open_cookies_folder": "opens a folder in the file manager",
    "_open_secrets_folder": "opens a folder in the file manager",
    "_redetect_path": "path rediscovery UI",
    "_refresh_ollama_models": "model list refresh UI",
    "_reset_context_caps": "resets numeric fields in the form",
    "_show_format_help": "help dialog",
    "_show_help": "help dialog",
    "_sync_asset_toggles": "checkbox coupling UI; persist is probed via save_all",
    "_sync_editor_from_spin": "spinbox-to-editor UI sync",
    "_sync_spin_from_editor": "editor-to-spinbox UI sync",
    "_tick": "UI timer tick",
    "_toggle_secret_visibility": "show/hide a field; does not log the value",
    "accept": "dialog accept",
    "clear": "clears a text field",
    "close": "closes a dialog",
    "fetch_pin": "loads a pin for display; writes are probed via write_pinned",
    "handle_bot_output": "bot log display",
    "load_file": "loads a prompt file for display; writes are probed via save_*_prompt",
    "note_reasoning": "reasoning pane display",
    "on_cog_manager_clicked": "opens the Cog Manager window",
    "on_donate_clicked": "opens the donate dialog",
    "on_settings_clicked": "opens the Settings window",
    "on_unifier_content_saved": "unifier editor saved UI notice",
    "on_update_clicked": "opens the updater flow; handoff is probed via launch_handoff",
    "open_context_editor": "opens the context editor window",
    "open_personality_window": "opens the personality editor; saves are probed via save_personality_prompt",
    "open_unifier_window": "opens the unifier window; saves are probed via on_save_clicked",
    "open_economy_window": "opens the economy window",
    "open_provider_window": "opens the provider editor; saves are probed via save_provider_prompt",
    "open_reasoning_window": "opens the reasoning window",
    "open_summarizer_window": "opens the summarizer editor; saves are probed via save_summarizer_prompt",
    "reject": "dialog reject",
    "request_refresh": "requests a UI refresh",
    "reset_default": "restores editor defaults for display",
    "reset_pin": "pin reset UI; KIND_BOT writes are probed via write_pinned",
    "save": "settings persist is probed via save_all",
    "save_pin": "pin save UI; KIND_BOT writes are probed via write_pinned",
    "setValue": "numeric field write in a form",
}

# stdin commands that do not need a success-path record. Each entry needs a
# reason. Operator commands must be reviewed and probed instead.
STDIN_ALLOWLIST: dict[str, str] = {}


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
            ids = _ids_from_args(args)
            method = getattr(fn, "__name__", event)
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                _safe_log(
                    kind, event, outcome="error",
                    error_type=type(exc).__name__, method=method, **ids,
                )
                raise
            _safe_log(kind, event, outcome="success", method=method, **ids)
            return result

        async_wrapped._audit_wrapped = True  # type: ignore[attr-defined]
        return async_wrapped

    @functools.wraps(fn)
    def sync_wrapped(*args, **kwargs):
        ids = _ids_from_args(args)
        method = getattr(fn, "__name__", event)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            _safe_log(
                kind, event, outcome="error",
                error_type=type(exc).__name__, method=method, **ids,
            )
            raise
        _safe_log(kind, event, outcome="success", method=method, **ids)
        return result

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


def _is_external_hook(fn) -> bool:
    """True for asyncio/discord internals, which are not Eche entry points."""
    mod = getattr(fn, "__module__", "") or ""
    return (
        mod == "asyncio" or mod.startswith("asyncio.")
        or mod == "discord" or mod.startswith("discord.")
        or mod.startswith("aiohttp.")
    )


def discover_listener_events(bot, eche_cls=None) -> set[str]:
    events: set[str] = set()
    extra = getattr(bot, "extra_events", {}) or {}
    for ev, funcs in extra.items():
        live = [fn for fn in (funcs or []) if not _is_external_hook(fn)]
        if live:
            events.add(ev)
    for cog in getattr(bot, "cogs", {}) and bot.cogs.values() or []:
        try:
            for ev, fn in cog.get_listeners():
                if not _is_external_hook(fn):
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
