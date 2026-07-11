"""Startup version check against GitHub Releases for EVE Alert.

Makes a single async GET to the GitHub Releases API and compares the latest
tag against the currently running version.  The check is fire-and-forget — if
the request fails (offline, rate-limited, API changed) it logs at DEBUG level
and returns None without raising.
"""

import logging

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.update")

_RELEASES_URL = "https://api.github.com/repos/bluhayz/EVE-Alert/releases/latest"
_HTTP_TIMEOUT = 5.0


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert '2.6.0' or 'v2.6.0' to (2, 6, 0) for comparison."""
    cleaned = v.lstrip("v").strip()
    try:
        return tuple(int(x) for x in cleaned.split("."))
    except ValueError:
        return (0,)


async def check_for_update(current_version: str) -> str | None:
    """Return the latest release tag string if newer than *current_version*, else None.

    Returns None on any network or parse error (non-fatal).
    """
    if not _HTTPX_AVAILABLE:
        logger.debug("httpx not available; skipping update check.")
        return None

    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"EVEAlert/{current_version}",
            },
        ) as client:
            resp = await client.get(_RELEASES_URL)
            resp.raise_for_status()
            tag = resp.json().get("tag_name", "")  # e.g. "v2.6.0"
    except Exception as exc:
        logger.debug("Update check failed: %s", exc)
        return None

    if not tag:
        return None

    if _version_tuple(tag) > _version_tuple(current_version):
        return tag
    return None
