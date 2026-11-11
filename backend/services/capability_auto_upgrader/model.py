"""Content-addressed evidence model of the capability auto-upgrader.

Everything the upgrader decides is a function of *evidence records*, and every
record carries its own content hash.  Two consequences follow, and both are
tested rather than promised:

* a decision can be replayed from the ledger alone (no hidden mutable state);
* replaying the *same* record cannot inflate a streak, because a duplicate
  record has the same digest and is dropped before it reaches the policy
  (see :func:`dedupe_runs`).

Level ladder
------------
``NOT_SUPPORTED < STUB < MOCK < WITHHELD < REAL`` — identical to the ladder used
by the sibling grading tools (``tools/capability_upgrade``), so a level written
by this module means the same thing to the rest of the platform.  ``WITHHELD``
sits *above* ``MOCK`` on purpose: ``MOCK`` says "we know this is not a real
algorithm", ``WITHHELD`` says "an implementation exists but the proof is not in
place".

There is no domain vocabulary in this file: capability identifiers, file names
and numeric thresholds all arrive as data.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

SCHEMA = "capability-auto-upgrader/1.0"

LEVELS: tuple = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_INDEX: Dict[str, int] = {name: i for i, name in enumerate(LEVELS)}

#: Levels whose evidence is missing/weak enough that new proof may raise them.
UPGRADABLE_LEVELS = frozenset({"STUB", "MOCK", "WITHHELD"})
#: The only level a promotion can land on.
TARGET_LEVEL = "REAL"


class EvidenceError(ValueError):
    """Malformed or untrustworthy evidence record (never silently dropped)."""


# ---------------------------------------------------------------- hashing ---
def canonical_json(obj: Any) -> str:
    """Deterministic JSON: the bytes hashed for every provenance digest."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_sha256(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise EvidenceError(f"{field_name} must be a 64-char sha256 hex string")
    try:
        int(value, 16)
    except ValueError as exc:  # pragma: no cover - defensive
        raise EvidenceError(f"{field_name} is not hexadecimal") from exc
    return value.lower()


def _require_count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError(f"{field_name} must be a non-negative integer, got {value!r}")
    return value


def normalise_level(level: str) -> str:
    if not isinstance(level, str):
        raise EvidenceError(f"level must be a string, got {level!r}")
    up = level.strip().upper()
    if up not in LEVEL_INDEX:
        raise EvidenceError(f"unknown evidence level {level!r}; expected one of {LEVELS}")
    return up


def level_below(level: str, steps: int = 1) -> str:
    idx = LEVEL_INDEX[normalise_level(level)]
    return LEVELS[max(0, idx - steps)]


def level_at_least(level: str, floor: str) -> bool:
    return LEVEL_INDEX[normalise_level(level)] >= LEVEL_INDEX[normalise_level(floor)]


# --------------------------------------------------------------- evidence ---
@dataclass(frozen=True)
class TestEvidence:
    """Outcome of one pytest run, summarised (the junit report is the anchor)."""

    #: Not a pytest test class, even though the name starts with "Test".
    __test__ = False

    passed: int
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    report_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("passed", "failed", "errors", "skipped"):
            _require_count(getattr(self, name), f"test.{name}")
        _require_sha256(self.report_sha256, "test.report_sha256")

    @property
    def executed(self) -> int:
        """Tests that produced a verdict; skips carry no verdict."""
        return self.passed + self.failed + self.errors

    @property
    def pass_rate(self) -> Optional[float]:
        if self.executed == 0:
            return None
        return self.passed / self.executed

    def as_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "pass_rate": self.pass_rate,
            "report_sha256": self.report_sha256,
        }

    @classmethod
    def from_junit_xml(cls, path: Path | str) -> "TestEvidence":
        """Parse a pytest ``--junit-xml`` report.

        Counts come from ``<testcase>`` children.  A testcase is *skipped* when it
        carries a ``<skipped>`` child, *failed* when it carries ``<failure>`` and
        *errored* when it carries ``<error>``; the rest are passed.  A report with
        no testcases is not an error here — it becomes ``executed == 0`` and the
        policy will refuse to call it evidence.
        """
        path = Path(path)
        if not path.is_file():
            raise EvidenceError(f"junit report not found: {path}")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise EvidenceError(f"junit report is not well formed: {exc}") from exc
        cases: List[ET.Element] = []
        if root.tag == "testcase":
            cases = [root]
        else:
            cases = list(root.iter("testcase"))
        passed = failed = errors = skipped = 0
        for case in cases:
            tags = {child.tag for child in case}
            if "skipped" in tags:
                skipped += 1
            elif "failure" in tags:
                failed += 1
            elif "error" in tags:
                errors += 1
            else:
                passed += 1
        return cls(
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            report_sha256=sha256_file(path),
        )


@dataclass(frozen=True)
class BenchmarkEvidence:
    """One numeric comparison against a reference value.

    ``error_pct`` is *derived* here from the two raw numbers; it is never read
    from the record.  That is deliberate: a producer cannot certify itself by
    writing ``error_pct: 0`` into the payload.
    """

    measured: float
    reference: float
    n_samples: int
    source_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("measured", "reference"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EvidenceError(f"benchmark.{name} must be numeric, got {value!r}")
            if value != value or value in (float("inf"), float("-inf")):
                raise EvidenceError(f"benchmark.{name} must be finite, got {value!r}")
            object.__setattr__(self, name, float(value))
        if self.reference == 0.0:
            raise EvidenceError("benchmark.reference must be non-zero")
        _require_count(self.n_samples, "benchmark.n_samples")
        if self.n_samples < 1:
            raise EvidenceError("benchmark.n_samples must be >= 1")
        _require_sha256(self.source_sha256, "benchmark.source_sha256")

    @property
    def error_pct(self) -> float:
        return abs(self.measured - self.reference) / abs(self.reference) * 100.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "measured": self.measured,
            "reference": self.reference,
            "n_samples": self.n_samples,
            "error_pct": self.error_pct,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "BenchmarkEvidence":
        if not isinstance(record, Mapping):
            raise EvidenceError("benchmark record must be a mapping")
        for key in ("measured", "reference", "n_samples"):
            if key not in record:
                raise EvidenceError(f"benchmark record misses {key!r}")
        return cls(
            measured=record["measured"],
            reference=record["reference"],
            n_samples=record["n_samples"],
            source_sha256=record.get("source_sha256"),
        )


@dataclass(frozen=True)
class EvidenceRun:
    """One measured run of one capability: tests + accuracy, content-addressed."""

    capability_id: str
    seq: int
    tests: TestEvidence
    benchmark: BenchmarkEvidence
    run_id: Optional[str] = None
    recorded_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not self.capability_id.strip():
            raise EvidenceError("capability_id must be a non-empty string")
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 1:
            raise EvidenceError("seq must be a positive integer")
        if not isinstance(self.tests, TestEvidence):
            raise EvidenceError("tests must be a TestEvidence")
        if not isinstance(self.benchmark, BenchmarkEvidence):
            raise EvidenceError("benchmark must be a BenchmarkEvidence")

    @property
    def run_id_effective(self) -> str:
        return self.run_id or f"{self.capability_id}#{self.seq}"

    def body(self) -> Dict[str, Any]:
        """Evidence content of the run — the bytes the digest covers.

        ``recorded_at`` is deliberately excluded: it is wall-clock metadata, and
        including it would make two identical replays look like two different
        runs, which is exactly the loophole a streak must not have.
        """
        return {
            "capability_id": self.capability_id,
            "seq": self.seq,
            "run_id": self.run_id_effective,
            "tests": self.tests.as_dict(),
            "benchmark": self.benchmark.as_dict(),
        }

    @property
    def digest(self) -> str:
        return sha256_obj(self.body())

    def as_dict(self) -> Dict[str, Any]:
        payload = self.body()
        payload["recorded_at"] = self.recorded_at
        payload["digest"] = self.digest
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceRun":
        tests = payload.get("tests") or {}
        run = cls(
            capability_id=payload["capability_id"],
            seq=payload["seq"],
            tests=TestEvidence(
                passed=tests.get("passed", 0),
                failed=tests.get("failed", 0),
                errors=tests.get("errors", 0),
                skipped=tests.get("skipped", 0),
                report_sha256=tests.get("report_sha256"),
            ),
            benchmark=BenchmarkEvidence.from_record(payload["benchmark"]),
            run_id=payload.get("run_id"),
            recorded_at=payload.get("recorded_at", utc_now()),
        )
        declared = payload.get("digest")
        if declared is not None and declared != run.digest:
            raise EvidenceError(
                f"run digest mismatch for {run.capability_id}#{run.seq}: "
                f"declared {declared} != recomputed {run.digest}"
            )
        return run


def dedupe_runs(runs: Iterable[EvidenceRun]) -> List[EvidenceRun]:
    """Drop duplicate digests, then order by ``seq`` (oldest first).

    Two runs with the same content hash are the *same* evidence; counting them
    twice would let a caller buy a streak by replaying one report.
    """
    seen: Dict[str, EvidenceRun] = {}
    for run in runs:
        seen.setdefault(run.digest, run)
    ordered = sorted(seen.values(), key=lambda r: (r.seq, r.digest))
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.seq == cur.seq:
            raise EvidenceError(
                f"two distinct runs share seq={cur.seq} for {cur.capability_id}; "
                "sequence numbers must be unique per capability"
            )
    return ordered


def sha256_of_levels(levels: Sequence[str]) -> str:
    return sha256_obj({"levels": list(levels), "schema": SCHEMA})