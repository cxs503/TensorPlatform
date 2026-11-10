"""Cross-domain coupling consistency gate (domain-agnostic mechanism layer).

The gate answers one question for a campaign maturity gate: *do the capability
interfaces produced by upstream nodes still agree with an independent
recomputation when they are composed across domains?*

Mechanism only — no physics, no domain nouns, no domain thresholds:
  * ``spec``   — structural validation of a case pack (contract fields, units)
  * ``gate``   — evaluator registry, per-coupling primary-vs-independent
                 comparison, interface-contract coverage, end-to-end regression
  * ``report`` — six-element paper-grade markdown rendering

Domain content is injected by a *case pack* directory (see ``domains/``).
"""
from .spec import (
    Coupling,
    EndToEnd,
    GateSpec,
    SpecError,
    REQUIRED_CONTRACT_FIELDS,
    EVIDENCE_GRADES,
    load_spec,
)
from .gate import EvaluatorRegistry, run_gate, relative_error

__all__ = [
    "Coupling",
    "EndToEnd",
    "GateSpec",
    "SpecError",
    "REQUIRED_CONTRACT_FIELDS",
    "EVIDENCE_GRADES",
    "load_spec",
    "EvaluatorRegistry",
    "run_gate",
    "relative_error",
]
