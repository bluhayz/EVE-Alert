"""UIBridge protocol — the only interface between engine and GUI (Phase 0/7, #124/#131).

After the Phase 7 cutover TkBridge is removed; all GUI calls flow through
QtBridge (evealert/ui/qt_bridge.py).
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class UIBridge(Protocol):
    """Minimal surface the engine needs from the GUI layer."""

    def log(self, text: str, color: str = "normal") -> None:
        """Write a message to the GUI log (thread-safe)."""
        ...

    def refresh_region_toggles(self) -> None:
        """Refresh the alert/faction region button states."""
        ...

    def show_error(self, message: str) -> None:
        """Display an error dialog (thread-safe)."""
        ...
