# core/eche.py
# Entry point used by the GUI:  python -m core.eche
# Also invoked by the frozen exe via: Eche.exe --bot
# Sets up import paths, loads env/settings, then starts the Discord bot.

from __future__ import annotations

import os
import sys

# ============================================================
# ABSOLUTELY EARLIEST FFMPEG PATH FORCE
# Must happen before ANY discord import, because discord.player
# does shutil.which("ffmpeg") checks at import time in some paths.
# ============================================================
try:
    from core.paths import find_ffmpeg
    _ff = find_ffmpeg()
    if _ff:
        _ff_dir = os.path.dirname(_ff)
        if _ff_dir and os.path.isdir(_ff_dir):
            current = os.environ.get("PATH", "")
            parts = [p for p in current.split(os.pathsep) if p and p != _ff_dir]
            os.environ["PATH"] = _ff_dir + os.pathsep + os.pathsep.join(parts)
except Exception:
    pass


def _bootstrap_paths() -> str:
    """
    Put the package root on sys.path so `core.*` / `cogs.*` / `gui.*` import.
    Returns the writable package (user) root.
    """
    from core.paths import ensure_user_layout, is_frozen

    root = ensure_user_layout()

    if not is_frozen():
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root and project_root not in sys.path:
            sys.path.insert(0, project_root)
        os.chdir(project_root)
        return project_root

    # Frozen: keep cwd at package root so .env / config / cookies resolve
    if root and root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)
    return root


def _load_env(project_root: str) -> None:
    """
    Load configuration securely:
      1) Existing process env (set by GUI parent or shell)
      2) DPAPI secret store + public settings.json
      3) .env as last-resort dev fallback (dotenv does not override set vars)
    """
    # Prefer explicit user root from GUI parent when present
    root = os.environ.get("ECHE_USER_ROOT") or project_root

    try:
        from core.secrets import apply_to_environ
        apply_to_environ(root, override_existing=False)
    except Exception as e:
        print(
            f'{{"event":"log","data":{{"message":"Secure config load warning: {e}","channel":"bot"}}}}',
            flush=True,
        )

    try:
        from dotenv import load_dotenv
        # override=False: never clobber DPAPI / parent-provided secrets
        load_dotenv(os.path.join(root, ".env"), override=False)
    except Exception:
        pass


def _emit_fatal(message: str, *, code: str = "config") -> None:
    """Print a structured error the GUI can classify + plain text for logs."""
    import json
    payload = {
        "event": "fatal",
        "data": {
            "message": message,
            "code": code,
            "channel": "bot",
        },
    }
    print(json.dumps(payload), flush=True)
    print(f"[FATAL] {message}", flush=True)


def main() -> None:
    os.environ.setdefault("ECHE_RUNNING", "BOT")
    os.environ.setdefault("ECHE_GUI_BRIDGE", "1")

    project_root = _bootstrap_paths()
    _load_env(project_root)

    token = (os.getenv("DISCORD_TOKEN") or "").strip().replace("\n", "").replace("\r", "")
    if not token:
        _emit_fatal(
            "DISCORD_TOKEN missing — set it in Settings (secure store) or .env"
        )
        sys.exit(1)

    from core.home_id import parse_home_server_id

    home_raw = (os.getenv("HOME_SERVER_ID") or "").strip()
    if not home_raw:
        try:
            from core.secrets import load_all
            home_raw = (load_all(project_root).get("home_server_id") or "").strip()
        except Exception:
            home_raw = ""
    home_id = parse_home_server_id(home_raw)
    if not home_id:
        if home_raw:
            _emit_fatal(
                "HOME_SERVER_ID is not a Discord server ID. "
                "Enable Developer Mode, right-click the server name, and choose Copy Server ID. "
                "Do not paste the server icon link."
            )
        else:
            _emit_fatal(
                "HOME_SERVER_ID is not set. Add it to .env or GUI Settings before starting the bot."
            )
        sys.exit(2)
    home = str(home_id)
    os.environ["HOME_SERVER_ID"] = home

    # Import after path bootstrap so core.* resolves
    try:
        from core.gui_bridge import enable, log
        from core.bot import Eche
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb, flush=True)
        _emit_fatal(f"Failed to import bot modules: {e}", code="traceback")
        sys.exit(3)

    # Refresh module-level HOME_SERVER_ID if bot was imported with 0
    try:
        import core.bot as bot_mod
        bot_mod.HOME_SERVER_ID = int(home)
    except Exception:
        pass

    enable()
    log("Starting Eche process...", channel="bot")

    bot = Eche()
    try:
        bot.run(token)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb, flush=True)
        log(f"Bot crashed: {e}", channel="bot")
        _emit_fatal(str(e), code="traceback")
        raise


if __name__ == "__main__":
    main()
