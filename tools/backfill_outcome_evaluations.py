"""S×E 成果评价真实回填（AOG SPEC v1.2 §7.1）——从平台事实记录批量生成待评价
记录 + **规则化**等级判定，全部证据可追。

两步，互相独立、可重放：

1. ``--mode pending``：为某仓 succeeded 且无有效记录的 mission 生成 pending 记录
   （等级留空；同一 mission 重放不产生重复行）。
2. ``--mode evaluate``：对已生成的 pending 记录按**机器证据**给定 S/E 草案：
   - E 级：报告(report_id) + 能力增量(tensor_capability_deltas)双证据 → E2；
     仅其一 → E1；都没有 → E0（不可登记，保持 pending 不强行定级）。
   - S 级：能力增量类别 → ``new_capability``/``reusable_knowledge`` → S3，
     ``capability_extension``/``defect_fix`` 且有基线证据 → S2，否则 S1，
     ``falsified_hypothesis`` → S-N（否定轨），``no_substantive_delta`` → S1。
   - 审查者固定为 ``platform-evidence-scan``（``reviewer_kind='auto_scan'``）：
     这是**机器证据扫描**，不是人工/同行独立评审 → S4/S5/S-N 只能落 candidate。
   - 理由文本由模板程序化生成，内嵌 mission/报告/增量/工件计数，禁手抄。

**零领域语义**：本脚本不含学科词；等级语义全部来自 §7.1 结构判据。

用法::

    python tools/backfill_outcome_evaluations.py \\
        --dsn "postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql" \\
        --repo TensorPlatform --window 30 --mode all --out artifacts/<dir>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from tools import outcome_evaluations as oe  # noqa: E402

SCANNER = "platform-evidence-scan"
BOUNDARY = (
    "扫描边界：仅依据平台自有记录（mission 行、报告、能力增量、工件清单）判定，"
    "未复跑代码、未做独立复算，也未做同行评审；结论不跨仓库、不跨版本外推。"
)

S_BY_CATEGORY = {
    "new_capability": "S3",
    "reusable_knowledge": "S3",
    "capability_extension": "S2",
    "defect_fix": "S2",
    "falsified_hypothesis": "S-N",
    "no_substantive_delta": "S1",
}


def _json_blob(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def fetch_rows(conn, *, repo: str, window: int) -> list[dict[str, Any]]:
    """窗口内 succeeded mission + 报告 + 能力增量（一次 join 取齐）。"""
    sql = text(
        """
        SELECT m.id, m.organization_id, m.repo, m.status, m.claimed_by, m.finished_at,
               m.report_id, m.worker_role,
               r.id AS rep_id, r.title AS rep_title, length(r.markdown) AS rep_chars,
               jsonb_array_length(r.figures) AS rep_figures,
               d.category AS delta_category, d.summary AS delta_summary,
               d.reason AS delta_reason, d.artifacts AS delta_artifacts,
               d.evidence AS delta_evidence, d.reviewer AS delta_reviewer
        FROM (
            SELECT * FROM tensor_dev_missions
            WHERE repo = :repo AND status = 'succeeded'
            ORDER BY finished_at DESC NULLS LAST, id DESC
            LIMIT :window
        ) m
        -- 报告按 mission 关联（tensor_dev_missions.report_id 常为空：报告欠账机制），
        -- 取该 mission 最新一份报告作为「已发布报告」事实证据
        LEFT JOIN (
            SELECT mission_id, max(id) AS rep_id FROM tensor_reports
            WHERE mission_id IS NOT NULL GROUP BY mission_id
        ) rm ON rm.mission_id = m.id
        LEFT JOIN tensor_reports r ON r.id = COALESCE(m.report_id, rm.rep_id)
        LEFT JOIN tensor_capability_deltas d ON d.mission_id = m.id
        ORDER BY m.finished_at DESC NULLS LAST, m.id DESC
        """
    )
    return [dict(r) for r in conn.execute(sql, {"repo": repo, "window": int(window)}).mappings().all()]


def evidence_profile(row: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """从事实记录推证据级与证据引用清单（只引用真实存在的载体）。"""
    refs: list[dict[str, str]] = [{"kind": "api", "ref": f"/api/v1/tensor/missions/{row['id']}/provenance"}]
    has_report = bool(row.get("rep_id"))
    delta_present = bool(row.get("delta_category"))
    artifacts = _json_blob(row.get("delta_artifacts")) or []
    artifacts = artifacts if isinstance(artifacts, list) else []

    if has_report:
        refs.append({"kind": "report", "ref": str(row["rep_id"])})
    if delta_present:
        refs.append({"kind": "artifact", "ref": f"tensor_capability_deltas:mission={row['id']}"})
    for item in artifacts[:3]:
        value = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        if value:
            refs.append({"kind": "artifact", "ref": str(value)[:180]})

    if has_report and delta_present:
        e_level = "E2"   # 扫描上限：E3/E4 需外部复现/独立复算，机器扫描不可自证
    elif has_report or delta_present:
        e_level = "E1"
    else:
        e_level = "E0"
    return e_level, refs


def build_payload(row: dict[str, Any]) -> dict[str, Any]:
    """mission 行 → 评价 payload（等级规则化 + 理由程序化生成）。"""
    e_level, refs = evidence_profile(row)
    category = row.get("delta_category") or ""
    grade = S_BY_CATEGORY.get(category)

    artifacts = _json_blob(row.get("delta_artifacts")) or []
    artifacts = artifacts if isinstance(artifacts, list) else []
    evidence_blob = _json_blob(row.get("delta_evidence")) or {}
    has_baseline = bool(evidence_blob) or bool(artifacts)
    if grade in ("S2", "S4") and not has_baseline:
        grade = "S1"          # 无对照基线不得申报 S2（§7.1 规则 4）

    if grade is None:         # 无能力增量登记 → 只能按执行性成果处理
        grade = "S1" if e_level != "E0" else "S1"

    if grade == "S-N":
        baseline = (
            "否弃范围：被证伪假设见 tensor_capability_deltas.evidence 记录的复现条件；"
            "扫描未独立复算，故仅登记为候选"
        )
    elif grade in ("S2", "S4"):
        baseline = (
            "对照基线：能力增量登记的改动前/改动后证据（tensor_capability_deltas.evidence，"
            f"{len(evidence_blob) if isinstance(evidence_blob, dict) else 0} 个字段、"
            f"{len(artifacts)} 项工件）；未由扫描方独立复算"
        )
    elif grade in ("S3", "S5"):
        baseline = (
            "能力契约与适用方式：mission 目标 + 报告正文 + 能力增量工件清单"
            f"（{len(artifacts)} 项）；新颖性检索范围未由扫描方核对"
        )
    else:
        baseline = "执行性成果：交付物与任务目标、验收判据逐条对应（无对照基线需求）"

    if row.get("rep_id"):
        report_txt = (f"已发布 #{row['rep_id']}（正文 {row.get('rep_chars')} 字符，"
                      f"图 {row.get('rep_figures') if row.get('rep_figures') is not None else 0} 张）")
    else:
        report_txt = "未关联已发布报告"
    reason = (
        f"规则化扫描（§7.1 结构判据，非人工/同行评审）：mission {row['id']} 于 "
        f"{row.get('finished_at')} 完结，贡献者 {row.get('claimed_by') or 'unattributed'}；"
        f"报告 {report_txt}，能力增量类别 {category or '未登记'}（工件 {len(artifacts)} 项）；"
        f"据此判 E{e_level[1]}（{'报告+增量双证据' if e_level == 'E2' else '单一证据' if e_level == 'E1' else '无证据'}）、"
        f"{grade}（类别→等级映射 S_BY_CATEGORY；无增量申报时取执行性下限 S1）。"
        "审查者为机器证据扫描，非独立同行评审，故 S4/S5/S-N 只能落候选、E 级上限 E2。"
    )
    return {
        "artifact_id": oe.artifact_id_for_mission(row["id"]),
        "mission_id": int(row["id"]),
        "contributor": row.get("claimed_by") or "unattributed",
        "grade": grade,
        "e_level": e_level,
        "grade_reason": reason,
        "evidence_refs": refs,
        "reviewer": SCANNER,
        "reviewer_kind": "auto_scan",
        "baseline_or_scope": baseline,
        "boundary": BOUNDARY,
        "repo": row.get("repo"),
        "organization_id": row.get("organization_id"),
    }


def run(*, dsn: str, repo: str, window: int, mode: str, out: pathlib.Path | None,
        max_evaluations: int = 0) -> dict[str, Any]:
    engine = create_engine(dsn)
    report: dict[str, Any] = {"repo": repo, "window": window, "mode": mode,
                              "pending_created": 0, "evaluations_written": 0,
                              "by_status": {}, "by_grade": {}, "items": []}
    with engine.begin() as conn:
        for stmt in [s.strip() for s in oe.DDL.split(";") if s.strip()]:
            conn.execute(text(stmt))

    with engine.connect() as conn:
        rows = fetch_rows(conn, repo=repo, window=window)

    if mode in ("pending", "all"):
        with engine.begin() as conn:
            # 只对**统计窗口内**的 succeeded mission 补待评价记录（与分布统计同口径，
            # 因此可重放且不随重跑扩张）；窗口外的存量覆盖情况见 --mode pending 的
            # 全仓统计（repo_succeeded_total / covered_total）。
            sql = text(
                "WITH win AS ("
                "  SELECT m.id, m.organization_id, m.repo, m.status, m.claimed_by, m.finished_at "
                "  FROM tensor_dev_missions m WHERE m.repo=:repo AND m.status='succeeded' "
                "  ORDER BY m.finished_at DESC NULLS LAST, m.id DESC LIMIT :lim"
                ") "
                "SELECT w.* FROM win w WHERE NOT EXISTS ("
                "  SELECT 1 FROM outcome_evaluations e "
                "  WHERE e.mission_id = w.id AND e.status <> 'rejected') "
                "ORDER BY w.finished_at DESC NULLS LAST, w.id DESC"
            )
            insert = text(oe.insert_if_absent_sql())
            for row in conn.execute(sql, {"repo": repo, "lim": int(window)}).mappings().all():
                record = oe.pending_record_from_mission(dict(row))
                # 计数只认真的插入了行（幂等跳过不计）
                report["pending_created"] += int(conn.execute(insert, record).rowcount or 0)

    if mode in ("evaluate", "all"):
        selected = rows if not max_evaluations else rows[:max_evaluations]
        with engine.begin() as conn:
            for row in selected:
                payload = build_payload(row)
                verdict = oe.decide(payload)
                if verdict.status == "rejected":
                    report["items"].append({"mission_id": row["id"], "skipped": verdict.codes})
                    continue
                record = oe.record_from_verdict(
                    payload, verdict, reason_source="rule_derived",
                    window_finished_at=str(row.get("finished_at")),
                )
                oe.store_record(conn, record, commit=False)
                report["evaluations_written"] += 1
                report["by_status"][verdict.status] = report["by_status"].get(verdict.status, 0) + 1
                report["by_grade"][verdict.grade] = report["by_grade"].get(verdict.grade, 0) + 1
                report["items"].append({
                    "mission_id": row["id"],
                    "evaluation_id": record["evaluation_id"],
                    "grade": verdict.grade,
                    "e_level": verdict.e_level,
                    "status": verdict.status,
                    "reason_codes": verdict.codes,
                    "contributor": record["contributor"],
                })

    with engine.connect() as conn:
        stats = oe.summarize(conn, repo=repo, window=window)
    report["stats"] = {k: v for k, v in stats.items() if k != "sql"}
    report["stats_sql"] = stats["sql"]
    report["stats_sql_params"] = stats["sql_params"]
    report["stats_markdown"] = oe.render_markdown(stats)

    # 快照钉住窗口：平台是活的（mission 持续完结，窗口会滑动），
    # 因此把本次统计的窗口 mission id 与逐行判定一并落盘，并给出按 id 钉住的复算 SQL，
    # 任何人都能在任意时刻对同一窗口复算出同一张矩阵。
    with engine.connect() as conn:
        win_sql = text(
            "SELECT m.id FROM tensor_dev_missions m WHERE m.repo = :repo AND m.status = 'succeeded' "
            "ORDER BY m.finished_at DESC NULLS LAST, m.id DESC LIMIT :window"
        )
        window_ids = [int(r[0]) for r in conn.execute(win_sql, {"repo": repo, "window": int(window)})]
        id_list = ", ".join(str(i) for i in window_ids) or "NULL"
        pinned = text(
            "SELECT COALESCE(grade,'PENDING') AS grade, COALESCE(e_level,'PENDING') AS e_level, "
            "status, count(*) AS n FROM outcome_evaluations "
            f"WHERE mission_id IN ({id_list}) AND status <> 'rejected' "
            "GROUP BY 1,2,3 ORDER BY 1,2,3"
        )
        pinned_rows = [dict(r) for r in conn.execute(pinned).mappings().all()]
    with engine.connect() as conn:
        totals = conn.execute(
            text(
                "SELECT count(*) AS succeeded, "
                "(SELECT count(DISTINCT mission_id) FROM outcome_evaluations WHERE status <> 'rejected') AS covered "
                "FROM tensor_dev_missions WHERE repo=:repo AND status='succeeded'"
            ),
            {"repo": repo},
        ).mappings().first()
    report["repo_coverage"] = {k: int(v or 0) for k, v in dict(totals).items()}

    report["snapshot"] = {
        "generated_utc": oe.now_utc(),
        "window_mission_ids": window_ids,
        "pinned_matrix": pinned_rows,
        "note": "平台为活系统：窗口按 finished_at 滑动；本快照的复算请使用 stats_pinned.sql（按窗口 id 钉住）。",
    }

    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "stats_snapshot.json").write_text(
            json.dumps(report["snapshot"], ensure_ascii=False, indent=2), encoding="utf-8")
        id_list = ", ".join(str(i) for i in report["snapshot"]["window_mission_ids"]) or "NULL"
        (out / "stats_pinned.sql").write_text(
            "-- 按快照窗口 id 钉住的复算 SQL（窗口=" +
            str(len(report["snapshot"]["window_mission_ids"])) + " 单，生成于 " +
            report["snapshot"]["generated_utc"] + "）\n"
            "SELECT COALESCE(grade,'PENDING') AS grade, COALESCE(e_level,'PENDING') AS e_level,\n"
            "       status, count(*) AS n\n"
            "FROM outcome_evaluations\n"
            f"WHERE mission_id IN ({id_list}) AND status <> 'rejected'\n"
            "GROUP BY 1,2,3 ORDER BY 1,2,3;\n",
            encoding="utf-8")
        (out / "backfill_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (out / "stats_last_window.md").write_text(report["stats_markdown"], encoding="utf-8")
        (out / "stats.sql").write_text(
            "-- 可复算 SQL（params: repo / window / org；由 tools/outcome_evaluations.stats_queries 生成）\n"
            + "\n\n".join(f"-- [{name}]\n{sql};" for name, sql in report["stats_sql"].items())
            + f"\n\n-- params: {json.dumps(report['stats_sql_params'], ensure_ascii=False)}\n",
            encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S×E 成果评价回填（SPEC §7.1）")
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--repo", default="TensorPlatform")
    ap.add_argument("--window", type=int, default=oe.DEFAULT_WINDOW)
    ap.add_argument("--mode", choices=("pending", "evaluate", "all"), default="all")
    ap.add_argument("--max-evaluations", type=int, default=0, help="0=窗口内全部")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out) if args.out else None
    rep = run(dsn=args.dsn, repo=args.repo, window=args.window, mode=args.mode,
              out=out, max_evaluations=args.max_evaluations)
    print(json.dumps({k: rep[k] for k in
                      ("repo", "window", "mode", "pending_created", "evaluations_written",
                       "by_status", "by_grade")}, ensure_ascii=False, indent=2))
    print(rep["stats_markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())