# gui/widgets/logpane.py
# Main-window log. Ordinary lines stay one row. A tool row collapses
# until it is clicked, then the result opens underneath.

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
)

from gui.theme import ACCENT, BG, TEXT

_SUMMARY = Qt.ItemDataRole.UserRole
_DETAIL = Qt.ItemDataRole.UserRole + 1


class _LogDetail(QPlainTextEdit):
    """The expansion is one tall row. The log pane scrolls through it."""

    def wheelEvent(self, event):
        event.ignore()


class LogPane(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogPane")
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setRootIsDecorated(False)
        self.setIndentation(16)
        self.setAnimated(False)
        self.setUniformRowHeights(False)
        self.setWordWrap(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setFont(QFont("Consolas", 10))
        self.header().setStretchLastSection(True)
        self.itemClicked.connect(self._on_item_clicked)

    def append_line(self, text: str) -> None:
        item = QTreeWidgetItem([text])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setData(0, _SUMMARY, text)
        self.addTopLevelItem(item)
        self.scrollToItem(item)

    def append_tool(self, summary: str, detail: str) -> None:
        item = QTreeWidgetItem()
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setData(0, _SUMMARY, summary)
        item.setData(0, _DETAIL, detail)
        item.setToolTip(0, "Click to show what the tool returned")
        child = QTreeWidgetItem()
        child.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.addChild(child)
        self.addTopLevelItem(item)
        self._attach_detail(child, detail)
        self._mark(item)
        item.setExpanded(False)
        self.scrollToItem(item)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_open_details()

    def plain_text(self) -> str:
        lines: list[str] = []
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            lines.append(str(item.data(0, _SUMMARY) or item.text(0)))
            detail = item.data(0, _DETAIL)
            if detail:
                lines.append(str(detail))
        return "\n".join(lines)

    def _attach_detail(self, child: QTreeWidgetItem, detail: str) -> None:
        editor = _LogDetail()
        editor.setObjectName("LogDetail")
        editor.setReadOnly(True)
        editor.setPlainText(detail)
        editor.setFont(QFont("Consolas", 10))
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setStyleSheet(
            "QPlainTextEdit#LogDetail {"
            f"background: {BG}; color: {TEXT}; border: none;"
            f"border-left: 2px solid {ACCENT}; padding: 6px 8px;"
            "}"
        )
        child.setSizeHint(0, QSize(400, 72))
        self.setItemWidget(child, 0, editor)

    def _fit_detail(self, item: QTreeWidgetItem) -> None:
        """Make the open row as tall as its text, so the log can scroll to the end."""
        child = item.child(0) if item is not None else None
        editor = self.itemWidget(child, 0) if child is not None else None
        if not isinstance(editor, QPlainTextEdit):
            return
        width = max(160, self.viewport().width() - 28)
        editor.document().setTextWidth(width - 20)
        spacing = max(editor.fontMetrics().lineSpacing(), 16)
        blocks = max(1, editor.blockCount())
        height = max(
            72,
            int(editor.document().size().height()) + 18,
            blocks * spacing + 18,
        )
        editor.setFixedHeight(height)
        child.setSizeHint(0, QSize(width, height))

    def _fit_open_details(self) -> None:
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if item is not None and item.isExpanded():
                self._fit_detail(item)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.parent() is not None or item.childCount() == 0:
            return
        item.setExpanded(not item.isExpanded())
        self._mark(item)
        if item.isExpanded():
            self._fit_detail(item)
            child = item.child(0)
            if child is not None:
                self.scrollToItem(child, QAbstractItemView.ScrollHint.EnsureVisible)

    def _mark(self, item: QTreeWidgetItem) -> None:
        summary = str(item.data(0, _SUMMARY) or "")
        mark = "▾" if item.isExpanded() else "▸"
        item.setText(0, f"{mark}  {summary}")

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            chunks = []
            for item in self.selectedItems():
                text = item.text(0)
                if text:
                    chunks.append(text)
            if chunks:
                QApplication.clipboard().setText("\n".join(chunks))
            return
        super().keyPressEvent(event)


