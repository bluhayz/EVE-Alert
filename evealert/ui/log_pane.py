"""LogPane — enhanced log display widget for EVE Alert (#167).

Replaces the bare QPlainTextEdit in MainWindow with a self-contained widget
that provides:
  - Category toolbar: All / Alarms / Intel / System toggle buttons
  - Free-text live search
  - Pause mode (buffer keeps filling; re-renders on resume)
  - Right-click context menu: Copy line / Copy all visible

Usage::

    pane = LogPane(parent)
    pane.append("Enemy detected", "red")   # same signature as old append_log
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import NamedTuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from evealert.ui import theme

# Maximum entries to keep in the ring buffer
_BUFFER_MAX = 2000
# Maximum blocks shown in the widget at one time
_WIDGET_BLOCK_MAX = 500


class _Entry(NamedTuple):
    timestamp: str   # pre-formatted HH:MM:SS
    text: str
    color: str       # raw color tag ("red", "cyan", "green", "yellow", "normal", …)
    tag: str         # derived category: "alarm" | "intel" | "system" | "normal"


def _color_to_tag(color: str) -> str:
    if color in ("red", "yellow"):
        return "alarm"
    if color == "cyan":
        return "intel"
    if color == "green":
        return "system"
    return "normal"


class LogPane(QWidget):
    """Filterable, pauseable, searchable log widget."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._buffer: deque[_Entry] = deque(maxlen=_BUFFER_MAX)
        self._paused = False
        self._active_tag: str = "all"   # "all" | "alarm" | "intel" | "system"
        self._search_text: str = ""
        self._build_ui()

    # ------------------------------------------------------------------
    # Public API (backward-compatible with the old append_log signature)
    # ------------------------------------------------------------------

    def append(self, text: str, color: str = "normal") -> None:
        """Add a log entry to the buffer and (if unfiltered) to the display."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{now}] {text}"
        tag = _color_to_tag(color)
        entry = _Entry(timestamp=now, text=line, color=color, tag=tag)
        self._buffer.append(entry)

        if self._paused:
            return
        if self._matches(entry):
            self._insert_entry(entry)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Toolbar row
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self._btn_all     = self._filter_btn("All",    "all",    True)
        self._btn_alarms  = self._filter_btn("Alarms", "alarm",  False)
        self._btn_intel   = self._filter_btn("Intel",  "intel",  False)
        self._btn_system  = self._filter_btn("System", "system", False)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setMaximumWidth(200)
        self._search.textChanged.connect(self._on_search_changed)

        self._pause_btn = QPushButton("⏸ Pause")
        self._pause_btn.setCheckable(True)
        self._pause_btn.setProperty("class", "")
        self._pause_btn.toggled.connect(self._on_pause_toggled)

        lbl = QLabel("Filter:")
        lbl.setProperty("class", "muted")
        toolbar.addWidget(lbl)
        toolbar.addWidget(self._btn_all)
        toolbar.addWidget(self._btn_alarms)
        toolbar.addWidget(self._btn_intel)
        toolbar.addWidget(self._btn_system)
        toolbar.addStretch()
        toolbar.addWidget(self._search)
        toolbar.addWidget(self._pause_btn)
        root.addLayout(toolbar)

        # Log widget
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.document().setMaximumBlockCount(_WIDGET_BLOCK_MAX)
        self._log.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._log.customContextMenuRequested.connect(self._show_context_menu)
        root.addWidget(self._log, 1)

    def _filter_btn(self, label: str, tag: str, active: bool) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(active)
        if active:
            btn.setProperty("class", "primary")
        btn.clicked.connect(lambda: self._set_tag_filter(tag))
        return btn

    # ------------------------------------------------------------------
    # Filter / search / pause
    # ------------------------------------------------------------------

    def _set_tag_filter(self, tag: str) -> None:
        self._active_tag = tag
        for btn, t in (
            (self._btn_all,    "all"),
            (self._btn_alarms, "alarm"),
            (self._btn_intel,  "intel"),
            (self._btn_system, "system"),
        ):
            btn.setChecked(t == tag)
            btn.setProperty("class", "primary" if t == tag else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._rerender()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.lower()
        self._rerender()

    def _on_pause_toggled(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self._pause_btn.setText("▶ Resume")
        else:
            self._pause_btn.setText("⏸ Pause")
            self._rerender()

    def _matches(self, entry: _Entry) -> bool:
        if self._active_tag != "all" and entry.tag != self._active_tag:
            return False
        if self._search_text and self._search_text not in entry.text.lower():
            return False
        return True

    def _rerender(self) -> None:
        """Clear the widget and re-insert all matching buffer entries."""
        self._log.clear()
        for entry in self._buffer:
            if self._matches(entry):
                self._insert_entry(entry)
        scrollbar = self._log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _insert_entry(self, entry: _Entry) -> None:
        """Append one entry to the display widget."""
        fmt = QTextCharFormat()
        hex_color = theme.LOG_COLORS.get(entry.color, theme.TEXT)
        fmt.setForeground(QColor(hex_color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(entry.text + "\n", fmt)
        scrollbar = self._log.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum() - 4:
            self._log.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        copy_line = QAction("Copy line", self)
        copy_all  = QAction("Copy all visible", self)

        def _copy_line():
            cursor = self._log.cursorForPosition(pos)
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            text = cursor.selectedText()
            if text:
                QApplication_clipboard().setText(text)

        def _copy_all():
            QApplication_clipboard().setText(self._log.toPlainText())

        copy_line.triggered.connect(_copy_line)
        copy_all.triggered.connect(_copy_all)
        menu.addAction(copy_line)
        menu.addAction(copy_all)
        menu.exec(self._log.mapToGlobal(pos))


def QApplication_clipboard():
    """Deferred import to avoid needing QApplication at module load time."""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415
    return QApplication.clipboard()
