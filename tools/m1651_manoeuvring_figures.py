#!/usr/bin/env python3
"""Figures for the manoeuvring verification report (m1651).

Reads nothing from the network: recomputes the same cases as
``tools/benchmarks/manoeuvring_anchors.py`` and writes PNGs into
``artifacts/m1651/``.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.digital_twin.models.geometry import HullForm  # noqa: E402
from services.digital_twin.models.manoeuvring import _MMGCore, rk4_step, turning_circle  # noqa: E402

OUT = ROOT / "artifacts" / "m1651"
OUT.mkdir(parents=True, exist_ok=True)
U0 = 15.5 * 1852.0 / 3600.0
HULL = HullForm(L=320.0, B=58.0, T=20.8, Cb=0.8098)
L = HULL.L


def zigzag_history(rudder_deg: float, dt: float = 0.1, t_end: float = 300.0):
    core = _MMGCore(HULL, U0)
    d = math.radians(rudder_deg)
    state = np.array([0.0, 0.0, 0.0, U0, 0.0, 0.0])
    ts, psis, ds = [], [], []
    t = 0.0
    stage = 0
    while t < t_end and stage < 2:
        state = rk4_step(core, state, d, dt)
        t += dt
        psi = math.degrees(state[2])
        if stage == 0 and psi >= rudder_deg:
            stage, d = 1, -d
        ts.append(t); psis.append(psi); ds.append(math.degrees(d))
    return np.array(ts), np.array(psis), np.array(ds)


def main() -> int:
    anchors = json.loads((OUT / "manoeuvring_anchors.json").read_text())

    fig, axes = plt.subplots(2, 2, figsize=(13.0, 9.6))

    # ---- (a) turning tracks ------------------------------------------------
    ax = axes[0][0]
    for delta, col in ((5.0, "#1f77b4"), (10.0, "#2ca02c"), (20.0, "#ff7f0e"), (35.0, "#d62728")):
        r = turning_circle(HULL, U0, rudder_deg=delta, t_end=1800.0, dt=0.2)
        ax.plot(np.array(r["traj_x"]) / L, np.array(r["traj_y"]) / L, col, lw=1.4,
                label=f"$\\delta$={delta:.0f}°, D={r['turning_diameter_L']:.2f}L")
    rp = turning_circle(HULL, U0, rudder_deg=-35.0, t_end=900.0, dt=0.2)
    ax.plot(np.array(rp["traj_x"]) / L, np.array(rp["traj_y"]) / L, "k--", lw=1.0,
            label="35° port (mirror, M-A2)")
    ax.axhline(0.0, color="grey", lw=0.6)
    ax.set_xlabel("x / L"); ax.set_ylabel("y / L")
    ax.set_title("(a) Turning tracks, KVLCC2 (L=320 m, $C_b$=0.810), $U_0$=15.5 kn")
    ax.legend(fontsize=7, loc="upper left"); ax.grid(alpha=0.3); ax.set_aspect("equal")

    # ---- (b) diameter vs rudder + cross-model ------------------------------
    ax = axes[0][1]
    deltas = [5.0, 10.0, 20.0, 35.0]
    ds_meas = []
    for delta in deltas:
        r = turning_circle(HULL, U0, rudder_deg=delta, t_end=1800.0, dt=0.2)
        ds_meas.append(r["turning_diameter_L"])
    ax.plot(deltas, ds_meas, "o-", color="#d62728", label="digital-twin MMG (m1651 fix)")
    xt = anchors["turning_metrics"]["35deg_starboard"]
    ax.plot([35.0], [xt["turning_diameter_surge_m"] / L], "s", mfc="none", color="#d62728",
            label="pre-m1651 metric $2u/r$ (biased)")
    a11 = next(a for a in anchors["anchors"] if a["id"] == "M-A11")["value"]
    ax.plot([35.0], [a11["steady_D_L_w3n08"]], "^", color="#1f77b4",
            label="W3-N08 MMG (code-to-code)")
    ax.axhline(5.0, color="k", ls=":", lw=1.0)
    ax.text(6, 5.08, "IMO A.751(18) limit 5L", fontsize=7)
    ax.set_xlabel("rudder angle [deg]"); ax.set_ylabel("steady turning diameter / L")
    ax.set_title("(b) Diameter vs rudder: monotone, converged, IMO-compliant")
    ax.legend(fontsize=7); ax.grid(alpha=0.3)

    # ---- (c) zigzag heading histories -------------------------------------
    ax = axes[1][0]
    for rud, col in ((10.0, "#2ca02c"), (20.0, "#9467bd")):
        t, psi, rud_cmd = zigzag_history(rud)
        ax.plot(t, psi, col, lw=1.3, label=f"{rud:.0f}°/{rud:.0f}° zigzag")
        ax.plot(t, rud_cmd, col, lw=0.6, ls=":")
    ax.axhline(0, color="grey", lw=0.6)
    ax.set_xlabel("time [s]"); ax.set_ylabel("heading / rudder angle [deg]")
    ax.set_title("(c) Zigzag heading response (dotted = rudder command)")
    ax.legend(fontsize=7); ax.grid(alpha=0.3)

    # ---- (d) anchor verdict board ----------------------------------------
    ax = axes[1][1]
    ax.axis("off")
    rows = []
    for a in anchors["anchors"]:
        v = a["value"] if isinstance(a["value"], dict) else {"v": a["value"]}
        key = next((v[k] for k in v if "rel" in k and isinstance(v[k], float)), None)
        rows.append([a["id"], a["type"].split(" (")[0], a["verdict"],
                     "-" if key is None else f"{key:.2e}", a["name"][:38]])
    tbl = ax.table(cellText=rows,
                   colLabels=["id", "type", "verdict", "rel.err", "criterion"],
                   loc="center", cellLoc="left")
    tbl.auto_set_font_size(False); tbl.set_fontsize(6.6); tbl.scale(1.0, 1.22)
    for (i, j), cell in tbl.get_celld().items():
        if i == 0:
            cell.set_facecolor("#e8e8e8")
        elif j == 2:
            txt = rows[i - 1][2]
            cell.set_facecolor({"PASS": "#d9f2d9", "WITHHELD": "#fff2cc",
                                "FAIL": "#f8d0d0", "DEFECT": "#f8d0d0"}.get(txt, "white"))
    ax.set_title("(d) Anchor verdicts (evidence: manoeuvring_anchors.json)", fontsize=10)

    fig.suptitle("Manoeuvring verification debt audit — m1651 "
                 "(digital-twin MMG, KVLCC2, 35° IMO trial)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    p1 = OUT / "fig_manoeuvring_anchors.png"
    fig.savefig(p1, dpi=135)
    plt.close(fig)
    print("wrote", p1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())