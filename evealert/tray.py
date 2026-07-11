"""System tray integration for EVE Alert.

Provides a TrayManager that shows a system tray icon so the app can run
in the background without occupying the taskbar/Dock. All callbacks from
the tray thread are marshalled to the main Tkinter thread via after(0, ...).
"""

import logging
from threading import Thread
from typing import TYPE_CHECKING

try:
    import pystray
    from PIL import Image

    _PYSTRAY_AVAILABLE = True
except ImportError:
    _PYSTRAY_AVAILABLE = False

from evealert.settings.helper import get_resource_path

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu

logger = logging.getLogger("main")


class TrayManager:
    """Manages the system tray icon and menu.

    The pystray.Icon.run() call blocks, so it runs in a daemon thread.
    All icon callbacks that need to touch Tkinter widgets use
    self.main.after(0, callable) to marshal back to the main thread.
    """

    def __init__(self, main: "MainMenu") -> None:
        self.main = main
        self._icon = None
        self.available = _PYSTRAY_AVAILABLE

    def start(self) -> None:
        """Create and start the tray icon in a daemon thread."""
        if not self.available:
            logger.warning(
                "pystray not installed — system tray disabled. "
                "Install with: pip install pystray"
            )
            return

        try:
            img = Image.open(get_resource_path("img/eve.png")).resize((64, 64))
        except Exception as e:
            logger.error("Could not load tray icon: %s", e)
            return

        menu = pystray.Menu(
            pystray.MenuItem("Show EVE Alert", self._show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start Detection", self._start),
            pystray.MenuItem("Stop Detection", self._stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._exit),
        )

        self._icon = pystray.Icon(
            name="EVE Alert",
            icon=img,
            title="EVE Alert",
            menu=menu,
        )
        Thread(target=self._icon.run, daemon=True, name="TrayThread").start()
        logger.debug("Tray icon started.")

    def stop(self) -> None:
        """Stop and remove the tray icon."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as e:
                logger.debug("Tray stop error (ignored): %s", e)
            self._icon = None

    def notify(self, title: str, message: str) -> None:
        """Show a tray notification if the platform supports it."""
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass  # not all platforms support notifications

    # ------------------------------------------------------------------
    # Tray menu callbacks (run on pystray's thread — must use after(0,...))
    # ------------------------------------------------------------------

    def _show(self, icon=None, item=None) -> None:
        self.main.after(0, self.main.deiconify)
        self.main.after(0, self.main.lift)

    def _start(self, icon=None, item=None) -> None:
        self.main.after(0, self.main.start_alert_script)

    def _stop(self, icon=None, item=None) -> None:
        self.main.after(0, self.main.stop_alert_script)

    def _exit(self, icon=None, item=None) -> None:
        self.main.after(0, self.main.clean_up)
