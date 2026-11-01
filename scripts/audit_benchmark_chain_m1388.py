#!/usr/bin/env python3
"""
Mission m1388 — benchmark chain audit artefact generator.

Runs the reference-dataset audit and the live (in-process) benchmark chain, then
writes machine-readable results plus figures:

    artifacts/m1388/benchmark_audit.json     per-case identity checks + dataset verdict
    artifacts/m1388/chain_results.json       live chain output per ship type
    artifacts/m1388/table.md                 markdown tables (program-generated, no hand typing)
    artifacts/m1388/fig_data_quality.png     case x check matrix
    artifacts/m1388/fig_ct_vs_fr.png         reference Ct vs platform model vs ITTC-57 Cf
    artifacts/m1388/fig_suboff_identity.png  SUBOFF: declared Ct vs Ct implied by Rt column

Usage:
    cd backend && ENVIRONMENT=test DATABASE_URL=sqlite:///:memory: \
        python3 ../scripts/audit_benchmark_chain_m1388.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BACKEND = Path(__file__).resolve().parent.parent / "backend"
OUT = Path(__file__).resolve().parent.parent / "artifacts" / "m1388"
sys.path.insert(0, str(BACKEND))
OUT.mkdir(parents=True, exist_ok=True)

from api.domain.ship_benchmark import (  # noqa: E402
    BenchmarkValidateRequest, SHIP_GEOMETRY, _load_benchmark_data, audit_benchmark_cases,
    benchmark_validate,
)
from services.ship_resistance import (  # noqa: E402
    DEFAULT_KINEMATIC_VISCOSITY, DEFAULT_WATER_DENSITY, ct_from_rt, froude_number,
    ittc57_cf, resistance_breakdown, schoenherr_cf,
)

CHECK_LABELS = ["I1 Ct-Rt", "I2 Froude", "I3 Cf floor"]


def main() -> int:
    data = _load_benchmark_data()
    audit = audit_benchmark_cases()
    (OUT / "benchmark_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False))

    # ---- live chain -------------------------------------------------------
    chain = {}
    for ship_type in ("SUBOFF", "DTMB-5415", "KVLCC2"):
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all"))
        chain[ship_type] = {
            "summary": resp.summary,
            "results": [r.model_dump(by_alias=True) for r in resp.results],
        }
    (OUT / "chain_results.json").write_text(json.dumps(chain, indent=2, ensure_ascii=False))

    # ---- friction-line cross-check ---------------------------------------
    friction = []
    for re in (1e6, 1e7, 1e8, 1e9):
        cf57, cfsch = ittc57_cf(re), schoenherr_cf(re)
        friction.append({"re": re, "ittc57": cf57, "schoenherr": cfsch,
                         "rel_diff_pct": abs(cf57 - cfsch) / cfsch * 100.0})
    (OUT / "friction_crosscheck.json").write_text(json.dumps(friction, indent=2))

    # ---- table.md ---------------------------------------------------------
    lines = ["## 表1 参考数据集一致性审计（程序生成）", "",
             "| case | ship | V (m/s) | Ct_ref | 状态 | 失败/未跑检查 |",
             "|---|---|---|---|---|---|"]
    for row in audit["cases"]:
        bad = ",".join(row["failed_checks"] + row["checks_not_run"]) or "-"
        lines.append(f"| {row['case_id']} | {row['ship_type']} | {row['speed_ms']} | "
                     f"{row['experimental_ct']} | {row['status']} | {bad} |")
    lines += ["", "## 表2 实链路对标结果（进程内物理，无 mock）", "",
              "| ship | cases | verified | calc Ct (min–max) | ref Ct (min–max) | 误差幅度 | pass |",
              "|---|---|---|---|---|---|---|"]
    for ship_type, blk in chain.items():
        rs = blk["results"]
        calc = [r["calc_ct"] for r in rs]
        ref = [r["ref_ct"] for r in rs]
        errs = [r["error_pct"] for r in rs]
        lines.append(f"| {ship_type} | {len(rs)} | {blk['summary']['verified_cases']} | "
                     f"{min(calc):.5f}–{max(calc):.5f} | {min(ref):.5f}–{max(ref):.5f} | "
                     f"{min(errs):.1f}%–{max(errs):.1f}% | {blk['summary']['passed']} |")
    lines += ["", "## 表3 摩擦线交叉校核（ITTC-1957 vs Schoenherr）", "",
              "| Re | Cf ITTC-57 | Cf Schoenherr | 相对差 |", "|---|---|---|---|"]
    for f in friction:
        lines.append(f"| {f['re']:.0e} | {f['ittc57']:.6e} | {f['schoenherr']:.6e} | {f['rel_diff_pct']:.2f}% |")
    (OUT / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- figure 1: data-quality matrix ------------------------------------
    cases = audit["cases"]
    mat = np.full((len(cases), 3), np.nan)
    for i, row in enumerate(cases):
        for chk in row["checks"]:
            j = {"I1_ct_rt_identity": 0, "I2_froude_identity": 1, "I3_friction_floor": 2}[chk["id"]]
            mat[i, j] = 1.0 if chk.get("passed") is True else (0.0 if chk.get("passed") is False else 0.5)
    fig, ax = plt.subplots(figsize=(6.2, 5.0))
    ax.imshow(mat, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(3), CHECK_LABELS)
    ax.set_yticks(range(len(cases)), [f"{c['case_id']} ({c['status'][:4]})" for c in cases], fontsize=8)
    for i in range(len(cases)):
        for j in range(3):
            lab = {1.0: "PASS", 0.0: "FAIL", 0.5: "n/a"}[mat[i, j]]
            ax.text(j, i, lab, ha="center", va="center", fontsize=7)
    ax.set_title("m1388 参考数据集一致性矩阵\n(green=通过, red=失败, 黄=不可检验)")
    fig.tight_layout()
    fig.savefig(OUT / "fig_data_quality.png", dpi=140)
    plt.close(fig)

    # ---- figure 2: Ct vs Fr ----------------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    for ax, ship_type in zip(axes, ("SUBOFF", "DTMB-5415", "KVLCC2")):
        geo = SHIP_GEOMETRY[ship_type]
        rows = [c for c in data["cases"] if c["ship_type"] == ship_type]
        vmax = max(c["speed_ms"] for c in rows) * 1.25
        speeds = np.linspace(max(0.2, vmax * 0.05), vmax, 80)
        ct_model = [resistance_breakdown(geo["length"], geo["beam"], geo["draft"], geo["cb"], v)["ct"] for v in speeds]
        cf_model = [ittc57_cf(v * geo["length"] / DEFAULT_KINEMATIC_VISCOSITY) for v in speeds]
        fr_axis = [froude_number(v, geo["length"]) for v in speeds]
        ax.plot(fr_axis, ct_model, "-", lw=2, label="平台模型 Ct (ITTC-57 + 经验 Cr)")
        ax.plot(fr_axis, cf_model, "--", lw=1.4, label="ITTC-1957 Cf（摩擦下限）")
        ax.plot([c["froude_number"] for c in rows], [c["experimental_ct"] for c in rows],
                "ks", ms=7, label="参考 Ct（数据集）")
        ax.set_title(f"{ship_type} (L={geo['length']} m)")
        ax.set_xlabel("Fr")
        ax.set_ylabel("Ct")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.suptitle("m1388 平台阻力模型 vs 参考数据集 Ct（参考点已审计：全部 WITHHELD）", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "fig_ct_vs_fr.png", dpi=140)
    plt.close(fig)

    # ---- figure 3: SUBOFF Ct-Rt identity ---------------------------------
    sub = [c for c in data["cases"] if c["ship_type"] == "SUBOFF"]
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    v = [c["speed_ms"] for c in sub]
    declared = [c["experimental_ct"] for c in sub]
    implied = [ct_from_rt(c["experimental_rt_n"], DEFAULT_WATER_DENSITY,
                          c["geometry"]["wetted_surface_m2"], c["speed_ms"]) for c in sub]
    ax.plot(v, declared, "o-", label="数据集 Ct 列")
    ax.plot(v, implied, "s--", label="由 Rt 列反算的 Ct（同一行）")
    for c, d, i in zip(sub, declared, implied):
        ax.annotate(f"×{d/i:.1f}", (c["speed_ms"], d), textcoords="offset points",
                    xytext=(6, 4), fontsize=8)
    ax.set_yscale("log")
    ax.set_xlabel("V (m/s)")
    ax.set_ylabel("Ct")
    ax.set_title("SUBOFF：Ct 列与 Rt 列自相矛盾（同一行差 7–9.5 倍）")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_suboff_identity.png", dpi=140)
    plt.close(fig)

    # ---- stdout summary ---------------------------------------------------
    print(json.dumps({
        "audit_status_counts": audit["status_counts"],
        "verified_cases": audit["verified_cases"],
        "chain": {k: {"verified_cases": v["summary"]["verified_cases"],
                      "passed": v["summary"]["passed"],
                      "transport": v["summary"]["transport"],
                      "err_pct": [r["error_pct"] for r in v["results"]]}
                  for k, v in chain.items()},
        "friction_max_diff_pct": max(f["rel_diff_pct"] for f in friction),
        "artifacts": sorted(p.name for p in OUT.iterdir()),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())