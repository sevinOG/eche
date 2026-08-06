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


def is_enabled() -> bool:
    import os
    return (
        os.environ.get("ECHE_RUNNING", "").upper() == "BOT"
        or os.environ.get("ECHE_GUI_BRIDGE", "").strip() == "1"
    )


def enable() -> None:
    global _enabled
    _enabled = True


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


def chat(text: str) -> None:
    emit("chat", {"text": str(text)})


def subconscious(text: str) -> None:
    emit("subconscious_update", {"text": str(text)})


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
        else:
            await bot.load_extension(full)
            log(f"Loaded cog: {full}", channel="cogs")
    except Exception as e:
        log(f"Cog toggle failed for {full}: {e}", channel="cogs")
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
    home_id = os.getenv("HOME_SERVER_ID")
    if not home_id:
        log("ANNOUNCE failed: HOME_SERVER_ID not set", channel="bridge")
        return
    guild = bot.get_guild(int(home_id))
    if guild is None:
        log("ANNOUNCE failed: home guild not found", channel="bridge")
        return
    channel = guild.system_channel
    if channel is None:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                channel = ch
                break
    if channel is None:
        log("ANNOUNCE failed: no sendable channel", channel="bridge")
        return
    try:
        await channel.send(text)
        log(f"Announced: {text}", channel="bridge")
    except Exception as e:
        log(f"ANNOUNCE failed: {e}", channel="bridge")


async def _handle_line(bot, line: str) -> None:
    line = line.strip()
    if not line:
        return

    upper = line.upper()

    if upper == "PING":
        emit("log", {"message": "PONG", "channel": "bridge"})
        return

    if upper == "LIST_COGS" or upper == "COG_LIST":
        await _emit_cog_list(bot)
        return

    if upper == "STATUS":
        emit("status", _bot_busy(bot))
        return

    if upper.startswith("ANNOUNCE "):
        text = line[len("ANNOUNCE "):].strip()
        if text:
            await _announce(bot, text)
        return

    if upper.startswith("TOGGLE_COG "):
        module = line[len("TOGGLE_COG "):].strip()
        await _toggle_cog(bot, module)
        return

    if upper.startswith("LOAD_COG "):
        module = normalize_cog_module(line[len("LOAD_COG "):].strip())
        try:
            if module in bot.extensions:
                await bot.reload_extension(module)
                log(f"Reloaded cog: {module}", channel="cogs")
            else:
                await bot.load_extension(module)
                log(f"Loaded cog: {module}", channel="cogs")
        except Exception as e:
            log(f"Load cog failed for {module}: {e}", channel="cogs")
        await _emit_cog_list(bot)
        return

    if upper.startswith("UNLOAD_COG "):
        module = normalize_cog_module(line[len("UNLOAD_COG "):].strip())
        try:
            if module in bot.extensions:
                await bot.unload_extension(module)
                log(f"Unloaded cog: {module}", channel="cogs")
            else:
                log(f"Cog not loaded: {module}", channel="cogs")
        except Exception as e:
            log(f"Unload cog failed for {module}: {e}", channel="cogs")
        await _emit_cog_list(bot)
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
