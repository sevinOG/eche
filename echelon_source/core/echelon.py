# core/echelon.py
# Entry point used by the GUI:  python -m core.echelon
# Also invoked by the frozen exe via: Echelon.exe --bot
# Sets up import paths, loads env/settings, then starts the Discord bot.

from __future__ import annotations

import os
import sys


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
    root = os.environ.get("ECHELON_USER_ROOT") or project_root

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
    os.environ.setdefault("ECHELON_RUNNING", "BOT")
    os.environ.setdefault("ECHELON_GUI_BRIDGE", "1")

    project_root = _bootstrap_paths()
    _load_env(project_root)

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        _emit_fatal(
            "DISCORD_TOKEN missing — set it in Settings (secure store) or .env"
        )
        sys.exit(1)

    home = (os.getenv("HOME_SERVER_ID") or "").strip()
    if not home:
        try:
            from core.secrets import load_all
            home = (load_all(project_root).get("home_server_id") or "").strip()
            if home:
                os.environ["HOME_SERVER_ID"] = home
        except Exception:
            pass
    if not home:
        _emit_fatal(
            "HOME_SERVER_ID is not set. Add it to .env or GUI Settings before starting the bot."
        )
        sys.exit(2)

    # Import after path bootstrap so core.* resolves
    try:
        from core.gui_bridge import enable, log
        from core.bot import Echelon
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
    log("Starting Echelon process...", channel="bot")

    bot = Echelon()
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
