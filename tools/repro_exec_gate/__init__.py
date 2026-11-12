"""Delivery-time reproducibility execution gate (domain-neutral, standalone).

``tools/evidence_grade.py`` checks that a contract *declares* ``repro_cmd``;
this package is the complementary **execution** gate: it runs the declared
command inside a worktree, enforces a hard timeout that kills the whole process
group, and verifies the artifacts the contract promises.

Registry side-effect free: importing this package executes nothing. Domain
content arrives only through the caller's ``contract.json`` (platform rule:
zero domain vocabulary in common machinery).

Quick use::

    python3 -m tools.repro_exec_gate --contract c.json --worktree . --timeout 60

See ``docs/repro_exec_gate.md`` for the frozen CLI/reason contract.
"""

from __future__ import annotations

from . import cli, core, report  # noqa: F401

__all__ = ["cli", "core", "report"]
__version__ = core.SCHEMA_VERSION