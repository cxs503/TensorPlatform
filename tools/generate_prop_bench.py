#!/usr/bin/env python3
"""Generate literature benchmark for Wageningen B-series open-water performance."""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import wageningen_b_series as bs

BLADES = [
    (4, 0.70, 1.00, "B4-70"),
    (4, 0.55, 0.80, "B4-55"),
    (5, 0.75, 1.20, "B5-75"),
]
J_POINTS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

def compute_bench():
    points = []
    for z, ae_ao, p_d, label in BLADES:
        j0 = bs.zero_thrust_advance_ratio(p_d, ae_ao, z)
        for j in J_POINTS:
            if j > j0 * 1.05:
                continue
            kt_v = bs.kt(j, p_d, ae_ao, z, strict=False)
            kq_v = bs.kq(j, p_d, ae_ao, z, strict=False)
            eta_v = bs.eta0(j, p_d, ae_ao, z, strict=False)
            if j > 0 and kt_v > 0:
                ct = 8.0 * kt_v / (math.pi * j * j)
                eta_ideal = 2.0 / (1.0 + math.sqrt(1.0 + ct))
                eta_bound_ok = 0 < eta_v < eta_ideal
            else:
                eta_ideal = None
                eta_bound_ok = None
            points.append({
                "blade_type": label, "Z": z, "Ae_Ao": ae_ao, "P_D": p_d,
                "J": j, "J_over_J0": round(j / j0, 6) if j0 > 0 else None,
                "J0": round(j0, 6), "KT": round(kt_v, 6), "KQ": round(kq_v, 6),
                "eta0": round(eta_v, 6),
                "eta_ideal": round(eta_ideal, 6) if eta_ideal is not None else None,
                "eta0_below_ideal": eta_bound_ok,
            })
    return points

def main():
    points = compute_bench()
    bench = {
        "title": "Wageningen B-series Open-Water Benchmark",
        "reference": "Oosterveld & van Oossanen (1975), ISP 22(251), regression coefficients "
                     "transcribed from two independent public implementations (AlfredMylne/PropCalc "
                     "and isulovsky/Wageningen-B-series-propeller-calculator), fetched 2026-11-01, "
                     "term-for-term agreement verified in backend/services/wageningen_b_series.py",
        "source_note": "STRUCTURAL+INVARIANT benchmark: point values computed from the published "
                       "regression coefficient table itself. No external literature table was "
                       "reachable offline. This is NOT literature-agreement evidence; it is a "
                       "self-consistent reference for auditing kernel implementations against the "
                       "published O&O (1975) regression.",
        "blade_types": [
            {"label": "B4-70", "Z": 4, "Ae_Ao": 0.70, "P_D": 1.00},
            {"label": "B4-55", "Z": 4, "Ae_Ao": 0.55, "P_D": 0.80},
            {"label": "B5-75", "Z": 5, "Ae_Ao": 0.75, "P_D": 1.20},
        ],
        "j_points": J_POINTS,
        "points": points,
        "n_points": len(points),
        "generated_by": "tools/generate_prop_bench.py",
        "generated_date": "2026-11-15",
    }
    out_path = os.path.join(ROOT, "artifacts", "prop_openwater", "bench.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(bench, f, indent=2)
    print(f"Generated {len(points)} benchmark points -> {out_path}")
    print(f"\n{'Blade':<8} {'J':>5} {'KT':>8} {'KQ':>8} {'eta0':>6} {'J/J0':>6}")
    print("-" * 50)
    for p in points:
        print(f"{p['blade_type']:<8} {p['J']:>5.2f} {p['KT']:>8.5f} {p['KQ']:>8.5f} "
              f"{p['eta0']:>6.4f} {p['J_over_J0']:>6.3f}")

if __name__ == "__main__":
    main()
