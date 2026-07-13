"""Composite threat score for EVE Alert (#141).

Combines local hostile count, KOS status, zKillboard danger ratio,
D-scan ship class, and adjacent system kills into a 1–10 score that
drives alarm severity and TTS phrasing.

Higher score = more dangerous:
  1–3   CAUTION — worth watching
  4–6   HIGH    — prepare to leave
  7–10  CRITICAL — leave immediately

Cyno detection overrides everything to 10 / CRITICAL.
"""

from dataclasses import dataclass, field


@dataclass
class ThreatAssessment:
    score: int           # 1–10
    label: str           # CAUTION | HIGH | CRITICAL
    reasons: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        reason_str = "; ".join(self.reasons) if self.reasons else "unknown threat"
        return f"[THREAT: {self.score}/10 \u2014 {self.label}] {reason_str}"


def compute_threat_score(
    local_hostile_count: int = 0,
    is_kos: bool = False,
    kos_tier: str = "",
    danger_ratio: float = 0.0,   # 0.0–1.0 from zKB dangerRatio
    dscan_threat_class: str = "", # ShipThreatClass value string
    adjacent_kills: int = 0,
    is_cyno: bool = False,
) -> ThreatAssessment:
    """Compute a composite 1–10 threat score.

    Args:
        local_hostile_count: Number of non-blue pilots in local.
        is_kos:              Whether the pilot is confirmed KOS.
        kos_tier:            Human-readable KOS tier label.
        danger_ratio:        zKB danger ratio (0.0–1.0).
        dscan_threat_class:  ShipThreatClass.value string (e.g. 'tackle').
        adjacent_kills:      Kills in adjacent systems in the last 15 min.
        is_cyno:             Whether a cyno was detected.
    """
    # Cyno overrides everything — capital ship inbound
    if is_cyno:
        return ThreatAssessment(
            10, "CRITICAL",
            ["Cynosural field detected \u2014 capital ship inbound"]
        )

    score = 0
    reasons: list[str] = []

    # --- Local count (max 3) ---
    if local_hostile_count >= 3:
        score += 3
        reasons.append(f"{local_hostile_count} hostiles in local")
    elif local_hostile_count == 2:
        score += 2
        reasons.append("2 hostiles in local")
    elif local_hostile_count == 1:
        score += 1
        reasons.append("hostile in local")

    # --- KOS status (max 2) ---
    if is_kos:
        score += 2
        reasons.append(f"KOS{f' ({kos_tier})' if kos_tier else ''}")

    # --- zKB danger ratio (max 2) ---
    dr_pts = round(danger_ratio * 2)
    if dr_pts > 0:
        score += dr_pts
        reasons.append(f"{int(danger_ratio * 100)}% zKB danger")

    # --- D-scan ship class (max 2) ---
    high_class = {"tackle", "dictor", "force_recon"}
    if dscan_threat_class in high_class:
        score += 2
        reasons.append(f"{dscan_threat_class.replace('_', ' ')} on D-scan")
    elif dscan_threat_class == "covert_ops":
        score += 1
        reasons.append("scanning ship on D-scan")
    elif dscan_threat_class == "combat":
        score += 1
        reasons.append("combat ship on D-scan")

    # --- Adjacent kills (max 1) ---
    if adjacent_kills > 0:
        score += 1
        reasons.append(f"{adjacent_kills} kill(s) in adjacent system")

    score = min(score, 10)
    if score >= 7:
        label = "CRITICAL"
    elif score >= 4:
        label = "HIGH"
    else:
        label = "CAUTION"

    return ThreatAssessment(score=score, label=label, reasons=reasons)
