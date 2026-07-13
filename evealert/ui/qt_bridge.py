"""QtBridge — UIBridge implementation backed by PySide6 signals (Phase 2, #126).

Signals are emitted from any thread and delivered queued on the Qt main
thread automatically, so the engine never needs manual after(0) dispatching.
"""

from PySide6.QtCore import QObject, Signal


class QtBridge(QObject):
    """UIBridge backed by Qt signals.

    Engine calls log(), refresh_region_toggles(), show_error() from the
    alert daemon thread.  Qt queues the signals to the main thread so widget
    access is always safe.
    """

    # text, color tag (see evealert/ui/theme.LOG_COLORS)
    log_message = Signal(str, str)
    toggles_changed = Signal()
    error = Signal(str)

    def log(self, text: str, color: str = "normal") -> None:
        self.log_message.emit(text, color)

    def refresh_region_toggles(self) -> None:
        self.toggles_changed.emit()

    def show_error(self, message: str) -> None:
        self.error.emit(message)
