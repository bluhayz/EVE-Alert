"""Statistics window for EVE Alert System.

Displays alarm statistics including:
- Total and session alarm counts
- Alarm type breakdown
- Recent alarm history
- Session duration
- Past session reports (Sessions tab)
"""

import csv
import json
import os
from pathlib import Path
from tkinter import filedialog

import customtkinter

from evealert.constants import STATUS_CHECK_INTERVAL
from evealert.settings.stats_store import list_session_reports


class StatisticsWindow(customtkinter.CTkToplevel):
    """Statistics display window with Live Stats and Sessions tabs."""

    def __init__(self, main) -> None:
        super().__init__(main)
        self.main = main
        self.is_open = True

        self.title("EVE Alert - Statistics")
        self.geometry("520x600")
        self.protocol("WM_DELETE_WINDOW", self.close_window)

        self.init_widgets()
        self.update_statistics()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def init_widgets(self) -> None:
        outer = customtkinter.CTkFrame(self)
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        title_lbl = customtkinter.CTkLabel(
            outer,
            text="Alarm Statistics",
            font=customtkinter.CTkFont(size=20, weight="bold"),
        )
        title_lbl.pack(pady=(0, 10))

        self.tabs = customtkinter.CTkTabview(outer)
        self.tabs.pack(fill="both", expand=True)

        self.tabs.add("Live Stats")
        self.tabs.add("Sessions")

        self._build_live_tab(self.tabs.tab("Live Stats"))
        self._build_sessions_tab(self.tabs.tab("Sessions"))

    def _build_live_tab(self, parent) -> None:
        # Session info
        sf = customtkinter.CTkFrame(parent)
        sf.pack(fill="x", pady=(0, 12))
        customtkinter.CTkLabel(
            sf, text="Session Info", font=customtkinter.CTkFont(size=15, weight="bold")
        ).pack(pady=8)
        self.session_duration_label = customtkinter.CTkLabel(
            sf, text="Duration: 0s", font=customtkinter.CTkFont(size=13)
        )
        self.session_duration_label.pack(pady=4)

        # Lifetime totals
        tf = customtkinter.CTkFrame(parent)
        tf.pack(fill="x", pady=(0, 12))
        customtkinter.CTkLabel(
            tf,
            text="Lifetime Totals",
            font=customtkinter.CTkFont(size=15, weight="bold"),
        ).pack(pady=8)
        self.total_alarms_label = customtkinter.CTkLabel(
            tf, text="Total: 0", font=customtkinter.CTkFont(size=13)
        )
        self.total_alarms_label.pack(pady=3)
        self.total_enemy_label = customtkinter.CTkLabel(
            tf, text="Enemy: 0", font=customtkinter.CTkFont(size=13)
        )
        self.total_enemy_label.pack(pady=3)
        self.total_faction_label = customtkinter.CTkLabel(
            tf, text="Faction: 0", font=customtkinter.CTkFont(size=13)
        )
        self.total_faction_label.pack(pady=3)

        # Current session
        csf = customtkinter.CTkFrame(parent)
        csf.pack(fill="x", pady=(0, 12))
        customtkinter.CTkLabel(
            csf,
            text="Current Session",
            font=customtkinter.CTkFont(size=15, weight="bold"),
        ).pack(pady=8)
        self.session_alarms_label = customtkinter.CTkLabel(
            csf, text="Total: 0", font=customtkinter.CTkFont(size=13)
        )
        self.session_alarms_label.pack(pady=3)
        self.session_enemy_label = customtkinter.CTkLabel(
            csf, text="Enemy: 0", font=customtkinter.CTkFont(size=13)
        )
        self.session_enemy_label.pack(pady=3)
        self.session_faction_label = customtkinter.CTkLabel(
            csf, text="Faction: 0", font=customtkinter.CTkFont(size=13)
        )
        self.session_faction_label.pack(pady=3)

        # Recent history
        hf = customtkinter.CTkFrame(parent)
        hf.pack(fill="both", expand=True)
        customtkinter.CTkLabel(
            hf,
            text="Recent History (Last 10)",
            font=customtkinter.CTkFont(size=15, weight="bold"),
        ).pack(pady=8)
        self.history_textbox = customtkinter.CTkTextbox(hf, height=130, width=440)
        self.history_textbox.pack(pady=(0, 8), padx=10)

        # Action buttons
        bf = customtkinter.CTkFrame(parent)
        bf.pack(fill="x", pady=(6, 0))
        customtkinter.CTkButton(
            bf, text="Reset Session", command=self.reset_session, width=118
        ).pack(side="left", padx=4)
        customtkinter.CTkButton(
            bf, text="Clear History", command=self.clear_history, width=118
        ).pack(side="left", padx=4)
        customtkinter.CTkButton(
            bf, text="Export History", command=self.export_history, width=118
        ).pack(side="left", padx=4)

    def _build_sessions_tab(self, parent) -> None:
        top = customtkinter.CTkFrame(parent)
        top.pack(fill="x", pady=(0, 8))
        customtkinter.CTkLabel(
            top,
            text="Past Sessions",
            font=customtkinter.CTkFont(size=15, weight="bold"),
        ).pack(side="left", padx=10, pady=8)
        customtkinter.CTkButton(
            top, text="Refresh", command=self._refresh_sessions, width=90
        ).pack(side="right", padx=8, pady=8)
        customtkinter.CTkButton(
            top, text="Open Folder", command=self._open_sessions_folder, width=100
        ).pack(side="right", padx=4, pady=8)

        self.sessions_scroll = customtkinter.CTkScrollableFrame(parent, height=280)
        self.sessions_scroll.pack(fill="both", expand=True, pady=(0, 8))

        self.session_detail = customtkinter.CTkTextbox(parent, height=160)
        self.session_detail.pack(fill="x", padx=0, pady=(0, 4))
        self.session_detail.insert("1.0", "Select a session to view details.")
        self.session_detail.configure(state="disabled")

        self._refresh_sessions()

    # ------------------------------------------------------------------
    # Sessions tab helpers
    # ------------------------------------------------------------------

    def _refresh_sessions(self) -> None:
        for widget in self.sessions_scroll.winfo_children():
            widget.destroy()

        reports = list_session_reports()
        if not reports:
            customtkinter.CTkLabel(
                self.sessions_scroll,
                text="No saved sessions found.",
                font=customtkinter.CTkFont(size=13),
            ).pack(pady=20)
            return

        for path in reports:
            row = customtkinter.CTkFrame(self.sessions_scroll)
            row.pack(fill="x", pady=2, padx=2)
            name = path.stem  # e.g. session_20240501_153022
            customtkinter.CTkLabel(
                row,
                text=name,
                font=customtkinter.CTkFont(size=12),
                anchor="w",
            ).pack(side="left", padx=8, pady=4, expand=True, fill="x")
            customtkinter.CTkButton(
                row,
                text="View",
                width=55,
                command=lambda p=path: self._view_session(p),
            ).pack(side="left", padx=2)
            customtkinter.CTkButton(
                row,
                text="Delete",
                width=55,
                fg_color="#8B0000",
                hover_color="#660000",
                command=lambda p=path: self._delete_session(p),
            ).pack(side="left", padx=4)

    def _view_session(self, path: Path) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            self._set_detail(f"Could not read session file: {e}")
            return

        lines = [
            f"Session:  {data.get('session_start', '?')}  →  {data.get('session_end', '?')}",
            f"Duration: {data.get('duration', '?')}",
            f"Alarms:   {data.get('session_alarms', 0)}  "
            f"(Enemy: {data.get('total_enemy', 0)}, Faction: {data.get('total_faction', 0)})",
            "",
            "History:",
        ]
        for ev in data.get("history", []):
            lines.append(f"  [{ev.get('time', '?')}] {ev.get('type', '?')}")
        self._set_detail("\n".join(lines))

    def _delete_session(self, path: Path) -> None:
        try:
            os.remove(path)
        except OSError:
            pass
        self._refresh_sessions()
        self._set_detail("Session deleted.")

    def _open_sessions_folder(self) -> None:
        from evealert.settings.stats_store import get_sessions_dir

        folder = str(get_sessions_dir())
        try:
            import platform
            import subprocess

            if platform.system() == "Windows":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    def _set_detail(self, text: str) -> None:
        self.session_detail.configure(state="normal")
        self.session_detail.delete("1.0", "end")
        self.session_detail.insert("1.0", text)
        self.session_detail.configure(state="disabled")

    # ------------------------------------------------------------------
    # Live stats refresh
    # ------------------------------------------------------------------

    def update_statistics(self) -> None:
        if not self.is_open:
            return

        stats = self.main.alert.get_statistics()

        self.session_duration_label.configure(
            text=f"Duration: {stats.get_session_duration()}"
        )
        self.total_alarms_label.configure(text=f"Total: {stats.total_alarms}")
        self.total_enemy_label.configure(text=f"Enemy: {stats.total_by_type['Enemy']}")
        self.total_faction_label.configure(
            text=f"Faction: {stats.total_by_type['Faction']}"
        )
        self.session_alarms_label.configure(text=f"Total: {stats.session_alarms}")
        self.session_enemy_label.configure(
            text=f"Enemy: {stats.session_by_type['Enemy']}"
        )
        self.session_faction_label.configure(
            text=f"Faction: {stats.session_by_type['Faction']}"
        )

        self.history_textbox.delete("1.0", "end")
        recent = stats.get_recent_history(10)
        if recent:
            for event in recent:
                self.history_textbox.insert(
                    "end", f"[{event.formatted_time()}] {event.alarm_type}\n"
                )
        else:
            self.history_textbox.insert("end", "No alarms yet in this session.")

        self.after(STATUS_CHECK_INTERVAL, self.update_statistics)

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def reset_session(self) -> None:
        self.main.alert.get_statistics().reset_session()
        self.main.write_message("Statistics: Session reset.", "green")
        self.update_statistics()

    def clear_history(self) -> None:
        self.main.alert.get_statistics().clear_history()
        self.main.write_message("Statistics: History cleared.", "green")
        self.update_statistics()

    def export_history(self) -> None:
        stats = self.main.alert.get_statistics()
        if len(stats.alarm_history) == 0:
            self.main.write_message("Statistics: No history to export.", "yellow")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[
                ("CSV files", "*.csv"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
            title="Export Alarm History",
        )
        if not file_path:
            return

        try:
            if file_path.endswith(".json"):
                self._export_json(file_path, stats)
            else:
                self._export_csv(file_path, stats)
            self.main.write_message(f"Statistics: Exported to {file_path}", "green")
        except Exception as e:
            self.main.write_message(f"Statistics: Export failed. {e}", "red")

    def _export_csv(self, file_path: str, stats) -> None:
        with open(file_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Timestamp", "Alarm Type"])
            for event in stats.alarm_history:
                writer.writerow([event.formatted_time(), event.alarm_type])

    def _export_json(self, file_path: str, stats) -> None:
        data = {
            "export_info": {
                "total_alarms": stats.total_alarms,
                "session_alarms": stats.session_alarms,
                "session_duration": stats.get_session_duration(),
                "total_by_type": stats.total_by_type,
                "session_by_type": stats.session_by_type,
            },
            "history": [
                {"timestamp": event.formatted_time(), "alarm_type": event.alarm_type}
                for event in stats.alarm_history
            ],
        }
        with open(file_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def close_window(self) -> None:
        self.is_open = False
        self.destroy()
