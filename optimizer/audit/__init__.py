"""Audit tooling that lives inside the optimizer package (m1610).

The acceptance entry point is ``tools/audit/optimizer_objective_audit.py`` (a thin
re-export shim); the implementation lives here because this mission's
EDIT_WHITELIST covers ``optimizer/`` only.
"""