"""Baseline tampering detection.

Detects three types of baseline tampering:
  1. **Content hash mismatch**: the baseline records don't match the recorded digest.
  2. **Structural tampering**: the baseline schema has unexpected fields or missing
     required fields (e.g. someone added/removed/renamed a field).
  3. **Chain-of-trust violation**: the provenance chain is broken (the chain_hash
     doesn't link correctly to the previous snapshot).

All checks are domain-neutral — they operate on the generic snapshot structure,
not on any domain-specific content.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .model import (
    BaselineSnapshot,
    TamperType,
    TamperVerdict,
)


# Required top-level fields in a baseline snapshot dict (schema contract).
REQUIRED_FIELDS = frozenset({"arch_key", "records", "digest", "schema_version"})

# Allowed top-level fields (structural tampering = unexpected fields).
ALLOWED_FIELDS = frozenset({
    "arch_key", "records", "digest", "chain_hash", "created_at", "schema_version",
})

# Required fields per record.
REQUIRED_RECORD_FIELDS = frozenset({"criterion_id", "expected_outcome"})

# Allowed expected_outcome values.
ALLOWED_OUTCOMES = frozenset({"PASS", "FAIL_PRE_EXISTING"})


@dataclass(frozen=True)
class TamperFinding:
    """One tampering finding."""
    tamper_type: TamperType
    detail: str
    expected: str = ""
    actual: str = ""


@dataclass(frozen=True)
class TamperReport:
    """Complete tamper detection report."""
    verdict: TamperVerdict
    findings: tuple[TamperFinding, ...]

    @property
    def is_clean(self) -> bool:
        return self.verdict == TamperVerdict.CLEAN

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "findings": [
                {
                    "tamper_type": f.tamper_type.value,
                    "detail": f.detail,
                    "expected": f.expected,
                    "actual": f.actual,
                }
                for f in self.findings
            ],
        }


def check_content_hash(snapshot: BaselineSnapshot) -> TamperFinding | None:
    """Type 1: verify the content hash matches the recorded digest."""
    computed = snapshot.compute_digest()
    if snapshot.digest and computed != snapshot.digest:
        return TamperFinding(
            tamper_type=TamperType.CONTENT_HASH,
            detail="baseline content hash mismatch — records were modified after digest was recorded",
            expected=snapshot.digest,
            actual=computed,
        )
    return None


def check_structural_integrity(raw: Mapping[str, Any]) -> list[TamperFinding]:
    """Type 2: verify the baseline schema has not been tampered with.

    Checks:
    - All required fields are present.
    - No unexpected fields have been added.
    - Each record has required fields and valid expected_outcome.
    """
    findings: list[TamperFinding] = []

    # Missing required fields
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        findings.append(TamperFinding(
            tamper_type=TamperType.STRUCTURAL,
            detail=f"required fields missing: {sorted(missing)}",
            expected=sorted(REQUIRED_FIELDS),
            actual=sorted(raw.keys()),
        ))

    # Unexpected fields
    extra = set(raw.keys()) - ALLOWED_FIELDS
    if extra:
        findings.append(TamperFinding(
            tamper_type=TamperType.STRUCTURAL,
            detail=f"unexpected fields added: {sorted(extra)}",
            expected=sorted(ALLOWED_FIELDS),
            actual=sorted(raw.keys()),
        ))

    # Record-level checks
    records = raw.get("records", [])
    if not isinstance(records, list):
        findings.append(TamperFinding(
            tamper_type=TamperType.STRUCTURAL,
            detail="records field is not a list",
            expected="list",
            actual=type(records).__name__,
        ))
    else:
        for i, rec in enumerate(records):
            if not isinstance(rec, Mapping):
                findings.append(TamperFinding(
                    tamper_type=TamperType.STRUCTURAL,
                    detail=f"record[{i}] is not a mapping",
                    expected="mapping",
                    actual=type(rec).__name__,
                ))
                continue
            rec_missing = REQUIRED_RECORD_FIELDS - set(rec.keys())
            if rec_missing:
                findings.append(TamperFinding(
                    tamper_type=TamperType.STRUCTURAL,
                    detail=f"record[{i}] missing fields: {sorted(rec_missing)}",
                ))
            outcome = rec.get("expected_outcome")
            if outcome is not None and outcome not in ALLOWED_OUTCOMES:
                findings.append(TamperFinding(
                    tamper_type=TamperType.STRUCTURAL,
                    detail=f"record[{i}] invalid expected_outcome: {outcome!r}",
                    expected=sorted(ALLOWED_OUTCOMES),
                    actual=str(outcome),
                ))

    return findings


def check_chain_of_trust(snapshot: BaselineSnapshot,
                         prev_digest: str = "") -> TamperFinding | None:
    """Type 3: verify the chain-of-trust link to the previous snapshot.

    If ``prev_digest`` is empty, this is a genesis snapshot and no chain check
    is performed (returns None).
    """
    if not prev_digest:
        return None
    if not snapshot.chain_hash:
        return TamperFinding(
            tamper_type=TamperType.CHAIN_OF_TRUST,
            detail="chain_hash is empty but previous snapshot digest is provided",
            expected="non-empty chain_hash",
            actual="",
        )
    expected_chain = snapshot.compute_chain_hash(prev_digest)
    if snapshot.chain_hash != expected_chain:
        return TamperFinding(
            tamper_type=TamperType.CHAIN_OF_TRUST,
            detail="chain-of-trust hash mismatch — provenance chain is broken",
            expected=expected_chain,
            actual=snapshot.chain_hash,
        )
    return None


def verify_baseline_integrity(snapshot: BaselineSnapshot,
                              raw: Mapping[str, Any] | None = None,
                              prev_digest: str = "") -> TamperReport:
    """Run all three tamper checks and return a consolidated report.

    Parameters
    ----------
    snapshot : BaselineSnapshot
        The parsed baseline snapshot.
    raw : Mapping, optional
        The raw dict form of the baseline (for structural checks).
        If None, structural checks are skipped.
    prev_digest : str, optional
        The digest of the previous snapshot in the chain (for chain-of-trust).
        If empty, chain check is skipped (genesis snapshot).
    """
    findings: list[TamperFinding] = []

    # Type 1: content hash
    content_finding = check_content_hash(snapshot)
    if content_finding:
        findings.append(content_finding)

    # Type 2: structural integrity
    if raw is not None:
        findings.extend(check_structural_integrity(raw))

    # Type 3: chain of trust
    chain_finding = check_chain_of_trust(snapshot, prev_digest)
    if chain_finding:
        findings.append(chain_finding)

    verdict = TamperVerdict.CLEAN if not findings else TamperVerdict.TAMPERED
    return TamperReport(verdict=verdict, findings=tuple(findings))
