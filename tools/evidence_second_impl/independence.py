"""Reference-source independence: what makes ``REAL`` mean something.

The rule enforced here (and nothing else):

    A ``REAL`` grade survives only if the claim *declares* a reference source
    that is independent of the code under test **and** backs that declaration
    with an evidence locator that re-reads offline (path + 1-based line + hash of
    that line).  A self-referential claim is downgraded to ``WITHHELD`` unless a
    second, independent corroboration locator also verifies — in which case it is
    retained but *flagged*, and its self-reference share stays visible.

Reasons are machine-readable codes so the ledger can be queried by them.  The
declarations themselves are data (an artifact); this module contains no domain
vocabulary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .model import (
    DeclarationRecord,
    EvidenceLocator,
    IndependenceVerdict,
    ReferenceSource,
    RegradeVerdict,
    line_sha256,
)

DEFAULT_DECLARATIONS_REL = "artifacts/evidence_second_impl/capability_declarations.json"

REASON_KEEP = "INDEPENDENT_SOURCE_VERIFIED"
REASON_DOWNGRADE_SELF = "SELF_REFERENCE_NO_INDEPENDENT_CORROBORATION"
REASON_DOWNGRADE_NO_LOCATOR = "NO_VERIFIED_EVIDENCE_LOCATOR"
REASON_DOWNGRADE_UNDECLARED = "REFERENCE_SOURCE_UNDECLARED"
REASON_RETAINED = "SELF_REFERENCE_WITH_INDEPENDENT_CORROBORATION"
REASON_NOT_REAL = "CLAIM_NOT_REAL"


# ---------------------------------------------------------------------------
# locator verification (offline, hash-checked)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LocatorCheck:
    locator: EvidenceLocator
    verified: bool
    actual_sha256: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.locator.path,
            "line": self.locator.line,
            "declared_sha256": self.locator.line_sha256,
            "actual_sha256": self.actual_sha256,
            "verified": self.verified,
            "note": self.locator.note,
            "detail": self.detail,
        }


def verify_locator(locator: EvidenceLocator, root: Path | str) -> LocatorCheck:
    """Re-read one cited line and check its hash.  No network, no imports."""
    root = Path(root)
    path = Path(locator.path)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        return LocatorCheck(locator, False, "", f"missing file {locator.path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if locator.line < 1 or locator.line > len(lines):
        return LocatorCheck(locator, False, "",
                            f"line {locator.line} outside file ({len(lines)} lines)")
    actual = line_sha256(lines[locator.line - 1])
    if not locator.line_sha256:
        return LocatorCheck(locator, False, actual, "no declared line_sha256")
    if actual != locator.line_sha256:
        return LocatorCheck(locator, False, actual,
                            f"line hash mismatch (declared {locator.line_sha256}, actual {actual})")
    return LocatorCheck(locator, True, actual, "verified")


# ---------------------------------------------------------------------------
# declarations artifact
# ---------------------------------------------------------------------------

def load_declarations(path: Path | str) -> List[DeclarationRecord]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw["declarations"] if isinstance(raw, dict) else raw
    return [DeclarationRecord.from_dict(r) for r in rows]


def save_declarations(path: Path | str, records: Sequence[DeclarationRecord],
                      extra: Mapping[str, Any] | None = None) -> str:
    payload = {
        "schema_version": "capability-declarations/1.0",
        "note": ("Reference-provenance declarations. Every locator is re-read offline and "
                 "hash-checked; this file is data, the mechanism lives in "
                 "tools/evidence_second_impl/independence.py."),
        **(dict(extra) if extra else {}),
        "declarations": [r.to_dict() for r in records],
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(blob, encoding="utf-8")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# regrade
# ---------------------------------------------------------------------------

def regrade(record: DeclarationRecord, root: Path | str) -> Dict[str, Any]:
    """Apply the independence rule to one declared capability."""
    checks = [verify_locator(loc, root) for loc in record.locators]
    corr = (verify_locator(record.independent_corroboration, root)
            if record.independent_corroboration else None)
    source = record.reference_source

    if record.graded_grade != "REAL":
        verdict = RegradeVerdict.NOT_APPLICABLE
        final, reason = record.graded_grade, REASON_NOT_REAL
        indep = (IndependenceVerdict.INDEPENDENT if source.is_independent
                 else IndependenceVerdict.SELF_REFERENTIAL)
    else:
        any_verified = any(c.verified for c in checks)
        corr_ok = bool(corr and corr.verified)
        if not source.is_independent:
            indep = IndependenceVerdict.SELF_REFERENTIAL
            if corr_ok:
                verdict, final, reason = RegradeVerdict.RETAIN_FLAGGED, "REAL", REASON_RETAINED
            else:
                verdict, final, reason = RegradeVerdict.DOWNGRADE, "WITHHELD", REASON_DOWNGRADE_SELF
        elif not any_verified:
            indep = IndependenceVerdict.UNVERIFIED
            verdict, final, reason = RegradeVerdict.DOWNGRADE, "WITHHELD", REASON_DOWNGRADE_NO_LOCATOR
        else:
            indep = IndependenceVerdict.INDEPENDENT
            verdict, final, reason = RegradeVerdict.KEEP, "REAL", REASON_KEEP

    return {
        "capability_id": record.capability_id,
        "graded_grade": record.graded_grade,
        "regraded_grade": final,
        "reference_source": source.value,
        "independence": indep.value,
        "verdict": verdict.value,
        "reason_code": reason,
        "reason": record.reason,
        "self_reference": (not source.is_independent),
        "self_reference_share": 1.0 if (final == "REAL" and not source.is_independent) else 0.0,
        "locators": [c.to_dict() for c in checks],
        "corroboration": corr.to_dict() if corr else None,
        "verified_locators": sum(1 for c in checks if c.verified),
        "evidence_lines": [{"path": c.locator.path, "line": c.locator.line,
                            "line_sha256": c.actual_sha256 or c.locator.line_sha256}
                           for c in checks],
    }


def regrade_all(records: Iterable[DeclarationRecord], root: Path | str) -> List[Dict[str, Any]]:
    return [regrade(r, root) for r in records]


def summarise(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Aggregate the re-grade: counts, downgrade list, self-reference share."""
    real_before = [r for r in rows if r["graded_grade"] == "REAL"]
    real_after = [r for r in rows if r["regraded_grade"] == "REAL"]
    downgraded = [r for r in rows if r["verdict"] == RegradeVerdict.DOWNGRADE.value]
    retained = [r for r in rows if r["verdict"] == RegradeVerdict.RETAIN_FLAGGED.value]

    def share(population: Sequence[Mapping[str, Any]]) -> float:
        if not population:
            return 0.0
        return sum(float(r["self_reference_share"]) for r in population) / len(population)

    def share_by_declaration(population: Sequence[Mapping[str, Any]]) -> float:
        """Share of REAL claims whose *declared* reference source is self-reference."""
        if not population:
            return 0.0
        return sum(1 for r in population if r["self_reference"]) / len(population)

    declared_selfref = [r for r in real_before if r["self_reference"]]
    return {
        "n_capabilities": len(rows),
        "real_before": len(real_before),
        "real_after": len(real_after),
        "downgraded": [r["capability_id"] for r in downgraded],
        "downgrade_count": len(downgraded),
        "retained_flagged": [{"capability_id": r["capability_id"],
                              "reason_code": r["reason_code"],
                              "reason": r["reason"]} for r in retained],
        "declared_self_reference_real": [r["capability_id"] for r in declared_selfref],
        "self_reference_count": len(declared_selfref),
        "self_reference_share_before": (
            share_by_declaration(real_before) if real_before else 0.0),
        "self_reference_share_after": share(real_after),
        "self_reference_share_after_detail": {
            r["capability_id"]: r["self_reference_share"] for r in real_after},
        "by_reason": _count_by(rows, "reason_code"),
        "by_source": _count_by(rows, "reference_source"),
    }


def _count_by(rows: Sequence[Mapping[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        out[str(r[key])] = out.get(str(r[key]), 0) + 1
    return out