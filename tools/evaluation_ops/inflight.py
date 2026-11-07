"""in-flight 增量评价入口（IN38-N09 第 3 条）：finished 但无评价的 mission → 单行评价。

做什么
------
对**已完结（``status='succeeded'`` 且 ``finished_at`` 非空）**但没有有效评价记录
（无 ``confirmed``/``candidate``/``pending`` 行；只有 ``rejected`` 也算没有）的 mission，
按需生成**恰好一行**评价记录：先落 ``pending``（可审计「进了队列」），随即用机器证据扫描
就地判定（``pending`` 行原地升级，不新增行）。对已有 ``pending`` 行的 mission 则直接判定。

不做什么（纪律）
----------------
- **不碰调度面**：本模块不对 ``tensor_dev_missions`` 做任何写操作（不建单、不改状态、
  不改排队序）。``missions_digest()`` 提供前后指纹，测试用它证明零改动。
- **不预填等级**：待评价行等级留空；判定由 ``tools/outcome_evaluations.decide`` 的结构
  判据裁定，机器扫描上限 E2、S4/S5/S-N 只能落候选（§7.1）。
- **不批量刷库**：每次 cycle 有 ``limit`` 上限，避免「一次把所有存量都判了」这种不可
  复核的动作；剩余缺口如实计入 ``unrecorded``。

复用而非重写
------------
证据画像与等级规则直接调用 ``tools.backfill_outcome_evaluations.evidence_profile`` /
``build_payload`` / ``S_BY_CATEGORY``（同一套扫描规则，防止两处漂移）；本模块只负责
「哪些 mission 该进评价」与「写一行」。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools import outcome_evaluations as oe
from tools.backfill_outcome_evaluations import SCANNER, S_BY_CATEGORY, build_payload

from .cohorts import PENDING_STATUS, DECIDED_STATUSES, now_utc

SCHEMA = "eval-inflight/1"
TAB_MISSIONS = "tensor_dev_missions"
TAB_EVALS = "outcome_evaluations"
TAB_REPORTS = "tensor_reports"
TAB_DELTAS = "tensor_capability_deltas"

#: 评价器签名：mission 行 → payload（dict）或 None（证据不足，保持 pending）
Evaluator = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


def _rows(conn, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return [dict(r) for r in conn.execute(text(sql), dict(params)).mappings().all()]


def _scalar(conn, sql: str, params: Mapping[str, Any] | None = None) -> Any:
    from sqlalchemy import text

    row = conn.execute(text(sql), dict(params or {})).first()
    return None if row is None else row[0]


def has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect

    try:
        return bool(inspect(conn).has_table(name))
    except Exception:  # pragma: no cover - 引擎无 inspector 时退化为不可用
        return False


# ─────────────────────────── 选择入队对象 ───────────────────────────

def _mission_select(conn) -> str:
    """mission 行 + 报告/能力增量事实（缺表时置 NULL，保证 SQLite 夹具可用）。"""
    has_reports = has_table(conn, TAB_REPORTS)
    has_deltas = has_table(conn, TAB_DELTAS)
    rep_join = (
        f"\nLEFT JOIN (\n    SELECT mission_id, max(id) AS rep_id FROM {TAB_REPORTS}\n"
        f"    WHERE mission_id IS NOT NULL GROUP BY mission_id\n) rm ON rm.mission_id = m.id\n"
        f"LEFT JOIN {TAB_REPORTS} r ON r.id = COALESCE(m.report_id, rm.rep_id)\n"
        if has_reports else "\n")
    delta_join = (
        f"\nLEFT JOIN {TAB_DELTAS} d ON d.mission_id = m.id\n" if has_deltas else "\n")
    rep_cols = ("r.id AS rep_id, length(r.markdown) AS rep_chars, "
                "r.figures AS rep_figures, " if has_reports
                else "NULL AS rep_id, NULL AS rep_chars, NULL AS rep_figures, ")
    delta_cols = ("d.category AS delta_category, d.artifacts AS delta_artifacts, "
                  "d.evidence AS delta_evidence" if has_deltas
                  else "NULL AS delta_category, NULL AS delta_artifacts, NULL AS delta_evidence")
    return (
        "SELECT m.id, m.repo, m.organization_id, m.claimed_by, m.status, m.finished_at, "
        f"m.report_id, m.result, {rep_cols}{delta_cols}\n"
        f"FROM {TAB_MISSIONS} m{rep_join}{delta_join}"
    )


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    """跨引擎归一：jsonb 列在 PG 里是对象、在 SQLite 快照里是 TEXT；统一成计数/对象。"""
    figs = row.get("rep_figures")
    if isinstance(figs, str):
        try:
            figs = json.loads(figs)
        except (TypeError, ValueError):
            figs = None
    if isinstance(figs, list):
        row["rep_figures"] = len(figs)
    result = row.get("result")
    if isinstance(result, str):
        try:
            row["result"] = json.loads(result)
        except (TypeError, ValueError):
            row["result"] = {}
    for key in ("delta_artifacts", "delta_evidence"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except (TypeError, ValueError):
                pass
    return row


def plan(conn, *, repo: str | None = None, limit: int = 0,
         include_pending: bool = True) -> dict[str, Any]:
    """算出本轮该评价谁：``unrecorded``（无有效记录）与 ``pending``（已有待评价行）。"""
    base = _mission_select(conn)
    params: dict[str, Any] = {}
    repo_clause = ""
    if repo:
        repo_clause = "AND m.repo = :repo\n"
        params["repo"] = repo

    unrecorded_sql = (
        f"{base}WHERE m.status = 'succeeded' AND m.finished_at IS NOT NULL\n{repo_clause}"
        "  AND NOT EXISTS (\n"
        f"    SELECT 1 FROM {TAB_EVALS} e WHERE e.mission_id = m.id AND e.status <> 'rejected'\n"
        "  )\n"
        "ORDER BY m.finished_at DESC, m.id DESC\n"
    )
    pending_sql = (
        f"{base}WHERE m.status = 'succeeded' AND m.finished_at IS NOT NULL\n{repo_clause}"
        f"  AND EXISTS (SELECT 1 FROM {TAB_EVALS} e2 WHERE e2.mission_id = m.id "
        f"AND e2.status = '{PENDING_STATUS}')\n"
        "ORDER BY m.finished_at ASC, m.id ASC\n"
    )

    unrecorded = [_normalize(row) for row in _rows(conn, unrecorded_sql, params)]
    pending = [_normalize(row) for row in _rows(conn, pending_sql, params)] if include_pending else []
    if limit and limit > 0:
        unrecorded = unrecorded[: int(limit)]

    return {
        "schema": SCHEMA,
        "as_of": now_utc(),
        "repo": repo,
        "limit": int(limit or 0),
        "unrecorded": unrecorded,
        "pending": pending,
        "counts": {
            "unrecorded_available": len(_rows(conn, unrecorded_sql, params)),
            "unrecorded_selected": len(unrecorded),
            "pending_available": len(pending),
        },
        "pending_rows_total": int(_scalar(
            conn, f"SELECT count(*) FROM {TAB_EVALS} WHERE status = :s", {"s": PENDING_STATUS}) or 0),
    }


# ─────────────────────────── 写入 ───────────────────────────

#: 「不是结论，只是还没证据」的判据码：命中时不写 rejected 行，保持 pending
NOT_READY_CODES: tuple[str, ...] = ("E0_NOT_REGISTRABLE",)

#: mission.result 里可当「平台事实」的键（平台在验收时写下的自述输出，非报告、非独立复算）
PLATFORM_FACT_KEYS: tuple[tuple[str, str], ...] = (
    ("checks", "run"),
    ("evidence", "run"),
    ("git_status", "commit"),
    ("on_disk_changes", "artifact"),
    ("accepted", "run"),
)

PLATFORM_FACT_BOUNDARY = (
    "边界：等级只依据平台自有事实（mission.result 记录的验收输出/提交状态），"
    "无已发布报告、无能力增量登记、未复跑代码、未做独立复算，也不跨仓库/版本外推；"
    "E 级封顶 E1（平台事实），S 级按执行性成果取下限 S1。"
)


def platform_fact_refs(mission: Mapping[str, Any]) -> list[dict[str, str]]:
    """mission.result 中真实存在的字段 → 证据引用（没有的字段不编造引用）。"""
    result = mission.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            result = {}
    if not isinstance(result, Mapping):
        return []
    refs: list[dict[str, str]] = []
    for key, kind in PLATFORM_FACT_KEYS:
        value = result.get(key)
        if value in (None, "", [], {}):
            continue
        refs.append({"kind": kind, "ref": f"mission:{int(mission['id'])}#result.{key}"})
    return refs


def default_evaluator(mission: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """机器证据扫描（复用 backfill 的画像/等级规则）；证据不足返回 None（保持 pending）。

    E0（无任何证据）不是「评价为否弃」，而是「还不能评」：返回 None，让记录留在 pending，
    下一轮 still 会被计划器选中，而不是写一条 rejected 把成果钉死。
    """
    payload = build_payload(dict(mission))
    if not payload:
        return None
    if str(payload.get("e_level") or "").upper() != "E0":
        return payload

    # 无报告、无能力增量 → E0；但若 mission.result 有平台事实，按「仅有平台事实 → E1」登记
    facts = platform_fact_refs(mission)
    if not facts:
        return None
    n = len(facts)
    return {
        "artifact_id": oe.artifact_id_for_mission(mission["id"]),
        "mission_id": int(mission["id"]),
        "contributor": mission.get("claimed_by") or "unattributed",
        "grade": "S1",
        "e_level": "E1",
        "grade_reason": (
            f"规则化扫描（§7.1 结构判据，非人工/同行评审）：mission {mission['id']} 于 "
            f"{mission.get('finished_at')} 完结，贡献者 {mission.get('claimed_by') or 'unattributed'}；"
            f"无已发布报告、无能力增量登记，但 mission.result 留有 {n} 项平台事实"
            f"（{', '.join(r['ref'].split('#')[-1] for r in facts)}）；"
            "据此登记 E1（仅平台事实）、S1（执行性成果下限），未复跑代码、未做独立复算。"
        ),
        "evidence_refs": facts,
        "reviewer": SCANNER,
        "reviewer_kind": "auto_scan",
        "baseline_or_scope": "执行性成果：交付物与任务目标、验收判据逐条对应（无对照基线需求）",
        "boundary": PLATFORM_FACT_BOUNDARY,
        "repo": mission.get("repo"),
        "organization_id": mission.get("organization_id"),
    }


def apply(conn, plan_result: Mapping[str, Any], *, evaluator: Evaluator | None = default_evaluator,
          dry_run: bool = False, commit: bool = False) -> dict[str, Any]:
    """执行一轮：无记录 → 建 pending 单行；有证据 → 就地判定。幂等可重放。"""
    stats: dict[str, Any] = {
        "schema": SCHEMA,
        "queued": 0,
        "decided": 0,
        "rejected": 0,
        "undecidable": 0,
        "errors": [],
        "by_status": {},
        "by_grade": {},
        "dry_run": bool(dry_run),
        "items": [],
    }

    def bump(bucket: str, key: Any) -> None:
        d = stats[bucket]
        d[str(key)] = int(d.get(str(key), 0)) + 1

    work: list[tuple[str, Mapping[str, Any]]] = (
        [("unrecorded", m) for m in plan_result.get("unrecorded", [])]
        + [("pending", m) for m in plan_result.get("pending", [])]
    )

    for origin, mission in work:
        mid = int(mission["id"])
        if origin == "unrecorded":
            record = oe.pending_record_from_mission(mission)
            if not dry_run:
                try:
                    oe.store_record(conn, record)
                except Exception as exc:  # pragma: no cover - 约束冲突等
                    stats["errors"].append({"mission_id": mid, "stage": "queue", "error": str(exc)})
                    continue
            stats["queued"] += 1

        if evaluator is None:
            continue
        try:
            payload = evaluator(mission)
        except Exception as exc:  # pragma: no cover
            stats["errors"].append({"mission_id": mid, "stage": "evaluate", "error": str(exc)})
            continue
        if not payload:
            stats["undecidable"] += 1
            stats["items"].append({"mission_id": mid, "origin": origin, "status": "pending",
                                   "note": "证据不足，保持待评价"})
            continue

        verdict = oe.decide(payload)
        if verdict.status == "rejected" and set(NOT_READY_CODES) & set(verdict.codes):
            stats["undecidable"] += 1
            stats["items"].append({"mission_id": mid, "origin": origin, "status": "pending",
                                   "note": "证据级 E0（不可登记），保持待评价"})
            continue
        if not dry_run:
            try:
                record = oe.record_from_verdict(
                    payload, verdict, reason_source="auto_pending" if verdict.status == "candidate" else "rule_derived",
                    window_finished_at=None,
                )
                oe.store_record(conn, record)
            except Exception as exc:  # pragma: no cover
                stats["errors"].append({"mission_id": mid, "stage": "store", "error": str(exc)})
                continue
        if verdict.status == "rejected":
            stats["rejected"] += 1
        else:
            stats["decided"] += 1
        bump("by_status", verdict.status)
        bump("by_grade", verdict.grade)
        stats["items"].append({
            "mission_id": mid,
            "origin": origin,
            "status": verdict.status,
            "grade": verdict.grade,
            "e_level": verdict.e_level,
            "reason_codes": list(verdict.codes),
        })

    if commit and not dry_run:
        conn.commit()
    return stats


def refresh_counts(conn) -> dict[str, Any]:
    """队列/覆盖的即时读数（全部 SQL，可独立复算）。"""
    pending_rows = int(_scalar(
        conn, f"SELECT count(*) FROM {TAB_EVALS} WHERE status = :s", {"s": PENDING_STATUS}) or 0)
    by_status = {str(r["status"]): int(r["n"]) for r in _rows(
        conn, f"SELECT status, count(*) AS n FROM {TAB_EVALS} GROUP BY status", {})}
    total_finished = int(_scalar(
        conn, f"SELECT count(*) FROM {TAB_MISSIONS} WHERE status='succeeded' AND finished_at IS NOT NULL") or 0)
    decided_missions = int(_scalar(
        conn,
        f"SELECT count(DISTINCT mission_id) FROM {TAB_EVALS} "
        f"WHERE status IN ('confirmed','candidate') AND mission_id IS NOT NULL") or 0)
    return {
        "pending_rows": pending_rows,
        "by_status": by_status,
        "missions_finished": total_finished,
        "missions_decided": decided_missions,
        "missions_unrecorded": max(total_finished - int(_scalar(
            conn, f"SELECT count(DISTINCT mission_id) FROM {TAB_EVALS} "
                  f"WHERE status <> 'rejected' AND mission_id IS NOT NULL") or 0), 0),
        "coverage": (decided_missions / total_finished) if total_finished else None,
        "pending_share": (pending_rows / total_finished) if total_finished else None,
    }


def missions_digest(conn) -> str:
    """调度面指纹：本轮前后必须一致（证明没动过 dispatcher 的队列）。"""
    rows = _rows(conn, f"SELECT id, status, claimed_by, finished_at FROM {TAB_MISSIONS} ORDER BY id", {})
    blob = json.dumps([[r["id"], r["status"], r["claimed_by"], str(r["finished_at"])] for r in rows],
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def run_cycles(conn, *, cycles: int = 3, repo: str | None = None, limit: int = 50,
               evaluator: Evaluator | None = default_evaluator, dry_run: bool = False,
               commit: bool = True) -> dict[str, Any]:
    """连跑 ``cycles`` 轮，逐轮留痕（pending 下降幅度可机器裁）。"""
    before = refresh_counts(conn)
    digest_before = missions_digest(conn)
    history: list[dict[str, Any]] = []
    for i in range(1, int(cycles) + 1):
        p = plan(conn, repo=repo, limit=limit)
        stats = apply(conn, p, evaluator=evaluator, dry_run=dry_run, commit=commit)
        counts = refresh_counts(conn)
        history.append({
            "cycle": i,
            "planned": p["counts"],
            "applied": {k: v for k, v in stats.items() if k != "items"},
            "counts": counts,
        })
        if not dry_run and commit:
            conn.commit()
    after = refresh_counts(conn)
    digest_after = missions_digest(conn)

    def drop(before_v: float | None, after_v: float | None) -> float | None:
        if before_v in (None, 0) or after_v is None:
            return None
        return round((float(before_v) - float(after_v)) / float(before_v), 6)

    return {
        "schema": f"{SCHEMA}#cycles",
        "repo": repo,
        "cycles": int(cycles),
        "limit_per_cycle": int(limit),
        "before": before,
        "after": after,
        "history": history,
        "pending_drop_share": drop(before.get("pending_rows"), after.get("pending_rows")),
        "unrecorded_drop_share": drop(before.get("missions_unrecorded"), after.get("missions_unrecorded")),
        "dispatcher_untouched": digest_before == digest_after,
        "missions_digest": {"before": digest_before, "after": digest_after},
        "evaluator": getattr(evaluator, "__name__", str(evaluator)),
        "dry_run": bool(dry_run),
    }