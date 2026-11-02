#!/usr/bin/env python3
"""Added-resistance / speed-loss verification gate (mission m1859, verifier seat).

Closes the open verification debt recorded in
``docs/verification/seakeeping_crosspath_adjudication.md`` (lines 120-121, 151):
the digital-twin added resistance ``R_AW`` has **no wave-period dependence** and the
registry attributes it to a literature method (Gerritsma-Beukelman) that is not
implemented.  This gate turns both statements into machine-checkable criteria and
writes the evidence artefacts.

Criteria (all evaluated on the *live* module, nothing copied):

W1  Period sensitivity   : R_AW(Tp) spread over Tp in [3,14] s at fixed Hs must exceed
                           15 % (added resistance of a real ship peaks near lambda ~ L).
W2  Quadratic law        : log-log slope of R_AW vs Hs must be 2 (second-order theory);
                           error must be < 1 %.
W3  Declaration drift    : the machine-readable ``RAW_LAW`` declaration must reproduce
                           ``added_resistance()`` to < 1e-12 relative on a 5-ship x
                           4-heading x 5-speed x 3-Hs grid (doc/code drift gate).
W4  Zero-speed artefact  : R_AW(Fn->0)/R_AW(Fn=0.20) must stay inside [0.8, 1.2]
                           (a mean drift force does not halve when the ship stops).
W5  Declared band        : R_AW(Hs=4 m, head seas) / R_calm must land in the declared
                           5-15 % band for full-form ships (Holtrop, in-repo kernel).
W6  Downstream blindness : the speed loss implied by R_AW must vary with Tp by > 15 %
                           (it cannot, given W1: this is the propagation criterion).

Usage:  python3 tools/verifier/raw_added_resistance_gate.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.digital_twin.models.geometry import HullForm  # noqa: E402
from services.digital_twin.models.resistance import total_resistance  # noqa: E402
from services.digital_twin.models.seakeeping import (  # noqa: E402
    RAW_LAW,
    added_resistance,
    irregular_seastate,
)

G = 9.80665
SHIPS = [
    # name, L, B, T, Cb, design Fn
    ("KVLCC2 tanker", 320.0, 58.0, 20.8, 0.82, 0.137),
    ("KCS container", 230.0, 32.2, 10.8, 0.651, 0.190),
    ("Handysize bulker", 180.0, 30.0, 10.5, 0.83, 0.160),
    ("Ferry", 120.0, 20.0, 5.0, 0.60, 0.220),
    ("Trawler", 45.0, 11.0, 3.2, 0.55, 0.280),
]


def hull(name_lbtc):
    n, L, B, T, Cb, Fn = name_lbtc
    return HullForm(L, B, T, Cb), Fn, L


def calm_resistance(V, L, B, T, Cb, rho=1025.0):
    r = total_resistance(float(V), L, B, T, Cb, 0.0, 0.0, 0.0, rho=rho)
    return float(r["R_calm_N"])


def declared_raw(Hs, heading_deg, h, U, c_aw=None):
    """Independent re-evaluation of RAW_LAW['formula'] from the declaration fields."""
    c = RAW_LAW["c_aw"] if c_aw is None else c_aw
    Fn = U / math.sqrt(G * h.L)
    head = math.cos(math.radians(0.5 * (180.0 - abs(heading_deg)))) ** 2
    speed_fac = 0.5 + 0.5 * min(Fn / RAW_LAW["fn_ref"], RAW_LAW["fn_cap"])
    return c * head * speed_fac * h.rho * G * Hs ** 2 * h.B ** 2 / h.L


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(__file__), "..", "..",
        "artifacts", "verification_debt", "added_resistance_speed_loss"))
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)

    crit = []
    detail = {}

    # ---------------- W1 period sensitivity ----------------
    tps = [3.0, 5.0, 7.0, 9.0, 11.0, 14.0]
    w1_rows = []
    for ship in SHIPS:
        h, Fn, L = hull(ship)
        V = Fn * math.sqrt(G * L)
        vals = [added_resistance(4.0, 180.0, h, V) for _ in tps]
        heave = [irregular_seastate(4.0, tp, 180.0, h, 0.5 * h.T + 2.0, V,
                                    n_freq=16, n_sections=31)["heave_rms_m"] for tp in tps]
        spread = (max(vals) - min(vals)) / max(max(vals), 1e-12)
        w1_rows.append({"ship": ship[0], "Hs_m": 4.0, "Tp_s": tps,
                        "R_aw_kN": [v / 1e3 for v in vals],
                        "spread_frac": spread,
                        "heave_rms_m": heave})
    w1_spread = max(r["spread_frac"] for r in w1_rows)
    crit.append({"id": "W1", "name": "period sensitivity (>15% over Tp in [3,14] s)",
                 "value": w1_spread, "threshold": 0.15,
                 "pass": bool(w1_spread > 0.15),
                 "note": "measured max-min spread of R_AW at fixed Hs=4 m"})
    detail["W1"] = w1_rows

    # ---------------- W2 quadratic law ----------------
    h0, Fn0, L0 = hull(SHIPS[0])
    V0 = Fn0 * math.sqrt(G * L0)
    hs = np.array([0.5, 1.0, 2.0, 4.0, 8.0])
    raw = np.array([added_resistance(float(x), 180.0, h0, V0) for x in hs])
    slope = float(np.polyfit(np.log(hs), np.log(raw), 1)[0])
    crit.append({"id": "W2", "name": "quadratic law d(log R_AW)/d(log Hs) = 2 (err<1%)",
                 "value": slope, "target": 2.0,
                 "error_pct": 100.0 * abs(slope - 2.0) / 2.0,
                 "pass": bool(abs(slope - 2.0) / 2.0 <= 0.01),
                 "note": "second-order theory: added resistance scales with wave amplitude^2"})
    detail["W2"] = {"Hs_m": hs.tolist(), "R_aw_N": raw.tolist(), "loglog_slope": slope}

    # ---------------- W3 declaration vs code ----------------
    worst = 0.0
    worst_pt = None
    n_pts = 0
    for ship in SHIPS:
        h, Fn, L = hull(ship)
        for hd in (0.0, 90.0, 135.0, 180.0):
            for fn in (0.0, 0.08, 0.15, 0.25, 0.35):
                U = fn * math.sqrt(G * L)
                for H in (1.0, 4.0, 8.0):
                    code = added_resistance(H, hd, h, U)
                    decl = declared_raw(H, hd, h, U)
                    den = max(abs(code), 1e-12)
                    rel = abs(code - decl) / den
                    n_pts += 1
                    if rel > worst:
                        worst, worst_pt = rel, {"ship": ship[0], "heading_deg": hd,
                                                "Fn": fn, "Hs_m": H}
    crit.append({"id": "W3", "name": "RAW_LAW declaration reproduces code (<1e-12 rel)",
                 "value": worst, "threshold": 1e-12, "points": n_pts,
                 "worst_point": worst_pt,
                 "pass": bool(worst < 1e-12),
                 "note": "doc/code drift gate: the declaration is the single source of truth"})
    detail["W3"] = {"points": n_pts, "worst_rel_residual": worst, "worst_point": worst_pt}

    # ---------------- W4 zero-speed artefact ----------------
    fn_ref = RAW_LAW["fn_ref"]
    h1, Fn1, L1 = hull(SHIPS[0])
    r0 = added_resistance(4.0, 180.0, h1, 0.0)
    r_ref = added_resistance(4.0, 180.0, h1, fn_ref * math.sqrt(G * L1))
    ratio = r0 / r_ref
    crit.append({"id": "W4", "name": "R_AW(Fn->0)/R_AW(Fn=0.20) in [0.8,1.2]",
                 "value": ratio, "pass": bool(0.8 <= ratio <= 1.2),
                 "note": "a mean wave drift force does not halve when the ship stops; "
                         "the 0.5 offset in the speed factor is an artefact"})
    detail["W4"] = {"R_aw_zero_speed_N": r0, "R_aw_Fn020_N": r_ref, "ratio": ratio,
                    "speed_fac_Fn030": 0.5 + 0.5 * min(0.30 / fn_ref, RAW_LAW["fn_cap"])}

    # ---------------- W5 declared band ----------------
    w5 = []
    for ship in SHIPS:
        h, Fn, L = hull(ship)
        V = Fn * math.sqrt(G * L)
        rc = calm_resistance(V, h.L, h.B, h.T, h.Cb, h.rho)
        raw4 = added_resistance(4.0, 180.0, h, V)
        w5.append({"ship": ship[0], "Fn": Fn, "V_ms": V, "R_calm_kN": rc / 1e3,
                   "R_aw_kN": raw4 / 1e3, "share_pct": 100.0 * raw4 / rc})
    shares = [r["share_pct"] for r in w5]
    in_band = [r["ship"] for r in w5 if 5.0 <= r["share_pct"] <= 15.0]
    crit.append({"id": "W5", "name": "R_AW/R_calm at Hs=4 m head seas within declared 5-15%",
                 "value": shares, "n_ships": len(w5), "n_in_band": len(in_band),
                 "in_band": in_band,
                 "pass": bool(len(in_band) == len(w5)),
                 "note": "the declared calibration target of c_aw=0.055, checked on the "
                         "in-repo Holtrop kernel"})
    detail["W5"] = w5

    # ---------------- W6 downstream speed loss ----------------
    h2, Fn2, L2 = hull(SHIPS[0])
    V2 = Fn2 * math.sqrt(G * L2)
    dv = 0.02
    drdv = (calm_resistance(V2 + dv, h2.L, h2.B, h2.T, h2.Cb, h2.rho)
            - calm_resistance(V2 - dv, h2.L, h2.B, h2.T, h2.Cb, h2.rho)) / (2 * dv)
    losses = []
    for tp in tps:
        r = irregular_seastate(4.0, tp, 180.0, h2, 0.5 * h2.T + 2.0, V2,
                               n_freq=16, n_sections=31)
        dV = r["R_aw_N"] / drdv           # first-order thrust balance, fixed power setting
        losses.append({"Tp_s": tp, "R_aw_kN": r["R_aw_kN"],
                       "dV_ms": dV, "dV_kn": dV / 0.514444,
                       "loss_pct": 100.0 * dV / V2})
    spread6 = (max(l["dV_ms"] for l in losses) - min(l["dV_ms"] for l in losses)) / max(
        abs(losses[0]["dV_ms"]), 1e-12)
    crit.append({"id": "W6", "name": "speed loss varies with Tp by >15%",
                 "value": spread6, "threshold": 0.15, "pass": bool(spread6 > 0.15),
                 "note": "propagation of W1 into the ship-level metric; "
                         "dR_T/dV from the in-repo Holtrop kernel (central difference)"})
    detail["W6"] = {"dRTdV_N_per_ms": drdv, "rows": losses}

    # ---------------- verdict ----------------
    failed = [c for c in crit if not c["pass"]]
    verdict = {
        "mission": "m1859",
        "law": RAW_LAW,
        "criteria": crit,
        "n_pass": len([c for c in crit if c["pass"]]),
        "n_fail": len(failed),
        "failed": [c["id"] for c in failed],
        "evidence_level": "MOCK",
        "evidence_reason": "R_AW is a declared empirical heuristic (no wavelength, "
                           "spectrum-shape or relative-motion dependence); the registry "
                           "previously attributed it to Gerritsma-Beukelman, which is NOT "
                           "implemented (W1/W6 fail). Absolute accuracy vs model tests: "
                           "WITHHELD (no tank data in repo).",
        "absolute_accuracy": "WITHHELD",
    }
    payload = {"verdict": verdict, "detail": detail}
    json.dump(payload, open(os.path.join(out, "results.json"), "w"), indent=2)

    # ---------------- figures ----------------
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    for r in w1_rows:
        ax.plot(tps, r["R_aw_kN"], "o-", label=f"R_AW {r['ship']}")
    ax.set_xlabel("peak period Tp [s]  (Hs = 4 m, head seas)")
    ax.set_ylabel("R_AW [kN]")
    ax.set_title("W1: R_AW is exactly flat in Tp (verified blindness)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2)
    ax2 = ax.twinx()
    hh = w1_rows[0]
    ax2.plot(tps, hh["heave_rms_m"], "s--", color="k", alpha=0.6,
             label=f"heave RMS {hh['ship']} [m]")
    ax2.set_ylabel("heave RMS [m]")
    ax2.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig1_tp_blindness.png"), dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.axhspan(5.0, 15.0, color="tab:green", alpha=0.12,
               label="declared band 5-15 % of R_calm")
    ax.bar([r["ship"] for r in w5], [r["share_pct"] for r in w5], color="tab:blue",
           alpha=0.8)
    for i, r in enumerate(w5):
        ax.annotate(f"{r['share_pct']:.1f}%", (i, r["share_pct"]),
                    ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("R_AW / R_calm [%]  (Hs=4 m, head seas)")
    ax.set_title("W5: declared calibration band vs measured share")
    ax.tick_params(axis="x", labelsize=8)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig2_declared_band.png"), dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.plot([l["Tp_s"] for l in losses], [l["dV_kn"] for l in losses], "o-",
            label="speed loss [kn]")
    ax.set_xlabel("peak period Tp [s]  (Hs = 4 m, head seas, KVLCC2)")
    ax.set_ylabel("implied speed loss [kn]")
    ax.set_title("W6: Tp-blind speed loss propagated to the ship level")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig3_speed_loss.png"), dpi=140)
    plt.close(fig)

    # ---------------- markdown fragments (zero hand-copy) ----------------
    lines = ["| criterion | measured | threshold | verdict |", "|---|---|---|---|"]
    for c in crit:
        val = c["value"]
        if isinstance(val, list):
            val = ", ".join(f"{v:.1f}" for v in val)
        elif isinstance(val, float):
            val = f"{val:.4g}"
        thr = c.get("threshold", c.get("target", "-"))
        thr = f"{thr:.4g}" if isinstance(thr, float) else thr
        lines.append(f"| {c['id']} {c['name']} | {val} | {thr} | "
                     f"{'PASS' if c['pass'] else 'FAIL'} |")
    open(os.path.join(out, "criteria_table.md"), "w").write("\n".join(lines) + "\n")

    rows = ["| ship | Fn | V [m/s] | R_calm [kN] | R_AW [kN] | share [%] |", "|---|---|---|---|---|---|"]
    for r in w5:
        rows.append(f"| {r['ship']} | {r['Fn']:.3f} | {r['V_ms']:.2f} | "
                    f"{r['R_calm_kN']:.1f} | {r['R_aw_kN']:.1f} | {r['share_pct']:.2f} |")
    open(os.path.join(out, "band_table.md"), "w").write("\n".join(rows) + "\n")

    rows = ["| Tp [s] | R_AW [kN] | implied dV [kn] | dV/V [%] |", "|---|---|---|---|"]
    for l in losses:
        rows.append(f"| {l['Tp_s']:.0f} | {l['R_aw_kN']:.3f} | {l['dV_kn']:.4f} | "
                    f"{l['loss_pct']:.3f} |")
    open(os.path.join(out, "loss_table.md"), "w").write("\n".join(rows) + "\n")

    print(json.dumps({"verdict": {k: verdict[k] for k in
                                  ("n_pass", "n_fail", "failed", "evidence_level")}},
                     indent=2))
    for c in crit:
        print(f"{c['id']} {'PASS' if c['pass'] else 'FAIL'}  {c['name']}  -> "
              f"{c['value'] if not isinstance(c['value'], list) else c['value']}")
    print("artefacts:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())