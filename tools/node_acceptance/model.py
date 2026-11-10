"""Data model for the node-acceptance evidence aggregator (domain-neutral).

Every type here is a frozen dataclass or an enum with a stable wire value, so a
verdict can be hashed, diffed and replayed.  No domain vocabulary appears in
this module: domain meaning enters only through artifacts and configuration
(see ``lexicon.json``).

Schema version: node-acceptance/1.0
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "node-acceptance/1.0"
ENGINE_VERSION = "node-acceptance-engine/1.0.0"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Comparator(str, Enum):
    """How an observed value is compared against a criterion threshold."""

    GE = ">="
    GT = ">"
    LE = "<="
    LT = "<"
    EQ = "=="
    APPROX = "~="          # "≈": no tolerance given -> cannot be judged
    RATIO_GE = "ratio>="   # "4/5" style ratio criteria
    ALL_TRUE = "all_true"  # "全绿" / "all pass"
    PRESENT = "present"    # named elements must be present
    NONE = "none"          # nothing parseable


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    INCOMPLETE = "INCOMPLETE"      # node level: some criteria undecidable
    MECHANICAL_CONFLICT = "MECHANICAL_CONFLICT"  # number violated but the record documents an accepted deviation
    NO_CRITERIA = "NO_CRITERIA"    # node level: nothing to judge against


HUMAN_PASSISH = ("pass", "passed", "ok", "green", "true", "verified", "met", "success", "succeeded")
HUMAN_FAILISH = ("fail", "failed", "red", "false", "error", "broken", "unmet", "missing")
HUMAN_SOFTPASS = ("pass_with_note", "pass_with_notes", "partial", "waived", "conditional")


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Criterion:
    """One machine-judgeable acceptance criterion extracted from text."""

    raw: str
    comparator: Comparator = Comparator.NONE
    threshold: float | None = None
    ratio: tuple[int, int] | None = None
    unit: str | None = None
    metric_name: str | None = None      # explicit identifier found in the text
    hint: str | None = None             # lexicon hint (error/duration/...)
    scope: str = ""                     # artifact pointer the criterion came from
    source: str = ""                    # artifact path (relative to node root)
    source_pointer: str = ""            # pointer inside that artifact
    parse_confidence: float = 0.0
    notes: tuple[str, ...] = ()

    @property
    def judgeable(self) -> bool:
        """A criterion the engine may actually decide (APPROX/NONE are not)."""
        if self.comparator in (Comparator.NONE, Comparator.APPROX):
            return False
        return (
            self.threshold is not None
            or self.ratio is not None
            or self.comparator in (Comparator.ALL_TRUE, Comparator.PRESENT)
        )

    def as_dict(self) -> dict:
        d = {
            "raw": self.raw,
            "comparator": self.comparator.value,
            "threshold": self.threshold,
            "ratio": list(self.ratio) if self.ratio else None,
            "unit": self.unit,
            "metric_name": self.metric_name,
            "hint": self.hint,
            "scope": self.scope,
            "source": self.source,
            "source_pointer": self.source_pointer,
            "parse_confidence": round(self.parse_confidence, 4),
            "judgeable": self.judgeable,
            "notes": list(self.notes),
        }
        return d


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArtifactRef:
    """A scanned artifact with its content hash (audit chain)."""

    path: str
    role: str
    sha256: str
    bytes: int

    def as_dict(self) -> dict:
        return {"path": self.path, "role": self.role, "sha256": self.sha256, "bytes": self.bytes}


@dataclass(frozen=True)
class MetricRecord:
    """A numeric or boolean leaf lifted out of an artifact."""

    key: str                    # dotted pointer, e.g. anchors[0].relative_error
    value: float | bool | None
    kind: str                   # "number" | "bool" | "status"
    source: str                 # artifact path
    pointer: str = ""           # == key (kept explicit for provenance reports)
    scope: str = ""             # parent container pointer, e.g. anchors[0]

    @property
    def leaf(self) -> str:
        return self.key.split(".")[-1].split("[")[0]

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "kind": self.kind,
            "source": self.source,
            "scope": self.scope,
        }


@dataclass
class NodeEvidence:
    """Everything the scanner extracted from one node artifact directory."""

    node_id: str
    root: str
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metrics: list[MetricRecord] = field(default_factory=list)
    derived: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    engine_elapsed_s: float = 0.0
    # parsed JSON documents kept in memory for criterion discovery; never
    # serialised (the report points at the artifact path + sha256 instead).
    documents: list[tuple[str, Any]] = field(default_factory=list)

    @property
    def metric_count(self) -> int:
        return len(self.metrics)

    def digest(self) -> str:
        import hashlib

        h = hashlib.sha256()
        for a in sorted(self.artifacts, key=lambda a: a.path):
            h.update(f"{a.path}:{a.sha256}:{a.role}".encode())
        return h.hexdigest()

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "root": self.root,
            "artifact_count": len(self.artifacts),
            "metric_count": len(self.metrics),
            "derived": self.derived,
            "warnings": list(self.warnings),
            "elapsed_s": round(self.elapsed_s, 3),
            "digest": self.digest(),
            "artifacts": [a.as_dict() for a in self.artifacts],
        }


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    """The evidence actually used to judge one criterion."""

    value: float | bool | None
    matched_keys: tuple[str, ...] = ()
    source: str = ""
    scope: str = ""
    match_score: float = 0.0
    scaling: str | None = None

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "matched_keys": list(self.matched_keys),
            "source": self.source,
            "scope": self.scope,
            "match_score": round(self.match_score, 3),
            "scaling": self.scaling,
        }


@dataclass(frozen=True)
class CriterionOutcome:
    criterion: Criterion
    verdict: Verdict
    observation: Observation = Observation(value=None)
    note: str = ""
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "criterion": self.criterion.as_dict(),
            "verdict": self.verdict.value,
            "observation": self.observation.as_dict(),
            "note": self.note,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class NodeVerdict:
    """Machine verdict for one node plus the audit trail behind it."""

    node_id: str
    verdict: Verdict = Verdict.NO_CRITERIA
    outcomes: list[CriterionOutcome] = field(default_factory=list)
    confidence: float = 0.0
    evidence: NodeEvidence | None = None
    elapsed_s: float = 0.0
    caveats: list[str] = field(default_factory=list)

    # -- counting -----------------------------------------------------
    def count(self, v: Verdict) -> int:
        return sum(1 for o in self.outcomes if o.verdict is v)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "PASS": self.count(Verdict.PASS),
            "FAIL": self.count(Verdict.FAIL),
            "MECHANICAL_CONFLICT": self.count(Verdict.MECHANICAL_CONFLICT),
            "UNKNOWN": self.count(Verdict.UNKNOWN),
            "total": len(self.outcomes),
        }

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 4),
            "counts": self.counts,
            "elapsed_s": round(self.elapsed_s, 3),
            "caveats": list(self.caveats),
            "outcomes": [o.as_dict() for o in self.outcomes],
            "evidence": self.evidence.as_dict() if self.evidence else None,
        }


# ---------------------------------------------------------------------------
# Behavioural verdict rules (single source of truth)
# ---------------------------------------------------------------------------

def node_verdict_from_outcomes(outcomes: Sequence[CriterionOutcome]) -> Verdict:
    """Combine per-criterion outcomes into one node verdict.

    FAIL is sticky (honest-FAIL is never overwritten by PASS votes); any
    undecidable criterion keeps the node out of a clean PASS.
    """
    if not outcomes:
        return Verdict.NO_CRITERIA
    if any(o.verdict is Verdict.FAIL for o in outcomes):
        return Verdict.FAIL
    if all(o.verdict is Verdict.PASS for o in outcomes):
        return Verdict.PASS
    if any(o.verdict is Verdict.MECHANICAL_CONFLICT for o in outcomes):
        # a declared number is violated while the record documents the deviation:
        # neither PASS (the number is violated) nor FAIL (the deviation is curated)
        # -> the node needs a curator decision
        return Verdict.INCOMPLETE
    if any(o.verdict is Verdict.PASS for o in outcomes):
        return Verdict.INCOMPLETE
    return Verdict.INCOMPLETE if any(o.verdict is Verdict.UNKNOWN for o in outcomes) else Verdict.NO_CRITERIA


def classify_human_token(token: Any) -> Verdict:
    """Map a human-authored status token onto a binary verdict."""
    if isinstance(token, bool):
        return Verdict.PASS if token else Verdict.FAIL
    if token is None:
        return Verdict.UNKNOWN
    text = json.dumps(token, ensure_ascii=False) if isinstance(token, (dict, list)) else str(token)
    t = text.strip().lower()
    if not t:
        return Verdict.UNKNOWN
    if any(s in t for s in HUMAN_SOFTPASS):
        return Verdict.PASS   # soft pass counts as human PASS, flagged by caller
    if any(s in t for s in HUMAN_FAILISH):
        return Verdict.FAIL
    if any(s in t for s in HUMAN_PASSISH):
        return Verdict.PASS
    return Verdict.UNKNOWN


def jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/enums into JSON-ready structures."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "as_dict"):
        return jsonable(obj.as_dict())
    return obj


def dumps(doc: Any) -> str:
    return json.dumps(jsonable(doc), ensure_ascii=False, indent=2, sort_keys=False)