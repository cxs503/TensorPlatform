"""backlog 构建 + 覆盖率表 + 提案草案（dry-run，只出报告不派单）。

产物（默认 ``artifacts/backlog_extraction/``）
----------------------------------------------
* ``backlog.jsonl``        —— 去重后的欠账行，一行一条，schema 由 ``schema.py`` 约束
* ``coverage.json/.md``    —— 战役节点 × 报告 × 欠账条数的覆盖率表
* ``dedup_report.json``    —— 与 campaign_nodes / 历史提案池 / backlog 内部的近重判定
* ``proposal_drafts.json`` —— 由欠账自动生成的提案草案（dry-run）
* ``proposal_dry_run.md``  —— dry-run 结论（零重复、零派单）

硬纪律：本模块**不发任何写请求**。``--no-network`` 默认开启，且代码路径里没有任何
``POST``；派单只能由人来接线（任务书：「先 dry-run 只出报告不派单」）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from . import dedup
from .extract import extract_corpus, iter_corpus
from .schema import ROOT_CAUSE_ZH, DebtRow, dumps_row, validate_jsonl_row

DOMAIN_BY_REPO = {
    "TensorLBM": "阻力/湍流（LBM）",
    "TensorFEM": "结构/振动（FEM）",
    "TensorFVM": "阻力（FVM）",
    "TensorBEM": "耐波/水动力（BEM）",
    "TensorDEM": "冰区/散粒（DEM）",
    "TensorSPH": "砰击/自由面（SPH）",
    "TensorDG": "对流（DG）",
    "TensorCAD": "几何保真（CAD）",
    "TensorPlatform": "证据治理",
    "TensorPhysics": "科研流程/几何契约",
    "ship-performance-platform": "船舶总体性能",
}

DOMAIN_KEYWORDS = (
    ("阻力", "阻力"),
    ("推进", "推进"),
    ("耐波", "耐波"),
    ("操纵", "操纵"),
    ("稳性", "稳性"),
    ("结构", "结构"),
    ("振动", "振动噪声"),
    ("噪声", "振动噪声"),
    ("冰", "冰区"),
    ("几何", "几何保真"),
    ("CII", "绿色航行"),
    ("EEDI", "绿色航行"),
    ("证据", "证据治理"),
    ("台账", "证据治理"),
)


def infer_domain(row: DebtRow) -> str:
    blob = f"{row.case} {row.metric}"
    for key, domain in DOMAIN_KEYWORDS:
        if key in blob:
            return domain
    return DOMAIN_BY_REPO.get(row.repo, "通用")


def enrich_repo(rows: Iterable[DebtRow], nodes: list[dict]) -> list[DebtRow]:
    """把 ``unknown`` 仓归属按节点表/案例名前缀补齐（不改其它字段）。"""
    by_case = {}
    for node in nodes:
        ext = str(node.get("ext_id") or "")
        if ext:
            by_case[ext] = str(node.get("repo") or "")
    out = []
    for row in rows:
        if row.repo and row.repo != "unknown":
            out.append(row)
            continue
        repo = by_case.get(row.case, "")
        for known in DOMAIN_BY_REPO:
            if known.casefold() in (row.case + row.metric).casefold():
                repo = known
                break
        out.append(row if repo == row.repo else DebtRow(**{**row.__dict__, "repo": repo or "unknown"}))
    return out


def build_backlog(
    corpus: str | Path,
    outdir: str | Path,
    *,
    nodes_path: str | Path | None = None,
    proposals_root: str | Path | None = None,
) -> dict[str, Any]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    nodes_payload = {}
    if nodes_path and Path(nodes_path).is_file():
        nodes_payload = json.loads(Path(nodes_path).read_text(encoding="utf-8"))
    nodes = nodes_payload.get("nodes", nodes_payload) if isinstance(nodes_payload, dict) else nodes_payload
    nodes = nodes or []

    rows = enrich_repo(extract_corpus(corpus), nodes)
    kept, internal_pairs = dedup.dedupe_within(rows)

    targets = [dedup.Target.from_node(n) for n in nodes]
    if proposals_root:
        targets.extend(dedup.load_proposals(proposals_root))
    pairs = dedup.dedupe_against(kept, targets)

    status_by_row = {p["row"]: p for p in pairs}
    backlog: list[dict] = []
    for row in sorted(kept, key=lambda r: (r.repo, r.case, -(abs(r.error_pct or 0)), r.row_id)):
        payload = row.to_dict()
        hit = status_by_row.get(row.row_id)
        payload["dedup"] = (
            {"status": hit["decision"], "target": hit["target"], "similarity": hit["similarity"]}
            if hit
            else {"status": "novel", "target": None, "similarity": 0.0}
        )
        validate_jsonl_row(payload)
        backlog.append(payload)

    with (out / "backlog.jsonl").open("w", encoding="utf-8") as fh:
        for payload in backlog:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    dedup_report = {
        "n_rows": len(rows),
        "n_kept": len(kept),
        "internal_duplicates": internal_pairs,
        "external_matches": pairs,
        "targets": {"campaign_nodes": len(nodes), "proposal_pool": len(targets) - len(nodes)},
        "decision_counts": dict(_counts(p["decision"] for p in pairs)),
    }
    (out / "dedup_report.json").write_text(
        json.dumps(dedup_report, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    coverage = build_coverage(corpus, backlog, nodes)
    (out / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "coverage.md").write_text(render_coverage_md(coverage), encoding="utf-8")

    drafts, dry = build_proposal_drafts(backlog, targets)
    (out / "proposal_drafts.json").write_text(
        json.dumps({"proposals": drafts, "dry_run": dry}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (out / "proposal_dry_run.md").write_text(render_dry_run_md(dry, drafts), encoding="utf-8")
    return {
        "backlog": backlog,
        "dedup_report": dedup_report,
        "coverage": coverage,
        "drafts": drafts,
        "dry_run": dry,
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    from collections import Counter

    return Counter(values)


# ------------------------------------------------------------------ 覆盖率表
PLAN21_NODES = (
    ("IN20-R1", 5045),
    ("IN20-R2", 5046),
    ("IN20-R3", 5104),
    ("IN20-R4", 5048),
    ("IN20-R5", 5055),
    ("IN20-R6", 5049),
    ("IN20-R7", 5050),
    ("IN20-D1", 5051),
    ("IN20-D2", 5052),
    ("IN20-D3", 5053),
    ("IN20-D4", 5054),
    ("IN20-G21", 5177),
)


def build_coverage(corpus: str | Path, backlog: list[dict], nodes: list[dict]) -> dict[str, Any]:
    """plan-21（IN20，12 单）与 P21（plan 44，9 单）两波覆盖：报告是否入库 × 欠账条数。"""
    have: dict[str, dict] = {}
    for path, meta in iter_corpus(corpus):
        if meta.kind != "report":
            continue
        have[meta.mission_id] = {
            "file": meta.file,
            "title": meta.title,
            "chars": path.stat().st_size,
        }
    per_mission = defaultdict(list)
    for row in backlog:
        per_mission[str(row["source"]["mission_id"])].append(row)

    def wave(name: str, spec: Iterable[tuple[str, int]]) -> dict[str, Any]:
        nodes_out = []
        for node, mission in spec:
            rep = have.get(str(mission))
            rows = per_mission.get(str(mission), [])
            nodes_out.append(
                {
                    "node": node,
                    "mission_id": mission,
                    "report_present": bool(rep),
                    "report_file": (rep or {}).get("file"),
                    "debt_rows": len(rows),
                    "fallback_roots": sorted({r["rootcause"] for r in rows}),
                }
            )
        covered = sum(1 for n in nodes_out if n["report_present"])
        return {
            "wave": name,
            "nodes": nodes_out,
            "n_nodes": len(nodes_out),
            "n_reports": covered,
            "n_debt_rows": sum(n["debt_rows"] for n in nodes_out),
        }

    p21 = wave(
        "P21 / plan-44 multi_domain_verification",
        [
            ("P21-N01", 5345),
            ("P21-N02", 5346),
            ("P21-N03", 5347),
            ("P21-N04", 5348),
            ("P21-N05", 5349),
            ("P21-N06", 5350),
            ("P21-N07", 5351),
            ("P21-N08", 5352),
            ("P21-N09", 5353),
        ],
    )
    plan21 = wave("plan-21 / IN20 续航军", PLAN21_NODES)
    pending = defaultdict(int)
    for row in backlog:
        if row["source"]["kind"] == "pending_verdict":
            pending[row["source"]["file"].split("__")[-1]] += 1
    return {
        "waves": [plan21, p21],
        "pending_verdicts": dict(pending),
        "totals": {
            "reports_in_corpus": len(have),
            "debt_rows": len(backlog),
            "rows_in_missing_reports": 0,
        },
        "repos": dict(_counts(r["repo"] for r in backlog)),
        "rootcauses": dict(_counts(r["rootcause"] for r in backlog)),
    }


def render_coverage_md(cov: dict[str, Any]) -> str:
    lines = ["# 覆盖率表（战役节点 × 报告入库 × 欠账条数）", ""]
    for w in cov["waves"]:
        lines.append(
            f"## {w['wave']}：报告 {w['n_reports']}/{w['n_nodes']}，欠账 {w['n_debt_rows']} 条"
        )
        lines.append("")
        lines.append("| 节点 | mission | 报告入库 | 欠账行 | 根因分布 |")
        lines.append("|---|---|---|---|---|")
        for n in w["nodes"]:
            lines.append(
                "| {node} | m{mid} | {rep} | {debt} | {rc} |".format(
                    node=n["node"],
                    mid=n["mission_id"],
                    rep="✅" if n["report_present"] else "❌ 缺报",
                    debt=n["debt_rows"],
                    rc=", ".join(n["fallback_roots"]) or "—",
                )
            )
        lines.append("")
    lines.append("## pending 终态判词（未入库的求解器仓）")
    lines.append("")
    lines.append("| 判词文件 | 欠账行 |")
    lines.append("|---|---|")
    for name, cnt in sorted(cov["pending_verdicts"].items()):
        lines.append(f"| {name} | {cnt} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 提案草案
def build_proposal_drafts(
    backlog: list[dict], targets: list[dedup.Target]
) -> tuple[list[dict], dict[str, Any]]:
    drafts: list[dict] = []
    duplicates: list[dict] = []
    for payload in backlog:
        status = payload["dedup"]["status"]
        if status in ("duplicate", "already_delivered", "near"):
            continue
        row = _row_from_payload(payload)
        draft = {
            "title": f"[DEBT] {row.repo} · {row.case} · {row.metric[:40]} 再派（{ROOT_CAUSE_ZH[row.rootcause]}）",
            "repo": row.repo,
            "physics_domain": infer_domain(row),
            "why_frontier": (
                f"该欠账来自 {row.source.kind}={Path(row.source.file).name}:{row.source.line}，"
                f"当前判词 {row.verdict}，根因类 {row.rootcause}；提案池与战役节点中无同字段条目"
            ),
            "goal": (
                f"清偿 {row.case} 的「{row.metric}」欠账：误差 {row.error_pct}%（门槛 "
                f"{row.threshold_pct}%）。再派判据：{row.requeue_criterion}。"
                f"验收须给出可复算的误差表行与证据链（禁宽正则/禁手抄数字）。"
            ),
            "dependencies_hint": list(row.depends_on),
            "source_row": row.row_id,
            "rootcause": row.rootcause,
            "priority": _priority(row),
            "dispatch": "dry-run（不派单）",
        }
        draft_target = dedup.Target(
            key=draft["title"],
            label=draft["title"],
            repo=draft["repo"],
            case="",
            metric=draft["title"],
            source="draft",
            tokens=dedup.tokens(draft["title"] + " " + draft["goal"]),
        )
        worst = None
        for target in targets:
            res = dedup.compare(row, target)
            if res["decision"] in ("duplicate", "already_delivered") and (
                worst is None or res["confidence"] > worst["confidence"]
            ):
                worst = res
        if worst is not None:
            duplicates.append({"draft": draft["title"], "collides_with": worst})
            continue
        draft["self_similarity"] = round(dedup.jaccard(draft_target.tokens, draft_target.tokens), 4)
        drafts.append(draft)
    dry = {
        "n_backlog": len(backlog),
        "n_drafts": len(drafts),
        "n_suppressed_duplicates": len(duplicates),
        "suppressed": duplicates,
        "dispatch_attempted": False,
        "network_calls": 0,
        "verdict": "ZERO_DUPLICATE_PROPOSALS" if not duplicates else "DUPLICATES_FOUND",
    }
    return drafts, dry


def _row_from_payload(payload: dict) -> DebtRow:
    from .schema import Source

    src = payload["source"]
    return DebtRow(
        repo=payload["repo"],
        case=payload["case"],
        metric=payload["metric"],
        measured=payload["measured"],
        reference=payload["reference"],
        error_pct=payload["error_pct"],
        threshold_pct=payload["threshold_pct"],
        lt_3pct=payload["lt_3pct"],
        rootcause=payload["rootcause"],
        requeue_criterion=payload["requeue_criterion"],
        depends_on=tuple(payload["depends_on"]),
        verdict=payload["verdict"],
        source=Source(
            kind=src["kind"],
            file=src["file"],
            line=src["line"],
            report_id=src["report_id"],
            mission_id=src["mission_id"],
            repo=src["repo"],
            benchmark=src["benchmark"],
            node=src["node"],
        ),
        evidence=tuple(payload.get("evidence") or ()),
        raw={},
    )


def _priority(row: DebtRow) -> str:
    if row.rootcause in ("reference_missing", "reference_limited"):
        return "P1-参考治理"
    if row.rootcause == "implementation_defect":
        return "P2-实现修复"
    return "P3-网格/分辨率"


def render_dry_run_md(dry: dict[str, Any], drafts: list[dict]) -> str:
    lines = [
        "# 提案草案 dry-run（只出报告，不派单）",
        "",
        f"- backlog 行数：{dry['n_backlog']}",
        f"- 生成草案：{dry['n_drafts']}",
        f"- 因近重被抑制：{dry['n_suppressed_duplicates']}",
        f"- 派单尝试：{dry['dispatch_attempted']}；网络写请求：{dry['network_calls']}",
        f"- 结论：**{dry['verdict']}**",
        "",
        "| # | 标题 | 仓 | 优先级 | 来源行 |",
        "|---|---|---|---|---|",
    ]
    for i, d in enumerate(drafts, 1):
        lines.append(f"| {i} | {d['title'][:70]} | {d['repo']} | {d['priority']} | {d['source_row']} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="build FAIL/WITHHELD backlog (offline)")
    ap.add_argument("--corpus", default="artifacts/backlog_extraction/corpus")
    ap.add_argument("--out", default="artifacts/backlog_extraction")
    ap.add_argument("--nodes", default="artifacts/backlog_extraction/campaign_nodes.json")
    ap.add_argument("--proposals", default="/root/tp-agent/proposals")
    args = ap.parse_args(argv)
    res = build_backlog(args.corpus, args.out, nodes_path=args.nodes, proposals_root=args.proposals)
    print(
        f"[backlog] rows={len(res['backlog'])} drafts={len(res['drafts'])} "
        f"dry_run={res['dry_run']['verdict']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())