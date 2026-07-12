"""Diagnostic helpers for EVE Alert.

Provides environment-context gathering, settings redaction, and a one-shot
bundle export (zip of logs + redacted settings + system info) for remote
debugging.
"""

import copy
import json
import logging
import os
import platform
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("main")

# Keys within a settings dict whose values should be blanked for privacy.
# Tuples represent nested paths: ("push", "telegram_token") → settings["push"]["telegram_token"]
_REDACT_PATHS: list[tuple] = [
    ("push", "telegram_token"),
    ("push", "pushover_token"),
    ("push", "pushover_user"),
    ("push", "ntfy_url"),
    ("server", "webhook"),
    ("esi_oauth", "client_id"),
]

_REDACTED = "***REDACTED***"


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


def gather_context(settings: dict | None = None) -> dict:
    """Collect non-secret diagnostic context about the runtime environment.

    Returns a dict with keys: app, platform, python, monitors, eve_dirs,
    ocr, features.  No secrets are included regardless of *settings*.
    """
    from evealert import __version__  # avoid circular at module level

    ctx: dict[str, Any] = {}

    # App version
    ctx["app"] = {"version": __version__}

    # Platform
    ctx["platform"] = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "node": platform.node(),
    }

    # Python
    ctx["python"] = {
        "version": sys.version,
        "executable": sys.executable,
    }

    # Monitor/screen info
    try:
        import screeninfo  # type: ignore

        monitors = []
        for m in screeninfo.get_monitors():
            monitors.append(
                {
                    "x": m.x,
                    "y": m.y,
                    "width": m.width,
                    "height": m.height,
                    "is_primary": getattr(m, "is_primary", None),
                }
            )
        ctx["monitors"] = monitors
    except Exception as exc:
        ctx["monitors"] = {"error": str(exc)}

    # EVE chat-log and d-scan directories
    try:
        from evealert.tools.intel_watcher import get_eve_chatlog_dir  # type: ignore

        chatlog = get_eve_chatlog_dir()
        ctx["eve_dirs"] = {
            "chatlog_dir": str(chatlog) if chatlog else None,
            "chatlog_found": chatlog is not None,
        }
    except Exception as exc:
        ctx["eve_dirs"] = {"error": str(exc)}

    # OCR availability
    try:
        from evealert.tools.ocr_local import is_ocr_available  # type: ignore

        available = is_ocr_available()
        tesseract_version = None
        if available:
            try:
                import pytesseract  # type: ignore

                tesseract_version = pytesseract.get_tesseract_version().vstring
            except Exception:
                pass
        ctx["ocr"] = {
            "pytesseract_available": available,
            "tesseract_version": tesseract_version,
        }
    except Exception as exc:
        ctx["ocr"] = {"error": str(exc)}

    # Feature flags + regions from settings (non-secret keys only)
    if settings:
        try:
            ctx["features"] = {
                "esi_enabled": bool(settings.get("esi_enabled", False)),
                "kos_enabled": bool(settings.get("kos", {}).get("enabled", False)),
                "ocr_enabled": bool(settings.get("ocr", {}).get("enabled", False)),
                "ocr_region": settings.get("ocr", {}).get("region"),
                "dscan_enabled": bool(settings.get("dscan", {}).get("enabled", False)),
                "wormhole_enabled": bool(
                    settings.get("wormhole", {}).get("enabled", False)
                ),
                "fleet_enabled": bool(settings.get("fleet", {}).get("enabled", False)),
                "adjacent_enabled": bool(
                    settings.get("adjacent", {}).get("enabled", False)
                ),
                "diagnostics_enabled": bool(
                    settings.get("diagnostics", {}).get("enabled", False)
                ),
                "log_level": settings.get("log_level", "INFO"),
                "alert_region": {
                    "x1": settings.get("x1"),
                    "y1": settings.get("y1"),
                    "x2": settings.get("x2"),
                    "y2": settings.get("y2"),
                },
            }
        except Exception as exc:
            ctx["features"] = {"error": str(exc)}

    return ctx


def write_context_log(settings: dict | None = None) -> None:
    """Log the diagnostic context at INFO level so it lands in the log file."""
    ctx = gather_context(settings)
    logger.info("=== EVE Alert Diagnostic Context ===")
    for section, data in ctx.items():
        logger.info("  [%s] %s", section, json.dumps(data, default=str))
    logger.info("=== End Diagnostic Context ===")


# ---------------------------------------------------------------------------
# Settings redaction
# ---------------------------------------------------------------------------


def _redact_settings(settings: dict) -> dict:
    """Return a deep copy of *settings* with sensitive values blanked.

    Only non-empty values are replaced so the recipient can still tell
    whether a field was configured at all.
    """
    out = copy.deepcopy(settings)

    for path in _REDACT_PATHS:
        node = out
        for key in path[:-1]:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if isinstance(node, dict):
            leaf = path[-1]
            if leaf in node and node[leaf]:
                node[leaf] = _REDACTED

    # Redact any webhook URL fields nested under "webhooks" list
    if isinstance(out.get("webhooks"), list):
        for hook in out["webhooks"]:
            if isinstance(hook, dict) and hook.get("url"):
                hook["url"] = _REDACTED

    return out


# ---------------------------------------------------------------------------
# Bundle export
# ---------------------------------------------------------------------------


def create_bundle(settings: dict | None = None) -> Path:
    """Create a timestamped diagnostics zip in the config directory.

    The zip contains:
    - All *.log and *.log.* files from the logs directory.
    - redacted_settings.json  (settings with secrets blanked).
    - diagnostics_info.txt    (human-readable context dump).

    Returns the path to the created zip file.
    """
    from evealert.settings.logger import LOG_PATH  # avoid circular at import time

    config_dir = LOG_PATH.parent
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    bundle_path = config_dir / f"eve-alert-diagnostics-{ts}.zip"

    ctx = gather_context(settings)

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Log files
        if LOG_PATH.is_dir():
            for log_file in sorted(LOG_PATH.iterdir()):
                if log_file.suffix in (".log",) or ".log." in log_file.name:
                    zf.write(log_file, arcname=f"logs/{log_file.name}")

        # Redacted settings
        if settings:
            redacted = _redact_settings(settings)
            zf.writestr(
                "redacted_settings.json",
                json.dumps(redacted, indent=2, default=str),
            )

        # Human-readable context info
        lines = [
            "EVE Alert Diagnostics",
            "=" * 60,
            f"Generated: {datetime.now(tz=timezone.utc).isoformat()}",
            "",
            "NOTE: Log files may contain system names and character names",
            "from your EVE session. Secrets (tokens, OAuth IDs) have been",
            "redacted from settings but are NOT present in logs.",
            "",
        ]
        for section, data in ctx.items():
            lines.append(f"[{section}]")
            if isinstance(data, dict):
                for k, v in data.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"  {data}")
            lines.append("")
        zf.writestr("diagnostics_info.txt", os.linesep.join(lines))

    logger.info("Diagnostics bundle created: %s", bundle_path)
    return bundle_path


__all__ = [
    "gather_context",
    "write_context_log",
    "create_bundle",
    "_redact_settings",
]
