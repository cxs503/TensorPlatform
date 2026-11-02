"""Analytic positive control for the structure-domain audit.

This module implements one *physically correct* answer for each of the four audited
endpoints, using closed-form classical solutions only (no regression data, no network).
It has two jobs:

1. Negative-free proof that the criteria are not over-tight.  If a correctly derived
   implementation follows the same response schema and *passes every non-circular
   criterion*, then any failure of the live endpoint is attributable to the endpoint,
   not to the criteria.
2. A byte-identical probe plan: it is evaluated on exactly the same inputs as the live
   endpoint (see `probe.PROBE_PLAN`).

Documented modelling choices (all deliberately conservative, i.e. favourable to the
endpoint under audit -- none of them is needed for the endpoint to pass a decisive
criterion except where noted):

* plate buckling: Timoshenko & Gere Eq. 9-3, simply supported, uniaxial compression.
* panel check: same formula with k = 4 (long panel between frames), critical stress
  capped at yield (standard stiffened-panel check).
* hull girder: idealised single-wall box girder of breadth B, depth D and *effective*
  plating thickness t_eff = 1.6 t (the 0.6 allowance stands for longitudinal
  stiffeners, which is a conventional way to smear stiffener area into the plating);
  wave bending moments from IACS UR S11, moments divided by the box section modulus.
* fatigue: DNV-RP-C203 C curve (which is the curve the endpoint itself names),
  constant-amplitude damage-equivalent range equal to 0.30 x the extreme wave-induced
  stress range, 5e6 cycles per year (mean encounter period 6.3 s), Palmgren-Miner.
"""
from __future__ import annotations

from . import baselines as B

STIFFENER_AREA_FACTOR = 1.6       # t_eff = 1.6 t
DAMAGE_EQUIV_FACTOR = 0.30        # constant-amplitude range = 0.30 * extreme range
CYCLES_PER_YEAR = 5.0e6           # mean encounter period 6.3 s
ALLOW_FACTOR = 0.6                # permissible normal stress = 0.6 sigma_y


def _plate_buckling(p: dict) -> dict:
    th = B.plate_buckling_stress(p["a"], p["b"], p["t"], p.get("E", 210000.0),
                                 p.get("nu", 0.3))
    sy = float(p.get("yield_stress", 235.0))
    return {
        "critical_stress": round(th["sigma_cr"], 4),          # MPa
        "critical_load": round(th["edge_force_kN"], 6),        # kN (edge force)
        "buckling_mode": p.get("load_type", "compression"),
        "half_waves": th["m"],
        "safety_margin": round(sy / th["sigma_cr"] - 1.0, 6),
        "method": "Timoshenko-Gere thin plate, simply supported, k(alpha) minimised",
    }


def _hull_girder(p: dict) -> dict:
    t_eff = float(p["plate_thickness"]) * STIFFENER_AREA_FACTOR
    box = B.hull_girder_stress_box(p["L"], p["B"], p["D"], p["Cb"], t_eff)
    sigma = max(box["sigma_hog_MPa"], box["sigma_sag_MPa"])
    sy = float(p.get("yield_stress", 235.0))
    allow = B.allowable_normal_stress(sy, ALLOW_FACTOR)
    rng = DAMAGE_EQUIV_FACTOR * sigma
    life = B.DNV_C.cycles(rng) / CYCLES_PER_YEAR
    return {"sigma_MPa": sigma, "sigma_allow_MPa": allow,
            "utilization": sigma / allow, "range": rng, "life": life, "W": box["W_m3"]}


def _analyze(p: dict) -> dict:
    g = _hull_girder(p)
    need = g["utilization"] > 1.0
    return {
        "max_stress": round(g["sigma_MPa"], 4),                 # MPa
        "allowable_stress": round(g["sigma_allow_MPa"], 4),     # MPa
        "utilization": round(g["utilization"], 6),
        "fatigue_life": round(g["life"], 3),                    # years
        "status": "需校核" if need else "合格",
    }


def _panel(p: dict) -> dict:
    s = float(p["frame_spacing"])
    th = B.plate_buckling_stress(a=s, b=s, t=float(p["plate_thickness"]))
    sy = float(p.get("yield_stress", 235.0))
    crit = min(th["sigma_cr"], sy)
    g = _hull_girder(p)
    act = g["sigma_MPa"]
    sf = crit / act
    return {"location": "船底板", "critical_stress": round(crit, 4),
            "actual_stress": round(act, 4), "safety_factor": round(sf, 6),
            "status": "需加强" if act > crit else "合格"}


def _fatigue(p: dict) -> dict:
    g = _hull_girder(p)
    life = g["life"]
    design = 25.0
    return {"stress_range": round(g["range"], 4),
            "design_life": design,
            "calculated_life": round(life, 3),
            "s_n_curve": "DNV-C",
            "status": "合格" if life >= design else "不合格"}


_DISPATCH = {
    "/fea/plate-buckling": _plate_buckling,
    "/structure/analyze": _analyze,
    "/structure/plate": _panel,
    "/structure/fatigue": _fatigue,
}


def respond(endpoint: str, payload: dict) -> dict:
    """Return the positive-control response for `endpoint` and `payload`."""
    if endpoint not in _DISPATCH:
        raise KeyError(f"no reference implementation for {endpoint}")
    return _DISPATCH[endpoint](payload)


def reference_hull_girder(payload: dict) -> dict:
    """Expose the internal hull-girder helper for plotting and tests."""
    return _hull_girder(payload)


def reference_plate_buckling_curve(aspect_ratios, b: float = 1.2, t: float = 0.012,
                                   E: float = 210000.0, nu: float = 0.3):
    """sigma_cr(alpha) on a dense grid, for the comparison figure."""
    return [B.plate_buckling_stress(alpha * b, b, t, E, nu)["sigma_cr"]
            for alpha in aspect_ratios]


def reference_fatigue_life(stress_ranges):
    """life(S) for the positive control, for the comparison figure."""
    return [B.DNV_C.cycles(S) / CYCLES_PER_YEAR for S in stress_ranges]


def _unused() -> float:  # pragma: no cover - keeps math import meaningful
    return math.pi