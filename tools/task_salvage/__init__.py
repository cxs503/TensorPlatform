"""Task-level salvage: circuit-breaker terminal tasks → machine-readable hand-over.

Campaign node ``p199w1-N06-task_salvage``.  Scope: a task that reaches a
*circuit-breaker* terminal state (``llm_error`` / ``timeout`` /
``budget_exhausted``) leaves partial work behind.  This package turns that work
into a machine-readable artefact (``salvage.json``) carrying

* the **inheritable** file list (only files that pass the three-gate sift),
* the **residual** item list (acceptance obligations still open),
* the **blocking reason** when nothing is inheritable,
* a content **fingerprint** (same task → same sha256, twice),

plus a seedable continuation subtask (``continuation.json``) so the next worker
starts from the salvaged state instead of from zero.

Zero domain semantics: everything domain-specific arrives as opaque payload
(mission records, file bytes, quoted acceptance strings).  No vocabulary,
threshold or fixture in this package names a scientific field; the guard test
runs the platform denylist scanner over the package.

Sibling package :mod:`tools.salvage` handles *dead-node worktree packaging*
(agent_lost, byte-exact copies, manifest hash chain).  This package handles
*terminal task hand-over* (state taxonomy, three-gate sift, backtest gates).
"""

from . import backtest, continuation, salvage, sift, taxonomy  # noqa: F401

__all__ = ["taxonomy", "sift", "salvage", "continuation", "backtest"]
__version__ = "1.0.0"