"""报告图件 (matplotlib Agg, 无中文字体依赖 → 英文轴标, 零领域语义)。

三张图:

* ``completeness``: 每节点 已产出/必需/期望 工件数 + 完整性比例;
* ``contract``: 契约检查按 code 的 PASS/FAIL 计数 + 跨生产者一致结论;
* ``baseline``: 每条基线量的当前/基线相对偏差 (缺值 → WITHHELD 标注)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .provenance import sha256_file  # noqa: E402

COLORS = {"PASS": "#2e7d32", "FAIL": "#c62828", "NA": "#9e9e9e",
          "PRESENT": "#2e7d32", "PARTIAL": "#f9a825", "MISSING": "#c62828"}


def _save(fig, path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return {"name": path.name, "path": str(path), "sha256": sha256_file(path)}


def fig_completeness(nodes: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    labels = [n["node_ext_id"] for n in nodes]
    req = [len(n["required_present"]) for n in nodes]
    exp = [len(n["expected_present"]) for n in nodes]
    req_tot = [len(n["required_artifacts"]) for n in nodes]
    ratio = [n["completeness"] for n in nodes]
    fig, ax = plt.subplots(figsize=(max(7.0, 1.1 * len(labels)), 3.8))
    x = range(len(labels))
    ax.bar([i - 0.22 for i in x], req, width=0.42, label="required present",
           color="#1565c0")
    ax.bar([i + 0.22 for i in x], exp, width=0.42, label="expected present",
           color="#42a5f5")
    ax.scatter(list(x), req_tot, marker="_", s=260, color="#c62828",
               label="required total", zorder=5)
    for i, (r, st) in enumerate(zip(ratio, [n["status"] for n in nodes])):
        ax.text(i, max(req[i], exp[i], req_tot[i]) + 0.15, f"{r:.0%}\n{st}",
                ha="center", va="bottom", fontsize=7,
                color=COLORS.get(st, "#000000"))
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=7)
    ax.set_ylabel("artifact count")
    ax.set_title("I1 artifact completeness per node (scan snapshot)")
    ax.legend(fontsize=7, loc="upper right")
    ax.grid(axis="y", alpha=0.25)
    return _save(fig, path)


def fig_contract(contract: dict[str, Any], path: Path) -> dict[str, Any]:
    by_code = contract["summary"].get("by_code", {})
    codes = sorted(by_code)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.6),
                                   gridspec_kw={"width_ratios": [1.5, 1.0]})
    if codes:
        pas = [by_code[c].get("PASS", 0) for c in codes]
        fails = [by_code[c].get("FAIL", 0) for c in codes]
        na = [by_code[c].get("NA", 0) for c in codes]
        y = range(len(codes))
        ax1.barh(list(y), pas, color="#2e7d32", label="PASS")
        ax1.barh(list(y), fails, left=pas, color="#c62828", label="FAIL")
        ax1.barh(list(y), na, left=[p + f for p, f in zip(pas, fails)],
                 color="#9e9e9e", label="NA")
        ax1.set_yticks(list(y))
        ax1.set_yticklabels(codes, fontsize=8)
        ax1.set_xlabel("checks")
        ax1.set_title("contract checks by code")
        ax1.legend(fontsize=7)
        ax1.grid(axis="x", alpha=0.25)
    else:
        ax1.text(0.5, 0.5, "no contract checks ran", ha="center")
        ax1.set_axis_off()
    cross = contract.get("cross_producer", [])
    if cross:
        names = [c["quantity"] for c in cross]
        vals = [{"PASS": 1, "FAIL": 0, "NA": 0.5}.get(c["result"], 0.25) for c in cross]
        cols = [COLORS.get(c["result"], "#000000") for c in cross]
        ax2.barh(range(len(names)), vals, color=cols)
        ax2.set_yticks(range(len(names)))
        ax2.set_yticklabels(names, fontsize=7)
        ax2.set_xlim(0, 1.15)
        ax2.set_title("cross-producer agreement (PASS=1, NA=0.5, FAIL=0)")
        ax2.grid(axis="x", alpha=0.25)
    else:
        ax2.text(0.5, 0.5, "no cross-producer quantities", ha="center")
        ax2.set_axis_off()
    fig.suptitle(f"I2 interface contract (pass rate={contract['summary'].get('pass_rate')})",
                 fontsize=9)
    return _save(fig, path)


def fig_baseline(baseline_block: list[dict[str, Any]], wave_label: str,
                 path: Path) -> dict[str, Any]:
    names = [f"{b['node_ext_id']}:{b['name']}" for b in baseline_block]
    fig, ax = plt.subplots(figsize=(max(7.0, 0.9 * len(names) + 3), 3.6))
    if not names:
        ax.text(0.5, 0.5, "no baseline quantity declared", ha="center")
        ax.set_axis_off()
        return _save(fig, path)
    deltas, colors, ann = [], [], []
    for b in baseline_block:
        comp = b["comparison"]
        if comp.get("delta_rel") is None:
            deltas.append(0.0)
            colors.append(COLORS["NA"])
            ann.append("WITHHELD")
        else:
            deltas.append(comp["delta_rel"] * 100.0)
            colors.append(COLORS["PASS"] if comp["verdict"] in ("PASS", "NA")
                          else COLORS["FAIL"])
            ann.append(f"{comp['delta_rel'] * 100:+.3g}%")
    ax.barh(range(len(names)), deltas, color=colors)
    for i, (a, c) in enumerate(zip(ann, colors)):
        ax.text(0.002, i, a, va="center", ha="left", fontsize=7,
                color="#000000" if c != COLORS["NA"] else "#555555")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("relative delta vs baseline [%]")
    ax.set_title(f"I3 regression baseline vs {wave_label} "
                 f"(grey = WITHHELD: measured value absent)")
    ax.axvline(0.0, color="#000000", lw=0.8)
    ax.grid(axis="x", alpha=0.25)
    return _save(fig, path)