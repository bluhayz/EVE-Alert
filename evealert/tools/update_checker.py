"""Version check and asset download against GitHub Releases for EVE Alert.

Provides three public helpers:

* ``check_for_update(current_version)``   — async, returns tag string or None
* ``fetch_latest_asset_url(tag)``         — async, returns .exe download URL
* ``download_release(url, dest, progress_cb)`` — async, streams .exe to disk
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.update")

_RELEASES_URL = "https://api.github.com/repos/bluhayz/EVE-Alert/releases/latest"
_RELEASE_TAG_URL = "https://api.github.com/repos/bluhayz/EVE-Alert/releases/tags/{tag}"
_HTTP_TIMEOUT = 10.0
_CHUNK_SIZE = 65_536  # 64 KB


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
            tag = resp.json().get("tag_name", "")  # e.g. "v6.3.8"
    except Exception as exc:
        logger.debug("Update check failed: %s", exc)
        return None

    if not tag:
        return None

    if _version_tuple(tag) > _version_tuple(current_version):
        return tag
    return None


async def fetch_latest_asset_url(tag: str) -> str | None:
    """Return the browser_download_url of the first .exe asset in *tag*.

    Returns None on any network or parse error.
    """
    if not _HTTPX_AVAILABLE:
        return None

    url = _RELEASE_TAG_URL.format(tag=tag)
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "EVEAlert/updater",
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            assets = resp.json().get("assets", [])
    except Exception as exc:
        logger.debug("Failed to fetch release assets for %s: %s", tag, exc)
        return None

    for asset in assets:
        name: str = asset.get("name", "")
        if name.lower().endswith(".exe"):
            return asset.get("browser_download_url")

    logger.debug("No .exe asset found in release %s", tag)
    return None


async def download_release(
    url: str,
    dest: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> None:
    """Stream *url* to *dest*, calling *progress_cb(bytes_done, total_bytes)* each chunk.

    Raises httpx.HTTPError or OSError on failure — caller is responsible for
    cleaning up the partial file.
    """
    if not _HTTPX_AVAILABLE:
        raise RuntimeError("httpx is not installed")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
        follow_redirects=True,
        headers={"User-Agent": "EVEAlert/updater"},
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
