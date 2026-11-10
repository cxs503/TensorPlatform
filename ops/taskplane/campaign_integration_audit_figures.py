#!/usr/bin/env python3
"""campaign_integration_audit_figures —— 战役级集成审计的矢量证据图（数据全部来自报告 JSON）。

纪律
----
* **零手抄**：所有数字从 ``audit()`` 的报告字典取值，本模块不含任何常数结论；
* **零领域语义**：标签一律取节点 ``ext_id`` / 判据 id / 仓名等注入字段，模块内无学科词条；
* 图内文字用 ASCII/英文，避免容器缺 CJK 字体时渲染成豆腐块（报告正文负责中文表述）；
* 输出确定性：同一报告连跑两次像素一致（无随机、无时间戳）。

三张图
------
``fig1_capability_matrix.png``  节点 × 四维矩阵（证据分类 / 判据通过率 / 精度带 / 台账覆盖）
``fig2_precision_bands.png``    每节点最差相对误差 + 3%/15% 验收线（log 轴）+ 精度带堆叠
``fig3_gate_and_classes.png``   八条机裁判据实测 vs 阈值 + 四分类分布（honest FAIL 显式可见）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")  # 无显示环境（集群/CI）必须
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

CLASS_ORDER = ["pass", "honest_fail", "unproven", "fail"]
CLASS_COLORS = {"pass": "#2ca02c", "honest_fail": "#ff7f0e",
                "unproven": "#7f7f7f", "fail": "#d62728"}
BAND_ORDER = ["excellent", "acceptable", "unacceptable", "unmeasured"]
BAND_COLORS = {"excellent": "#1f77b4", "acceptable": "#ffbf00",
               "unacceptable": "#d62728", "unmeasured": "#bfbfbf"}
BAND_EDGES = {"excellent": 0.03, "acceptable": 0.15}


def _labels(report: Mapping[str, Any]) -> list[str]:
    return [str(n.get("ext_id") or n.get("node_id")) for n in report.get("nodes") or []]


def fig_capability_matrix(report: Mapping[str, Any], path: Path) -> dict:
    """节点 × 四维矩阵：每格填充 0..1 的「符合度」，格内写原始值。"""
    nodes = list(report.get("nodes") or [])
    if not nodes:
        return {"figure": path.name, "skipped": "no_nodes"}
    dims = ["evidence\nclass", "criteria\npass-ratio", "precision\nband", "ledger\ncoverage"]
    ledger_required = {(r.get("ext_id"), c): r for r in
                       (report.get("ledger") or {}).get("required") or []
                       for c in [r.get("capability_id")]}
    rows, texts, colors = [], [], []
    for node in nodes:
        ext = str(node.get("ext_id") or "")
        klass = str(node.get("class"))
        ratio = (node.get("n_passed") or 0) / (node.get("n_criteria") or 1) \
            if node.get("n_criteria") else 0.0
        band = str(node.get("band"))
        caps = list(node.get("capability_ids") or [])
        covered = 0
        for cid in caps:
            entry = ledger_required.get((ext, cid))
            if entry and entry.get("present") and str(entry.get("level") or "").upper() not in ("", "NONE"):
                covered += 1
        cov = (covered / len(caps)) if caps else 0.0
        col = [{"pass": 1.0, "honest_fail": 0.55, "unproven": 0.25, "fail": 0.0}.get(klass, 0.1),
               float(ratio),
               {"excellent": 1.0, "acceptable": 0.7, "unacceptable": 0.0,
                "unmeasured": 0.25}.get(band, 0.25),
               float(cov)]
        txt = [klass, f"{node.get('n_passed') or 0}/{(node.get('n_criteria') or 0)}",
               band, f"{covered}/{len(caps)}"]
        rows.append(col)
        texts.append(txt)
        colors.append(col)

    fig, ax = plt.subplots(figsize=(7.6, 0.62 * len(nodes) + 1.9))
    im = ax.imshow(rows, cmap=ListedColormap(["#f2f2f2"]), vmin=0, vmax=1, aspect="auto")
    # 用像素方块着色（imshow + 逐格 Rectangle 保证色与文字对齐）
    ax.clear()
    ax.set_xlim(-0.5, len(dims) - 0.5)
    ax.set_ylim(len(nodes) - 0.5, -0.5)
    for i, node in enumerate(nodes):
        for j, _dim in enumerate(dims):
            value = rows[i][j]
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=plt.get_cmap("RdYlGn")(value),
                                       edgecolor="white", linewidth=1.2))
            ax.text(j, i, texts[i][j], ha="center", va="center", fontsize=8.5,
                    color="black" if value > 0.25 else "white", fontweight="bold")
    ax.set_xticks(range(len(dims)))
    ax.set_xticklabels(dims, fontsize=9)
    ax.set_yticks(range(len(nodes)))
    ax.set_yticklabels(_labels(report), fontsize=8.5)
    ax.set_title(f"Capability matrix - plan {report.get('plan_id')} "
                 f"({report.get('verdict')}, n={len(nodes)})", fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, label="conformance 0..1")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"figure": path.name, "n_rows": len(nodes), "dims": [d.replace("\n", " ") for d in dims]}


def fig_precision_bands(report: Mapping[str, Any], path: Path) -> dict:
    nodes = list(report.get("nodes") or [])
    if not nodes:
        return {"figure": path.name, "skipped": "no_nodes"}
    labels = _labels(report)
    errs = [n.get("worst_rel_error") for n in nodes]
    bands = [str(n.get("band")) for n in nodes]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(max(6.4, 0.75 * len(nodes) + 2.4), 6.4),
                                   gridspec_kw={"height_ratios": [2, 1]}, sharex=True)
    xs = list(range(len(nodes)))
    plotted = [(x, e) for x, e in zip(xs, errs) if e is not None]
    if plotted:
        ax1.bar([p[0] for p in plotted], [max(p[1], 1e-6) for p in plotted],
                color=[BAND_COLORS.get(bands[p[0]], "#bfbfbf") for p in plotted], width=0.6)
        for x, e in plotted:
            ax1.text(x, max(e, 1e-6) * 1.12, f"{e * 100:.2f}%", ha="center", fontsize=8)
    ax1.set_yscale("log")
    for name, edge in BAND_EDGES.items():
        ax1.axhline(edge, linestyle="--", linewidth=1.1,
                    color=BAND_COLORS[name], label=f"{name} line {edge * 100:.0f}%")
    ax1.set_ylabel("worst rel. error (log)")
    ax1.set_title(f"Precision bands vs acceptance lines - plan {report.get('plan_id')}", fontsize=10)
    ax1.legend(fontsize=8)
    ax1.grid(True, axis="y", alpha=0.3)

    counts = {b: bands.count(b) for b in BAND_ORDER}
    bottom = 0
    for b in BAND_ORDER:
        if counts[b]:
            ax2.bar(xs, [1] * len(xs), bottom=[0] * len(xs), color="none")
    # 每节点一格：按其精度带着色（堆叠分布画在右侧计数图更清晰）
    for x, b in zip(xs, bands):
        ax2.add_patch(plt.Rectangle((x - 0.4, 0), 0.8, 1, color=BAND_COLORS.get(b, "#bfbfbf")))
    ax2.set_yticks([])
    ax2.set_ylabel("band")
    ax2.set_ylim(0, 1)
    ax2.set_xticks(xs)
    ax2.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax2.text(1.01, 0.5, "  ".join(f"{b}={counts[b]}" for b in BAND_ORDER), transform=ax2.transAxes,
             rotation=0, va="center", fontsize=7, color="#444444")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"figure": path.name, "counts": counts,
            "n_measured": sum(1 for e in errs if e is not None)}


def fig_gate_and_classes(report: Mapping[str, Any], path: Path) -> dict:
    crit = list(report.get("criteria") or [])
    classes = report.get("classes") or {}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.2),
                                   gridspec_kw={"width_ratios": [1.7, 1]})
    ids = [c["id"].split("_")[0] for c in crit]
    # 归一化余量：measured/threshold 仅在 threshold>0 时有意义；用「违规数」倒序条呈现
    measured = [float(c["measured"]) for c in crit]
    passed = [bool(c["passed"]) for c in crit]
    colors = ["#2ca02c" if p else "#d62728" for p in passed]
    y = list(range(len(crit)))
    ax1.barh(y, [max(m, 0.05) for m in measured], color=colors)
    for yi, c, m in zip(y, crit, measured):
        ax1.text(max(m, 0.05) * 1.05, yi, f"{c['op']}{c['threshold']} / got {m:g}",
                 va="center", fontsize=8)
    ax1.set_yticks(y)
    ax1.set_yticklabels(ids, fontsize=9)
    ax1.set_xscale("symlog", linthresh=0.1)
    ax1.set_xlabel("measured value (symlog)")
    ax1.set_title(f"Eight machine criteria - {report.get('verdict')}", fontsize=10)
    ax1.grid(True, axis="x", alpha=0.3)

    names = CLASS_ORDER
    vals = [int(classes.get(n, 0)) for n in names]
    bars = ax2.bar(names, vals, color=[CLASS_COLORS[n] for n in names])
    for b, v in zip(bars, vals):
        ax2.text(b.get_x() + b.get_width() / 2, v + 0.05, str(v), ha="center", fontsize=9)
    ax2.set_ylabel("nodes")
    ax2.set_title("Outcome classes (honest FAIL kept visible)", fontsize=10)
    ax2.set_ylim(0, max(vals + [1]) * 1.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"figure": path.name, "classes": {n: int(classes.get(n, 0)) for n in names},
            "n_criteria": len(crit),
            "n_failed_criteria": sum(1 for p in passed if not p)}


def render_all(report: Mapping[str, Any], out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "tp.campaign.integration_audit.figures/v1",
        "plan_id": report.get("plan_id"),
        "input_digest": report.get("input_digest"),
        "figures": [
            fig_capability_matrix(report, out_dir / "fig1_capability_matrix.png"),
            fig_precision_bands(report, out_dir / "fig2_precision_bands.png"),
            fig_gate_and_classes(report, out_dir / "fig3_gate_and_classes.png"),
        ],
    }
    (out_dir / "figures.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":  # pragma: no cover
    import sys

    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(render_all(payload, Path(sys.argv[2])), ensure_ascii=False, indent=2))