"""Value objects for the machine-adjudicated capability ledger audit.

This module is a platform mechanism, so it carries **zero domain semantics**:
capability identities, labels, metric values, thresholds and expected labels all
arrive through an injected spec document. Nothing here knows what a capability
"means"; it only knows metric names, comparators and numbers.

Two determinism rules make the audit reproducible bit-for-bit:

1. every number is normalised through :func:`fmt_number` before it is written to
   the ledger, so a value round-trips through SQLite without changing text;
2. nothing that is not derived from the spec (wall-clock time, host name, row
   insertion order) is ever part of the ledger or of the audit report.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"

# Evidence-level vocabulary (ordered from strongest to weakest claim).
LEVELS: Tuple[str, ...] = ("REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED")
LEVEL_RANK: Dict[str, int] = {lvl: len(LEVELS) - i for i, lvl in enumerate(LEVELS)}

# The three evidence items that make up an evidence chain, in canonical order.
GATES: Tuple[str, ...] = (
    "test_pass_rate",
    "benchmark_error_pct",
    "independent_review",
)

REVIEW_VERDICTS = ("pass", "fail", "missing")
TRANSITIONS = ("INIT", "UPGRADE", "HOLD", "DOWNGRADE")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON text (sorted keys, no whitespace, UTF-8 kept)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def fmt_number(value: Any) -> str:
    """Canonical textual form of a metric value used both in SQL and report.

    ``None`` becomes ``null``; floats are rendered with ``repr`` so the text is
    the shortest string that round-trips to the same IEEE-754 double; booleans
    become ``true``/``false``; everything else is ``str``-formatted.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
            raise ValueError("non-finite metric value is not writable")
        return repr(float(value))
    if isinstance(value, int):
        return str(value)
    return str(value)


class SpecError(ValueError):
    """Raised when the injected spec is internally inconsistent."""


@dataclass(frozen=True)
class Thresholds:
    """Pre-declared acceptance thresholds for the three evidence items."""

    test_pass_rate_min: float = 0.80
    benchmark_error_max_pct: float = 15.0
    require_independent_review: bool = True

    def validate(self) -> "Thresholds":
        if not 0.0 < self.test_pass_rate_min <= 1.0:
            raise SpecError(
                f"test_pass_rate_min must be in (0, 1], got {self.test_pass_rate_min!r}"
            )
        if not self.benchmark_error_max_pct > 0.0:
            raise SpecError(
                "benchmark_error_max_pct must be > 0, got "
                f"{self.benchmark_error_max_pct!r}"
            )
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_pass_rate_min": self.test_pass_rate_min,
            "benchmark_error_max_pct": self.benchmark_error_max_pct,
            "require_independent_review": self.require_independent_review,
        }

    def sha256(self) -> str:
        return sha256_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Thresholds":
        data = data or {}
        unknown = set(data) - set(cls().to_dict())
        if unknown:
            raise SpecError(f"unknown threshold keys: {sorted(unknown)}")
        return cls(
            test_pass_rate_min=float(
                data.get("test_pass_rate_min", cls.test_pass_rate_min)
            ),
            benchmark_error_max_pct=float(
                data.get("benchmark_error_max_pct", cls.benchmark_error_max_pct)
            ),
            require_independent_review=bool(
                data.get("require_independent_review", cls.require_independent_review)
            ),
        ).validate()


def _check_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise SpecError(f"{name} must be a non-empty string, got {value!r}")
    # '|' separates fields in the canonical ledger dump; keep the dump lossless.
    if any(ch in value for ch in ("|", "\n", "\r")):
        raise SpecError(f"{name} must not contain '|' or newlines: {value!r}")


@dataclass(frozen=True)
class EvidenceRecord:
    """One capability plus the raw numbers collected for it.

    The record holds *measurements only*; the level is derived by
    :mod:`classifier`, never asserted here.
    """

    capability_id: str
    impl_present: bool = True
    test_pass_rate: Optional[float] = None
    benchmark_error_pct: Optional[float] = None
    review_verdict: str = "missing"
    mock_markers: int = 0
    evidence_refs: Tuple[str, ...] = ()
    expected_level: Optional[str] = None
    current_level: str = "WITHHELD"

    def validate(self) -> "EvidenceRecord":
        _check_identifier("capability_id", self.capability_id)
        if self.test_pass_rate is not None:
            rate = float(self.test_pass_rate)
            if not 0.0 <= rate <= 1.0:
                raise SpecError(
                    f"{self.capability_id}: test_pass_rate must be in [0, 1], got {rate!r}"
                )
        if self.benchmark_error_pct is not None:
            err = float(self.benchmark_error_pct)
            if err < 0.0:
                raise SpecError(
                    f"{self.capability_id}: benchmark_error_pct must be >= 0, got {err!r}"
                )
        if self.review_verdict not in REVIEW_VERDICTS:
            raise SpecError(
                f"{self.capability_id}: review_verdict must be one of "
                f"{REVIEW_VERDICTS}, got {self.review_verdict!r}"
            )
        if not isinstance(self.mock_markers, int) or self.mock_markers < 0:
            raise SpecError(
                f"{self.capability_id}: mock_markers must be a non-negative int"
            )
        if self.expected_level is not None and self.expected_level not in LEVELS:
            raise SpecError(
                f"{self.capability_id}: expected_level must be one of {LEVELS}"
            )
        if self.current_level not in LEVELS:
            raise SpecError(
                f"{self.capability_id}: current_level must be one of {LEVELS}"
            )
        for ref in self.evidence_refs:
            _check_identifier("evidence_ref", ref)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "impl_present": bool(self.impl_present),
            "test_pass_rate": self.test_pass_rate,
            "benchmark_error_pct": self.benchmark_error_pct,
            "review_verdict": self.review_verdict,
            "mock_markers": int(self.mock_markers),
            "evidence_refs": list(self.evidence_refs),
            "expected_level": self.expected_level,
            "current_level": self.current_level,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceRecord":
        known = set(cls(capability_id="x").to_dict())
        unknown = set(data) - known
        if unknown:
            raise SpecError(f"unknown evidence keys: {sorted(unknown)}")
        refs = data.get("evidence_refs") or ()
        return cls(
            capability_id=data.get("capability_id", ""),
            impl_present=bool(data.get("impl_present", True)),
            test_pass_rate=data.get("test_pass_rate"),
            benchmark_error_pct=data.get("benchmark_error_pct"),
            review_verdict=data.get("review_verdict", "missing"),
            mock_markers=int(data.get("mock_markers", 0)),
            evidence_refs=tuple(refs),
            expected_level=data.get("expected_level"),
            current_level=data.get("current_level", "WITHHELD"),
        ).validate()


@dataclass(frozen=True)
class AuditSpec:
    """The whole injected input of one audit run."""

    capabilities: Sequence[EvidenceRecord]
    thresholds: Thresholds = field(default_factory=Thresholds)
    label: str = ""

    def validate(self) -> "AuditSpec":
        self.thresholds.validate()
        if not self.capabilities:
            raise SpecError("spec must contain at least one capability")
        seen = set()
        for rec in self.capabilities:
            rec.validate()
            if rec.capability_id in seen:
                raise SpecError(f"duplicate capability_id: {rec.capability_id}")
            seen.add(rec.capability_id)
        return self

    def to_dict(self) -> Dict[str, Any]:
        # Capabilities are emitted in sorted-id order: the spec digest must not
        # depend on the order a human happened to type them in.
        return {
            "schema_version": SCHEMA_VERSION,
            "label": self.label,
            "thresholds": self.thresholds.to_dict(),
            "capabilities": [
                rec.to_dict() for rec in sorted(self.capabilities, key=lambda r: r.capability_id)
            ],
        }

    def sha256(self) -> str:
        return sha256_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditSpec":
        return cls(
            capabilities=[EvidenceRecord.from_dict(d) for d in data.get("capabilities", [])],
            thresholds=Thresholds.from_dict(data.get("thresholds")),
            label=str(data.get("label", "")),
        ).validate()

    @classmethod
    def load(cls, path: str | Path) -> "AuditSpec":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)