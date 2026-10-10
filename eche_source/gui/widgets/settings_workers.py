# gui/widgets/settings_workers.py
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class SettingsSaveWorker(QThread):
    """Write settings off the GUI thread. DPAPI and icacls block if they run on it."""

    finished_ok = pyqtSignal(bool, str)

    def __init__(self, payload: dict | None = None, *, clear: bool = False):
        super().__init__()
        self.payload = payload or {}
        self.clear = clear

    def run(self):
        try:
            from gui.widgets.settingswindow import PROJECT_ROOT, save_settings

            if self.clear:
                from core.secrets import clear_secrets
                clear_secrets(PROJECT_ROOT)
            else:
                save_settings(self.payload)
                try:
                    from core.summarizer_prompt import ensure_summarizer_prompt_file
                    ensure_summarizer_prompt_file(
                        self.payload.get("summarizer_prompt_path") or None
                    )
                except Exception:
                    pass
            self.finished_ok.emit(True, "")
        except Exception as exc:
            self.finished_ok.emit(False, str(exc) or type(exc).__name__)


class UpdatePrepareWorker(QThread):
    """Download GitHub source off the GUI thread. Local mode does no I/O here."""

    log_line = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode

    def run(self):
        if self.mode != "github":
            self.finished_ok.emit(True, "")
            return
        import tempfile

        stage = tempfile.mkdtemp(prefix="eche-update-src-")
        try:
            from core.app_update import stage_github_source

            stage_github_source(stage, log=self.log_line.emit)
            self.finished_ok.emit(True, stage)
        except Exception as exc:
            try:
                import shutil
                shutil.rmtree(stage, ignore_errors=True)
            except Exception:
                pass
            self.finished_ok.emit(False, str(exc) or type(exc).__name__)