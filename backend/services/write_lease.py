"""
Concurrent Write Ownership Governance — 并发写所有权治理模块

提供 worktree 级写租约、等待图死锁检测、静默覆写归因。

核心组件:
  - WriteLeaseManager:  租约协议 acquire/release/heartbeat/reclaim
  - WaitGraph:          等待图 + Tarjan SCC 死锁检测
  - CASProtocol:        写前期望哈希 + 写后内容哈希 → 丢失更新检出
  - AuditLedger:        JSON-lines 审计台账，支持等待图重建

零领域语义：所有路径/仓名使用合成命名，不含任何域词表。

验收判据:
  D1 线性化: 40并发对同一文件读-改-写各100轮 → 成功写=100, 丢失=0
  D2 死锁检出: 2环/4环/自环+TTL竞争15例 → 15/15检出
  D3 零误杀: 不相交路径10^4次acquire → 拒绝=0, 吞吐降≤10%
  D4 崩溃回收: SIGKILL 10例 → TTL+1s内回收
  D5 shadow无副作用: 200次真实写字节级不变
  D6 零领域语义: 域词表扫描0命中
  D7 可复现审计: 任取一次可从台账重建等待图
"""

import hashlib
import json
import os
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------

class LeaseMode(str, Enum):
    RW = "rw"
    RO = "ro"


class LeaseState(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class Lease:
    """写租约"""
    lease_id: str
    holder: str
    paths: List[str]
    mode: LeaseMode
    ttl: float
    created_at: float
    expires_at: float
    state: LeaseState = LeaseState.ACTIVE
    heartbeat_at: float = 0.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.monotonic()
        return now > self.expires_at

    def to_dict(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "holder": self.holder,
            "paths": self.paths,
            "mode": self.mode.value,
            "ttl": self.ttl,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "state": self.state.value,
            "heartbeat_at": self.heartbeat_at,
        }


@dataclass
class WaitEdge:
    """等待图边"""
    waiter: str
    holder: str
    paths: List[str]
    since: float


@dataclass
class DeadlockCycle:
    """死锁环"""
    participants: List[str]
    edges: List[Tuple[str, str]]
    resolved_by: Optional[str] = None
    resolution_reason: str = ""


@dataclass
class LostUpdateEvent:
    """静默覆写/丢失更新事件"""
    path: str
    holder: str
    expected_hash: str
    actual_hash: str
    timestamp: float
    lease_id: Optional[str] = None


@dataclass
class AcquireResult:
    """acquire 返回结果"""
    granted: bool
    lease_id: Optional[str] = None
    reason: Optional[str] = None
    conflict_with: Optional[str] = None
    deadlock_detected: bool = False
    cycle: Optional[DeadlockCycle] = None


# ---------------------------------------------------------------------------
# Audit Ledger
# ---------------------------------------------------------------------------

class AuditLedger:
    """JSON-lines 审计台账"""

    def __init__(self, path: Optional[str] = None):
        self._path = path
        self._lock = threading.Lock()
        self._entries: List[dict] = []
        if path and os.path.exists(path):
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._entries.append(json.loads(line))

    def record(self, event_type: str, **kwargs):
        entry = {
            "ts": time.time(),
            "mono": time.monotonic(),
            "event": event_type,
            **kwargs,
        }
        with self._lock:
            self._entries.append(entry)
            if self._path:
                with open(self._path, "a") as f:
                    f.write(json.dumps(entry) + "\n")

    def entries(self) -> List[dict]:
        with self._lock:
            return list(self._entries)

    def rebuild_wait_graph_at(self, mono_time: float) -> List[Tuple[str, str]]:
        """从台账重建某时刻的等待图 (D7)"""
        active_waits: Dict[Tuple[str, str], WaitEdge] = {}
        for e in self._entries:
            if e["mono"] > mono_time:
                break
            ev = e["event"]
            if ev == "wait_start":
                key = (e["waiter"], e["holder"])
                active_waits[key] = WaitEdge(
                    waiter=e["waiter"], holder=e["holder"],
                    paths=e.get("paths", []), since=e["mono"]
                )
            elif ev == "wait_end":
                key = (e["waiter"], e["holder"])
                active_waits.pop(key, None)
        return [(w.waiter, w.holder) for w in active_waits.values()]


# ---------------------------------------------------------------------------
# Wait Graph with Tarjan SCC
# ---------------------------------------------------------------------------

class WaitGraph:
    """等待图 + Tarjan SCC 死锁检测"""

    def __init__(self):
        self._edges: Dict[str, Set[str]] = defaultdict(set)
        self._edge_meta: Dict[Tuple[str, str], WaitEdge] = {}

    def add_edge(self, waiter: str, holder: str, paths: List[str]):
        self._edges[waiter].add(holder)
        self._edge_meta[(waiter, holder)] = WaitEdge(
            waiter=waiter, holder=holder, paths=paths, since=time.monotonic()
        )

    def remove_edge(self, waiter: str, holder: str):
        self._edges[waiter].discard(holder)
        self._edge_meta.pop((waiter, holder), None)
        if not self._edges[waiter]:
            del self._edges[waiter]

    def remove_node(self, node: str):
        self._edges.pop(node, None)
        for waiter in list(self._edges.keys()):
            self._edges[waiter].discard(node)
            if not self._edges[waiter]:
                del self._edges[waiter]
        keys_to_remove = [k for k in self._edge_meta if node in k]
        for k in keys_to_remove:
            del self._edge_meta[k]

    def find_cycles(self) -> List[List[str]]:
        """Tarjan SCC"""
        index_counter = [0]
        stack = []
        lowlink = {}
        index = {}
        on_stack = {}
        sccs = []

        all_nodes = set(self._edges.keys())
        for targets in self._edges.values():
            all_nodes.update(targets)

        def strongconnect(v):
            index[v] = index_counter[0]
            lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True

            for w in self._edges.get(v, set()):
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], index[w])

            if lowlink[v] == index[v]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.append(w)
                    if w == v:
                        break
                if len(scc) > 1:
                    sccs.append(scc)
                elif len(scc) == 1 and scc[0] in self._edges.get(scc[0], set()):
                    sccs.append(scc)

        for v in sorted(all_nodes):
            if v not in index:
                strongconnect(v)

        return sccs

    def snapshot(self) -> Dict[str, List[str]]:
        return {k: list(v) for k, v in self._edges.items()}


# ---------------------------------------------------------------------------
# Write Lease Manager
# ---------------------------------------------------------------------------

class WriteLeaseManager:
    """并发写所有权管理器"""

    def __init__(self, audit_path: Optional[str] = None, shadow_mode: bool = False):
        self._lock = threading.RLock()
        self._leases: Dict[str, Lease] = {}
        self._holder_leases: Dict[str, Set[str]] = defaultdict(set)
        self._path_leases: Dict[str, Set[str]] = defaultdict(set)
        self._wait_graph = WaitGraph()
        self._audit = AuditLedger(audit_path)
        self._shadow_mode = shadow_mode
        self._lost_updates: List[LostUpdateEvent] = []
        self._file_hashes: Dict[str, str] = {}
        self._deadlock_cycles: List[DeadlockCycle] = []
        self._waiting_holders: Set[str] = set()
        # Performance: track last reclaim time to avoid O(n) scan on every acquire
        self._last_reclaim_mono: float = 0.0
        self._reclaim_interval: float = 1.0  # seconds

    @property
    def shadow_mode(self) -> bool:
        return self._shadow_mode

    @shadow_mode.setter
    def shadow_mode(self, value: bool):
        self._shadow_mode = value

    def _find_conflicts(self, paths: List[str], exclude_holder: str) -> Set[str]:
        """Find active lease holders conflicting with given paths"""
        conflicting: Set[str] = set()
        for p in paths:
            for lid in self._path_leases.get(p, set()):
                lease = self._leases.get(lid)
                if lease and lease.holder != exclude_holder and lease.state == LeaseState.ACTIVE:
                    conflicting.add(lease.holder)
        return conflicting

    def _maybe_reclaim(self, now: float):
        """Periodic reclaim to avoid O(n) scan on every acquire"""
        if now - self._last_reclaim_mono > self._reclaim_interval:
            self._reclaim_expired_locked(now)
            self._last_reclaim_mono = now

    def acquire(self, paths: List[str], holder: str, ttl: float,
                mode: LeaseMode = LeaseMode.RW) -> AcquireResult:
        """获取写租约"""
        now = time.monotonic()

        with self._lock:
            # Periodic reclaim (not on every call for performance)
            self._maybe_reclaim(now)

            # Check for conflicts
            conflicting_holders = self._find_conflicts(paths, holder)

            if not conflicting_holders:
                return self._grant_lease(paths, holder, ttl, mode, now)

            # There are conflicts
            if self._shadow_mode:
                self._audit.record("shadow_conflict", holder=holder,
                                 paths=paths, conflicts=list(conflicting_holders))
                return self._grant_lease(paths, holder, ttl, mode, now)

            # Enforcing mode: record wait edges
            for ch in conflicting_holders:
                if ch not in self._wait_graph._edges.get(holder, set()):
                    self._wait_graph.add_edge(holder, ch, paths)
                    self._audit.record("wait_start", waiter=holder, holder=ch, paths=paths)
            self._waiting_holders.add(holder)

            # Check for deadlock
            cycles = self._wait_graph.find_cycles()
            if cycles:
                cycle = self._resolve_deadlock(cycles, now)
                self._clear_wait_edges(holder)

                if cycle and cycle.resolved_by == holder:
                    return AcquireResult(
                        granted=False,
                        reason="deadlock_victim",
                        deadlock_detected=True,
                        cycle=cycle,
                    )

                # Victim was someone else — check if we can now grant
                remaining_conflicts = self._find_conflicts(paths, holder)
                if not remaining_conflicts:
                    return self._grant_lease(paths, holder, ttl, mode, now)
                else:
                    return AcquireResult(
                        granted=False,
                        reason="conflict",
                        conflict_with=list(remaining_conflicts)[0],
                    )
            else:
                # No deadlock — deny but keep wait edges
                return AcquireResult(
                    granted=False,
                    reason="conflict",
                    conflict_with=list(conflicting_holders)[0],
                )

    def _grant_lease(self, paths, holder, ttl, mode, now) -> AcquireResult:
        """Internal: create and register a new lease"""
        lease_id = str(uuid.uuid4())
        lease = Lease(
            lease_id=lease_id,
            holder=holder,
            paths=list(paths),
            mode=mode,
            ttl=ttl,
            created_at=time.time(),
            expires_at=now + ttl,
            heartbeat_at=now,
        )
        self._leases[lease_id] = lease
        self._holder_leases[holder].add(lease_id)
        for p in paths:
            self._path_leases[p].add(lease_id)

        if holder in self._waiting_holders:
            self._clear_wait_edges(holder)

        self._audit.record("lease_granted", lease_id=lease_id, holder=holder,
                         paths=paths, ttl=ttl, mode=mode.value)

        return AcquireResult(granted=True, lease_id=lease_id)

    def _clear_wait_edges(self, holder: str):
        """Remove all wait edges for a holder"""
        self._waiting_holders.discard(holder)
        targets = list(self._wait_graph._edges.get(holder, set()))
        for t in targets:
            self._wait_graph.remove_edge(holder, t)
            self._audit.record("wait_end", waiter=holder, holder=t)

    def release(self, lease_id: str) -> bool:
        """释放租约"""
        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                return False
            lease.state = LeaseState.REVOKED
            self._holder_leases[lease.holder].discard(lease_id)
            for p in lease.paths:
                self._path_leases[p].discard(lease_id)
                if not self._path_leases[p]:
                    del self._path_leases[p]

            self._wait_graph.remove_node(lease.holder)
            for waiter in list(self._waiting_holders):
                if lease.holder in self._wait_graph._edges.get(waiter, set()):
                    self._wait_graph.remove_edge(waiter, lease.holder)
                    self._audit.record("wait_end", waiter=waiter, holder=lease.holder)

            self._audit.record("lease_released", lease_id=lease_id, holder=lease.holder)
            return True

    def heartbeat(self, lease_id: str) -> bool:
        """心跳续期"""
        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease or lease.state != LeaseState.ACTIVE:
                return False
            now = time.monotonic()
            lease.expires_at = now + lease.ttl
            lease.heartbeat_at = now
            self._audit.record("heartbeat", lease_id=lease_id, holder=lease.holder,
                             new_expires=lease.expires_at)
            return True

    def validate_write(self, path: str, holder: str, expected_hash: str,
                       content_hash: str, lease_id: Optional[str] = None) -> Tuple[bool, Optional[LostUpdateEvent]]:
        """CAS 写验证"""
        with self._lock:
            current_hash = self._file_hashes.get(path)

            if current_hash is not None and current_hash != expected_hash:
                event = LostUpdateEvent(
                    path=path, holder=holder,
                    expected_hash=expected_hash, actual_hash=current_hash,
                    timestamp=time.time(), lease_id=lease_id,
                )
                self._lost_updates.append(event)
                self._audit.record("lost_update", path=path, holder=holder,
                                 expected_hash=expected_hash, actual_hash=current_hash,
                                 lease_id=lease_id)
                return False, event

            self._file_hashes[path] = content_hash
            self._audit.record("write_accepted", path=path, holder=holder,
                             content_hash=content_hash, lease_id=lease_id)
            return True, None

    def register_file_hash(self, path: str, content_hash: str):
        with self._lock:
            self._file_hashes[path] = content_hash

    def get_file_hash(self, path: str) -> Optional[str]:
        with self._lock:
            return self._file_hashes.get(path)

    def reclaim_expired(self) -> List[str]:
        with self._lock:
            return self._reclaim_expired_locked(time.monotonic())

    def _reclaim_expired_locked(self, now: float) -> List[str]:
        reclaimed = []
        for lid, lease in list(self._leases.items()):
            if lease.state == LeaseState.ACTIVE and lease.is_expired(now):
                lease.state = LeaseState.EXPIRED
                self._holder_leases[lease.holder].discard(lid)
                for p in lease.paths:
                    self._path_leases[p].discard(lid)
                    if not self._path_leases[p]:
                        del self._path_leases[p]
                self._wait_graph.remove_node(lease.holder)
                reclaimed.append(lid)
                self._audit.record("lease_expired_reclaimed", lease_id=lid,
                                 holder=lease.holder, paths=lease.paths)
        return reclaimed

    def _resolve_deadlock(self, cycles: List[List[str]], now: float) -> DeadlockCycle:
        """Resolve deadlock by revoking the lease with earliest TTL expiry"""
        cycle_nodes = cycles[0]
        edges = []
        for node in cycle_nodes:
            for t in self._wait_graph._edges.get(node, set()):
                if t in cycle_nodes:
                    edges.append((node, t))

        earliest_expiry = float("inf")
        victim = None
        for node in cycle_nodes:
            for lid in self._holder_leases.get(node, set()):
                lease = self._leases.get(lid)
                if lease and lease.expires_at < earliest_expiry:
                    earliest_expiry = lease.expires_at
                    victim = node

        if victim is None:
            victim = cycle_nodes[0]

        for lid in list(self._holder_leases.get(victim, set())):
            lease = self._leases.get(lid)
            if lease and lease.state == LeaseState.ACTIVE:
                lease.state = LeaseState.REVOKED
                for p in lease.paths:
                    self._path_leases[p].discard(lid)
                    if not self._path_leases[p]:
                        del self._path_leases[p]

        self._wait_graph.remove_node(victim)
        self._waiting_holders.discard(victim)

        cycle = DeadlockCycle(
            participants=cycle_nodes,
            edges=edges,
            resolved_by=victim,
            resolution_reason="earliest_ttl_expiry",
        )
        self._deadlock_cycles.append(cycle)
        self._audit.record("deadlock_resolved", participants=cycle_nodes,
                         victim=victim, edges=edges)
        return cycle

    # --- Query methods ---

    def get_active_leases(self) -> List[Lease]:
        with self._lock:
            return [l for l in self._leases.values() if l.state == LeaseState.ACTIVE]

    def get_leases_by_holder(self, holder: str) -> List[Lease]:
        with self._lock:
            lids = self._holder_leases.get(holder, set())
            return [self._leases[lid] for lid in lids if lid in self._leases]

    def get_leases_by_path(self, path: str) -> List[Lease]:
        with self._lock:
            lids = self._path_leases.get(path, set())
            return [self._leases[lid] for lid in lids if lid in self._leases]

    def get_wait_graph_snapshot(self) -> Dict[str, List[str]]:
        with self._lock:
            return self._wait_graph.snapshot()

    def get_lost_updates(self) -> List[LostUpdateEvent]:
        with self._lock:
            return list(self._lost_updates)

    def get_deadlock_cycles(self) -> List[DeadlockCycle]:
        with self._lock:
            return list(self._deadlock_cycles)

    def get_audit_entries(self) -> List[dict]:
        return self._audit.entries()

    def stats(self) -> dict:
        with self._lock:
            active = sum(1 for l in self._leases.values() if l.state == LeaseState.ACTIVE)
            expired = sum(1 for l in self._leases.values() if l.state == LeaseState.EXPIRED)
            revoked = sum(1 for l in self._leases.values() if l.state == LeaseState.REVOKED)
            return {
                "total_leases": len(self._leases),
                "active": active,
                "expired": expired,
                "revoked": revoked,
                "lost_updates": len(self._lost_updates),
                "deadlock_cycles": len(self._deadlock_cycles),
                "shadow_mode": self._shadow_mode,
                "wait_graph_nodes": len(self._wait_graph._edges),
            }
