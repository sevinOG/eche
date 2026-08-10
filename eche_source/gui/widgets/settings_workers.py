# gui/widgets/settings_workers.py
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QThread, pyqtSignal


class UpdateWorker(QThread):
    """Run eche_source\\BUILD.bat and stream log lines to the UI."""

    log_line = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str, bool)

    def __init__(self, source_path: str, build_script: str):
        super().__init__()
        self.source_path = source_path
        self.build_script = build_script

    def run(self):
        import subprocess

        if not os.path.isfile(self.build_script):
            self.finished_ok.emit(
                False, f"BUILD.bat not found:\n{self.build_script}", False
            )
            return

        env = os.environ.copy()
        env["ECHE_NO_PAUSE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        args = ["cmd.exe", "/c", self.build_script]
        self.log_line.emit(f"Running: {self.build_script}")
        self.log_line.emit(f"Source:  {self.source_path}")

        try:
            proc = subprocess.Popen(
                args,
                cwd=self.source_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            assert proc.stdout is not None
            collected: list[str] = []
            for line in proc.stdout:
                clean = line.rstrip("\n")
                if clean.strip():
                    collected.append(clean)
                    self.log_line.emit(clean)
            code = proc.wait()
            if code == 0:
                portable_exe = os.path.normpath(
                    os.path.join(self.source_path, "..", "eche", "Eche.exe")
                )
                if os.path.isfile(portable_exe):
                    self.finished_ok.emit(
                        True,
                        f"Portable app rebuilt:\n{portable_exe}\n\n"
                        "Icons and _internal were published. Restart Eche to use the new build.",
                        False,
                    )
                else:
                    self.finished_ok.emit(
                        True,
                        "BUILD.bat finished successfully. "
                        "If Eche.exe is missing under eche/, check the log.",
                        False,
                    )
            else:
                tail = "\n".join(collected[-12:]) if collected else ""
                msg = f"BUILD.bat exited with code {code}."
                if tail:
                    msg += f"\n\nLast output:\n{tail}"
                self.finished_ok.emit(False, msg, False)
        except Exception as e:
            self.finished_ok.emit(False, str(e), False)