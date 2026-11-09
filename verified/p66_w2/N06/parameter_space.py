"""Parameter-space consistency between the abrasion field and the resistance chain.

Criterion (2) of the gate: the ``(h, V)`` envelope on which the abrasion field
(N03/N05) is produced must be covered by the domain on which the resistance
chain (N04) is *evaluable*, with an overlap of at least 90 %.  Two numbers are
reported because they answer two different questions:

``sample_overlap_fraction``
    fraction of the *produced* abrasion samples whose ``(h, V)`` point lies
    inside the resistance chain's evaluable domain **and** is flagged
    ``in_domain`` by the N04 payload itself.  This is the criterion number: it
    is measured on the delivered payloads, so it cannot drift away from them.

``envelope_overlap_fraction``
    continuous analogue: area of ``Omega_abrasion ∩ Omega_resistance`` divided
    by ``area(Omega_abrasion)``, computed on the midpoint cells of the abrasion
    envelope.  Reported together with the Jaccard index so that an envelope that
    merely *contains* the other is visible as ``J < 1``.

The evaluable domain of the resistance chain is
``h in [h_min, min(0.03 L, 0.10 B)]`` and ``V <= 0.25 sqrt(g L)``
(:class:`tensorphysics.ice.resistance.DomainLimits`) -- empirical laws are never
extrapolated silently, so "the chain can be evaluated" is the honest notion of
"the chain speaks about this ice condition".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .producers import BeltGeometry


@dataclass(frozen=True)
class ParamEnvelope:
    """Axis-aligned ``(h, V)`` rectangle."""

    h_min: float
    h_max: float
    v_min: float
    v_max: float

    def __post_init__(self) -> None:
        if self.h_max <= self.h_min or self.v_max <= self.v_min:
            raise ValueError("degenerate parameter envelope")

    @property
    def area(self) -> float:
        return (self.h_max - self.h_min) * (self.v_max - self.v_min)

    def contains(self, h: float, v: float) -> bool:
        return self.h_min <= h <= self.h_max and self.v_min <= v <= self.v_max

    def intersection(self, other: "ParamEnvelope") -> "ParamEnvelope | None":
        h_min = max(self.h_min, other.h_min)
        h_max = min(self.h_max, other.h_max)
        v_min = max(self.v_min, other.v_min)
        v_max = min(self.v_max, other.v_max)
        if h_max <= h_min or v_max <= v_min:
            return None
        return ParamEnvelope(h_min, h_max, v_min, v_max)

    def as_dict(self) -> dict[str, float]:
        return {"h_min_m": self.h_min, "h_max_m": self.h_max,
                "v_min_ms": self.v_min, "v_max_ms": self.v_max}


def resistance_envelope(ship: Any, limits: Any) -> ParamEnvelope:
    """Evaluable ``(h, V)`` domain of the empirical resistance chain."""
    return ParamEnvelope(
        h_min=float(limits.h_min_m),
        h_max=float(limits.h_limit(ship)),
        v_min=1e-3,
        v_max=float(limits.v_limit(ship)),
    )


def abrasion_envelope(n03: Mapping[str, Any] | Sequence[tuple[float, float]]) -> ParamEnvelope:
    """Envelope spanned by the produced abrasion samples."""
    if isinstance(n03, Mapping):
        samples = [(float(s["h_m"]), float(s["V_ms"])) for s in n03["samples"]]
    else:
        samples = [(float(h), float(v)) for h, v in n03]
    if len(samples) < 2:
        raise ValueError("need at least two samples to form an envelope")
    h = np.array([s[0] for s in samples])
    v = np.array([s[1] for s in samples])
    pad_h = 0.5 * (h.max() - h.min()) / max(len(np.unique(h)) - 1, 1) if h.max() > h.min() else 1e-3
    pad_v = 0.5 * (v.max() - v.min()) / max(len(np.unique(v)) - 1, 1) if v.max() > v.min() else 1e-3
    return ParamEnvelope(h.min() - pad_h, h.max() + pad_h, v.min() - pad_v, v.max() + pad_v)


def sample_overlap(n03: Mapping[str, Any], n04: Mapping[str, Any],
                   envelope: ParamEnvelope) -> dict[str, Any]:
    """Fraction of abrasion samples inside the resistance chain's evaluable domain."""
    resistance_in_domain = {(round(float(s["h_m"]), 9), round(float(s["V_ms"]), 9)): bool(s["in_domain"])
                            for s in n04["samples"]}
    total = 0
    covered = 0
    outside: list[dict[str, float]] = []
    for sample in n03["samples"]:
        h = round(float(sample["h_m"]), 9)
        v = round(float(sample["V_ms"]), 9)
        total += 1
        if envelope.contains(h, v) and resistance_in_domain.get((h, v), False):
            covered += 1
        else:
            outside.append({"h_m": h, "V_ms": v})
    return {
        "n_samples": total,
        "n_covered": covered,
        "fraction": covered / total if total else 0.0,
        "outside": outside,
    }


def envelope_overlap(abrasion: ParamEnvelope, resistance: ParamEnvelope,
                     n_h: int = 64, n_v: int = 64) -> dict[str, float]:
    """Midpoint-cell measure of ``area(A ∩ R) / area(A)`` and the Jaccard index."""
    h_edges = np.linspace(abrasion.h_min, abrasion.h_max, n_h + 1)
    v_edges = np.linspace(abrasion.v_min, abrasion.v_max, n_v + 1)
    h_c = 0.5 * (h_edges[:-1] + h_edges[1:])
    v_c = 0.5 * (v_edges[:-1] + v_edges[1:])
    cell = np.outer(np.diff(h_edges), np.diff(v_edges))
    inside = np.array([[resistance.contains(h, v) for v in v_c] for h in h_c])
    inter = float(np.sum(cell * inside))
    union = abrasion.area + resistance.area - inter
    return {
        "area_abrasion": float(abrasion.area),
        "area_resistance": float(resistance.area),
        "area_intersection": float(inter),
        "fraction": float(inter / abrasion.area) if abrasion.area > 0 else 0.0,
        "jaccard": float(inter / union) if union > 0 else 0.0,
    }


def force_closure(n03: Mapping[str, Any], n04: Mapping[str, Any], *,
                  belt: BeltGeometry) -> dict[str, Any]:
    """Belt-integrated ice contact force vs the resistance chain's ``R_crush``.

    ``F_field = f_c * sum_i A_i p_i (sin theta_i + mu cos theta_i)`` is the force
    the DEM field exerts on the hull; ``R_crush`` is the closed-form crushing
    component of the resistance chain.  **This is a definitional closure, not an
    independent validation**: the N03 reference producer calibrates its pressure
    level at ``h_ref`` from that very closure, so what the residual verifies is
    the ``h``-scaling and the belt geometry/quadrature -- it becomes an
    independent test as soon as the real DEM producer lands.
    """
    f_c = float(n03["contact_area_ratio"])
    mu = float(n03["ice"]["mu_hull_ice"])
    n04_by_key = {(round(float(s["h_m"]), 9), round(float(s["V_ms"]), 9)): s for s in n04["samples"]}
    rows = []
    skipped: list[dict[str, float]] = []
    for sample in n03["samples"]:
        key = (round(float(sample["h_m"]), 9), round(float(sample["V_ms"]), 9))
        areas = np.asarray(sample["area_m2"], dtype=float)
        p = np.asarray(sample["p_ice_Pa"], dtype=float)
        theta = np.asarray(sample["alpha_rad"], dtype=float)
        f_field = f_c * float(np.sum(areas * p * (np.sin(theta) + mu * np.cos(theta))))
        match = n04_by_key.get(key)
        if match is None:      # out-of-domain sample: the chain truncated the key
            skipped.append({"h_m": float(sample["h_m"]), "V_ms": float(sample["V_ms"])})
            continue
        reference = float(match["R_crush_N"])
        rel = abs(f_field - reference) / reference if reference > 0 else float("inf")
        rows.append({
            "h_m": sample["h_m"], "V_ms": sample["V_ms"],
            "F_field_N": f_field, "R_crush_N": reference, "relative_error": rel,
        })
    if not rows:
        raise ValueError("no in-domain sample to close the force balance on")
    return {
        "rows": rows,
        "n_closed": len(rows),
        "n_skipped_out_of_domain": len(skipped),
        "max_relative_error": float(max(row["relative_error"] for row in rows)),
        "mean_relative_error": float(np.mean([row["relative_error"] for row in rows])),
        "grade": "DEFINITIONAL-CLOSURE",
    }


@dataclass
class OverlapReport:
    sample: dict[str, Any]
    envelope: dict[str, float]
    jaccard: float
    threshold: float
    force_closure: dict[str, Any]

    @property
    def fraction(self) -> float:
        return float(self.sample["fraction"])

    @property
    def ok(self) -> bool:
        return self.fraction >= self.threshold and float(self.envelope["fraction"]) >= self.threshold

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_overlap": self.sample,
            "envelope_overlap": self.envelope,
            "jaccard": self.jaccard,
            "threshold": self.threshold,
            "ok": self.ok,
            "force_closure": self.force_closure,
        }


OVERLAP_THRESHOLD = 0.90


def parameter_space_consistency(n03: Mapping[str, Any], n04: Mapping[str, Any], *,
                                ship: Any, limits: Any, belt: BeltGeometry,
                                threshold: float = OVERLAP_THRESHOLD) -> OverlapReport:
    """Criterion (2): overlap of the ``(h, V)`` parameter spaces."""
    abrasion = abrasion_envelope(n03)
    resistance = resistance_envelope(ship, limits)
    inter = abrasion.intersection(resistance)
    envelope = envelope_overlap(abrasion, resistance)
    return OverlapReport(
        sample=sample_overlap(n03, n04, resistance),
        envelope=envelope,
        jaccard=float(envelope["jaccard"]),
        threshold=threshold,
        force_closure=force_closure(n03, n04, belt=belt),
    )