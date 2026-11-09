"""DAG dispatch layer: acyclicity, topological order, CPM, resource-saturated
list scheduling.

Everything is deterministic: ties are broken by node id, so the same plan always
produces the same dispatch order, schedule and makespan (needed by C1/C6).

Definitions
-----------
*Critical path* = longest chain in the DAG weighted by node duration.  With
``ES(v) = max_{p in preds(v)} EF(p)`` and ``EF(v) = ES(v) + dur(v)`` the project
duration is ``max_v EF(v)``; the critical path is recovered by walking back from
an arg-max node through the predecessor that attains its ``ES``.

*List scheduling* with capacity ``k`` assigns, in critical-length priority
order, every ready node to the earliest free lane.  It is a greedy
resource-constrained scheduler; it respects dependencies and ``k`` and finishes
within ``makespan >= critical_path`` (asserted by the caller/tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import CycleError, Plan, PlanError


@dataclass(frozen=True)
class ScheduleEntry:
    node_id: str
    lane: int
    start: float
    end: float
    duration: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "lane": self.lane,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
        }


@dataclass(frozen=True)
class Schedule:
    entries: tuple[ScheduleEntry, ...]
    makespan: float
    capacity: int
    critical_path: tuple[str, ...]
    critical_length: float
    waves: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "makespan": self.makespan,
            "capacity": self.capacity,
            "critical_path": list(self.critical_path),
            "critical_length": self.critical_length,
            "waves": [list(w) for w in self.waves],
        }

    def order(self) -> list[str]:
        return [e.node_id for e in sorted(self.entries, key=lambda e: (e.start, e.node_id))]

    def max_parallelism(self) -> int:
        """Peak number of nodes simultaneously in flight."""
        events: list[tuple[float, int]] = []
        for e in self.entries:
            if e.duration <= 0:
                continue
            events.append((e.start, 1))
            events.append((e.end, -1))
        events.sort(key=lambda t: (t[0], t[1]))  # endings before starts at equal time
        cur = peak = 0
        for _, delta in events:
            cur += delta
            peak = max(peak, cur)
        return peak


# ---------------------------------------------------------------------------
def adjacency(plan: Plan) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    nodes = plan.by_id()
    succ: dict[str, list[str]] = {k: [] for k in nodes}
    pred: dict[str, list[str]] = {k: [] for k in nodes}
    for n in plan.nodes:
        for d in n.deps:
            pred[n.node_id].append(d)
            succ[d].append(n.node_id)
    for v in succ.values():
        v.sort()
    for v in pred.values():
        v.sort()
    return succ, pred


def find_cycle(plan: Plan) -> list[str] | None:
    """Return one concrete cycle as a node sequence, or ``None`` if acyclic."""
    succ, _ = adjacency(plan)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {k: WHITE for k in succ}
    stack: list[str] = []

    def visit(v: str) -> list[str] | None:
        colour[v] = GREY
        stack.append(v)
        for w in succ[v]:
            if colour[w] == GREY:
                idx = stack.index(w)
                return stack[idx:] + [w]
            if colour[w] == WHITE:
                found = visit(w)
                if found is not None:
                    return found
        stack.pop()
        colour[v] = BLACK
        return None

    for v in sorted(succ):
        if colour[v] == WHITE:
            found = visit(v)
            if found is not None:
                return found
    return None


def topological_order(plan: Plan) -> list[str]:
    """Kahn's algorithm with a sorted ready set (deterministic)."""
    succ, pred = adjacency(plan)
    indeg = {k: len(v) for k, v in pred.items()}
    ready = sorted(k for k, d in indeg.items() if d == 0)
    out: list[str] = []
    while ready:
        v = ready.pop(0)
        out.append(v)
        for w in succ[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                ready.append(w)
        ready.sort()
    if len(out) != len(pred):
        cycle = find_cycle(plan)
        raise CycleError(cycle or [])
    return out


def waves(plan: Plan) -> list[list[str]]:
    """Dependency levels: wave *i* can run once waves ``< i`` are complete."""
    succ, pred = adjacency(plan)
    level: dict[str, int] = {}
    for v in topological_order(plan):
        level[v] = 1 + max((level[p] for p in pred[v]), default=-1)
    out: list[list[str]] = []
    for v, lv in sorted(level.items(), key=lambda kv: (kv[1], kv[0])):
        while len(out) <= lv:
            out.append([])
        out[lv].append(v)
    return [sorted(w) for w in out]


def critical_path(plan: Plan) -> tuple[list[str], float]:
    """Longest weighted chain (CPM) plus its length."""
    nodes = plan.by_id()
    _, pred = adjacency(plan)
    es: dict[str, float] = {}
    best_pred: dict[str, str | None] = {}
    for v in topological_order(plan):
        cands = [(es[p] + nodes[p].duration, p) for p in pred[v]]
        if cands:
            value, p = max(cands, key=lambda t: (t[0], t[1]))
            es[v] = value
            best_pred[v] = p
        else:
            es[v] = 0.0
            best_pred[v] = None
    ef = {v: es[v] + nodes[v].duration for v in es}
    # ark-max with deterministic tie-break (larger id wins on equal finish)
    end = max(sorted(ef), key=lambda v: (ef[v], v))
    path: list[str] = []
    cur: str | None = end
    while cur is not None:
        path.append(cur)
        cur = best_pred[cur]
    path.reverse()
    return path, ef[end]


def slacks(plan: Plan) -> dict[str, float]:
    """Total slack per node: ``LS - ES`` with ``LS = LF - duration``.

    0 exactly for the nodes on the critical path; larger values are the float
    (time) available before the node starts pushing the project end.
    """
    nodes = plan.by_id()
    _, pred = adjacency(plan)
    succ, _ = adjacency(plan)
    tp = topological_order(plan)
    es: dict[str, float] = {}
    for v in tp:
        es[v] = max((es[p] + nodes[p].duration for p in pred[v]), default=0.0)
    _, length = critical_path(plan)
    lf: dict[str, float] = {}
    for v in reversed(tp):
        lf[v] = min((lf[s] - nodes[s].duration for s in succ[v]), default=length)
    return {v: round(lf[v] - nodes[v].duration - es[v], 10) for v in tp}


def schedule(plan: Plan, capacity: int = 10) -> Schedule:
    """Resource-saturated list scheduling respecting dependencies.

    ``capacity`` is the number of concurrent execution lanes (the campaign
    dispatcher contract requires >= 10).  Event-driven simulation: at every
    instant ``now`` the free lanes are filled with the ready nodes of highest
    remaining-critical-length, then the clock jumps to the next completion.
    """
    if capacity < 1:
        raise PlanError("capacity must be >= 1")
    nodes = plan.by_id()
    succ, pred = adjacency(plan)
    topo = topological_order(plan)
    cp, cp_len = critical_path(plan)
    cp_rank: dict[str, float] = {v: 0.0 for v in topo}
    for v in reversed(topo):
        cp_rank[v] = nodes[v].duration + max((cp_rank[c] for c in succ[v]), default=0.0)

    def priority(v: str) -> tuple[float, str]:
        return (-cp_rank[v], v)

    remaining = {v: len(pred[v]) for v in topo}
    ready = [v for v in topo if remaining[v] == 0]
    ready.sort(key=priority)
    lane_free = [0.0] * capacity
    entries: list[ScheduleEntry] = []
    running: list[tuple[float, str, int]] = []  # (end, node, lane)
    now = 0.0
    guard = 0

    while ready or running:
        guard += 1
        if guard > 10 * len(topo) + 100:  # pragma: no cover - loop safety net
            raise PlanError("scheduler failed to converge")
        free = [i for i in range(capacity) if lane_free[i] <= now + 1e-12]
        while ready and free:
            v = ready.pop(0)
            lane = min(free, key=lambda i: (lane_free[i], i))
            free.remove(lane)
            end = now + nodes[v].duration
            lane_free[lane] = end
            entries.append(ScheduleEntry(v, lane, now, end, nodes[v].duration))
            running.append((end, v, lane))
        if not running:
            if ready:  # pragma: no cover - defensive
                raise PlanError("scheduler stuck with ready nodes and no lanes")
            break
        now = min(t[0] for t in running)
        done = [t for t in running if t[0] <= now + 1e-12]
        running = [t for t in running if t[0] > now + 1e-12]
        for end, v, lane in sorted(done, key=lambda t: t[1]):
            lane_free[lane] = now
            for w in succ[v]:
                remaining[w] -= 1
                if remaining[w] == 0:
                    ready.append(w)
        ready.sort(key=priority)

    makespan = max((e.end for e in entries), default=0.0)
    order = sorted(entries, key=lambda e: (e.start, e.node_id))
    return Schedule(
        entries=tuple(order),
        makespan=makespan,
        capacity=capacity,
        critical_path=tuple(cp),
        critical_length=cp_len,
        waves=tuple(tuple(w) for w in waves(plan)),
    )


def serial_makespan(plan: Plan, turnaround: float = 0.0) -> float:
    """Baseline reference: fully serialised execution, one node at a time.

    ``turnaround`` models the per-node manual hand-off (dispatch write, verdict
    write, coordinator poll) charged once per node.
    """
    order = topological_order(plan)
    nodes = plan.by_id()
    return sum(nodes[v].duration + turnaround for v in order)


def validate_schedule(plan: Plan, sched: Schedule) -> list[str]:
    """Return a list of violated invariants (empty == valid)."""
    problems: list[str] = []
    nodes = plan.by_id()
    seen = {e.node_id: e for e in sched.entries}
    if set(seen) != set(nodes):
        problems.append("schedule does not cover every node exactly once")
    for n in plan.nodes:
        e = seen.get(n.node_id)
        if e is None:
            continue
        for d in n.deps:
            pd = seen.get(d)
            if pd is None:
                problems.append(f"{n.node_id}: dependency {d} unscheduled")
            elif e.start + 1e-9 < pd.end:
                problems.append(f"{n.node_id} starts before dependency {d} finishes")
    # lane capacity: at most `capacity` overlapping intervals
    intervals = [(e.start, e.end) for e in sched.entries if e.duration > 0]
    for i, (s1, e1) in enumerate(intervals):
        overlap = sum(1 for s2, e2 in intervals[i:] if s2 < e1 - 1e-9)
        if overlap > sched.capacity:
            problems.append(f"lane capacity exceeded: {overlap} > {sched.capacity}")
            break
    if sched.makespan + 1e-9 < sched.critical_length:
        problems.append("makespan shorter than critical path length")
    return problems