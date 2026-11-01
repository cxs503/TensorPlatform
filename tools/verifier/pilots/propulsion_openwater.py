#!/usr/bin/env python3
"""
propulsion_openwater — analytic reference invariants for open-water propeller performance.

Purpose (verifier seat, domain "propulsion")
-------------------------------------------
The platform exposes an open-water / Wageningen-B endpoint whose evidence grade was
declared REAL.  Before spending effort on absolute-accuracy comparison against
experimental series data (which needs a B-series regression table or test reports),
the cheapest *falsifying* evidence is a set of **analytic invariants and series
physics constraints** that any valid KT/KQ model must satisfy exactly:

  [1] Open-water efficiency definition (ITTC recommended procedure 7.5-02-03-02.1):
          eta0 = J * KT / (2*pi*KQ)
      This is a definition, not a fit — deviations are implementation errors.

  [2] Actuator-disk (Froude/Rankine momentum theory) ideal-efficiency bound
      (Breslin & Andersen, *Principles of Naval Architecture* Vol. II, ch. V):
          C_T    = T / (0.5 * rho * A0 * Va^2) = 8*KT / (pi*J^2)
          eta_id = 2 / (1 + sqrt(1 + C_T))
      A real propeller can never exceed the ideal jet efficiency at the same
      thrust loading (profile/viscous losses only reduce it), so
      eta0 <= eta_id is a hard physical constraint.

  [3] Wageningen B-series structure (Oosterveld & van Oossanen, 1975): KT and KQ
      are triple polynomials in (P/D, Ae/A0, J) with blade-count-specific term
      sets.  Consequences used here:
        - for fixed (J, Z, Ae/A0), eta0 MUST vary with P/D (no cancellation exists);
        - Z must enter the result (distinct KT for Z = 3..7, all modelled);
        - KT is strictly decreasing in J (no plateaus; becomes negative past the
          zero-thrust advance ratio);
        - KQ/KT is not a constant;
        - eta0 -> 0 as J -> 0 (bollard condition: P_E = T * Va = 0).

Every check returns a measured number plus the analytic reference, so the caller can
quote a quantitative deviation instead of a bare pass/fail.

The module is pure Python and dependency-free so that it can be unit-tested offline
against synthetic candidates (see tests/test_propulsion_pilot.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Callable, Iterable

# Sampler contract: sampler(J, P_D, Z, Ae_A0) -> {"KT": float, "KQ": float, "eta0": float}
Sampler = Callable[[float, float, int, float], dict[str, float]]

# Default sampling grids (kept inside the published B-series validity box:
#   Z = 2..7, Ae/A0 = 0.30..1.05, P/D = 0.5..1.4)
J_GRID: tuple[float, ...] = (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.4)
P_GRID: tuple[float, ...] = (0.6, 0.8, 1.0, 1.2, 1.4)
Z_GRID: tuple[int, ...] = (3, 4, 5, 6, 7)
AE_GRID: tuple[float, ...] = (0.30, 0.55, 0.65, 0.85, 1.05)
REF_Z = 4
REF_AE = 0.65


# ---------------------------------------------------------------------------
# Analytic references
# ---------------------------------------------------------------------------
def thrust_loading(KT: float, J: float) -> float:
    """Actuator-disk thrust loading coefficient C_T = 8*KT/(pi*J^2)."""
    if J <= 0.0:
        raise ValueError("C_T is singular at J=0; use the bollard check instead")
    return 8.0 * KT / (math.pi * J * J)


def ideal_efficiency(KT: float, J: float) -> float:
    """Ideal (jet) efficiency of an actuator disk at the given thrust loading."""
    ct = thrust_loading(KT, J)
    return 2.0 / (1.0 + math.sqrt(1.0 + ct))


def eta0_from_coefficients(J: float, KT: float, KQ: float) -> float:
    """Open-water efficiency from its definition; returns 0.0 when ill-posed."""
    if J <= 0.0 or KQ <= 0.0:
        return 0.0
    return J * KT / (2.0 * math.pi * KQ)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class InvariantResult:
    name: str
    passed: bool
    measured: float
    reference: float
    tolerance: float
    unit: str
    error_pct: float | None
    detail: str
    provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rel_error(measured: float, reference: float) -> float | None:
    if reference == 0.0:
        return None if measured == 0.0 else float("inf")
    return abs(measured - reference) / abs(reference) * 100.0


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
def inv_eta_definition(sampler: Sampler) -> InvariantResult:
    """eta0 must equal J*KT/(2*pi*KQ) (definitional identity)."""
    devs = []
    for J in J_GRID:
        if J <= 0.0:
            continue
        for p in P_GRID:
            r = sampler(J, p, REF_Z, REF_AE)
            if r["KQ"] <= 0.0:
                continue
            ref = eta0_from_coefficients(J, r["KT"], r["KQ"])
            if abs(ref) < 1e-6:      # near zero-thrust: identity ill-conditioned
                continue
            devs.append(abs(r["eta0"] - ref) / abs(ref))
    worst = max(devs)
    return InvariantResult(
        name="eta0_definition",
        passed=worst <= 0.03,
        measured=worst * 100.0,
        reference=0.0,
        tolerance=3.0,
        unit="% relative deviation",
        error_pct=worst * 100.0,
        detail=(
            "max |eta0 - J*KT/(2*pi*KQ)| / (J*KT/(2*pi*KQ)) over the (J, P/D) grid; "
            "a correct implementation satisfies this exactly (round-off only)"
        ),
        provenance={
            "source": "ITTC 7.5-02-03-02.1 open-water test procedure (eta0 definition)",
            "type": "definition",
        },
    )


def inv_actuator_disk_bound(sampler: Sampler) -> InvariantResult:
    """eta0 must not exceed the momentum-theory ideal efficiency."""
    worst = -1e9
    worst_pt = None
    for J in J_GRID:
        if J <= 0.0:
            continue
        for p in P_GRID:
            r = sampler(J, p, REF_Z, REF_AE)
            if r["KT"] <= 0.0:
                continue
            eta_id = ideal_efficiency(r["KT"], J)
            exc = r["eta0"] / eta_id - 1.0
            if exc > worst:
                worst, worst_pt = exc, (J, p, r["KT"], r["eta0"], eta_id)
    J, p, kt, eta, eta_id = worst_pt
    return InvariantResult(
        name="actuator_disk_bound",
        passed=worst <= 0.01,
        measured=worst * 100.0,
        reference=0.0,
        tolerance=1.0,
        unit="% excess over eta_ideal",
        error_pct=worst * 100.0,
        detail=(
            f"worst point J={J:.2f}, P/D={p:.2f}: KT={kt:.4f}, eta0={eta:.4f}, "
            f"eta_ideal=2/(1+sqrt(1+C_T))={eta_id:.4f}"
        ),
        provenance={
            "source": "Froude/Rankine actuator-disk momentum theory; "
                      "Breslin & Andersen, Principles of Naval Architecture Vol. II ch. V",
            "type": "physical bound",
        },
    )


def inv_pitch_sensitivity(sampler: Sampler) -> InvariantResult:
    """For fixed (J, Z, Ae/A0), eta0 must vary with P/D (B-series structure)."""
    J = 0.5
    etas = [sampler(J, p, REF_Z, REF_AE)["eta0"] for p in P_GRID]
    spread = max(etas) - min(etas)
    return InvariantResult(
        name="pitch_sensitivity",
        passed=spread >= 0.02,
        measured=spread,
        reference=0.02,
        tolerance=0.02,
        unit="absolute eta0 spread over P/D=0.6..1.4",
        error_pct=(1.0 - spread / 0.02) * 100.0 if spread < 0.02 else 0.0,
        detail=(
            f"J={J}, Z={REF_Z}, Ae/A0={REF_AE}: eta0 = "
            + ", ".join(f"{p:.1f}->{e:.4f}" for p, e in zip(P_GRID, etas))
        ),
        provenance={
            "source": "Oosterveld & van Oossanen (1975) B-series polynomial structure",
            "type": "series physics",
        },
    )


def inv_bollard_efficiency(sampler: Sampler) -> InvariantResult:
    """At J=0 (bollard) the open-water efficiency is 0 because P_E = T*Va = 0."""
    eta = sampler(0.0, 1.0, REF_Z, REF_AE)["eta0"]
    return InvariantResult(
        name="bollard_efficiency",
        passed=eta <= 0.01,
        measured=eta,
        reference=0.0,
        tolerance=0.01,
        unit="eta0 at J=0",
        error_pct=None if eta <= 0.01 else float("inf"),
        detail=f"sample at J=0, P/D=1.0: eta0={eta:.4f} (must be 0)",
        provenance={
            "source": "P_E = T*Va = 0 at zero advance speed (ITTC open-water definition)",
            "type": "analytic limit",
        },
    )


def inv_kt_monotone_no_plateau(sampler: Sampler) -> InvariantResult:
    """KT must be strictly decreasing in J; a flat KT indicates clamping."""
    plateau_pairs = 0
    transitions = 0
    worst_pair = None
    for p in P_GRID:
        kts = [sampler(J, p, REF_Z, REF_AE)["KT"] for J in J_GRID]
        for a, b in zip(kts[:-1], kts[1:]):
            transitions += 1
            if abs(b - a) <= 1e-9:
                plateau_pairs += 1
                if worst_pair is None:
                    worst_pair = (p, a, b)
    return InvariantResult(
        name="kt_monotone_no_plateau",
        passed=plateau_pairs == 0,
        measured=float(plateau_pairs),
        reference=0.0,
        tolerance=0.0,
        unit="plateau pairs (equal consecutive KT)",
        error_pct=None if plateau_pairs == 0 else 100.0,
        detail=(
            f"{plateau_pairs}/{transitions} consecutive KT pairs identical"
            + (f"; first plateau: P/D={worst_pair[0]:.2f} KT {worst_pair[1]:.4f}->{worst_pair[2]:.4f}"
               if worst_pair else "")
        ),
        provenance={
            "source": "B-series KT(J) strictly decreasing (Oosterveld & van Oossanen 1975)",
            "type": "series physics",
        },
    )


def inv_kq_kt_ratio_varies(sampler: Sampler) -> InvariantResult:
    """KQ/KT must not be a constant across the open-water diagram."""
    ratios = []
    for J in J_GRID:
        if J <= 0.0:
            continue
        for p in P_GRID:
            r = sampler(J, p, REF_Z, REF_AE)
            if r["KT"] > 0.0:
                ratios.append(r["KQ"] / r["KT"])
    lo, hi = min(ratios), max(ratios)
    rel_spread = (hi - lo) / max(abs(lo), 1e-12)
    return InvariantResult(
        name="kq_kt_ratio_varies",
        passed=rel_spread >= 0.005,
        measured=rel_spread,
        reference=0.005,
        tolerance=0.005,
        unit="relative spread of KQ/KT",
        error_pct=None if rel_spread >= 0.005 else 100.0,
        detail=f"KQ/KT in [{lo:.6f}, {hi:.6f}] over the (J, P/D) grid",
        provenance={
            "source": "B-series: KQ and KT are independent polynomials, ratio varies with J and P/D",
            "type": "series physics",
        },
    )


def inv_blade_count_resolution(sampler: Sampler) -> InvariantResult:
    """All modelled blade counts Z=3..7 must produce distinct KT."""
    kts = {z: sampler(0.5, 1.0, z, REF_AE)["KT"] for z in Z_GRID}
    zs = list(Z_GRID)
    collisions = []
    min_rel = 1e9
    for i, z1 in enumerate(zs):
        for z2 in zs[i + 1:]:
            rel = abs(kts[z1] - kts[z2]) / max(kts[z1], 1e-12)
            min_rel = min(min_rel, rel)
            if rel < 0.001:
                collisions.append((z1, z2))
    return InvariantResult(
        name="blade_count_resolution",
        passed=not collisions,
        measured=len(collisions),
        reference=0.0,
        tolerance=0.0,
        unit="colliding (Z1,Z2) pairs",
        error_pct=None if not collisions else 100.0,
        detail=(
            "KT at J=0.5, P/D=1.0: "
            + ", ".join(f"Z={z}:{kts[z]:.4f}" for z in zs)
            + (f"; collisions {collisions}" if collisions else "")
        ),
        provenance={
            "source": "Oosterveld & van Oossanen (1975): separate coefficient sets for Z=2..7",
            "type": "series physics",
        },
    )


INVARIANTS: tuple[Callable[[Sampler], InvariantResult], ...] = (
    inv_eta_definition,
    inv_actuator_disk_bound,
    inv_pitch_sensitivity,
    inv_bollard_efficiency,
    inv_kt_monotone_no_plateau,
    inv_kq_kt_ratio_varies,
    inv_blade_count_resolution,
)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def sample_grid(sampler: Sampler) -> list[dict[str, float]]:
    """Record the full KT/KQ/eta0 map (used for tables and figures)."""
    rows = []
    for J in J_GRID:
        for p in P_GRID:
            r = sampler(J, p, REF_Z, REF_AE)
            row = {
                "J": J, "P_over_D": p, "Z": REF_Z, "Ae_over_A0": REF_AE,
                "KT": r["KT"], "KQ": r["KQ"], "eta0": r["eta0"],
            }
            if J > 0 and r["KT"] > 0:
                row["C_T"] = thrust_loading(r["KT"], J)
                row["eta_ideal"] = ideal_efficiency(r["KT"], J)
                row["eta_excess_pct"] = (r["eta0"] / row["eta_ideal"] - 1.0) * 100.0
            rows.append(row)
    return rows


def evaluate_candidate(sampler: Sampler, candidate_id: str = "platform-api") -> dict[str, Any]:
    """Run every invariant against the sampler and build the verdict report."""
    results = [fn(sampler) for fn in INVARIANTS]
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    return {
        "candidate_id": candidate_id,
        "domain": "propulsion",
        "grid": {
            "J": list(J_GRID), "P_over_D": list(P_GRID),
            "Z": list(Z_GRID), "Ae_over_A0": list(AE_GRID),
            "reference_point": {"Z": REF_Z, "Ae_over_A0": REF_AE},
        },
        "invariants": [r.to_dict() for r in results],
        "passed": passed,
        "total": total,
        "verdict": "pass" if passed == total else "fail",
        "samples": sample_grid(sampler),
    }