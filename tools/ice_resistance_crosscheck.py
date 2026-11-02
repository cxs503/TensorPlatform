"""Level-ice resistance cross-implementation verification harness (m1682).

Three independent level-ice resistance implementations live in this worktree and
all three are reachable from production code paths:

``polar``     services/polar/resistance.py::ice_components          (TP-POLAR-1)
``lindqvist`` services/ice_resistance_uq.py::_lindqvist_raw          (Lindqvist 1989)
``twin``      services/digital_twin/models/resistance.py::lindqvist_ice_resistance

Nothing in the tree compared them against each other or against the full-scale
trial means that ``ice_resistance_uq.SHIP_TRIAL_DATA`` already carries.  This
harness closes that verification gap with machine-checkable probes:

P1  absolute level at the three trial conditions (literature full-scale means)
P2  component split at the trial conditions (crushing / breaking / submersion)
P3  ice-thickness scaling exponent  d ln R / d ln h  vs the analytical window
P4  ship-length (L) sensitivity artefact  -- ice resistance must not be carried
    by a B/L surrogate for the ice-sheet characteristic length
P5  small-thickness limit and monotonicity invariants
P6  speed dependence

Physics relied on by the acceptance criteria (all classical, no fitting):

* crushing of the ice edge over a process zone of order h*t  scales as h^2;
* a semi-infinite beam on an elastic foundation of characteristic length
  ``l_c = (E h^3 / (12 rho_w g (1 - nu^2)))^(1/4)`` fails at the yield moment
  ``M_y = sigma_f h^2 / 6`` per unit width; the edge force per unit width that
  reaches ``M_y`` is ``2 M_y / l_c`` -> the breaking term scales as
  ``h^2 / l_c`` = ``h^{1.25}`` (``l_c ~ h^{0.75}``);
* buoyancy-driven submersion of the broken rubble scales linearly with h.

Therefore the *total* level-ice resistance of a ship must obey
``1.0 <= d ln R / d ln h <= 2.0`` with the local exponent decreasing toward 1 as
h grows (submersion dominates for large hulls), and it must be odd (0.75-1.25
for the breaking component).  Any implementation that returns ~2.0 for the total
at h ~ 1 m has lost (or mis-scaled) the submersion term; any implementation whose
ice force moves with the ship *length* at constant beam/draft has replaced the
ice-sheet characteristic length by a hull-length surrogate.

Run:  python3 tools/ice_resistance_crosscheck.py [--json OUT] [--figdir DIR]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

G = 9.80665
RHO_W = 1025.0
RHO_ICE = 917.0
NU_ICE = 0.33
E_ICE = 2.5e9


# ---------------------------------------------------------------------------
# analytical anchors (encoded independently of the production modules)
# ---------------------------------------------------------------------------
def characteristic_length(h: float, E: float = E_ICE, nu: float = NU_ICE) -> float:
    """l_c = (E h^3 / (12 rho_w g (1 - nu^2)))^(1/4)  [m]."""
    return (E * h ** 3 / (12.0 * RHO_W * G * (1.0 - nu ** 2))) ** 0.25


def breaking_exponent() -> float:
    """d ln(l_c) / d ln h = 0.75 -> breaking term exponent = 2 - 0.75."""
    h1, h2 = 0.4, 0.8
    return 2.0 - math.log(characteristic_length(h2) / characteristic_length(h1)) / math.log(h2 / h1)


# ---------------------------------------------------------------------------
# thin adapters around the three implementations
# ---------------------------------------------------------------------------
@dataclass
class Ship:
    key: str
    L: float
    B: float
    T: float
    Cb: float
    bow_deg: float
    mu: float


def polar_components(ship: Ship, v: float, h: float, sigma_mpa: float) -> Dict[str, float]:
    from services.polar.resistance import ice_components
    from services.polar.ships import ShipGeometry

    geom = ShipGeometry(
        key=ship.key,
        name=ship.key,
        category="bulk_carrier",
        length_m=ship.L,
        beam_m=ship.B,
        draft_m=ship.T,
        cb=ship.Cb,
        dwt_t=0.8 * ship.Cb * ship.L * ship.B * ship.T * RHO_W / 1000.0,
        design_speed_kn=15.0,
        brake_power_kw=15000.0,
        sfoc_g_kwh=175.0,
        eta_propulsive=0.68,
        bow_half_angle_deg=ship.bow_deg,
        mu_hull_ice=ship.mu,
    )
    out = ice_components(geom, v, h, sigma_mpa)
    return {
        "crushing": out["crushing"],
        "breaking": out["breaking"],
        "submersion": out["submersion"],
        "total": out["total"],
    }


def lindqvist_raw_components(ship: Ship, v: float, h: float, sigma_mpa: float) -> Dict[str, float]:
    """Component split of services/ice_resistance_uq._lindqvist_raw.

    The production function returns the total only; the split below reproduces
    its arithmetic in the same order so the sum is bit-comparable (asserted in
    the regression test).
    """
    sigma_c = sigma_mpa * 1e6
    phi = math.radians(ship.bow_deg)
    mu = ship.mu
    if phi > 0.01:
        f_cr = sigma_c * h * h * math.tan(phi) * (1.0 + mu / math.tan(phi))
    else:
        f_cr = sigma_c * h * h * 0.5
    sigma_f = sigma_c / 4.0
    D = E_ICE * h ** 3 / (12.0 * (1.0 - NU_ICE ** 2))
    l_c = (D / (RHO_W * G)) ** 0.25
    f_br = sigma_f * h ** 2 / (6.0 * l_c) * (1.0 + mu * math.pi * l_c / (4.0 * h)) * ship.B
    if phi > 0.01:
        f_sub = (RHO_W - RHO_ICE) * G * h * ship.B * (1.0 + mu * math.cos(phi) / math.sin(phi)) * 0.3 * ship.L
    else:
        f_sub = (RHO_W - RHO_ICE) * G * h * ship.B * 0.3 * ship.L
    f_spd = 0.5 * RHO_W * ship.B * h * v ** 2 * 0.1
    return {
        "crushing": f_cr,
        "breaking": f_br,
        "submersion": f_sub,
        "speed": f_spd,
        "total": f_cr + f_br + f_sub + f_spd,
    }


def lindqvist_raw(ship: Ship, v: float, h: float, sigma_mpa: float) -> float:
    from services.ice_resistance_uq import ShipIceParams, _lindqvist_raw

    params = ShipIceParams(
        name=ship.key,
        length_m=ship.L,
        beam_m=ship.B,
        draft_m=ship.T,
        cb=ship.Cb,
        bow_angle_deg=ship.bow_deg,
        displacement_t=0.8 * ship.Cb * ship.L * ship.B * ship.T * RHO_W / 1000.0,
        calibration_factor=1.0,
    )
    return _lindqvist_raw(params, h, sigma_mpa, ship.mu, v) * 1000.0  # kN -> N


def twin_components(ship: Ship, v: float, h: float, sigma_mpa: float) -> Dict[str, float]:
    from services.digital_twin.models.resistance import lindqvist_ice_resistance

    sigma_c = sigma_mpa * 1e6
    out = lindqvist_ice_resistance(
        V=v, L=ship.L, B=ship.B, T=ship.T, Cb=ship.Cb,
        ice_concentration=1.0, ice_thickness=h,
        sigma_c=sigma_c, sigma_b=0.4 * sigma_c,
    )
    return {
        "crushing": out["Rc_N"],
        "breaking": out["Rb_N"],
        "submersion": out["Rs_N"],
        "total": out["R_ice_N"],
    }


IMPLS = {"polar": polar_components, "lindqvist": lindqvist_raw_components, "twin": twin_components}


# ---------------------------------------------------------------------------
# trial reference data (shipped in-repo, published full-scale means)
# ---------------------------------------------------------------------------
def trial_cases() -> List[Dict]:
    from services.ice_resistance_uq import SHIP_TRIAL_DATA

    cases = []
    for key, entry in SHIP_TRIAL_DATA.items():
        p = entry["params"]
        cases.append(
            {
                "key": key,
                "mean_resistance_kN": entry["mean_resistance_kN"],
                "h": entry["nominal_ice_thickness_m"],
                "sigma_mpa": entry["nominal_ice_strength_MPa"],
                "v": entry["nominal_speed_ms"],
                "mu": entry["nominal_friction_coeff"],
                "ship": Ship(key=key, L=p.length_m, B=p.beam_m, T=p.draft_m, Cb=p.cb,
                             bow_deg=p.bow_angle_deg, mu=entry["nominal_friction_coeff"]),
                "calibration_factor": p.calibration_factor,
            }
        )
    return cases


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------
def probe_p1_absolute(cases: Sequence[Dict]) -> List[Dict]:
    rows = []
    for c in cases:
        row = {"ship": c["key"], "trial_kN": c["mean_resistance_kN"],
               "h_m": c["h"], "sigma_MPa": c["sigma_mpa"], "v_ms": c["v"]}
        for name, fn in IMPLS.items():
            r_n = fn(c["ship"], c["v"], c["h"], c["sigma_mpa"])["total"]
            row[name + "_kN"] = r_n / 1000.0
            row[name + "_err_pct"] = 100.0 * (r_n / 1000.0 - c["mean_resistance_kN"]) / c["mean_resistance_kN"]
        rows.append(row)
    return rows


def probe_p2_components(cases: Sequence[Dict]) -> List[Dict]:
    rows = []
    for c in cases:
        for name, fn in IMPLS.items():
            comp = fn(c["ship"], c["v"], c["h"], c["sigma_mpa"])
            rows.append({
                "ship": c["key"], "impl": name,
                **{k: v / 1000.0 for k, v in comp.items()},
                "submersion_share": (comp["submersion"] / comp["total"]) if comp["total"] else 0.0,
            })
    return rows


def local_exponent(fn, ship: Ship, v: float, h0: float, sigma_mpa: float, rel: float = 1e-3) -> float:
    r1 = fn(ship, v, h0 * (1.0 - rel), sigma_mpa)["total"]
    r2 = fn(ship, v, h0 * (1.0 + rel), sigma_mpa)["total"]
    return math.log(r2 / r1) / math.log((1.0 + rel) / (1.0 - rel))


def probe_p3_exponents(cases: Sequence[Dict]) -> Dict:
    out = {"analytic_breaking_exponent": breaking_exponent(),
           "analytic_window_total": [1.0, 2.0],
           "by_impl": {}}
    for name, fn in IMPLS.items():
        per_ship = []
        for c in cases:
            for h0 in (0.3, 0.6, 1.0, 1.5):
                per_ship.append({
                    "ship": c["key"], "h_m": h0,
                    "n_total": local_exponent(fn, c["ship"], c["v"], h0, c["sigma_mpa"]),
                    "n_crushing": local_exponent(lambda s, vv, hh, ss: {"total": fn(s, vv, hh, ss)["crushing"]},
                                                 c["ship"], c["v"], h0, c["sigma_mpa"]),
                    "n_breaking": local_exponent(lambda s, vv, hh, ss: {"total": fn(s, vv, hh, ss)["breaking"]},
                                                 c["ship"], c["v"], h0, c["sigma_mpa"]),
                    "n_submersion": local_exponent(lambda s, vv, hh, ss: {"total": fn(s, vv, hh, ss)["submersion"]},
                                                   c["ship"], c["v"], h0, c["sigma_mpa"]),
                })
        out["by_impl"][name] = per_ship
    return out


def probe_p4_length_sensitivity(cases: Sequence[Dict], rel: float = 0.10) -> List[Dict]:
    rows = []
    for c in cases:
        base = {name: fn(c["ship"], c["v"], c["h"], c["sigma_mpa"])["total"] for name, fn in IMPLS.items()}
        stretched = Ship(**{**c["ship"].__dict__, "L": c["ship"].L * (1.0 + rel)})
        shrunk = Ship(**{**c["ship"].__dict__, "L": c["ship"].L * (1.0 - rel)})
        row = {"ship": c["key"], "dL_pct": 100.0 * rel}
        for name, fn in IMPLS.items():
            r_hi = fn(stretched, c["v"], c["h"], c["sigma_mpa"])["total"]
            r_lo = fn(shrunk, c["v"], c["h"], c["sigma_mpa"])["total"]
            row[name + "_dL_hi_pct"] = 100.0 * (r_hi - base[name]) / base[name]
            row[name + "_dL_lo_pct"] = 100.0 * (r_lo - base[name]) / base[name]
            row[name + "_spread_pct"] = 100.0 * (max(r_hi, r_lo) - min(r_hi, r_lo)) / base[name]
        rows.append(row)
    return rows


def probe_p5_invariants(cases: Sequence[Dict]) -> List[Dict]:
    """Continuity / monotonicity / non-negativity invariants.

    A level-ice resistance model is linear-ish in h (1 <= n <= 2), so at
    h = 1 mm it must return a *small finite* value: R(1 mm) / R(h_nom) <= 0.2.
    Divergence of the speed-correction factor 1 + C v / sqrt(g h) as h -> 0 is
    recorded separately as an extrapolation diagnostic (a large value is a
    warning about thin-ice use, not a failure of the model at design h).
    """
    rows = []
    for c in cases:
        r_nom = {n: fn(c["ship"], c["v"], c["h"], c["sigma_mpa"])["total"] for n, fn in IMPLS.items()}
        for name, fn in IMPLS.items():
            small = fn(c["ship"], c["v"], 1e-3, c["sigma_mpa"])["total"]
            mono_h = all(
                fn(c["ship"], c["v"], h2, c["sigma_mpa"])["total"] >= fn(c["ship"], c["v"], h1, c["sigma_mpa"])["total"]
                for h1, h2 in zip([0.2, 0.4, 0.6, 0.8, 1.0], [0.4, 0.6, 0.8, 1.0, 1.2])
            )
            mono_s = all(
                fn(c["ship"], c["v"], c["h"], s2)["total"] >= fn(c["ship"], c["v"], c["h"], s1)["total"]
                for s1, s2 in zip([0.5, 1.0, 1.5], [1.0, 1.5, 2.0])
            )
            rows.append({
                "ship": c["key"], "impl": name,
                "R_at_h_1mm_kN": small / 1000.0,
                "small_h_ratio": small / r_nom[name],
                "small_h_finite": 0.0 < small / r_nom[name] <= 0.2,
                "monotone_in_h": mono_h,
                "monotone_in_sigma": mono_s,
                "non_negative": r_nom[name] >= 0.0,
                "dyn_amplification_at_1mm": 1.0 + 0.5 * c["v"] / math.sqrt(G * 1e-3),
            })
    return rows


def probe_p7_spread(cases: Sequence[Dict]) -> List[Dict]:
    """Inter-implementation spread at the trial conditions (headline finding)."""
    rows = []
    for c in cases:
        vals = {n: fn(c["ship"], c["v"], c["h"], c["sigma_mpa"])["total"] / 1000.0 for n, fn in IMPLS.items()}
        vmax, vmin = max(vals.values()), min(vals.values())
        rows.append({**{("kN_" + k): v for k, v in vals.items()},
                     "ship": c["key"], "max_kN": vmax, "min_kN": vmin,
                     "spread_ratio": vmax / vmin if vmin else float("inf")})
    return rows


def probe_p8_submersion_form(cases: Sequence[Dict]) -> List[Dict]:
    """Effective ice-hull contact measure of each submersion term.

    Lindqvist's submersion term integrates a per-unit-length buoyancy force over
    the rubble-transport length 0.3 L; the polar term replaces that length by
    T * Cb.  The equivalent contact *area* (in m^2, i.e. submerged cross-section
    times contact length) exposes the difference:
        polar:     B * T * Cb * (mu + sin beta)
        lindqvist: B * (1 + mu cot phi) * 0.3 L
    """
    rows = []
    for c in cases:
        ship = c["ship"]
        phi = math.radians(ship.bow_deg)
        beta = math.atan(2.0 * ship.T / ship.L)
        pol = ship.B * ship.T * ship.Cb * (ship.mu + math.sin(beta))
        lq = ship.B * (1.0 + ship.mu / math.tan(phi)) * 0.3 * ship.L
        comp = {n: fn(ship, c["v"], c["h"], c["sigma_mpa"]) for n, fn in IMPLS.items()}
        rows.append({
            "ship": c["key"],
            "polar_contact_area_m2": pol,
            "lindqvist_contact_area_m2": lq,
            "area_ratio_lindqvist_over_polar": lq / pol,
            "polar_submersion_kN": comp["polar"]["submersion"] / 1000.0,
            "lindqvist_submersion_kN": comp["lindqvist"]["submersion"] / 1000.0,
            "submersion_ratio_lindqvist_over_polar": comp["lindqvist"]["submersion"] / comp["polar"]["submersion"],
        })
    return rows


def probe_p6_speed(cases: Sequence[Dict]) -> List[Dict]:
    rows = []
    for c in cases:
        for name, fn in IMPLS.items():
            r0 = fn(c["ship"], 0.5 * c["v"], c["h"], c["sigma_mpa"])["total"]
            r1 = fn(c["ship"], c["v"], c["h"], c["sigma_mpa"])["total"]
            r2 = fn(c["ship"], 1.5 * c["v"], c["h"], c["sigma_mpa"])["total"]
            rows.append({
                "ship": c["key"], "impl": name,
                "R_0.5v_ratio": r0 / r1, "R_1.5v_ratio": r2 / r1,
                "speed_sensitivity_pct": 100.0 * (r2 - r0) / r1,
            })
    return rows


def verdict(result: Dict) -> Dict:
    """Machine verdict against the pre-registered analytical criteria."""
    checks = []

    # C1: total exponent inside the analytical window for every (ship, h)
    bad = [(r["ship"], r["h_m"], round(r["n_total"], 3))
           for r in result["P3_exponents"]["by_impl"]["polar"] if not (0.9 <= r["n_total"] <= 2.1)]
    checks.append({"id": "C1_polar_total_exponent_in_[0.9,2.1]", "pass": not bad, "failures": bad})

    bad = [(r["ship"], r["h_m"], round(r["n_total"], 3))
           for r in result["P3_exponents"]["by_impl"]["lindqvist"] if not (0.9 <= r["n_total"] <= 2.1)]
    checks.append({"id": "C1_lindqvist_total_exponent_in_[0.9,2.1]", "pass": not bad, "failures": bad})

    # C2: breaking exponent must be h^1.25 (elastic beam on elastic foundation).
    # The admissible window [1.00, 1.30] also admits the documented friction
    # multiplier (1 + mu pi l_c / (4 h)), which lowers the exponent by up to
    # 0.25 * delta, delta = share of that multiplier in the total breaking term.
    ana = result["P3_exponents"]["analytic_breaking_exponent"]
    for name in IMPLS:
        ns = [r["n_breaking"] for r in result["P3_exponents"]["by_impl"][name]]
        checks.append({"id": f"C2_{name}_breaking_exponent_vs_{ana:.3f}",
                       "pass": all(1.00 <= n <= 1.30 for n in ns),
                       "min": min(ns), "max": max(ns)})

    # C3: ice resistance must not move with ship length by more than the physics
    #     allows (length enters only through the submersion length factor)
    for name in IMPLS:
        worst = max(abs(r[name + "_dL_hi_pct"]) for r in result["P4_length_sensitivity"])
        checks.append({"id": f"C3_{name}_length_sensitivity_lt_15pct", "pass": worst < 15.0,
                       "worst_dL_hi_pct": worst})

    # C4: invariants
    for name in IMPLS:
        rows = [r for r in result["P5_invariants"] if r["impl"] == name]
        checks.append({"id": f"C4_{name}_invariants",
                       "pass": all(r["small_h_finite"] and r["monotone_in_h"]
                                   and r["monotone_in_sigma"] and r["non_negative"] for r in rows)})

    # C5: absolute accuracy vs the published full-scale means (15 % platform gate)
    for name in IMPLS:
        for row in result["P1_absolute"]:
            err = abs(row[name + "_err_pct"])
            checks.append({"id": f"C5_{name}_{row['ship']}_abs_err_lt_15pct", "pass": err < 15.0,
                           "err_pct": row[name + "_err_pct"]})
    return {
        "checks": checks,
        "n_checks": len(checks),
        "n_pass": sum(1 for c in checks if c["pass"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "docs/reports/artifacts/m1682_ice_resistance_crosscheck.json"))
    ap.add_argument("--figdir", default=str(ROOT / "docs/reports/figures"))
    args = ap.parse_args(argv)

    cases = trial_cases()
    result = {
        "harness": "tools/ice_resistance_crosscheck.py",
        "implementations": {
            "polar": "services/polar/resistance.py::ice_components (TP-POLAR-1)",
            "lindqvist": "services/ice_resistance_uq.py::_lindqvist_raw (Lindqvist 1989)",
            "twin": "services/digital_twin/models/resistance.py::lindqvist_ice_resistance",
        },
        "trial_cases": [
            {k: c[k] for k in ("key", "mean_resistance_kN", "h", "sigma_mpa", "v", "mu", "calibration_factor")}
            for c in cases
        ],
        "P1_absolute": probe_p1_absolute(cases),
        "P2_components": probe_p2_components(cases),
        "P3_exponents": probe_p3_exponents(cases),
        "P4_length_sensitivity": probe_p4_length_sensitivity(cases),
        "P5_invariants": probe_p5_invariants(cases),
        "P6_speed": probe_p6_speed(cases),
        "P7_inter_impl_spread": probe_p7_spread(cases),
        "P8_submersion_form": probe_p8_submersion_form(cases),
    }
    result["verdict"] = verdict(result)
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=float))

    print(f"== P1 absolute level vs published full-scale means")
    hdr = ["ship", "trial_kN", "polar_kN", "polar_err%", "lindq_kN", "lindq_err%", "twin_kN", "twin_err%"]
    print("  " + "".join(f"{h:>14s}" for h in hdr))
    for r in result["P1_absolute"]:
        print("  " + f"{r['ship']:>14s}" + f"{r['trial_kN']:>14.1f}"
              + f"{r['polar_kN']:>14.1f}{r['polar_err_pct']:>14.2f}"
              + f"{r['lindqvist_kN']:>14.1f}{r['lindqvist_err_pct']:>14.2f}"
              + f"{r['twin_kN']:>14.1f}{r['twin_err_pct']:>14.2f}")

    print("== P2 component split at trial conditions [kN]")
    print(f"  {'ship':>12s} {'impl':>10s} {'crush':>10s} {'break':>10s} {'subm':>10s} {'total':>10s} {'sub share':>10s}")
    for r in result["P2_components"]:
        print(f"  {r['ship']:>12s} {r['impl']:>10s} {r['crushing']:>10.1f} {r['breaking']:>10.1f} "
              f"{r['submersion']:>10.1f} {r['total']:>10.1f} {r['submersion_share']:>10.3f}")

    print("== P3 local exponents (analytic: crush=2, break=%.3f, sub=1)" % result["P3_exponents"]["analytic_breaking_exponent"])
    for name in IMPLS:
        for h0 in (0.3, 0.6, 1.0, 1.5):
            sub = [r for r in result["P3_exponents"]["by_impl"][name] if r["h_m"] == h0]
            print(f"  {name:>10s} h={h0:<4.1f} " + " ".join(
                f"{s['ship'][:8]}: n_tot={s['n_total']:.3f} n_br={s['n_breaking']:.3f} n_sub={s['n_submersion']:.3f}"
                for s in sub))

    print("== P4 ship-length sensitivity (+-10% L at fixed B,T)")
    for r in result["P4_length_sensitivity"]:
        print(f"  {r['ship']:>12s} " + " ".join(
            f"{n}: hi{r[n+'_dL_hi_pct']:+.2f}% lo{r[n+'_dL_lo_pct']:+.2f}%"
            for n in IMPLS))

    print("== P7 inter-implementation spread at trial conditions")
    for r in result["P7_inter_impl_spread"]:
        print(f"  {r['ship']:>12s} " + " ".join(f"{n}={r['kN_'+n]:.1f}kN" for n in IMPLS)
              + f"  spread={r['spread_ratio']:.2f}x")

    print("== P8 submersion term: equivalent ice-hull contact area")
    for r in result["P8_submersion_form"]:
        print(f"  {r['ship']:>12s} polar={r['polar_contact_area_m2']:.1f}m2 "
              f"lindqvist={r['lindqvist_contact_area_m2']:.1f}m2 "
              f"ratio={r['area_ratio_lindqvist_over_polar']:.1f}x "
              f"sub_kN {r['polar_submersion_kN']:.1f} vs {r['lindqvist_submersion_kN']:.1f}")

    print("== P6 speed sensitivity")
    for r in result["P6_speed"]:
        print(f"  {r['ship']:>12s} {r['impl']:>10s} R(0.5v)/R(v)={r['R_0.5v_ratio']:.4f} "
              f"R(1.5v)/R(v)={r['R_1.5v_ratio']:.4f} spread={r['speed_sensitivity_pct']:+.1f}%")

    v = result["verdict"]
    print(f"== VERDICT {v['n_pass']}/{v['n_checks']} checks pass")
    for c in v["checks"]:
        if not c["pass"]:
            print("   FAIL", c)
    print(f"json -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())