"""Generate verification figures for Wigley hull hydrostatics validation."""
import math
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from services.ship_hydrostatics import wigley_offsets, compute_hydrostatics

L, B, T = 6.0, 0.6, 0.375
RHO = 1025.0

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "wigley_validation")
os.makedirs(OUTDIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Fig 1: Body plan
# ---------------------------------------------------------------------------
offsets = wigley_offsets(L, B, T, 41, 21)
fig, ax = plt.subplots(figsize=(8, 4))
for i, xs in enumerate(offsets.stations):
    y = [offsets.half_breadths[i][j] for j in range(len(offsets.waterlines))]
    z = offsets.waterlines
    ax.plot(y, z, 'b-', linewidth=0.8, alpha=0.6)
    ax.plot(-np.array(y), z, 'b-', linewidth=0.8, alpha=0.6)
ax.axhline(T, color='r', linestyle='--', linewidth=1.5, label='Design WL')
ax.set_xlabel('Half-breadth y (m)')
ax.set_ylabel('Height z (m)')
ax.set_title('Wigley Hull Body Plan')
ax.legend()
ax.set_aspect('equal')
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(OUTDIR, "body_plan.png"), dpi=150)
plt.close()

# ---------------------------------------------------------------------------
# Fig 2: Section area curve
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(8, 4))
xs = offsets.stations
areas = []
for i, xs_val in enumerate(xs):
    u = 2.0 * (xs_val - 0.5 * L) / L
    along = max(0.0, 1.0 - u * u)
    # Section area at station: integral of 2*y dz from 0 to T
    # y = B/2 * along * (2z/T - z^2/T^2)
    # A = B * along * T * (1 - 1/3) = 2/3 * B * T * along
    A = 2.0/3.0 * B * T * along
    areas.append(A)
ax.plot(xs, areas, 'b-', linewidth=2, label='Analytical')
ax.set_xlabel('x from AP (m)')
ax.set_ylabel('Section Area (m²)')
ax.set_title('Wigley Hull Section Area Curve')
ax.legend()
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(OUTDIR, "section_area.png"), dpi=150)
plt.close()

# ---------------------------------------------------------------------------
# Fig 3: Grid convergence
# ---------------------------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
grids = [(21, 11), (41, 21), (81, 41), (161, 81)]
vol_errors = []
kb_errors = []
vol_analytical = 4.0/9.0 * L * B * T
kb_analytical = 5.0/8.0 * T
for ns, nw in grids:
    h = compute_hydrostatics(wigley_offsets(L, B, T, ns, nw), rho=RHO)
    vol_errors.append(abs(h.volume_m3 - vol_analytical) / vol_analytical * 100)
    kb_errors.append(abs(h.kb_m - kb_analytical) / kb_analytical * 100)

n_pts = [ns * nw for ns, nw in grids]
ax1.semilogy(n_pts, vol_errors, 'ro-', linewidth=2, markersize=8)
ax1.set_xlabel('Number of grid points')
ax1.set_ylabel('Volume error (%)')
ax1.set_title('Volume Grid Convergence')
ax1.grid(True, alpha=0.3)

ax2.semilogy(n_pts, kb_errors, 'bo-', linewidth=2, markersize=8)
ax2.set_xlabel('Number of grid points')
ax2.set_ylabel('KB error (%)')
ax2.set_title('KB Grid Convergence')
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUTDIR, "convergence.png"), dpi=150)
plt.close()

# ---------------------------------------------------------------------------
# Fig 4: Hydrostatics comparison bar chart
# ---------------------------------------------------------------------------
h = compute_hydrostatics(wigley_offsets(L, B, T, 81, 41), rho=RHO)
bm_t_analytical = 3.0 * B**2 / (35.0 * T)

quantities = ['Volume', 'Cb', 'Cw', 'KB', 'BM_T']
calc_vals = [h.volume_m3, h.cb, h.cw, h.kb_m, h.bm_t_m]
analytical_vals = [vol_analytical, 4.0/9.0, 2.0/3.0, kb_analytical, bm_t_analytical]
errors_pct = [abs(c - a) / a * 100 for c, a in zip(calc_vals, analytical_vals)]

fig, ax = plt.subplots(figsize=(8, 4))
colors = ['green' if e < 0.01 else 'orange' if e < 0.1 else 'red' for e in errors_pct]
bars = ax.bar(quantities, errors_pct, color=colors, edgecolor='black', linewidth=0.5)
ax.axhline(3.0, color='r', linestyle='--', linewidth=1.5, label='3% threshold')
ax.set_ylabel('Relative error (%)')
ax.set_title('Wigley Hydrostatics: Platform vs Analytical')
ax.set_yscale('log')
ax.legend()
for bar, err in zip(bars, errors_pct):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() * 1.5,
            f'{err:.4f}%', ha='center', va='bottom', fontsize=8)
ax.grid(True, alpha=0.3, axis='y')
plt.tight_layout()
plt.savefig(os.path.join(OUTDIR, "hydrostatics_bar.png"), dpi=150)
plt.close()

print(f"Figures saved to {OUTDIR}")
