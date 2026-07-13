"""Shared HTTP constants for all external API calls in EVE Alert.

Import DEFAULT_HEADERS into every httpx.AsyncClient() call so every
outbound request carries a well-formed, up-to-date User-Agent.
"""

from evealert import __version__

USER_AGENT = (
    f"EVE-Alert/{__version__} "
    "(+https://github.com/bluhayz/EVE-Alert; maintainer: bluhayz)"
)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
}
