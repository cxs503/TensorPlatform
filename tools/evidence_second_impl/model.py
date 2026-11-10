"""Frozen data model for the second-implementation layer (domain-neutral).

No domain vocabulary appears here: capability identifiers, probe points and
tolerances all arrive through artifacts.  Everything below is pure structure.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


GRADES = ("REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED")


class ReferenceSource(str, Enum):
    """Where the *reference* value of a claim comes from.

    The first three are independent of the code under test; the fourth is not.
    """

    ANALYTIC_CLOSED_FORM = "analytic_closed_form"      # closed-form solution, re-evaluable offline
    INDEPENDENT_ALGORITHM = "independent_algorithm"    # a second, differently written solver
    EXTERNAL_DATA = "external_data"                    # measurement / curated external dataset
    SAME_CODE_SELF_REFERENCE = "same_code_self_reference"  # residual of the subject against itself
    NOT_ASSERTED = "not_asserted"                      # no reference value asserted at all

    @property
    def is_independent(self) -> bool:
        return self in (ReferenceSource.ANALYTIC_CLOSED_FORM,
                        ReferenceSource.INDEPENDENT_ALGORITHM,
                        ReferenceSource.EXTERNAL_DATA)


class IndependenceVerdict(str, Enum):
    INDEPENDENT = "INDEPENDENT"
    SELF_REFERENTIAL = "SELF_REFERENTIAL"
    UNVERIFIED = "UNVERIFIED"          # locator cannot be re-read / hash mismatch
    UNDECLARED = "UNDECLARED"          # no declaration at all


class RegradeVerdict(str, Enum):
    KEEP = "KEEP"                      # grade unchanged
    DOWNGRADE = "DOWNGRADE"            # REAL -> WITHHELD
    RETAIN_FLAGGED = "RETAIN_FLAGGED"  # self-referential but independently corroborated
    NOT_APPLICABLE = "NOT_APPLICABLE"  # claim was not REAL to begin with


class InjectionType(str, Enum):
    """The three forgery classes the gate must catch."""

    SCALE_1P05 = "scale_1p05"
    OFFSET = "offset"
    SIGN_FLIP = "sign_flip"


class RelationKind(str, Enum):
    """Relations that must hold for any correct implementation."""

    EQUIVALENCE = "equivalence"        # two implementations of one spec agree
    SCALING = "scaling"               # f(lam*x) == lam**k * f(x)      (homogeneity)
    ZERO_POINT = "zero_point"         # f(0) == 0                       (degeneracy)
    ADDITIVITY = "additivity"         # f(x1+x2) == f(x1) + f(x2)       (superposition)
    MIRROR_PARITY = "mirror_parity"   # f(m(x)) == s * f(x), s = +-1    (symmetry)
    CONSERVATION = "conservation"     # sum_i f(i) == declared total    (partition)


class RelationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# evidence locator
# ---------------------------------------------------------------------------

def line_sha256(line: str) -> str:
    """Hash of a stripped source line (16 hex chars, matches ledger convention)."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EvidenceLocator:
    """A citation that must re-read offline: path + 1-based line + line hash."""

    path: str
    line: int
    line_sha256: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line,
                "line_sha256": self.line_sha256, "note": self.note}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceLocator":
        return cls(path=str(raw["path"]), line=int(raw["line"]),
                   line_sha256=str(raw.get("line_sha256", "")),
                   note=str(raw.get("note", "")))


@dataclass(frozen=True)
class DeclarationRecord:
    """One capability's reference-provenance declaration (artifact data)."""

    capability_id: str
    graded_grade: str                       # grade produced by tools/evidence_grade.py
    reference_source: ReferenceSource
    locators: tuple[EvidenceLocator, ...] = ()
    independent_corroboration: EvidenceLocator | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "graded_grade": self.graded_grade,
            "reference_source": self.reference_source.value,
            "locators": [loc.to_dict() for loc in self.locators],
            "independent_corroboration": (self.independent_corroboration.to_dict()
                                          if self.independent_corroboration else None),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DeclarationRecord":
        src = raw.get("reference_source")
        corr = raw.get("independent_corroboration")
        return cls(
            capability_id=str(raw["capability_id"]),
            graded_grade=str(raw.get("graded_grade", "WITHHELD")),
            reference_source=ReferenceSource(str(src)) if src else ReferenceSource.SAME_CODE_SELF_REFERENCE,
            locators=tuple(EvidenceLocator.from_dict(x) for x in raw.get("locators", [])),
            independent_corroboration=EvidenceLocator.from_dict(corr) if corr else None,
            reason=str(raw.get("reason", "")),
        )


# ---------------------------------------------------------------------------
# handles / relations
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelationSpec:
    """One executable relation instance bound to a handle."""

    kind: RelationKind
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RelationSpec":
        return cls(kind=RelationKind(str(raw["kind"])), params=dict(raw.get("params") or {}))


@dataclass(frozen=True)
class HandleSpec:
    """A capability handle the bench can execute (spec only, no closures)."""

    handle_id: str
    family: str
    handle_class: str          # "target" (injected) | "control" (untouched)
    params: Mapping[str, Any] = field(default_factory=dict)
    relations: tuple[RelationSpec, ...] = ()
    probes: Mapping[str, Any] = field(default_factory=dict)
    precision_digits: int = 9  # second implementation rounds to this many digits
    ledger_proxy_for: str = ""  # optional: capability_id this handle stands in for

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle_id": self.handle_id,
            "family": self.family,
            "handle_class": self.handle_class,
            "params": dict(self.params),
            "relations": [r.to_dict() for r in self.relations],
            "probes": dict(self.probes),
            "precision_digits": self.precision_digits,
            "ledger_proxy_for": self.ledger_proxy_for,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "HandleSpec":
        return cls(
            handle_id=str(raw["handle_id"]),
            family=str(raw["family"]),
            handle_class=str(raw.get("handle_class", "control")),
            params=dict(raw.get("params") or {}),
            relations=tuple(RelationSpec.from_dict(x) for x in raw.get("relations", [])),
            probes=dict(raw.get("probes") or {}),
            precision_digits=int(raw.get("precision_digits", 9)),
            ledger_proxy_for=str(raw.get("ledger_proxy_for", "")),
        )


@dataclass(frozen=True)
class RelationResult:
    handle_id: str
    kind: RelationKind
    status: RelationStatus
    observed: float
    expected: float
    residual: float
    tolerance: float
    metric: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle_id": self.handle_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "observed": self.observed,
            "expected": self.expected,
            "residual": self.residual,
            "tolerance": self.tolerance,
            "metric": self.metric,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HandleVerdict:
    """Aggregate verdict for one handle: flagged if any relation failed."""

    handle_id: str
    handle_class: str
    results: tuple[RelationResult, ...]

    @property
    def applicable(self) -> tuple[RelationResult, ...]:
        return tuple(r for r in self.results if r.status is not RelationStatus.NOT_APPLICABLE)

    @property
    def failed(self) -> tuple[RelationResult, ...]:
        return tuple(r for r in self.results if r.status is RelationStatus.FAIL)

    @property
    def flagged(self) -> bool:
        return bool(self.failed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle_id": self.handle_id,
            "handle_class": self.handle_class,
            "flagged": self.flagged,
            "n_applicable": len(self.applicable),
            "failed_kinds": [r.kind.value for r in self.failed],
            "results": [r.to_dict() for r in self.results],
        }


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0