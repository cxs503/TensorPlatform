#!/usr/bin/env python3
"""敏感性与图形工件：导数无关优化的参数扫描 + 两张台账图。

产物（都在 ``artifacts/external_tools/``）::

    sensitivity.json        两参数点（trials）× 两随机种子（seed）扫描
    figures/coverage.png    各工具类状态矩阵 + 覆盖判据标线
    figures/residuals.png   每条不变式的观测值 vs 判据上限（对数轴）

用法::

    EXTERNAL_TOOLS_DOTENV=<deploy .env> python3 -m tools.external_tools.sensitivity
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tools.external_tools import adapters, harness, report, spec as spec_mod  # noqa: E402

OUT = ROOT / "artifacts" / "external_tools"


def sweep_optimizer(trials=(80, 200), seeds=(7, 11)) -> Dict[str, Any]:
    """同一目标函数、两个试验预算 × 两个种子：看判据 1e-3 是否靠运气。"""

    rows: List[Dict[str, Any]] = []
    for n in trials:
        for seed in seeds:
            res = adapters.blackbox_optimization({"trials": n, "seed": seed})
            rows.append({"trials": n, "seed": seed,
                         "best_objective": res["metrics"]["best_objective"],
                         "distance_to_optimum": res["metrics"]["distance_to_optimum"]})
    return {"adapter": "blackbox_optimization",
            "objective": "(x-0.3)^2 + (y+0.7)^2 on [-3,3]^2",
            "invariant_limit": 1e-3,
            "rows": rows,
            "all_within_limit": all(r["best_objective"] <= 1e-3 for r in rows)}


def plot_coverage(ledger: Dict[str, Any], path: Path) -> str:
    classes = ledger["classes"]
    cats = sorted({c["category"] for c in classes})
    status_of = {(c["category"], c["status"]) for c in classes}
    levels = ["REAL", "MOCK", "WITHHELD", "FAIL"]
    colors = {"REAL": "#2a9d8f", "MOCK": "#e9c46a", "WITHHELD": "#8d99ae", "FAIL": "#e63946"}
    fig, ax = plt.subplots(figsize=(7.2, 3.2), dpi=130)
    for i, cat in enumerate(cats):
        for j, lv in enumerate(levels):
            if (cat, lv) in status_of:
                ax.barh(i, 1, left=j, color=colors[lv], edgecolor="white")
    ax.set_yticks(range(len(cats)))
    ax.set_yticklabels(cats, fontsize=8)
    ax.set_xticks([j + 0.5 for j in range(len(levels))])
    ax.set_xticklabels(levels, fontsize=8)
    ax.set_title(f"{ledger['suite_id']}: REAL {ledger['measured']['real_classes']}"
                 f"/{ledger['coverage']['target_classes']} (target) -> "
                 f"{ledger.get('verdict', '')}", fontsize=9)
    ax.invert_yaxis()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def plot_residuals(ledger: Dict[str, Any], path: Path) -> str:
    """每条不变式的观测值（归一化到判据上限）——1 以上即违约。"""

    pts = []
    for rec in ledger["classes"]:
        for inv in rec.get("invariants") or []:
            obs, lim = inv.get("observed"), inv.get("limit")
            if obs is None or lim in (None, 0):
                continue
            pts.append((f"{rec['tool_id']}:{inv['metric']}", float(obs) / float(lim),
                        rec["status"]))
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=130)
    labels = [p[0] for p in pts]
    vals = [max(p[1], 1e-12) for p in pts]
    colors = ["#2a9d8f" if p[2] == "REAL" else "#e63946" for p in pts]
    ax.barh(range(len(vals)), vals, color=colors)
    ax.set_xscale("log")
    ax.axvline(1.0, color="black", ls="--", lw=1, label="判据上限 (obs/limit=1)")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("observed / limit（对数轴，<1 = 通过）", fontsize=8)
    ax.set_title("外部工具类不变式余量", fontsize=9)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    suite = spec_mod.load_suite(ROOT / "data" / "external_tools" / "classes.json")
    ctx = {"repo": str(ROOT), "out_dir": str(OUT),
           "dotenv": os.environ.get("EXTERNAL_TOOLS_DOTENV", "")}
    ledger = harness.run_suite(suite, ctx=ctx)
    ledger["verdict"] = harness.verdict(ledger)
    paths = report.write_artifacts(ledger, OUT, stem="external_tools")
    sweep = sweep_optimizer()
    (OUT / "sensitivity.json").write_text(
        json.dumps(sweep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    figs = {"coverage": plot_coverage(ledger, OUT / "figures" / "coverage.png"),
            "residuals": plot_residuals(ledger, OUT / "figures" / "residuals.png")}
    print(json.dumps({"verdict": ledger["verdict"],
                      "real": ledger["measured"]["real_classes"],
                      "target": ledger["coverage"]["target_classes"],
                      "digest": ledger["digest"],
                      "value_digest": ledger["value_digest"],
                      "sweep_all_within_limit": sweep["all_within_limit"],
                      "figures": figs, "artifacts": paths,
                      "sweep": sweep["rows"]}, ensure_ascii=False, indent=2))
    return 0 if ledger["verdict"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())