"""Self-healing state machine for campaign DAG nodes.

Responsibilities (mission node P162W1-N08):

1. **Automatic retry** — ``report_failure`` schedules an exponential-backoff
   retry while ``consecutive_fails <= max_retries``.
2. **Bypass routing** — once retries are exhausted (and no degraded result is
   acceptable) the node is marked ``skipped`` so its dependants keep running.
3. **Degraded execution** — a caller-supplied degraded result is accepted only
   if it deviates from the last normal result by less than the configured
   tolerance, and is then labelled ``DEGRADED``.
4. **Resource-saturation awareness** — ``schedule`` compares the worst load
   ratio against the threshold and emits a ``saturation_detected`` event.
5. **Elastic scheduling** — saturated clusters run critical-path work first and
   defer the rest by a bounded delay (< ``max_defer_s``).
6. **Audit chain** — every action above lands in the hash-chained
   ``AuditChain``; no failure ⇒ no healing event (zero false positives).
7. **Acyclic invariant** — the graph is validated on construction and every
   healing action leaves the edge set untouched.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .audit import AuditChain, AuditEvent
from .dag import DagNode, NodeStatus, SelfHealingDag
from .policy import (
    LoadSample,
    ResourcePolicy,
    RetryPolicy,
    SelfHealPolicy,
)

_UNSET = object()


def relative_deviation(value: float, baseline: float) -> float:
    """``|value - baseline| / max(|baseline|, eps)`` as a fraction."""
    denom = max(abs(float(baseline)), 1e-12)
    return abs(float(value) - float(baseline)) / denom


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class SelfHealEngine:
    """Healing controller bound to one campaign DAG."""

    def __init__(
        self,
        dag: SelfHealingDag,
        policy: SelfHealPolicy | None = None,
        audit: AuditChain | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.dag = dag
        self.policy = policy or SelfHealPolicy()
        self.audit = audit or AuditChain()
        self._clock = clock or time.time
        self._degraded_executors: dict[Any, Callable[[DagNode], Any]] = {}
        #: node ids touched since the engine was built (persistence hint).
        self.dirty: set[Any] = set()
        #: The graph invariant is enforced on every construction.
        self.dag.validate()

    # ── helpers ─────────────────────────────────────────────────────
    def _now(self, now: float | None) -> float:
        return float(now) if now is not None else float(self._clock())

    def _retry_policy(self, node: DagNode) -> RetryPolicy:
        if node.max_retries is None:
            return self.policy.retry
        return RetryPolicy(
            max_retries=node.max_retries,
            base_delay_s=self.policy.retry.base_delay_s,
            factor=self.policy.retry.factor,
            max_delay_s=self.policy.retry.max_delay_s,
            jitter_ratio=self.policy.retry.jitter_ratio,
        )

    def node(self, node_id: Any) -> DagNode:
        try:
            return self.dag.nodes[node_id]
        except KeyError:
            raise KeyError(f"unknown campaign node: {node_id!r}") from None

    def bind_degraded_executor(
        self, node_id: Any, executor: Callable[[DagNode], Any]
    ) -> None:
        """Register an in-process fallback used when retries are exhausted."""
        self.node(node_id)  # existence check
        self._degraded_executors[node_id] = executor

    def set_baseline(self, node_id: Any, value: Any) -> None:
        """Record the normal-precision result used by the C3 deviation guard."""
        node = self.node(node_id)
        node.baseline_result = value
        self.dirty.add(node_id)

    # ── lifecycle reports ───────────────────────────────────────────
    def report_success(
        self, node_id: Any, result: Any = _UNSET, now: float | None = None
    ) -> dict[str, Any]:
        ts = self._now(now)
        node = self.node(node_id)
        node.attempts += 1
        recovered = node.consecutive_fails > 0
        prior_fails = node.consecutive_fails
        node.consecutive_fails = 0
        node.status = NodeStatus.DONE
        node.quality = "normal"
        node.next_attempt_at = None
        node.defer_until = None
        node.last_error = None
        if result is not _UNSET:
            node.result = result
            node.baseline_result = result
        self.dirty.add(node_id)
        if recovered:
            self.audit.append(
                ts,
                node_id,
                "recovered",
                "done",
                attempts=node.attempts,
                prior_consecutive_fails=prior_fails,
            )
        else:
            self.audit.append(ts, node_id, "node_succeeded", "done")
        return {
            "node_id": node_id,
            "action": "recovered" if recovered else "succeeded",
            "status": node.status.value,
            "attempts": node.attempts,
            "prior_consecutive_fails": prior_fails,
        }

    def report_failure(
        self,
        node_id: Any,
        error: str = "",
        now: float | None = None,
        degraded_result: Any = _UNSET,
    ) -> dict[str, Any]:
        ts = self._now(now)
        node = self.node(node_id)
        node.attempts += 1
        node.consecutive_fails += 1
        node.last_error = str(error or "")
        node.defer_until = None
        policy = self._retry_policy(node)
        self.dirty.add(node_id)
        self.audit.append(
            ts, node_id, "node_failed", "failed", error=node.last_error,
            consecutive_fails=node.consecutive_fails, attempts=node.attempts,
        )

        if node.consecutive_fails <= policy.max_retries:
            delay = policy.delay_for(node.consecutive_fails)
            node.status = NodeStatus.RETRY_PENDING
            node.next_attempt_at = ts + delay
            node.metadata["last_delay_s"] = delay
            self.audit.append(
                ts,
                node_id,
                "retry_scheduled",
                "pending",
                consecutive_fails=node.consecutive_fails,
                max_retries=policy.max_retries,
                delay_s=delay,
                next_attempt_at=node.next_attempt_at,
            )
            return {
                "node_id": node_id,
                "action": "retry_scheduled",
                "status": node.status.value,
                "consecutive_fails": node.consecutive_fails,
                "max_retries": policy.max_retries,
                "delay_s": delay,
                "next_attempt_at": node.next_attempt_at,
            }

        if node.degradable and self.policy.degradation.enabled:
            return self._degrade(node, ts, degraded_result)
        return self._bypass(node, ts, reason="retries_exhausted")

    def _degrade(
        self, node: DagNode, ts: float, degraded_result: Any
    ) -> dict[str, Any]:
        result = degraded_result
        source = "caller"
        if result is _UNSET:
            executor = self._degraded_executors.get(node.node_id)
            if executor is not None:
                try:
                    result = executor(node)
                    source = "executor"
                except Exception as exc:  # noqa: BLE001 - degrade must not crash
                    result = _UNSET
                    source = f"executor_error:{exc}"
        if result is _UNSET:
            return self._bypass(node, ts, reason="no_degraded_result")

        deviation = None
        if _is_number(result) and _is_number(node.baseline_result):
            deviation = relative_deviation(result, node.baseline_result)
            if deviation > self.policy.degradation.max_deviation:
                self.audit.append(
                    ts,
                    node.node_id,
                    "degrade_rejected",
                    "rejected",
                    deviation=deviation,
                    max_deviation=self.policy.degradation.max_deviation,
                    degraded_result=result,
                    baseline_result=node.baseline_result,
                )
                return self._bypass(node, ts, reason="deviation_exceeded")

        node.status = NodeStatus.DEGRADED
        node.quality = "degraded"
        node.result = result
        node.next_attempt_at = None
        self.audit.append(
            ts,
            node.node_id,
            "degraded",
            "degraded",
            source=source,
            consecutive_fails=node.consecutive_fails,
            deviation=deviation,
            max_deviation=self.policy.degradation.max_deviation,
            degraded_result=result,
            baseline_result=node.baseline_result,
        )
        return {
            "node_id": node.node_id,
            "action": "degraded",
            "status": node.status.value,
            "quality": node.quality,
            "deviation": deviation,
            "result": result,
        }

    def _bypass(self, node: DagNode, ts: float, reason: str) -> dict[str, Any]:
        if self.policy.bypass_on_exhaust:
            node.status = NodeStatus.SKIPPED
            node.quality = "skipped"
            node.next_attempt_at = None
            self.audit.append(
                ts,
                node.node_id,
                "bypassed",
                "skipped",
                reason=reason,
                consecutive_fails=node.consecutive_fails,
            )
            return {
                "node_id": node.node_id,
                "action": "bypassed",
                "status": node.status.value,
                "reason": reason,
            }
        node.status = NodeStatus.FAILED
        node.next_attempt_at = None
        self.audit.append(
            ts,
            node.node_id,
            "escalated",
            "failed",
            reason=reason,
            consecutive_fails=node.consecutive_fails,
        )
        return {
            "node_id": node.node_id,
            "action": "escalated",
            "status": node.status.value,
            "reason": reason,
        }

    # ── scheduler ───────────────────────────────────────────────────
    def resolve_blocked(self, now: float | None = None) -> list[dict[str, Any]]:
        """Mark pending nodes whose upstream terminally failed as ``blocked``."""
        ts = self._now(now)
        out = []
        for node in list(self.dag.nodes.values()):
            if node.status is not NodeStatus.PENDING:
                continue
            poisoned = self.dag.poisoned(node)
            if not poisoned:
                continue
            node.status = NodeStatus.BLOCKED
            node.last_error = f"upstream_failed:{sorted(map(str, poisoned))}"
            self.dirty.add(node.node_id)
            self.audit.append(
                ts,
                node.node_id,
                "dependency_blocked",
                "blocked",
                dependencies=poisoned,
            )
            out.append(
                {"node_id": node.node_id, "action": "dependency_blocked",
                 "status": node.status.value}
            )
        return out

    def advance(self, now: float | None = None) -> list[dict[str, Any]]:
        """Dispatch retry-pending nodes whose backoff has elapsed."""
        ts = self._now(now)
        out = []
        for node in self.dag.nodes.values():
            if node.status is not NodeStatus.RETRY_PENDING:
                continue
            due = node.next_attempt_at or 0.0
            if due > ts:
                continue
            node.status = NodeStatus.IN_FLIGHT
            node.next_attempt_at = None
            self.dirty.add(node.node_id)
            self.audit.append(
                ts,
                node.node_id,
                "retry_dispatched",
                "dispatched",
                attempt=node.attempts + 1,
                delay_s=node.metadata.get("last_delay_s"),
            )
            out.append(
                {"node_id": node.node_id, "action": "retry_dispatched",
                 "status": node.status.value, "due_at": due}
            )
        return out

    def _defer_delay(self, ratio: float) -> float:
        rp = self.policy.resources
        span = max(1.0 - rp.saturation_threshold, 1e-9)
        frac = min(max((ratio - rp.saturation_threshold) / span, 0.0), 1.0)
        # Strictly below max_defer_s so the C5 "< 30 s" bound always holds.
        frac = min(frac, 1.0 - 1e-6)
        return round(rp.max_defer_s * frac, 6)

    def schedule(
        self, now: float | None = None, load: LoadSample | None = None
    ) -> dict[str, Any]:
        ts = self._now(now)
        load = load or LoadSample()
        rp: ResourcePolicy = self.policy.resources
        ratio = load.saturation()
        saturated = ratio > rp.saturation_threshold
        dispatched: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []

        if saturated:
            self.audit.append(
                ts,
                None,
                "saturation_detected",
                "saturated",
                saturation=ratio,
                threshold=rp.saturation_threshold,
                load=load.to_dict(),
            )

        priority = self.dag.priority_nodes()
        for node in self.dag.ready_nodes():
            is_critical = node.critical or node.node_id in priority
            if node.defer_until is not None and ts < node.defer_until:
                deferred.append(
                    {
                        "node_id": node.node_id,
                        "defer_until": node.defer_until,
                        "delay_s": round(node.defer_until - ts, 6),
                        "already_deferred": True,
                    }
                )
                continue
            if saturated and not is_critical:
                delay = self._defer_delay(ratio)
                node.defer_until = ts + delay
                self.dirty.add(node.node_id)
                self.audit.append(
                    ts,
                    node.node_id,
                    "schedule_deferred",
                    "deferred",
                    saturation=ratio,
                    delay_s=delay,
                    defer_until=node.defer_until,
                )
                deferred.append(
                    {
                        "node_id": node.node_id,
                        "defer_until": node.defer_until,
                        "delay_s": delay,
                        "already_deferred": False,
                    }
                )
                continue
            node.status = NodeStatus.IN_FLIGHT
            node.defer_until = None
            self.dirty.add(node.node_id)
            self.audit.append(
                ts,
                node.node_id,
                "schedule_dispatched",
                "dispatched",
                saturated=saturated,
                critical=is_critical,
            )
            dispatched.append(
                {"node_id": node.node_id, "critical": is_critical, "delay_s": 0.0}
            )

        return {
            "timestamp": ts,
            "saturated": saturated,
            "saturation": ratio,
            "threshold": rp.saturation_threshold,
            "max_defer_s": rp.max_defer_s,
            "dispatched": dispatched,
            "deferred": deferred,
            "max_scheduling_latency_s": max(
                [d["delay_s"] for d in deferred if not d["already_deferred"]], default=0.0
            ),
        }

    def step(
        self,
        now: float | None = None,
        load: LoadSample | None = None,
    ) -> dict[str, Any]:
        """One healing tick: unblock, dispatch due retries, schedule."""
        ts = self._now(now)
        blocked = self.resolve_blocked(ts)
        advanced = self.advance(ts)
        sched = self.schedule(ts, load=load)
        return {"blocked": blocked, "advanced": advanced, "schedule": sched}

    # ── reporting ───────────────────────────────────────────────────
    def heal_audit(self) -> list[AuditEvent]:
        return self.audit.heal_events()

    def summary(self) -> dict[str, Any]:
        heal = self.audit.heal_events()
        by_action: dict[str, int] = {}
        for ev in heal:
            by_action[ev.action] = by_action.get(ev.action, 0) + 1
        return {
            "dag": self.dag.summary(),
            "critical_path": list(self.dag.critical_path()),
            "policy": self.policy.to_dict(),
            "audit_events": len(self.audit),
            "heal_events": len(heal),
            "heal_actions": by_action,
            "audit_integrity": self.audit.verify(),
        }

    def state(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "nodes": [n.to_dict() for n in self.dag.nodes.values()],
        }