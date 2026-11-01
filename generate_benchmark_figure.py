#!/usr/bin/env python3
"""Generate benchmark validation figure: Ct vs Fn comparison."""
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
BENCHMARK_PATH = BACKEND_DIR / "data" / "benchmark_reference.json"

with open(BENCHMARK_PATH) as f:
    BENCHMARK_DATA = json.load(f)

SHIP_GEOMETRY = {
    "SUBOFF": {"length": 4.356, "beam": 0.508, "draft": 0.508, "cb": 0.508},
    "DTMB-5415": {"length": 5.720, "beam": 0.759, "draft": 0.246, "cb": 0.508},
    "KVLCC2": {"length": 320.0, "beam": 58.0, "draft": 20.8, "cb": 0.8098},
}

WATER_DENSITY = 998.0
KINEMATIC_VISCOSITY = 1.004e-6
G = 9.81


def calc_resistance_api_logic(geometry, speed_ms):
    L, B, T, Cb = geometry["length"], geometry["beam"], geometry["draft"], geometry["cb"]
    V = speed_ms
    rho, nu = WATER_DENSITY, KINEMATIC_VISCOSITY
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Fn = V / math.sqrt(G * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rt = 0.5 * rho * S * (Cf + Cr) * V ** 2
    Ct = Rt / (0.5 * rho * S * V ** 2) if V > 0 else 0.0
    return Ct, Fn


# Collect data by ship type
ship_types = ["SUBOFF", "DTMB-5415", "KVLCC2"]
colors = {"SUBOFF": "#e74c3c", "DTMB-5415": "#3498db", "KVLCC2": "#2ecc71"}
markers = {"SUBOFF": "o", "DTMB-5415": "s", "KVLCC2": "^"}

fig, axes = plt.subplots(1, 3, figsize=(18, 5))

for idx, ship_type in enumerate(ship_types):
    ax = axes[idx]
    geometry = SHIP_GEOMETRY[ship_type]
    cases = [c for c in BENCHMARK_DATA["cases"] if c["ship_type"] == ship_type]

    fn_exp = []
    ct_exp = []
    fn_calc = []
    ct_calc = []

    for case in cases:
        speed_ms = case.get("speed_ms", 0)
        exp_ct = case["experimental_ct"]
        calc_ct, calc_fn = calc_resistance_api_logic(geometry, speed_ms)
        exp_fn = case.get("froude_number", calc_fn)

        fn_exp.append(exp_fn)
        ct_exp.append(exp_ct)
        fn_calc.append(calc_fn)
        ct_calc.append(calc_ct)

    # Plot experimental
    ax.plot(fn_exp, ct_exp, marker=markers[ship_type], linestyle="-",
            color=colors[ship_type], linewidth=2, label="Experimental", zorder=3)
    # Plot calculated
    ax.plot(fn_calc, ct_calc, marker=markers[ship_type], linestyle="--",
            color=colors[ship_type], linewidth=2, alpha=0.7, label="Calculated (API)", zorder=2)

    ax.set_xlabel("Froude Number (Fn)", fontsize=11)
    ax.set_ylabel("Total Resistance Coefficient (Ct)", fontsize=11)
    ax.set_title(f"{ship_type}", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.ticklabel_format(style="sci", axis="y", scilimits=(0, 0))

plt.suptitle("Resistance Benchmark Validation: Ct vs Fn — Experimental vs Calculated",
             fontsize=14, fontweight="bold", y=1.02)
plt.tight_layout()

output_path = Path(__file__).resolve().parent / "docs" / "resistance_benchmark_ct_vs_fn.png"
fig.savefig(output_path, dpi=150, bbox_inches="tight")
print(f"Figure saved to {output_path}")
plt.close()
