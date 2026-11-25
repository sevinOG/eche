# loop_watchdog.py
# Says when the bot's event loop stops turning, and dumps every thread's stack once.
#
# Live 2026-11-25 16:04 ET: the loop sat in Thread.start() for minutes and the only clue was
# discord's own heartbeat warning, which shows the loop thread alone. This watchdog is a daemon
# thread started at setup (never during a stall: starting a thread is what hung). Every
# CHECK_S it asks the loop to stamp a time; when the stamp is older than STALL_S it writes one
# "[loop] stalled" line and a faulthandler dump of all threads to logs/loop_stall.txt.
# One dump per stall, at most MAX_DUMPS per run. Stacks are file/line/function names only:
# no values, tokens or message text.

from __future__ import annotations

import faulthandler
import os
import threading
import time


def _say(line: str) -> None:
    """Always shown (not only with ECHE_DEBUG): the GUI log captures the bot's stdout."""
    try:
        print(line, flush=True)
    except Exception:
        pass

STALL_S = 2.0
CHECK_S = 0.5
MAX_DUMPS = 5
DUMP_NAME = "loop_stall.txt"

_started: threading.Thread | None = None


def dump_path() -> str:
    try:
        from core.paths import ensure_user_layout

        root = ensure_user_layout()
    except Exception:
        root = os.path.join(os.path.expanduser("~"), ".eche")
    folder = os.path.join(root, "logs")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, DUMP_NAME)


class Watchdog:
    def __init__(self, loop, *, stall_s: float = STALL_S, check_s: float = CHECK_S,
                 max_dumps: int = MAX_DUMPS, path_fn=dump_path, log=_say):
        self.loop = loop
        self.stall_s = stall_s
        self.check_s = check_s
        self.max_dumps = max_dumps
        self.path_fn = path_fn
        self.log = log
        self.beat = time.monotonic()
        self.dumps = 0
        self.stalled = False
        self.stop = threading.Event()

    def _stamp(self) -> None:
        self.beat = time.monotonic()

    def check_once(self) -> None:
        """One tick: ask the loop for a stamp, then judge the last one."""
        try:
            self.loop.call_soon_threadsafe(self._stamp)
        except RuntimeError:  # the loop is closed: the bot is stopping
            self.stop.set()
            return
        lag = time.monotonic() - self.beat
        if lag <= self.stall_s:
            if self.stalled:
                self.log(f"[loop] running again after a stall of about {lag:.1f}s")
            self.stalled = False
            return
        if self.stalled:
            return
        self.stalled = True
        self.log(f"[loop] stalled for {lag:.1f}s")
        if self.dumps >= self.max_dumps:
            return
        self.dumps += 1
        try:
            path = self.path_fn()
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"\n=== loop stalled {lag:.1f}s at {time.strftime('%Y-%m-%d %H:%M:%S %z')} ===\n")
                handle.flush()
                faulthandler.dump_traceback(file=handle, all_threads=True)
                # faulthandler shows ids only; names say which is which (eche-whisper-load, ...).
                for thread in threading.enumerate():
                    handle.write(f"thread 0x{(thread.ident or 0):016x} = {thread.name}\n")
            self.log(f"[loop] all thread stacks written to logs/{os.path.basename(path)}")
        except Exception as exc:
            self.log(f"[loop] stack dump failed: {type(exc).__name__}")

    def run(self) -> None:
        while not self.stop.wait(self.check_s):
            self.check_once()


def start(loop) -> Watchdog | None:
    """Start once per process. ECHE_LOOP_WATCHDOG=off turns it off."""
    global _started
    if (os.environ.get("ECHE_LOOP_WATCHDOG") or "").strip().lower() in ("0", "off", "false", "no"):
        return None
    if _started is not None and _started.is_alive():
        return None
    dog = Watchdog(loop)
    _started = threading.Thread(target=dog.run, name="eche-loop-watchdog", daemon=True)
    _started.start()
    return dog
