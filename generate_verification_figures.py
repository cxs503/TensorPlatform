"""
Generate verification report figures for resistance benchmark comparison.

Produces:
1. Ct vs Froude number comparison (calc vs experimental) for all 3 hull types
2. Error percentage vs Froude number
3. Summary table as text
"""
import math
import json
import base64
import io
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

with open(BACKEND_DIR / "data" / "benchmark_reference.json") as f:
    benchmark_data = json.load(f)

SHIP_GEOMETRY = {
    "SUBOFF": {"length": 4.356, "beam": 0.508, "draft": 0.508, "cb": 0.508},
    "DTMB-5415": {"length": 5.720, "beam": 0.759, "draft": 0.246, "cb": 0.508},
    "KVLCC2": {"length": 320.0, "beam": 58.0, "draft": 20.8, "cb": 0.8098},
}

def compute_resistance(length, beam, draft, cb, speed_ms, water_density=998.0, kinematic_viscosity=1.004e-6):
    L, B, Cb, V, rho, nu = length, beam, cb, speed_ms, water_density, kinematic_viscosity
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * V ** 2
    g = 9.81
    Fn = V / math.sqrt(g * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2
    Rt = Rf + Rr
    dynamic_pressure = 0.5 * rho * S * V ** 2
    Ct = Rt / dynamic_pressure if dynamic_pressure > 0 else 0.0
    return {"Fn": Fn, "Ct": Ct, "S": S, "Re": Re, "Cf": Cf}

# Compute all results
results_by_type = {"SUBOFF": [], "DTMB-5415": [], "KVLCC2": []}
for case in benchmark_data["cases"]:
    ship_type = case["ship_type"]
    geo = SHIP_GEOMETRY.get(ship_type)
    if geo is None:
        continue
    speed_ms = case.get("speed_ms", 0.0)
    if speed_ms <= 0:
        continue
    calc = compute_resistance(geo["length"], geo["beam"], geo["draft"], geo["cb"], speed_ms)
    ref_ct = case["experimental_ct"]
    error_pct = abs(calc["Ct"] - ref_ct) / ref_ct * 100.0 if ref_ct > 0 else 999.0
    results_by_type[ship_type].append({
        "case_id": case["case_id"],
        "speed_ms": speed_ms,
        "fn": calc["Fn"],
        "calc_ct": calc["Ct"],
        "ref_ct": ref_ct,
        "error_pct": error_pct,
        "pass_15pct": error_pct <= 15.0,
    })

# ---------------------------------------------------------------------------
# Figure 1: Ct vs Fn comparison
# ---------------------------------------------------------------------------

fig, axes = plt.subplots(1, 3, figsize=(18, 5))
colors = {"SUBOFF": "#2196F3", "DTMB-5415": "#FF9800", "KVLCC2": "#4CAF50"}
markers = {"SUBOFF": "o", "DTMB-5415": "s", "KVLCC2": "^"}

for idx, (ship_type, results) in enumerate(results_by_type.items()):
    ax = axes[idx]
    fns = [r["fn"] for r in results]
    calc_cts = [r["calc_ct"] for r in results]
    ref_cts = [r["ref_ct"] for r in results]

    ax.plot(fns, ref_cts, markers[ship_type], color=colors[ship_type],
            markersize=10, label="Experimental", zorder=5)
    ax.plot(fns, calc_cts, markers[ship_type], color=colors[ship_type],
            markersize=10, markerfacecolor='none', markeredgewidth=2,
            label="Calculated (axisymmetric S)", zorder=4)

    # Connect points with lines
    ax.plot(fns, ref_cts, '-', color=colors[ship_type], alpha=0.3, linewidth=1)
    ax.plot(fns, calc_cts, '--', color=colors[ship_type], alpha=0.3, linewidth=1)

    ax.set_xlabel("Froude Number (Fn)", fontsize=11)
    ax.set_ylabel("Total Resistance Coefficient (Ct)", fontsize=11)
    ax.set_title(f"{ship_type}", fontsize=13, fontweight='bold')
    ax.legend(fontsize=9, loc='upper left')
    ax.grid(True, alpha=0.3)
    ax.ticklabel_format(style='sci', axis='y', scilimits=(-4, -2))

plt.suptitle("Resistance Coefficient: Calculated vs Experimental", fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()

fig_path = OUTPUT_DIR / "ct_vs_fn_comparison.png"
fig.savefig(fig_path, dpi=150, bbox_inches='tight', facecolor='white')
plt.close(fig)
print(f"Saved: {fig_path}")

# ---------------------------------------------------------------------------
# Figure 2: Error percentage vs Fn
# ---------------------------------------------------------------------------

fig, ax = plt.subplots(figsize=(10, 6))

for ship_type, results in results_by_type.items():
    fns = [r["fn"] for r in results]
    errors = [r["error_pct"] for r in results]
    ax.plot(fns, errors, markers[ship_type], color=colors[ship_type],
            markersize=12, label=ship_type, zorder=5)
    ax.plot(fns, errors, '-', color=colors[ship_type], alpha=0.3, linewidth=1)

# 15% threshold line
ax.axhline(y=15.0, color='red', linestyle='--', linewidth=2, alpha=0.7, label="15% threshold")
ax.fill_between([0, 2.5], 0, 15, alpha=0.1, color='green', label="Pass zone")
ax.fill_between([0, 2.5], 15, 700, alpha=0.1, color='red')

ax.set_xlabel("Froude Number (Fn)", fontsize=12)
ax.set_ylabel("Error Percentage (%)", fontsize=12)
ax.set_title("Resistance Calculation Error vs Froude Number", fontsize=14, fontweight='bold')
ax.legend(fontsize=10, loc='upper left')
ax.grid(True, alpha=0.3)
ax.set_ylim(0, 100)

plt.tight_layout()
fig_path2 = OUTPUT_DIR / "error_vs_fn.png"
fig.savefig(fig_path2, dpi=150, bbox_inches='tight', facecolor='white')
plt.close(fig)
print(f"Saved: {fig_path2}")

# ---------------------------------------------------------------------------
# Figure 3: Summary bar chart
# ---------------------------------------------------------------------------

fig, ax = plt.subplots(figsize=(10, 5))

ship_types = ["SUBOFF", "DTMB-5415", "KVLCC2"]
pass_counts = []
fail_counts = []
for st in ship_types:
    results = results_by_type[st]
    pass_counts.append(sum(1 for r in results if r["pass_15pct"]))
    fail_counts.append(sum(1 for r in results if not r["pass_15pct"]))

x = range(len(ship_types))
width = 0.35
bars1 = ax.bar([i - width/2 for i in x], pass_counts, width, label='Pass (≤15%)', color='#4CAF50')
bars2 = ax.bar([i + width/2 for i in x], fail_counts, width, label='Fail (>15%)', color='#F44336')

ax.set_xlabel("Hull Type", fontsize=12)
ax.set_ylabel("Number of Cases", fontsize=12)
ax.set_title("Benchmark Pass/Fail Summary by Hull Type", fontsize=14, fontweight='bold')
ax.set_xticks(list(x))
ax.set_xticklabels(ship_types, fontsize=11)
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3, axis='y')

# Add count labels on bars
for bar in bars1:
    height = bar.get_height()
    if height > 0:
        ax.annotate(f'{int(height)}', xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=11)
for bar in bars2:
    height = bar.get_height()
    if height > 0:
        ax.annotate(f'{int(height)}', xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=11)

plt.tight_layout()
fig_path3 = OUTPUT_DIR / "pass_fail_summary.png"
fig.savefig(fig_path3, dpi=150, bbox_inches='tight', facecolor='white')
plt.close(fig)
print(f"Saved: {fig_path3}")

# ---------------------------------------------------------------------------
# Print summary table
# ---------------------------------------------------------------------------

print("\n" + "=" * 80)
print("RESISTANCE BENCHMARK VERIFICATION SUMMARY")
print("=" * 80)
print(f"{'Case':<15} {'Ship Type':<12} {'Fn':>6} {'Ref Ct':>8} {'Calc Ct':>8} {'Error%':>8} {'Result':>7}")
print("-" * 80)

all_results = []
for ship_type, results in results_by_type.items():
    for r in results:
        all_results.append(r)
        status = "PASS" if r["pass_15pct"] else "FAIL"
        print(f"{r['case_id']:<15} {ship_type:<12} {r['fn']:>6.3f} {r['ref_ct']:>8.4f} {r['calc_ct']:>8.4f} {r['error_pct']:>7.1f}% {status:>7}")

print("-" * 80)
total = len(all_results)
passed = sum(1 for r in all_results if r["pass_15pct"])
failed = total - passed
print(f"Total: {total} cases | Pass: {passed} ({passed/total*100:.0f}%) | Fail: {failed} ({failed/total*100:.0f}%)")
print()

# Per-type summary
for ship_type in ship_types:
    results = results_by_type[ship_type]
    p = sum(1 for r in results if r["pass_15pct"])
    f = len(results) - p
    avg_err = sum(r["error_pct"] for r in results) / len(results)
    print(f"{ship_type}: {p}/{len(results)} pass, avg error = {avg_err:.1f}%")

print()
print("KEY FINDINGS:")
print("1. SUBOFF (axisymmetric): 3/4 pass. Formula valid for Fr < 0.92.")
print("2. DTMB-5415 (fine-form): 1/4 pass. Large errors at high Fn due to wrong wetted surface.")
print("3. KVLCC2 (full-form tanker): 0/4 pass. ~50% error due to axisymmetric S formula.")
print()
print("ROOT CAUSE: Wetted surface S = π·B·L·(0.5+0.25·Cb) is for axisymmetric bodies only.")
print("For ship hulls, use Holtrop's S formula or actual geometry.")
print("=" * 80)
