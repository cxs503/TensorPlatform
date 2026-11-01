#!/usr/bin/env python3
"""m1614 收编报告图：四分支保留/弃用矩阵 + 正典数据判定 + 模型误差谱。

所有数字从工件读取（runs/m1614/benchmark_audit.json、artifacts/m1388/*.json、
artifacts/baseline_integrity/audit_canon_v3.json），本脚本不手抄任何数值。

用法：python3 scripts/m1614_integration_figures.py
输出：docs/reports/m1614/fig1_branch_merge_matrix.png
      docs/reports/m1614/fig2_dataset_verdicts.png
      docs/reports/m1614/fig3_model_error_spectrum.png
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "reports" / "m1614"
OUT.mkdir(parents=True, exist_ok=True)

# ── 图 1：四分支逐文件收编矩阵（kept=2, hunk=1, dropped=0）────────────────
BRANCHES = ["①m1388", "②m1406", "③m1407", "④m1383"]
ITEMS = [
    "ship_benchmark.py", "calculations.py", "benchmark_integrity.py",
    "benchmark_dataset_audit.py", "ship_proxy.py",
    "benchmark_reference.json", "benchmark_canon.py",
    "feat/test_benchmark_chain_m1388.py", "feat/test_ship_benchmark.py",
    "test_benchmark_integrity.py", "test_baseline_integrity.py",
    "test_benchmark_reference_integrity.py", "test_benchmark_audit_endpoint.py",
    "scripts/benchmark_audit.py", "scripts/audit_benchmark_chain_m1388.py",
    "tools/audit/baseline_integrity.py", "tools/audit/normalize_*",
    "tools/audit/benchmark_reference_integrity.py", "docs/ + runs/ + artifacts/",
]
# 2=整文件收编 / 1=hunk 级合成（或按正典适配）/ 0=弃用（正典胜出）/ -1=本分支未涉及
MATRIX = [
    [2, 1, -1, 1],   # ship_benchmark.py：m1388 全取 + m1406 端点/C_T 反推/端口探测 hunk + m1383 消费门禁语义
    [2, -1, -1, -1],  # calculations.py：m1388 的 C_T/method 透出 hunk
    [1, 1, -1, -1],   # benchmark_integrity.py：正典版 + m1406 的 bases/C_T 反推工具
    [2, -1, -1, -1],  # dataset_audit facade（正典已有，m1388 未改）
    [-1, -1, 0, -1],  # ship_proxy.py：m1407 重构与正典测试冲突 → 弃用（仅取 gate_enabled 消费门禁）
    [-1, -1, 0, 0],   # benchmark_reference.json：正典 v3.0 数据判定胜出
    [-1, -1, -1, -1],  # benchmark_canon.py：正典独有
    [2, -1, -1, -1],
    [2, -1, -1, -1],
    [-1, 1, -1, -1],
    [-1, -1, 1, -1],
    [-1, -1, -1, 1],
    [-1, 1, -1, -1],
    [-1, 2, -1, -1],
    [2, -1, -1, -1],
    [-1, -1, 2, -1],
    [-1, -1, 1, -1],
    [-1, -1, -1, 2],
    [2, 2, 2, 2],
]
COLORS = {2: "#1e8449", 1: "#f1c40f", 0: "#c0392b", -1: "#ecf0f1"}


def fig1() -> Path:
    mat = np.array(MATRIX, dtype=float)
    fig, ax = plt.subplots(figsize=(7.2, 8.4))
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            ax.add_patch(plt.Rectangle((j, i), 1, 1,
                                       color=COLORS[mat[i, j]], ec="white"))
            lab = {2: "whole", 1: "hunk", 0: "drop", -1: "-"}[mat[i, j]]
            ax.text(j + 0.5, i + 0.5, lab, ha="center", va="center", fontsize=8)
    ax.set_xlim(0, mat.shape[1])
    ax.set_ylim(mat.shape[0], 0)
    ax.set_xticks(np.arange(mat.shape[1]) + 0.5, BRANCHES)
    ax.set_yticks(np.arange(mat.shape[0]) + 0.5, ITEMS, fontsize=8)
    ax.xaxis.tick_top()
    ax.set_title("m1614 four-branch integration matrix (green=whole file, yellow=hunk merge, red=dropped: canon wins)",
                 fontsize=10, pad=28)
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[k]) for k in (2, 1, 0)]
    ax.legend(handles, ["taken whole", "hunk-level merge/adapt", "dropped (canon wins)"],
              loc="lower right", fontsize=8, bbox_to_anchor=(1.0, -0.06))
    fig.tight_layout()
    path = OUT / "fig1_branch_merge_matrix.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ── 图 2：正典数据集判定 + 三个独立审阅者交叉复核 ────────────────────────
def fig2() -> Path:
    ledger = json.loads((ROOT / "runs" / "m1614" / "benchmark_audit.json").read_text())
    rows = ledger["dataset_audit"]["rows"]
    ids = [r["case_id"] for r in rows]
    status_rank = {"PASS": 1.0, "WARN": 0.6, "WITHHELD": 0.3, "FAIL": 0.0}
    own = [status_rank.get(r["verdict"], 0.5) for r in rows]

    # 独立复核 #2：m1383 工具（thorough physics identity audit）
    spec = __import__("importlib.util", fromlist=["util"]).spec_from_file_location(
        "bri", ROOT / "tools" / "audit" / "benchmark_reference_integrity.py")
    bri = __import__("importlib.util", fromlist=["util"]).module_from_spec(spec)
    spec.loader.exec_module(bri)  # type: ignore[union-attr]
    data = json.loads((ROOT / "backend" / "data" / "benchmark_reference.json").read_text())
    m1383 = {r["case_id"]: r["verdict"] for r in bri.audit_dataset(data)["cases"]}
    m1383_rank = {"REAL": 1.0, "DERIVED": 0.8, "WITHHELD": 0.3}
    other = [m1383_rank.get(m1383[i], 0.5) for i in ids]

    # 独立复核 #3：m1407 工具（六类审计，C1 Ct↔Rt 偏差 → 判定）
    bl = json.loads((ROOT / "artifacts" / "baseline_integrity" / "audit_canon_v3.json").read_text())
    blrows = {r["case_id"]: r for r in bl["case_results"]}
    third = []
    for i in ids:
        chk = blrows[i]["checks"]["C1_ct_rt"]["status"]
        third.append({"PASS": 1.0, "WARN": 0.6, "WITHHELD": 0.3, "FAIL": 0.0}.get(chk, 0.5))

    fig, ax = plt.subplots(figsize=(11, 4.6))
    x = np.arange(len(ids))
    w = 0.27
    ax.bar(x - w, own, w, label="canon v3.0 + benchmark_integrity (endpoint ledger)", color="#2471a3")
    ax.bar(x, other, w, label="m1383 independent reviewer (REAL/DERIVED/WITHHELD)", color="#1e8449")
    ax.bar(x + w, third, w, label="m1407 six-check audit (C1 Ct<->Rt)", color="#b9770e")
    ax.set_xticks(x, ids, rotation=45, ha="right", fontsize=8)
    ax.set_yticks([0, 0.3, 0.6, 1.0], ["FAIL", "WITHHELD", "WARN", "PASS"])
    ax.set_ylim(0, 1.15)
    ax.set_title("Three independent reviewers on the 12 reference rows (higher = usable as ground truth)", fontsize=10)
    ax.legend(fontsize=8, loc="upper center", ncol=3, bbox_to_anchor=(0.5, -0.28))
    fig.tight_layout()
    path = OUT / "fig2_dataset_verdicts.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ── 图 3：模型 vs 参考 Ct 的误差谱 + 15% 精度线 ──────────────────────────
def fig3() -> Path:
    chain = json.loads((ROOT / "artifacts" / "m1388" / "chain_results.json").read_text())
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), sharey=True)
    for ax, ship in zip(axes, ("SUBOFF", "DTMB-5415", "KVLCC2")):
        res = chain[ship]["results"]
        geo = json.loads((ROOT / "backend" / "data" / "benchmark_reference.json").read_text())
        fr_by_case = {c["case_id"]: c["froude_number"] for c in geo["cases"]}
        fr = [fr_by_case[r["case_id"]] for r in res]
        err = [r["error_pct"] for r in res]
        colors = ["#1e8449" if e <= 15 else "#c0392b" for e in err]
        ax.bar(range(len(fr)), err, color=colors)
        ax.axhline(15.0, color="k", ls="--", lw=0.9, label="15% accuracy gate")
        ax.set_xticks(range(len(fr)), [f"Fr={f:.3f}" for f in fr], rotation=45,
                      ha="right", fontsize=7)
        verdict = "verified" if chain[ship]["summary"]["verified_cases"] else "all withheld"
        ax.set_title(f"{ship}（{verdict}）", fontsize=9)
        ax.set_xlabel("case (sorted by Fr)")
    axes[0].set_ylabel("|Ct_calc - Ct_ref| / Ct_ref  [%]")
    axes[0].legend(fontsize=8)
    fig.suptitle("Platform resistance model vs reference dataset: per-case relative error (green<=15%, red>15%)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = OUT / "fig3_model_error_spectrum.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


if __name__ == "__main__":
    for p in (fig1(), fig2(), fig3()):
        print("figure ->", p)