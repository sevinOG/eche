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
import os
import secrets
import sys
import threading
from typing import Any, Optional, Tuple

# Per-process nonce. The GUI creates it and passes it to the bot in this
# environment variable. emit() prefixes every event line with it. The
# parser accepts a line only when it starts with that exact value. Never
# log this value.
SENTINEL_ENV = "ECHE_GUI_SENTINEL"

_bot = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_enabled = False
_reasoning_handler = None
_emit_lock = threading.RLock()
_sentinel: Optional[str] = None
_writers_installed = False
_orig_print = None


def is_enabled() -> bool:
    import os
    return (
        os.environ.get("ECHE_RUNNING", "").upper() == "BOT"
        or os.environ.get("ECHE_GUI_BRIDGE", "").strip() == "1"
    )


def make_sentinel() -> str:
    """A fresh per-process nonce. Call from the GUI before spawning the bot."""
    return "ECHE" + secrets.token_hex(16)


def set_sentinel(value: str) -> None:
    """Install the nonce for this process. Do not log `value`."""
    global _sentinel
    _sentinel = str(value or "")
    os.environ.pop(SENTINEL_ENV, None)


def get_sentinel() -> str:
    """The nonce for this process, or '' if the GUI did not provide one.

    After the first read, the value is cached and ECHE_GUI_SENTINEL is
    removed from os.environ so later child processes do not inherit it.
    """
    global _sentinel
    if _sentinel is None:
        _sentinel = os.environ.get(SENTINEL_ENV, "") or ""
    os.environ.pop(SENTINEL_ENV, None)
    return _sentinel


def reset_sentinel() -> None:
    """Test helper: forget a cached nonce so the next get_sentinel() rereads env."""
    global _sentinel
    _sentinel = None
    os.environ.pop(SENTINEL_ENV, None)


def child_environ(env=None) -> dict:
    """Copy of env (or os.environ) with the GUI sentinel removed."""
    out = dict(os.environ if env is None else env)
    out.pop(SENTINEL_ENV, None)
    return out


def redact_sentinel(text: str) -> str:
    """Replace the cached nonce in text. Never log the nonce itself."""
    raw = "" if text is None else str(text)
    token = get_sentinel()
    if not token or token not in raw:
        return raw
    return raw.replace(token, "•" * 8)


class _LockedStream:
    """Proxy that holds the emit lock around write/flush, including .buffer."""

    def __init__(self, inner, lock):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_lock", lock)

    def write(self, data):
        with self._lock:
            return self._inner.write(data)

    def flush(self):
        with self._lock:
            return self._inner.flush()

    def reconfigure(self, *args, **kwargs):
        with self._lock:
            return self._inner.reconfigure(*args, **kwargs)

    @property
    def buffer(self):
        buf = getattr(self._inner, "buffer", None)
        if buf is None:
            raise AttributeError("buffer")
        return _LockedStream(buf, self._lock)

    def __getattr__(self, name):
        return getattr(self._inner, name)


_child_filter_installed = False


def install_child_env_filter() -> None:
    """Drop the GUI sentinel from every subprocess env, including ffmpeg."""
    global _child_filter_installed
    if _child_filter_installed:
        return
    import subprocess
    import asyncio

    orig_popen = subprocess.Popen

    class _FilteredPopen(orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs["env"] = child_environ(kwargs.get("env"))
            super().__init__(*args, **kwargs)

    subprocess.Popen = _FilteredPopen

    orig_exec = asyncio.create_subprocess_exec
    orig_shell = asyncio.create_subprocess_shell

    async def _filtered_exec(*args, **kwargs):
        kwargs["env"] = child_environ(kwargs.get("env"))
        return await orig_exec(*args, **kwargs)

    async def _filtered_shell(*args, **kwargs):
        kwargs["env"] = child_environ(kwargs.get("env"))
        return await orig_shell(*args, **kwargs)

    asyncio.create_subprocess_exec = _filtered_exec
    asyncio.create_subprocess_shell = _filtered_shell
    _child_filter_installed = True


def install_locked_writers() -> None:
    """Serialize print/dprint/sys.stdout with the same lock emit() uses."""
    global _writers_installed, _orig_print
    if _writers_installed:
        return
    import builtins

    if not isinstance(sys.stdout, _LockedStream):
        sys.stdout = _LockedStream(sys.stdout, _emit_lock)
    if not isinstance(sys.stderr, _LockedStream):
        sys.stderr = _LockedStream(sys.stderr, _emit_lock)
    if _orig_print is None:
        _orig_print = builtins.print

        def _locked_print(*args, **kwargs):
            with _emit_lock:
                return _orig_print(*args, **kwargs)

        builtins.print = _locked_print
    _writers_installed = True


def configure_stdio() -> None:
    """Use UTF-8 for the GUI pipe. A Windows cp1252 stdout would drop emoji
    and typical model punctuation when emit() writes a chat line."""
    get_sentinel()
    install_child_env_filter()
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        inner = getattr(stream, "_inner", stream)
        reconfigure = getattr(inner, "reconfigure", None) or getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        kwargs = {"encoding": "utf-8", "errors": "replace"}
        if stream is not sys.stdin and inner is not sys.stdin:
            kwargs["line_buffering"] = True
        try:
            reconfigure(**kwargs)
        except Exception:
            kwargs.pop("line_buffering", None)
            try:
                reconfigure(**kwargs)
            except Exception:
                pass
    install_locked_writers()


def enable() -> None:
    global _enabled
    _enabled = True
    configure_stdio()


def set_reasoning_handler(handler) -> None:
    """Desktop chat runs in the GUI process, so a trace cannot ride stdout."""
    global _reasoning_handler
    _reasoning_handler = handler


def emit(event: str, data: Optional[dict[str, Any]] = None) -> None:
    if not _enabled and not is_enabled():
        return
    payload = {"event": event, "data": data or {}}
    body = json.dumps(payload, ensure_ascii=False)
    token = get_sentinel()
    line = (token + body if token else body) + "\n"
    with _emit_lock:
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
            return
        except Exception:
            pass
        # A non-UTF-8 text stdout (common on Windows) raises on emoji or dashes.
        # Write the same line as UTF-8 bytes so the GUI reader can still parse it.
        try:
            buf = getattr(sys.stdout, "buffer", None)
            if buf is None:
                return
            buf.write(line.encode("utf-8", errors="replace"))
            buf.flush()
        except Exception:
            pass


def parse_bridge_line(
    raw: str, sentinel: Optional[str] = None
) -> Tuple[Optional[str], dict[str, Any]]:
    """Parse one stdout line from the bot process.

    A line is an event only when it starts, at column 0, with the per-process
    sentinel that emit() prefixes, followed by one JSON object whose first
    key is event. Leading spaces are not stripped before the sentinel check,
    so a log print that smuggles a newline plus '{"event"' cannot pass.
    """
    text = "" if raw is None else str(raw).rstrip("\r\n")
    token = sentinel if sentinel is not None else get_sentinel()
    if not token or not text.startswith(token):
        return None, {}
    payload = text[len(token):]
    if not payload.startswith('{"event"'):
        return None, {}
    obj = None
    try:
        obj = json.loads(payload)
    except Exception:
        try:
            obj, end = json.JSONDecoder().raw_decode(payload)
        except Exception:
            return None, {}
        if payload[end:].strip():
            return None, {}
    if isinstance(obj, dict) and "event" in obj:
        data = obj.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        return obj.get("event"), data
    return None, {}


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
    from core.home_id import home_server_id_from_env
    home_id = home_server_id_from_env()
    if not home_id:
        log("ANNOUNCE failed: HOME_SERVER_ID not set", channel="bridge")
        return
    guild = bot.get_guild(home_id)
    if guild is None:
        log("ANNOUNCE failed: home guild not found", channel="bridge")
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
        return
    try:
        from core.chat_panel import send_visible
        await send_visible(channel, text)
        log(f"Announced: {text}", channel="bridge")
    except Exception as e:
        log(f"ANNOUNCE failed: {e}", channel="bridge")


async def _reset_memory(bot) -> None:
    try:
        from core.context_manager import get_home_guild
        from core.memory_wipe import wipe_memory

        report = await wipe_memory(
            get_home_guild(bot),
            log=lambda text: log(text, channel="memory"),
        )
        emit("memory_reset", {"ok": report.ok, "summary": report.summary()})
    except Exception as e:
        log(f"Memory reset failed: {e}", channel="memory")


async def _unlock_memory(bot) -> None:
    try:
        from core.context_manager import get_home_guild
        from core.memory_access import unlock_memory

        report = await unlock_memory(
            get_home_guild(bot),
            log=lambda text: log(text, channel="memory"),
        )
        emit("memory_access", {"ok": report.ok, "summary": report.summary()})
    except Exception as e:
        log(f"Memory access failed: {e}", channel="memory")


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

    if upper == "RESET_MEMORY":
        # Only the desktop app writes this pipe. There is no Discord command.
        await _reset_memory(bot)
        return

    if upper == "UNLOCK_MEMORY":
        # Only the desktop app writes this pipe. There is no Discord command.
        await _unlock_memory(bot)
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
    configure_stdio()

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
