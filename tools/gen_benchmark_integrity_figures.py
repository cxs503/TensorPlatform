"""生成基准完整性裁定工件与图（数字全部程序化导出，零手抄）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
OUT = REPO / "artifacts"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(BACKEND))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


integ = _load("ship_benchmark_integrity", "services/ship_benchmark_integrity.py")
bench = _load("ship_benchmark", "api/domain/ship_benchmark.py")

audit = integ.load_and_audit()
OUT.joinpath("ship_benchmark_integrity.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")

# --- 精度比对：只统计通过完整性裁定的算例 ---
rows = []
for ship_type in sorted(bench.SHIP_GEOMETRY):
    resp = bench.benchmark_validate(bench.BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all"))
    for r in resp.results:
        rows.append({
            "ship_type": ship_type, "case_id": r.case_id, "speed_ms": r.speed_ms,
            "calc_ct": r.calc_ct, "ref_ct": r.ref_ct, "error_pct": r.error_pct, "pass": bool(r.pass_),
        })
    rows.append({"ship_type": ship_type, "_summary": resp.summary})

per_case = [r for r in rows if "_summary" not in r]
summaries = {r["ship_type"]: r["_summary"] for r in rows if "_summary" in r}
metrics = {
    "generated_by": "tools/gen_benchmark_integrity_figures.py",
    "criterion": "|Ct_calc-Ct_ref|/Ct_ref <= 15%",
    "integrity": {
        "asset_sha256": audit["asset_sha256"],
        "tolerances": audit["tolerances"],
        "reference_fluid": audit["reference_fluid"],
        "ct_admitted": audit["admission"]["ct"],
        "ct_excluded": audit["excluded"]["ct"],
        "summary": audit["summary"],
    },
    "accuracy_per_case": per_case,
    "accuracy_by_hull": {
        k: {kk: vv for kk, vv in v.items() if kk != "integrity"} for k, v in summaries.items()
    },
    "geometry_presets": bench.SHIP_GEOMETRY,
}
# 补充 Fn 自洽性数字
fn_rows = []
for c in audit["cases"]:
    for chk in c["checks"]:
        if chk["name"] == "fn_identity":
            fn_rows.append({"case_id": c["case_id"], "ship_type": c["ship_type"],
                            "fn_claimed": chk["metrics"]["fn_claimed"],
                            "fn_from_geometry": chk["metrics"]["fn_from_geometry"],
                            "rel_dev": chk["metrics"]["rel_dev"]})
metrics["froude_self_consistency"] = fn_rows
OUT.joinpath("ship_benchmark_accuracy.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")

# ---------------- Fig 1: Fn 自洽性 ----------------
fig, ax = plt.subplots(figsize=(7.2, 4.2))
labels = [r["case_id"].replace("DTMB5415", "5415") for r in fn_rows]
x = np.arange(len(fn_rows))
claimed = [r["fn_claimed"] for r in fn_rows]
geom = [r["fn_from_geometry"] for r in fn_rows]
ax.bar(x - 0.2, claimed, 0.4, label="Fn as tabulated", color="#4c72b0")
ax.bar(x + 0.2, geom, 0.4, label="Fn = V/sqrt(gL) from own geometry", color="#dd8452")
ax.set_yscale("log")
ax.set_xticks(x)
ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
ax.set_ylabel("Froude number (log scale)")
ax.set_title("Reference-dataset Froude identity check\n(5415 block: model-scale geometry paired with full-scale speeds)")
ax.legend(fontsize=8)
ax.grid(axis="y", ls=":", alpha=0.5)
fig.tight_layout()
fig.savefig(OUT / "benchmark_froude_identity.png", dpi=150)
plt.close(fig)

# ---------------- Fig 2: Ct 比对 + 误差 ----------------
fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.3))
for ship_type, color in (("SUBOFF", "#4c72b0"), ("KVLCC2", "#55a868")):
    sub = [r for r in per_case if r["ship_type"] == ship_type]
    if not sub:
        continue
    v = [r["speed_ms"] for r in sub]
    axes[0].plot(v, [r["ref_ct"] for r in sub], "o--", color=color, label=f"{ship_type} reference Ct")
    axes[0].plot(v, [r["calc_ct"] for r in sub], "s-", color=color, alpha=0.7, label=f"{ship_type} model Ct")
axes[0].set_xlabel("speed V (m/s)")
axes[0].set_ylabel("total resistance coefficient Ct")
axes[0].set_title("Ct: published reference vs platform model")
axes[0].legend(fontsize=7)
axes[0].grid(ls=":", alpha=0.5)

for ship_type, color in (("SUBOFF", "#4c72b0"), ("KVLCC2", "#55a868")):
    sub = [r for r in per_case if r["ship_type"] == ship_type]
    if not sub:
        continue
    axes[1].bar([f"{ship_type[:3]}-{i+1}" for i in range(len(sub))],
                [r["error_pct"] for r in sub], color=color, label=ship_type)
axes[1].axhline(15.0, color="crimson", ls="--", lw=1.2, label="15% acceptance gate")
axes[1].set_ylabel("|Ct_calc - Ct_ref| / Ct_ref  (%)")
axes[1].set_title("Per-case Ct error (5415 excluded by integrity gate)")
axes[1].legend(fontsize=8)
axes[1].grid(axis="y", ls=":", alpha=0.5)
fig.tight_layout()
fig.savefig(OUT / "benchmark_ct_comparison.png", dpi=150)
plt.close(fig)

print(json.dumps({
    "artifacts": ["artifacts/ship_benchmark_integrity.json", "artifacts/ship_benchmark_accuracy.json",
                  "artifacts/benchmark_froude_identity.png", "artifacts/benchmark_ct_comparison.png"],
    "accuracy_by_hull": metrics["accuracy_by_hull"],
    "excluded": metrics["integrity"]["ct_excluded"],
}, ensure_ascii=False, indent=2))