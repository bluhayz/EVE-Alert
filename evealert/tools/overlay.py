import platform
from typing import TYPE_CHECKING, Optional

import customtkinter

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu


class OverlaySystem:
    """Screen overlay system for visual region selection.

    Creates a semi-transparent fullscreen overlay that allows users to
    select rectangular regions on the screen using marquee selection.
    Used for defining alert and faction detection regions.

    Attributes:
        main: Reference to MainMenu instance
        start_x: Starting X coordinate of selection
        start_y: Starting Y coordinate of selection
        end_x: Ending X coordinate of selection
        end_y: Ending Y coordinate of selection
        rect: Canvas rectangle object for visual feedback
        overlay: Toplevel window for the overlay
        canvas: Canvas widget for drawing selection rectangle
    """

    def __init__(self, mainmenu: "MainMenu") -> None:
        """Initialize the overlay system.

        Args:
            mainmenu: Reference to the MainMenu instance
        """
        self.main = mainmenu
        self.start_x: Optional[int] = None
        self.start_y: Optional[int] = None
        self.end_x: Optional[int] = None
        self.end_y: Optional[int] = None
        self.rect = None
        self.overlay = None
        self.canvas = None
        self._monitor = None  # monitor cached at create_overlay time
        self._x_offset = 0  # DWM border offset applied to overlay position

    def create_overlay(self, monitor) -> None:
        """Create a fullscreen overlay on the specified monitor.

        Args:
            monitor: Monitor object with position and dimensions
        """
        self.clean_up()
        self.overlay = customtkinter.CTkToplevel(self.main)
        self.overlay.attributes("-alpha", 0.3)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="black")

        x_offset = -10 if platform.system() == "Windows" else 0
        self._x_offset = x_offset  # cache for use in on_button_release
        self._monitor = monitor  # cache monitor so release uses correct origin
        monitor_x = monitor.x + x_offset
        monitor_y = monitor.y

        self.overlay.geometry(
            f"{monitor.width}x{monitor.height}+{(monitor_x)}+{monitor_y}"
        )
        self.overlay.protocol("WM_DELETE_WINDOW", self.clean_up)

        self.canvas = customtkinter.CTkCanvas(
            self.overlay, bg="black", highlightthickness=0
        )
        self.canvas.pack(fill=customtkinter.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)

    def clean_up(self) -> None:
        """Destroy the overlay and reset all selection state."""
        if self.overlay:
            self.overlay.destroy()
            self.overlay = None
            self.main.menu.config.faction_region = False
            self.main.menu.config.alert_region = False
        if self.canvas:
            self.canvas.destroy()
            self.canvas = None
        self.start_x = None
        self.start_y = None
        self.end_x = None
        self.end_y = None
        self.rect = None

    def on_button_press(self, event) -> None:
        """Handle mouse button press to start region selection.

        Args:
            event: Mouse event with x, y coordinates
        """
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="red",
            width=3,
        )

    def on_mouse_drag(self, event) -> None:
        """Handle mouse drag to update selection rectangle."""
        if self.rect is None or self.canvas is None:
            return
        cur_x, cur_y = (event.x, event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_button_release(self, event) -> None:
        """Handle mouse button release to finalize region selection.

        Normalizes coordinates, adjusts for monitor offset, and saves
        the selected region to either alert or faction settings.

        Args:
            event: Mouse event with final x, y coordinates
        """
        self.end_x, self.end_y = (event.x, event.y)

        # Guard: ButtonPress may not have fired (e.g. concurrent clean_up)
        if self.start_x is None:
            return

        if self.end_x < self.start_x:
            self.start_x, self.end_x = self.end_x, self.start_x
        if self.end_y < self.start_y:
            self.start_y, self.end_y = self.end_y, self.start_y

        # Use the monitor cached at create_overlay time so the correct
        # origin is applied even if the cursor has moved to a different monitor.
        monitor = self._monitor
        if monitor:
            # Apply the same x_offset that was used to position the overlay
            # window so canvas coordinates map correctly to screen coordinates.
            self.start_x += monitor.x + self._x_offset
            self.start_y += monitor.y
            self.end_x += monitor.x + self._x_offset
            self.end_y += monitor.y

        self.main.write_message(
            f"Selected region: ({self.start_x}, {self.start_y}) to ({self.end_x}, {self.end_y})"
        )

        if self.main.menu.config.is_alert_region:
            self.set_alert_region()
        elif self.main.menu.config.is_faction_region:
            self.set_faction_region()

    def set_alert_region(self) -> None:
        """Save the selected region as the alert detection area.

        Applies a 30-pixel Y-offset correction and saves coordinates
        to settings. Marks configuration as changed.
        """
        settings = self.main.menu.setting.load_settings()
        settings["alert_region_1"]["x"] = self.start_x
        # On Windows, compensate for title bar / taskbar offset (+30px).
        y_offset = 30 if platform.system() == "Windows" else 0
        settings["alert_region_1"]["y"] = self.start_y + y_offset

        settings["alert_region_2"]["x"] = self.end_x
        settings["alert_region_2"]["y"] = self.end_y + y_offset

        self.main.menu.setting.save_settings(settings)
        self.main.menu.config.changed = True
        self.clean_up()
        self.main.write_message("Settings: Enemy Deactivated.")

    def set_faction_region(self) -> None:
        """Save the selected region as the faction detection area.

        Applies a 30-pixel Y-offset correction and saves coordinates
        to settings. Marks configuration as changed.
        """
        settings = self.main.menu.setting.load_settings()
        settings["faction_region_1"]["x"] = self.start_x
        # On Windows, compensate for title bar / taskbar offset (+30px).
        y_offset = 30 if platform.system() == "Windows" else 0
        settings["faction_region_1"]["y"] = self.start_y + y_offset

        settings["faction_region_2"]["x"] = self.end_x
        settings["faction_region_2"]["y"] = self.end_y + y_offset

        self.main.menu.setting.save_settings(settings)
        self.main.menu.config.changed = True
        self.clean_up()
        self.main.write_message("Settings: Faction Deactivated.")
