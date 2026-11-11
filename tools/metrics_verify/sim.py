#!/usr/bin/env python3
"""Synthetic ground-truth generators for the metrics-tool verification chain.

Every stream here is *generated*, so the truth is known by construction: the
simulator keeps the injected numbers (latencies, dropped heartbeats, swapped
events, per-agent outcomes) and the collectors under test are then required to
recover them.  That inversion (truth -> observable -> recovered) is what makes
"the metric tool works" a falsifiable statement instead of a report.

Nothing in this module touches a database, the network or the wall clock; all
timestamps are derived from an explicit epoch so runs are bit-reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .stats import seeded_rng

EPOCH = datetime(2026, 11, 11, 6, 0, 0, tzinfo=timezone.utc)
HEARTBEAT_INTERVAL_S = 1.0


# --------------------------------------------------------------------------- #
# pulse: fleet of agents sending heartbeats over a lossy, jittery link
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FleetConfig:
    """Knobs of the synthetic fleet.

    ``stall_rate`` heartbeats are given a multi-second delay (the failure mode a
    P99 ceiling exists for); ``loss_rate`` heartbeats never reach the collector.
    """

    n_agents: int = 100
    n_heartbeats: int = 1000
    seed: int = 20261111
    median_latency_s: float = 0.08
    sigma: float = 0.9
    stall_rate: float = 0.0
    stall_latency_range_s: Tuple[float, float] = (5.5, 12.0)
    loss_rate: float = 0.0
    enabled_fraction: float = 1.0

    def __post_init__(self) -> None:
        if self.n_agents < 1 or self.n_heartbeats < 1:
            raise ValueError("n_agents and n_heartbeats must be >= 1")
        if not 0.0 <= self.stall_rate < 1.0 or not 0.0 <= self.loss_rate < 1.0:
            raise ValueError("stall_rate / loss_rate must be within [0, 1)")
        if self.median_latency_s <= 0 or self.sigma < 0:
            raise ValueError("median_latency_s must be > 0 and sigma >= 0")


@dataclass
class AgentStream:
    agent_id: str
    enabled: bool = True
    seqs: List[int] = field(default_factory=list)          # delivered sequence numbers
    latencies: List[float] = field(default_factory=list)   # delivered one-way latencies (s)
    received: List[datetime] = field(default_factory=list) # arrival instants
    dropped_seqs: List[int] = field(default_factory=list)  # never arrived
    stalled_seqs: List[int] = field(default_factory=list)  # arrived late (>= stall floor)
    state: str = "idle"
    mission_id: Optional[int] = None

    @property
    def last_received(self) -> Optional[datetime]:
        return max(self.received) if self.received else None


@dataclass
class FleetSim:
    config: FleetConfig
    agents: List[AgentStream]

    @property
    def delivered_latencies(self) -> List[float]:
        return [lat for a in self.agents for lat in a.latencies]

    @property
    def all_dropped(self) -> List[Tuple[str, int]]:
        return [(a.agent_id, s) for a in self.agents for s in a.dropped_seqs]

    @property
    def all_stalled(self) -> List[Tuple[str, int]]:
        return [(a.agent_id, s) for a in self.agents for s in a.stalled_seqs]

    def last_heartbeats(self) -> Dict[str, datetime]:
        return {a.agent_id: a.last_received for a in self.agents if a.last_received is not None}


def simulate_fleet(cfg: Optional[FleetConfig] = None) -> FleetSim:
    """Generate ``n_agents`` heartbeat streams with known truth."""
    cfg = cfg or FleetConfig()
    rng = seeded_rng(cfg.seed)
    agents: List[AgentStream] = []
    n_enabled = max(1, int(round(cfg.n_agents * cfg.enabled_fraction)))
    for i in range(cfg.n_agents):
        agent = AgentStream(agent_id=f"agent-{i:03d}", enabled=i < n_enabled)
        # realistic fleet mix: half the agents are on a mission, half idle
        agent.state = "running" if i % 2 == 0 else "idle"
        agent.mission_id = 9000 + i if agent.state == "running" else None
        for seq in range(1, cfg.n_heartbeats + 1):
            send_ts = EPOCH + timedelta(seconds=HEARTBEAT_INTERVAL_S * seq, milliseconds=17 * i)
            if rng.random() < cfg.loss_rate:
                agent.dropped_seqs.append(seq)
                continue
            if rng.random() < cfg.stall_rate:
                lo, hi = cfg.stall_latency_range_s
                latency = rng.uniform(lo, hi)
                agent.stalled_seqs.append(seq)
            else:
                z = rng.gauss(0.0, 1.0)
                latency = cfg.median_latency_s * math.exp(cfg.sigma * z)
            agent.seqs.append(seq)
            agent.latencies.append(latency)
            agent.received.append(send_ts + timedelta(seconds=latency))
        agents.append(agent)
    return FleetSim(config=cfg, agents=agents)


# --------------------------------------------------------------------------- #
# chronicle: event stream shaped like tensor_agent_session_events
# --------------------------------------------------------------------------- #
EVENT_KINDS: Tuple[str, ...] = (
    "mission_created",
    "mission_claimed",
    "mission_succeeded",
    "mission_failed",
)


@dataclass
class EventStream:
    events: List[Dict[str, Any]]
    injected_disorder_ids: List[str] = field(default_factory=list)  # event ids moved out of order
    expected_per_session: Dict[str, int] = field(default_factory=dict)
    injected_disorder_positions: List[int] = field(default_factory=list)  # index of the inverted pair

    def ids(self) -> List[str]:
        return [e["id"] for e in self.events]


def simulate_event_stream(n_events: int = 10000, *, sessions: int = 5, seed: int = 20261111,
                          disorder_rate: float = 0.0,
                          drop_rate: float = 0.0,
                          duplicate_rate: float = 0.0) -> EventStream:
    """Generate the honest stream, then apply the requested fault injections.

    Column names follow the deployed table ``tensor_agent_session_events``
    (``id, session_id, seq, kind, payload, created_at``); ``seq`` is per session
    and ``created_at`` is monotone within a session *by definition* of an
    append-only event log.  Both invariants are what the validator checks.
    """
    if n_events < 1:
        raise ValueError("n_events must be >= 1")
    rng = seeded_rng(seed)
    session_ids = [f"sess-{i:02d}" for i in range(max(1, sessions))]
    counters: Dict[str, int] = {s: 0 for s in session_ids}
    events: List[Dict[str, Any]] = []
    ts = EPOCH
    for _ in range(n_events):
        session = rng.choice(session_ids)
        counters[session] += 1
        seq = counters[session]
        ts = ts + timedelta(milliseconds=rng.randint(1, 20))
        events.append({
            "id": f"evt-{len(events):06d}",
            "session_id": session,
            "seq": seq,
            "kind": EVENT_KINDS[(seq + len(session)) % len(EVENT_KINDS)],
            "payload": {"mission_id": 9000 + seq, "session": session},
            "created_at": ts.isoformat(),
        })
    expected = dict(counters)

    # --- fault injection: out-of-order (adjacent transposition, same session) --
    injected: List[str] = []
    injected_positions: List[int] = []
    if disorder_rate > 0:
        n_target = max(1, int(round(len(events) * disorder_rate)))
        idx = 0
        while len(injected) < n_target and idx + 1 < len(events):
            if (events[idx]["session_id"] == events[idx + 1]["session_id"]
                    and events[idx]["id"] not in injected
                    and events[idx + 1]["id"] not in injected):
                events[idx], events[idx + 1] = events[idx + 1], events[idx]
                injected.extend([events[idx]["id"], events[idx + 1]["id"]])
                injected_positions.append(idx + 1)  # the inversion is observable here
            idx += 2

    # --- fault injection: duplicates ---------------------------------------
    if duplicate_rate > 0:
        n_dup = max(1, int(round(len(events) * duplicate_rate)))
        for _ in range(n_dup):
            k = rng.randrange(len(events))
            events.insert(k, dict(events[k]))

    # --- fault injection: losses -------------------------------------------
    if drop_rate > 0:
        n_drop = max(1, int(round(len(events) * drop_rate)))
        drop_idx = sorted(rng.sample(range(len(events)), n_drop), reverse=True)
        for k in drop_idx:
            events.pop(k)

    # expected counts stay the pre-injection truth: losses/duplicates are then
    # visible as completeness deviations instead of being hidden.
    return EventStream(events=events, injected_disorder_ids=sorted(set(injected)),
                       expected_per_session=expected,
                       injected_disorder_positions=sorted(injected_positions))


# --------------------------------------------------------------------------- #
# portrait: agents with known success rate / wall time / capability domain mix
# --------------------------------------------------------------------------- #
CAPABILITY_DOMAINS: Tuple[str, ...] = ("domain_alpha", "domain_beta", "domain_gamma")


@dataclass(frozen=True)
class AgentTruth:
    worker: str
    p_success: float
    median_wall_s: float
    sigma_wall: float
    domain_weights: Tuple[float, ...]

    def normalised_weights(self) -> Tuple[float, ...]:
        total = sum(self.domain_weights)
        return tuple(w / total for w in self.domain_weights)


@dataclass
class PortraitSim:
    rows: List[Dict[str, Any]]
    truth: Dict[str, Dict[str, Any]]   # worker -> ground truth aggregates


def simulate_portrait(*, n_agents: int = 8, missions_per_agent: int = 200,
                      seed: int = 20261111,
                      injected_status_flips: int = 0) -> PortraitSim:
    """Generate mission rows + the simulator-side truth they must reproduce.

    Truth is accumulated by the generator (counts of committed outcomes, sum of
    wall seconds, per-domain tallies) *before* serialisation, so comparing it to
    ``worker_portrait.portrait.aggregate`` measures the whole pipeline
    (ISO-8601 rounding, status bucketing, denominator choice) rather than
    re-running one implementation twice.
    """
    if n_agents < 3:
        raise ValueError("n_agents must be >= 3 (worker_portrait needs >=3 active workers)")
    rng = seeded_rng(seed)
    truths: List[AgentTruth] = []
    for i in range(n_agents):
        weights = (
            0.5 + 0.4 * ((i + 1) % 3) / 3.0,
            0.3 + 0.2 * (i % 2),
            0.2 + 0.1 * ((i + 2) % 3) / 3.0,
        )
        truths.append(AgentTruth(
            worker=f"worker-{i:02d}",
            p_success=0.55 + 0.4 * ((i * 7) % 10) / 10.0,
            median_wall_s=600.0 + 120.0 * i,
            sigma_wall=0.45,
            domain_weights=weights,
        ))

    rows: List[Dict[str, Any]] = []
    truth: Dict[str, Dict[str, Any]] = {}
    mission_id = 10000
    for agent in truths:
        n_ok = 0
        wall_sum = 0.0
        domain_counts = {d: 0 for d in CAPABILITY_DOMAINS}
        for _ in range(missions_per_agent):
            mission_id += 1
            created = EPOCH + timedelta(seconds=mission_id * 3)
            pickup = rng.uniform(5.0, 60.0)
            wall = agent.median_wall_s * math.exp(agent.sigma_wall * rng.gauss(0.0, 1.0))
            ok = rng.random() < agent.p_success
            domain = rng.choices(CAPABILITY_DOMAINS, weights=agent.domain_weights, k=1)[0]
            rows.append({
                "mission_id": mission_id,
                "worker": agent.worker,
                "model": "test-model",
                "runner": "verify-rig",
                "worker_role": "builder",
                "repo": domain,
                "status": "succeeded" if ok else "failed",
                "campaign_id": 1,
                "created_at": created.isoformat(),
                "claim_at": (created + timedelta(seconds=pickup)).isoformat(),
                "finished_at": (created + timedelta(seconds=pickup + wall)).isoformat(),
            })
            if ok:
                n_ok += 1
            wall_sum += wall
            domain_counts[domain] += 1
        truth[agent.worker] = {
            "n_missions": missions_per_agent,
            "success_rate": n_ok / missions_per_agent,
            "mean_wall_s": wall_sum / missions_per_agent,
            "domain_share": {d: domain_counts[d] / missions_per_agent for d in CAPABILITY_DOMAINS},
        }

    # fault injection: silently re-label settled successes as failures.  A correct
    # portrait must move by the injected amount, which is how the rig proves the
    # consistency check is sensitive rather than tautological.
    if injected_status_flips > 0:
        candidates = [i for i, r in enumerate(rows) if r["status"] == "succeeded"]
        for k in rng.sample(candidates, min(injected_status_flips, len(candidates))):
            rows[k]["status"] = "failed"
    return PortraitSim(rows=rows, truth=truth)


# --------------------------------------------------------------------------- #
# drift: per-event success stream with a regime change at a known index
# --------------------------------------------------------------------------- #
@dataclass
class DriftStream:
    outcomes: List[int]          # 1 = success, 0 = failure
    change_index: Optional[int]  # index of the first post-change event (None = stable)


def simulate_drift_stream(*, n_events: int = 300, change_index: Optional[int] = 150,
                          p_before: float = 0.9, p_after: float = 0.5,
                          seed: int = 20261111) -> DriftStream:
    rng = seeded_rng(seed)
    outcomes: List[int] = []
    for i in range(n_events):
        p = p_before if (change_index is None or i < change_index) else p_after
        outcomes.append(1 if rng.random() < p else 0)
    return DriftStream(outcomes=outcomes, change_index=change_index)