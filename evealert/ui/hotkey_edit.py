"""HotkeyEdit — click-to-capture key binding widget for EVE Alert (#165).

Replaces free-text QLineEdit hotkey entry with a press-to-record control.

Usage::

    edit = HotkeyEdit("f1", used_by={"Profile cycle": "f3", "Status": "f4"})
    edit.binding_changed.connect(lambda key: print("new key:", key))
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QPushButton, QToolTip

# ---------------------------------------------------------------------------
# Qt Key → pynput-compatible string
# ---------------------------------------------------------------------------

_QT_TO_PYNPUT: dict[int, str] = {}

# F1–F12
for _i in range(1, 13):
    _QT_TO_PYNPUT[getattr(Qt.Key, f"Key_F{_i}").value] = f"f{_i}"

# Letters a–z
for _c in "abcdefghijklmnopqrstuvwxyz":
    _QT_TO_PYNPUT[getattr(Qt.Key, f"Key_{_c.upper()}").value] = _c

# Specials
_QT_TO_PYNPUT.update({
    Qt.Key.Key_Escape.value:    "esc",
    Qt.Key.Key_Return.value:    "enter",
    Qt.Key.Key_Enter.value:     "enter",
    Qt.Key.Key_Space.value:     "space",
    Qt.Key.Key_Tab.value:       "tab",
    Qt.Key.Key_Backspace.value: "backspace",
    Qt.Key.Key_Delete.value:    "delete",
    Qt.Key.Key_Insert.value:    "insert",
    Qt.Key.Key_Home.value:      "home",
    Qt.Key.Key_End.value:       "end",
    Qt.Key.Key_Up.value:        "up",
    Qt.Key.Key_Down.value:      "down",
    Qt.Key.Key_Left.value:      "left",
    Qt.Key.Key_Right.value:     "right",
    Qt.Key.Key_PageUp.value:    "page_up",
    Qt.Key.Key_PageDown.value:  "page_down",
})

# Numpad 0–9
for _i in range(10):
    k = getattr(Qt.Key, f"Key_0{_i}" if _i < 10 else None, None)
    if k:
        _QT_TO_PYNPUT[k.value] = str(_i)


def qt_key_to_pynput(qt_key: int) -> str | None:
    """Convert a Qt key value to a pynput-compatible lowercase string.

    Returns None for keys that cannot be represented (e.g. modifier-only).
    """
    return _QT_TO_PYNPUT.get(qt_key)


# ---------------------------------------------------------------------------
# HotkeyEdit widget
# ---------------------------------------------------------------------------

class HotkeyEdit(QPushButton):
    """Click to record a new key binding; emits binding_changed(str) on capture.

    Args:
        binding:  Initial binding string (e.g. "f1").
        used_by:  Dict of {action_name: binding} for conflict detection.
                  If the pressed key matches any binding in this dict the
                  capture is rejected with an inline tooltip warning.
    """

    binding_changed = Signal(str)

    def __init__(
        self,
        binding: str = "",
        used_by: dict[str, str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._binding = binding.lower().strip() if binding else ""
        self._used_by: dict[str, str] = used_by or {}
        self._capturing = False
        self._update_label()
        self.setMinimumWidth(90)
        self.clicked.connect(self._start_capture)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_binding(self) -> str:
        return self._binding

    def set_binding(self, binding: str) -> None:
        self._binding = binding.lower().strip() if binding else ""
        self._update_label()

    def set_used_by(self, used_by: dict[str, str]) -> None:
        self._used_by = used_by

    # ------------------------------------------------------------------
    # Capture flow
    # ------------------------------------------------------------------

    def _start_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.setText("Press a key\u2026 (Esc to cancel)")
        self.setProperty("class", "warning")
        self.style().unpolish(self)
        self.style().polish(self)
        self.grabKeyboard()
        self.setFocus()

    def _stop_capture(self, accepted: bool = False, new_binding: str = "") -> None:
        self.releaseKeyboard()
        self._capturing = False
        if accepted:
            self._binding = new_binding
            self.binding_changed.emit(new_binding)
        self._update_label()
        self.setProperty("class", "")
        self.style().unpolish(self)
        self.style().polish(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        qt_key = event.key()

        # Esc → cancel
        if qt_key == Qt.Key.Key_Escape.value:
            self._stop_capture(accepted=False)
            return

        pynput_str = qt_key_to_pynput(qt_key)
        if pynput_str is None:
            # Unknown key — ignore and keep capturing
            return

        # Conflict check
        for action, bound in self._used_by.items():
            if bound.lower() == pynput_str:
                QToolTip.showText(
                    self.mapToGlobal(self.rect().bottomLeft()),
                    f"Already used by: {action}",
                    self,
                )
                return

        self._stop_capture(accepted=True, new_binding=pynput_str)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_label(self) -> None:
        self.setText(self._binding.upper() if self._binding else "(none)")
