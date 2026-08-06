from __future__ import annotations
import os
import sys

def _bootstrap():
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return None
    project_root = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(project_root)
    for path in (project_root, parent):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    os.chdir(project_root)

def main():
    _bootstrap()
    want_bot = "--bot" in sys.argv or os.environ.get("ECHE_RUNNING", "").upper() == "BOT"
    if sys.platform.startswith("win"):
        try:
            import multiprocessing
            multiprocessing.freeze_support()
        except Exception:
            pass
    if want_bot:
        os.environ["ECHE_RUNNING"] = "BOT"
        os.environ.setdefault("ECHE_GUI_BRIDGE", "1")
        from core.eche import main as bot_main
        bot_main()
    else:
        os.environ.setdefault("ECHE_RUNNING", "GUI")
        from gui.main import launch_gui
        launch_gui()

if __name__ == "__main__":
    main()
