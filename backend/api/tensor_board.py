"""任务榜直领端点（FEAT-BOARD-CLAIM）—— 空闲 agent 不经 dispatcher 直接领开放锚。

问题
----
领单早就是 PULL（``POST /tensor/missions/claim``），但**造单**（挑锚 + 去重 +
组装 goal + 记账）住在 dispatcher 侧守护进程里（``case_feeder`` / ``selfstarter`` /
``taskboard``）。于是「agent 有空、锚也开着」这件事只有 dispatcher 醒着才会变成
任务——协调者注意力成为单点，断流两天的实证即由此而来。

本端点把造单搬进平台服务端：agent 一次 POST 换回一张已落库的 mission。

    POST /api/v1/tensor/board/claim   {domain? repo? anchor? ...}  -> mission_id

服务端严格按序做四件事（任一步失败都不产生孤儿单）：

1. **挑锚**：``feature_ledger`` 里 ``status ∈ ('open','planned')`` 的锚，跳过
   (a) 已有活租约的（``anchor_leases``，与 ``anchor_feeder`` 共用同一张占用表）、
   (b) 已被在飞单覆盖的（``task_key = 'anchor-<slug>'`` 或 goal 里已有
   ``【锚点=<slug>】`` 的非终态单）、
   (c) ``feature_ledger.mission_id`` 指向非终态单的。
2. **锚级租约**：``services.anchor_leases.acquire``，并发抢同一锚由部分唯一索引
   裁决，10 个并发只有 1 个赢（红队测试 backend/tests/test_board_claim_pg.py）。
   租约默认 6h，到期自动可抢（无需常驻 reaper）。
3. **服务端组装 goal**：模板对齐 ``case_feeder.goal_of``——锚 + 验收判据原文 +
   ref 出处零手抄 + 开工三查 + cluster 路由 + 报告六段 + 记忆回路 + 台账等级纪律
   （:func:`build_goal`，纯函数，单测可不连库直接对拍）。
4. **落 mission**：``runner`` 默认 native、``origin='agent'``，并回填
   ``anchor_leases.mission_id`` 与事件流 plan 事件；返回 ``mission_id``。

配套端点
--------
* ``GET  /tensor/board/anchors``  —— 当前**可领**的锚（与 claim 同一套 SQL 口径，只读）
* ``GET  /tensor/board/leases``   —— 活租约视图（诊断「谁占着这个锚」）
* ``POST /tensor/board/leases/{slug}/release`` —— 完单/放弃时释放（带 holder 校验）
* ``POST /tensor/board/reap``     —— 巡检式过期回收（可选，claim 时也会顺带回收）

dispatcher 不删：``selfstarter``（席位唤醒）与 ``case_feeder``（算例供给）降级为
兜底与推式供给，主路径是本端点。

边界（零领域语义）
------------------
本模块只认识 feature_ledger / anchor_leases / missions 三种治理对象，不硬编码任何
域词条、判据或算例；域内容全部来自数据行（slug/title/domain/acceptance）。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from database.config import get_db
from services import anchor_leases as AL
from services.anchor_leases import SOURCE_BOARD_CLAIM
from api.domain.tensor_common import append_event, resolved_org

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor/board", tags=["任务榜直领"])

#: claim 认的「开放锚」状态（done/verified 是已完成，in_progress 已有在飞单）
OPEN_STATUSES = ("open", "planned")
#: 在飞单状态（占用判据）
LIVE_MISSION_STATUSES = ("queued", "claimed", "running")
#: 候选窗大小（一次 claim 最多试这么多个锚，抢不到就换下一个，避免长事务）
CANDIDATE_WINDOW = 25
DEFAULT_BUDGET = {"max_tool_calls": 80, "max_wall_seconds": 7200, "max_tokens": 3_000_000}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ───────────────────────────── goal 组装（纯函数） ─────────────────────────────
def anchor_task_key(slug: str) -> str:
    """锚级 task_key：与 dispatcher 侧既有的 ``anchor-<slug>`` 约定同源，防双供。"""
    return ("anchor-" + str(slug).strip().lower())[:120]


def build_goal(
    anchor: dict[str, Any],
    *,
    repo: str,
    holder: str,
    lease_ttl_seconds: int,
) -> str:
    """把一行 feature_ledger 组装成 taskboard 级 goal（与 case_feeder.goal_of 同构）。

    判据：锚 slug / 验收判据原文 / 报告六段 / cluster 路由 / 记忆回路 / 台账等级纪律
    必须逐字在返回值里（单测 test_board_claim_goal.py 对拍），且整段 goal 不得触发
    报告台账门（REPORT_OVERCLAIMS_LEDGER）的误判——见同文件 LEDGER-GATE 用例。
    """
    slug = anchor["slug"]
    title = anchor.get("title") or slug
    acceptance = anchor.get("acceptance") or ""
    domain = anchor.get("domain") or repo
    source = anchor.get("source") or "-"
    ledger_id = anchor.get("id")
    return (
        f"【锚点={slug}】【榜上直领·FEAT-BOARD-CLAIM】{title}\n"
        f"锚源：feature_ledger#{ledger_id}（domain={domain}，source={source}，status={anchor.get('status')}）"
        f"｜租约持有人 {holder}（{lease_ttl_seconds}s，本端点持有，防双供）\n"
        f"验收判据（总账原文，零手抄——逐条对拍，报告里引用原表原页）：{acceptance}\n"
        f"目标仓：{repo}。**先读代码再改**；改动最小、可验证；能用测试证明就用测试证明。\n"
        f"开工三查（只读）：knowledge_base 同域 capability + negative_knowledge_ledger keywords='{repo}' "
        f"+ oss_solutions 同域；优先复用 {repo} 现有能力跑通（跑不动=生态病记 friction，不另起炉灶）。\n"
        f"计算/扫描 >10min 走 cluster_queue（集群递交，禁指定节点，调度器派发排队）。\n"
        f"报告六段（论文级）：①案例/问题+判据出处 ②方法与流程关键设置 ③参数表（无量纲数完整）"
        f"④结果对照表（基准 vs 本次+误差逐项打勾叉）⑤收敛性/参数敏感性（至少两点）"
        f"⑥注意点/失败模式/局限与工件；关键数字一律 SQL/工件溯源，禁止手抄。\n"
        f"能力等级纪律：报告里若断言某能力的证据等级，必须与能力台账 reconciled_level 一致"
        f"（报告 gate 的 REPORT_OVERCLAIMS_LEDGER 规则；降级≠放行）。\n"
        f"记忆回路：开工先取 context 包（同域 capability + 否定性知识命中），完结把教训/否定结果写回"
        f"（失败单也写，honest-FAIL 不得粉饰）。\n"
        f"完单（成功或 honest-FAIL）后释放本锚租约："
        f"POST /api/v1/tensor/board/leases/{slug}/release（holder={holder}）；不释放则 {lease_ttl_seconds}s 后自动过期可抢。\n"
        "失败=honest-FAIL：留 fail_stage + failure_reason，不覆盖既有结论。"
    )


# ───────────────────────────── 锚选择（同一口径三处共用） ─────────────────────────────
_CLAIMABLE_SQL = """
SELECT l.id, l.slug, l.title, l.domain, l.acceptance, l.status, l.source, l.ledger_group,
       l.mission_id,
       (SELECT al.holder FROM anchor_leases al
         WHERE al.anchor_slug = l.slug AND al.released_at IS NULL AND al.expires_at > now()
         LIMIT 1) AS live_holder
FROM feature_ledger l
WHERE l.status IN ('open','planned')
  AND (CAST(:slug AS text) IS NULL OR l.slug = CAST(:slug AS text))
  AND (CAST(:domain AS text) IS NULL OR lower(l.domain) = lower(CAST(:domain AS text)))
  AND (CAST(:repo AS text) IS NULL OR lower(l.domain) = lower(CAST(:repo AS text)))
  AND NOT EXISTS (
        SELECT 1 FROM anchor_leases al
         WHERE al.anchor_slug = l.slug AND al.released_at IS NULL
           AND al.expires_at > now())
  AND NOT EXISTS (
        SELECT 1 FROM tensor_dev_missions m
         WHERE m.organization_id = :org
           AND m.status IN ('queued','claimed','running')
           AND m.suspended_at IS NULL
           AND (m.task_key = 'anchor-' || lower(l.slug)
                OR m.goal LIKE '%【锚点=' || l.slug || '】%'))
  AND (l.mission_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM tensor_dev_missions mm
         WHERE mm.id = l.mission_id
           AND mm.status IN ('queued','claimed','running')))
ORDER BY l.id
LIMIT :limit
"""


def select_candidates(
    db: Session,
    *,
    org_id: int,
    domain: str | None = None,
    repo: str | None = None,
    slug: str | None = None,
    limit: int = CANDIDATE_WINDOW,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(_CLAIMABLE_SQL),
        {"org": org_id, "domain": domain or None, "repo": repo or None,
         "slug": slug or None, "limit": int(limit)},
    ).mappings().all()
    return [dict(r) for r in rows]


# ───────────────────────────── 落 mission ─────────────────────────────
def _create_mission(
    db: Session,
    *,
    org_id: int,
    user: dict[str, Any],
    goal: str,
    repo: str,
    task_key: str,
    runner: str,
    model: str | None,
    worker_role: str | None,
    budget: dict[str, Any],
) -> dict[str, Any]:
    """落 mission + session + plan 事件（形态与 api/tensor_missions.create_mission 同构）。

    与 create_mission 的差别只有一处：task_key 在 advisory lock 下**先查后插**，
    已存在即视为「该锚已被覆盖」——由调用方换下一个候选，而不是 409 打断整次 claim。
    """
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, :org))"),
               {"key": task_key, "org": org_id})
    # 同锚既有单：在飞 → 该锚已被覆盖（换下一个候选）；终态 → 返修接棒，
    # task_key 追加序号（-r2/-r3…），既不撞唯一索引也不让失败锚永久锁死。
    existing = db.execute(
        text(
            "SELECT id, status, task_key FROM tensor_dev_missions "
            "WHERE organization_id = :org AND (task_key = :key OR task_key LIKE :prefix) "
            "ORDER BY id"
        ),
        {"org": org_id, "key": task_key, "prefix": task_key + "-r%"},
    ).all()
    for row in existing:
        if str(row[1]) in LIVE_MISSION_STATUSES:
            raise _AnchorOccupied(int(row[0]), str(row[1]))
    if existing:
        task_key = f"{task_key}-r{len(existing) + 1}"[:120]

    mission_id = db.execute(
        text(
            """
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, budget, runner, origin, model,
                 created_by, worker_role, task_key)
            VALUES (:org, :goal, :repo, CAST(:budget AS jsonb), :runner, 'agent', :model,
                    :uid, :worker_role, :task_key)
            RETURNING id
            """
        ),
        {"org": org_id, "goal": goal, "repo": repo, "budget": _dumps(budget),
         "runner": runner, "model": model, "uid": user.get("id"),
         "worker_role": worker_role, "task_key": task_key},
    ).scalar()
    session_id = db.execute(
        text(
            """
            INSERT INTO tensor_agent_sessions
                (organization_id, agent_name, status, budget, started_by)
            VALUES (:org, 'tensor-agent', 'active', CAST(:budget AS jsonb), :uid)
            RETURNING id
            """
        ),
        {"org": org_id, "budget": _dumps(budget), "uid": user.get("id")},
    ).scalar()
    db.execute(text("UPDATE tensor_dev_missions SET session_id = :sid WHERE id = :mid"),
               {"sid": session_id, "mid": mission_id})
    append_event(db, session_id, "plan", {
        "mission_id": mission_id,
        "goal": goal,
        "repo": repo,
        "budget": budget,
        "runner": runner,
        "origin": "agent",
        "model": model,
        "via": "board/claim",
        "created_by": user.get("username"),
    })
    return {"mission_id": int(mission_id), "session_id": int(session_id)}


class _AnchorOccupied(Exception):
    """该锚已有同 task_key 的在册单（在飞或终态），本次换下一个候选。"""

    def __init__(self, mission_id: int, status: str):
        super().__init__(f"anchor already covered by mission#{mission_id} ({status})")
        self.mission_id = mission_id
        self.status = status


# ───────────────────────────── 请求体 ─────────────────────────────
class BoardClaimRequest(BaseModel):
    # 靶区过滤（都可不填 = 全榜挑最老的开放锚）
    domain: str | None = Field(default=None, max_length=64)
    repo: str | None = Field(default=None, max_length=64)
    # 定向领取（红队/复算用；填了就只试这一个锚）
    anchor: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    # 席位绑定（可选）：给 worker 用，落单时按角色/模型钉住，避免跨模型抢单
    worker_role: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    holder: str | None = Field(default=None, max_length=120)
    runner: str = Field(default="native", max_length=16)
    ttl_seconds: int | None = Field(default=None, ge=60, le=86400)
    budget: dict[str, Any] | None = None
    # 演练：只算不发（不落 mission，也不留租约）
    dry_run: bool = False
    organization_id: int | None = None


class LeaseReleaseRequest(BaseModel):
    holder: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="completed", max_length=40)


def _normalize_budget(raw: dict[str, Any] | None) -> dict[str, Any]:
    budget = dict(DEFAULT_BUDGET)
    for key, lo, hi in (("max_tool_calls", 1, 2000),
                        ("max_wall_seconds", 60, 86400),
                        ("max_tokens", 1000, 50_000_000)):
        if raw and key in raw:
            try:
                budget[key] = max(lo, min(hi, int(raw[key])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"budget.{key} must be an integer")
    return budget


def _holder_of(body: BoardClaimRequest, user: dict[str, Any]) -> str:
    if body.holder:
        return body.holder
    return f"user:{user.get('username') or user.get('id')}"


# ───────────────────────────── 端点 ─────────────────────────────
@router.post("/claim", dependencies=[Depends(require_auth)])
def board_claim(
    body: BoardClaimRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """空闲 agent 直领一个开放锚 → 服务端落 mission → 返回 mission_id。"""
    org_id = resolved_org(body.organization_id, user, db)
    AL.ensure_schema(db)
    holder = _holder_of(body, user)
    ttl = AL.clamp_ttl(body.ttl_seconds)
    runner = body.runner or "native"
    if runner not in ("native", "openhands"):
        raise HTTPException(status_code=422, detail=f"unknown runner: {runner}")

    candidates = select_candidates(db, org_id=org_id, domain=body.domain,
                                   repo=body.repo, slug=body.anchor)
    if not candidates:
        return {"claimed": False, "reason": "no_open_anchor", "scanned": 0,
                "filters": {"domain": body.domain, "repo": body.repo, "anchor": body.anchor}}

    budget = _normalize_budget(body.budget)
    tried: list[dict[str, Any]] = []
    for anchor in candidates:
        slug = anchor["slug"]
        lease = AL.acquire(db, slug, holder=holder, source=SOURCE_BOARD_CLAIM,
                           organization_id=org_id, ttl_seconds=ttl,
                           meta={"via": "board/claim", "user": str(user.get("id"))})
        if not lease.acquired:
            db.rollback()
            tried.append({"anchor": slug, "reason": lease.reason})
            continue
        repo = body.repo or anchor.get("domain") or "TensorPlatform"
        goal = build_goal(anchor, repo=repo, holder=holder, lease_ttl_seconds=ttl)
        task_key = anchor_task_key(slug)
        if body.dry_run:
            db.rollback()  # 演练不留痕
            return {"claimed": False, "dry_run": True, "anchor": slug, "repo": repo,
                    "goal": goal, "task_key": task_key, "tried": tried}
        try:
            created = _create_mission(
                db, org_id=org_id, user=user, goal=goal, repo=repo, task_key=task_key,
                runner=runner, model=body.model, worker_role=body.worker_role,
                budget=budget,
            )
        except _AnchorOccupied as occupied:
            db.rollback()  # 释放刚拿到的租约（同事务回滚）
            tried.append({"anchor": slug, "reason": "anchor_already_covered",
                          "mission_id": occupied.mission_id, "status": occupied.status})
            continue
        AL.bind_mission(db, lease.lease_id, created["mission_id"])
        db.commit()
        logger.info("board/claim %s -> mission#%s (holder=%s)", slug, created["mission_id"], holder)
        return {
            "claimed": True,
            "mission_id": created["mission_id"],
            "session_id": created["session_id"],
            "anchor": {"slug": slug, "title": anchor.get("title"),
                       "domain": anchor.get("domain"), "status": anchor.get("status")},
            "repo": repo,
            "task_key": task_key,
            "runner": runner,
            "budget": budget,
            "lease": lease.as_dict(),
            "goal": goal,
            "tried": tried,
        }

    return {"claimed": False, "reason": "all_candidates_contended",
            "scanned": len(candidates), "tried": tried,
            "filters": {"domain": body.domain, "repo": body.repo, "anchor": body.anchor}}


@router.get("/anchors", dependencies=[Depends(require_auth)])
def board_anchors(
    domain: str | None = Query(default=None),
    repo: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    organization_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """当前可领锚清单（与 claim 同一套占用口径，只读；供 agent 选题与看板）。"""
    org_id = resolved_org(organization_id, user, db)
    AL.ensure_schema(db)
    rows = select_candidates(db, org_id=org_id, domain=domain, repo=repo, limit=limit)
    return {"count": len(rows), "anchors": [
        {"slug": r["slug"], "title": r["title"], "domain": r["domain"],
         "status": r["status"], "source": r["source"], "ledger_id": r["id"]}
        for r in rows
    ]}


@router.get("/leases", dependencies=[Depends(require_auth)])
def board_leases(
    anchor: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """活租约视图（诊断「这个锚被谁占着」）。"""
    AL.ensure_schema(db)
    leases = AL.live_leases(db, anchor)
    return {"count": len(leases), "leases": leases}


@router.post("/leases/{slug}/release", dependencies=[Depends(require_auth)])
def board_release(
    slug: str,
    body: LeaseReleaseRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """释放锚租约（完单/放弃）。带 holder 时只有持有者能释放。"""
    AL.ensure_schema(db)
    holder = body.holder or f"user:{user.get('username') or user.get('id')}"
    n = AL.release(db, slug, holder=holder, reason=body.reason)
    db.commit()
    return {"released": n, "anchor": slug, "holder": holder}


@router.post("/reap", dependencies=[Depends(require_auth)])
def board_reap(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """巡检式过期回收（claim 时已顺带回收，这里给看板/巡检脚本一个显式入口）。"""
    AL.ensure_schema(db)
    reaped = AL.reap_expired(db)
    db.commit()
    return {"reaped": len(reaped), "leases": reaped}