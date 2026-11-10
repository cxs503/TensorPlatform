#!/usr/bin/env python3
"""并发写所有权治理（平台共性机制，零领域语义）。

四件套
------
1. **写租约协议**（:mod:`.registry`）：``acquire(paths, holder, ttl, mode)`` → ``lease_id``；
   ``rw`` 独占、``r`` 共享；同键活跃 ``rw`` 持有者 ≤ 1（不变量 I1）。
2. **等待图死锁检测**（:mod:`.waitgraph`）：等待边 ``waiter → blocker``，Tarjan SCC 检环，
   打破策略 = 环内 TTL 最早到期者被撤销。
3. **静默覆写归因**（:mod:`.cas`）：写前比对期望前置哈希，不符即拒写 + ``cas_reject`` 事件。
4. **租约回收**（:mod:`.registry`）：``now > heartbeat + ttl`` 即强制回收（进程被 SIGKILL 后
   心跳停摆是其典型触发条件）。

灰度：:class:`.gate.WriteLeaseGate` 先 ``shadow``（只观察不拦写）累计真实写，达阈值再
``promote()`` 到 ``enforcing``。

产物
----
* ``artifacts/write_lease/acceptance.json``：D1–D7 判据数字（:mod:`.acceptance`）
* ``docs/write_lease/WRITE_LEASE.md``：报告（数字由 :mod:`.gen_doc` 从产物注入）
"""
from __future__ import annotations

from .cas import CasWriter, content_sha, lost_update_events, read_state
from .gate import WriteLeaseGate, open_gate
from .ledger import Ledger, live_edges, reconstruct_wait_graph, wait_graph
from .model import (ACTIVE, ACQUIRE, CAS_COMMIT, CAS_REJECT, DEADLOCK, R, RECLAIM,
                    RELEASE, REVOKE, RW, WAIT_START, digest, norm_path, resource_key)
from .registry import Lease, LeaseRegistry, LeaseRevoked, LeaseTimeout, LostUpdate
from .waitgraph import choose_victim, detect, find_cycles, tarjan_sccs

__all__ = [
    "WriteLeaseGate", "open_gate", "LeaseRegistry", "Lease", "LeaseTimeout",
    "LeaseRevoked", "LostUpdate", "CasWriter", "content_sha", "read_state",
    "lost_update_events", "Ledger", "live_edges", "wait_graph",
    "reconstruct_wait_graph", "tarjan_sccs", "find_cycles", "detect", "choose_victim",
    "resource_key", "norm_path", "digest", "ACTIVE", "ACQUIRE", "CAS_COMMIT",
    "CAS_REJECT", "DEADLOCK", "R", "RECLAIM", "RELEASE", "REVOKE", "RW", "WAIT_START",
]

__version__ = "1.0.0"