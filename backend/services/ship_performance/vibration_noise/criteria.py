"""Habitability / acceptance criteria layer for the vibration-noise domain.

Evidence discipline
-------------------
The *numbers* in a vibration or noise criterion (limit curves, frequency
weighting, zone boundaries) are contractual: they live in licensed standards
(ISO 6954, ISO 20283, ISO 10816/20816, class-society comfort notations) and in
charter-party agreements.  **No such text is present in this repository**, so
hard-coding a table of limit values would be fabrication.  This module therefore
implements only the *machinery* — band energy summation, weighted/overall
aggregation, and verdict evaluation against limits that the caller must supply
**with provenance** — and marks the limit tables themselves ``WITHHELD``.

Anything that fails closed here does so on purpose: a missing limit must raise
rather than silently pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

#: Reason recorded in reports: the rule text is not in the repository.
WITHHELD_REASON = (
    "no licensed standard text (ISO 6954 / ISO 20283 / ISO 10816 / class comfort "
    "notation) is present in this repository; limit tables are therefore not "
    "hard-coded and must be supplied by the caller with provenance"
)


@dataclass(frozen=True)
class LimitProfile:
    """A caller-supplied acceptance profile.

    Attributes
    ----------
    name: human readable profile name (e.g. ``"owner-contract-2026"``).
    quantity: what is being compared (``"velocity_rms_mm_s"`` is the convention
        used by ship habitability criteria).
    limits: mapping ``zone -> value``, lower value = better.
    provenance: free-text citation of *where the numbers come from*.  A profile
        without provenance is rejected.
    """

    name: str
    quantity: str
    limits: Mapping[str, float]
    provenance: str

    def __post_init__(self) -> None:
        if not self.provenance or not self.provenance.strip():
            raise ValueError("a limit profile must carry provenance (fail-closed)")
        if not self.limits:
            raise ValueError("limit profile has no limits")


@dataclass
class Verdict:
    value: float
    quantity: str
    zone: str
    passed: bool
    profile: str
    provenance: str
    notes: List[str] = field(default_factory=list)


def band_energy_sum(values: Sequence[float]) -> float:
    """RMS aggregation over independent (incoherent) 1/3-octave bands.

    ``x_total = sqrt(sum x_i^2)`` — exact for uncorrelated narrowband signals in
    disjoint bands.
    """
    return math.sqrt(sum(v * v for v in values))


def energy_mean(levels_db: Sequence[float]) -> float:
    """Energy average of dB levels (``10 log10 mean(10^(L/10))``)."""
    if not levels_db:
        raise ValueError("empty level list")
    total = sum(10.0 ** (lvl / 10.0) for lvl in levels_db)
    return 10.0 * math.log10(total / len(levels_db))


def evaluate_velocity(value_mm_s: float, profile: LimitProfile) -> Verdict:
    """Compare an overall RMS velocity against a caller-supplied profile.

    Zones are consumed in ascending-limit order: the first zone whose limit is
    ``>= value`` is reported as the achieved zone; if ``value`` exceeds every
    limit the verdict is a failure on the last (worst) zone.
    """
    if value_mm_s < 0.0:
        raise ValueError("value must be non-negative")
    if profile.quantity != "velocity_rms_mm_s":
        raise ValueError(f"profile quantity {profile.quantity!r} is not velocity_rms_mm_s")
    ordered = sorted(profile.limits.items(), key=lambda kv: kv[1])
    notes = []
    for zone, limit in ordered:
        if value_mm_s <= limit:
            return Verdict(value_mm_s, profile.quantity, zone, True, profile.name, profile.provenance, notes)
    worst_zone = ordered[-1][0]
    notes.append(f"value {value_mm_s:.3f} mm/s exceeds worst supplied limit {ordered[-1][1]:.3f} mm/s")
    return Verdict(value_mm_s, profile.quantity, worst_zone, False, profile.name, profile.provenance, notes)


def withheld_limit_profiles() -> Dict[str, str]:
    """Registry of criteria that are deliberately NOT implemented (evidence: WITHHELD)."""
    return {
        "iso_6954_2000_habitability": WITHHELD_REASON,
        "iso_20283_2_underwater_noise_measurement": WITHHELD_REASON,
        "iso_10816_3_machinery_severity_zones": WITHHELD_REASON,
        "class_comfort_notation_vibration": WITHHELD_REASON,
    }


def require_limit_profile(profile: Optional[LimitProfile], name: str) -> LimitProfile:
    """Fail-closed accessor used by callers that must have a licensed profile."""
    if profile is None:
        raise ValueError(f"{name}: no limit profile supplied — criteria table is WITHHELD in this repository")
    return profile


__all__ = [
    "WITHHELD_REASON",
    "LimitProfile",
    "Verdict",
    "band_energy_sum",
    "energy_mean",
    "evaluate_velocity",
    "withheld_limit_profiles",
    "require_limit_profile",
]