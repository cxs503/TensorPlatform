#!/usr/bin/env python3
"""p9044w5_n08_campaign_report —— 战役级综合报告装配器（N08 集成门节点的交付物生成器）。

输入：三份 ``campaign_integration_audit`` 报告（本战役 / 父战役 / 合成正对照）+ pytest 证据 JSON。
输出：``campaign_integration_report.json``（机器可裁）+ ``fig4_campaign_comparison.png``（对照图）。

纪律
----
* **零手抄**：报告里每个数字都来自输入的审计 JSON / pytest JSON，本脚本不含结论常数；
* **零领域语义**：节点标签取 ``ext_id``，仓名取 DB 字段；脚本内无学科词条；
* **honest FAIL 不可覆盖**：``gate_summary.*.failed`` 与 ``honest_fail.undocumented`` 原样透传，
  不因"报告要好看"而过滤；
* 确定性：不取 wall clock；输出按 key 排序。

用法::

    python3 tools/p9044w5_n08_campaign_report.py \
        --audit artifacts/p9044w5_n08/integration_audit_plan9044.json \
        --audit artifacts/p9044w5_n08/integration_audit_plan152.json \
        --control artifacts/p9044w5_n08/control_audit.json \
        --pytest artifacts/p9044w5_n08/pytest_summary.json \
        --inputs artifacts/p9044w5_n08/audit_inputs_plan9044.json \
        --out artifacts/p9044w5_n08/campaign_integration_report.json \
        --fig-dir artifacts/p9044w5_n08/figures
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "tp.campaign.integration_report/v1"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def node_matrix(plan_audit: Mapping[str, Any],
                inputs: Mapping[str, Any] | None = None) -> list[dict]:
    """八节点能力矩阵：审计行（7 个 peer）+ 自身集成门节点（scope=self，未自审）。"""
    rows = []
    for r in plan_audit.get("nodes") or []:
        rows.append({"scope": "peer", **dict(r)})
    excluded = plan_audit.get("excluded_node_ids") or []
    if inputs:
        by_id = {int(n["id"]): n for n in inputs.get("nodes") or [] if n.get("id") is not None}
        for nid in excluded:
            node = by_id.get(int(nid), {})
            meta = node.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta or "{}")
            rows.append({
                "scope": "self",
                "node_id": nid,
                "ext_id": meta.get("ext_id"),
                "repo": meta.get("repo"),
                "mission_id": node.get("mission_id"),
                "node_status": node.get("status"),
                "mission_status": None,
                "class": "self_deliverable",
                "class_reason": "excluded_from_peer_audit_owning_this_gate",
                "n_criteria": len(meta.get("acceptance_criteria") or []),
                "n_passed": 0,
                "n_failed": 0,
                "n_unproven": 0,
                "worst_rel_error": None,
                "band": "unmeasured",
                "capability_ids": meta.get("capability_ids") or [],
                "has_report": False,
            })
    rows.sort(key=lambda r: str(r.get("ext_id") or ""))
    return rows


def build(audits: Sequence[Mapping[str, Any]], control: Mapping[str, Any] | None,
          pytest_summary: Mapping[str, Any] | None, inputs: Mapping[str, Any] | None,
          plan_id: int | None = None) -> dict:
    primary = next(a for a in audits if plan_id is None or int(a.get("plan_id")) == int(plan_id))
    gate_summary = {}
    for a in audits:
        gate_summary[str(a.get("plan_id"))] = {
            "verdict": a.get("verdict"),
            "n_nodes_audited": a.get("n_nodes_audited"),
            "classes": a.get("classes"),
            "failed": [c["id"] for c in a.get("criteria") or [] if not c["passed"]],
            "criteria": [{"id": c["id"], "measured": c["measured"], "threshold": c["threshold"],
                          "op": c["op"], "passed": c["passed"], "note": c["note"]}
                         for c in a.get("criteria") or []],
            "dag_ok": (a.get("dag") or {}).get("ok"),
            "ext_id_duplicates": (a.get("ext_ids") or {}).get("duplicates"),
            "ext_id_collisions": [c.get("ext_id") for c in (a.get("ext_ids") or {}).get("collisions") or []],
            "repo_violations": len((a.get("repos") or {}).get("violations") or []),
            "lineage_chain": [e.get("plan_id") for e in (a.get("lineage") or {}).get("chain") or []],
        }
    campaign_caps = [c for r in primary.get("nodes") or [] for c in (r.get("capability_ids") or [])]
    ledger = primary.get("ledger") or {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "campaign": {
            "plan_id": primary.get("plan_id"),
            "plan_name": primary.get("plan_name"),
            "parent_plan_id": ((primary.get("lineage") or {}).get("chain") or [{}])[1].get("plan_id")
            if len((primary.get("lineage") or {}).get("chain") or []) > 1 else None,
            "lineage_chain": [e.get("plan_id") for e in (primary.get("lineage") or {}).get("chain") or []],
            "lineage_ok": (primary.get("lineage") or {}).get("ok"),
        },
        "node_matrix": node_matrix(primary, inputs),
        "gate_summary": gate_summary,
        "control": {
            "plan_id": (control or {}).get("plan_id"),
            "verdict": (control or {}).get("verdict"),
            "classes": (control or {}).get("classes"),
            "purpose": "synthetic positive control: proves the gate can reach ready (not a constant red light)",
        },
        "precision": primary.get("precision"),
        "capability_ledger": {
            "n_ledger_entries": ledger.get("n_ledger_entries"),
            "n_required": ledger.get("n_required"),
            "n_covered": ledger.get("n_covered"),
            "coverage": ledger.get("coverage"),
            "campaign_capability_ids": sorted(set(campaign_caps)),
            "status": "honest_fail" if not ledger.get("ok") else "covered",
            "note": ledger.get("note"),
            "acceptance_target": "战役节点任务书要求「能力台账更新（8 条新能力条目）」",
            "measured_registered": len(set(campaign_caps)),
        },
        "honest_fail": primary.get("honest_fail"),
        "pytest": dict(pytest_summary or {}),
        "ext_ids": primary.get("ext_ids"),
        "repos": {"n_nodes": (primary.get("repos") or {}).get("n_nodes"),
                  "violations": (primary.get("repos") or {}).get("violations"),
                  "whitelist": (primary.get("repos") or {}).get("whitelist")},
        "dag": primary.get("dag"),
        "verdict": "ready" if (primary.get("verdict") == "ready"
                               and all(v["verdict"] == "ready" for v in gate_summary.values())
                               and (control or {}).get("verdict", "ready") == "ready"
                               and int((pytest_summary or {}).get("failed", 1)) == 0
                               and ledger.get("ok")) else "not_ready",
        "input_digest": _digest({"audits": audits, "control": control, "pytest": pytest_summary}),
    }
    return report


def fig_campaign_comparison(report: Mapping[str, Any], path: Path) -> dict:
    """战役级对照图：三场战役（本战役 / 父战役 / 合成正对照）的四分类分布 + 失败门数。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels, counts = [], {k: [] for k in ("pass", "honest_fail", "unproven", "fail")}
    failed_gates = []
    for key, summary in sorted((report.get("gate_summary") or {}).items()):
        labels.append(f"plan {key}")
        for klass in counts:
            counts[klass].append(int((summary.get("classes") or {}).get(klass, 0)))
        failed_gates.append(len(summary.get("failed") or []))
    if report.get("control"):
        labels.append("control (synthetic)")
        for klass in counts:
            counts[klass].append(int((report["control"].get("classes") or {}).get(klass, 0)))
        failed_gates.append(0 if report["control"].get("verdict") == "ready" else 1)

    colors = {"pass": "#2ca02c", "honest_fail": "#ff7f0e", "unproven": "#7f7f7f", "fail": "#d62728"}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.4), gridspec_kw={"width_ratios": [1.6, 1]})
    xs = list(range(len(labels)))
    bottom = [0] * len(labels)
    for klass, vals in counts.items():
        ax1.bar(xs, vals, bottom=bottom, color=colors[klass], label=klass)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax1.set_xticks(xs)
    ax1.set_xticklabels(labels, rotation=12, fontsize=9)
    ax1.set_ylabel("nodes")
    ax1.set_title("Outcome classes per audited plan", fontsize=10)
    ax1.legend(fontsize=8)
    bars = ax2.bar(xs, failed_gates, color="#d62728")
    for b, v in zip(bars, failed_gates):
        ax2.text(b.get_x() + b.get_width() / 2, v + 0.05, str(v), ha="center", fontsize=9)
    ax2.set_xticks(xs)
    ax2.set_xticklabels(labels, rotation=12, fontsize=9)
    ax2.set_ylabel("failed criteria (of 8)")
    ax2.set_title("Gate criteria failed per plan", fontsize=10)
    ax2.set_ylim(0, 9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"figure": path.name, "plans": labels, "failed_criteria": failed_gates}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="装配战役级综合报告")
    ap.add_argument("--audit", action="append", required=True)
    ap.add_argument("--control")
    ap.add_argument("--pytest")
    ap.add_argument("--inputs")
    ap.add_argument("--plan", type=int)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fig-dir")
    args = ap.parse_args(argv)

    audits = [load(p) for p in args.audit]
    control = load(args.control) if args.control else None
    pytest_summary = load(args.pytest) if args.pytest else None
    inputs = load(args.inputs) if args.inputs else None
    report = build(audits, control, pytest_summary, inputs, args.plan)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    if args.fig_dir:
        fig_dir = Path(args.fig_dir)
        fig_dir.mkdir(parents=True, exist_ok=True)
        manifest = fig_campaign_comparison(report, fig_dir / "fig4_campaign_comparison.png")
        (fig_dir / "figures_campaign.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "verdict": report["verdict"],
                      "digest": report["input_digest"]}, ensure_ascii=False))
    return 0 if report["verdict"] == "ready" else 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())