#!/usr/bin/env python3
"""Offline benchmark integrity audit — writes artifacts/benchmark_integrity_audit.json.

用法（在工作树根目录）：
    python3 scripts/audit_benchmark.py

零网络、零数据库、纯 CPU（<1 s），因此可在高负载机器上安全运行。
所有入 JSON 的数字都由 services.benchmark_integrity 现算，脚本内不出现任何手抄常数。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from services.benchmark_integrity import audit_dataset, platform_vs_reference  # noqa: E402

DATA_PATH = ROOT / "backend" / "data" / "benchmark_reference.json"
OUT_PATH = ROOT / "artifacts" / "benchmark_integrity_audit.json"
FIG_DIR = ROOT / "artifacts" / "figs"


def build_audit() -> dict:
    data = json.loads(DATA_PATH.read_text())
    audit = audit_dataset(data)
    accuracy = platform_vs_reference(data)
    return {
        "generated_by": "scripts/audit_benchmark.py",
        "dataset_path": str(DATA_PATH.relative_to(ROOT)),
        "dataset_description": data.get("description", ""),
        "integrity": audit,
        "accuracy_vs_reference": accuracy,
    }


def make_figures(result: dict) -> list:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    integrity = result["integrity"]
    cases = integrity["cases"]

    # --- Fig 1: declared Fn vs geometry-derived Fn (log-log, 1:1) ---
    rows = [c for c in cases if c["checks"]["C1_froude_consistency"]["status"] != "UNAVAILABLE"]
    fig, ax = plt.subplots(figsize=(5.6, 5.0))
    markers = {"SUBOFF": "o", "DTMB-5415": "s", "KVLCC2": "^"}
    for ship_type in sorted({r["ship_type"] for r in rows}):
        pts = [r for r in rows if r["ship_type"] == ship_type]
        xs = [p["checks"]["C1_froude_consistency"]["fn_derived"] for p in pts]
        ys = [p["checks"]["C1_froude_consistency"]["fn_declared"] for p in pts]
        ax.loglog(xs, ys, marker=markers.get(ship_type, "o"), ls="none", label=ship_type, ms=7)
    lims = [0.04, 3.0]
    ax.plot(lims, lims, "k--", lw=1, label="Fn_declared = Fn(V/√gL)")
    ax.set_xlabel("Fn from V/√(g·L)   (geometry-consistent)")
    ax.set_ylabel("Fn declared in benchmark_reference.json")
    ax.set_title("C1: declared Froude number vs geometry-derived V/sqrt(gL)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    p1 = FIG_DIR / "benchmark_fn_consistency.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    paths.append(p1)

    # --- Fig 2: C1/C2 relative violations per case (log) ---
    labelled = [(c["case_id"], c["checks"]["C1_froude_consistency"].get("rel_err"),
                 c["checks"]["C2_cb_closure"].get("rel_err")) for c in cases]
    fig, ax = plt.subplots(figsize=(8.0, 3.9))
    idx = range(len(labelled))
    fn_err = [max(v[1] if v[1] else 1e-12, 1e-12) * 100 for v in labelled]
    cb_err = [max(v[2] if v[2] else 1e-12, 1e-12) * 100 for v in labelled]
    ax.bar([i - 0.2 for i in idx], fn_err, width=0.4, label="C1 |Fn_decl−Fn(V/√gL)|/Fn", color="#2980b9")
    ax.bar([i + 0.2 for i in idx], cb_err, width=0.4, label="C2 |Cb_decl−∇/(LBT)|/Cb", color="#c0392b")
    ax.axhline(0.1, color="#2980b9", ls=":", lw=1)
    ax.axhline(1.0, color="#c0392b", ls=":", lw=1)
    ax.set_yscale("log")
    ax.set_xticks(list(idx))
    ax.set_xticklabels([v[0] for v in labelled], rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("relative violation  [%]  (log)")
    ax.set_title("C1/C2 geometry closure residuals (thresholds: Fn 0.1%, Cb 1%)")
    ax.legend(fontsize=7)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    p2 = FIG_DIR / "benchmark_geometry_closure.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    paths.append(p2)

    # --- Fig 3: two panels — Ct error vs ref; Ct-Rt identity collapse ---
    acc = [r for r in result["accuracy_vs_reference"]["rows"] if r.get("points")]
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.2))

    ax = axes[0]
    ids = [r["case_id"] for r in acc]
    best = [r["err_pct_ct_min"] for r in acc]
    worst = [r["err_pct_ct_max"] for r in acc]
    errs = [max(e, 1e-2) for e in best]
    ax.bar(range(len(ids)), errs, color="#8e44ad", label="|Ct_calc−Ct_ref|/Ct_ref (best case)")
    if any(w > b for w, b in zip(worst, best)):
        ax.plot(range(len(ids)), [max(w, 1e-2) for w in worst], "k_", ms=14, label="worst case over Cb band")
    ax.axhline(3, color="#27ae60", ls=":", lw=1.2, label="3% target")
    ax.axhline(15, color="#c0392b", ls="--", lw=1.2, label="15% limit")
    ax.set_yscale("log")
    ax.set_xticks(range(len(ids)))
    ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("Ct error  [%]")
    ax.set_title("Endpoint Ct vs dataset experimental_ct")
    ax.legend(fontsize=7)
    ax.grid(True, axis="y", alpha=0.3)

    ax = axes[1]
    for c in cases:
        c4 = c["checks"]["C4_ct_rt_identity"]
        if c4["status"] == "UNAVAILABLE":
            continue
        ax.loglog(c4["rt_implied_by_ct_n"], c4["rt_declared_n"], "o", ms=7, label=c["case_id"])
    lims = [1.0, 200.0]
    ax.plot(lims, lims, "k--", lw=1, label="identity holds: Rt = Ct*0.5*rho*S*V^2")
    ax.set_xlabel("Rt implied by experimental_ct  [N]")
    ax.set_ylabel("experimental_rt_n  [N]")
    ax.set_title("C4: reference Ct and Rt contradict each other (all points below 1:1)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=6)
    fig.tight_layout()
    p3 = FIG_DIR / "benchmark_ct_rt_identity.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    paths.append(p3)
    return paths


def main() -> int:
    result = build_audit()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig_paths = make_figures(result)
    result["figures"] = [str(p.relative_to(ROOT)) for p in fig_paths]
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(json.dumps({
        "out": str(OUT_PATH.relative_to(ROOT)),
        "dataset_evidence": result["integrity"]["dataset_evidence"],
        "grade_counts": result["integrity"]["grade_counts"],
        "check_stats": result["integrity"]["check_stats"],
        "accuracy_summary": result["accuracy_vs_reference"]["summary"],
        "figures": result["figures"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())