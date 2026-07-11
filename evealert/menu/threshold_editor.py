"""Per-image threshold editor window for EVE Alert.

Allows configuring a custom detection confidence threshold for each
template image individually, overriding the global slider for that image.
"""

import os
from typing import TYPE_CHECKING

import customtkinter

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu


class ThresholdEditorWindow:
    """Modal window to edit per-image thresholds.

    For each template image currently loaded, shows a toggle switch and a
    threshold slider. When the toggle is off, that image uses the global
    detectionscale threshold. When on, the slider value overrides it.

    Settings are saved immediately to settings.json on Close.
    """

    def __init__(self, main: "MainMenu") -> None:
        self.main = main
        self._sliders: dict = {}  # {basename: CTkSlider}
        self._switches: dict = {}  # {basename: CTkSwitch / BooleanVar}
        self._switch_vars: dict = {}

        self.window = customtkinter.CTkToplevel(main)
        self.window.title("Per-Image Thresholds")
        self.window.resizable(False, True)
        self.window.grab_set()  # modal

        self._build_ui()
        self._load_thresholds()

    def _get_all_image_names(self) -> list[str]:
        """Return basenames of all currently loaded alert and faction images."""
        names = []
        agent = self.main.alert
        for vision_obj in (agent.alert_vision, agent.alert_vision_faction):
            for path in getattr(vision_obj, "needle_paths", []):
                names.append(os.path.basename(path))
        return names

    def _build_ui(self) -> None:
        frame = customtkinter.CTkScrollableFrame(self.window, width=420, height=360)
        frame.pack(padx=10, pady=10, fill="both", expand=True)

        header = customtkinter.CTkLabel(
            frame,
            text="Enable toggle to override the global threshold for that image.",
            wraplength=380,
            justify="left",
        )
        header.pack(pady=(0, 8))

        image_names = self._get_all_image_names()
        if not image_names:
            customtkinter.CTkLabel(frame, text="No template images loaded.").pack()
            return

        for name in image_names:
            row_frame = customtkinter.CTkFrame(frame)
            row_frame.pack(fill="x", pady=2, padx=4)

            var = customtkinter.BooleanVar(value=False)
            self._switch_vars[name] = var

            switch = customtkinter.CTkSwitch(
                row_frame,
                text="",
                variable=var,
                width=40,
                command=lambda n=name: self._on_toggle(n),
            )
            switch.pack(side="left", padx=(4, 8))
            self._switches[name] = switch

            lbl = customtkinter.CTkLabel(row_frame, text=name, width=200, anchor="w")
            lbl.pack(side="left")

            slider = customtkinter.CTkSlider(
                row_frame,
                from_=1,
                to=100,
                number_of_steps=99,
                width=100,
            )
            slider.set(80)
            slider.pack(side="left", padx=(4, 0))
            self._sliders[name] = slider

            val_lbl = customtkinter.CTkLabel(row_frame, text="80", width=30)
            val_lbl.pack(side="left", padx=(4, 0))
            slider.configure(
                command=lambda v, lbl=val_lbl: lbl.configure(text=str(int(v)))
            )

        btn_frame = customtkinter.CTkFrame(self.window)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        customtkinter.CTkButton(
            btn_frame, text="Save & Close", command=self._save_and_close
        ).pack(side="left", padx=4)
        customtkinter.CTkButton(
            btn_frame, text="Cancel", command=self.window.destroy
        ).pack(side="left", padx=4)
        customtkinter.CTkButton(
            btn_frame, text="Clear All Overrides", command=self._clear_all
        ).pack(side="right", padx=4)

    def _on_toggle(self, name: str) -> None:
        """Enable/disable the slider for this image."""
        slider = self._sliders.get(name)
        if slider:
            state = "normal" if self._switch_vars[name].get() else "disabled"
            slider.configure(state=state)

    def _load_thresholds(self) -> None:
        """Populate UI from current settings.image_thresholds."""
        settings = self.main.menu.setting.load_settings()
        thresholds = settings.get("image_thresholds", {})
        for name, slider in self._sliders.items():
            val = thresholds.get(name)
            if val is not None:
                self._switch_vars[name].set(True)
                slider.set(int(val))
                slider.configure(state="normal")
            else:
                self._switch_vars[name].set(False)
                slider.configure(state="disabled")

    def _save_and_close(self) -> None:
        """Write per-image thresholds to settings and close."""
        settings = self.main.menu.setting.load_settings()
        thresholds: dict = {}
        for name, var in self._switch_vars.items():
            if var.get():
                thresholds[name] = int(self._sliders[name].get())
            else:
                thresholds[name] = None
        settings["image_thresholds"] = thresholds
        self.main.menu.setting.save_settings(settings)
        self.main.write_message("Per-image thresholds saved.", "green")
        self.window.destroy()

    def _clear_all(self) -> None:
        for var in self._switch_vars.values():
            var.set(False)
        for slider in self._sliders.values():
            slider.configure(state="disabled")
