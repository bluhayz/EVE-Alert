"""Inline link-marker encoding shared between alertmanager.py and
log_pane.py (#210).

alertmanager.py builds log lines as plain strings and deliberately has no
Qt dependency (it stays headless-testable); log_pane.py is the only place
that knows how to turn a log line into rendered markup. This module is the
small, framework-free contract between them: encode() wraps a display
string so LogPane can render it as a clickable link without the raw URL
ever appearing as separate visible text.

Control characters are used as delimiters because they cannot occur in
EVE pilot/system names or in the URLs this app generates, so there is no
need to escape or validate caller input.
"""

import re

MARK_START = "\x02"
MARK_SEP = "\x1f"
MARK_END = "\x03"

MARKER_RE = re.compile(
    "\x02(?P<text>[^\x1f\x02\x03]*)\x1f(?P<url>[^\x02\x03]*)\x03"
)


def make_link(display_text: str, url: str) -> str:
    """Wrap *display_text* so LogPane renders it as a clickable link to
    *url*, instead of showing the raw URL as separate visible text."""
    return f"{MARK_START}{display_text}{MARK_SEP}{url}{MARK_END}"
