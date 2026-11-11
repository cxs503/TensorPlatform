"""Evidence-level regression gate: capability probe registry + REAL->MOCK degradation alarm.

The module turns a *silent* evidence downgrade (an implementation drifting to MOCK while
the ledger still claims REAL, or a level table being edited without evidence) into a red
gate verdict, and lets a level-change write path refuse the change.

Design boundaries
-----------------
* **Zero domain semantics** in code: this package knows nothing about physics. Every
  capability binding, input case, expected value and tolerance is *data* loaded from a
  registry file (``config/evidence_regression/probes.json`` by default). Tests use a
  synthetic laboratory module (``tests/evidence_regression/synthetic_lab.py``).
* **Probe kinds are generic contracts**: algebraic anchor, homogeneity (scale law),
  monotonicity, non-triviality (variation across cases), determinism and bounds. None of
  them encodes a domain criterion.
* **Fail-closed**: a claim of REAL must survive the executable contracts; a downgrade
  without fresh probe evidence is blocked until an acknowledgement carries the digest of
  the very probe run that justified it.

Public entry points
-------------------
``registry.load_registry`` / ``gate.audit_claims`` / ``gate.evaluate_changes`` /
``mutation.drill`` / ``ledger.apply_changes``
"""
from __future__ import annotations

from .model import Check, Entry, ProbeReport, ProbeSpec, TargetSpec  # noqa: F401
from .registry import ProbeRegistry, load_registry  # noqa: F401
from . import gate, ledger, mutation, probes, targets  # noqa: F401

__all__ = [
    "Check",
    "Entry",
    "ProbeReport",
    "ProbeSpec",
    "TargetSpec",
    "ProbeRegistry",
    "load_registry",
    "gate",
    "ledger",
    "mutation",
    "probes",
    "targets",
]