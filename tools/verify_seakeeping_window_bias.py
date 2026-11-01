#!/usr/bin/env python3
"""Seakeeping: frequency-window truncation bias of the *response* spectral moments.

Background
----------
m1645 closed the truncation deficit for the wave spectrum itself (m0/m1/m2 with an
analytic exponential-skeleton tail) but explicitly left the *response* moments
(``response_statistics``: ``Tz_heave_s``, ``Tz_pitch_s``, ``Tz_roll_s``,
``sigma_vertical_acc_m_s2``) and the fatigue module's ``m4`` on the raw
window-only trapezoid rule ("同源偏差未修").  This script adjudicates that debt
quantitatively instead of by inspection.

Reference (machine-decidable, no literature table)
--------------------------------------------------
The reference is the *same* code integrated on an ultra-wide, ultra-fine grid
(omega_min -> 0, omega_max -> 20 rad/s, ~2e4 points).  Because the response
spectrum is RAO^2 * S and the excitation carries ``exp(-k*T)`` (k = omega^2/g),
the response density decays exponentially at high frequency and as
``exp(-B/omega^4)`` at low frequency, so the ultra-wide integral is converged to
far below 1e-6 relative -- it is a numerical truth for the model, not a model
for the truth.  The deficit measured here is therefore a *quadrature/window*
defect of the production call, which is exactly the debt that was declared.

Usage
-----
    python3 tools/verify_seakeeping_window_bias.py            # human table
    python3 tools/verify_seakeeping_window_bias.py --json     # machine output
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Dict, List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services import seakeeping as sk  # noqa: E402

# ---------------------------------------------------------------------------
# Case: KVLCC2-class tanker, design condition
# ---------------------------------------------------------------------------
L, B, T = 320.0, 58.0, 20.8
CB, CWP = 0.8098, 0.8700
DISP = 320_000.0        # t
KG, GM_T = 18.5, 5.00

# Production grid (what the online endpoint actually evaluates) and reference grid.
PROD_OMEGA = sk.uniform_grid(0.10, 2.00, 191)
REF_OMEGA = sk.uniform_grid(0.01, 20.0, 40001)

MODES = (
    "Tz_heave_s", "Tz_pitch_s", "Tz_roll_s",
    "significant_heave", "significant_pitch", "significant_roll",
    "sigma_vertical_acc_m_s2",
)


def build_model() -> "sk.SeakeepingModel":
    return sk.SeakeepingModel(
        L=L, B=B, T=T, Cb=CB, Cwp=CWP, displacement_t=DISP, KG=KG, GM_T=GM_T
    )


def compute(model, omega: List[float], kind: str = "jonswap",
            Hs: float = 6.0, Tp: float = 11.0, heading: float = 180.0,
            speed: float = 0.0) -> Dict[str, float]:
    spec = sk.spectrum(kind, Hs, Tp, omega)
    return sk.response_statistics(model, spec, heading_deg=heading, speed=speed)


def rel(a: float, b: float) -> float:
    if b == 0.0 or not math.isfinite(b):
        return float("nan")
    return (a - b) / b


def run(json_out: bool = False) -> Dict[str, object]:
    model = build_model()
    rows: List[Dict[str, float]] = []

    cases = [
        ("head_jonswap", dict(kind="jonswap", heading=180.0, speed=0.0)),
        ("head_ittc", dict(kind="ittc", heading=180.0, speed=0.0)),
        ("bow_quarter_jonswap", dict(kind="jonswap", heading=135.0, speed=8.0)),
        ("beam_jonswap", dict(kind="jonswap", heading=90.0, speed=0.0)),
        ("following_jonswap", dict(kind="jonswap", heading=0.0, speed=8.0)),
    ]

    for tag, kw in cases:
        prod = compute(model, PROD_OMEGA, **kw)
        ref = compute(model, REF_OMEGA, **kw)
        for key in MODES:
            vp, vr = prod.get(key, float("nan")), ref.get(key, float("nan"))
            rows.append(
                {
                    "case": tag,
                    "quantity": key,
                    "production": vp,
                    "reference": vr,
                    "rel_err": rel(vp, vr),
                }
            )

    worst = max(
        (r for r in rows if math.isfinite(r["rel_err"])),
        key=lambda r: abs(r["rel_err"]),
    )
    payload = {
        "case_definition": {
            "L": L, "B": B, "T": T, "Cb": CB, "Cwp": CWP,
            "displacement_t": DISP, "KG": KG, "GM_T": GM_T,
            "production_grid": {"omega_min": 0.10, "omega_max": 2.00, "n": 191},
            "reference_grid": {"omega_min": 0.01, "omega_max": 20.0, "n": 40001},
            "spectra": ["jonswap(Hs=6 m, Tp=11 s)", "ittc(Hs=6 m, T1=11 s)"],
        },
        "reference_definition": (
            "same implementation on an ultra-wide/fine grid; response density decays "
            "as exp(-k*T)*omega^-5 at high frequency and exp(-B/omega^4) at low "
            "frequency, so the wide integral is converged well below 1e-6 relative"
        ),
        "rows": rows,
        "worst": worst,
        "max_abs_rel_err": max(
            abs(r["rel_err"]) for r in rows if math.isfinite(r["rel_err"])
        ),
    }
    return payload


def main() -> int:
    payload = run()
    if "--json" in sys.argv:
        print(json.dumps(payload, indent=2))
        return 0

    print("case                     quantity                    production      reference     rel_err")
    for r in payload["rows"]:
        print(
            f"{r['case']:<24} {r['quantity']:<26} {r['production']:>12.6f} "
            f"{r['reference']:>13.6f} {r['rel_err'] * 100:>+9.4f}%"
        )
    w = payload["worst"]
    print(
        f"\nworst: {w['case']}/{w['quantity']} = {w['rel_err'] * 100:+.4f}% "
        f"| max |err| = {payload['max_abs_rel_err'] * 100:.4f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())