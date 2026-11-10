#!/usr/bin/env python3
"""战役损耗自动补偿闭环：复活判定 + 回退重播执行器 + 熔断 + 产出度量。

为什么需要这个模块
------------------
:mod:`tools.validation.mission_attribution` 已经把「任务怎么没的」变成机器分类
（``infra_runtime`` / ``claim_falsified`` / ``acceptance_rejected`` ...），但分类只是
**诊断**：诊断完节点照样停在 blocked，战役产出照样掉。本模块把诊断接成**执行闭环**：

1. **复活判定器**（纯函数 :func:`classify_replayable`）
   只读机器字段（``status`` / ``fail_stage`` / ``stop_condition`` / ``tool_calls`` /
   ``revoked_reason`` / ``accepted`` / ``decision_note`` / evidence gate），输出
   ``{replayable, reason_code, ...}``。白名单**只有**运行期丢失五类
   （``llm_error`` / ``agent_lost`` / ``budget_exhausted`` / ``wall_exhausted`` /
   ``no_progress``）且 ``tool_calls == 0``（一行活没干，重播不会重复劳动）。
   ``claim_falsified`` / ``acceptance_rejected`` / honest-FAIL 一律 ``false``——
   技术失败重播等于掩盖失败，这是闭环红线。

2. **重播执行器**（:func:`replay_one` / :func:`run_round`）
   为可复活节点生成新 mission（``parent_mission_id`` 指向原节点、``origin`` 标记
   ``infra_replay``），模型走**回退链**：跳过 ``tensor_model_blacklist`` 再按 yield
   降序选，同 yield 按名字定序（确定性可复跑）。预算双闸：每节点 ≤
   ``per_node_cap``（默认 2）、每 plan ≤ ``plan_fraction``（默认 20%）× 计划节点数。

3. **熔断**（:func:`breaker_tripped`）
   同一模型连续 ``threshold``（默认 3）次 ``infra_runtime`` → 写
   ``tensor_model_blacklist`` + 事件 + 通知载荷（事件 detail 即通知面）。

4. **度量**（:func:`campaign_yield` / :func:`infra_loss_rate` /
   :func:`forced_finish_rate`）。

5. **幂等/审计**（:func:`run_round`）
   重播在飞（非终态）时再调只返回 ``already_replayed``，不产生新 mission；每次真重播
   写 ``mission_lifecycle_log``（action=``infra_replay``）+ ``campaign_events``
   （event_type=``mission_replay``），两表 append-only，按 ``parent_mission_id`` 可回溯。

平台中性纪律
------------
本模块是**共性机制**：不含任何领域词，域数据一律由调用方经参数 / DB 注入；测试夹具
用合成节点名。

用法
----
    python3 -m tools.validation.infra_replay --snapshot missions.jsonl      # 离线判定
    python3 -m tools.validation.infra_replay --from-db --plan-id 188 \
        --out-json artifacts/infra_replay/round.json                       # 只读大盘
    # 真跑（写沙箱 schema，绝不写生产节点）
    python3 -m tools.validation.infra_replay --from-db --limit 2000 \
        --sandbox-schema sbx_replay --out-json artifacts/infra_replay/round.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.validation import mission_attribution as ATTR  # noqa: E402

# ── 常量 ──────────────────────────────────────────────────────────────────────
EVIDENCE_CLASS = "infra_runtime"

#: 复活白名单：只有运行期丢失（工具/模型/预算层面的掉线），不含任何技术失败
RUNTIME_STAGES: Tuple[str, ...] = (
    "llm_error",
    "agent_lost",
    "budget_exhausted",
    "wall_exhausted",
    "no_progress",
)

TERMINAL_STATUS = ("succeeded", "failed", "revoked")

#: 重播 mission 的身份标记。生产表 ``tensor_dev_missions.origin`` 有 CHECK 约束
#: （只允许 owner/agent），所以**不**用 origin 承载身份：改用 ``task_key`` 前缀 +
#: ``parent_mission_id`` + ``result.reason_code`` 三件套，零 schema 改动即可唯一定位。
REPLAY_TAG = "infra_replay"
REPLAY_TASK_KEY_PREFIX = f"{REPLAY_TAG}:"
MISSION_ORIGIN = "agent"
LIFECYCLE_ACTION = "infra_replay"
EVENT_TYPE = "mission_replay"
BREAKER_EVENT_TYPE = "model_blacklisted"
#: campaign_events.plan_id 为 NOT NULL；跨 plan 的全局事件用哨兵 0
GLOBAL_EVENT_PLAN_ID = 0

#: reason_code 全表（机器判据按字符串断言，禁止改名）
REASON_OK = "replay_infra_zero_work"
REASON_KILL_SWITCH = "kill_switch_off"
REASON_NOT_TERMINAL = "not_terminal"
REASON_SUCCEEDED = "already_succeeded"
REASON_CLAIM_FALSIFIED = "claim_falsified"
REASON_ACCEPTANCE_REJECTED = "acceptance_rejected"
REASON_ADMIN_DEDUPE = "admin_dedupe"
REASON_INFRA_CRASH = "infra_crash_not_whitelisted"
REASON_EVIDENCE_ABSENT = "evidence_absent"
REASON_NOT_INFRA = "not_infra_runtime"
REASON_STAGE_NOT_WHITELISTED = "stage_not_whitelisted"
REASON_WORK_ALREADY_DONE = "work_already_done"
REASON_TOOL_CALLS_UNKNOWN = "tool_calls_unknown"
REASON_ALREADY_REPLAYED = "already_replayed"
REASON_BUDGET_CAP = "budget_cap"
REASON_NO_MODEL = "no_fallback_model"

REASON_TEXT = {
    REASON_OK: "运行期丢失且零工具调用 → 可复活",
    REASON_KILL_SWITCH: "kill-switch 关闭：一切判定为不可复活",
    REASON_NOT_TERMINAL: "非终态，尚无结论可判",
    REASON_SUCCEEDED: "已成功，无需补偿",
    REASON_CLAIM_FALSIFIED: "声明被独立复核证伪（技术失败，重播=掩盖失败）",
    REASON_ACCEPTANCE_REJECTED: "验收判据拒绝（技术失败，重播=掩盖失败）",
    REASON_ADMIN_DEDUPE: "行政去重停单，不计质量也不补偿",
    REASON_INFRA_CRASH: "基础设施崩溃（仓缺失/异常退出），不属运行期白名单",
    REASON_EVIDENCE_ABSENT: "无证据（零改动/未 finish），不可判定为运行期丢失",
    REASON_NOT_INFRA: "非运行期丢失类，不补偿",
    REASON_STAGE_NOT_WHITELISTED: "有运行期标记但不在白名单五类内",
    REASON_WORK_ALREADY_DONE: "已有工具调用（干过活），重播会重复劳动/污染工作树",
    REASON_TOOL_CALLS_UNKNOWN: "tool_calls 缺失，无法证明零工作量",
    REASON_ALREADY_REPLAYED: "已有在飞的重播 mission（幂等跳过）",
    REASON_BUDGET_CAP: "重播预算封顶（每节点/每 plan）",
    REASON_NO_MODEL: "回退链被黑名单耗尽，无可用模型",
}


# ── 输入归一化 ────────────────────────────────────────────────────────────────
def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def stage_tokens(mission: Mapping[str, Any]) -> List[str]:
    """把 ``fail_stage`` / ``stop_condition`` 拆成 token（``budget_exhausted+forced_finish``）。

    harness 把「怎么死的」与「怎么收尾的」用 ``+`` 拼在同一字段里，必须拆 token 再匹配
    白名单——否则 ``forced_finish`` 后缀会把真正的运行期丢失吞掉。
    """
    result = _as_dict(mission.get("result"))
    checks = _as_dict(result.get("evidence")).get("checks")
    checks = _as_dict(checks) if not isinstance(checks, dict) else checks
    summary = _as_dict(result.get("evidence")).get("summary")
    summary = _as_dict(summary) if not isinstance(summary, dict) else summary
    raw = [
        mission.get("fail_stage"),
        result.get("fail_stage"),
        # harness 的终局标记常落在 evidence.checks.fail_stage（列 fail_stage 可能全空）
        checks.get("fail_stage"),
        summary.get("fail_stage"),
        mission.get("stop_condition"),
        result.get("stop_condition"),
    ]
    tokens: List[str] = []
    for item in raw:
        if not item:
            continue
        for tok in re.split(r"[+;,/\s]+", str(item).strip().lower()):
            if tok and tok not in tokens:
                tokens.append(tok)
    return tokens


def tool_calls_of(mission: Mapping[str, Any]) -> Optional[int]:
    """取 ``result.tool_calls``（缺失 → ``None``，与 ``0`` 严格区分）。"""
    result = _as_dict(mission.get("result"))
    evidence = _as_dict(result.get("evidence"))
    summary = evidence.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    for key in ("tool_calls", "tool_call_count"):
        value = result.get(key, mission.get(key))
        if value is None:
            value = summary.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def forced_finish(mission: Mapping[str, Any]) -> bool:
    return "forced_finish" in stage_tokens(mission)


# ── 1. 复活判定器 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ReplayPolicy:
    """重播策略（全默认值即任务书口径）。"""

    per_node_cap: int = 2
    plan_fraction: float = 0.2
    breaker_threshold: int = 3
    require_zero_tool_calls: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "per_node_cap": self.per_node_cap,
            "plan_fraction": self.plan_fraction,
            "breaker_threshold": self.breaker_threshold,
            "require_zero_tool_calls": self.require_zero_tool_calls,
        }


DEFAULT_POLICY = ReplayPolicy()


def classify_replayable(
    mission: Mapping[str, Any],
    *,
    kill_switch: bool = False,
    policy: ReplayPolicy = DEFAULT_POLICY,
) -> Dict[str, Any]:
    """判定单条 mission 能否重播。**纯函数**：只读入参，无 IO、无全局状态。

    判定序（首个命中即返回，保证 reason_code 唯一）：
      kill-switch → 非终态 → 归因类（成功/证伪/验收拒/去重/崩溃/无证据/非运行期）
      → 白名单 token → ``tool_calls`` 零工作量。
    """
    status = str(mission.get("status") or "")
    tokens = stage_tokens(mission)
    calls = tool_calls_of(mission)

    base: Dict[str, Any] = {
        "mission_id": mission.get("id", mission.get("mission_id")),
        "status": status,
        "stage_tokens": tokens,
        "tool_calls": calls,
        "forced_finish": "forced_finish" in tokens,
        "policy": policy.as_dict(),
    }

    def out(replayable: bool, reason: str, **extra: Any) -> Dict[str, Any]:
        row = dict(base)
        row.update({"replayable": bool(replayable), "reason_code": reason,
                    "reason": REASON_TEXT.get(reason, reason)})
        row.update(extra)
        return row

    if kill_switch:
        return out(False, REASON_KILL_SWITCH, attribution=None)

    if status not in TERMINAL_STATUS:
        return out(False, REASON_NOT_TERMINAL, attribution=None)

    # 复用归因器（单一事实源），不重写它的优先级
    try:
        attribution = ATTR.classify(dict(mission))
    except (ValueError, KeyError):
        attribution = None
    primary = (attribution or {}).get("primary")

    if status == "succeeded" or primary in ("succeeded_unverified", "succeeded_verified"):
        return out(False, REASON_SUCCEEDED, attribution=primary)
    if primary == "claim_falsified":
        return out(False, REASON_CLAIM_FALSIFIED, attribution=primary)
    if primary == "acceptance_rejected":
        return out(False, REASON_ACCEPTANCE_REJECTED, attribution=primary)
    if primary == "admin_dedupe":
        return out(False, REASON_ADMIN_DEDUPE, attribution=primary)
    if primary == "infra_crash":
        return out(False, REASON_INFRA_CRASH, attribution=primary)
    if primary == "evidence_absent":
        return out(False, REASON_EVIDENCE_ABSENT, attribution=primary)
    if primary != EVIDENCE_CLASS:
        return out(False, REASON_NOT_INFRA, attribution=primary)

    whitelisted = [t for t in tokens if t in RUNTIME_STAGES]
    if not whitelisted:
        return out(False, REASON_STAGE_NOT_WHITELISTED, attribution=primary)

    if policy.require_zero_tool_calls:
        if calls is None:
            return out(False, REASON_TOOL_CALLS_UNKNOWN, attribution=primary,
                       runtime_stage=whitelisted[0])
        if calls > 0:
            return out(False, REASON_WORK_ALREADY_DONE, attribution=primary,
                       runtime_stage=whitelisted[0])

    return out(True, REASON_OK, attribution=primary, runtime_stage=whitelisted[0])


# ── 2. 模型回退链 + 预算 ──────────────────────────────────────────────────────
def select_fallback_model(
    model_yields: Mapping[str, float],
    blacklist: Iterable[str] = (),
) -> Optional[str]:
    """回退链：跳过黑名单，按 yield 降序；同 yield 按名字升序（确定性，可复跑）。"""
    banned = {str(b).strip().lower() for b in blacklist if str(b).strip()}
    candidates = [
        (str(name), float(score))
        for name, score in (model_yields or {}).items()
        if str(name).strip() and str(name).strip().lower() not in banned
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda kv: (-kv[1], kv[0]))
    return candidates[0][0]


def check_budget(
    *,
    node_replays: int,
    plan_replays: int,
    plan_total_nodes: int,
    policy: ReplayPolicy = DEFAULT_POLICY,
) -> Dict[str, Any]:
    """预算双闸：节点上限 + plan 份额。返回 ``{allowed, reason, scope, ...}``。

    小 plan（节点数 < 5）``floor(20%)=0`` 会让补偿永远无预算，故有效帽取
    ``max(1, floor(...))`` 并同时记 ``raw_plan_cap`` 供审计对账（这是显式口径，不是暗改）。
    """
    plan_scope = "known" if (plan_total_nodes or 0) > 0 else "unknown"
    plan_cap = int(math.floor(max(0, plan_total_nodes) * policy.plan_fraction))
    effective = max(1, plan_cap)
    detail = {
        "node_replays": node_replays,
        "per_node_cap": policy.per_node_cap,
        "plan_replays": plan_replays,
        "plan_total_nodes": plan_total_nodes,
        "plan_fraction": policy.plan_fraction,
        "raw_plan_cap": plan_cap,
        "effective_plan_cap": effective,
        "plan_scope": plan_scope,
    }
    if node_replays >= policy.per_node_cap:
        return {"allowed": False, "reason": REASON_BUDGET_CAP, "scope": "node", **detail}
    # plan 上下文缺失（mission 未挂节点 / 无计划）时不能把「未知」当「零预算」：
    # 静默拒绝补偿等于把损耗永久吞掉。未知即只受节点闸约束，并显式记账。
    if plan_scope == "known" and plan_replays >= effective:
        return {"allowed": False, "reason": REASON_BUDGET_CAP, "scope": "plan", **detail}
    return {"allowed": True, "reason": "budget_ok", "scope": None, **detail}


# ── 3. 熔断 ───────────────────────────────────────────────────────────────────
def breaker_tripped(
    events: Sequence[Mapping[str, Any]],
    model: str,
    *,
    threshold: int = 3,
) -> bool:
    """同一模型**连续** ``threshold`` 次 ``infra_runtime`` → 熔断。

    ``events`` 按时间升序；只数该模型自己的历史（别的模型的失败不该烧到它头上）。
    """
    if threshold <= 0:
        return False
    history = [e for e in events if str(e.get("model") or "") == str(model)]
    if len(history) < threshold:
        return False
    tail = history[-threshold:]
    return all(str(e.get("primary") or e.get("class") or "") == EVIDENCE_CLASS for e in tail)


def is_replay_mission(row: Mapping[str, Any]) -> bool:
    """重播 mission 判定（task_key 前缀 + 有 parent）：身份标记只有这一条定义。"""
    task_key = str(row.get("task_key") or "")
    return task_key.startswith(REPLAY_TASK_KEY_PREFIX) and row.get("parent_mission_id") is not None


# ── 4. 度量 ───────────────────────────────────────────────────────────────────
def campaign_yield(node_statuses: Iterable[str], *,
                   done_statuses: Sequence[str] = ("done",)) -> Dict[str, Any]:
    """``campaign.yield`` = 达标节点 / 全部节点。

    口径：按 **campaign_nodes.status** 而非 mission 状态算——节点是战役层产出单位。
    两者会分叉（节点 done 而其 mission failed），故另给 :func:`mission_level_yield`
    供对账；分叉本身是「节点完成度虚高」的度量对象。
    """
    rows = [str(s) for s in node_statuses]
    total = len(rows)
    done = sum(1 for s in rows if s in done_statuses)
    return {
        "succeeded": done,
        "total": total,
        "yield": (done / total) if total else None,
        "yield_6dp": round(done / total, 6) if total else None,
        "definition": "done/campaign_nodes.total",
    }


def mission_level_yield(mission_statuses: Iterable[str]) -> Dict[str, Any]:
    rows = [str(s) for s in mission_statuses]
    total = len(rows)
    ok = sum(1 for s in rows if s == "succeeded")
    return {"succeeded": ok, "total": total, "yield": (ok / total) if total else None,
            "definition": "succeeded/tensor_dev_missions.total"}


def infra_loss_rate(classified: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """运行期丢失率 = 归因 ``infra_runtime`` 的终态 mission / 全部终态 mission。"""
    rows = list(classified)
    total = len(rows)
    loss = sum(1 for r in rows
               if str(r.get("attribution") or r.get("primary") or "") == EVIDENCE_CLASS)
    return {"infra_runtime": loss, "terminal": total,
            "infra_loss_rate": (loss / total) if total else None,
            "definition": "infra_runtime/terminal_mission"}


def forced_finish_rate(classified: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """被迫收尾率 = 带 ``forced_finish`` 标记的 mission / 全部终态 mission。"""
    rows = list(classified)
    total = len(rows)
    forced = sum(1 for r in rows if r.get("forced_finish"))
    return {"forced_finish": forced, "terminal": total,
            "forced_finish_rate": (forced / total) if total else None,
            "definition": "forced_finish_marker/terminal_mission"}


def campaign_kpi(nodes: Sequence[str], missions: Sequence[Mapping[str, Any]],
                 classified: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """四项 KPI 打包（campaign.yield + mission_level_yield + 两个比率）。"""
    return {
        "campaign.yield": campaign_yield(nodes),
        "mission_level_yield": mission_level_yield([str(m.get("status")) for m in missions]),
        "infra_loss_rate": infra_loss_rate(classified),
        "forced_finish_rate": forced_finish_rate(classified),
    }

# ── 5. 执行器（store 无关，纯逻辑） ───────────────────────────────────────────
def replay_one(
    mission: Mapping[str, Any],
    *,
    store: "Store",
    policy: ReplayPolicy = DEFAULT_POLICY,
    plan_total_nodes: int = 0,
    kill_switch: bool = False,
    model_yields: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """对一个节点执行「判定 → 幂等 → 预算 → 选模型 → 落库 + 审计」。

    幂等语义：该 parent 已有**在飞**（非终态）重播 → ``already_replayed``，不建新
    mission。所以同一节点连调 N 次只产生 1 个新 mission（判据 b）。
    """
    verdict = classify_replayable(mission, kill_switch=kill_switch, policy=policy)
    mission_id = verdict["mission_id"]
    record: Dict[str, Any] = {"mission_id": mission_id, "verdict": verdict}

    # 先判定再碰 store：全库扫描时 8000 条里只有几十条可复活，对每条都开一次连接
    # 会让「一轮补偿」变成几千次往返（实测会把 120s 预算烧穿）。
    if not verdict["replayable"]:
        record.update({"action": "skip", "reason_code": verdict["reason_code"],
                       "reason": verdict["reason"], "existing_replays": 0,
                       "in_flight_replays": 0})
        return record

    existing = store.existing_replays(mission_id) if mission_id is not None else []
    terminal_attempts = [e for e in existing if str(e.get("status")) in TERMINAL_STATUS]
    in_flight = [e for e in existing if str(e.get("status")) not in TERMINAL_STATUS]
    record["existing_replays"] = len(existing)
    record["in_flight_replays"] = len(in_flight)

    if in_flight:
        record.update({"action": "skip", "reason_code": REASON_ALREADY_REPLAYED,
                       "reason": REASON_TEXT[REASON_ALREADY_REPLAYED]})
        return record

    plan_id = mission.get("plan_id", mission.get("campaign_id"))
    budget = check_budget(
        node_replays=len(terminal_attempts),
        plan_replays=store.plan_replays(plan_id) if plan_id is not None else 0,
        plan_total_nodes=plan_total_nodes,
        policy=policy,
    )
    record["budget"] = budget
    if not budget["allowed"]:
        store.append_lifecycle(mission_id, LIFECYCLE_ACTION,
                               {"action": "replay_denied", "reason": budget["reason"],
                                "scope": budget["scope"], **budget})
        record.update({"action": "skip", "reason_code": budget["reason"],
                       "reason": REASON_TEXT[budget["reason"]]})
        return record

    blacklist = store.list_blacklist()
    model = select_fallback_model(model_yields or {}, blacklist)
    record["fallback_blacklist"] = sorted(blacklist)
    if model is None:
        store.append_lifecycle(mission_id, LIFECYCLE_ACTION,
                               {"action": "replay_denied", "reason": REASON_NO_MODEL})
        record.update({"action": "skip", "reason_code": REASON_NO_MODEL,
                       "reason": REASON_TEXT[REASON_NO_MODEL]})
        return record

    attempt = len(terminal_attempts) + 1
    new_id = store.create_replay(
        parent_mission_id=mission_id,
        plan_id=plan_id,
        model=model,
        attempt=attempt,
        goal=str(mission.get("goal") or ""),
        repo=str(mission.get("repo") or ""),
        reason_code=verdict["reason_code"],
    )
    payload = {"parent_mission_id": mission_id, "replay_mission_id": new_id, "model": model,
               "attempt": attempt, "reason_code": verdict["reason_code"],
               "runtime_stage": verdict.get("runtime_stage"), "budget": budget}
    store.append_lifecycle(mission_id, LIFECYCLE_ACTION, {"action": "replay_created", **payload})
    store.append_event(plan_id, EVENT_TYPE, payload)
    record.update({"action": "replay", "reason_code": verdict["reason_code"],
                   "replay_mission_id": new_id, "model": model, "attempt": attempt})
    return record


def run_round(
    missions: Sequence[Mapping[str, Any]],
    *,
    store: "Store",
    policy: ReplayPolicy = DEFAULT_POLICY,
    plan_total_nodes: Optional[Mapping[Any, int]] = None,
    kill_switch: bool = False,
    model_yields: Optional[Mapping[str, float]] = None,
    events: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """一轮补偿：判定全部节点 → 逐个重播 → 熔断 → 汇总。

    ``events`` 传历史事件（时间升序）。本轮新建的重播不算 infra_runtime 事件——新
    mission 还没跑，把「结果未发生」当失败会误熔断。
    """
    totals = {"scanned": 0, "replayable": 0, "replayed": 0, "skipped": 0,
              "already_replayed": 0, "budget_cap": 0}
    records: List[Dict[str, Any]] = []
    for mission in missions:
        plan_id = mission.get("plan_id", mission.get("campaign_id"))
        nodes = int((plan_total_nodes or {}).get(plan_id, 0) or 0)
        rec = replay_one(mission, store=store, policy=policy, plan_total_nodes=nodes,
                         kill_switch=kill_switch, model_yields=model_yields)
        records.append(rec)
        totals["scanned"] += 1
        if rec["verdict"]["replayable"]:
            totals["replayable"] += 1
        if rec["action"] == "replay":
            totals["replayed"] += 1
        elif rec.get("reason_code") == REASON_ALREADY_REPLAYED:
            totals["already_replayed"] += 1
        elif rec.get("reason_code") == REASON_BUDGET_CAP:
            totals["budget_cap"] += 1
        else:
            totals["skipped"] += 1

    tripped: List[Dict[str, Any]] = []
    for model in sorted({str(e.get("model")) for e in (events or []) if e.get("model")}):
        if breaker_tripped(events or [], model, threshold=policy.breaker_threshold):
            already = model in set(store.list_blacklist())
            added = store.blacklist_add(model, f"consecutive_{policy.breaker_threshold}_infra_runtime")
            # campaign_events.plan_id 是 NOT NULL；熔断是跨 plan 的全局事件 → 用哨兵 0
            # （0 不是任何真实 plan id，SQL 里可一眼分离全局事件与 plan 内事件）
            store.append_event(GLOBAL_EVENT_PLAN_ID, BREAKER_EVENT_TYPE,
                               {"model": model, "threshold": policy.breaker_threshold,
                                "already_listed": already, "row_added": bool(added),
                                "notify": [f"model {model} blacklisted after "
                                           f"{policy.breaker_threshold} consecutive infra_runtime"]})
            tripped.append({"model": model, "already_listed": already, "row_added": bool(added)})

    return {"policy": policy.as_dict(), "kill_switch": bool(kill_switch),
            "totals": totals, "records": records, "breaker": tripped}


# ── 6. store 适配器 ───────────────────────────────────────────────────────────
class Store:
    """重播状态的最小接口（内存实现给单测，PG 实现给真跑）。"""

    def existing_replays(self, parent_mission_id: Any) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def plan_replays(self, plan_id: Any) -> int:
        raise NotImplementedError

    def create_replay(self, *, parent_mission_id: Any, plan_id: Any, model: str,
                      attempt: int, goal: str, repo: str, reason_code: str) -> Any:
        raise NotImplementedError

    def append_lifecycle(self, mission_id: Any, action: str, detail: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def append_event(self, plan_id: Any, event_type: str, detail: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def list_blacklist(self) -> List[str]:
        raise NotImplementedError

    def blacklist_add(self, model: str, reason: str) -> bool:
        raise NotImplementedError


class InMemoryStore(Store):
    """单测用内存 store：语义与 PG 版一致（含 append-only 日志）。"""

    def __init__(self) -> None:
        self.missions: Dict[int, Dict[str, Any]] = {}
        self.lifecycle: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.blacklist: List[str] = []
        self._next_id = 9_000_001

    def existing_replays(self, parent_mission_id: Any) -> List[Dict[str, Any]]:
        return [m for m in self.missions.values()
                if str(m.get("parent_mission_id")) == str(parent_mission_id)]

    def plan_replays(self, plan_id: Any) -> int:
        return sum(1 for m in self.missions.values() if str(m.get("plan_id")) == str(plan_id))

    def create_replay(self, *, parent_mission_id: Any, plan_id: Any, model: str,
                      attempt: int, goal: str, repo: str, reason_code: str) -> int:
        new_id = self._next_id
        self._next_id += 1
        self.missions[new_id] = {
            "id": new_id, "parent_mission_id": parent_mission_id, "plan_id": plan_id,
            "model": model, "attempt": attempt, "goal": goal, "repo": repo,
            "status": "queued", "origin": MISSION_ORIGIN,
            "task_key": f"{REPLAY_TASK_KEY_PREFIX}{parent_mission_id}:{attempt}",
            "reason_code": reason_code,
        }
        return new_id

    def append_lifecycle(self, mission_id: Any, action: str, detail: Mapping[str, Any]) -> None:
        self.lifecycle.append({"mission_id": mission_id, "action": action, "detail": dict(detail)})

    def append_event(self, plan_id: Any, event_type: str, detail: Mapping[str, Any]) -> None:
        self.events.append({"plan_id": GLOBAL_EVENT_PLAN_ID if plan_id is None else plan_id,
                            "event_type": event_type, "detail": dict(detail)})

    def list_blacklist(self) -> List[str]:
        return list(self.blacklist)

    def blacklist_add(self, model: str, reason: str) -> bool:
        if model in self.blacklist:
            return False
        self.blacklist.append(model)
        return True


class PgStore(Store):
    """Postgres store。真跑**必须**给沙箱 schema——不写生产节点。"""

    TABLES = ("tensor_dev_missions", "campaign_nodes", "mission_lifecycle_log",
              "campaign_events", "tensor_model_blacklist")

    #: 与 backend/migrations/111_infra_replay_action.sql 同源（棘轮：只增词，不缩词）
    AUDIT_ACTIONS = ("suspend", "resume", "write_off", "orphan_detect", "reclaim",
                     "necessity_gate", "pr_gate", "custody_requeue", "reopen", "infra_replay")
    AUDIT_EVENTS = ("node_status_change", "milestone_status_change", "block_detected",
                    "block_resolved", "spinning_detected", "escalation_created",
                    "snapshot_taken", "plan_updated", "mission_dispatched",
                    "mission_completed", "guerrilla_detected", "mission_replay",
                    "model_blacklisted")

    def __init__(self, dsn: str, *, schema: str, organization_id: int = 1) -> None:
        import psycopg  # noqa: PLC0415  只有真跑才需要驱动
        self._psycopg = psycopg
        self.dsn = dsn
        self.schema = schema
        self.organization_id = organization_id

    @property
    def AUDIT_WORDLIST_SQL(self) -> List[str]:  # noqa: N802 (常量式访问)
        actions = ",".join(f"'{a}'" for a in self.AUDIT_ACTIONS)
        events = ",".join(f"'{e}'" for e in self.AUDIT_EVENTS)
        return [
            f"ALTER TABLE {self.schema}.mission_lifecycle_log "
            f"DROP CONSTRAINT IF EXISTS mission_lifecycle_log_action_check",
            f"ALTER TABLE {self.schema}.mission_lifecycle_log "
            f"ADD CONSTRAINT mission_lifecycle_log_action_check CHECK (action IN ({actions}))",
            f"ALTER TABLE {self.schema}.campaign_events "
            f"DROP CONSTRAINT IF EXISTS campaign_events_event_type_check",
            f"ALTER TABLE {self.schema}.campaign_events "
            f"ADD CONSTRAINT campaign_events_event_type_check CHECK (event_type IN ({events}))",
        ]

    def _conn(self):
        return self._psycopg.connect(self.dsn)

    def prepare_sandbox(self, *, source_schema: str = "public") -> Dict[str, Any]:
        """建沙箱 schema（LIKE 生产表 INCLUDING ALL）+ 独立 id 序列（9000001 起，肉眼可辨）。"""
        stmts = [f"DROP SCHEMA IF EXISTS {self.schema} CASCADE",
                 f"CREATE SCHEMA {self.schema}",
                 f"CREATE SEQUENCE {self.schema}.sbx_mission_id_seq START 9000001"]
        for table in self.TABLES:
            stmts.append(f"CREATE TABLE {self.schema}.{table} "
                         f"(LIKE {source_schema}.{table} INCLUDING ALL)")
            stmts.append(f"ALTER TABLE {self.schema}.{table} DROP CONSTRAINT IF EXISTS "
                         f"{table}_plan_id_fkey")
        stmts.append(f"ALTER TABLE {self.schema}.tensor_dev_missions "
                     f"ALTER COLUMN id SET DEFAULT nextval('{self.schema}.sbx_mission_id_seq')")
        # 审计词表增补（生产由 backend/migrations/111_infra_replay_action.sql 落地；
        # 沙箱 LIKE INCLUDING ALL 会把旧 CHECK 一起拷过来，故此处镜像同一份定义）
        stmts.extend(self.AUDIT_WORDLIST_SQL)
        with self._conn() as conn, conn.cursor() as cur:
            for stmt in stmts:
                cur.execute(stmt)
        return {"schema": self.schema, "tables": list(self.TABLES), "statements": len(stmts)}

    def seed_missions(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """把 mission 快照灌进沙箱（判定读真 result 字段）。"""
        payload = [
            (int(row["id"]), self.organization_id, str(row.get("goal") or ""),
             str(row.get("repo") or ""), str(row.get("status") or "failed"),
             json.dumps(_as_dict(row.get("result")), ensure_ascii=False),
             row.get("model"), row.get("parent_mission_id"),
             row.get("origin") or MISSION_ORIGIN, row.get("campaign_id"), row.get("task_key"))
            for row in rows
        ]
        if not payload:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {self.schema}.tensor_dev_missions"
                " (id, organization_id, goal, repo, status, result, model,"
                "  parent_mission_id, origin, campaign_id, task_key)"
                " VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                payload,
            )
        return len(payload)

    def existing_replays(self, parent_mission_id: Any) -> List[Dict[str, Any]]:
        if parent_mission_id is None:
            return []
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id, status, model, task_key, result"
                f" FROM {self.schema}.tensor_dev_missions"
                " WHERE parent_mission_id = %s AND task_key LIKE %s ORDER BY id",
                (int(parent_mission_id), REPLAY_TASK_KEY_PREFIX + "%"),
            )
            return [{"id": r[0], "status": r[1], "model": r[2], "task_key": r[3], "result": r[4]}
                    for r in cur.fetchall()]

    def plan_replays(self, plan_id: Any) -> int:
        if plan_id is None:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {self.schema}.tensor_dev_missions"
                " WHERE campaign_id = %s AND task_key LIKE %s",
                (int(plan_id), REPLAY_TASK_KEY_PREFIX + "%"),
            )
            return int(cur.fetchone()[0])

    def create_replay(self, *, parent_mission_id: Any, plan_id: Any, model: str,
                      attempt: int, goal: str, repo: str, reason_code: str) -> int:
        payload = {"origin_mission_id": parent_mission_id, "attempt": attempt,
                   "reason_code": reason_code, "model": model}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self.schema}.tensor_dev_missions"
                " (organization_id, goal, repo, status, result, model, parent_mission_id,"
                "  origin, campaign_id, task_key)"
                " VALUES (%s,%s,%s,'queued',%s::jsonb,%s,%s,%s,%s,%s) RETURNING id",
                (self.organization_id, goal, repo, json.dumps(payload, ensure_ascii=False),
                 model, int(parent_mission_id), MISSION_ORIGIN, plan_id,
                 f"{REPLAY_TASK_KEY_PREFIX}{parent_mission_id}:{attempt}"),
            )
            return int(cur.fetchone()[0])

    def append_lifecycle(self, mission_id: Any, action: str, detail: Mapping[str, Any]) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self.schema}.mission_lifecycle_log"
                " (mission_id, action, detail) VALUES (%s,%s,%s::jsonb)",
                (int(mission_id) if mission_id is not None else None, action,
                 json.dumps(dict(detail), ensure_ascii=False)),
            )

    def append_event(self, plan_id: Any, event_type: str, detail: Mapping[str, Any]) -> None:
        # plan_id NOT NULL：未挂节点的 mission 归到全局哨兵 0（显式记账，不丢事件）
        pid = GLOBAL_EVENT_PLAN_ID if plan_id is None else plan_id
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self.schema}.campaign_events"
                " (organization_id, plan_id, event_type, detail) VALUES (%s,%s,%s,%s::jsonb)",
                (self.organization_id, pid, event_type,
                 json.dumps(dict(detail), ensure_ascii=False)),
            )

    def list_blacklist(self) -> List[str]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT model FROM {self.schema}.tensor_model_blacklist")
            return [r[0] for r in cur.fetchall()]

    def blacklist_add(self, model: str, reason: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {self.schema}.tensor_model_blacklist"
                        " WHERE organization_id = %s AND model = %s",
                        (self.organization_id, model))
            if cur.fetchone():
                return False
            cur.execute(f"INSERT INTO {self.schema}.tensor_model_blacklist"
                        " (organization_id, model, reason) VALUES (%s,%s,%s)",
                        (self.organization_id, model, reason))
            return True

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        with self._conn() as conn, conn.cursor() as cur:
            for name, sql in (
                ("replay_missions", f"SELECT count(*) FROM {self.schema}.tensor_dev_missions"
                                    f" WHERE task_key LIKE '{REPLAY_TASK_KEY_PREFIX}%'"),
                ("lifecycle_rows", f"SELECT count(*) FROM {self.schema}.mission_lifecycle_log"),
                ("event_rows", f"SELECT count(*) FROM {self.schema}.campaign_events"),
                ("blacklist_rows", f"SELECT count(*) FROM {self.schema}.tensor_model_blacklist"),
            ):
                cur.execute(sql)
                out[name] = int(cur.fetchone()[0])
        return out

    def set_replay_status(self, replay_mission_id: int, status: str) -> None:
        """把重播 mission 推到终态（单测/验收用：模拟重播又掉线或已立功）。"""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {self.schema}.tensor_dev_missions SET status = %s WHERE id = %s",
                        (status, int(replay_mission_id)))

    def lifecycle_rows(self) -> List[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT mission_id, action, detail FROM {self.schema}."
                        "mission_lifecycle_log ORDER BY id")
            return [{"mission_id": r[0], "action": r[1], "detail": r[2]} for r in cur.fetchall()]

    def event_rows(self) -> List[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT plan_id, event_type, detail FROM {self.schema}."
                        "campaign_events ORDER BY id")
            return [{"plan_id": r[0], "event_type": r[1], "detail": r[2]} for r in cur.fetchall()]


# ── 7. 生产只读查询（判定输入） ───────────────────────────────────────────────
def default_dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        for pid in subprocess.run(["pgrep", "-f", "uvicorn"], capture_output=True,
                                  text=True).stdout.split():
            try:
                env = open(f"/proc/{pid}/environ", "rb").read().decode("utf-8", "replace").split("\0")
            except OSError:
                continue
            for item in env:
                if item.startswith("DATABASE_URL="):
                    url = item.split("=", 1)[1]
                    break
            if url:
                break
    if not url:
        raise RuntimeError("no DATABASE_URL in env or uvicorn process")
    return (url.replace("postgresql+psycopg://", "postgresql://")
               .replace("postgresql+psycopg2://", "postgresql://"))


SQL_TERMINAL_MISSIONS = """
SELECT m.id, m.status, m.fail_stage, m.repo, m.model, m.result, m.parent_mission_id, m.origin,
       m.campaign_id, n.plan_id
  FROM tensor_dev_missions m
  -- 一条 mission 可能被多个 campaign_nodes 引用；直接 JOIN 会让同一 mission 出现多行，
  -- 补偿轮里表现为「同节点被重播两次」（幂等闸挡住了，但计数会飘）→ 取最早一个节点即可
  LEFT JOIN LATERAL (SELECT plan_id FROM campaign_nodes
                      WHERE mission_id = m.id ORDER BY id LIMIT 1) n ON TRUE
 WHERE m.status = ANY(%s)
   AND (m.task_key IS NULL OR m.task_key NOT LIKE %s)
 ORDER BY m.id
"""


def fetch_terminal_missions(dsn: str, *, limit: int = 0) -> List[Dict[str, Any]]:
    import psycopg  # noqa: PLC0415
    sql = SQL_TERMINAL_MISSIONS + (" LIMIT %s" if limit else "")
    params: List[Any] = [list(TERMINAL_STATUS), REPLAY_TASK_KEY_PREFIX + "%"]
    if limit:
        params.append(int(limit))
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_plan_nodes(dsn: str, plan_id: int) -> List[Dict[str, Any]]:
    import psycopg  # noqa: PLC0415
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, status, mission_id FROM campaign_nodes WHERE plan_id = %s"
                    " ORDER BY id", (plan_id,))
        return [{"node_id": r[0], "status": r[1], "mission_id": r[2]} for r in cur.fetchall()]


def fetch_model_yields(dsn: str, *, window: int = 400) -> Dict[str, float]:
    """回退链排序依据：近 ``window`` 条终态 mission 的 succeeded 率，按模型分组。"""
    import psycopg  # noqa: PLC0415
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT model, count(*) FILTER (WHERE status='succeeded')::float / count(*)"
            " FROM (SELECT model, status FROM tensor_dev_missions"
            "       WHERE status = ANY(%s) AND model IS NOT NULL"
            "       ORDER BY id DESC LIMIT %s) t GROUP BY model",
            (list(TERMINAL_STATUS), window),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def fetch_model_events(dsn: str, *, window: int = 300) -> List[Dict[str, Any]]:
    """熔断输入：近 ``window`` 条终态 mission 的 (model, primary) 时间序。"""
    rows = fetch_terminal_missions(dsn, limit=window)
    rows.sort(key=lambda r: int(r["id"]))
    events = []
    for row in rows:
        verdict = ATTR.classify({k: row[k] for k in ("id", "status", "fail_stage", "repo",
                                                     "model", "result") if k in row})
        events.append({"mission_id": row["id"], "model": row.get("model"),
                       "primary": verdict.get("primary")})
    return events


# ── 8. CLI ───────────────────────────────────────────────────────────────────
def _load_snapshot(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="campaign infra-loss replay loop")
    ap.add_argument("--snapshot", help="missions jsonl（离线判定）")
    ap.add_argument("--from-db", action="store_true", help="从生产库只读拉取终态 mission")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--plan-id", type=int, default=0, help="campaign plan（算 yield / 预算）")
    ap.add_argument("--sandbox-schema", default="", help="真跑写入的沙箱 schema（空=只判定不写）")
    ap.add_argument("--dsn", default="")
    ap.add_argument("--kill-switch", action="store_true")
    ap.add_argument("--seed-scope", choices=("candidates", "all", "none"), default="candidates",
                    help="沙箱灌哪些 mission（默认只灌会被重播的候选，避免上万行空灌）")
    ap.add_argument("--out-json", default="")
    args = ap.parse_args(argv)

    dsn = args.dsn or ""
    if args.snapshot:
        missions = _load_snapshot(args.snapshot)
    elif args.from_db:
        dsn = dsn or default_dsn()
        missions = fetch_terminal_missions(dsn, limit=args.limit)
    else:
        ap.error("need --snapshot or --from-db")
        return 2

    if not dsn:
        try:
            dsn = default_dsn()
        except RuntimeError:
            dsn = ""

    store: Store = InMemoryStore()
    plan_nodes: Dict[Any, int] = {}
    yields: Dict[str, float] = {}
    events: List[Dict[str, Any]] = []
    sandbox: Dict[str, Any] = {}
    if dsn:
        yields = fetch_model_yields(dsn)
        events = fetch_model_events(dsn)
        if args.plan_id:
            nodes = fetch_plan_nodes(dsn, args.plan_id)
            plan_nodes[args.plan_id] = len(nodes)
            for m in missions:
                if m.get("plan_id") == args.plan_id:
                    m["plan_id"] = args.plan_id
        if args.sandbox_schema:
            pg = PgStore(dsn, schema=args.sandbox_schema)
            sandbox = pg.prepare_sandbox()
            if args.seed_scope == "all":
                seed_rows = missions
            elif args.seed_scope == "none":
                seed_rows = []
            else:
                seed_rows = [m for m in missions if classify_replayable(m)["replayable"]]
            sandbox["seeded"] = pg.seed_missions(seed_rows)
            sandbox["seed_scope"] = args.seed_scope
            store = pg

    report = run_round(missions, store=store, plan_total_nodes=plan_nodes,
                       kill_switch=args.kill_switch, model_yields=yields, events=events)
    report["sandbox"] = sandbox
    report["model_yields"] = {k: round(v, 4) for k, v in sorted(yields.items())}
    verdicts = [r["verdict"] for r in report["records"]]
    report["kpi"] = {
        "infra_loss_rate": infra_loss_rate(verdicts),
        "forced_finish_rate": forced_finish_rate(verdicts),
    }
    if args.plan_id and dsn:
        nodes = fetch_plan_nodes(dsn, args.plan_id)
        plan_missions = [m for m in missions if m.get("plan_id") == args.plan_id]
        if plan_missions:
            report["kpi"]["campaign.yield"] = campaign_yield([n["status"] for n in nodes])
            report["kpi"]["mission_level_yield"] = mission_level_yield(
                [str(m.get("status")) for m in plan_missions])

    if isinstance(store, PgStore):
        report["store_counts"] = store.counts()

    # 工件瘦身：全库一轮有上万条 skip 记录（多数是 already_succeeded），逐条落盘会把
    # 工件撑到 MB 级且毫无信息量。只保留「非平凡」记录 + 全量 reason 直方图（对账够用）。
    keep_codes = {REASON_OK, REASON_ALREADY_REPLAYED, REASON_BUDGET_CAP, REASON_NO_MODEL}
    hist: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for rec in report["records"]:
        hist[rec["reason_code"]] = hist.get(rec["reason_code"], 0) + 1
        if rec["reason_code"] in keep_codes or rec["action"] == "replay":
            slim = {k: v for k, v in rec.items() if k not in ("verdict",)}
            slim["verdict"] = {k: rec["verdict"][k] for k in
                               ("mission_id", "replayable", "reason_code", "attribution",
                                "stage_tokens", "tool_calls", "forced_finish")}
            kept.append(slim)
    report["reason_histogram"] = dict(sorted(hist.items(), key=lambda kv: -kv[1]))
    report["records_omitted"] = len(report["records"]) - len(kept)
    report["records_non_trivial"] = kept
    report.pop("records", None)

    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ("totals", "breaker", "kpi")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
