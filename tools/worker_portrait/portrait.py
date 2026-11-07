#!/usr/bin/env python3
"""worker 画像最小面（自回归燃料④：识产能）——纯只读分析内核。

战役 IN15-O4 交付物。目标：按 worker（``tensor_dev_missions.claimed_by``）
聚合成交产能画像，供后续波「按产能调度」决策使用。本模块**只观察不干预**：

* 不写平台库（所有 SQL 均为 SELECT；--source psql 走 psql 只读导出）；
* 不改任何既有表结构、不动冻结 API 面、不新增 sheet/schema；
* 产物只落 ``artifacts/worker_portraits/``（md + json）+ 调用方指定目录。

画像四项核心指标（定义写死在 ``DEFINITIONS``，报告与 md 逐字一致）：

1. **领取速度**（pickup）：``claim_at - created_at``。``claim_at`` 取
   ``tensor_agent_session_events`` 中 ``payload->>'subtype'='mission_claimed'``
   事件的**最早**时间戳，按 ``session_id`` 与 mission 关联——这是平台上唯一
   记录了「谁在何时领走工单」的时序证据（mission 表本身无 claimed_at 列）。
   若该 mission 无 claim 事件（claim_at 为空），则不计入领取速度样本，
   但仍在成功率/仓分布里计入（覆盖度 ``n_claim_events / n_missions`` 明示）。
2. **成功率**：``delivered / settled``，其中 delivered={succeeded,
   awaiting_pr_merge}，settled=delivered ∪ {failed, revoked, stopped}；
   running/queued/suspended 属在飞态，不进分母、单独计数（避免把「还在跑」
   误判成「失败」）。
3. **仓分布**：每 worker 按 ``repo`` 的工单数/占比。
4. **7200s 内完成率**：已完结工单中 ``finished_at - claim_at <= 7200s`` 的比例
   （7200s = 平台 budget 默认 max_wall_seconds）。

逐条可溯（traceability）：每个画像内嵌 ``per_mission`` 逐单明细（mission_id /
repo / status / pickup_s / wall_s），并对 mission_id 列表取 sha256 指纹；
``--verify`` 会用同一 SQL 复查在线 mission 表，逐单比对 id/worker/repo/status，
把「画像里的每一行都能在 mission 表里找到且一致」变成机器判据。

负例（拒收）语义：空 worker（无工单）与空归属（worker 字段为空）一律
``EmptyWorkerError``；活跃 worker 数不足 ``min_active_workers`` 时
``InsufficientWorkersError``——宁可报 FAIL 也不出空画像。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA = "worker_portrait/v1"
DEFINITION_VERSION = "wp-v1"

DELIVERED = ("succeeded", "awaiting_pr_merge")
FAILED = ("failed", "revoked", "stopped")
IN_FLIGHT = ("running", "queued", "suspended")
TERMINAL = DELIVERED + FAILED

WALL_BUDGET_S = 7200.0  # 平台默认 budget.max_wall_seconds

DEFINITIONS = {
    "pickup_s": "claim_at - created_at（领取延迟，秒）；claim_at=最早 mission_claimed 事件时间",
    "wall_s": "finished_at - claim_at（在岗执行时长，秒）；claim_at 缺失时退化为 finished_at - created_at",
    "success_rate": "delivered/(delivered+failed)，delivered={succeeded,awaiting_pr_merge}，failed={failed,revoked,stopped}；在飞态不进分母",
    "completion_7200": "#(wall_s<=7200)/#(已完结)；7200s=budget.max_wall_seconds 默认值",
    "repo_distribution": "该 worker 领取工单按 repo 计数与占比",
    "invalid_interval": "pickup_s<0、wall_s<0 或 claim_at>finished_at 的行，剔出时长统计并单独计数",
}

CLAIM_SQL = """
WITH claim AS (
  SELECT session_id, min(created_at) AS claim_at
  FROM tensor_agent_session_events
  WHERE kind = 'note' AND payload->>'subtype' = 'mission_claimed'
  GROUP BY session_id
)
SELECT json_agg(t ORDER BY t.mission_id)::text AS payload
FROM (
  SELECT m.id                AS mission_id,
         m.claimed_by        AS worker,
         m.model             AS model,
         m.runner            AS runner,
         m.worker_role       AS worker_role,
         m.repo              AS repo,
         m.status            AS status,
         m.campaign_id       AS campaign_id,
         m.created_at        AS created_at,
         c.claim_at          AS claim_at,
         m.finished_at       AS finished_at
  FROM tensor_dev_missions m
  LEFT JOIN claim c ON c.session_id = m.session_id
  WHERE m.claimed_by IS NOT NULL AND m.claimed_by <> ''
) t
""".strip()


# --------------------------------------------------------------------------- #
# 错误类型
# --------------------------------------------------------------------------- #
class PortraitError(Exception):
    """画像构建失败基类（fail closed，不静默降级）。"""


class EmptyWorkerError(PortraitError):
    """空 worker：worker 无任何工单，或工单归属为空——拒收。"""


class InsufficientWorkersError(PortraitError):
    """活跃 worker 数不足判据下限——拒收（不出空画像）。"""


# --------------------------------------------------------------------------- #
# 时间工具
# --------------------------------------------------------------------------- #
def parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        txt = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(txt)
        except ValueError as exc:  # pragma: no cover - 防御性
            raise PortraitError(f"无法解析时间戳: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _seconds(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _pct(values: Sequence[float], q: float) -> Optional[float]:
    """nearest-rank 分位数（无 numpy 依赖）。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = int(round((q / 100.0) * (len(ordered) - 1)))
    return float(ordered[idx])


def _stats(values: Sequence[float]) -> Dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "p90": None, "mean": None, "max": None}
    return {
        "n": len(values),
        "median": _pct(values, 50),
        "p90": _pct(values, 90),
        "mean": round(sum(values) / len(values), 3),
        "max": float(max(values)),
    }


def _round(value: Optional[float], nd: int = 3) -> Optional[float]:
    return None if value is None else round(float(value), nd)


# --------------------------------------------------------------------------- #
# 行规范化 + 单 worker 画像
# --------------------------------------------------------------------------- #
def normalise_row(row: Dict[str, Any]) -> Dict[str, Any]:
    worker = (row.get("worker") or "").strip() if isinstance(row.get("worker"), str) else row.get("worker")
    if not worker:
        raise EmptyWorkerError(
            "空归属工单（worker 字段为空/missing），拒收：mission_id=%s" % row.get("mission_id"))
    created = parse_ts(row.get("created_at"))
    claim = parse_ts(row.get("claim_at"))
    finished = parse_ts(row.get("finished_at"))
    pickup = _seconds(created, claim)
    wall = _seconds(claim, finished)
    if wall is None and claim is None:
        wall = _seconds(created, finished)  # 退化：无 claim 事件时用 created→finished
    invalid = False
    if pickup is not None and pickup < 0:
        invalid = True
        pickup = None
    if wall is not None and wall < 0:
        invalid = True
        wall = None
    return {
        "mission_id": int(row["mission_id"]),
        "worker": worker,
        "model": row.get("model"),
        "runner": row.get("runner"),
        "worker_role": row.get("worker_role"),
        "repo": row.get("repo") or "UNKNOWN",
        "status": row.get("status") or "UNKNOWN",
        "campaign_id": row.get("campaign_id"),
        "created_at": created.isoformat() if created else None,
        "claim_at": claim.isoformat() if claim else None,
        "finished_at": finished.isoformat() if finished else None,
        "pickup_s": _round(pickup),
        "wall_s": _round(wall),
        "invalid_interval": invalid,
    }


def build_profile(worker: str, rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """单个 worker 的画像。rows 为空 → EmptyWorkerError（负例判据）。"""
    rows = list(rows)
    if not rows:
        raise EmptyWorkerError(f"worker={worker!r} 没有任何工单，拒收（空 worker 不出画像）")
    for row in rows:
        if row["worker"] != worker:
            raise PortraitError(f"worker 归属错配: {worker!r} vs {row['worker']!r}")

    n = len(rows)
    pickups = [r["pickup_s"] for r in rows if r["pickup_s"] is not None]
    walls = [r["wall_s"] for r in rows if r["wall_s"] is not None]
    delivered = [r for r in rows if r["status"] in DELIVERED]
    failed = [r for r in rows if r["status"] in FAILED]
    inflight = [r for r in rows if r["status"] in IN_FLIGHT]
    other = [r for r in rows if r["status"] not in TERMINAL and r["status"] not in IN_FLIGHT]
    settled = len(delivered) + len(failed)

    within = [w for w in walls if w <= WALL_BUDGET_S]
    repos: Dict[str, int] = {}
    for r in rows:
        repos[r["repo"]] = repos.get(r["repo"], 0) + 1
    repo_share = {k: round(v / n, 4) for k, v in sorted(repos.items(), key=lambda kv: (-kv[1], kv[0]))}

    stamps = [parse_ts(r["created_at"]) for r in rows if r["created_at"]]
    span_days = None
    if stamps:
        span = (max(stamps) - min(stamps)).total_seconds() / 86400.0
        span_days = round(span, 3)

    models: Dict[str, int] = {}
    roles: Dict[str, int] = {}
    for r in rows:
        if r.get("model"):
            models[r["model"]] = models.get(r["model"], 0) + 1
        if r.get("worker_role"):
            roles[r["worker_role"]] = roles.get(r["worker_role"], 0) + 1

    return {
        "worker": worker,
        "n_missions": n,
        "n_claim_events": sum(1 for r in rows if r["claim_at"]),
        "n_settled": settled,
        "n_delivered": len(delivered),
        "n_failed": len(failed),
        "n_in_flight": len(inflight),
        "n_other_status": len(other),
        "success_rate": round(len(delivered) / settled, 4) if settled else None,
        "pickup_s": _stats(pickups),
        "wall_s": _stats(walls),
        "completion_7200": {
            "n": len(walls),
            "n_within": len(within),
            "rate": round(len(within) / len(walls), 4) if walls else None,
            "budget_s": WALL_BUDGET_S,
        },
        "repos": repos,
        "repo_share": repo_share,
        "models": models,
        "roles": roles,
        "span_days": span_days,
        "throughput_per_day": round(n / span_days, 3) if span_days else None,
        "n_invalid_intervals": sum(1 for r in rows if r["invalid_interval"]),
        "mission_ids": sorted(r["mission_id"] for r in rows),
        "per_mission": sorted(
            ({k: r[k] for k in ("mission_id", "repo", "status", "claim_at", "finished_at",
                                "pickup_s", "wall_s", "invalid_interval")} for r in rows),
            key=lambda r: r["mission_id"],
        ),
    }


# --------------------------------------------------------------------------- #
# 汇总报告
# --------------------------------------------------------------------------- #
def aggregate(rows: Iterable[Dict[str, Any]], min_missions: int = 3,
              min_active_workers: int = 3) -> Dict[str, Any]:
    """把 mission 级行聚合成 worker 画像集合。

    * ``min_missions``：低于该工单数的 worker 标记 below_threshold（不出画像）；
    * ``min_active_workers``：活跃 worker 数不足 → InsufficientWorkersError。
    """
    if min_missions < 1:
        raise PortraitError("min_missions 必须 >= 1")
    norm = [normalise_row(r) for r in rows]
    if not norm:
        raise EmptyWorkerError("输入 0 条工单：无 worker 可画像，拒收")

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in norm:
        buckets.setdefault(row["worker"], []).append(row)

    active, below, empty = [], [], []
    for worker in sorted(buckets):
        wrows = buckets[worker]
        if not wrows:  # 防御性：buckets 构造保证非空
            empty.append(worker)
            continue
        if len(wrows) < min_missions:
            below.append({"worker": worker, "n_missions": len(wrows),
                          "reason": f"n_missions<{min_missions}"})
            continue
        active.append(build_profile(worker, wrows))

    active.sort(key=lambda p: (-p["n_missions"], p["worker"]))
    if len(active) < min_active_workers:
        raise InsufficientWorkersError(
            f"活跃 worker 仅 {len(active)} 个（< {min_active_workers}），画像面不足，拒收")

    pooled_pickup = [r["pickup_s"] for r in norm if r["pickup_s"] is not None]
    pooled_wall = [r["wall_s"] for r in norm if r["wall_s"] is not None]
    total = len(norm)
    sql_sha = hashlib.sha256(CLAIM_SQL.encode()).hexdigest()
    id_sha = hashlib.sha256(
        ",".join(str(r["mission_id"]) for r in sorted(norm, key=lambda x: x["mission_id"])).encode()
    ).hexdigest()

    return {
        "schema": SCHEMA,
        "definition_version": DEFINITION_VERSION,
        "definitions": DEFINITIONS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "tables": ["tensor_dev_missions", "tensor_agent_session_events"],
            "sql_sha256": sql_sha,
            "sql": CLAIM_SQL,
            "read_only": True,
        },
        "coverage": {
            "missions_total": total,
            "missions_with_claim_event": sum(1 for r in norm if r["claim_at"]),
            "workers_total": len(buckets),
            "workers_active": len(active),
            "workers_below_threshold": len(below),
            "workers_empty": len(empty),
            "pickup_sample": len(pooled_pickup),
            "wall_sample": len(pooled_wall),
            "missions_sha256": id_sha,
            "min_missions": min_missions,
        },
        "pooled": {
            "pickup_s": _stats(pooled_pickup),
            "wall_s": _stats(pooled_wall),
            "n_delivered": sum(1 for r in norm if r["status"] in DELIVERED),
            "n_settled": sum(1 for r in norm if r["status"] in TERMINAL),
            "success_rate": round(
                sum(1 for r in norm if r["status"] in DELIVERED) /
                max(1, sum(1 for r in norm if r["status"] in TERMINAL)), 4),
            "completion_7200": {
                "n": len(pooled_wall),
                "n_within": sum(1 for w in pooled_wall if w <= WALL_BUDGET_S),
                "rate": round(sum(1 for w in pooled_wall if w <= WALL_BUDGET_S) / len(pooled_wall), 4)
                if pooled_wall else None,
            },
        },
        "workers": active,
        "below_threshold": below,
        "empty_workers": empty,
    }


# --------------------------------------------------------------------------- #
# 逐条可溯（traceability）
# --------------------------------------------------------------------------- #
def verify_against_missions(report: Dict[str, Any], rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """把画像逐单与 mission 表行比对，返回 {ok, checked, mismatches}。

    判据：画像里的每一个 mission_id 必须在 mission 表中存在，且
    worker / repo / status 三项一致；反之 mission 表中该 worker 的工单数
    必须等于画像 n_missions（无漏单、无多单）。
    """
    live = {int(r["mission_id"]): r for r in rows}
    checked = 0
    mismatches: List[Dict[str, Any]] = []
    for prof in report.get("workers", []):
        for pm in prof["per_mission"]:
            mid = pm["mission_id"]
            src = live.get(mid)
            checked += 1
            if src is None:
                mismatches.append({"mission_id": mid, "error": "mission 表无此单"})
                continue
            if (src.get("worker") or "") != prof["worker"]:
                mismatches.append({"mission_id": mid, "error": "worker 不一致",
                                   "portrait": prof["worker"], "mission": src.get("worker")})
            if (src.get("repo") or "UNKNOWN") != pm["repo"]:
                mismatches.append({"mission_id": mid, "error": "repo 不一致",
                                   "portrait": pm["repo"], "mission": src.get("repo")})
            if (src.get("status") or "UNKNOWN") != pm["status"]:
                mismatches.append({"mission_id": mid, "error": "status 不一致",
                                   "portrait": pm["status"], "mission": src.get("status")})
        live_ids = {int(r["mission_id"]) for r in rows if (r.get("worker") or "") == prof["worker"]}
        portrait_ids = set(prof["mission_ids"])
        for missing in sorted(live_ids - portrait_ids):
            mismatches.append({"mission_id": missing, "error": "mission 表有单但画像漏收"})
    return {"ok": not mismatches, "checked": checked, "n_mismatches": len(mismatches),
            "mismatches": mismatches[:20]}


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _fmt(value: Optional[float], unit: str = "", nd: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{nd}f}{unit}"


def render_worker_md(prof: Dict[str, Any], meta: Dict[str, Any]) -> str:
    cov = meta["coverage"]
    lines = [
        f"# worker 画像 · {prof['worker']}",
        "",
        f"> schema `{meta['schema']}` / 定义版本 `{meta['definition_version']}` / "
        f"生成于 {meta['generated_at']} / 数据源 mission 表逐条可溯（mission_id sha256 "
        f"`{cov['missions_sha256'][:16]}`）。本画像只读，不参与调度。",
        "",
        "## 1. 身份与样本",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| worker | `{prof['worker']}` |",
        f"| 模型分布 | {prof['models'] or 'n/a'} |",
        f"| 角色分布 | {prof['roles'] or 'n/a'} |",
        f"| 工单数 n_missions | {prof['n_missions']} |",
        f"| 领取事件可溯样本 | {prof['n_claim_events']} |",
        f"| 活跃跨度 span_days | {_fmt(prof['span_days'], ' d', 2)} |",
        f"| 吞吐 throughput_per_day | {_fmt(prof['throughput_per_day'], ' 单/d', 2)} |",
        "",
        "## 2. 四项核心指标",
        "",
        "| 指标 | 值 | 口径 |",
        "|---|---|---|",
        f"| 领取速度(中位/ p90) | {_fmt(prof['pickup_s']['median'], ' s')} / "
        f"{_fmt(prof['pickup_s']['p90'], ' s')} | n={prof['pickup_s']['n']}；"
        "claim_at−created_at |",
        f"| 成功率 | {_fmt((prof['success_rate'] or 0) * 100, '%', 2) if prof['success_rate'] is not None else 'n/a'} "
        f"| {prof['n_delivered']}/{prof['n_settled']}，在飞 {prof['n_in_flight']} 不进分母 |",
        f"| 7200s 内完成率 | "
        f"{_fmt((prof['completion_7200']['rate'] or 0) * 100, '%', 2) if prof['completion_7200']['rate'] is not None else 'n/a'} "
        f"| {prof['completion_7200']['n_within']}/{prof['completion_7200']['n']} 已完结单 |",
        f"| 在岗时长(中位/ p90) | {_fmt(prof['wall_s']['median'], ' s')} / "
        f"{_fmt(prof['wall_s']['p90'], ' s')} | n={prof['wall_s']['n']} |",
        "",
        "## 3. 仓分布",
        "",
        "| repo | 单数 | 占比 |",
        "|---|---|---|",
    ]
    for repo, cnt in sorted(prof["repos"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {repo} | {cnt} | {prof['repo_share'][repo] * 100:.1f}% |")
    lines += [
        "",
        "## 4. 逐单溯源（与 mission 表逐条对齐）",
        "",
        "| mission_id | repo | status | claim_at | pickup_s | wall_s |",
        "|---|---|---|---|---|---|",
    ]
    for pm in prof["per_mission"]:
        lines.append(
            f"| {pm['mission_id']} | {pm['repo']} | {pm['status']} | "
            f"{(pm['claim_at'] or 'n/a')[:19]} | {_fmt(pm['pickup_s'])} | {_fmt(pm['wall_s'])} |")
    lines += [
        "",
        f"无效区间行（负时长/claim 晚于 finish）：{prof['n_invalid_intervals']}；"
        f"这些行剔出时长统计但仍计入成败与仓分布。",
        "",
        "复算：`python3 -m tools.worker_portrait --source psql --out-dir artifacts/worker_portraits`"
        "（只读 SELECT，SQL 见索引 md 附录，sha256 "
        f"`{meta['source']['sql_sha256'][:16]}`）。",
        "",
    ]
    return "\n".join(lines)


def render_index_md(report: Dict[str, Any]) -> str:
    cov = report["coverage"]
    pooled = report["pooled"]
    lines = [
        "# worker 画像索引（IN15-O4 · 自回归燃料④：识产能）",
        "",
        f"> schema `{report['schema']}` / 定义版本 `{report['definition_version']}` / "
        f"生成于 {report['generated_at']}",
        "> **只读分析**：全部 SQL 为 SELECT，不写库、不改表结构、不参与调度（策略=后续波）。",
        "",
        "## 覆盖度",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| mission 样本数 | {cov['missions_total']} |",
        f"| 含 claim 事件（可算领取速度） | {cov['missions_with_claim_event']} "
        f"（{cov['missions_with_claim_event'] / max(1, cov['missions_total']) * 100:.1f}%） |",
        f"| worker 总数 | {cov['workers_total']} |",
        f"| 出画像的活跃 worker（≥{cov['min_missions']} 单） | {cov['workers_active']} |",
        f"| 低于阈值未出画像 | {cov['workers_below_threshold']} |",
        f"| mission_id 指纹 sha256 | `{cov['missions_sha256']}` |",
        "",
        "## 平台整体（pooled，n 为样本口径）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 领取延迟 中位 / p90 | {_fmt(pooled['pickup_s']['median'], ' s')} / "
        f"{_fmt(pooled['pickup_s']['p90'], ' s')}（n={pooled['pickup_s']['n']}） |",
        f"| 在岗时长 中位 / p90 | {_fmt(pooled['wall_s']['median'], ' s')} / "
        f"{_fmt(pooled['wall_s']['p90'], ' s')}（n={pooled['wall_s']['n']}） |",
        f"| 成功率 | {pooled['success_rate'] * 100:.2f}% |",
        f"| 7200s 内完成率 | "
        f"{(pooled['completion_7200']['rate'] or 0) * 100:.2f}% "
        f"（{pooled['completion_7200']['n_within']}/{pooled['completion_7200']['n']}） |",
        "",
        "## 画像总表（按工单数降序）",
        "",
        "| worker | n | 成功率 | 领取中位(s) | 领取p90(s) | 在岗中位(s) | 7200s内完成率 | 主仓 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for prof in report["workers"]:
        top_repo = next(iter(prof["repo_share"]), "n/a")
        lines.append(
            f"| [{prof['worker']}]({prof['worker']}.md) | {prof['n_missions']} | "
            f"{_fmt((prof['success_rate'] or 0) * 100, '%', 1) if prof['success_rate'] is not None else 'n/a'} | "
            f"{_fmt(prof['pickup_s']['median'])} | {_fmt(prof['pickup_s']['p90'])} | "
            f"{_fmt(prof['wall_s']['median'])} | "
            f"{_fmt((prof['completion_7200']['rate'] or 0) * 100, '%', 1) if prof['completion_7200']['rate'] is not None else 'n/a'} | "
            f"{top_repo} |")
    if report["below_threshold"]:
        lines += ["", "## 低于阈值（未出画像，仅登记）", "",
                  "| worker | n | 原因 |", "|---|---|---|"]
        for item in report["below_threshold"]:
            lines.append(f"| {item['worker']} | {item['n_missions']} | {item['reason']} |")
    lines += [
        "",
        "## 口径（definition_version " + report["definition_version"] + "）",
        "",
    ]
    for key, desc in report["definitions"].items():
        lines.append(f"- **{key}**：{desc}")
    lines += [
        "",
        "## 附录：只读 SQL（sha256 " + report["source"]["sql_sha256"] + "）",
        "",
        "```sql",
        report["source"]["sql"],
        "```",
        "",
        "溯源校验：`python3 -m tools.worker_portrait --verify` —— 逐单比对画像 vs "
        "mission 表（id/worker/repo/status 四项 + 无漏单）。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 数据源
# --------------------------------------------------------------------------- #
def fetch_psql(database: str = "tensorplatform", user: str = "postgres",
               psql_bin: str = "psql") -> List[Dict[str, Any]]:
    """只读导出（psql CLI 单条 SELECT，不建 engine、不开事务写）。"""
    proc = subprocess.run(
        [psql_bin, "-U", user, "-d", database, "-tA", "-v", "ON_ERROR_STOP=1", "-c", CLAIM_SQL],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise PortraitError(f"psql 只读导出失败: {proc.stderr.strip()[:300]}")
    payload = proc.stdout.strip()
    if not payload:
        raise EmptyWorkerError("psql 返回空集：无已领取工单可画像，拒收")
    return json.loads(payload)


def load_snapshot(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        for key in ("rows", "missions", "payload"):
            if key in data:
                data = data[key]
                break
    if not isinstance(data, list):
        raise PortraitError("快照 JSON 必须是行数组或含 rows/missions/payload 键的对象")
    if not data:
        raise EmptyWorkerError("快照里 0 条工单：无 worker 可画像，拒收")
    return data


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"[write] {path} ({len(content)} B)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="worker_portrait",
        description="worker 画像最小面（只读聚合：领取速度/成功率/仓分布/7200s 内完成率）")
    ap.add_argument("--source", choices=["psql", "json"], default="psql",
                    help="数据源：psql 只读导出（默认）或离线 JSON 快照")
    ap.add_argument("--snapshot", help="--source json 时的快照文件")
    ap.add_argument("--database", default="tensorplatform")
    ap.add_argument("--db-user", default="postgres")
    ap.add_argument("--out-dir", default="artifacts/worker_portraits")
    ap.add_argument("--min-missions", type=int, default=3,
                    help="出画像所需最少工单数（默认 3）")
    ap.add_argument("--min-active-workers", type=int, default=3,
                    help="活跃 worker 判据下限（< 则拒收，默认 3）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只计算并打印摘要，不写任何文件")
    ap.add_argument("--verify", action="store_true",
                    help="与 mission 表逐单比对（仅 --source psql 有意义）")
    ap.add_argument("--no-worker-files", action="store_true",
                    help="只写 index.md + report.json，不写每个 worker 的 md")
    args = ap.parse_args(argv)

    try:
        if args.source == "psql":
            rows = fetch_psql(args.database, args.db_user)
        else:
            if not args.snapshot:
                raise PortraitError("--source json 需要 --snapshot")
            rows = load_snapshot(args.snapshot)
        report = aggregate(rows, min_missions=args.min_missions,
                           min_active_workers=args.min_active_workers)
    except PortraitError as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    cov = report["coverage"]
    print(f"[portrait] workers={cov['workers_total']} active={cov['workers_active']} "
          f"below={cov['workers_below_threshold']} missions={cov['missions_total']} "
          f"claim_events={cov['missions_with_claim_event']}")
    top = report["workers"][0] if report["workers"] else None
    if top:
        print(f"[portrait] top worker={top['worker']} n={top['n_missions']} "
              f"success={top['success_rate']} pickup_median={top['pickup_s']['median']}s")

    verdict = {"ok": True, "dry_run": args.dry_run, "verify": None}
    if args.verify:
        if args.source != "psql":
            print("FAIL: --verify 需要 --source psql", file=sys.stderr)
            return 2
        rows2 = fetch_psql(args.database, args.db_user)
        verdict["verify"] = verify_against_missions(report, rows2)
        print(f"[verify] ok={verdict['verify']['ok']} checked={verdict['verify']['checked']} "
              f"mismatches={verdict['verify']['n_mismatches']}")

    if args.dry_run:
        print("[dry-run] 不写文件；summary=" + json.dumps({
            "workers_active": cov["workers_active"],
            "missions_total": cov["missions_total"],
            "pooled": report["pooled"],
        }, ensure_ascii=False)[:400])
        return 0

    _write(os.path.join(args.out_dir, "report.json"),
           json.dumps({**report, "verdict": verdict}, ensure_ascii=False, indent=1))
    _write(os.path.join(args.out_dir, "index.md"), render_index_md(report))
    if not args.no_worker_files:
        for prof in report["workers"]:
            _write(os.path.join(args.out_dir, f"{prof['worker']}.md"),
                   render_worker_md(prof, report))
    print(f"[done] {cov['workers_active']} 个 worker 画像 → {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())