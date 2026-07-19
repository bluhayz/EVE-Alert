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
    # Emitted when a red (alarm) message is logged — tray icon can flash (#168)
    alarm_fired = Signal(str)
    # Emitted when the authenticated character's system changes (location monitor)
    context_changed = Signal()
    # Emitted when a newer GitHub release is detected — carries the tag string
    update_available = Signal(str)
    # Emitted when a crash bundle is written mid-session (#180) — carries
    # the bundle directory path as a string.
    crash_detected = Signal(str)

    def log(self, text: str, color: str = "normal") -> None:
        self.log_message.emit(text, color)
        if color == "red":
            self.alarm_fired.emit(text)

    def refresh_region_toggles(self) -> None:
        self.toggles_changed.emit()

    def show_error(self, message: str) -> None:
        self.error.emit(message)

    def notify_update(self, tag: str) -> None:
        self.update_available.emit(tag)

    def refresh_context_line(self) -> None:
        self.context_changed.emit()

    def notify_crash(self, bundle_dir: str) -> None:
        self.crash_detected.emit(bundle_dir)
