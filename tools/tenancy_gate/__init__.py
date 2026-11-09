#!/usr/bin/env python3
"""多租户并发写冲突检测与合并预演门（平台共性机制，零领域语义）。

三段门 + 一本台账
----------------
1. **派单前**：路径预留（:mod:`.lease`）——同一片路径被两个写租约抢，拒派或降级只读。
2. **收单时**：冲突矩阵（:mod:`.intake`）——本次 ``changed_files`` × 同窗口其他任务，
   文件级/租约级/范围级三条 lane 出矩阵。
3. **推送前**：三方合并预演（:mod:`.rehearsal`）——裸仓 ``git merge-tree`` dry-run，
   冲突给文件级 + hunk 级清单，无法判定（仓不可读 / merge-tree 失败 / 超时 / 超预算）
   一律 ``unknown`` 且 ``allowed=false``（fail-closed）。
4. **台账**：:mod:`.ledger` append-only JSONL，幂等重放、查询、链表校验、结论可复算。

边界
----
* 只读 git（``rev-parse`` / ``merge-base`` / ``diff`` / ``merge-tree --write-tree``），
  不建删 ref、不写工作树与索引、不改 .git 配置。
* 无业务/学科词条：可写根、时间窗、处置策略全部来自 ``config``。
"""
from __future__ import annotations

from .api import build_query, build_recompute, build_verify, create_router
from .config import DEFAULTS, load_config
from .intake import conflict_matrix
from .lease import check_lease
from .ledger import (
    append_events,
    make_event,
    query_events,
    read_events,
    recompute_matrix,
    record_intake,
    record_lease_decision,
    record_rehearsal,
    verify_chain,
)
from .model import (
    INTAKE_SCHEMA,
    LEASE_SCHEMA,
    REHEARSAL_SCHEMA,
    FinishRecord,
    PathLease,
    TenancyError,
    canonical_json,
    digest,
)
from .rehearsal import changed_files, rehearse_pair, rehearse_pairs, repo_probe
from .semantics import load_denylist, scan_text, scan_tree

__all__ = [
    "DEFAULTS",
    "FinishRecord",
    "INTAKE_SCHEMA",
    "LEASE_SCHEMA",
    "PathLease",
    "REHEARSAL_SCHEMA",
    "TenancyError",
    "append_events",
    "build_query",
    "build_recompute",
    "build_verify",
    "create_router",
    "canonical_json",
    "changed_files",
    "check_lease",
    "conflict_matrix",
    "digest",
    "load_config",
    "load_denylist",
    "make_event",
    "query_events",
    "read_events",
    "recompute_matrix",
    "record_intake",
    "record_lease_decision",
    "record_rehearsal",
    "rehearse_pair",
    "rehearse_pairs",
    "repo_probe",
    "scan_text",
    "scan_tree",
    "verify_chain",
]

__version__ = "1.0.0"