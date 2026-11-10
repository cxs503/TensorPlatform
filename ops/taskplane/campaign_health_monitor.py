#!/usr/bin/env python3
"""campaign_health_monitor —— 战役节点健康监控（超时 / 空转 / 资源泄漏 / 自动恢复）。

为什么存在
----------
``ops/taskplane/stall_detector.py`` 只回答**平台整体**是否停摆（断粮 / 舰队心跳丢失 /
脉搏停滞）；它不看**单个战役节点**是否在烧预算、是否在空转、是否在漏资源。于是节点跑飞
（超时 2 倍预算）、节点假活（心跳在但工作区一动不动）、容器内存爬坡，仍然只能靠人发现。
本模块把这三类判据 + 终止后的 salvage 释放做成纯函数层，可离线重放、可 pytest 复现，
不依赖任何人类会话。

四个锚（阈值全部集中在 :class:`Thresholds`，无领域语义、无节点硬编码）
---------------------------------------------------------------------
A1 超时：``elapsed/planned >= timeout_warn_factor``(1.5) → P2 预警（``node_timeout``）；
        ``>= timeout_kill_factor``(2.0) → P1 终止（``node_timeout_kill``）。
A2 空转：连续 ``idle_heartbeats``(10) 次心跳**工作区指纹不变且进度计数不增** → P1 终止
        （``node_idle``）。工作区指纹 = sha256(max_mtime, new_files, commit)；
        ``progress``（可选迭代/测试计数）严格递增视为有进展 → 不计空转（给算力型节点留活口）。
A3 资源泄漏：``mem_pct`` / ``cpu_pct`` / ``disk_pct`` 任一 ``>= resource_pct``(80) 且
        **持续** ``resource_sustain_min``(5) 分钟（窗口内至少 2 个采样）→ P2 预警
        （``resource_pressure``）；回落到阈值以下同一指标可再预警（有恢复即重臂）。
A4 自动恢复：终止（timeout_kill / idle_kill）节点状态置 ``failed``，其**直接下游** DAG 边
        由 ``waiting`` 转 ``released``，上游从下游未满足依赖集里移除；下游已无未满足依赖
        且原本 pending/blocked → ``runnable``（释放下游依赖），否则保留 ``blocked`` 并记录
        剩余依赖。非直接下游（隔代）不动 —— 上游只是残废而非完成，隔代释放属越权。
        非终止（仅超时预警 / 资源预警）**不触发** salvage。

不变式（纪律）
--------------
* **零领域语义**：节点 id / 名称 / 预算 / DAG 全部由场景 JSON 注入；模块内不含域词条。
* **内容派生时钟**：只用场景内采样时间戳推导，不读 wall clock；同输入连跑两次逐字节相同。
* **分母为 0 返回 None**（不返回假 0），误报率等口径由报表层按期望集计算。
* **纯函数优先**：检测与 salvage 均可单独单测；CLI 只是薄壳（读场景 → 算 → 落盘）。
* 只写 ``--out-dir``，不改既有表结构 / 冻结 API 面。

用法
----
    python3 ops/taskplane/campaign_health_monitor.py \
        --scenario tests/fixtures/campaign_health_p250_n04_scenario.json \
        --out-dir artifacts/p250_n04
    python3 ops/taskplane/campaign_health_monitor.py --scenario ... --dry-run

退出码：0=无告警；2=有 P1（终止）；3=仅有 P2（预警）；1=输入错误（fail closed）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

UTC = timezone.utc

#: 节点状态（按严重度升序；节点结论取最高者，全部告警另存 alerts）。
NODE_STATES: tuple[str, ...] = (
    "healthy",
    "resource_warn",
    "timeout_warn",
    "idle_kill",
    "timeout_kill",
)
STATE_RANK: dict[str, int] = {s: i for i, s in enumerate(NODE_STATES)}
#: 终止态（触发 salvage）。
TERMINAL_STATES: frozenset[str] = frozenset({"timeout_kill", "idle_kill"})
#: 资源指标白名单。
METRICS: tuple[str, ...] = ("mem_pct", "cpu_pct", "disk_pct")
#: DAG 边状态。
EDGE_STATES: tuple[str, ...] = ("waiting", "released")
#: 告警种类 → 节点状态。
KIND_TO_STATE: dict[str, str] = {
    "node_timeout": "timeout_warn",
    "node_timeout_kill": "timeout_kill",
    "node_idle": "idle_kill",
    "resource_pressure": "resource_warn",
}


# ─────────────────────────── 时间与文化 ───────────────────────────

def parse_ts(value: Any) -> datetime:
    """解析 ISO-8601（``Z`` / 偏移 / 无时区按 UTC）/ epoch 秒 → aware datetime。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("empty timestamp")
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def fmt_ts(dt: datetime) -> str:
    """UTC ISO-8601 秒精度（秒以下截断，保证序列化确定性）。"""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _minutes(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 60.0


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────── 阈值 ───────────────────────────

@dataclass(frozen=True)
class Thresholds:
    """判据阈值（全部可配置；默认值即任务书口径）。"""

    timeout_warn_factor: float = 1.5
    timeout_kill_factor: float = 2.0
    idle_heartbeats: int = 10
    resource_pct: float = 80.0
    resource_sustain_min: float = 5.0

    def __post_init__(self) -> None:
        if self.timeout_warn_factor <= 1.0:
            raise ValueError("timeout_warn_factor must be > 1.0")
        if self.timeout_kill_factor <= self.timeout_warn_factor:
            raise ValueError("timeout_kill_factor must exceed timeout_warn_factor")
        if self.idle_heartbeats < 2:
            raise ValueError("idle_heartbeats must be >= 2")
        if not 0.0 < self.resource_pct <= 100.0:
            raise ValueError("resource_pct must be in (0, 100]")
        if self.resource_sustain_min < 0:
            raise ValueError("resource_sustain_min must be >= 0")

    @classmethod
    def from_dict(cls, raw: Optional[dict[str, Any]]) -> "Thresholds":
        data = {k: v for k, v in (raw or {}).items() if v is not None}
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown threshold keys: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


# ─────────────────────────── 采样与指纹 ───────────────────────────

def workspace_fingerprint(sample: dict[str, Any]) -> str:
    """工作区指纹：max_mtime + 新增文件数 + HEAD commit 摘要（任一变化即视为有动静）。"""
    ws = sample.get("workspace") or {}
    return _digest({k: ws.get(k) for k in ("max_mtime", "new_files", "commit")})


def _ordered_samples(node: dict[str, Any]) -> list[dict[str, Any]]:
    """按时间升序（同一时间戳保持输入序）→ 确定性。"""
    indexed = list(enumerate(node.get("samples") or []))
    indexed.sort(key=lambda pair: (parse_ts(pair[1].get("ts")), pair[0]))
    return [s for _, s in indexed]


def _alert(
    node_id: str,
    kind: str,
    severity: str,
    ts: datetime,
    *,
    metric: Optional[str] = None,
    value: Optional[float] = None,
    threshold: Optional[float] = None,
    message: str = "",
    action: str = "none",
    detail: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "node_id": node_id,
        "kind": kind,
        "severity": severity,
        "ts_utc": fmt_ts(ts),
        "metric": metric,
        "value": None if value is None else round(float(value), 3),
        "threshold": None if threshold is None else round(float(threshold), 3),
        "action": action,
        "message": message,
        "detail": detail or {},
    }
    body["alert_id"] = _digest({k: body[k] for k in ("node_id", "kind", "severity", "ts_utc")})
    return body


# ─────────────────────────── 三个检测锚 ───────────────────────────

def detect_timeout(
    node: dict[str, Any], samples: Sequence[dict[str, Any]], th: Thresholds
) -> list[dict[str, Any]]:
    """A1 超时：预算 1.5× 预警，2× 终止（各只报首次跨越）。"""
    planned = float(node.get("planned_minutes") or 0.0)
    if planned <= 0 or not samples:
        return []
    node_id = str(node["node_id"])
    start = parse_ts(node.get("started_at_utc") or samples[0]["ts"])
    warn_at = start + timedelta(minutes=planned * th.timeout_warn_factor)
    kill_at = start + timedelta(minutes=planned * th.timeout_kill_factor)

    alerts: list[dict[str, Any]] = []
    first_warn = next((s for s in samples if parse_ts(s["ts"]) >= warn_at), None)
    if first_warn is not None:
        ts = parse_ts(first_warn["ts"])
        elapsed = _minutes(ts, start)
        alerts.append(
            _alert(
                node_id, "node_timeout", "P2", ts,
                metric="budget_ratio", value=elapsed / planned,
                threshold=th.timeout_warn_factor,
                message=(
                    f"elapsed {elapsed:.1f}min >= {th.timeout_warn_factor}x "
                    f"planned {planned:.1f}min"
                ),
                action="warn",
                detail={"elapsed_minutes": round(elapsed, 3), "planned_minutes": planned},
            )
        )
    first_kill = next((s for s in samples if parse_ts(s["ts"]) >= kill_at), None)
    if first_kill is not None:
        ts = parse_ts(first_kill["ts"])
        elapsed = _minutes(ts, start)
        alerts.append(
            _alert(
                node_id, "node_timeout_kill", "P1", ts,
                metric="budget_ratio", value=elapsed / planned,
                threshold=th.timeout_kill_factor,
                message=(
                    f"elapsed {elapsed:.1f}min >= {th.timeout_kill_factor}x "
                    f"planned {planned:.1f}min"
                ),
                action="terminate+salvage",
                detail={"elapsed_minutes": round(elapsed, 3), "planned_minutes": planned},
            )
        )
    return alerts


def idle_run_lengths(
    node: dict[str, Any], samples: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """逐心跳累计「无进展」连续长度（工作区指纹不变 **且** progress 未递增）。

    返回每步 ``{ts, run, fingerprint_changed, progress_advanced}``，检测与作图共用。
    """
    trace: list[dict[str, Any]] = []
    run = 0
    prev_fp: Optional[str] = None
    prev_progress: Optional[float] = None
    for sample in samples:
        fp = workspace_fingerprint(sample)
        raw_progress = sample.get("progress")
        progress = None if raw_progress is None else float(raw_progress)
        if prev_fp is None:
            run = 1
            changed = True
            advanced = False
        else:
            advanced = (
                progress is not None and prev_progress is not None and progress > prev_progress
            )
            changed = fp != prev_fp
            run = run + 1 if (not changed and not advanced) else 1
        trace.append(
            {
                "ts": fmt_ts(parse_ts(sample["ts"])),
                "run": run,
                "fingerprint_changed": bool(changed),
                "progress_advanced": bool(advanced),
            }
        )
        prev_fp, prev_progress = fp, progress
    return trace


def detect_idle(
    node: dict[str, Any], samples: Sequence[dict[str, Any]], th: Thresholds
) -> list[dict[str, Any]]:
    """A2 空转：连续 ``idle_heartbeats`` 次心跳无工作区变化且无进度增量 → P1 终止。"""
    node_id = str(node["node_id"])
    for step in idle_run_lengths(node, samples):
        if step["run"] >= th.idle_heartbeats:
            ts = parse_ts(step["ts"])
            return [
                _alert(
                    node_id, "node_idle", "P1", ts,
                    metric="idle_heartbeats", value=float(step["run"]),
                    threshold=float(th.idle_heartbeats),
                    message=(
                        f"{step['run']} consecutive heartbeats without workspace change "
                        f"and without progress increment (N={th.idle_heartbeats})"
                    ),
                    action="terminate+salvage",
                    detail={
                        "idle_run": step["run"],
                        "heartbeats_required": th.idle_heartbeats,
                    },
                )
            ]
    return []


def detect_resource_leak(
    node: dict[str, Any], samples: Sequence[dict[str, Any]], th: Thresholds
) -> list[dict[str, Any]]:
    """A3 资源泄漏：任一指标 >= resource_pct 且持续 resource_sustain_min 分钟 → P2 预警。"""
    node_id = str(node["node_id"])
    alerts: list[dict[str, Any]] = []
    for metric in METRICS:
        window_start: Optional[datetime] = None
        window_count = 0
        fired = False
        for sample in samples:
            raw = sample.get(metric)
            if raw is None:
                continue
            ts = parse_ts(sample["ts"])
            value = float(raw)
            if value < th.resource_pct:
                window_start, window_count, fired = None, 0, False
                continue
            if window_start is None:
                window_start, window_count, fired = ts, 1, False
                continue
            window_count += 1
            if fired or window_count < 2:
                continue
            held = _minutes(ts, window_start)
            if held >= th.resource_sustain_min:
                alerts.append(
                    _alert(
                        node_id, "resource_pressure", "P2", ts,
                        metric=metric, value=value, threshold=th.resource_pct,
                        message=(
                            f"{metric}={value:.1f}% >= {th.resource_pct:.1f}% sustained "
                            f"{held:.1f}min (>= {th.resource_sustain_min:.1f}min)"
                        ),
                        action="warn",
                        detail={
                            "held_minutes": round(held, 3),
                            "samples_in_window": window_count,
                        },
                    )
                )
                fired = True
    return alerts


# ─────────────────────────── 节点结论 ───────────────────────────

def evaluate_node(node: dict[str, Any], th: Thresholds) -> dict[str, Any]:
    """单节点体检：汇总三锚告警 + 预算比，给出节点结论（最高严重度）。"""
    node_id = str(node["node_id"])
    samples = _ordered_samples(node)
    alerts = (
        detect_timeout(node, samples, th)
        + detect_idle(node, samples, th)
        + detect_resource_leak(node, samples, th)
    )
    state = "healthy"
    for alert in alerts:
        candidate = KIND_TO_STATE[alert["kind"]]
        if STATE_RANK[candidate] > STATE_RANK[state]:
            state = candidate

    planned = float(node.get("planned_minutes") or 0.0)
    elapsed: Optional[float] = None
    budget_ratio: Optional[float] = None
    if samples and planned > 0:
        start = parse_ts(node.get("started_at_utc") or samples[0]["ts"])
        elapsed = _minutes(parse_ts(samples[-1]["ts"]), start)
        budget_ratio = elapsed / planned if planned else None
    peaks = {
        m: max((float(s[m]) for s in samples if s.get(m) is not None), default=None)
        for m in METRICS
    }
    return {
        "node_id": node_id,
        "name": node.get("name") or node_id,
        "state": state,
        "planned_minutes": planned or None,
        "elapsed_minutes": None if elapsed is None else round(elapsed, 3),
        "budget_ratio": None if budget_ratio is None else round(budget_ratio, 4),
        "peak_resource_pct": {k: (None if v is None else round(v, 3)) for k, v in peaks.items()},
        "samples": len(samples),
        "alerts": alerts,
    }


# ─────────────────────────── A4 自动恢复 / DAG 释放 ───────────────────────────

def plan_salvage(
    nodes: Sequence[dict[str, Any]], verdicts: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """终止节点 → failed，直接下游依赖释放（纯函数，确定性）。

    ``nodes`` 为场景 DAG 节点表（含 ``status`` / ``dependencies``）；
    ``verdicts`` 为 :func:`evaluate_node` 结果。终止按 node_id 字典序施加，保证可复算。
    """
    status = {str(n["node_id"]): str(n.get("status") or "pending") for n in nodes}
    deps = {
        str(n["node_id"]): [str(d) for d in (n.get("dependencies") or [])] for n in nodes
    }
    downstream = {nid: sorted(d for d, ds in deps.items() if nid in ds) for nid in deps}
    # 未满足依赖 = 上游尚未 done 的依赖
    unresolved = {
        nid: {d for d in ds if status.get(d) != "done"} for nid, ds in deps.items()
    }

    terminations = sorted(
        (v for v in verdicts if v["state"] in TERMINAL_STATES),
        key=lambda v: str(v["node_id"]),
    )

    node_transitions: list[dict[str, Any]] = []
    edge_transitions: list[dict[str, Any]] = []
    terminations_out: list[dict[str, Any]] = []

    for verdict in terminations:
        nid = str(verdict["node_id"])
        kill_alert = next(
            (a for a in verdict["alerts"] if a["kind"] in ("node_timeout_kill", "node_idle")),
            None,
        )
        before = status.get(nid, "unknown")
        status[nid] = "failed"
        reason = (
            "timeout_budget_exceeded"
            if verdict["state"] == "timeout_kill"
            else "idle_no_progress"
        )
        node_transitions.append(
            {
                "node_id": nid,
                "status_before": before,
                "status_after": "failed",
                "reason": reason,
                "alert_id": kill_alert["alert_id"] if kill_alert else None,
                "terminal_state": verdict["state"],
                "elapsed_minutes": verdict["elapsed_minutes"],
                "budget_ratio": verdict["budget_ratio"],
            }
        )
        terminations_out.append(
            {
                "node_id": nid,
                "reason": reason,
                "terminal_state": verdict["state"],
                "ts_utc": kill_alert["ts_utc"] if kill_alert else None,
                "alert_id": kill_alert["alert_id"] if kill_alert else None,
                "elapsed_minutes": verdict["elapsed_minutes"],
                "budget_ratio": verdict["budget_ratio"],
                "downstream": downstream.get(nid, []),
                "salvage_actions": ["mark_failed", "release_downstream_edges", "requeue_freed"],
            }
        )

        for d in downstream.get(nid, []):
            edge_before = "waiting"
            unresolved[d].discard(nid)
            edge_transitions.append(
                {
                    "from": nid,
                    "to": d,
                    "status_before": edge_before,
                    "status_after": "released",
                    "reason": f"upstream_{reason}",
                    "remaining_dependencies": sorted(unresolved[d]),
                }
            )
            if status.get(d) in ("pending", "blocked"):
                if not unresolved[d]:
                    node_transitions.append(
                        {
                            "node_id": d,
                            "status_before": status[d],
                            "status_after": "runnable",
                            "reason": "all_dependencies_resolved_after_salvage",
                            "remaining_dependencies": [],
                        }
                    )
                    status[d] = "runnable"
                else:
                    node_transitions.append(
                        {
                            "node_id": d,
                            "status_before": status[d],
                            "status_after": "blocked",
                            "reason": "partial_dependency_release",
                            "remaining_dependencies": sorted(unresolved[d]),
                        }
                    )

    # 未被终止触达的边：保持 waiting，显式登记（证明「没动」而非「没看」）
    touched = {(e["from"], e["to"]) for e in edge_transitions}
    untouched: list[dict[str, Any]] = []
    for nid, ds in deps.items():
        for d in ds:
            if (d, nid) in touched:
                continue
            untouched.append(
                {
                    "from": d,
                    "to": nid,
                    "status_before": "waiting",
                    "status_after": "waiting",
                    "reason": "upstream_not_terminated",
                    "remaining_dependencies": sorted(unresolved[nid]),
                }
            )

    # 终态口径（不是中间态）：只看被释放边触达的下游，其**最终**状态
    affected = sorted({e["to"] for e in edge_transitions})
    return {
        "terminations": terminations_out,
        "node_transitions": node_transitions,
        "edge_transitions": edge_transitions,
        "untouched_edges": sorted(untouched, key=lambda e: (e["from"], e["to"])),
        "dag_states_after": {k: status[k] for k in sorted(status)},
        "released_edges": len(edge_transitions),
        "downstream_runnable": [n for n in affected if status[n] == "runnable"],
        "downstream_still_blocked": [n for n in affected if status[n] == "blocked"],
    }


# ─────────────────────────── 场景编排 ───────────────────────────

def load_scenario(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("nodes"), list) or not data["nodes"]:
        raise ValueError("scenario must contain a non-empty 'nodes' list")
    seen: set[str] = set()
    for node in data["nodes"]:
        nid = str(node.get("node_id") or "")
        if not nid:
            raise ValueError("every node needs a node_id")
        if nid in seen:
            raise ValueError(f"duplicate node_id: {nid}")
        seen.add(nid)
    for node in data["nodes"]:
        for dep in node.get("dependencies") or []:
            if str(dep) not in seen:
                raise ValueError(f"node {node['node_id']} depends on unknown node {dep}")
    return data


# ─────────────────────────── 线上台账 → 场景（采集适配层） ───────────────────────────

def scenario_from_rows(
    rows: Sequence[dict[str, Any]],
    heartbeats: dict[str, Sequence[dict[str, Any]]],
    *,
    scenario_id: str = "campaign-health-live",
    thresholds: Optional[Thresholds] = None,
    monitored_statuses: Sequence[str] = ("running", "in_progress"),
) -> dict[str, Any]:
    """把 ``campaign_nodes`` 台账行 + 席位心跳拼成场景（只读适配，零领域语义）。

    ``rows`` 每项形如 ``{node_id, name?, status?, planned_start?, planned_end?,
    planned_minutes?, started_at?, dependencies?}``：planned 时长优先取 ``planned_minutes``，
    否则由 ``planned_end - planned_start`` 推导（与 ``backend/api/campaign_plans.py`` 的
    campaign_nodes 列同名，采集器只需 SELECT 后透传）。
    ``heartbeats`` 形如 ``{node_id: [采样...]}``，采样字段即 :func:`evaluate_node` 所需
    （ts / workspace.max_mtime,new_files,commit / progress? / mem_pct,cpu_pct,disk_pct?）。

    没有心跳的节点 ``monitored=False``（不体检也不进告警），但**保留在 DAG 里**——
    下游依赖释放仍需要它。
    """
    nodes: list[dict[str, Any]] = []
    known = {str(r.get("node_id")) for r in rows}
    for row in rows:
        nid = str(row["node_id"])
        planned = row.get("planned_minutes")
        if planned is None and row.get("planned_start") and row.get("planned_end"):
            planned = _minutes(parse_ts(row["planned_end"]), parse_ts(row["planned_start"]))
        beats = list(heartbeats.get(nid) or [])
        status = str(row.get("status") or "pending")
        deps = [str(d) for d in (row.get("dependencies") or [])]
        missing = [d for d in deps if d not in known]
        if missing:
            raise ValueError(f"node {nid} depends on unknown node(s): {missing}")
        nodes.append(
            {
                "node_id": nid,
                "name": row.get("name") or nid,
                "status": status,
                "planned_minutes": planned,
                "started_at_utc": row.get("started_at") or row.get("planned_start"),
                "dependencies": deps,
                "monitored": bool(beats) and status in set(monitored_statuses),
                "samples": beats,
            }
        )
    return {
        "scenario_id": scenario_id,
        "time_origin_utc": min(
            (fmt_ts(parse_ts(s["ts"])) for beats in heartbeats.values() for s in beats),
            default=None,
        ),
        "thresholds": (thresholds or Thresholds()).to_dict(),
        "nodes": nodes,
    }


def run_monitor(
    scenario: dict[str, Any], thresholds: Optional[Thresholds] = None
) -> dict[str, Any]:
    """全场景体检 → 结果字典（JSON 可序列化、逐字节确定性）。"""
    th = thresholds or Thresholds.from_dict(scenario.get("thresholds"))
    nodes = scenario["nodes"]
    verdicts: list[dict[str, Any]] = []
    for node in sorted(nodes, key=lambda n: str(n["node_id"])):
        if not node.get("monitored", True):
            continue
        verdicts.append(evaluate_node(node, th))

    alerts: list[dict[str, Any]] = []
    for verdict in verdicts:
        alerts.extend(verdict["alerts"])
    alerts.sort(key=lambda a: (a["ts_utc"], a["node_id"], a["kind"]))

    salvage = plan_salvage(nodes, verdicts)
    summary = {
        "scenario_id": scenario.get("scenario_id"),
        "nodes_total": len(nodes),
        "nodes_monitored": len(verdicts),
        "alerts_total": len(alerts),
        "alerts_p1": sum(1 for a in alerts if a["severity"] == "P1"),
        "alerts_p2": sum(1 for a in alerts if a["severity"] == "P2"),
        "terminations": len(salvage["terminations"]),
        "released_edges": salvage["released_edges"],
        "downstream_runnable": len(salvage["downstream_runnable"]),
        "healthy_nodes": sum(1 for v in verdicts if v["state"] == "healthy"),
        "healthy_nodes_alert_free": sum(
            1 for v in verdicts if v["state"] == "healthy" and not v["alerts"]
        ),
    }
    return {
        "scenario_id": scenario.get("scenario_id"),
        "time_origin_utc": scenario.get("time_origin_utc"),
        "thresholds": th.to_dict(),
        "summary": summary,
        "verdicts": verdicts,
        "alerts": alerts,
        "salvage": salvage,
    }


def alert_log_lines(alerts: Sequence[dict[str, Any]]) -> list[str]:
    """告警日志（JSONL + 哈希链）：每行带 ``prev_hash`` → 篡改可检出。"""
    lines: list[str] = []
    prev = "0" * 16
    for seq, alert in enumerate(alerts, start=1):
        record = {"seq": seq, "prev_hash": prev, "alert": alert}
        record["hash"] = _digest({k: record[k] for k in ("seq", "prev_hash", "alert")})
        prev = record["hash"]
        lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False))
    return lines


def verify_alert_log(lines: Sequence[str]) -> bool:
    """校验哈希链完整性。"""
    prev = "0" * 16
    for seq, line in enumerate(lines, start=1):
        record = json.loads(line)
        if record.get("seq") != seq or record.get("prev_hash") != prev:
            return False
        expect = _digest({k: record[k] for k in ("seq", "prev_hash", "alert")})
        if record.get("hash") != expect:
            return False
        prev = record["hash"]
    return True


def dump_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ─────────────────────────── CLI（薄壳） ───────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="campaign node health monitor (timeout/idle/leak/salvage)")
    parser.add_argument("--scenario", required=True, help="scenario JSON path")
    parser.add_argument("--out-dir", default=None, help="output directory (monitor_result/alerts/recovery)")
    parser.add_argument("--thresholds-json", default=None, help="override thresholds JSON (file or inline)")
    parser.add_argument("--dry-run", action="store_true", help="compute only, write nothing")
    args = parser.parse_args(argv)

    try:
        scenario = load_scenario(args.scenario)
        override = None
        if args.thresholds_json:
            text = args.thresholds_json.strip()
            override = Thresholds.from_dict(
                json.loads(Path(text).read_text(encoding="utf-8")) if text.endswith(".json") else json.loads(text)
            )
        result = run_monitor(scenario, override)
    except (OSError, ValueError, KeyError) as exc:  # fail closed
        print(f"campaign_health_monitor: input error: {exc}", file=sys.stderr)
        return 1

    lines = alert_log_lines(result["alerts"])
    assert verify_alert_log(lines)

    if args.dry_run or not args.out_dir:
        print(dump_json(result["summary"]))
        return 2 if result["summary"]["alerts_p1"] else (3 if result["summary"]["alerts_total"] else 0)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "monitor_result.json").write_text(dump_json(result), encoding="utf-8")
    (out / "alerts.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    (out / "recovery.json").write_text(
        dump_json({"terminations": result["salvage"]["terminations"],
                   "node_transitions": result["salvage"]["node_transitions"],
                   "edge_transitions": result["salvage"]["edge_transitions"],
                   "dag_states_after": result["salvage"]["dag_states_after"]}),
        encoding="utf-8",
    )
    print(dump_json(result["summary"]))
    return 2 if result["summary"]["alerts_p1"] else (3 if result["summary"]["alerts_total"] else 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
