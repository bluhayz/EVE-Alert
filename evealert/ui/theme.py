"""Color tokens and QSS stylesheet loader for the EVE Alert Qt UI."""

from evealert.settings.helper import get_resource_path

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
BG = "#0F1216"
SURFACE = "#161B22"
SURFACE_2 = "#1E252E"
BORDER = "#2A333D"
TEXT = "#E6EDF3"
TEXT_MUTED = "#8B98A5"
ACCENT = "#2F81F7"
ACCENT_HOVER = "#1F6FEB"
SUCCESS = "#3FB950"
DANGER = "#F85149"
DANGER_HOVER = "#DA3633"
WARNING = "#D29922"

LOG_COLORS: dict[str, str] = {
    "normal": TEXT,
    "green": SUCCESS,
    "red": DANGER,
    "yellow": WARNING,
    "cyan": "#39C5CF",
    "gray": TEXT_MUTED,
}

# Clickable link color in the log pane (zkillboard/dotlan links, #207) — kept
# distinct from every LOG_COLORS value so a link is recognizable regardless
# of which severity color the surrounding line uses.
LOG_LINK_COLOR = "#58A6FF"


def load_qss() -> str:
    """Load theme.qss from the bundled ui/ resource directory."""
    path = get_resource_path("ui/theme.qss")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""
