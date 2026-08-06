import os
import sys
import psutil

try:
    from core.paths import user_dir
except Exception:
    def user_dir():
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


LOCKFILE = os.path.join(user_dir(), "gui.lock")


def is_gui_process(pid: int) -> bool:
    """Return True if PID is a running Echelon GUI process (not the --bot child)."""
    try:
        if pid == os.getpid():
            return True
        proc = psutil.Process(pid)
        cmd = " ".join(proc.cmdline()).lower()
        name = (proc.name() or "").lower()

        # Source launches
        if "-m gui.main" in cmd or "run_gui.py" in cmd or "echelon_app.py" in cmd:
            if "--bot" in cmd:
                return False
            return True

        # Frozen GUI (exe without --bot)
        if name in ("echelon.exe", "echelon_app.exe"):
            return "--bot" not in cmd

        return False
    except Exception:
        return False


def ensure_single_gui_instance():
    """
    Ensures only one GUI instance runs.
    Removes stale lockfiles automatically.
    Creates a new lockfile for this process.
    """
    # Bot child must never take the GUI lock / exit because of it
    if "--bot" in sys.argv or os.environ.get("ECHELON_RUNNING", "").upper() == "BOT":
        return True

    if os.path.exists(LOCKFILE):
        try:
            with open(LOCKFILE, "r") as f:
                old_pid = int(f.read().strip())
        except Exception:
            old_pid = None

        if old_pid and old_pid != os.getpid() and is_gui_process(old_pid):
            print(f"GUI already running (PID {old_pid})")
            return False
        else:
            try:
                os.remove(LOCKFILE)
                print("Stale GUI lockfile removed.")
            except Exception:
                pass

    try:
        with open(LOCKFILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    return True


def cleanup_lockfile():
    """Remove lockfile on exit."""
    try:
        if os.path.exists(LOCKFILE):
            with open(LOCKFILE, "r") as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(LOCKFILE)
    except Exception:
        pass
