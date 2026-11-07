"""
四中心最小视图后端面（PR-10 首期）——Organization / Quality / Capability / Innovation。

设计约束（SPEC S3 域中立 + 零假数据）：

1. **单一真值载荷**：每个中心一个只读聚合端点，响应里的 ``metrics[]`` 每项都带
   ``source``（人类可读的 SQL 口径）与后端实测 ``value``。前端只渲染、不自己算、
   不自己造数——于是「页面上的数字」与「SQL 数出来的数字」是同一个数，可逐项对拍。
2. **零领域语义**：本模块不含任何领域词/领域常量。状态词、类别词来自平台契约；
   页面文案与夹具域名无关（夹具用合成名）。
3. **未实现面如实标注**：没有实体支撑的面不进图表占位，直接进 ``not_implemented[]``，
   前端渲染「未实现」徽标；代理指标必须显式声明 ``proxy_of``，不得冒充实体本体。
4. **org 隔离与既有 ``/tensor/*`` 端点同规**；全部只读。
5. **SQL 可移植**（PG 生产 + sqlite CI）：不用 ``FILTER``、不用 jsonb 运算符、
   不用 ``now()``/``interval``，聚合一律 ``SUM(CASE WHEN ...)``。

端点：
- GET /tensor/centers/organization  组织面：任务在册/会话/执行器舰队 + 战役实况
- GET /tensor/centers/quality       质量面：评价流（报告 QA）+ 质量门态（四道门）
- GET /tensor/centers/capability    能力面：能力台账（类别分布）+ 验收入账覆盖 + Delta 流水
- GET /tensor/centers/innovation    创新面：提案/吸收（实体未实现，代理面如实标注）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.tenant_access import is_admin
from api.domain.tensor_common import resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/centers", tags=["四中心"])

SCHEMA_VERSION = "1.0.0"

# 能力增量词表（与 api/tensor_capability.py 同一契约；此处只做展示顺序，
# 不新增词项——新增词项必须同时进契约文件，避免两侧词表漂移）。
CATEGORIES = (
    "new_capability",
    "capability_extension",
    "defect_fix",
    "reusable_knowledge",
    "falsified_hypothesis",
    "no_substantive_delta",
)

# 「吸收」口径：一次工作被判断为**留下可复用增量**的三类（新增能力 / 能力扩展 /
# 可复用知识）。证伪假设与纯缺陷修复不计入吸收，避免把「修了个 bug」算成创新吸收。
ABSORBING_CATEGORIES = ("new_capability", "capability_extension", "reusable_knowledge")

# 会话「在役」状态词（会话契约里代表运行中的取值）。
ACTIVE_SESSION_STATUSES = ("active", "claimed", "running", "in_progress")

MISSION_TERMINAL_OK = "succeeded"
MISSION_TERMINAL_BAD = "failed"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _org_scope(db: Session, user: dict) -> int:
    """与既有能力中心端点同规：先按用户绑定，再单租户兜底，无上下文即 404。"""
    org_id = resolved_org(None, user, db)
    if org_id is None and not is_admin(user):
        raise HTTPException(status_code=404, detail="no organization context")
    return org_id


def _metric(
    key: str,
    label: str,
    value: Any,
    source: str,
    unit: str | None = None,
    note: str | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "source": source,
        "note": note,
        "status": status,
    }


def _counts(db: Session, sql: str, params: dict[str, Any]) -> dict[str, int]:
    """GROUP BY 计数 → dict（键为 None 的归入 ``__null__``）。"""
    out: dict[str, int] = {}
    for row in db.execute(text(sql), params).fetchall():
        key = row[0]
        out["__null__" if key is None else str(key)] = int(row[1] or 0)
    return out


def _total(counts: dict[str, int]) -> int:
    return sum(counts.values())


def _num(row: Any, idx: int = 0) -> int:
    if row is None:
        return 0
    value = row[idx] if not hasattr(row, "_mapping") else list(row)[idx]
    return int(value or 0)


def _safe_count(db: Session, sql: str, params: dict[str, Any]) -> int | None:
    """容错计数：表/列在该库 schema 中不存在时返回 None（不伪造 0）。

    失败后必须 rollback——否则会话停留在 aborted 事务里，后续查询全部连锁失败
    （生产库 quality_check_results 无 organization_id 列，正是这条路径）。
    """
    try:
        return _num(db.execute(text(sql), params).first())
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _rows(db: Session, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


def _envelope(
    center: str,
    org_id: int,
    metrics: list[dict[str, Any]],
    *,
    lists: dict[str, Any] | None = None,
    gates: list[dict[str, Any]] | None = None,
    not_implemented: list[dict[str, Any]] | None = None,
    definitions: dict[str, str] | None = None,
    implemented: bool = True,
) -> dict[str, Any]:
    return {
        "center": center,
        "schema_version": SCHEMA_VERSION,
        "org_id": org_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "implemented": implemented,
        "metrics": metrics,
        "gates": gates or [],
        "lists": lists or {},
        "not_implemented": not_implemented or [],
        "definitions": definitions or {},
        "provenance": {
            "sources": sorted({m["source"] for m in metrics}),
            "metric_count": len(metrics),
            "data_origin": "platform_sql",  # 全部来自库内实测，无 mock/无种子常量
        },
    }


def _by_status_metrics(
    prefix: str,
    label_prefix: str,
    counts: dict[str, int],
    source: str,
    *,
    extra_keys: Iterable[str] = (),
    total_key: str | None = None,
) -> list[dict[str, Any]]:
    """状态分布 → 指标项（total + 每个状态一档；缺状态补 0，不隐藏）。"""
    metrics: list[dict[str, Any]] = []
    if total_key:
        metrics.append(_metric(total_key, f"{label_prefix}总数", _total(counts), source))
    for status in extra_keys:
        metrics.append(_metric(
            f"{prefix}_{status.replace('-', '_')}",
            f"{label_prefix}·{status}",
            counts.get(status, 0),
            source,
        ))
    return metrics


# --------------------------------------------------------------------------- #
# Organization —— 舰队 + 战役实况 + 任务在册
# --------------------------------------------------------------------------- #
@router.get("/organization", dependencies=[Depends(require_auth)])
def organization_center(db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    """组织面：执行器舰队 / Agent 会话 / 战役节点实况 / 任务在册。"""
    org = _org_scope(db, user)
    p = {"org": org}

    missions_by_status = _counts(db, """
        SELECT status, count(*) FROM tensor_dev_missions
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY status
    """, p)
    sessions_by_status = _counts(db, """
        SELECT status, count(*) FROM tensor_agent_sessions
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY status
    """, p)
    approvals_by_status = _counts(db, """
        SELECT status, count(*) FROM tensor_approval_requests
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY status
    """, p)
    executors_registered = _num(db.execute(text(
        "SELECT count(*) FROM tensor_executors"
    )).first())

    plans_by_status = _counts(db, """
        SELECT status, count(*) FROM campaign_plans
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY status
    """, p)
    # campaign_nodes / campaign_milestones 无 organization_id 列 —— org 隔离经
    # campaign_plans.organization_id 判定（无计划归属的孤儿节点不计入任何 org，
    # 生产库当前 182/182 均可归属，见 artifacts/m5023 对拍记录）。
    nodes_by_status = _counts(db, """
        SELECT n.status, count(*) FROM campaign_nodes n
        LEFT JOIN campaign_plans p ON p.id = n.plan_id
        WHERE (:org IS NULL OR p.organization_id = :org)
        GROUP BY n.status
    """, p)
    milestones_by_status = _counts(db, """
        SELECT m.status, count(*) FROM campaign_milestones m
        LEFT JOIN campaign_plans p ON p.id = m.plan_id
        WHERE (:org IS NULL OR p.organization_id = :org)
        GROUP BY m.status
    """, p)

    active_sessions = sum(v for k, v in sessions_by_status.items() if k in ACTIVE_SESSION_STATUSES)
    pending_approvals = approvals_by_status.get("pending", 0)

    metrics = [
        _metric("missions_total", "任务在册", _total(missions_by_status),
                "tensor_dev_missions: count(*) [org]"),
        _metric(f"missions_{MISSION_TERMINAL_OK}", "任务·验收通过",
                missions_by_status.get(MISSION_TERMINAL_OK, 0),
                f"tensor_dev_missions: count(*) where status='{MISSION_TERMINAL_OK}'"),
        _metric(f"missions_{MISSION_TERMINAL_BAD}", "任务·验收失败",
                missions_by_status.get(MISSION_TERMINAL_BAD, 0),
                f"tensor_dev_missions: count(*) where status='{MISSION_TERMINAL_BAD}'"),
    ]
    metrics.append(_metric(
        "missions_running", "任务·在跑", missions_by_status.get("running", 0),
        "tensor_dev_missions: count(*) where status='running'"))
    metrics.append(_metric(
        "sessions_total", "Agent 会话", _total(sessions_by_status),
        "tensor_agent_sessions: count(*) [org]"))
    metrics.append(_metric(
        "sessions_active", "会话来役",
        active_sessions,
        f"tensor_agent_sessions: count(*) where status in {ACTIVE_SESSION_STATUSES}",
        note="claimed/running/active 均计为在役；口径见 definitions.sessions_active"))
    metrics.append(_metric(
        "executors_registered", "执行器舰队（登记）", executors_registered,
        "tensor_executors: count(*)",
        note=None if executors_registered else "表中 0 行——舰队未登记，如实显示 0，不造节点"))
    metrics.append(_metric(
        "approvals_pending", "待人审裁定", pending_approvals,
        "tensor_approval_requests: count(*) where status='pending' [org]"))

    metrics.extend(_by_status_metrics(
        "campaign_plans", "战役计划", plans_by_status,
        "campaign_plans: group by status [org]",
        extra_keys=("active", "completed"), total_key="campaign_plans_total"))
    metrics.extend(_by_status_metrics(
        "campaign_nodes", "战役节点", nodes_by_status,
        "campaign_nodes join campaign_plans: group by status [org]",
        extra_keys=("in-flight", "blocked", "done"), total_key="campaign_nodes_total"))
    metrics.extend(_by_status_metrics(
        "campaign_milestones", "战役里程碑", milestones_by_status,
        "campaign_milestones join campaign_plans: group by status [org]",
        extra_keys=("in-flight", "done"), total_key="campaign_milestones_total"))

    blocked_nodes = _rows(db, """
        SELECT n.id, n.name, n.status, n.block_reason, n.consecutive_fails,
               n.reproduction_verdict, n.plan_id
        FROM campaign_nodes n
        LEFT JOIN campaign_plans p ON p.id = n.plan_id
        WHERE (:org IS NULL OR p.organization_id = :org)
          AND n.status = 'blocked'
        ORDER BY n.id DESC LIMIT 12
    """, p)
    inflight_nodes = _rows(db, """
        SELECT n.id, n.name, n.status, n.block_reason, n.consecutive_fails,
               n.reproduction_verdict, n.plan_id
        FROM campaign_nodes n
        LEFT JOIN campaign_plans p ON p.id = n.plan_id
        WHERE (:org IS NULL OR p.organization_id = :org)
          AND n.status = 'in-flight'
        ORDER BY n.id DESC LIMIT 12
    """, p)
    recent_missions = _rows(db, """
        SELECT id, repo, goal, status, created_at FROM tensor_dev_missions
        WHERE (:org IS NULL OR organization_id = :org)
        ORDER BY id DESC LIMIT 8
    """, p)
    for row in recent_missions:
        row["created_at"] = _iso(row.get("created_at"))

    return _envelope(
        "organization", org, metrics,
        lists={
            "missions_by_status": missions_by_status,
            "sessions_by_status": sessions_by_status,
            "campaign_nodes_by_status": nodes_by_status,
            "campaign_milestones_by_status": milestones_by_status,
            "blocked_nodes": blocked_nodes,
            "inflight_nodes": inflight_nodes,
            "recent_missions": recent_missions,
        },
        definitions={
            "fleet": "执行器舰队 = tensor_executors 登记行 + Agent 会话在役数；两者都是平台实测，不做推算。",
            "campaign_live": "战役实况 = campaign_nodes.status 分布（in-flight/blocked/done）+ 阻塞节点的 block_reason 原文。",
        },
    )


# --------------------------------------------------------------------------- #
# Quality —— 评价流（报告 QA）+ 质量门态（四道门，逐门可解释）
# --------------------------------------------------------------------------- #
def _gate(key: str, label: str, state: str, detail: str, source: str) -> dict[str, Any]:
    return {"key": key, "label": label, "state": state, "detail": detail, "source": source}


@router.get("/quality", dependencies=[Depends(require_auth)])
def quality_center(db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    """质量面：报告 QA 评价流 + 质量门态（QA 门/欠账门/里程碑门/复现裁定门）。"""
    org = _org_scope(db, user)
    p = {"org": org}

    qa_by_status = _counts(db, """
        SELECT qa_status, count(*) FROM tensor_reports
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY qa_status
    """, p)
    reports_by_status = _counts(db, """
        SELECT status, count(*) FROM tensor_reports
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY status
    """, p)
    owed_row = db.execute(text("""
        SELECT SUM(CASE WHEN report_owed THEN 1 ELSE 0 END),
               SUM(CASE WHEN report_owed THEN 0 ELSE 1 END)
        FROM tensor_dev_missions
        WHERE (:org IS NULL OR organization_id = :org)
    """), p).first()
    owed, settled = _num(owed_row, 0), _num(owed_row, 1)
    repro_total = _num(db.execute(text("""
        SELECT count(*) FROM tensor_repro_verdicts
        WHERE (:org IS NULL OR organization_id = :org)
    """), p).first())
    # 规则级质检面：表可能不存在于某些部署的 schema —— 容错为 None（未实现），不写 0 冒充实测。
    rule_checks = _safe_count(db, "SELECT count(*) FROM quality_check_results", {})
    milestones_by_status = _counts(db, """
        SELECT m.status, count(*) FROM campaign_milestones m
        LEFT JOIN campaign_plans p ON p.id = m.plan_id
        WHERE (:org IS NULL OR p.organization_id = :org)
        GROUP BY m.status
    """, p)

    qa_pending = qa_by_status.get("pending", 0)
    qa_passed = qa_by_status.get("passed", 0)
    qa_rejected = qa_by_status.get("rejected", 0)
    qa_decided = qa_passed + qa_rejected
    reports_total = _total(reports_by_status)

    metrics = [
        _metric("reports_total", "报告总数", reports_total,
                "tensor_reports: count(*) [org]"),
        _metric("reports_qa_pending", "评价流·待检", qa_pending,
                "tensor_reports: count(*) where qa_status='pending'"),
        _metric("reports_qa_passed", "评价流·通过", qa_passed,
                "tensor_reports: count(*) where qa_status='passed'"),
        _metric("reports_qa_rejected", "评价流·打回", qa_rejected,
                "tensor_reports: count(*) where qa_status='rejected'"),
        _metric("reports_qa_undecided", "评价流·未分类",
                qa_by_status.get("__null__", 0),
                "tensor_reports: count(*) where qa_status is null",
                note="qa_status 为空 = 尚未进入评价流，单列不隐藏"),
        _metric("reports_qa_pass_rate", "评价流·已裁定通过率",
                round(qa_passed / qa_decided, 4) if qa_decided else None,
                "tensor_reports: passed/(passed+rejected)",
                unit="ratio",
                note="分母只取已裁定（passed+rejected）；无裁定 → null（不写 0 充数）"),
        _metric("missions_report_owed", "报告欠账", owed,
                "tensor_dev_missions: count(*) where report_owed"),
        _metric("missions_report_settled", "报告已清", settled,
                "tensor_dev_missions: count(*) where not report_owed"),
        _metric("campaign_milestones_total", "里程碑门·总数", _total(milestones_by_status),
                "campaign_milestones join campaign_plans [org]"),
        _metric("campaign_milestones_done", "里程碑门·通过", milestones_by_status.get("done", 0),
                "campaign_milestones: count(*) where status='done'"),
        _metric("quality_rule_checks", "规则级质检结果",
                rule_checks,
                "quality_check_results: count(*)（该表无 org 列，按全表计）",
                status="ok" if rule_checks is not None else "not_implemented",
                note=None if rule_checks is not None
                else "该库无 quality_check_results 表——质检面未实现，不编造通过率"),
        _metric("repro_verdicts", "复现裁定入库", repro_total,
                "tensor_repro_verdicts: count(*) [org]",
                note=None if repro_total else "0 行——复现裁定门无数据，如实显示 0"),
    ]

    # ── 结论五元证据链完整度（IN59-A3）：缺件 → E 封顶 E1；S3+ 无完整链 → 结算面拦付 ──
    chain_ready = True
    chains_by_status: dict[str, int] = {}
    chain_avg: float | None = None
    chain_capped = 0
    chain_blocked = 0
    chain_incomplete: list[dict[str, Any]] = []
    try:
        for r in _rows(db, """
            SELECT chain_status, count(*) AS n FROM outcome_evidence_chains
            WHERE (:org IS NULL OR organization_id = :org) GROUP BY chain_status
        """, p):
            chains_by_status[str(r["chain_status"])] = int(r["n"])
        avg_row = db.execute(text("""
            SELECT avg(completeness) FROM outcome_evidence_chains
            WHERE (:org IS NULL OR organization_id = :org)
        """), p).scalar()
        chain_avg = round(float(avg_row), 4) if avg_row is not None else None
        chain_capped = _safe_count(db, """
            SELECT count(*) FROM outcome_evidence_chains
            WHERE capped = TRUE AND (:org IS NULL OR organization_id = :org)
        """, p) or 0
        chain_blocked = _safe_count(db, """
            SELECT count(*) FROM outcome_eval_chain_audit e
            WHERE chain_blocked = TRUE AND (:org IS NULL OR e.organization_id = :org)
        """, p) or 0
        chain_incomplete = _rows(db, """
            SELECT chain_id, artifact_id, mission_id, grade, chain_status, completeness,
                   missing_elements, capped, created_at
            FROM outcome_evidence_chains
            WHERE chain_status <> 'complete' AND (:org IS NULL OR organization_id = :org)
            ORDER BY created_at DESC LIMIT 12
        """, p)
        for row in chain_incomplete:
            row["created_at"] = _iso(row.get("created_at"))
    except Exception:  # noqa: BLE001 —— 表/视图未建：如实报未实现，不写 0 充数
        chain_ready = False

    chain_total = sum(chains_by_status.values())
    metrics.extend([
        _metric("evidence_chain_total", "五元链·已登记", chain_total if chain_ready else None,
                "outcome_evidence_chains: count(*) [org]",
                status="ok" if chain_ready else "not_implemented"),
        _metric("evidence_chain_complete", "五元链·完整",
                chains_by_status.get("complete", 0) if chain_ready else None,
                "outcome_evidence_chains: count(*) where chain_status='complete'"),
        _metric("evidence_chain_partial", "五元链·缺件",
                chains_by_status.get("partial", 0) if chain_ready else None,
                "outcome_evidence_chains: count(*) where chain_status='partial'"),
        _metric("evidence_chain_missing", "五元链·全缺",
                chains_by_status.get("missing", 0) if chain_ready else None,
                "outcome_evidence_chains: count(*) where chain_status='missing'"),
        _metric("evidence_chain_completeness_avg", "五元链·平均完整度", chain_avg,
                "avg(completeness)；完整度 = 五元可解析元数/5", unit="ratio",
                note=None if chain_avg is not None else "无链记录 → null，不写 0"),
        _metric("evidence_chain_capped", "五元链·E 已封顶",
                chain_capped if chain_ready else None,
                "outcome_evidence_chains: count(*) where capped",
                note="缺件 → E 至多 E1（只降不升）"),
        _metric("evidence_chain_blocked", "五元链·结算拦付",
                chain_blocked if chain_ready else None,
                "outcome_eval_chain_audit: count(*) where chain_blocked",
                note="S3+ 无完整链 → 不按申报等级入信用账"),
    ])

    gates = [
        _gate("evidence_chain_gate", "结论五元证据链门",
              "未实现" if not chain_ready else
              ("有缺链" if (chains_by_status.get("partial", 0)
                            or chains_by_status.get("missing", 0))
               else ("全完整" if chain_total else "无数据")),
              (f"完整 {chains_by_status.get('complete', 0)} / 缺件 "
               f"{chains_by_status.get('partial', 0)} / 全缺 "
               f"{chains_by_status.get('missing', 0)}；封顶 {chain_capped}；"
               f"结算拦付 {chain_blocked}") if chain_ready
              else "该库无 outcome_evidence_chains 表——五元链面未初始化",
              "outcome_evidence_chains.chain_status"),
        _gate("report_qa_gate", "报告 QA 门",
              "有积压" if qa_pending else ("畅通" if qa_passed else "无数据"),
              f"待检 {qa_pending} / 通过 {qa_passed} / 打回 {qa_rejected}",
              "tensor_reports.qa_status"),
        _gate("report_delivery_gate", "报告交付门（欠账）",
              "有欠账" if owed else ("已清零" if settled else "无数据"),
              f"欠账 {owed} / 已清 {settled}",
              "tensor_dev_missions.report_owed"),
        _gate("campaign_milestone_gate", "战役里程碑门",
              "在验" if milestones_by_status.get("in-flight") else
              ("全通过" if milestones_by_status.get("done") else "无数据"),
              f"通过 {milestones_by_status.get('done', 0)} / 在验 "
              f"{milestones_by_status.get('in-flight', 0)}",
              "campaign_milestones.status"),
        _gate("repro_verdict_gate", "复现裁定门",
              "未实现" if not repro_total else "有裁定",
              f"裁定入库 {repro_total}",
              "tensor_repro_verdicts"),
    ]

    qa_queue = _rows(db, """
        SELECT id AS report_id, title, qa_status, status, created_at
        FROM tensor_reports
        WHERE (:org IS NULL OR organization_id = :org) AND qa_status = 'pending'
        ORDER BY id DESC LIMIT 12
    """, p)
    for row in qa_queue:
        row["created_at"] = _iso(row.get("created_at"))
    recent_decided = _rows(db, """
        SELECT id AS report_id, title, qa_status, qa_checked_at, qa_rejection_reason
        FROM tensor_reports
        WHERE (:org IS NULL OR organization_id = :org)
          AND qa_status IN ('passed', 'rejected')
        ORDER BY id DESC LIMIT 12
    """, p)
    for row in recent_decided:
        row["qa_checked_at"] = _iso(row.get("qa_checked_at"))

    return _envelope(
        "quality", org, metrics,
        gates=gates,
        lists={
            "evidence_chains_by_status": chains_by_status if chain_ready else None,
            "evidence_chains_incomplete": chain_incomplete,
            "qa_by_status": qa_by_status,
            "reports_by_status": reports_by_status,
            "milestones_by_status": milestones_by_status,
            "qa_queue": qa_queue,
            "recent_decided": recent_decided,
        },
        not_implemented=(
            [] if rule_checks is not None else
            [{"key": "quality_rule_checks", "label": "规则级质检结果面",
              "reason": "该库无 quality_check_results 表/列——质检面未实现，页面不编造通过率"}]
        ) + ([] if chain_ready else
             [{"key": "evidence_chain", "label": "结论五元证据链面",
               "reason": "该库无 outcome_evidence_chains 表/视图（迁移 105）——"
                         "链完整度未实现，页面不编造完整率"}]),
        definitions={
            "evaluation_flow": "评价流 = 报告进入 QA 后 qa_status 的三态流转（pending→passed/rejected）；"
                               "未分类（qa_status 为空）单列，不并入分母。",
            "quality_gate": "质量门态 = 五道平台级机器门（结论五元链 / 报告 QA / 报告欠账 / "
                            "战役里程碑 / 复现裁定）的当前状态；状态词由门内计数直接判定，"
                            "可在本响应 gates[].detail 复核。",
            "evidence_chain": "五元链 = 理论/文献依据 + 代码 commit + 实验配置 hash + 原始结果 + "
                              "分析报告 report_id；完整度 = 可解析元数/5，缺件 → E 封顶 E1，"
                              "S3+ 无完整链在结算面 chain_blocked（不按申报等级入信用账）。",
        },
    )


# --------------------------------------------------------------------------- #
# Capability —— 能力台账 + Delta 面
# --------------------------------------------------------------------------- #
@router.get("/capability", dependencies=[Depends(require_auth)])
def capability_center(db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    """能力面：能力增量台账（类别分布）+ 验收入账覆盖 + Delta 流水。"""
    org = _org_scope(db, user)
    p = {"org": org}

    by_category = _counts(db, """
        SELECT category, count(*) FROM tensor_capability_deltas
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY category
    """, p)
    covered = _num(db.execute(text("""
        SELECT count(DISTINCT d.mission_id)
        FROM tensor_capability_deltas d
        JOIN tensor_dev_missions m ON m.id = d.mission_id
        WHERE (:org IS NULL OR d.organization_id = :org) AND m.status = 'succeeded'
    """), p).first())
    succeeded = _num(db.execute(text("""
        SELECT count(*) FROM tensor_dev_missions
        WHERE status = 'succeeded' AND (:org IS NULL OR organization_id = :org)
    """), p).first())

    total = _total(by_category)
    substantive = total - by_category.get("no_substantive_delta", 0)

    metrics = [
        _metric("deltas_total", "能力增量总数", total,
                "tensor_capability_deltas: count(*) [org]"),
        _metric("substantive_deltas", "实质增量",
                substantive, "tensor_capability_deltas: count(*) where category<>'no_substantive_delta'"),
    ]
    for cat in CATEGORIES:
        metrics.append(_metric(
            f"deltas_{cat}", f"增量·{cat}", by_category.get(cat, 0),
            f"tensor_capability_deltas: count(*) where category='{cat}'"))
    metrics.extend([
        _metric("succeeded_missions", "验收入账分母（succeeded 任务）", succeeded,
                "tensor_dev_missions: count(*) where status='succeeded'"),
        _metric("covered_succeeded", "已入账的 succeeded 任务", covered,
                "count(distinct mission_id) from deltas join succeeded missions"),
        _metric("coverage_ratio", "验收入账覆盖率",
                round(covered / succeeded, 4) if succeeded else None,
                "covered_succeeded / succeeded_missions",
                unit="ratio",
                note="分母为 0 → null；覆盖率 < 1 表示成果未入账（真相不粉饰）"),
    ])

    recent = _rows(db, """
        SELECT d.id, d.mission_id, d.category, d.summary, d.reviewer, d.created_at,
               m.repo AS mission_repo, m.goal AS mission_goal, m.result AS mission_result
        FROM tensor_capability_deltas d
        LEFT JOIN tensor_dev_missions m ON m.id = d.mission_id
        WHERE (:org IS NULL OR d.organization_id = :org)
        ORDER BY d.id DESC LIMIT 12
    """, p)
    for row in recent:
        blob = row.pop("mission_result", None)
        headline = None
        try:
            if isinstance(blob, str):
                blob = json.loads(blob or "{}")
            if isinstance(blob, dict):
                headline = blob.get("headline")
        except Exception:
            headline = None
        row["mission_title"] = headline or row.get("mission_goal")
        row["created_at"] = _iso(row.get("created_at"))

    return _envelope(
        "capability", org, metrics,
        lists={
            "by_category": {c: by_category.get(c, 0) for c in CATEGORIES},
            "recent_deltas": recent,
        },
        definitions={
            "capability_ledger": "能力台账 = tensor_capability_deltas 逐 mission 增量流水"
                                 "（六类词表与 /tensor/capability 同一契约）。",
            "coverage": "覆盖率 = 已入账的 succeeded 任务数 / succeeded 任务总数；"
                        "不入账不算覆盖（闭环 C 真相不粉饰）。",
        },
    )


# --------------------------------------------------------------------------- #
# Innovation —— 提案 + 吸收率（实体未实现，代理面如实标注）
# --------------------------------------------------------------------------- #
@router.get("/innovation", dependencies=[Depends(require_auth)])
def innovation_center(db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    """创新面：创新实体（提案受理 / 自主探索 / 产出账）未实现——如实标注；
    给出**代理指标**：可复用增量吸收率（口径见 definitions.absorption_rate）。"""
    org = _org_scope(db, user)
    p = {"org": org}

    by_category = _counts(db, """
        SELECT category, count(*) FROM tensor_capability_deltas
        WHERE (:org IS NULL OR organization_id = :org) GROUP BY category
    """, p)
    total = _total(by_category)
    absorbing = sum(by_category.get(c, 0) for c in ABSORBING_CATEGORIES)
    absorption_rate = round(absorbing / total, 4) if total else None

    metrics = [
        _metric("innovation_proposals_total", "创新提案受理", None,
                "无 proposal 存储（无表、无端点）", status="not_implemented",
                note="创新实体未实现：不显示 0，也不造提案数"),
        _metric("knowledge_deltas", "可复用增量（吸收分子）", absorbing,
                "tensor_capability_deltas: count(*) where category in "
                f"{ABSORBING_CATEGORIES} [org]"),
        _metric("capability_deltas_total", "增量台账分母", total,
                "tensor_capability_deltas: count(*) [org]"),
        _metric("absorption_rate", "吸收率（代理）", absorption_rate,
                "knowledge_deltas / capability_deltas_total", unit="ratio",
                note="代理指标：非创新实体本体，定义见 definitions.absorption_rate"),
        _metric("falsified_hypothesis_deltas", "证伪假设增量",
                by_category.get("falsified_hypothesis", 0),
                "tensor_capability_deltas: count(*) where category='falsified_hypothesis'",
                note="证伪计入台账但不计入吸收分子"),
        _metric("defect_fix_deltas", "缺陷修复增量",
                by_category.get("defect_fix", 0),
                "tensor_capability_deltas: count(*) where category='defect_fix'",
                note="修 bug 不算创新吸收"),
    ]

    knowledge_rows = _rows(db, """
        SELECT d.id, d.mission_id, d.category, d.summary, d.created_at,
               m.repo AS mission_repo
        FROM tensor_capability_deltas d
        LEFT JOIN tensor_dev_missions m ON m.id = d.mission_id
        WHERE (:org IS NULL OR d.organization_id = :org)
          AND d.category IN ('new_capability', 'capability_extension', 'reusable_knowledge')
        ORDER BY d.id DESC LIMIT 12
    """, p)
    for row in knowledge_rows:
        row["created_at"] = _iso(row.get("created_at"))

    return _envelope(
        "innovation", org, metrics,
        implemented=False,
        lists={
            "absorption_evidence": knowledge_rows,
            "by_category": {c: by_category.get(c, 0) for c in CATEGORIES},
        },
        not_implemented=[
            {"key": "proposal_intake", "label": "创新提案受理机制",
             "reason": "无提案存储/端点（SPEC 第 4 章 P0 审计定谳未实现）"},
            {"key": "autonomous_exploration", "label": "自主探索任务线",
             "reason": "探索触发器未建设；任务平面仅承载人工/战役派发"},
            {"key": "innovation_output_ledger", "label": "创新产出账",
             "reason": "无独立产出账；相邻真值为能力增量台账（见能力中心）"},
        ],
        definitions={
            "absorption_rate": "吸收率（代理）= 类别属于 "
                               f"{list(ABSORBING_CATEGORIES)} 的能力增量条数 / 能力增量总条数。"
                               "分子只承认可复用增量；证伪假设与缺陷修复不计入分子（避免把修 bug 当创新）。"
                               "该指标是**相邻真值的代理**，不冒充创新实体本体。",
            "implemented_flag": "implemented=false 表示创新实体未建设；本页 metrics 中 "
                               "status=not_implemented 的项由前端渲染为「未实现」。",
        },
    )