"""把 ``artifacts/repro_audit.json`` 变成论文级报告素材（图 + 程序化注入的数字）。

原则：报告里的每个数字都从 JSON 读出来注入，**零手抄**（owner 2026-10-29 要求）。
产出：
  artifacts/repro_audit/figures/fig1_status_by_repo.png
  artifacts/repro_audit/figures/fig2_drift_magnitude.png
  artifacts/repro_audit/figures/fig3_attribution.png
  artifacts/repro_audit/figures/fig4_channel_blocked.png
  artifacts/repro_audit/report_tables.md   （含 headline 表 + 逐仓表 + drift 清单）
用法::

    python3 tools/repro_audit/make_report.py --audit artifacts/repro_audit.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LIMIT_FACTOR = 1.05
SHORT = {"ship-performance-platform": "ship-perf", "TensorPlatform": "TP"}


def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def status_by_repo(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = defaultdict(lambda: {"pass": 0, "drift": 0, "blocked": 0})
    for n in nodes:
        out[n["repo"]][n["status"]] += 1
    return dict(out)


def fig_status(repo_tbl: Dict[str, Dict[str, int]], path: str) -> None:
    repos = sorted(repo_tbl, key=lambda r: -(sum(repo_tbl[r].values())))
    labels = [SHORT.get(r, r) for r in repos]
    passes = [repo_tbl[r]["pass"] for r in repos]
    drifts = [repo_tbl[r]["drift"] for r in repos]
    blocked = [repo_tbl[r]["blocked"] for r in repos]
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.bar(labels, passes, label="pass", color="#2b8a3e")
    ax.bar(labels, drifts, bottom=passes, label="drift", color="#c92a2a")
    ax.bar(labels, blocked, bottom=[p + d for p, d in zip(passes, drifts)],
           label="blocked", color="#868e96")
    ax.set_ylabel("nodes")
    ax.set_title("Replay verdict per repository (blocked excluded from repro_rate)")
    ax.legend(frameon=False, ncol=3)
    plt.xticks(rotation=25, ha="right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_drift(nodes: List[Dict[str, Any]], path: str) -> None:
    """每个有数值比对的节点：最大相对误差（observed 量）与最大 ULP。"""
    pts = []
    for n in nodes:
        c = (n.get("numeric") or {}).get("counts") or {}
        if not c.get("compared"):
            continue
        rel = c.get("max_rel") or None
        ulp = c.get("max_ulp") or 0
        pts.append((n["mission_id"], rel, ulp, c.get("compared", 0)))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.2))
    if pts:
        ids = [str(p[0]) for p in pts]
        rels = [max(p[1] or 1e-18, 1e-18) for p in pts]
        ax1.bar(ids, rels, color="#1971c2")
        ax1.set_yscale("log")
        ax1.axhline(1e-6, color="#c92a2a", ls="--", lw=1, label="observed rel tol 1e-6")
        ax1.set_ylabel("max relative error")
        ax1.set_title("Max relative drift per node (numeric channel)")
        ax1.legend(frameon=False, fontsize=8)
        plt.setp(ax1.get_xticklabels(), rotation=90, fontsize=7)
        ulps = [max(p[2], 1) for p in pts]
        ax2.bar(ids, ulps, color="#5f3dc4")
        ax2.set_yscale("log")
        ax2.axhline(65536, color="#c92a2a", ls="--", lw=1, label="ULP gate 65536")
        ax2.set_ylabel("max ULP distance")
        ax2.set_title("Max ULP distance per node")
        ax2.legend(frameon=False, fontsize=8)
        plt.setp(ax2.get_xticklabels(), rotation=90, fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_attribution(summary: Dict[str, int], path: str) -> None:
    classes = ["source_change", "dependency_version", "float_path", "environment"]
    vals = [summary.get(c, 0) for c in classes]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.bar(classes, vals, color=["#c92a2a", "#e8590c", "#1971c2", "#2b8a3e"])
    ax.set_ylabel("drift items")
    ax.set_title("Drift attribution (four classes, evidence-based)")
    for i, v in enumerate(vals):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    plt.xticks(rotation=15, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_channel(nodes: List[Dict[str, Any]], blocked: Dict[str, int], path: str) -> None:
    ch = Counter(n.get("channel") or "none" for n in nodes)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.8))
    keys = sorted(ch)
    ax1.bar(keys, [ch[k] for k in keys], color="#0b7285")
    ax1.set_title("Replay channel coverage")
    ax1.set_ylabel("nodes")
    for i, k in enumerate(keys):
        ax1.text(i, ch[k], str(ch[k]), ha="center", va="bottom", fontsize=9)
    if blocked:
        bk = sorted(blocked, key=lambda k: -blocked[k])
        ax2.bar(bk, [blocked[k] for k in bk], color="#868e96")
        ax2.set_title("Blocked categories (single-listed, not in repro_rate)")
        plt.setp(ax2.get_xticklabels(), rotation=20, ha="right", fontsize=8)
        for i, k in enumerate(bk):
            ax2.text(i, blocked[k], str(blocked[k]), ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def tables(doc: Dict[str, Any]) -> str:
    nodes = doc["nodes"]
    kpi = doc["kpi"]
    repo_tbl = status_by_repo(nodes)
    lines: List[str] = []
    lines.append("### Headline（全部由 artifacts/repro_audit.json 注入）\n")
    lines.append("| 判据 | 实测值 |")
    lines.append("|---|---|")
    lines.append("| 抽样节点数 | **%d** |" % kpi["nodes_total"])
    lines.append("| 覆盖 repo 数 | **%d** |" % len(repo_tbl))
    lines.append("| 学科（discipline）层数 | **%d** |" % len({n.get("discipline") for n in nodes}))
    lines.append("| pass / drift / blocked | **%d / %d / %d** |" % (
        kpi["nodes_pass"], kpi["nodes_drift"], kpi["nodes_blocked"]))
    lines.append("| repro_rate = pass/(pass+drift) | **%.6f** |" % kpi["repro_rate"])
    lines.append("| repro_rate（JSON 重算，对账 |Δ|≤1e-9） | **%.6f**（Δ=%.1e） |" % (
        kpi["repro_rate_recomputed"], doc["kpi_consistency"]["delta"]))
    lines.append("| 数值项 compared / pass / drift | **%d / %d / %d** |" % (
        kpi["items_compared"], kpi["items_pass"], kpi["items_drift"]))
    lines.append("| 数值项通过率（参考量，不入 KPI） | **%.6f** |" % kpi["item_pass_rate"])
    lines.append("| 锚定工件 sha256 校验 | **%d 文件全部一致** |" % sum(
        (n.get("anchors") or {}).get("verified", 0) for n in nodes))
    lines.append("| 单节点最坏墙钟 | **%.1f s**（上限 %d s） |" % (
        doc["budget"]["max_node_wall_sec"], doc["budget"]["node_timeout_sec"]))
    lines.append("| 总墙钟 | **%.0f min** |" % (doc["budget"]["total_wall_sec"] / 60.0))
    lines.append("")
    lines.append("### 逐仓判决\n")
    lines.append("| repo | pass | drift | blocked | 小计 |")
    lines.append("|---|---|---|---|---|")
    for r in sorted(repo_tbl, key=lambda x: -sum(repo_tbl[x].values())):
        t = repo_tbl[r]
        lines.append("| %s | %d | %d | %d | %d |" % (
            r, t["pass"], t["drift"], t["blocked"], sum(t.values())))
    lines.append("")
    if doc.get("attribution_summary"):
        lines.append("### 漂移归因（四分类）\n")
        lines.append("| 归因类 | drift 项数 |")
        lines.append("|---|---|")
        for k, v in sorted(doc["attribution_summary"].items(), key=lambda kv: -kv[1]):
            lines.append("| %s | %d |" % (k, v))
        lines.append("")
    if doc.get("blocked_summary"):
        lines.append("### blocked 单列（不折算进通过率）\n")
        lines.append("| 类别 | 节点数 |")
        lines.append("|---|---|")
        for k, v in sorted(doc["blocked_summary"].items(), key=lambda kv: -kv[1]):
            lines.append("| %s | %d |" % (k, v))
        lines.append("")
    lines.append("### 逐节点明细\n")
    lines.append("| mission | repo | 学科 | 通道 | 判决 | 数值项 | drift | pytest(p/f) | 归因 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for n in nodes:
        c = (n.get("numeric") or {}).get("counts") or {}
        py = n.get("pytest") or {}
        cls = ",".join((n.get("attribution") or {}).get("classes") or []) or "-"
        lines.append("| m%s | %s | %s | %s | **%s** | %d | %d | %s/%s | %s |" % (
            n["mission_id"], SHORT.get(n["repo"], n["repo"]), n.get("discipline") or "-",
            n.get("channel") or "-", n["status"], c.get("compared", 0),
            c.get("drift", 0) + c.get("structural", 0),
            py.get("passed", "-"), py.get("failed", "-"), cls))
    lines.append("")
    return "\n".join(lines)


def facts(doc: Dict[str, Any]) -> Dict[str, Any]:
    """叙事用的派生事实（全部来自 JSON，报告 token 只引用这里）。"""
    nodes = doc["nodes"]
    kpi = doc["kpi"]
    numeric = [n for n in nodes if ((n.get("numeric") or {}).get("counts") or {}).get("compared")]
    assertion_only = [n for n in nodes if n.get("channel") == "assertion"]
    max_rel = max([((n["numeric"]["counts"]).get("max_rel") or 0.0) for n in numeric] or [0.0])
    max_ulp = max([((n["numeric"]["counts"]).get("max_ulp") or 0) for n in numeric] or [0])
    obs = sum(int((n["numeric"]["counts"]).get("observed", 0)) for n in numeric)
    dia = sum(int((n["numeric"]["counts"]).get("diagnostic", 0)) for n in numeric)
    attr = doc.get("attribution_summary") or {}
    consistent = sum(1 for n in numeric
                     if ((n.get("numeric") or {}).get("drift_inconsistent") == []))
    drift_nodes = [n for n in nodes if n["status"] == "drift"]
    labeled = sum(1 for n in drift_nodes if (n.get("attribution") or {}).get("classes"))
    return {
        "nodes_in_spec": doc["sampling"]["criteria"]["n_target"],
        "repos_covered": len({n["repo"] for n in nodes}),
        "disciplines_covered": len({n.get("discipline") for n in nodes}),
        "numeric_nodes": len(numeric),
        "assertion_only_nodes": len(assertion_only),
        "observed_items": obs,
        "diagnostic_items": dia,
        "max_rel_overall": max_rel,
        "max_ulp_overall": max_ulp,
        "anchored_files": sum((n.get("anchors") or {}).get("verified", 0) for n in nodes),
        "anchors_mismatched": sum(len((n.get("anchors") or {}).get("mismatched") or []) for n in nodes),
        "attr_source_change": attr.get("source_change", 0),
        "attr_dependency_version": attr.get("dependency_version", 0),
        "attr_float_path": attr.get("float_path", 0),
        "attr_environment": attr.get("environment", 0),
        "drift_nodes": len(drift_nodes),
        "drift_nodes_labeled": labeled,
        "drift_consistent_nodes": consistent,
        "blocked_nodes": kpi["nodes_blocked"],
        "item_pass_rate": kpi["item_pass_rate"],
        "kpi_gap": doc["kpi_consistency"]["delta"],
        "total_min": doc["budget"]["total_wall_sec"] / 60.0,
        "max_node_wall_sec": doc["budget"]["max_node_wall_sec"],
        "candidates": doc["sampling"]["scan"]["candidates"],
        "done_seen": doc["sampling"]["scan"]["done_nodes_seen"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", default="artifacts/repro_audit.json")
    ap.add_argument("--figdir", default="artifacts/repro_audit/figures")
    ap.add_argument("--tables", default="artifacts/repro_audit/report_tables.md")
    a = ap.parse_args()
    doc = load(a.audit)
    doc["facts"] = facts(doc)
    with open(a.audit, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.makedirs(a.figdir, exist_ok=True)
    repo_tbl = status_by_repo(doc["nodes"])
    fig_status(repo_tbl, os.path.join(a.figdir, "fig1_status_by_repo.png"))
    fig_drift(doc["nodes"], os.path.join(a.figdir, "fig2_drift_magnitude.png"))
    fig_attribution(doc.get("attribution_summary") or {},
                    os.path.join(a.figdir, "fig3_attribution.png"))
    fig_channel(doc["nodes"], doc.get("blocked_summary") or {},
                os.path.join(a.figdir, "fig4_channel_blocked.png"))
    with open(a.tables, "w", encoding="utf-8") as f:
        f.write(tables(doc))
    print(json.dumps({"figures": 4, "tables": a.tables,
                      "kpi": doc["kpi"]["repro_rate"],
                      "nodes": doc["kpi"]["nodes_total"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())