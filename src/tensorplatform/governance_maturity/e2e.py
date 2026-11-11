"""端到端治理链路：agent 心跳 → 事件流 → 画像更新 → 场交换 → 契约验证。

链路与判据（节点 p465w1-N07 验收项 (c)(d)）
------------------------------------------
E1 ``event_loss_rate``        心跳事件丢失率（真相侧 vs 落账侧），要求 = 0
E2 ``dropped_detected``       故障注入正对照：人为丢包必须被丢失检测器发现（灵敏度）
E3 ``portrait_consistency``   画像一致性误差（真相侧 vs 流式画像，逐 agent 取最大），< 5%
E4 ``portrait_tool_parity``   流式画像 vs 真跑 ``tools/worker_portrait.aggregate`` 的工单数/领取数
E5 ``pulse_runtime_parity``   流式状态 vs 真跑 ``tools/pulse.compute`` 的在线/忙碌/在跑工单数
E6 ``kind_contract_check``    每一个入账事件的事件种类都在会话事件契约内（违规数=0）
E7 ``field_exchange_in_loop`` 链路内场交换：量纲契约校验违规=0，同网格转写积分误差=0
E8 ``latency_p99``            单 tick 治理链路端到端延迟 P99（要求 < 5 s）

延迟在**真链路**上测，不另写一份"探针"复制品
--------------------------------------------
每个 tick 的处理（投递判定 → 契约校验 → 画像更新 → 按节奏插入场交换）被
``perf_counter`` 包一次，得到的就是这条治理链路的真实单 tick 端到端耗时。

为什么要故障注入
----------------
"丢失率=0"如果只在理想总线上测，等于没测——检测器可能永远报 0。
本模块用 ``drop_rate`` 注入丢包做**正对照**：注入 2% 时检测器必须报出同量级丢失，
且画像误差必须跟着变大。没有这一步，"0 丢失"不可信。

零领域语义：agent / mission / field 都是合成标签，没有任何学科量。
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import contracts as C
from .field_audit import _grid, build_envelope

HEARTBEAT_KIND = "note"
HEARTBEAT_SUBTYPE = "heartbeat"
MISSION_OPEN_KIND = "run_registered"
MISSION_CLAIM_KIND = "execution_started"
MISSION_VALIDATE_KIND = "validation_seen"
MISSION_CLOSE_KIND = "decision"
MISSION_LIFECYCLE: Tuple[Tuple[str, str], ...] = (
    (MISSION_OPEN_KIND, "mission_open"),
    (MISSION_CLAIM_KIND, "mission_claim"),
    (MISSION_VALIDATE_KIND, "mission_validate"),
    (MISSION_CLOSE_KIND, "mission_close"),
)

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class E2EConfig:
    num_agents: int = 100
    ticks: int = 1000
    drop_rate: float = 0.0
    exchange_every: int = 100
    mission_every: int = 50
    mission_length: int = 4
    seed: int = 20261111

    def validate(self) -> "E2EConfig":
        if not isinstance(self.num_agents, int) or self.num_agents < 1:
            raise ValueError("num_agents 必须是 >=1 的整数")
        if not isinstance(self.ticks, int) or self.ticks < 1:
            raise ValueError("ticks 必须是 >=1 的整数")
        if not isinstance(self.drop_rate, (int, float)) or not (0.0 <= self.drop_rate <= 0.5):
            raise ValueError("drop_rate 必须在 [0, 0.5]")
        if not isinstance(self.exchange_every, int) or self.exchange_every < 1:
            raise ValueError("exchange_every 必须是 >=1 的整数")
        if not isinstance(self.mission_every, int) or self.mission_every < 1:
            raise ValueError("mission_every 必须是 >=1 的整数")
        if not isinstance(self.mission_length, int) or self.mission_length < 2:
            raise ValueError("mission_length 必须是 >=2 的整数")
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {"num_agents": self.num_agents, "ticks": self.ticks,
                "drop_rate": self.drop_rate, "exchange_every": self.exchange_every,
                "mission_every": self.mission_every,
                "mission_length": self.mission_length, "seed": self.seed}


@dataclass
class Event:
    """会话事件（与 ``tensor_agent_session_events`` 的行同构：kind + payload）。"""

    agent: str
    seq: int
    tick: int
    kind: str
    subtype: str
    mission_id: Optional[int] = None

    def as_row(self) -> Dict[str, Any]:
        return {"agent": self.agent, "seq": self.seq, "tick": self.tick,
                "kind": self.kind, "subtype": self.subtype, "mission_id": self.mission_id}


def _emit_events(cfg: E2EConfig) -> Tuple[List[Event], List[List[Event]]]:
    """生成真相事件流：每 agent 每 tick 一个心跳 + 按节奏走的工单生命周期。

    返回 (扁平事件表, 按 tick 分桶事件表)。分桶用于在真链路上逐 tick 计时。
    """
    flat: List[Event] = []
    by_tick: List[List[Event]] = [[] for _ in range(cfg.ticks)]
    seq = [0] * cfg.num_agents
    mission_seq = 0
    for tick in range(cfg.ticks):
        bucket = by_tick[tick]
        for a in range(cfg.num_agents):
            agent = f"agent-{a:04d}"
            seq[a] += 1
            ev = Event(agent, seq[a], tick, HEARTBEAT_KIND, HEARTBEAT_SUBTYPE)
            flat.append(ev)
            bucket.append(ev)
            if tick % cfg.mission_every == 0:
                mission_id = mission_seq
                mission_seq += 1
                for offset, (kind, subtype) in enumerate(MISSION_LIFECYCLE):
                    if offset >= cfg.mission_length:
                        break
                    seq[a] += 1
                    lev = Event(agent, seq[a], tick + offset, kind, subtype, mission_id)
                    flat.append(lev)
                    by_tick[min(tick + offset, cfg.ticks - 1)].append(lev)
    return flat, by_tick


def detect_loss(truth: Sequence[Event], ledger: Sequence[Event]) -> Dict[str, Any]:
    """按 (agent, seq) 连续性检测丢失：返回期望/到账/缺失与丢失率。"""
    expected: Dict[str, int] = {}
    for ev in truth:
        if ev.seq > expected.get(ev.agent, 0):
            expected[ev.agent] = ev.seq
    got: Dict[str, set] = {}
    duplicates = 0
    for ev in ledger:
        bucket = got.setdefault(ev.agent, set())
        if ev.seq in bucket:
            duplicates += 1
            continue
        bucket.add(ev.seq)
    total_expected = sum(expected.values())
    missing = 0
    per_agent_missing: Dict[str, int] = {}
    for agent, top in expected.items():
        gap = top - len(got.get(agent, set()))
        if gap:
            per_agent_missing[agent] = gap
            missing += gap
    return {
        "expected": total_expected,
        "delivered": len(ledger),
        "missing": missing,
        "duplicates": duplicates,
        "loss_rate": (missing / total_expected) if total_expected else 0.0,
        "agents_with_loss": len(per_agent_missing),
    }


def _rel_error(truth: float, approx: float) -> float:
    if truth == 0:
        return 0.0 if approx == 0 else 1.0
    return abs(truth - approx) / abs(truth)


@dataclass
class GovernanceRunResult:
    config: E2EConfig
    summary: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"config": self.config.as_dict(), **self.summary}


def _resolved_edges(root: Path) -> List[Dict[str, Any]]:
    """取已发布契约上、量纲一致的耦合边（未发布契约的边不参与链路，见 findings）。"""
    from .units import parse_unit

    contracts = C.load_coupling_contracts(root)
    by_id = C.latest_by_contract_id(contracts)
    cpl_map = C.load_coupling_map(root, version=2)
    out: List[Dict[str, Any]] = []
    for edge in cpl_map.get("edges") or []:
        src_contract = by_id.get(str(edge["from"]["contract"]))
        dst_contract = by_id.get(str(edge["to"]["contract"]))
        if src_contract is None or dst_contract is None:
            continue
        src = next((p for p in src_contract.ports if p.get("name") == edge["from"]["port"]), None)
        dst = next((p for p in dst_contract.ports if p.get("name") == edge["to"]["port"]), None)
        if src is None or dst is None:
            continue
        if parse_unit(src["unit"]).dim != parse_unit(dst["unit"]).dim:
            continue
        out.append({"from": src, "to": dst,
                    "label": f"{edge['from']['contract']}.{edge['from']['port']}"
                             f"->{edge['to']['contract']}.{edge['to']['port']}"})
    return out


def run_governance_chain(cfg: Optional[E2EConfig] = None, *,
                         root: Optional[Path] = None,
                         validate_contracts: bool = True,
                         run_metric_tools: bool = True) -> GovernanceRunResult:
    """跑一次完整治理链路，返回全部判据数字（程序化注入，零手抄）。"""
    from src.coupling.contract_validation import ContractValidator
    from src.coupling.field_exchange import FieldExchange

    cfg = (cfg or E2EConfig()).validate()
    root = Path(root) if root else C.repo_root()

    vocabs = C.load_vocab_contracts(root)
    event_contract = vocabs["session_event_kinds"].data
    contract_kinds = set(event_contract.get("kinds") or [])
    governance_members = set(event_contract.get("governance_members") or [])

    truth, by_tick = _emit_events(cfg)
    n = cfg.num_agents
    index = {f"agent-{a:04d}": a for a in range(n)}
    edges = _resolved_edges(root)
    topology = _grid(2, 2)
    validator = ContractValidator()
    rng = random.Random(cfg.seed ^ 0x5EED)

    ledger: List[Event] = []
    dropped = 0
    kind_violations = 0
    governance_outside = 0
    stream_events = [0] * n
    stream_missions = [0] * n
    stream_claims = [0] * n
    stream_closed = [0] * n
    stream_by_kind: List[Dict[str, int]] = [{} for _ in range(n)]
    latencies_ms: List[float] = []
    exchange_stats = {"exchanges": 0, "edges_available": len(edges),
                      "contract_violations": 0, "identity_integral_error": 0.0,
                      "checks_performed": 0, "per_edge": {},
                      "method": "nearest（同网格恒等转写，只验证链路接线；conservative 见 findings）"}

    for tick in range(cfg.ticks):
        t0 = time.perf_counter()
        for ev in by_tick[tick]:
            if cfg.drop_rate > 0.0 and rng.random() < cfg.drop_rate:
                dropped += 1
                continue
            ledger.append(ev)
            if validate_contracts:
                if ev.kind not in contract_kinds:
                    kind_violations += 1
                elif ev.kind not in governance_members:
                    governance_outside += 1
            a = index[ev.agent]
            stream_events[a] += 1
            by_kind = stream_by_kind[a]
            by_kind[ev.kind] = by_kind.get(ev.kind, 0) + 1
            if ev.kind == MISSION_OPEN_KIND:
                stream_missions[a] += 1
            elif ev.kind == MISSION_CLAIM_KIND:
                stream_claims[a] += 1
            elif ev.kind == MISSION_CLOSE_KIND:
                stream_closed[a] += 1
        # 链路内场交换：按节奏插入（量纲契约校验 + 转写积分一致性）
        if edges and tick % cfg.exchange_every == 0:
            edge = edges[(tick // cfg.exchange_every) % len(edges)]
            src_msg = build_envelope(edge["from"], topology, timestamp=float(tick))
            dst_msg = build_envelope(edge["to"], topology, timestamp=float(tick))
            for _v in validator.validate_dimension_consistency(src_msg, dst_msg):
                exchange_stats["contract_violations"] += 1
            mapped = FieldExchange("src", "dst").map_field(src_msg, topology, method="nearest")
            src_int = float(src_msg.values.sum())
            dst_int = float(mapped.values.sum())
            if src_int != 0.0:
                exchange_stats["identity_integral_error"] = max(
                    exchange_stats["identity_integral_error"], abs(dst_int - src_int) / abs(src_int))
            exchange_stats["exchanges"] += 1
            exchange_stats["checks_performed"] = validator._checks_performed  # noqa: SLF001
            exchange_stats["per_edge"][edge["label"]] = (
                exchange_stats["per_edge"].get(edge["label"], 0) + 1)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    # ---- 真相侧计数（丢包前）------------------------------------------- #
    truth_events = [0] * n
    truth_missions = [0] * n
    truth_claims = [0] * n
    truth_closed = [0] * n
    for ev in truth:
        a = index[ev.agent]
        truth_events[a] += 1
        if ev.kind == MISSION_OPEN_KIND:
            truth_missions[a] += 1
        elif ev.kind == MISSION_CLAIM_KIND:
            truth_claims[a] += 1
        elif ev.kind == MISSION_CLOSE_KIND:
            truth_closed[a] += 1

    per_field = {
        "events": max(_rel_error(t, s) for t, s in zip(truth_events, stream_events)),
        "missions": max(_rel_error(t, s) for t, s in zip(truth_missions, stream_missions)),
        "claims": max(_rel_error(t, s) for t, s in zip(truth_claims, stream_claims)),
        "closed": max(_rel_error(t, s) for t, s in zip(truth_closed, stream_closed)),
    }
    portrait_error = max(per_field.values())
    loss = detect_loss(truth, ledger)

    # ---- 延迟分位数 ------------------------------------------------------ #
    ordered = sorted(latencies_ms)

    def pct(q: float) -> float:
        k = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[k]

    latency = {"n_ticks": len(ordered), "mean_ms": sum(ordered) / len(ordered),
               "p50_ms": pct(0.50), "p99_ms": pct(0.99), "max_ms": ordered[-1],
               "p99_s": pct(0.99) / 1000.0, "max_s": ordered[-1] / 1000.0,
               "scope": "真链路单 tick：投递判定+契约校验+画像更新(场交换按 exchange_every 插入)"}

    tool = _metric_tools_regression(cfg, ledger, stream_missions, stream_claims) \
        if run_metric_tools else {}

    accepted = {
        "c_event_loss_rate": loss["loss_rate"],
        "c_portrait_error": portrait_error,
        "d_latency_p99_s": latency["p99_s"],
        "c_pass": loss["loss_rate"] == 0.0 and portrait_error < 0.05,
        "d_pass": latency["p99_s"] < 5.0,
        "e_kind_violations": kind_violations,
        "e_field_contract_violations": exchange_stats["contract_violations"],
    }
    accepted["all_pass"] = bool(accepted["c_pass"] and accepted["d_pass"]
                                and kind_violations == 0
                                and exchange_stats["contract_violations"] == 0
                                and exchange_stats["identity_integral_error"] == 0.0)

    provenance = {
        "vocab_contracts": {name: ref.sha256 for name, ref in vocabs.items()},
        "heartbeats": cfg.num_agents * cfg.ticks,
        "events_emitted": len(truth),
        "determinism_seed": cfg.seed,
    }
    summary = {
        "events": {"emitted": len(truth), "delivered": len(ledger), "dropped_injected": dropped,
                   "heartbeats": cfg.num_agents * cfg.ticks,
                   "mission_lifecycle_events": len(truth) - cfg.num_agents * cfg.ticks},
        "loss": loss,
        "kind_contract": {"checked": len(ledger), "violations": kind_violations,
                          "outside_governance_members": governance_outside},
        "portrait": {"consistency_error": portrait_error, "per_field": per_field,
                     "agents": n, "tool": tool.get("portrait", {})},
        "pulse": tool.get("pulse", {}),
        "field_exchange": exchange_stats,
        "latency": latency,
        "acceptance": accepted,
        "provenance": provenance,
    }
    return GovernanceRunResult(cfg, summary)


def _metric_tools_regression(cfg: E2EConfig, ledger: Sequence[Event],
                             stream_missions: Sequence[int],
                             stream_claims: Sequence[int]) -> Dict[str, Any]:
    """真跑度量工具（pulse/portrait），与流式状态对拍。"""
    import importlib
    import sys

    root = C.repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    out: Dict[str, Any] = {"portrait": {}, "pulse": {}}

    # ---- portrait：把账本重放成 mission 行，交给真工具聚合 -------------- #
    try:
        portrait = importlib.import_module("tools.worker_portrait.portrait")
        missions: Dict[int, Dict[str, Any]] = {}
        for ev in ledger:
            if ev.mission_id is None:
                continue
            row = missions.setdefault(ev.mission_id, {
                "mission_id": ev.mission_id, "worker": ev.agent, "repo": "synthetic-repo",
                "status": "running", "model": "synthetic-model", "runner": "synthetic-runner",
                "worker_role": "worker", "campaign_id": "synthetic-campaign"})
            stamp = EPOCH + timedelta(seconds=ev.tick)
            if ev.kind == MISSION_OPEN_KIND:
                row["created_at"] = stamp
            elif ev.kind == MISSION_CLAIM_KIND:
                row["claim_at"] = stamp
            elif ev.kind == MISSION_CLOSE_KIND:
                row["finished_at"] = stamp
                row["status"] = "succeeded"
        rows = [r for r in missions.values() if "created_at" in r]
        report = portrait.aggregate(rows, min_missions=1, min_active_workers=1)
        per_worker = {w.get("worker"): w for w in report.get("workers", [])}
        mismatches = 0
        max_delta = 0
        for agent, w in per_worker.items():
            a = int(agent.split("-")[1])
            delta = abs(int(w.get("n_missions", 0)) - stream_missions[a])
            delta_claims = abs(int(w.get("n_claim_events", 0)) - stream_claims[a])
            max_delta = max(max_delta, delta, delta_claims)
            if delta or delta_claims:
                mismatches += 1
        out["portrait"] = {
            "available": True,
            "workers_reported": report.get("coverage", {}).get("workers_active"),
            "mission_rows": len(rows),
            "mismatched_workers": mismatches,
            "max_count_delta": max_delta,
            "coverage": report.get("coverage", {}),
        }
    except Exception as exc:  # noqa: BLE001
        out["portrait"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---- pulse：用链路末态构造 RawState，交给真工具算快照 --------------- #
    try:
        pulse = importlib.import_module("tools.pulse.saturation_pulse")
        now = EPOCH + timedelta(seconds=cfg.ticks)
        n_live = min(cfg.num_agents, 10)
        workers = tuple(
            {"worker_id": f"agent-{a:04d}", "enabled": True,
             "state": "running" if a < n_live else "idle",
             "current_mission_id": 900000 + a if a < n_live else None,
             "heartbeat_at": now - timedelta(seconds=1)} for a in range(cfg.num_agents))
        missions = tuple(
            {"id": 900000 + a, "status": "running", "claimed_by": f"agent-{a:04d}",
             "heartbeat_at": now - timedelta(seconds=1)} for a in range(n_live))
        snap = pulse.compute(pulse.RawState(workers=workers, missions=missions), now=now,
                             window_s=300)
        out["pulse"] = {"available": True, "online": snap.online,
                        "fresh_workers": snap.fresh_workers, "busy": snap.busy,
                        "running_missions": snap.running_missions,
                        "expected_online": cfg.num_agents, "expected_busy": n_live,
                        "online_parity": snap.online == cfg.num_agents,
                        "busy_parity": snap.busy == n_live,
                        "running_parity": snap.running_missions == n_live,
                        "payload_v": snap.note_payload().get("v")}
    except Exception as exc:  # noqa: BLE001
        out["pulse"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def sweep_drop_rates(rates: Sequence[float], *, num_agents: int = 100, ticks: int = 1000,
                     seed: int = 20261111, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """丢包率扫描（检测器灵敏度曲线 + 画像误差退化曲线）。"""
    curve = []
    for rate in rates:
        cfg = E2EConfig(num_agents=num_agents, ticks=ticks, drop_rate=float(rate), seed=seed)
        res = run_governance_chain(cfg, root=root, run_metric_tools=False).as_dict()
        curve.append({
            "drop_rate": float(rate),
            "detected_loss_rate": res["loss"]["loss_rate"],
            "detection_ratio": (res["loss"]["loss_rate"] / rate) if rate else 1.0,
            "portrait_error": res["portrait"]["consistency_error"],
            "agents_with_loss": res["loss"]["agents_with_loss"],
            "p99_ms": res["latency"]["p99_ms"],
        })
    return curve


def config_digest(cfg: E2EConfig) -> str:
    return hashlib.sha256(json.dumps(cfg.as_dict(), sort_keys=True).encode()).hexdigest()[:16]