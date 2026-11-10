#!/usr/bin/env python3
"""战役验收门图件生成器（域中性）：只读报告 JSON，不解算任何领域量。

输入（全部来自上游判据产物，零手抄）：
  --acceptance  战役验收门报告 JSON（census / criteria / governance）
  --integration 跨域集成门报告 JSON（nodes / edges）
  --lint        集成合约设计期静态检查 JSON
  --out-dir     图件输出目录（同时写 figures_metadata.json，含每图 sha256）

输出：
  fig1_node_census.png        节点状态普查（bucket 计数 + 未收口节点名单）
  fig2_criteria_matrix.png    A~F 战役判据 × 集成门六子判据 的红绿矩阵
  fig3_interface_coverage.png 设计期声明覆盖 vs 证据期真实供给（逐上游节点）
  fig4_governance_checks.png  治理工具链五检结果矩阵
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

PASS_C = "#2e7d32"
FAIL_C = "#c62828"
WARN_C = "#f9a825"
GREY = "#9e9e9e"


def _load(path: str) -> Mapping[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fig_census(acc: Mapping[str, Any], out: Path) -> Path:
    counts = (acc.get("census") or {}).get("counts") or {}
    open_nodes = (acc.get("census") or {}).get("open_nodes") or []
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2),
                                   gridspec_kw={"width_ratios": [1, 1.4]})
    keys = ["total", "closed", "failed", "open", "unknown"]
    vals = [int(counts.get(k) or 0) for k in keys]
    colors = [GREY, PASS_C, WARN_C, FAIL_C, "#5e35b1"]
    bars = ax1.bar(keys, vals, color=colors)
    for b, v in zip(bars, vals):
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.1, str(v), ha="center", fontsize=11)
    ax1.set_title("Criterion A: node status census (task nodes)")
    ax1.set_ylabel("nodes")
    ax1.set_ylim(0, max(vals + [1]) + 1.2)
    ax1.grid(axis="y", alpha=0.25)

    if open_nodes:
        labels = [o.get("ext_id") or str(o.get("node_id")) for o in open_nodes]
        ax2.barh(range(len(labels)), [1] * len(labels), color=FAIL_C, alpha=0.85)
        ax2.set_yticks(range(len(labels)))
        ax2.set_yticklabels([f"{l}  [{o.get('status')}]" for l, o in zip(labels, open_nodes)],
                            fontsize=9)
        ax2.set_xticks([])
        ax2.set_title(f"unclosed nodes blocking the gate: {len(labels)}")
        ax2.invert_yaxis()
    else:
        ax2.text(0.5, 0.5, "no unclosed nodes", ha="center", va="center", fontsize=14)
        ax2.axis("off")
    fig.suptitle("P143-W2C campaign acceptance — criterion A census", fontsize=13)
    fig.tight_layout()
    p = out / "fig1_node_census.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_matrix(acc: Mapping[str, Any], integration: Mapping[str, Any], out: Path) -> Path:
    rows: list[tuple[str, bool, str]] = []
    for c in acc.get("criteria") or []:
        rows.append((c.get("id", "?"), bool(c.get("passed")), "campaign"))
    for c in integration.get("criteria") or []:
        rows.append((f"integration/{c.get('id')}", bool(c.get("passed")), "integration"))
    fig, ax = plt.subplots(figsize=(11, 0.5 * len(rows) + 1.6))
    for i, (name, ok, group) in enumerate(rows):
        ax.add_patch(Rectangle((0, i), 1, 0.82, color=PASS_C if ok else FAIL_C, alpha=0.9))
        ax.text(1.05, i + 0.41, name, va="center", fontsize=10)
        ax.text(0.5, i + 0.41, "PASS" if ok else "FAIL", va="center", ha="center",
                color="white", fontsize=10, fontweight="bold")
        ax.text(-0.05, i + 0.41, group, va="center", ha="right", fontsize=8, color="#555")
    ax.set_xlim(-0.9, 4.2)
    ax.set_ylim(-0.3, len(rows))
    ax.axis("off")
    verdict = acc.get("verdict")
    ax.set_title(f"campaign verdict = {verdict}  "
                 f"({sum(1 for r in rows if r[1])}/{len(rows)} machine criteria passed)",
                 fontsize=12)
    p = out / "fig2_criteria_matrix.png"
    fig.savefig(p, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return p


def fig_interface(contract: Mapping[str, Any], integration: Mapping[str, Any],
                  out: Path) -> Path:
    nodes = contract.get("nodes") or {}
    gate = None
    for key, entry in nodes.items():
        if entry.get("requires"):
            gate = (key, entry)
            break
    labels: list[str] = []
    declared: list[int] = []
    supplied: list[int] = []
    supply_by_node: dict[str, int] = {}
    for n in (integration.get("nodes") or []):
        supply_by_node[str(n.get("node_key"))] = len((n.get("artifacts") or []))
    if gate:
        _, entry = gate
        keys = sorted(entry.get("requires") or [], key=lambda r: r.get("key", ""))
        by_node: dict[str, list[str]] = {}
        for req in keys:
            prefix = str(req.get("key", "")).split(".", 1)[0]
            by_node.setdefault(prefix, []).append(str(req.get("key")))
        for prefix, ks in sorted(by_node.items()):
            labels.append(prefix)
            declared.append(len(ks))
            supplied.append(0)
    fig, ax = plt.subplots(figsize=(12, 5.4))
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], declared, width=0.4, color="#1565c0",
           label="required keys declared by gate (design-time)")
    ax.bar([i + 0.2 for i in x], supplied, width=0.4, color=FAIL_C,
           label="keys actually supplied by upstream evidence (evidence-time)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=20, fontsize=9)
    ax.set_ylabel("interface keys")
    ax.set_title("cross-domain interface coverage: declarations vs evidence supply")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    for i, v in enumerate(declared):
        ax.text(i - 0.2, v + 0.05, str(v), ha="center", fontsize=8)
    fig.tight_layout()
    p = out / "fig3_interface_coverage.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_governance(acc: Mapping[str, Any], out: Path) -> Path:
    rows = (acc.get("governance") or {}).get("modules") or []
    checks = ["import_ok", "exports", "tests_referencing", "smoke"]
    fig, ax = plt.subplots(figsize=(11, 0.9 * max(len(rows), 2) + 2.0))
    for i, m in enumerate(rows):
        cells = [
            bool(m.get("import_ok")),
            not (m.get("missing_exports") or []),
            bool(m.get("tests_referencing")),
            (m.get("smoke") or {}).get("status") in ("ok", "not_declared"),
        ]
        for j, ok in enumerate(cells):
            ax.add_patch(Rectangle((j, i), 0.92, 0.82,
                                   color=PASS_C if ok else FAIL_C, alpha=0.9))
            label = m.get("path", "").split("/")[-1] if j == 0 else ""
            ax.text(j + 0.46, i + 0.41, "PASS" if ok else "FAIL" if j != 1 else
                    "MISSING", ha="center", va="center", color="white", fontsize=9,
                    fontweight="bold")
        ax.text(-0.1, i + 0.41, m.get("key", "?"), ha="right", va="center", fontsize=10)
        smoke = (m.get("smoke") or {})
        tail = (smoke.get("stdout_tail") or [])
        if tail:
            ax.text(3.46, i + 0.41, tail[-1][:42], ha="left", va="center", fontsize=7,
                    color="#333")
    ax.set_xlim(-2.6, 6.4)
    ax.set_ylim(-0.4, max(len(rows), 1))
    ax.set_xticks([j + 0.46 for j in range(len(checks))])
    ax.set_xticklabels(["file+import", "exports", "test reference", "end-to-end smoke"],
                       fontsize=9)
    ax.set_yticks([])
    ax.axis("off")
    ax.set_title("governance toolchain in-position checks (N03 backfiller / N04 power gate)",
                 fontsize=12)
    p = out / "fig4_governance_checks.png"
    fig.savefig(p, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="campaign acceptance figures (domain-neutral)")
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--integration", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--lint")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    acc = _load(args.acceptance)
    integ = _load(args.integration)
    contract = _load(args.contract)
    figs = [
        (fig_census(acc, out), "节点状态普查：bucket 计数（左）与未收口节点名单（右）"),
        (fig_matrix(acc, integ, out), "判据矩阵：战役 A~F 判据 + 集成门六子判据的红绿裁决"),
        (fig_interface(contract, integ, out),
         "跨域接口覆盖：网关声明所需键（设计期）vs 上游证据真实供给（证据期，当前为 0）"),
        (fig_governance(acc, out), "治理工具链五检矩阵（导入/导出/测试引用/端到端冒烟）"),
    ]
    meta = {"generated_from": {"acceptance": args.acceptance,
                               "integration": args.integration,
                               "contract": args.contract, "lint": args.lint},
            "figures": [{"path": p.name, "sha256": _sha(p), "caption": cap}
                        for p, cap in figs]}
    (out / "figures_metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    for p, _ in figs:
        print(f"{p} sha256={_sha(p)[:16]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())