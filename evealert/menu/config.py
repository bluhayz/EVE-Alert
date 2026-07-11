from typing import TYPE_CHECKING

import customtkinter

from evealert.tools.window_finder import find_eve_window

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu


class ConfigModeMenu:
    """Configuration mode menu for region selection.

    Provides a guide window with instructions for selecting alert and faction
    regions using keyboard hotkeys (F1/F2). The CTkToplevel window is created
    lazily on first open to avoid the macOS startup flash.
    """

    def __init__(self, main: "MainMenu") -> None:
        self.main = main
        self.open = False

        self.alert_region = False
        self.faction_region = False

        self._window_created = False

    def _ensure_window(self) -> None:
        """Create the CTkToplevel and widgets on first call (lazy init)."""
        if self._window_created:
            return
        self._window_created = True

        self.description_window = customtkinter.CTkToplevel(self.main)
        self.description_window.title("Config Mode")
        self.description_window.withdraw()

        description_text = "Alert Region: Press F1 to activate.\n"
        description_text += "Faction Mode: Press F2 to activate.\n"
        description_text += (
            "\nAfter pressing F1 or F2 set your region with Marquee Selection.\n"
        )
        description_text += "\nTo abort everything Press ESC.\n"

        menu_frame = customtkinter.CTkFrame(self.description_window)
        menu_frame.pack(side="left", padx=20, pady=20)

        description_label = customtkinter.CTkLabel(
            menu_frame, text=description_text, justify="left"
        )
        description_label.pack(padx=20, pady=20)

        close_button = customtkinter.CTkButton(
            menu_frame, text="Close", command=self.clean_up
        )
        close_button.pack(pady=10)

        detect_button = customtkinter.CTkButton(
            menu_frame,
            text="Detect EVE Window",
            command=self._detect_eve_window,
        )
        detect_button.pack(pady=(0, 10))

        image_mgr_button = customtkinter.CTkButton(
            menu_frame,
            text="Image Manager",
            command=self._open_image_manager,
        )
        image_mgr_button.pack(pady=(0, 10))

        self.description_window.protocol("WM_DELETE_WINDOW", self.clean_up)

    @property
    def is_open(self) -> bool:
        return self.open

    @property
    def is_alert_region(self) -> bool:
        return self.alert_region

    @property
    def is_faction_region(self) -> bool:
        return self.faction_region

    def _open_image_manager(self) -> None:
        """Open the Image Manager window."""
        from evealert.menu.image_manager import (  # pylint: disable=import-outside-toplevel
            ImageManagerWindow,
        )

        ImageManagerWindow(self.main)

    def _detect_eve_window(self) -> None:
        """Auto-detect the EVE Online client window and pre-fill region settings."""
        bounds = find_eve_window()
        if bounds is None:
            self.main.write_message(
                "EVE window not found. Make sure EVE Online is running and visible.",
                "red",
            )
            return

        left, top, width, height = bounds
        # Pre-populate both alert and faction regions with the full EVE window bounds
        settings = self.main.menu.setting.load_settings()
        settings["alert_region_1"] = {"x": left, "y": top}
        settings["alert_region_2"] = {"x": left + width, "y": top + height}
        settings["faction_region_1"] = {"x": left, "y": top}
        settings["faction_region_2"] = {"x": left + width, "y": top + height}
        self.main.menu.setting.save_settings(settings)
        self.main.write_message(
            f"EVE window detected: ({left},{top}) {width}x{height} — regions pre-filled. "
            "Adjust with F1/F2 to narrow to specific areas.",
            "green",
        )

    def clean_up(self) -> None:
        """Close the configuration window and reset button color."""
        if self.is_open:
            self.open = False
            self.main.mainmenu_buttons.config_mode_menu.configure(
                fg_color="#1f538d", hover_color="#14375e"
            )
            if self._window_created:
                self.description_window.withdraw()

    def open_menu(self) -> None:
        """Open or close the configuration mode guide window."""
        if not self.is_open:
            self._ensure_window()
            self.open = True
            self.main.mainmenu_buttons.config_mode_menu.configure(
                fg_color="#fa0202", hover_color="#bd291e"
            )

            main_menu_x = self.main.winfo_x()
            main_menu_y = self.main.winfo_y()
            main_menu_width = self.main.winfo_width()

            description_window_width = 435
            description_window_height = 300

            raw_x = main_menu_x + main_menu_width + 10
            raw_y = main_menu_y

            # Clamp to screen bounds so popup never opens off-screen
            screen_w = self.main.winfo_screenwidth()
            screen_h = self.main.winfo_screenheight()
            window_x = min(raw_x, screen_w - description_window_width - 10)
            window_y = min(max(raw_y, 10), screen_h - description_window_height - 10)

            self.description_window.geometry(
                f"{description_window_width}x{description_window_height}"
                f"+{window_x}+{window_y}"
            )
            self.description_window.deiconify()
        else:
            self.clean_up()
