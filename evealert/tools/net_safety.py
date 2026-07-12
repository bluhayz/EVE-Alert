"""Network safety helpers for user-supplied URLs (SSRF guard, issue #105)."""

import ipaddress
import urllib.parse


def is_safe_public_url(url: str) -> bool:
    """Return True if *url* is an https URL that does not target a loopback,
    private, link-local, or reserved address.

    Used to vet user-configured endpoints (custom KOS APIs, ntfy servers)
    before EVE Alert makes requests to them, so a shared/imported settings
    file cannot point the app at cloud metadata (169.254.169.254), localhost,
    or internal-range hosts. Checks are done on the literal host without DNS
    resolution to avoid blocking the event loop; this blocks the common,
    high-value SSRF targets while keeping the guard synchronous.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except (ValueError, AttributeError):
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return False
    # If the host is an IP literal, reject non-public ranges.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a public hostname (not an IP literal)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
