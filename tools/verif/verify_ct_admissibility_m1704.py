#!/usr/bin/env python3
"""m1704 —— Ct 级判据门验证脚本（数字零手抄，全部从工件读）。

做三件事：
1. 用正典参考数据集 ``backend/data/benchmark_reference.json`` v3.0 的 12 条
   文献/试验算例，经 ``benchmark_validate`` 跑出模型无量纲 Ct 对标表；
2. 记录每行的证据等级（CT+RT / CT / NONE）、15% 判据判决与缺口分解
   （Ct_ref - Ct_calc，即模型缺失的粘性形状 + 波形阻力份额）；
3. 输出 ``artifacts/m1704/ct_admissibility.json``（报告数字唯一来源）与
   两张图：Ct–Fn 对标曲线、逐算例相对误差。

用法::

    python3 tools/verif/verify_ct_admissibility_m1704.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OUT = REPO / "artifacts" / "m1704"
sys.path.insert(0, str(BACKEND))
# 纯模块导入：避开 psycopg2 依赖（本脚本只用物理核与数据集审计，不碰数据库）
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from api.domain.ship_benchmark import (  # noqa: E402
    BenchmarkValidateRequest,
    MODEL_NAME,
    _load_benchmark_data,
    benchmark_validate,
)
from services.benchmark_dataset_audit import case_status_map, load_and_assess  # noqa: E402
from services.ship_resistance import (  # noqa: E402
    DEFAULT_KINEMATIC_VISCOSITY,
    ittc57_cf,
    reynolds_number,
)

SHIP_TYPES = ("SUBOFF", "DTMB-5415", "KVLCC2")
THRESHOLD = 15.0


def collect() -> dict:
    data = _load_benchmark_data()
    rows = case_status_map(load_and_assess(data))
    cases = []
    summaries = {}
    for st in SHIP_TYPES:
        resp = benchmark_validate(BenchmarkValidateRequest(ship_type=st, benchmark_id="all"))
        d = resp.model_dump(by_alias=True)
        summaries[st] = d["summary"]
        for r in d["results"]:
            row = rows[r["case_id"]]
            geo = data["cases"][[c["case_id"] for c in data["cases"]].index(r["case_id"])]
            geo = geo.get("geometry") or {}
            length = geo.get("length_m") or 0.0
            rho = row.get("reference_density_kg_m3") or 998.0
            re = reynolds_number(r["speed_ms"], length, DEFAULT_KINEMATIC_VISCOSITY) if length else 0.0
            cf = ittc57_cf(re) if re > 0 else 0.0
            cases.append({
                "case_id": r["case_id"],
                "ship_type": st,
                "speed_ms": r["speed_ms"],
                "froude": geo and round(r["speed_ms"] / (9.81 * length) ** 0.5, 4) if length else None,
                "reynolds": round(re, 1),
                "cf_ittc57": round(cf, 6),
                "calc_ct": r["calc_ct"],
                "ref_ct": r["ref_ct"],
                "error_pct": r["error_pct"],
                "ct_evidence_level": r["ct_evidence_level"],
                "ct_admissible": r["ct_admissible"],
                "ct_level_pass": r["ct_level_pass"],
                "rt_dataset_status": row["status"],
                "rt_withheld": row["withheld"],
                # 缺口 = 参考 Ct 与模型 Ct 之差：模型没有形状因子 (1+k) 与波形项
                "ct_deficit": round(r["ref_ct"] - r["calc_ct"], 6),
                # 参考行隐含的总阻力占比 Ct/Cf（含形状+波形）
                "ref_ct_over_cf": round(r["ref_ct"] / cf, 3) if cf else None,
                "model_ct_over_cf": round(r["calc_ct"] / cf, 3) if cf else None,
                "rt_claimable": r["ct_evidence_level"] == "CT+RT",
            })
    overall = {
        "cases": len(cases),
        "ct_admissible": sum(1 for c in cases if c["ct_admissible"]),
        "ct_pass": sum(1 for c in cases if c["ct_level_pass"]),
        "rt_claimable": sum(1 for c in cases if c["rt_claimable"]),
        "rt_dataset_consistent": sum(1 for c in cases if not c["rt_withheld"]),
    }
    overall["ct_pass_rate_pct"] = round(100.0 * overall["ct_pass"] / overall["ct_admissible"], 2) \
        if overall["ct_admissible"] else None
    return {
        "mission": 1704,
        "model": MODEL_NAME,
        "threshold_pct": THRESHOLD,
        "dataset": data.get("description", "")[:200],
        "dataset_version": data.get("version"),
        "overall": overall,
        "per_ship_summary": summaries,
        "cases": cases,
    }


def figure(result: dict) -> Path:
    cases = result["cases"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.0, 5.2))
    style = {
        "SUBOFF": dict(marker="o", color="#1f77b4"),
        "DTMB-5415": dict(marker="s", color="#d62728"),
        "KVLCC2": dict(marker="^", color="#2ca02c"),
    }

    # (a) Ct vs Fn：参考点 + 模型点（同 Fn 连线，垂直间距即误差）
    for st, s in style.items():
        sub = [c for c in cases if c["ship_type"] == st]
        fn = [c["froude"] for c in sub]
        ax1.plot(fn, [c["ref_ct"] for c in sub], linestyle="none", label=f"{st} reference Ct", **s)
        ax1.plot(fn, [c["calc_ct"] for c in sub], linestyle="--", marker=s["marker"],
                 color=s["color"], alpha=0.55, label=f"{st} model Ct")
        for c in sub:
            ax1.annotate(f"{c['error_pct']:.1f}%", (c["froude"], c["ref_ct"]),
                         textcoords="offset points", xytext=(4, 6), fontsize=7,
                         color=s["color"])
    ax1.set_yscale("log")
    ax1.set_xlabel("Froude number Fn = V/sqrt(gL)")
    ax1.set_ylabel("Total resistance coefficient Ct")
    ax1.set_title("(a) Ct vs Fn — 12 literature cases, label = |error| (log Ct axis)")
    ax1.grid(alpha=0.3, which="both")
    ax1.legend(fontsize=7, ncol=2)

    # (b) 逐算例相对误差 + 15% 判据线
    labels = [f"{c['case_id']}" for c in cases]
    errs = [c["error_pct"] for c in cases]
    colors = [style[c["ship_type"]]["color"] for c in cases]
    bars = ax2.bar(range(len(cases)), errs, color=colors, alpha=0.85)
    ax2.axhline(THRESHOLD, color="black", linestyle="--", linewidth=1.2,
                label=f"{THRESHOLD:.0f}% acceptance")
    for b, c in zip(bars, cases):
        ax2.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.8,
                 f"{c['error_pct']:.1f}", ha="center", fontsize=7)
    ax2.set_xticks(range(len(cases)))
    ax2.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
    ax2.set_ylabel("|Ct_calc - Ct_ref| / Ct_ref  [%]")
    ax2.set_title("(b) Per-case Ct error (bar colour = hull type)")
    ax2.grid(alpha=0.3, axis="y")
    ax2.legend(fontsize=8)

    fig.suptitle("m1704 · Ct-level admissibility gate: 12 benchmark rows auditable "
                 "(4 Rt-claimable → 12 Ct-comparable)", fontsize=11)
    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / "ct_admissibility_m1704.png"
    fig.savefig(png, dpi=140)
    plt.close(fig)
    return png


def main() -> int:
    result = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    png = figure(result)
    result["figure"] = str(png.relative_to(REPO))
    (OUT / "ct_admissibility.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    o = result["overall"]
    print(f"cases={o['cases']} ct_admissible={o['ct_admissible']} ct_pass={o['ct_pass']} "
          f"ct_pass_rate={o['ct_pass_rate_pct']}% rt_claimable={o['rt_claimable']}")
    for c in result["cases"]:
        print(f"{c['case_id']:14s} Fn={c['froude']:.4f} calc={c['calc_ct']:.6f} "
              f"ref={c['ref_ct']:.6f} err={c['error_pct']:6.2f}% lvl={c['ct_evidence_level']:6s} "
              f"pass={c['ct_level_pass']}")
    print("artifact:", OUT / "ct_admissibility.json")
    print("figure  :", png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())