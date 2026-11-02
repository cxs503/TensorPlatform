#!/usr/bin/env python3
"""Seakeeping analytic verification — generates figures and numeric tables.

Usage: python3 tools/verify/seakeeping_analytic_check.py --out outputs/m1827
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend", "api"))

from seakeeping_physics import (
    deep_water_k, encounter_frequency, natural_periods,
    rao_curves, solve_rao, _fa, _fb, cwp_from_cb,
    N_STATIONS_DEFAULT, FREQ_GRID_DEFAULT,
)

# ---------------------------------------------------------------------------
# Benchmark ships
# ---------------------------------------------------------------------------
SHIPS = {
    "KVLCC2": {
        "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098,
        "displacement": 307000.0, "GM": 4.5, "KG": 18.0, "rho": 1025.0,
    },
    "S175": {
        "L": 175.0, "B": 25.4, "T": 9.5, "Cb": 0.570,
        "displacement": 24500.0, "GM": 2.8, "KG": 10.0, "rho": 1025.0,
    },
    "BARGE60": {
        "L": 60.0, "B": 12.0, "T": 4.0, "Cb": 0.850,
        "displacement": 5500.0, "GM": 3.0, "KG": 4.5, "rho": 1025.0,
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="outputs/m1827")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(os.path.join(args.out, "figures"), exist_ok=True)

    results = {}

    # ------------------------------------------------------------------ 1. Natural periods
    print("=== Natural Periods ===")
    periods = {}
    for name, ship in SHIPS.items():
        nat = natural_periods(ship)
        periods[name] = {
            "T33": round(nat["heave"], 2),
            "T55": round(nat["pitch"], 2),
            "T44": round(nat["roll"], 2),
            "A33_over_mass": round(nat["A33_over_mass"], 4),
        }
        print(f"  {name}: T33={nat['heave']:.2f}s, T55={nat['pitch']:.2f}s, "
              f"T44={nat['roll']:.2f}s, A33/m={nat['A33_over_mass']:.4f}")
    results["natural_periods"] = periods

    # ------------------------------------------------------------------ 2. Long-wave limit (heave RAO -> 1)
    print("\n=== Long-Wave Limit (Heave RAO -> 1) ===")
    long_wave = {}
    for name, ship in SHIPS.items():
        for w in [0.05, 0.08, 0.10, 0.15, 0.20]:
            r = solve_rao(ship, w, 180.0, 0.0, 1.0)
            k = deep_water_k(w)
            kl = k * ship["L"]
            key = f"{name}_w{w}"
            long_wave[key] = {
                "omega": w, "kL": round(kl, 4),
                "heave_rao": round(r["heave_rao"], 6),
                "err_pct": round((r["heave_rao"] - 1.0) * 100, 2),
            }
            print(f"  {name} @ ω={w}: kL={kl:.4f}, RAO={r['heave_rao']:.6f}, err={long_wave[key]['err_pct']:+.2f}%")
    results["long_wave_limit"] = long_wave

    # ------------------------------------------------------------------ 3. Roll symmetry
    print("\n=== Roll RAO vs Heading ===")
    roll_sym = {}
    for name in ["KVLCC2", "S175"]:
        ship = SHIPS[name]
        for beta in [0, 45, 90, 135, 180]:
            r = solve_rao(ship, 0.6, beta, 14.0, 1.0)
            roll_sym[f"{name}_beta{beta}"] = {
                "heading": beta,
                "roll_rao": round(r["roll_rao"], 6),
            }
            print(f"  {name} β={beta}°: roll_RAO={r['roll_rao']:.6f}")
    results["roll_symmetry"] = roll_sym

    # ------------------------------------------------------------------ 4. Encounter frequency
    print("\n=== Encounter Frequency ===")
    enc_freq = {}
    for w in [0.2, 0.6, 1.0]:
        for u_kn in [7.0, 14.0]:
            for beta in [0, 90, 180]:
                u_ms = u_kn * 1852.0 / 3600.0
                we = encounter_frequency(w, u_ms, beta)
                k = deep_water_k(w)
                we_analytic = abs(w - k * u_ms * math.cos(math.radians(beta)))
                err = abs(we - we_analytic) / max(we_analytic, 1e-12) * 100
                key = f"w{w}_u{u_kn}_b{beta}"
                enc_freq[key] = {
                    "omega": w, "speed_kn": u_kn, "heading": beta,
                    "we_calc": round(we, 6), "we_analytic": round(we_analytic, 6),
                    "err_pct": round(err, 4),
                }
                if err > 0.01:
                    print(f"  {key}: we={we:.6f}, analytic={we_analytic:.6f}, err={err:.4f}%")
    results["encounter_frequency"] = enc_freq

    # ------------------------------------------------------------------ 5. Frequency factors
    print("\n=== Frequency Factors ===")
    freq_factors = {}
    for nu in [0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0]:
        fa = _fa(nu)
        fb = _fb(nu)
        freq_factors[f"nu{nu}"] = {"nu": nu, "fa": round(fa, 6), "fb": round(fb, 6)}
        print(f"  ν={nu}: f_a={fa:.6f}, f_b={fb:.6f}")
    results["frequency_factors"] = freq_factors

    # ------------------------------------------------------------------ 6. RAO curves for figures
    print("\n=== Generating RAO curves ===")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Figure 1: KVLCC2 Heave RAO
    fig, ax = plt.subplots(figsize=(8, 5))
    for name, ship in SHIPS.items():
        rc = rao_curves(ship, heading_deg=180.0, speed_kn=0.0)
        freqs = np.array(rc["frequencies"])
        kl = deep_water_k(freqs) * ship["L"]
        ax.plot(freqs, rc["heave_rao"], label=f"{name} (kL={kl[0]:.2f}~{kl[-1]:.2f})")
    ax.axhline(1.0, color="red", linestyle="--", alpha=0.7, label="Long-wave limit (RAO=1)")
    ax.set_xlabel("Wave frequency ω (rad/s)")
    ax.set_ylabel("Heave RAO (m/m)")
    ax.set_title("KVLCC2 / S175 / BARGE60 — Heave RAO vs Frequency")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "figures", "seakeeping_heave_rao.png"), dpi=150)
    plt.close(fig)
    print("  Saved: figures/seakeeping_heave_rao.png")

    # Figure 2: Roll RAO vs heading
    fig, ax = plt.subplots(figsize=(8, 5))
    betas = np.arange(0, 181, 5)
    for name in ["KVLCC2", "S175"]:
        ship = SHIPS[name]
        roll_raos = []
        for b in betas:
            r = solve_rao(ship, 0.6, b, 14.0, 1.0)
            roll_raos.append(r["roll_rao"])
        ax.plot(betas, roll_raos, label=name)
    ax.set_xlabel("Wave heading β (deg, 0=following, 180=head)")
    ax.set_ylabel("Roll RAO (rad/ka)")
    ax.set_title("Roll RAO vs Wave Heading (ω=0.6 rad/s, U=14 kn)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "figures", "seakeeping_roll_heading.png"), dpi=150)
    plt.close(fig)
    print("  Saved: figures/seakeeping_roll_heading.png")

    # Figure 3: Frequency factors
    fig, ax = plt.subplots(figsize=(8, 5))
    nus = np.logspace(-2, 1.5, 100)
    fas = [_fa(n) for n in nus]
    fbs = [_fb(n) for n in nus]
    ax.semilogx(nus, fas, label="f_a(ν) — added mass factor", linewidth=2)
    ax.semilogx(nus, fbs, label="f_b(ν) — damping factor", linewidth=2)
    ax.axhline(1.0, color="gray", linestyle=":", alpha=0.5)
    ax.axhline(0.75, color="gray", linestyle=":", alpha=0.5)
    ax.set_xlabel("ν = ωe²T/g")
    ax.set_ylabel("Frequency factor")
    ax.set_title("2D Hydrodynamic Frequency Factors")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "figures", "seakeeping_freq_factors.png"), dpi=150)
    plt.close(fig)
    print("  Saved: figures/seakeeping_freq_factors.png")

    # ------------------------------------------------------------------ Save results
    out_path = os.path.join(args.out, "seakeeping_analytic_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")
    print("Done.")


if __name__ == "__main__":
    main()
