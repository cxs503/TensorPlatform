"""Sustainment ops — platform self-sustainment operation matrix (p228w1-N10).

Four independently callable, zero-domain-semantics mechanisms that turn
"platform metadata is probably recoverable / worktrees are probably fine"
into machine-adjudicated facts:

* :mod:`tools.sustainment.lineage`        — artifact lineage DAG (sha256 + DAG,
  forward/backward tracing, tamper detection)
* :mod:`tools.sustainment.evidence_audit` — report evidence-chain integrity
  (numeric provenance / figure sha256 / acceptance-criteria coverage)
* :mod:`tools.sustainment.backup_drill`   — isolated-schema restore drill with
  triple cross-check, three negative controls, RPO/RTO ledger
* :mod:`tools.sustainment.worktree_gc`    — worktree lifecycle governance
  (six classes, tar+zstd archive, disk pressure gate, flock guard)

Every module is importable on its own, ships a stable dataclass/JSON contract
(``SCHEMA_VERSION``) and has a pytest regression suite under ``tests/``.
Nothing here knows any physics/domain vocabulary: tables, roots and criteria
are injected via CLI/JSON so the platform stays domain-free.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

__all__ = ["SCHEMA_VERSION"]