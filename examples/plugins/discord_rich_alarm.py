"""Discord rich-embed alarm mirror -- example EVE Alert plugin (v2 API).

Posts a color-coded Discord embed for every Enemy/Faction alarm, using
ctx.fire_webhook() so no extra HTTP library is needed. Independent of
EVE Alert's own built-in webhook (Settings > Server) -- point this at a
different channel if you want alarms mirrored somewhere separate.

Setup: copy this file into your plugins folder, replace WEBHOOK_URL
below with a Discord webhook URL, restart EVE Alert (or Plugin Manager
> Reload All).
"""

__version__ = "1.0"

WEBHOOK_URL = "https://discord.com/api/webhooks/REPLACE/ME"

_COLOR_BY_ALARM_TYPE = {
    "Enemy": 0xE74C3C,   # red
    "Faction": 0xF1C40F,  # yellow
}


def on_enemy(ctx, event):
    _post(ctx, event)


def on_faction(ctx, event):
    _post(ctx, event)


def _post(ctx, event) -> None:
    if "REPLACE/ME" in WEBHOOK_URL:
        return  # not configured yet -- silently no-op rather than spam a 404

    payload = {
        "embeds": [{
            "title": f"{event.alarm_type} Appears!",
            "description": f"System: **{event.system}**\nTime: {event.timestamp}",
            "color": _COLOR_BY_ALARM_TYPE.get(event.alarm_type, 0x95A5A6),
        }]
    }
    ctx.fire_webhook(WEBHOOK_URL, payload)
