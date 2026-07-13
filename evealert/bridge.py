"""GUI-independent UIBridge protocol + TkBridge implementation (Phase 0, #124).

AlertAgent talks only to UIBridge.  The Tk app provides TkBridge; Phase 2
will provide QtBridge.  The engine never imports tkinter.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu


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


class TkBridge:
    """UIBridge backed by the customtkinter MainMenu.

    All calls are dispatched to the Tk main thread via after(0) so the
    engine can call these methods from any thread safely.
    """

    def __init__(self, main: "MainMenu"):
        self._main = main

    def log(self, text: str, color: str = "normal") -> None:
        self._main.after(0, lambda: self._main.write_message(text, color))

    def refresh_region_toggles(self) -> None:
        self._main.after(0, self._main.update_alert_button)
        self._main.after(0, self._main.update_faction_button)

    def show_error(self, message: str) -> None:
        self._main.after(0, lambda: self._main.open_error_window(message))
