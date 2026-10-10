# gui/widgets/loading.py
# Single toolbar status control: busy spinner or online/offline/error.

from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QLabel, QHBoxLayout, QWidget


class LoadingIndicator(QWidget):
    """
    One status chip for the main toolbar.
    Online, offline, error, and startup are the light only.
    A busy job can still show a short message beside it.
    """

    _FRAMES = ("◐", "◓", "◑", "◒")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._frame = 0
        self._base_msg = ""
        self._state = "offline"

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.icon = QLabel("◐")
        self.icon.setObjectName("StatusDot")
        self.icon.setProperty("state", "offline")
        self.icon.setMinimumWidth(28)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon)

        self.label = QLabel("")
        self.label.setObjectName("FieldHint")
        self.label.setVisible(False)
        lay.addWidget(self.label)

        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)

        self.set_state("offline")

    def set_busy(self, busy: bool, message: str = "Working…") -> None:
        """Back-compat for child windows (context/economy)."""
        if busy:
            self.set_state("busy", message)
        else:
            # Don't force offline — parent may still be online
            if self._state == "busy":
                self.set_state("offline")

    def set_state(self, state: str, message: str = "") -> None:
        """
        state: offline | busy | online | error | starting
        """
        state = (state or "offline").lower()
        if state == "starting":
            state = "busy"
            message = ""

        self._state = state
        self.show()

        if state == "busy":
            self._busy = True
            self._base_msg = (message or "").strip()
            self._frame = 0
            self.icon.setText(self._FRAMES[0])
            self.icon.setProperty("state", "online")
            if self._base_msg:
                self.label.setText(self._base_msg)
                self.label.setVisible(True)
            else:
                self.label.setText("")
                self.label.setVisible(False)
            self._refresh_style(self.icon)
            if not self._timer.isActive():
                self._timer.start()
            return

        self._busy = False
        self._timer.stop()
        self.icon.setText("●")

        if state == "online":
            self.icon.setProperty("state", "online")
        elif state == "error":
            self.icon.setProperty("state", "error")
        else:
            self.icon.setProperty("state", "offline")

        self._refresh_style(self.icon)
        note = (message or "").strip()
        if note:
            self.label.setText(note)
            self.label.setVisible(True)
        else:
            self.label.setText("")
            self.label.setVisible(False)

    def _tick(self):
        if not self._busy:
            return
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self.icon.setText(self._FRAMES[self._frame])

    @staticmethod
    def _refresh_style(w):
        try:
            w.style().unpolish(w)
            w.style().polish(w)
        except Exception:
            pass
