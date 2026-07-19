"""Append every alarm/intel/killmail event to a CSV file -- example EVE
Alert plugin (v2 API).

Independent of EVE Alert's own session-report/statistics files -- a
flat, append-only CSV you can open in Excel/pandas for your own
analysis (e.g. "what time of day do I actually get jumped").

Setup: copy this file into your plugins folder, adjust LOG_PATH below if
you want it somewhere other than your home directory, restart EVE Alert.
"""

import csv
import os
import time

__version__ = "1.0"

LOG_PATH = os.path.join(os.path.expanduser("~"), "eve_alert_events.csv")
_FIELDS = ["timestamp", "event", "system", "detail"]


def _append_row(event: str, system: str = "", detail: str = "") -> None:
    is_new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event, "system": system, "detail": detail,
        })


def on_enemy(ctx, event):
    _append_row("enemy_alarm", event.system, event.timestamp)


def on_faction(ctx, event):
    _append_row("faction_alarm", event.system, event.timestamp)


def on_intel(ctx, report):
    _append_row("intel", detail=report.line)


def on_killmail(ctx, km):
    _append_row(
        "killmail", km.system_name or str(km.system_id) or "",
        f"killmail_id={km.killmail_id} jumps={km.jump_distance}",
    )


def on_threat_score(ctx, assessment):
    _append_row("threat_score", detail=f"{assessment.score}/10 {assessment.label}")
