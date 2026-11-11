#!/usr/bin/env python3
"""Cross-solver physical-invariant audit (``invariant-audit/1.0``).

Layers
------
``dimensions``  exponent algebra over injected base symbols
``model``       declarative plan / target / invariant / result data model
``checks``      invariant evaluators (conservation, covariance, dimension, relation)
``engine``      plan execution -> per-pair ledger with PASS/FAIL/WITHHELD/ERROR
``bundle``      evidence bundle (sha256 chain) + tamper verification
``cli``         ``python3 -m tools.invariant_audit run|verify``

Zero-domain-semantics
---------------------
A solver under test joins the audit by *configuration* only: a dotted callable
path plus a JSON parameter block.  The mechanism never interprets what the
numbers mean, so the same code audits an arbitrary set of solvers/cases.  The
application layer that supplies synthetic models lives in
``tools.invariant_audit.anchors`` and is deliberately **not** imported here
(see ``tests/invariant_audit/test_invariant_audit_neutrality.py``, which proves
the dependency direction with an AST scan).

Relation to the existing single-criterion machinery
---------------------------------------------------
``tools/verification_harness`` answers "does this number match a reference
value?".  This package answers the complementary question "does this solver
obey the *structural* laws (conservation, symmetry, dimensional consistency)
that any correct implementation must obey, regardless of reference data?".  It
reuses the harness' :class:`~tools.verification_harness.model.Tolerance` mixed
error convention and its sha256/chain primitives rather than re-inventing them.
"""
from __future__ import annotations

from .checks import CheckOutcome, EvalContext, evaluate, KIND_HANDLERS
from .dimensions import Dimension, DimensionError
from .engine import (
    AuditPlan,
    PlanError,
    ledger_markdown,
    load_plan,
    load_plan_file,
    run_audit,
)
from .model import (
    AUDIT_SCHEMA_VERSION,
    TargetSpec,
    InvariantSpec,
    InvariantState,
    InvariantResult,
    TargetResult,
    AuditReport,
    aggregate_state,
    round_floats,
)
from .bundle import write_bundle, verify_bundle

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditPlan",
    "AuditReport",
    "CheckOutcome",
    "Dimension",
    "DimensionError",
    "EvalContext",
    "InvariantResult",
    "InvariantSpec",
    "InvariantState",
    "KIND_HANDLERS",
    "PlanError",
    "TargetResult",
    "TargetSpec",
    "aggregate_state",
    "evaluate",
    "ledger_markdown",
    "load_plan",
    "load_plan_file",
    "round_floats",
    "run_audit",
    "verify_bundle",
    "write_bundle",
]