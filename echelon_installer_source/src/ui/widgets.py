"""Custom widgets for echelon installer"""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt
from .theme import ECHELON_PALETTE


class StepIndicator(QWidget):
    def __init__(self, steps, parent=None):
        super().__init__(parent)
        self.steps = steps
        self.current = 0
        self.layout = QHBoxLayout(self)
        self.layout.setSpacing(12)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.labels = []
        self.dots = []
        self._build()

    def _build(self):
        for i, name in enumerate(self.steps):
            # Each step is a compact vertical unit: dot + label under it
            unit = QVBoxLayout()
            unit.setSpacing(4)
            unit.setContentsMargins(0, 0, 0, 0)
            unit.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            row = QHBoxLayout()
            row.setSpacing(0)
            row.setContentsMargins(0, 0, 0, 0)
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)

            if i > 0:
                sep = QFrame()
                sep.setFixedSize(28, 2)
                sep.setStyleSheet(f"background-color: {ECHELON_PALETTE['border']};")
                self.layout.addWidget(sep)

            dot = QFrame()
            dot.setFixedSize(28, 28)
            dot.setObjectName("StepPending")
            dot_layout = QVBoxLayout(dot)
            dot_layout.setContentsMargins(0, 0, 0, 0)
            dot_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num = QLabel(str(i + 1))
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(
                f"background: transparent; color: {ECHELON_PALETTE['text_muted']}; "
                f"font-size: 11px; font-weight: 700;"
            )
            dot_layout.addWidget(num)
            self.dots.append((dot, num))

            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"background: transparent; color: {ECHELON_PALETTE['text_dim']}; "
                f"font-size: 11px; letter-spacing: 0.4px;"
            )
            self.labels.append(lbl)

            unit.addWidget(dot, alignment=Qt.AlignmentFlag.AlignHCenter)
            unit.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
            wrap = QWidget()
            wrap.setLayout(unit)
            wrap.setStyleSheet("background: transparent;")
            self.layout.addWidget(wrap)

        self.layout.addStretch()

    def set_current(self, idx: int):
        self.current = idx
        for i, (dot, num) in enumerate(self.dots):
            lbl = self.labels[i]
            if i < idx:
                bg = ECHELON_PALETTE["success"]
                num.setText("✓")
                num.setStyleSheet(
                    "background: transparent; color: white; font-size: 11px; font-weight: 700;"
                )
                lbl.setStyleSheet(
                    f"background: transparent; color: {ECHELON_PALETTE['success']}; font-size: 11px;"
                )
                dot.setStyleSheet(
                    f"background-color: {bg}; border-radius: 14px; border: none;"
                )
            elif i == idx:
                bg = ECHELON_PALETTE["accent"]
                num.setText(str(i + 1))
                num.setStyleSheet(
                    "background: transparent; color: white; font-size: 11px; font-weight: 700;"
                )
                lbl.setStyleSheet(
                    f"background: transparent; color: {ECHELON_PALETTE['text']}; "
                    f"font-size: 11px; font-weight: 600;"
                )
                dot.setStyleSheet(
                    f"background-color: {bg}; border-radius: 14px; border: none;"
                )
            else:
                num.setText(str(i + 1))
                num.setStyleSheet(
                    f"background: transparent; color: {ECHELON_PALETTE['text_muted']}; "
                    f"font-size: 11px; font-weight: 700;"
                )
                lbl.setStyleSheet(
                    f"background: transparent; color: {ECHELON_PALETTE['text_dim']}; font-size: 11px;"
                )
                dot.setStyleSheet(
                    f"background-color: {ECHELON_PALETTE['surface_2']}; "
                    f"border: 1px solid {ECHELON_PALETTE['border']}; border-radius: 14px;"
                )
        self.update()


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet(
            f"#Card {{ background-color: {ECHELON_PALETTE['surface']}; "
            f"border: 1px solid {ECHELON_PALETTE['border']}; border-radius: 12px; }}"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 16, 18, 16)
        self._layout.setSpacing(12)

    def add_widget(self, w):
        self._layout.addWidget(w)

    def add_layout(self, l):
        self._layout.addLayout(l)


class LogView(QWidget):
    """simple wrapper to avoid circular imports"""
    pass
