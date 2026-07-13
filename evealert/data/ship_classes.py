"""Ship threat classification data for EVE Alert.

Maps EVE ship names (and type-column substrings) to high-level threat
classes that drive the CYNO alarm, composite threat score, and the
alarm headline labels.  Add new ships here — no code changes needed.
"""

from enum import Enum


class ShipThreatClass(str, Enum):
    TACKLE      = "tackle"       # Interceptors — land, point, hold — leave NOW
    DICTOR      = "dictor"       # Dictors/HICs — bubble incoming
    FORCE_RECON = "force_recon"  # Falcon/Rapier/Arazu/Pilgrim — cloaked until on grid
    COVERT_OPS  = "covert_ops"   # Scanning ships / probes — 60-120 s before landing
    CYNO        = "cyno"         # Cynosural field or cyno ship — capital drop imminent
    COMBAT      = "combat"       # Battleships, T3Cs, HACs, etc.
    INDUSTRIAL  = "industrial"   # Non-threat
    UNKNOWN     = "unknown"

    @property
    def urgency(self) -> int:
        """Higher = more urgent (used for sorting/score weighting)."""
        return {
            ShipThreatClass.CYNO:        10,
            ShipThreatClass.FORCE_RECON: 9,
            ShipThreatClass.DICTOR:      8,
            ShipThreatClass.TACKLE:      7,
            ShipThreatClass.COVERT_OPS:  5,
            ShipThreatClass.COMBAT:      3,
            ShipThreatClass.INDUSTRIAL:  0,
            ShipThreatClass.UNKNOWN:     0,
        }[self]


# ---------------------------------------------------------------------------
# Classification map — longest / most-specific entries first so the
# first-match loop picks the right class when names overlap.
# Keys are case-insensitive substrings matched against the D-scan Type column.
# ---------------------------------------------------------------------------

SHIP_CLASS_MAP: list[tuple[str, ShipThreatClass]] = [
    # --- Cyno objects / ships ---
    ("covert cynosural field",      ShipThreatClass.CYNO),
    ("cynosural field",             ShipThreatClass.CYNO),
    ("covert cyno",                 ShipThreatClass.CYNO),

    # --- Force Recon (cloaked, appear in local only when on grid) ---
    ("falcon",                      ShipThreatClass.FORCE_RECON),
    ("rapier",                      ShipThreatClass.FORCE_RECON),
    ("arazu",                       ShipThreatClass.FORCE_RECON),
    ("pilgrim",                     ShipThreatClass.FORCE_RECON),

    # --- Interdiction (Dictors / HICs) ---
    ("sabre",                       ShipThreatClass.DICTOR),
    ("flycatcher",                  ShipThreatClass.DICTOR),
    ("heretic",                     ShipThreatClass.DICTOR),
    ("eris",                        ShipThreatClass.DICTOR),
    ("heavy interdictor",           ShipThreatClass.DICTOR),
    ("interdictor",                 ShipThreatClass.DICTOR),
    ("phobos",                      ShipThreatClass.DICTOR),
    ("broadsword",                  ShipThreatClass.DICTOR),
    ("devoter",                     ShipThreatClass.DICTOR),
    ("onyx",                        ShipThreatClass.DICTOR),

    # --- Tackle (Interceptors + specialist tacklers) ---
    ("stiletto",                    ShipThreatClass.TACKLE),
    ("malediction",                 ShipThreatClass.TACKLE),
    ("ares",                        ShipThreatClass.TACKLE),
    ("crow",                        ShipThreatClass.TACKLE),
    ("raptor",                      ShipThreatClass.TACKLE),
    ("taranis",                     ShipThreatClass.TACKLE),
    ("crusader",                    ShipThreatClass.TACKLE),
    ("claw",                        ShipThreatClass.TACKLE),
    ("hyena",                       ShipThreatClass.TACKLE),
    ("interceptor",                 ShipThreatClass.TACKLE),

    # --- Covert Ops / scanning ---
    ("sisters core scanner probe",  ShipThreatClass.COVERT_OPS),
    ("sisters combat scanner probe",ShipThreatClass.COVERT_OPS),
    ("combat scanner probe",        ShipThreatClass.COVERT_OPS),
    ("core scanner probe",          ShipThreatClass.COVERT_OPS),
    ("helios",                      ShipThreatClass.COVERT_OPS),
    ("anathema",                    ShipThreatClass.COVERT_OPS),
    ("cheetah",                     ShipThreatClass.COVERT_OPS),
    ("buzzard",                     ShipThreatClass.COVERT_OPS),
    ("astero",                      ShipThreatClass.COVERT_OPS),
    ("covert ops",                  ShipThreatClass.COVERT_OPS),
    ("stealth bomber",              ShipThreatClass.COVERT_OPS),

    # --- General combat ---
    ("supercarrier",                ShipThreatClass.COMBAT),
    ("super carrier",               ShipThreatClass.COMBAT),
    ("carrier",                     ShipThreatClass.COMBAT),
    ("dreadnought",                 ShipThreatClass.COMBAT),
    ("force auxiliary",             ShipThreatClass.COMBAT),
    ("titan",                       ShipThreatClass.COMBAT),
    ("black ops",                   ShipThreatClass.COMBAT),
    ("strategic cruiser",           ShipThreatClass.COMBAT),
    ("heavy assault cruiser",       ShipThreatClass.COMBAT),
    ("assault frigate",             ShipThreatClass.COMBAT),
    ("command ship",                ShipThreatClass.COMBAT),
    ("recon ship",                  ShipThreatClass.COMBAT),
    ("combat recon",                ShipThreatClass.COMBAT),
    ("battleship",                  ShipThreatClass.COMBAT),
    ("battlecruiser",               ShipThreatClass.COMBAT),
    ("cruiser",                     ShipThreatClass.COMBAT),
    ("destroyer",                   ShipThreatClass.COMBAT),
    ("frigate",                     ShipThreatClass.COMBAT),

    # --- Industrial / non-threat ---
    ("jump freighter",              ShipThreatClass.INDUSTRIAL),
    ("freighter",                   ShipThreatClass.INDUSTRIAL),
    ("industrial",                  ShipThreatClass.INDUSTRIAL),
    ("mining barge",                ShipThreatClass.INDUSTRIAL),
    ("exhumer",                     ShipThreatClass.INDUSTRIAL),
    ("venture",                     ShipThreatClass.INDUSTRIAL),
    ("shuttle",                     ShipThreatClass.INDUSTRIAL),
    ("capsule",                     ShipThreatClass.INDUSTRIAL),
    ("pod",                         ShipThreatClass.INDUSTRIAL),
    ("rookie ship",                 ShipThreatClass.INDUSTRIAL),
]


def classify_ship(name: str) -> ShipThreatClass:
    """Return the ShipThreatClass for *name* (case-insensitive substring match).

    Uses the ordered SHIP_CLASS_MAP so more-specific entries (e.g.
    'covert cynosural field') take priority over shorter ones ('cyno').
    Returns UNKNOWN for unrecognised entries.
    """
    lower = name.lower()
    for key, cls in SHIP_CLASS_MAP:
        if key in lower:
            return cls
    return ShipThreatClass.UNKNOWN
