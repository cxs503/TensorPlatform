"""
Generate figures for Truth Gates (m613) paper-quality report.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

OUT = "/srv/tensor/agent/work/m613/artifacts/m613_figures"
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({
    "font.size": 11,
    "axes.labelsize": 12,
    "axes.titlesize": 13,
    "legend.fontsize": 10,
    "figure.dpi": 150,
})

# ─── Figure 1: Architecture diagram ─────────────────────────────────
fig, ax = plt.subplots(figsize=(10, 6))
ax.set_xlim(0, 10)
ax.set_ylim(0, 8)
ax.axis("off")
ax.set_title("Truth Gates Architecture — Independence-Convergence Verdict Mechanism",
             fontsize=14, fontweight="bold", pad=15)

# Three pillars
pillars = [
    (1.5, 5.5, "① 2-of-3 Vote\n   Majority", "#3498db",
     "GPT-4 / Claude-3 / LLaMA\nBlind · Independent · Append-only"),
    (5.0, 5.5, "② Independent\n   Metering", "#2ecc71",
     "Platform middleware records\ntool_calls / tokens / wall_seconds"),
    (8.5, 5.5, "③ Sidecar\n   Checkers", "#e74c3c",
     "Holtrop Cd-Re monotonicity\nWave Cw(Fn) peak position"),
]

for x, y, title, color, desc in pillars:
    rect = mpatches.FancyBboxPatch((x-1.2, y-0.8), 2.4, 1.6,
                                    boxstyle="round,pad=0.1",
                                    facecolor=color, alpha=0.2, edgecolor=color, linewidth=2)
    ax.add_patch(rect)
    ax.text(x, y+0.3, title, ha="center", va="center", fontweight="bold", fontsize=11, color=color)
    ax.text(x, y-0.4, desc, ha="center", va="center", fontsize=8, color="#333")

# Aggregator
rect_agg = mpatches.FancyBboxPatch((3.5, 2.5), 3.0, 1.2,
                                    boxstyle="round,pad=0.1",
                                    facecolor="#f39c12", alpha=0.2, edgecolor="#f39c12", linewidth=2)
ax.add_patch(rect_agg)
ax.text(5.0, 3.3, "Pure-Function Aggregator", ha="center", va="center",
        fontweight="bold", fontsize=11, color="#f39c12")
ax.text(5.0, 2.8, "aggregate_votes() · reconcile_usage() · run_checker()",
        ha="center", va="center", fontsize=8, color="#333")

# Arrows from pillars to aggregator
for x in [1.5, 5.0, 8.5]:
    ax.annotate("", xy=(5.0, 3.7), xytext=(x, 4.7),
                arrowprops=dict(arrowstyle="->", color="#666", lw=1.5))

# Output
rect_out = mpatches.FancyBboxPatch((3.0, 0.5), 4.0, 1.0,
                                    boxstyle="round,pad=0.1",
                                    facecolor="#9b59b6", alpha=0.2, edgecolor="#9b59b6", linewidth=2)
ax.add_patch(rect_out)
ax.text(5.0, 1.15, "Verdict · Integrity Event · Red Flag", ha="center", va="center",
        fontweight="bold", fontsize=10, color="#9b59b6")
ax.text(5.0, 0.75, "strike | split_event | anomaly | reproduction_queue",
        ha="center", va="center", fontsize=8, color="#333")

ax.annotate("", xy=(5.0, 1.5), xytext=(5.0, 2.5),
            arrowprops=dict(arrowstyle="->", color="#666", lw=1.5))

plt.tight_layout()
fig.savefig(f"{OUT}/fig1_architecture.png", dpi=150, bbox_inches="tight")
plt.close()

# ─── Figure 2: Vote aggregation scenarios ───────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(12, 4))

scenarios = [
    ("Split Verdict\n(1-1-1)", {"cheat": 1, "pass": 1, "partial": 1}, True, "#e74c3c"),
    ("Confirmed Cheat\n(2-1)", {"cheat": 2, "pass": 1}, False, "#27ae60"),
    ("Unanimous Pass\n(3-0)", {"pass": 3}, False, "#3498db"),
]

for ax, (title, tally, is_split, color) in zip(axes, scenarios):
    labels = list(tally.keys())
    values = list(tally.values())
    colors_bar = {"cheat": "#e74c3c", "pass": "#27ae60", "partial": "#f39c12", "fail": "#95a5a6"}
    bar_colors = [colors_bar.get(l, "#95a5a6") for l in labels]

    bars = ax.bar(labels, values, color=bar_colors, alpha=0.8, edgecolor="black", linewidth=0.5)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_ylabel("Votes")
    ax.set_ylim(0, 3.5)
    ax.axhline(y=2, color="red", linestyle="--", alpha=0.5, label="2-of-3 threshold")

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                str(val), ha="center", va="bottom", fontweight="bold")

    status = "SPLIT ⚠" if is_split else ("STRIKE ✓" if tally.get("cheat", 0) >= 2 else "PASS ✓")
    ax.text(0.5, -0.2, status, transform=ax.transAxes, ha="center",
            fontsize=12, fontweight="bold", color=color)
    ax.legend(fontsize=8)

plt.suptitle("2-of-3 Majority Voting — Red Team Evidence #1", fontsize=13, fontweight="bold", y=1.02)
plt.tight_layout()
fig.savefig(f"{OUT}/fig2_vote_scenarios.png", dpi=150, bbox_inches="tight")
plt.close()

# ─── Figure 3: Usage reconciliation deviation ───────────────────────
fig, ax = plt.subplots(figsize=(8, 5))

fields = ["tool_calls", "tokens", "wall_seconds"]
self_reported = [1000, 50000, 3600]
metered = [87, 45000, 3500]

x = np.arange(len(fields))
width = 0.35

bars1 = ax.bar(x - width/2, self_reported, width, label="Self-reported (agent)",
               color="#e74c3c", alpha=0.7)
bars2 = ax.bar(x + width/2, metered, width, label="Metered (platform)",
               color="#2ecc71", alpha=0.7)

ax.set_xlabel("Usage Metric")
ax.set_ylabel("Value")
ax.set_title("Usage Reconciliation — Red Team Evidence #2\nSelf-reported 1000 calls vs Metered 87 calls",
             fontsize=12, fontweight="bold")
ax.set_xticks(x)
ax.set_xticklabels(fields)
ax.legend()
ax.set_yscale("log")

# Add deviation annotations
for i, (sr, met) in enumerate(zip(self_reported, metered)):
    dev = abs(sr - met) / met * 100
    color = "#e74c3c" if dev > 20 else "#27ae60"
    ax.annotate(f"Δ={dev:.0f}%", xy=(i, max(sr, met)),
                xytext=(i, max(sr, met) * 1.5),
                ha="center", fontsize=10, fontweight="bold", color=color,
                arrowprops=dict(arrowstyle="->", color=color))

ax.axhline(y=1, color="gray", linestyle=":", alpha=0.3)
ax.text(0.5, -0.15, "⚠ tool_calls deviation = 1050% >> 20% threshold → INTEGRITY EVENT",
        transform=ax.transAxes, ha="center", fontsize=10, fontweight="bold", color="#e74c3c")

plt.tight_layout()
fig.savefig(f"{OUT}/fig3_reconciliation.png", dpi=150, bbox_inches="tight")
plt.close()

# ─── Figure 4: Sidecar checker — Cd-Re monotonicity ────────────────
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# Left: Physical (pass)
re_phys = np.array([1e6, 2e6, 5e6, 1e7, 2e7, 5e7, 1e8])
cd_phys = np.array([5.0e-3, 4.3e-3, 3.5e-3, 2.8e-3, 2.2e-3, 1.7e-3, 1.4e-3])

axes[0].semilogx(re_phys, cd_phys * 1e3, "bo-", linewidth=2, markersize=6, label="Physical data")
axes[0].set_xlabel("Reynolds Number Re")
axes[0].set_ylabel("Cd × 10³")
axes[0].set_title("✓ PASS: Monotonically Decreasing\n(Physical Turbulent BL)", fontweight="bold", color="#27ae60")
axes[0].legend()
axes[0].grid(True, alpha=0.3)
axes[0].text(0.5, -0.15, "Cd decreases with Re — consistent with ITTC-57",
             transform=axes[0].transAxes, ha="center", fontsize=9, color="#27ae60")

# Right: Non-physical (red flag)
re_bad = np.array([1e6, 2e6, 5e6, 1e7, 2e7, 5e7, 1e8])
cd_bad = np.array([5.0e-3, 4.3e-3, 3.5e-3, 5.5e-3, 2.2e-3, 1.7e-3, 1.4e-3])  # spike at 1e7

axes[1].semilogx(re_bad, cd_bad * 1e3, "ro-", linewidth=2, markersize=6, label="Non-physical data")
axes[1].semilogx(re_phys, cd_phys * 1e3, "b--", alpha=0.3, label="Reference (physical)")
axes[1].set_xlabel("Reynolds Number Re")
axes[1].set_ylabel("Cd × 10³")
axes[1].set_title("⚠ RED FLAG: Non-Monotonic\n(Sidecar Checker Detects)", fontweight="bold", color="#e74c3c")
axes[1].legend()
axes[1].grid(True, alpha=0.3)

# Highlight violation
axes[1].annotate("Cd INCREASES\n(unphysical!)", xy=(1e7, 5.5), xytext=(3e7, 6.0),
                 fontsize=9, fontweight="bold", color="#e74c3c",
                 arrowprops=dict(arrowstyle="->", color="#e74c3c"))
axes[1].text(0.5, -0.15, "⚠ Monotonicity violation → auto-enqueue for reproduction",
             transform=axes[1].transAxes, ha="center", fontsize=9, color="#e74c3c")

plt.suptitle("Sidecar Physical-Property Checker — Red Team Evidence #3\nHoltrop Cd-Re Monotonicity",
             fontsize=12, fontweight="bold", y=1.02)
plt.tight_layout()
fig.savefig(f"{OUT}/fig4_sidecar_cd_re.png", dpi=150, bbox_inches="tight")
plt.close()

# ─── Figure 5: Wave Cw(Fn) peak position ───────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# Left: Correct peak
fn = np.array([0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6])
cw_good = np.array([0.05, 0.12, 0.3, 0.6, 1.0, 1.3, 1.5, 1.6, 1.4, 1.1, 0.8]) * 1e-3

axes[0].plot(fn, cw_good * 1e3, "bo-", linewidth=2, markersize=5)
axes[0].axvspan(0.3, 0.6, alpha=0.1, color="green", label="Expected peak range")
axes[0].axvline(x=0.45, color="green", linestyle="--", alpha=0.5)
axes[0].plot(0.45, 1.6e-3, "g*", markersize=15, label="Peak at Fn=0.45")
axes[0].set_xlabel("Froude Number Fn")
axes[0].set_ylabel("Cw × 10³")
axes[0].set_title("✓ PASS: Peak at Fn≈0.45\n(Within Expected Range)", fontweight="bold", color="#27ae60")
axes[0].legend(fontsize=8)
axes[0].grid(True, alpha=0.3)

# Right: Wrong peak
cw_bad = np.array([0.5, 4.5, 3.0, 2.0, 1.2, 0.8, 0.5, 0.3, 0.2, 0.15, 0.1]) * 1e-3

axes[1].plot(fn, cw_bad * 1e3, "ro-", linewidth=2, markersize=5)
axes[1].axvspan(0.3, 0.6, alpha=0.1, color="green", label="Expected peak range")
axes[1].plot(0.15, 4.5e-3, "r*", markersize=15, label="Peak at Fn=0.15 (WRONG)")
axes[1].set_xlabel("Froude Number Fn")
axes[1].set_ylabel("Cw × 10³")
axes[1].set_title("⚠ RED FLAG: Peak at Fn≈0.15\n(Outside Expected Range)", fontweight="bold", color="#e74c3c")
axes[1].legend(fontsize=8)
axes[1].grid(True, alpha=0.3)
axes[1].annotate("Peak should be\nat Fn≈0.4-0.5!", xy=(0.15, 4.5), xytext=(0.35, 5.0),
                 fontsize=9, fontweight="bold", color="#e74c3c",
                 arrowprops=dict(arrowstyle="->", color="#e74c3c"))

plt.suptitle("Sidecar Wave Resistance Checker\nCw(Fn) Peak Position Domain Check",
             fontsize=12, fontweight="bold", y=1.02)
plt.tight_layout()
fig.savefig(f"{OUT}/fig5_sidecar_wave_cw.png", dpi=150, bbox_inches="tight")
plt.close()

print(f"✓ All 5 figures saved to {OUT}/")
for f in sorted(os.listdir(OUT)):
    print(f"  {f}")
