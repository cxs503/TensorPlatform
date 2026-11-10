"""Dependency graph used by the campaign self-healing engine.

Kept deliberately small and pure-Python (no ORM, no FastAPI) so the healing
semantics can be unit tested and replayed without a database:

* ``validate()`` implements Kahn's algorithm and rejects cycles / dangling
  dependencies (acceptance C7 — healing never introduces cyclic dependencies).
* ``ready_nodes()`` encodes the bypass contract: a ``skipped`` (bypassed)
  upstream node satisfies its dependants exactly like a ``done`` node, so a
  poisoned branch cannot cascade (acceptance C2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class NodeStatus(str, Enum):
    PENDING = "pending"
    IN_FLIGHT = "in-flight"
    DONE = "done"
    FAILED = "failed"
    RETRY_PENDING = "retry_pending"
    SKIPPED = "skipped"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


#: Statuses that unblock dependants (degraded and bypassed results are usable).
SATISFIED_STATUSES = frozenset(
    {NodeStatus.DONE, NodeStatus.DEGRADED, NodeStatus.SKIPPED}
)
#: Statuses that are final and *not* usable by dependants.
POISONED_STATUSES = frozenset({NodeStatus.FAILED, NodeStatus.BLOCKED})
TERMINAL_STATUSES = SATISFIED_STATUSES | POISONED_STATUSES


class DagError(ValueError):
    """Base class for graph validation errors."""


class CycleError(DagError):
    """Raised when the dependency graph contains a cycle."""

    def __init__(self, cycle: list[Any]):
        self.cycle = list(cycle)
        super().__init__(f"cyclic dependency detected: {self.cycle}")


class UnknownDependency(DagError):
    """Raised when a node depends on an id that was never added."""

    def __init__(self, node_id: Any, missing: Iterable[Any]):
        self.node_id = node_id
        self.missing = list(missing)
        super().__init__(
            f"node {node_id!r} depends on unknown node(s): {self.missing}"
        )


@dataclass
class DagNode:
    """One campaign node as seen by the healing engine."""

    node_id: Any
    name: str = ""
    deps: tuple[Any, ...] = ()
    critical: bool = False
    degradable: bool = False
    max_retries: int | None = None
    status: NodeStatus = NodeStatus.PENDING
    attempts: int = 0
    consecutive_fails: int = 0
    next_attempt_at: float | None = None
    defer_until: float | None = None
    result: Any = None
    baseline_result: Any = None
    quality: str = "normal"  # "normal" | "degraded" | "skipped"
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.deps = tuple(self.deps or ())
        if not isinstance(self.status, NodeStatus):
            self.status = NodeStatus(str(self.status))

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_satisfied(self) -> bool:
        return self.status in SATISFIED_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "deps": list(self.deps),
            "critical": self.critical,
            "degradable": self.degradable,
            "max_retries": self.max_retries,
            "status": self.status.value,
            "attempts": self.attempts,
            "consecutive_fails": self.consecutive_fails,
            "next_attempt_at": self.next_attempt_at,
            "defer_until": self.defer_until,
            "result": self.result,
            "baseline_result": self.baseline_result,
            "quality": self.quality,
            "last_error": self.last_error,
            "metadata": dict(self.metadata),
        }


class SelfHealingDag:
    """Insertion-ordered DAG with validation and readiness queries."""

    def __init__(self, name: str = "campaign"):
        self.name = name
        self.nodes: dict[Any, DagNode] = {}
        self._children: dict[Any, list[Any]] = {}

    # ── construction ────────────────────────────────────────────────
    def add_node(
        self,
        node_id: Any,
        name: str = "",
        deps: Iterable[Any] = (),
        *,
        critical: bool = False,
        degradable: bool = False,
        max_retries: int | None = None,
        status: NodeStatus | str = NodeStatus.PENDING,
        consecutive_fails: int = 0,
        attempts: int = 0,
        next_attempt_at: float | None = None,
        defer_until: float | None = None,
        result: Any = None,
        baseline_result: Any = None,
        quality: str = "normal",
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DagNode:
        if node_id in self.nodes:
            raise DagError(f"duplicate node id: {node_id!r}")
        node = DagNode(
            node_id=node_id,
            name=name or str(node_id),
            deps=tuple(deps),
            critical=critical,
            degradable=degradable,
            max_retries=max_retries,
            status=status,
            attempts=attempts,
            consecutive_fails=consecutive_fails,
            next_attempt_at=next_attempt_at,
            defer_until=defer_until,
            result=result,
            baseline_result=baseline_result,
            quality=quality,
            last_error=last_error,
            metadata=dict(metadata or {}),
        )
        self.nodes[node_id] = node
        self._children[node_id] = []
        return node

    # ── queries ─────────────────────────────────────────────────────
    @property
    def edges(self) -> set[tuple[Any, Any]]:
        return {(dep, nid) for nid, node in self.nodes.items() for dep in node.deps}

    def children(self, node_id: Any) -> list[Any]:
        out = []
        for nid, node in self.nodes.items():
            if node_id in node.deps:
                out.append(nid)
        return out

    def validate(self) -> list[Any]:
        """Return a topological order; raise on cycles or dangling deps."""
        indegree: dict[Any, int] = {}
        for nid, node in self.nodes.items():
            missing = [d for d in node.deps if d not in self.nodes]
            if missing:
                raise UnknownDependency(nid, missing)
            indegree[nid] = len(set(node.deps))
        # Kahn, deterministic: insertion order among equal indegrees.
        ready = [nid for nid in self.nodes if indegree[nid] == 0]
        order: list[Any] = []
        while ready:
            nid = ready.pop(0)
            order.append(nid)
            for child in self.children(nid):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(order) != len(self.nodes):
            remaining = [nid for nid in self.nodes if nid not in order]
            raise CycleError(self._find_cycle(remaining))
        return order

    def _find_cycle(self, candidates: list[Any]) -> list[Any]:
        """Return one concrete cycle among ``candidates`` (for audit detail)."""
        allowed = set(candidates)
        for start in candidates:
            path: list[Any] = []
            seen: set[Any] = set()
            nid = start
            while nid is not None and nid in allowed:
                if nid in seen:
                    return path[path.index(nid):] + [nid]
                seen.add(nid)
                path.append(nid)
                node = self.nodes[nid]
                nxt = next((d for d in node.deps if d in allowed), None)
                nid = nxt
        return list(candidates)

    def critical_path(self) -> list[Any]:
        """Longest dependency chain (tie-break for scheduling priority)."""
        order = self.validate()
        best: dict[Any, int] = {}
        for nid in reversed(order):
            best[nid] = 1 + max(
                (best.get(c, 0) for c in self.children(nid)), default=0
            )
        if not order:
            return []
        # Greedy walk from the highest-ranked root; deterministic.
        nid = max(order, key=lambda n: (best[n], -order.index(n)))
        path = [nid]
        while True:
            children = [c for c in self.children(nid) if c in best]
            if not children:
                break
            nid = max(children, key=lambda c: (best[c], -order.index(c)))
            path.append(nid)
        return path

    def priority_nodes(self) -> set[Any]:
        """Scheduling priority: explicit ``critical`` flags win.

        When a campaign does not flag any node (legacy plans), fall back to the
        computed longest dependency chain so saturated clusters still have a
        well-defined priority order.
        """
        flagged = {nid for nid, n in self.nodes.items() if n.critical}
        if flagged:
            return flagged
        return set(self.critical_path())

    def ready_nodes(self) -> list[DagNode]:
        """Pending nodes whose dependencies are all satisfied, in topo order."""
        order = self.validate()
        out = []
        for nid in order:
            node = self.nodes[nid]
            if node.status is not NodeStatus.PENDING:
                continue
            if all(
                d in self.nodes and self.nodes[d].is_satisfied for d in node.deps
            ):
                out.append(node)
        return out

    def poisoned(self, node: DagNode) -> list[Any]:
        """Dependencies of ``node`` that terminally failed (not bypassed)."""
        return [d for d in node.deps if self.nodes[d].status in POISONED_STATUSES]

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.status.value] = counts.get(node.status.value, 0) + 1
        return {
            "name": self.name,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "status_counts": counts,
        }