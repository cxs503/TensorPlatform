#!/usr/bin/env python3
"""
Generate the benchmark-audit artifacts (JSON + figures).

Outputs (all inside the repository, deterministic):
  docs/verification/benchmark_audit.json
  docs/verification/figures/fig_benchmark_audit.png

Run:
  cd backend && DATABASE_URL="sqlite:////tmp/tp.db" python3 ../docs/verification/gen_benchmark_audit.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/tp_audit.db")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from api.domain.ship_benchmark import SHIP_GEOMETRY, benchmark_validate, BenchmarkValidateRequest  # noqa: E402
from services import benchmark_audit as audit_mod  # noqa: E402

HERE = Path(__file__).resolve().parent
FIGDIR = HERE / "figures"
FIGDIR.mkdir(exist_ok=True)

DATA = json.loads((BACKEND / "data" / "benchmark_reference.json").read_text())

audit = audit_mod.audit_dataset(DATA, SHIP_GEOMETRY)

validation = {}
for ship_type in ("SUBOFF", "DTMB-5415", "KVLCC2"):
    resp = benchmark_validate(BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all"))
    validation[ship_type] = {
        "summary": resp.summary,
        "results": [r.model_dump(by_alias=True) for r in resp.results],
    }

artifact = {
    "title": "Ship resistance benchmark chain: integrity audit + in-process validation",
    "dataset": str(BACKEND / "data" / "benchmark_reference.json"),
    "audit": audit,
    "validation": validation,
    "notes": [
        "Ct is independent of the wetted-surface estimate (Ct = Cf + Cr); the area only "
        "scales the dimensional resistance.",
        "Excluded rows: stated Froude number disagrees with V/sqrt(gL) by >5%.",
        "verified_passes_in_envelope = passes whose model validity flags are empty.",
    ],
}
out = HERE / "benchmark_audit.json"
out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
print(f"wrote {out}")

# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))

colors = {"SUBOFF": "#1f77b4", "DTMB-5415": "#2ca02c", "KVLCC2": "#d62728"}
ax = axes[0]
for ship_type, block in validation.items():
    rows = block["results"]
    ref = sorted([(c["speed_ms"], c["experimental_ct"]) for c in DATA["cases"]
                  if c["ship_type"] == ship_type])
    if ref:
        ax.plot([r[0] for r in ref], [r[1] for r in ref], "o", color=colors[ship_type],
                label=f"{ship_type} reference Ct", mfc="none", mew=1.5)
    if rows:
        ax.plot([r["speed_ms"] for r in rows], [r["calc_ct"] for r in rows], "-s",
                color=colors[ship_type], ms=4, lw=1.2,
                label=f"{ship_type} platform Ct")
    for r in rows:  # ±15% acceptance band around each evaluated reference point
        ax.plot([r["speed_ms"], r["speed_ms"]], [r["ref_ct"] * 0.85, r["ref_ct"] * 1.15],
                color=colors[ship_type], alpha=0.25, lw=6)
ax.set_xlabel("speed [m/s]")
ax.set_ylabel(r"$C_T$ [-]")
ax.set_title("Reference vs platform total-resistance coefficient\n(shaded: ±15% acceptance band)")
ax.grid(alpha=0.3)
ax.legend(fontsize=7, loc="best")

ax = axes[1]
rows = audit["rows"]
labels = [r["case_id"].replace("DTMB5415", "5415") for r in rows]
ct_rt = [r["ct_rt_error_pct"] for r in rows]
fn_err = [min(r["froude_error_pct"] if r["froude_error_pct"] is not None else 0.0, 1e4) for r in rows]
x = range(len(rows))
ax.bar([i - 0.2 for i in x], ct_rt, width=0.4, label="|Rt(stated) - Ct·½ρSV²| / Rt [%]", color="#ff7f0e")
ax.bar([i + 0.2 for i in x], fn_err, width=0.4, label="|Fn(stated) - V/√(gL)| / Fn [%]", color="#9467bd")
ax.set_yscale("log")
ax.axhline(15.0, color="k", ls="--", lw=1)
ax.set_xticks(list(x))
ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
ax.set_ylabel("relative inconsistency [%]  (log)")
ax.set_title("Reference-dataset integrity audit\nall 12 rows exceed the 15% consistency tolerance")
ax.grid(alpha=0.3, axis="y")
ax.legend(fontsize=7)

fig.tight_layout()
figpath = FIGDIR / "fig_benchmark_audit.png"
fig.savefig(figpath, dpi=150)
print(f"wrote {figpath}")

print(json.dumps({k: v["summary"] for k, v in validation.items()}, indent=2)[:1800])