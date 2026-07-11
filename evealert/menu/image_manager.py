"""Image management UI for EVE Alert.

Allows users to add and remove custom detection template images without
manually copying files. Built-in templates (bundled in the app) are shown
read-only; user-added templates (in the platformdirs user img/ directory)
can be deleted.
"""

import os
import shutil
from typing import TYPE_CHECKING

import customtkinter

from evealert.constants import ALERT_IMAGE_PREFIX, FACTION_IMAGE_PREFIX
from evealert.settings.helper import get_resource_path, get_user_img_path

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu

IMG_FOLDER = "img"


class ImageManagerWindow:
    """Modal window to add, remove, and preview detection template images.

    Shows two tabs: Alert Images (image_*) and Faction Images (faction_*).
    Images in the user img directory can be removed; bundled images are
    shown as read-only. Adding an image copies it to the user img dir.
    """

    def __init__(self, main: "MainMenu") -> None:
        self.main = main

        self.window = customtkinter.CTkToplevel(main)
        self.window.title("Image Manager")
        self.window.geometry("520x420")
        self.window.resizable(False, True)
        self.window.grab_set()

        self._build_ui()

    def _build_ui(self) -> None:
        self.tabview = customtkinter.CTkTabview(self.window, width=500, height=360)
        self.tabview.pack(padx=10, pady=10, fill="both", expand=True)

        self.tabview.add("Alert Images")
        self.tabview.add("Faction Images")

        self._build_tab(
            self.tabview.tab("Alert Images"),
            ALERT_IMAGE_PREFIX,
        )
        self._build_tab(
            self.tabview.tab("Faction Images"),
            FACTION_IMAGE_PREFIX,
        )

        btn_frame = customtkinter.CTkFrame(self.window)
        btn_frame.pack(fill="x", padx=10, pady=(0, 8))

        customtkinter.CTkButton(
            btn_frame,
            text="Reload Detection Engine",
            command=self._reload_engine,
        ).pack(side="left", padx=4)
        customtkinter.CTkButton(
            btn_frame,
            text="Close",
            command=self.window.destroy,
        ).pack(side="right", padx=4)

    def _build_tab(self, tab, prefix: str) -> None:
        """Build the scrollable image list for one tab."""
        user_dir = get_user_img_path()
        bundled_dir = get_resource_path(IMG_FOLDER)

        info_label = customtkinter.CTkLabel(
            tab,
            text=f"Bundled: {bundled_dir}\nUser: {user_dir}",
            justify="left",
            font=("", 10),
        )
        info_label.pack(anchor="w", padx=8, pady=(4, 0))

        scroll = customtkinter.CTkScrollableFrame(tab, height=220)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # Collect and display all images with this prefix
        self._populate_list(scroll, bundled_dir, user_dir, prefix)

        btn_row = customtkinter.CTkFrame(tab)
        btn_row.pack(fill="x", padx=4, pady=4)

        customtkinter.CTkButton(
            btn_row,
            text=f"Add {prefix[:-1].replace('_', ' ').title()} Image...",
            command=lambda p=prefix: self._add_image(p, scroll, bundled_dir, user_dir),
        ).pack(side="left", padx=4)

    def _populate_list(self, parent, bundled_dir: str, user_dir, prefix: str) -> None:
        """Populate the scrollable list with image rows."""
        # Clear existing rows
        for widget in parent.winfo_children():
            widget.destroy()

        rows: list[tuple[str, bool]] = []  # (filepath, is_user_file)

        for d, is_user in [(bundled_dir, False), (str(user_dir), True)]:
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if f.startswith(prefix):
                    rows.append((os.path.join(d, f), is_user))

        if not rows:
            customtkinter.CTkLabel(parent, text="No images found.").pack()
            return

        for filepath, is_user in rows:
            row_frame = customtkinter.CTkFrame(parent)
            row_frame.pack(fill="x", pady=1, padx=2)

            source = "user" if is_user else "built-in"
            lbl = customtkinter.CTkLabel(
                row_frame,
                text=f"[{source}]  {os.path.basename(filepath)}",
                anchor="w",
                width=340,
            )
            lbl.pack(side="left", padx=4)

            if is_user:
                customtkinter.CTkButton(
                    row_frame,
                    text="Remove",
                    width=70,
                    fg_color="#b03a2e",
                    command=lambda fp=filepath, par=parent, bd=bundled_dir, ud=user_dir, pr=prefix: self._remove_image(
                        fp, par, bd, ud, pr
                    ),
                ).pack(side="right", padx=4)

    def _add_image(self, prefix: str, scroll, bundled_dir: str, user_dir) -> None:
        """Open a file dialog and copy the selected image to the user img dir."""
        import tkinter.filedialog  # pylint: disable=import-outside-toplevel

        paths = tkinter.filedialog.askopenfilenames(
            title="Select image(s) to add",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")],
        )
        if not paths:
            return

        user_dir_path = get_user_img_path()
        added = 0
        for src_path in paths:
            fname = os.path.basename(src_path)
            if not fname.startswith(prefix):
                # Auto-prefix: insert prefix before the filename
                fname = f"{prefix}{fname}"
            dest = user_dir_path / fname
            try:
                shutil.copy2(src_path, dest)
                added += 1
            except OSError as e:
                self.main.write_message(f"Could not copy {fname}: {e}", "red")

        if added:
            self.main.write_message(
                f"{added} image(s) added to user directory. "
                "Click 'Reload Detection Engine' to apply.",
                "green",
            )
            self._populate_list(scroll, bundled_dir, user_dir_path, prefix)

    def _remove_image(
        self, filepath: str, scroll, bundled_dir: str, user_dir, prefix: str
    ) -> None:
        """Delete a user-added image after confirmation."""
        import tkinter.messagebox  # pylint: disable=import-outside-toplevel

        fname = os.path.basename(filepath)
        if not tkinter.messagebox.askyesno("Remove Image", f"Delete '{fname}'?"):
            return
        try:
            os.remove(filepath)
            self.main.write_message(f"Removed: {fname}", "green")
        except OSError as e:
            self.main.write_message(f"Could not remove {fname}: {e}", "red")
            return
        self._populate_list(scroll, bundled_dir, get_user_img_path(), prefix)

    def _reload_engine(self) -> None:
        """Force the alert engine to reload its template image lists."""
        self.main.menu.setting.changed = True
        if self.main.alert.is_running:
            # Trigger the hot-reload path in run() on next cycle
            self.main.alert.load_settings()
            self.main.write_message(
                "Detection engine reloaded with updated images.", "green"
            )
        else:
            self.main.write_message(
                "Images updated — will be loaded on next Start.", "green"
            )
