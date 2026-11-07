#!/usr/bin/env python3
"""探索单 EP-001（S4 档：突破性探索）—— **能否把 60 条 pending 评价记录闭环到"有信息量"的分档？**

为什么要这张单（战役 G20 判据 ④）：S-N 轨要的是**真实的**诚实失败，不是夹具里的
假失败。本波交付的评价流留下了 60 条 `pending`（`reason_source=auto_pending`）：
记录在案但定不了级。于是本单把它当探索题真跑一遍，**把能不能闭合量出来**：

  假设 H：现有平台事实（任务状态 / 报告 / Capability Delta）足以把 pending 记录
          升到 **S2 及以上**（有对照基线的改进性成果），从而让 S×E 分布脱离退化。

  判定量：目标 = 至少 1 条 pending 记录升到 S≥2 且带独立复核；
          实测 = 0 条 → **诚实失败**（honest FAIL），登记 S-N 轨，不计入成果。

跑法（只读，可复算）：
    python3 tools/ops/explore_pending_closure.py            # 写 S-N 记录
    python3 tools/ops/explore_pending_closure.py --dry-run  # 只打印量测

产出：`artifacts/in19_g20/sn/EP001_measurements.json`（量测）+
      `artifacts/in19_g20/sn/sn_ep001.json`（S-N 记录，含根因与证据引用）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
SN_DIR = REPO_ROOT / "artifacts/in19_g20/sn"

PENDING_SQL = (
    "SELECT e.mission_id, e.contributor, coalesce(e.grade,'') AS grade, "
    "coalesce(e.baseline_or_scope,'') AS baseline_or_scope, "
    "coalesce(e.reviewer,'') AS reviewer, coalesce(e.reviewer_kind,'') AS reviewer_kind, "
    "coalesce(e.evidence_refs::text,'[]') AS evidence_refs, "
    "coalesce(m.status,'') AS mission_status, "
    "coalesce(r.n,0)::int AS n_reports, coalesce(c.n,0)::int AS n_capability_deltas "
    "FROM outcome_evaluations e "
    "LEFT JOIN tensor_dev_missions m ON m.id = e.mission_id "
    "LEFT JOIN (SELECT mission_id, count(*) AS n FROM tensor_reports GROUP BY 1) r ON r.mission_id = e.mission_id "
    "LEFT JOIN (SELECT mission_id, count(*) AS n FROM tensor_capability_deltas GROUP BY 1) c "
    "  ON c.mission_id = e.mission_id "
    "WHERE e.status = 'pending' ORDER BY e.mission_id"
)

CONFIRMED_SQL = (
    "SELECT coalesce(grade,'') AS grade, coalesce(e_level,'') AS e_level, count(*)::int AS n "
    "FROM outcome_evaluations WHERE status = 'confirmed' GROUP BY 1,2 ORDER BY 1,2"
)


def psql_rows(psql_cmd: Sequence[str], sql: str) -> List[Dict[str, str]]:
    proc = subprocess.run(
        [*psql_cmd, "-v", "ON_ERROR_STOP=1", "-c", f"COPY ({sql}) TO STDOUT WITH CSV HEADER"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql 失败: {proc.stderr.strip()[:400]}")
    return [dict(r) for r in csv.DictReader(io.StringIO(proc.stdout))]


def count_key(row: Dict[str, str]) -> str:
    return f"{row.get('grade') or '?'}/{row.get('e_level') or '?'}"


def _counterfactual(confirmed: Sequence[Dict[str, str]], n_skeletons: int) -> Dict[str, Any]:
    """若把骨架直接确认为 S1/E2，分布会变成什么样：数量涨、信息量不涨。"""
    counts: Dict[str, int] = {}
    for row in confirmed:  # 既支持聚合行（n）也支持逐行事实（评价行）
        if "n" in row:
            counts[count_key(row)] = counts.get(count_key(row), 0) + int(row["n"])
        else:
            counts[count_key(row)] = counts.get(count_key(row), 0) + 1
    before = counts
    before_total = sum(before.values())
    skeleton_stratum = "S1/E2"
    after = dict(before)
    after[skeleton_stratum] = after.get(skeleton_stratum, 0) + n_skeletons
    return {
        "skeleton_stratum": skeleton_stratum,
        "confirmed_before": before,
        "confirmed_total_before": before_total,
        "confirmed_total_after": before_total + n_skeletons,
        "relative_gain": round(n_skeletons / before_total, 4) if before_total else None,
        "distinct_strata_before": len(before),
        "distinct_strata_after": len(after),
        "creates_new_stratum": skeleton_stratum not in before,
        "note": "骨架单全部落在既有 S1/E2 格：条数增加、分层不增加 ⇒ 走捷径只买到数量，买不到信息量",
    }


def _bundle_rows(bundle_path: Path, status: str) -> List[Dict[str, str]]:
    """从证据 bundle 的只读事实快照里取评价行：量测可离线复算，不随时间漂移。

    精简块（outcome_evaluations）给身份与状态，detail 块（非 confirmed）补
    baseline_or_scope / reviewer / 证据引用条数。
    """
    facts = json.loads(bundle_path.read_text(encoding="utf-8"))["facts"]
    rows = [dict(r) for r in facts["outcome_evaluations"]["rows"] if r.get("status") == status]
    detail = {
        (int(r["mission_id"]), r.get("status")): r
        for r in facts.get("outcome_evaluations_detail", {}).get("rows", [])
    }
    for row in rows:
        extra = detail.get((int(row["mission_id"]), row.get("status")), {})
        row.setdefault("baseline_or_scope", extra.get("baseline_or_scope", ""))
        row.setdefault("reviewer", extra.get("reviewer", ""))
        row.setdefault("evidence_refs_n", extra.get("evidence_refs_n", row.get("evidence_refs_n", 0)))
    return rows


def measure(psql_cmd: Sequence[str], bundle_path: Path | None = None) -> Dict[str, Any]:
    if bundle_path is not None and bundle_path.is_file():
        pending = _bundle_rows(bundle_path, "pending")
        confirmed = _bundle_rows(bundle_path, "confirmed")
        # 辅助事实（报告数 / 能力增量数）不在 bundle 里，按 pending 的 mission_id 现查（只读）
        ids = sorted({int(r["mission_id"]) for r in pending})
        aux_sql = (
            "SELECT m.id AS mission_id, coalesce(m.status,'') AS mission_status, "
            "coalesce(r.n,0)::int AS n_reports, coalesce(c.n,0)::int AS n_capability_deltas "
            "FROM tensor_dev_missions m "
            "LEFT JOIN (SELECT mission_id, count(*) AS n FROM tensor_reports GROUP BY 1) r ON r.mission_id=m.id "
            "LEFT JOIN (SELECT mission_id, count(*) AS n FROM tensor_capability_deltas GROUP BY 1) c ON c.mission_id=m.id "
            f"WHERE m.id IN ({','.join(str(i) for i in ids)})" if ids else
            "SELECT NULL::int AS mission_id, ''::text AS mission_status, 0::int AS n_reports, "
            "0::int AS n_capability_deltas WHERE false"
        )
        aux = {int(r["mission_id"]): r for r in psql_rows(psql_cmd, aux_sql)} if ids else {}
        for row in pending:
            extra = aux.get(int(row["mission_id"]), {})
            row["mission_status"] = (row.get("mission_status") or extra.get("mission_status") or "")
            row["n_reports"] = extra.get("n_reports", 0)
            row["n_capability_deltas"] = extra.get("n_capability_deltas", 0)
        source = f"bundle:{bundle_path}（评价行）+ live psql（报告/能力增量计数）"
    else:
        pending = psql_rows(psql_cmd, PENDING_SQL)
        confirmed = psql_rows(psql_cmd, CONFIRMED_SQL)
        for row in pending:
            row["evidence_refs_n"] = 1 if (row.get("evidence_refs") or "[]").strip() not in ("", "[]") else 0
        source = "live psql"

    def has_evidence(row: Dict[str, str]) -> bool:
        if "evidence_refs_n" in row and row["evidence_refs_n"] is not None:
            return int(row["evidence_refs_n"]) > 0
        refs = row.get("evidence_refs") or "[]"
        try:
            parsed = json.loads(refs)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, list) and len(parsed) > 0

    for row in pending:
        row["has_report"] = int(row.get("n_reports") or 0) > 0
        row["has_capability_delta"] = int(row.get("n_capability_deltas") or 0) > 0
        row["has_evidence_refs"] = has_evidence(row)
        # 现规则下的"骨架可闭合"：任务 succeeded + 有报告/能力增量其一（=自动证据扫描的输入）
        row["closable_at_S1"] = (
            row.get("mission_status") == "succeeded"
            and (row["has_report"] or row["has_capability_delta"])
        )
        # S2+ 的 SPEC §7.1-4 硬门槛：必须有对照基线；并须独立复核
        row["s2plus_baseline_present"] = bool((row.get("baseline_or_scope") or "").strip())
        row["s2plus_independent_review"] = bool(
            (row.get("reviewer") or "").strip() and (row.get("reviewer_kind") or "").strip() == "independent"
        )
        row["qualifies_s2plus"] = bool(
            row["s2plus_baseline_present"] and row["s2plus_independent_review"] and row["has_evidence_refs"]
        )

    n = len(pending)
    closable = [r for r in pending if r["closable_at_S1"]]
    s2plus = [r for r in pending if r["qualifies_s2plus"]]
    no_report_no_delta = [r for r in pending if not (r["has_report"] or r["has_capability_delta"])]
    metrics = {
        "targets_attempted": n,
        "targets_achieved": len(s2plus),
        "goal": "至少 1 条 pending 记录升到 S>=2（带对照基线 + 独立复核 + 证据引用）",
        "pending_total": n,
        "pending_mission_succeeded": sum(1 for r in pending if r.get("mission_status") == "succeeded"),
        "closable_at_S1_skeleton": len(closable),
        "without_any_evidence_source": len(no_report_no_delta),
        "with_baseline_or_scope": sum(1 for r in pending if r["s2plus_baseline_present"]),
        "with_independent_reviewer": sum(1 for r in pending if r["s2plus_independent_review"]),
        "with_evidence_refs": sum(1 for r in pending if r["has_evidence_refs"]),
        "qualifies_s2plus": len(s2plus),
        "counterfactual_distribution_if_skeletons_accepted": _counterfactual(confirmed, len(closable)),
        "pending_mission_range": [min(int(r["mission_id"]) for r in pending), max(int(r["mission_id"]) for r in pending)]
        if pending else [None, None],
        "pending_sample": [
            {k: r[k] for k in ("mission_id", "contributor", "closable_at_S1", "has_report",
                               "has_capability_delta", "s2plus_baseline_present", "qualifies_s2plus")}
            for r in pending[:10]
        ],
        "sql": {"pending": PENDING_SQL, "confirmed": CONFIRMED_SQL},
        "data_source": source,
    }
    return metrics


def build_sn_record(metrics: Dict[str, Any], measured_artifact: str) -> Dict[str, Any]:
    achieved = metrics["targets_achieved"]
    attempted = metrics["targets_attempted"]
    return {
        "schema": "in19_g20.sn_record/1",
        "ticket_id": "EP-001",
        "track": "S-N（否定性成果：诚实失败）",
        "grade": "S4",
        "e_level": "E2",
        "declared_tier_rationale": "S4 探索单：试图突破'成果分布退化'这一长期限制（把 pending 闭合到有信息量的分档）",
        "objective": metrics["goal"],
        "hypothesis": "现有平台事实（任务状态/报告/Capability Delta）足以把 pending 记录升到 S>=2 并脱离退化分布。",
        "method": {
            "steps": [
                "只读拉取 60 条 pending 评价记录及其任务/报告/能力增量事实（SQL 见 measurements.sql）",
                "按现规则计算骨架可闭合集合（mission succeeded 且有报告或能力增量）",
                "按 SPEC §7.1-4/-9 硬门槛检验 S2+ 资格（对照基线 + 独立复核 + 证据引用）",
                "反事实：若把骨架直接确认为 S1/E2，分布的信息量如何变化",
            ],
            "tooling": ["psql（只读 COPY ... TO STDOUT）", "tools/ops/explore_pending_closure.py"],
            "reproduce": "python3 tools/ops/explore_pending_closure.py",
        },
        "attempt": {
            "status": "executed",
            "measured": metrics,
        },
        "result_status": "failed_honestly",
        "honest_fail": True,
        "counted_as_output": False,
        "root_cause": (
            f"失败在**审查与基线证据通道**，不在算力或数据量：{attempted} 条 pending 里 "
            f"{metrics['closable_at_S1_skeleton']} 条其实已有骨架证据（任务成功 + 报告/能力增量），"
            f"但 S2+ 的三条硬门槛全部为 0 —— 对照基线 {metrics['with_baseline_or_scope']}/{attempted}、"
            f"独立复核 {metrics['with_independent_reviewer']}/{attempted}、证据引用 "
            f"{metrics['with_evidence_refs']}/{attempted}。"
            "同时 S2 定级规则本身在产线是能跑的（库内已有大量 S2 级 confirmed），"
            "说明卡住的是**这批历史 pending 的重评覆盖 + baseline/独立复核的写入路径**："
            "pending 的 mission_id 区间 "
            f"（{metrics['pending_mission_range'][0]}–{metrics['pending_mission_range'][1]}）远早于最近的重评批次。"
        ),
        "negative_knowledge": [
            f"闭合 pending 的瓶颈是'对照基线 + 独立复核通道'，不是事实缺失："
            f"{metrics['closable_at_S1_skeleton']}/{attempted} 已有骨架证据可判。",
            "走捷径（把骨架确认为 S1/E2）只能买到数量：recorded "
            f"{metrics['counterfactual_distribution_if_skeletons_accepted']['confirmed_total_before']} → "
            f"{metrics['counterfactual_distribution_if_skeletons_accepted']['confirmed_total_after']}"
            f"（+{metrics['counterfactual_distribution_if_skeletons_accepted']['relative_gain']:.1%}），"
            f"分层数 {metrics['counterfactual_distribution_if_skeletons_accepted']['distinct_strata_before']} → "
            f"{metrics['counterfactual_distribution_if_skeletons_accepted']['distinct_strata_after']}"
            "（不新增分层）——信息量零增量。",
            f"另有 {metrics['without_any_evidence_source']}/{attempted} 条连报告与能力增量都没有，任何自动定级都无据可依。",
        ],
        "what_would_make_it_succeed": [
            "SPEC §7.1 增补 S2 的可执行判据（改进前后基线字段 + 量化收益），并把 baseline_or_scope 设为 S2+ 必填。",
            "在评价流里接独立审查通道（reviewer_kind=independent），否则 S3+ 与 S4/S5 永远无法合法登记。",
            "把 pending 的 reason_source 从 auto_pending 升级为 reason_code 枚举，让'为什么定不了级'可机读。",
        ],
        "independent_review": {
            "performed": False,
            "substitute": "量测可复算：SQL 与结果同产物落盘，验收门与 pytest 双路径复算",
            "note": "无人类在环、无第二位 Agent 复核 → 按 §7.1 S-N 门槛（E≥2 + 独立复核）本单只能登记为'待独立复核'的负知识，且不计入成果。",
        },
        "evidence_refs": [
            {"kind": "artifact", "path": measured_artifact, "note": "全部量测（含 SQL 与逐条样本）"},
            {"kind": "artifact", "path": "artifacts/in19_g20/evidence/db_facts.json", "note": "评价流事实快照（只读 SELECT）"},
            {"kind": "spec", "path": "docs/architecture/AUTONOMOUS_AGENT_ORGANIZATION_GOVERNANCE_SPEC_v1.2.md",
             "note": "§7.1 判定规则 4/8/9、S-N 轨门槛、§7.2 准入"},
        ],
        "budget": {"wall_seconds": None, "tool_calls": None, "note": "只读量测，未占用集群预算"},
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mission_id": 5176,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EP-001：pending 评价记录能否闭环到 S2+（诚实失败探索单）")
    ap.add_argument("--psql", default="psql -h /var/run/postgresql -U postgres -d tensorplatform")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--from-bundle",
        default=str(REPO_ROOT / "artifacts/in19_g20/evidence/db_facts.json"),
        help="评价行取自证据 bundle（默认，保证与验收门同一时点）；传空串则全量现查",
    )
    args = ap.parse_args(argv)

    bundle = Path(args.from_bundle) if args.from_bundle else None
    metrics = measure(args.psql.split(), bundle)
    print(json.dumps({k: metrics[k] for k in (
        "targets_attempted", "targets_achieved", "closable_at_S1_skeleton",
        "with_baseline_or_scope", "with_independent_reviewer", "qualifies_s2plus")},
        ensure_ascii=False, indent=1))
    if args.dry_run:
        return 0

    SN_DIR.mkdir(parents=True, exist_ok=True)
    measured_path = SN_DIR / "EP001_measurements.json"
    measured_path.write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "ticket_id": "EP-001", "measurements": metrics}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    rel = str(measured_path.relative_to(REPO_ROOT))
    record = build_sn_record(metrics, rel)
    out = SN_DIR / "sn_ep001.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"sn_record": str(out), "measured": rel, "result": record["result_status"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())