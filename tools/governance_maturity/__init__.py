"""Governance-suite maturation (platform, domain-agnostic).

Four machine-adjudicated instruments that turn manual confirmations into
mechanical verdicts:

* :mod:`artifact_registry` -- cross-task artifact schemas + consumer contracts,
  so "can upstream output feed downstream" is a validation call, not a review.
* :mod:`mutation_scan` -- AST-level, domain-agnostic mutation operators and a
  sharded kill-rate ledger, so "the suite passes" is quantified by how much
  semantic damage the suite can detect.
* :mod:`gate_power` -- corruption injection into criteria artifacts, so
  "the gate catches bad criteria" is measured as detected / injected and
  always-true criteria are listed explicitly.
* :mod:`writeback_gate` -- KPI provenance guard, derived self-consistency,
  freshness SLO and idempotent writes, so "a KPI cannot silently roll back"
  is enforced at write time.

No domain lexicon, criterion or case is hard-coded here; domain data is
injected through JSON artifacts.  See ``docs/P315W1-N10_governance_suite_maturity.md``.
"""

__all__ = [
    "artifact_registry",
    "adapters",
    "mutation_scan",
    "gate_power",
    "writeback_gate",
    "domain_scan",
]