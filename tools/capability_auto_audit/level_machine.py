"""Capability level state machine: automatic promotion / demotion (p262w1-N07).

The state machine is *evidence driven*: it consumes a
:class:`~tools.capability_auto_audit.evidence_chain.ChainVerdict` and returns a
transition.  Nothing here knows what a capability does -- only how strong the
evidence is.

Transition table (complete chain -> REAL, broken chain -> demote a REAL):

=================  =====================  ===================================
from               chain complete         chain broken
=================  =====================  ===================================
REAL               HOLD                   DOWNGRADE -> MOCK      (regression)
                                          DOWNGRADE -> WITHHELD  (evidence gap)
MOCK               UPGRADE -> REAL        HOLD
STUB               UPGRADE -> REAL        HOLD
WITHHELD           UPGRADE -> REAL        HOLD
NOT_SUPPORTED      UPGRADE -> REAL        HOLD
=================  =====================  ===================================

Demotion never fabricates a level: a chain that broke because tests/numbers
contradict the claim lands in ``MOCK``, while a chain that broke because an
artifact / report / review went missing lands in ``WITHHELD`` (the implementation
may still be real, we just cannot prove it any more).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .evidence_chain import FAILURE_EVIDENCE, FAILURE_REGRESSION, LEVELS, ChainVerdict

UPGRADE = "UPGRADE"
DOWNGRADE = "DOWNGRADE"
HOLD = "HOLD"

#: level ordering used for reporting (higher = more evidence)
LEVEL_RANK: Dict[str, int] = {
    "NOT_SUPPORTED": 0,
    "STUB": 1,
    "MOCK": 2,
    "WITHHELD": 3,
    "REAL": 4,
}

#: public, machine-readable transition table (also drives the report figure)
TRANSITION_TABLE: Tuple[Tuple[str, str, str, str], ...] = (
    ("REAL", "complete", HOLD, "CHAIN_COMPLETE_ALREADY_REAL"),
    ("REAL", "broken/regression", DOWNGRADE, "MOCK"),
    ("REAL", "broken/evidence", DOWNGRADE, "WITHHELD"),
    ("MOCK", "complete", UPGRADE, "REAL"),
    ("STUB", "complete", UPGRADE, "REAL"),
    ("WITHHELD", "complete", UPGRADE, "REAL"),
    ("NOT_SUPPORTED", "complete", UPGRADE, "REAL"),
)


@dataclass
class Transition:
    capability_id: str
    from_level: str
    to_level: str
    action: str
    reason_code: str
    rationale: str
    failure_kind: Optional[str] = None
    missing: List[str] = field(default_factory=list)
    chain_digest: str = ""

    @property
    def changed(self) -> bool:
        return self.from_level != self.to_level

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "action": self.action,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
            "failure_kind": self.failure_kind,
            "missing": list(self.missing),
            "chain_digest": self.chain_digest,
            "changed": self.changed,
        }


class InvalidLevel(ValueError):
    """Raised when a level outside :data:`LEVELS` is presented to the machine."""


def decide(current_level: str, verdict: ChainVerdict) -> Transition:
    """Apply the transition table to ``(current_level, verdict)``."""
    if current_level not in LEVELS:
        raise InvalidLevel(f"level {current_level!r} is not one of {LEVELS}")
    if not isinstance(verdict, ChainVerdict):  # defensive: no duck typing
        raise TypeError("verdict must be a ChainVerdict")

    cap = verdict.capability_id
    common = dict(capability_id=cap, from_level=current_level,
                  missing=list(verdict.missing), chain_digest=verdict.chain_digest,
                  failure_kind=verdict.failure_kind)

    if verdict.complete:
        if current_level == "REAL":
            return Transition(to_level="REAL", action=HOLD,
                              reason_code="CHAIN_COMPLETE_ALREADY_REAL",
                              rationale="chain complete and level already REAL",
                              **common)
        return Transition(to_level="REAL", action=UPGRADE, reason_code="CHAIN_COMPLETE",
                          rationale=(f"all {len(verdict.checks)} chain links pass "
                                     f"(spec {verdict.spec_digest[:12]})"),
                          **common)

    # broken chain
    if current_level == "REAL":
        if verdict.failure_kind == FAILURE_REGRESSION:
            return Transition(to_level="MOCK", action=DOWNGRADE,
                              reason_code=f"{verdict.reason_code}_ON_REGRESSION",
                              rationale=("regression evidence contradicts the REAL claim "
                                         f"(missing links: {', '.join(verdict.missing)})"),
                              **common)
        return Transition(to_level="WITHHELD", action=DOWNGRADE,
                          reason_code=f"{verdict.reason_code}_ON_EVIDENCE_LOSS",
                          rationale=("evidence chain broken, cannot prove REAL any more "
                                     f"(missing links: {', '.join(verdict.missing)})"),
                          **common)

    return Transition(to_level=current_level, action=HOLD,
                      reason_code=f"CHAIN_INCOMPLETE_{current_level}_HELD",
                      rationale=("chain incomplete; only REAL is demoted automatically "
                                 f"(missing links: {', '.join(verdict.missing)})"),
                      **common)


def transition_table() -> List[Dict[str, str]]:
    """Serialisable form of :data:`TRANSITION_TABLE` for reports/audits."""
    return [{"from": f, "condition": c, "action": a, "target": t}
            for f, c, a, t in TRANSITION_TABLE]


def level_distribution(levels: Sequence[str]) -> Dict[str, int]:
    dist = {lvl: 0 for lvl in LEVELS}
    for lvl in levels:
        dist[lvl] = dist.get(lvl, 0) + 1
    return dist