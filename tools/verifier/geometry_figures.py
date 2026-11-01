"""几何域审计出图: 图1 Cb 四源对比; 图2 各站剖面面积曲线 (型值表独立积分)."""
import base64
import json
import os

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "artifacts", "geometry_audit")
sys.path.insert(0, ROOT)
from tools.verifier.geometry_audit import integrate_table  # noqa: E402

A = json.load(open(os.path.join(OUT, "geometry_audit.json")))
S = json.load(open(os.path.join(OUT, "raw_probe.json")))

# ── 图1: Cb 四源对比 ──
models = [m for m in A["models"]]
xs = range(len(models))
w = 0.2
plt.figure(figsize=(8.2, 4.6))
series = [
    ("declared (/geometry)", [m["declared"]["Cb"] or 0 for m in models], "#7f8c8d"),
    ("literature", [m["literature_Cb"] or 0 for m in models], "#2c3e50"),
    ("served (hydro-at)", [(m.get("api_hydro") or {}).get("Cb") or 0 for m in models], "#c0392b"),
    ("independent (offsets)", [(m.get("independent") or {}).get("Cb") or 0 for m in models], "#27ae60"),
]
for i, (lab, vals, c) in enumerate(series):
    plt.bar([x + (i - 1.5) * w for x in xs], vals, w, label=lab, color=c)
plt.axhline(1.0, ls="--", lw=1.2, color="k")
plt.text(len(models) - 0.5, 1.02, "Cb = 1 physical bound", ha="right", fontsize=8)
plt.xticks(list(xs), [m["model"] for m in models])
plt.ylabel("block coefficient $C_B$ [-]")
plt.title("Geometry-domain audit: $C_B$ from four independent sources")
plt.legend(fontsize=8)
plt.tight_layout()
plt.savefig(os.path.join(OUT, "fig1_Cb_sources.png"), dpi=160)
plt.close()

# ── 图2: 站剖面面积曲线 A(ξ)/(B·T) ──
plt.figure(figsize=(8.2, 4.4))
for m in models:
    tbl = ((S["probes"].get(m["model"], {}).get("offsets") or {}).get("data") or {}).get("table") or []
    if len(tbl) < 3:
        continue
    ind = integrate_table(tbl)
    m["independent"] = {k: ind[k] for k in ("Cb", "Cm", "Cp", "Cw", "xi_bar")}
    plt.plot([i / (len(ind["A_stations"]) - 1) for i in range(len(ind["A_stations"]))],
             ind["A_stations"], "-o", ms=3, label=f"{m['model']} offsets (2-D Simpson)")
xi = [i / 20 for i in range(21)]
plt.plot(xi, [1 - (2 * x - 1) ** 2 for x in xi], "--", color="#2c3e50",
         label="Wigley analytic 1-(2$\\xi$-1)$^2$")
plt.axhline(1.0, ls=":", color="k", lw=1)
plt.xlabel("station $\\xi$ (0 = one end of offsets table, 1 = other)")
plt.ylabel("section area $A(\\xi)/(B\\cdot T)$ [-]")
plt.title("Station-area curves rebuilt from /geometry/offsets (independent integration)")
plt.legend(fontsize=8)
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(OUT, "fig2_station_areas.png"), dpi=160)
plt.close()

for f in ("fig1_Cb_sources.png", "fig2_station_areas.png"):
    p = os.path.join(OUT, f)
    print(f, os.path.getsize(p), "bytes, b64len", len(base64.b64encode(open(p, "rb").read())))