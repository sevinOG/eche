# core/gui_bridge.py
# Bridge between the Discord bot process and the PyQt GUI.
# Protocol: one JSON object per stdout line.
#   {"event": "...", "data": {...}}
# Commands from GUI on stdin (one line each):
#   TOGGLE_COG <module>
#   LIST_COGS
#   STATUS
#   ANNOUNCE <text>
#   PING

from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any, Optional

_bot = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_enabled = False
_reasoning_handler = None


def is_enabled() -> bool:
    import os
    return (
        os.environ.get("ECHE_RUNNING", "").upper() == "BOT"
        or os.environ.get("ECHE_GUI_BRIDGE", "").strip() == "1"
    )


def enable() -> None:
    global _enabled
    _enabled = True


def set_reasoning_handler(handler) -> None:
    """Desktop chat runs in the GUI process, so a trace cannot ride stdout."""
    global _reasoning_handler
    _reasoning_handler = handler


def emit(event: str, data: Optional[dict[str, Any]] = None) -> None:
    if not _enabled and not is_enabled():
        return
    payload = {"event": event, "data": data or {}}
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def log(message: str, channel: str = "global") -> None:
    emit("log", {"message": str(message), "channel": channel})


def tool_log(name: str, user: str, detail: str) -> None:
    """One expandable tool row for the main-window log."""
    emit(
        "tool",
        {
            "name": str(name or ""),
            "user": str(user or ""),
            "detail": str(detail or ""),
        },
    )


def chat(text: str) -> None:
    emit("chat", {"text": str(text)})


def reasoning(provider: str, model: str, text: str) -> None:
    """One reasoning trace for the Provider settings window. Not a chat line."""
    provider = str(provider or "")
    model = str(model or "")
    text = str(text or "")
    emit("reasoning", {"provider": provider, "model": model, "text": text})
    handler = _reasoning_handler
    if handler is None:
        return
    try:
        handler(provider, model, text)
    except Exception:
        pass


def subconscious(text: str) -> None:
    """Deprecated: subconscious panel removed. No-op."""
    return


def unifier(text: str) -> None:
    emit("unifier_update", {"text": str(text)})


def ready(user: str = "") -> None:
    emit("ready", {"user": user})
    log(f"Bot ready as {user}" if user else "Bot ready", channel="bot")


def normalize_cog_module(module: str) -> str:
    """Normalize a cog path to a loadable extension name (cogs.* or core.*)."""
    module = (module or "").strip()
    # Legacy package prefix from older builds
    if module.startswith("eche_ecosystem."):
        module = module[len("eche_ecosystem.") :]
    if module.startswith("cogs.") or module.startswith("core."):
        return module
    return "cogs." + module


def _deny_if_media(module: str) -> str:
    """Return a log line if this module must never load; otherwise empty.

    Hard denylist plus a source scan for leftover media generation.
    """
    from core.media_scan import module_is_blocked

    name = normalize_cog_module(module)
    blocked = module_is_blocked(name) or module_is_blocked(module)
    if blocked:
        return f"Refused to load denied media module: {name} ({blocked})"
    return ""


def _music_busy(bot) -> bool:
    """True if the music cog is actively playing audio."""
    music = bot.get_cog("Music")
    if music is None:
        return False
    try:
        if getattr(music, "playing", False):
            return True
        vc = getattr(music, "vc", None)
        if vc is not None and vc.is_playing():
            return True
    except Exception:
        pass
    return False


def _bot_busy(bot) -> dict[str, Any]:
    """Snapshot of bot activity for safe restart decisions."""
    music_playing = _music_busy(bot)
    # Active views / long games: rough signal via law_manager cases or similar
    other_busy = False
    try:
        law = getattr(bot, "law_manager", None)
        if law is not None and getattr(law, "active_cases", None):
            other_busy = bool(law.active_cases)
    except Exception:
        pass
    return {
        "music_playing": music_playing,
        "other_busy": other_busy,
        "ready": bot.is_ready(),
        "guilds": len(bot.guilds) if bot.is_ready() else 0,
        "latency_ms": round(bot.latency * 1000) if bot.is_ready() else None,
    }


async def _toggle_cog(bot, module: str) -> None:
    full = normalize_cog_module(module)
    loaded = full in bot.extensions
    try:
        if loaded:
            await bot.unload_extension(full)
            log(f"Unloaded cog: {full}", channel="cogs")
            from core.audit import safe_activity
            safe_activity("cog-toggle", module=full, action="unload", outcome="success")
        else:
            await bot.load_extension(full)
            log(f"Loaded cog: {full}", channel="cogs")
            from core.audit import safe_activity
            safe_activity("cog-toggle", module=full, action="load", outcome="success")
    except Exception as e:
        log(f"Cog toggle failed for {full}: {e}", channel="cogs")
        try:
            from core.audit import safe_activity
            safe_activity("cog-toggle", module=full, action="toggle", outcome="error", error_type=type(e).__name__)
        except Exception:
            pass
    await _emit_cog_list(bot)


async def _emit_cog_list(bot) -> None:
    """Send loaded + known extension list to the GUI."""
    loaded = sorted(bot.extensions.keys())
    # Also report music busy state for the GUI status strip
    emit(
        "cog_list",
        {
            "loaded": loaded,
            "count": len(loaded),
            **_bot_busy(bot),
        },
    )


async def _announce(bot, text: str) -> None:
    """Send a short message to the first available text channel of the home guild."""
    import os
    from core.home_id import home_server_id_from_env
    home_id = home_server_id_from_env()
    if not home_id:
        log("ANNOUNCE failed: HOME_SERVER_ID not set", channel="bridge")
        _audit_announce(None, None, "error")
        return
    guild = bot.get_guild(home_id)
    if guild is None:
        log("ANNOUNCE failed: home guild not found", channel="bridge")
        _audit_announce(home_id, None, "error")
        return
    from core.discord_store import find_category

    hidden = find_category(guild)
    hidden_id = hidden.id if hidden is not None else None
    channel = guild.system_channel
    if channel is not None and hidden_id and getattr(channel, "category_id", None) == hidden_id:
        channel = None
    if channel is None:
        for ch in guild.text_channels:
            if hidden_id and getattr(ch, "category_id", None) == hidden_id:
                continue
            if ch.permissions_for(guild.me).send_messages:
                channel = ch
                break
    if channel is None:
        log("ANNOUNCE failed: no sendable channel", channel="bridge")
        _audit_announce(home_id, None, "error")
        return
    try:
        await channel.send(text)
        log(f"Announced: {text}", channel="bridge")
        _audit_announce(home_id, getattr(channel, "id", None), "success")
    except Exception as e:
        log(f"ANNOUNCE failed: {e}", channel="bridge")
        _audit_announce(home_id, getattr(channel, "id", None), "error")


async def _reset_memory(bot) -> None:
    try:
        from core.context_manager import get_home_guild
        from core.memory_wipe import wipe_memory

        guild = get_home_guild(bot)
        report = await wipe_memory(
            guild,
            log=lambda text: log(text, channel="memory"),
        )
        emit("memory_reset", {"ok": report.ok, "summary": report.summary()})
        _audit_stdin(
            "RESET_MEMORY",
            outcome="success" if report.ok else "error",
            guild_id=getattr(guild, "id", None),
        )
    except Exception as e:
        log(f"Memory reset failed: {e}", channel="memory")
        _audit_stdin("RESET_MEMORY", outcome="error", error_type=type(e).__name__)


async def _unlock_memory(bot) -> None:
    try:
        from core.context_manager import get_home_guild
        from core.memory_access import unlock_memory

        guild = get_home_guild(bot)
        report = await unlock_memory(
            guild,
            log=lambda text: log(text, channel="memory"),
        )
        emit("memory_access", {"ok": report.ok, "summary": report.summary()})
        # Action and ids only; never the report text.
        _audit_stdin(
            "UNLOCK_MEMORY",
            outcome="success" if report.ok else "error",
            guild_id=getattr(guild, "id", None),
        )
    except Exception as e:
        log(f"Memory access failed: {e}", channel="memory")
        _audit_stdin("UNLOCK_MEMORY", outcome="error", error_type=type(e).__name__)


def _audit_announce(guild_id, channel_id, outcome: str) -> None:
    # Ids and the action only. Never the announcement text.
    _audit_stdin("ANNOUNCE", outcome=outcome, guild_id=guild_id, channel_id=channel_id)


def _audit_stdin(action: str, *, outcome: str = "success", **extra) -> None:
    # Ids and the action only. Never the command payload or secret values.
    try:
        from core.audit import safe_activity
        safe_activity("stdin", action=action, outcome=outcome, **extra)
    except Exception:
        pass


async def _handle_line(bot, line: str) -> None:
    line = line.strip()
    if not line:
        return

    upper = line.upper()

    if upper == "PING":
        emit("log", {"message": "PONG", "channel": "bridge"})
        _audit_stdin("PING")
        return

    if upper == "LIST_COGS" or upper == "COG_LIST":
        await _emit_cog_list(bot)
        _audit_stdin("LIST_COGS" if upper == "LIST_COGS" else "COG_LIST")
        return

    if upper == "STATUS":
        emit("status", _bot_busy(bot))
        _audit_stdin("STATUS")
        return

    if upper == "RESET_MEMORY":
        # Only the desktop app writes this pipe. There is no Discord command.
        await _reset_memory(bot)
        _audit_stdin("RESET_MEMORY")
        return

    if upper == "UNLOCK_MEMORY":
        # Only the desktop app writes this pipe. There is no Discord command.
        await _unlock_memory(bot)
        _audit_stdin("UNLOCK_MEMORY")
        return

    if upper.startswith("ANNOUNCE "):
        text = line[len("ANNOUNCE "):].strip()
        if text:
            await _announce(bot, text)
        _audit_stdin("ANNOUNCE")
        return

    if upper.startswith("TOGGLE_COG "):
        module = line[len("TOGGLE_COG "):].strip()
        denied = _deny_if_media(module)
        if denied:
            log(denied, channel="cogs")
            await _emit_cog_list(bot)
            _audit_stdin("TOGGLE_COG", outcome="error")
            return
        await _toggle_cog(bot, module)
        _audit_stdin("TOGGLE_COG")
        return

    if upper.startswith("LOAD_COG "):
        module = normalize_cog_module(line[len("LOAD_COG "):].strip())
        denied = _deny_if_media(module)
        if denied:
            log(denied, channel="cogs")
            await _emit_cog_list(bot)
            _audit_stdin("LOAD_COG", outcome="error")
            return
        try:
            if module in bot.extensions:
                await bot.reload_extension(module)
                log(f"Reloaded cog: {module}", channel="cogs")
                from core.audit import safe_activity
                safe_activity("cog-toggle", module=module, action="reload", outcome="success")
            else:
                await bot.load_extension(module)
                log(f"Loaded cog: {module}", channel="cogs")
                from core.audit import safe_activity
                safe_activity("cog-toggle", module=module, action="load", outcome="success")
        except Exception as e:
            log(f"Load cog failed for {module}: {e}", channel="cogs")
            try:
                from core.audit import safe_activity
                safe_activity("cog-toggle", module=module, action="load", outcome="error", error_type=type(e).__name__)
            except Exception:
                pass
        await _emit_cog_list(bot)
        _audit_stdin("LOAD_COG")
        return

    if upper.startswith("UNLOAD_COG "):
        module = normalize_cog_module(line[len("UNLOAD_COG "):].strip())
        try:
            if module in bot.extensions:
                await bot.unload_extension(module)
                log(f"Unloaded cog: {module}", channel="cogs")
                from core.audit import safe_activity
                safe_activity("cog-toggle", module=module, action="unload", outcome="success")
            else:
                log(f"Cog not loaded: {module}", channel="cogs")
        except Exception as e:
            log(f"Unload cog failed for {module}: {e}", channel="cogs")
            try:
                from core.audit import safe_activity
                safe_activity("cog-toggle", module=module, action="unload", outcome="error", error_type=type(e).__name__)
            except Exception:
                pass
        await _emit_cog_list(bot)
        _audit_stdin("UNLOAD_COG")
        return

    log(f"Unknown stdin command: {line}", channel="bridge")


def _stdin_thread(bot, loop: asyncio.AbstractEventLoop) -> None:
    log("Stdin command listener started", channel="bridge")
    while True:
        try:
            line = sys.stdin.readline()
        except Exception as e:
            log(f"Stdin read error: {e}", channel="bridge")
            break

        if line == "":
            log("Stdin closed (EOF)", channel="bridge")
            break

        try:
            asyncio.run_coroutine_threadsafe(_handle_line(bot, line), loop)
        except Exception as e:
            log(f"Failed to schedule command: {e}", channel="bridge")


def attach(bot) -> None:
    global _bot, _loop, _enabled
    _bot = bot
    _enabled = True

    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            _loop = asyncio.get_event_loop()
        except RuntimeError:
            _loop = None

    if _loop is None:
        log("No event loop yet; stdin listener deferred", channel="bridge")
        return

    t = threading.Thread(
        target=_stdin_thread,
        args=(bot, _loop),
        name="eche-gui-stdin",
        daemon=True,
    )
    t.start()
    log("GUI bridge attached", channel="bridge")

    # Push initial cog list shortly after attach
    async def _initial():
        await asyncio.sleep(1.0)
        await _emit_cog_list(bot)

    try:
        asyncio.run_coroutine_threadsafe(_initial(), _loop)
    except Exception:
        pass
