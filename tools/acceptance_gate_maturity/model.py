"""Data model for acceptance gate maturity (domain-neutral).

All types are frozen dataclasses so they can be used as dict keys and hashed
for content-addressed storage.  No domain vocabulary appears anywhere.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

class GateVerdict(str, Enum):
    """Top-level gate verdict after baseline differencing."""
    PASS = "PASS"                              # no new failures
    FAIL_NEW = "FAIL_NEW"                      # new regressions detected
    PASS_WITH_KNOWN = "PASS_WITH_KNOWN"        # only pre-existing failures
    MISSING_ARTIFACT = "MISSING_ARTIFACT"       # fail-closed: required artifact absent
    BASELINE_TAMPERED = "BASELINE_TAMPERED"    # baseline integrity violated


class MissingArtifactVerdict(str, Enum):
    """Sub-verdict for missing-artifact detection."""
    PRESENT = "PRESENT"
    MISSING = "MISSING"


class TamperType(str, Enum):
    """Three types of baseline tampering this engine detects."""
    CONTENT_HASH = "content_hash"              # file content doesn't match recorded hash
    STRUCTURAL = "structural"                  # schema fields added/removed/renamed
    CHAIN_OF_TRUST = "chain_of_trust"          # provenance chain broken


class TamperVerdict(str, Enum):
    CLEAN = "CLEAN"
    TAMPERED = "TAMPERED"


class CriterionOutcome(str, Enum):
    """Per-criterion outcome relative to baseline."""
    PASS = "PASS"
    FAIL_NEW = "FAIL_NEW"              # regression (not in baseline failure set)
    FAIL_PRE_EXISTING = "FAIL_PRE_EXISTING"  # known failure (in baseline)
    MISSING = "MISSING"                # artifact/metric absent


# ---------------------------------------------------------------------------
# Architecture key
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArchKey:
    """Identifies a build/test environment for baseline separation.

    Uses only generic platform descriptors — no domain vocabulary.
    """
    arch: str           # e.g. "loong64", "x86_64", "aarch64"
    os: str = ""        # e.g. "linux", "debian12"
    runtime: str = ""   # e.g. "cpython3.12", "pypy3.10"

    def to_key(self) -> str:
        parts = [self.arch]
        if self.os:
            parts.append(self.os)
        if self.runtime:
            parts.append(self.runtime)
        return "|".join(parts)

    @classmethod
    def from_key(cls, key: str) -> "ArchKey":
        parts = key.split("|")
        return cls(arch=parts[0],
                   os=parts[1] if len(parts) > 1 else "",
                   runtime=parts[2] if len(parts) > 2 else "")


# ---------------------------------------------------------------------------
# Baseline record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BaselineRecord:
    """One criterion's baseline outcome.

    ``criterion_id`` is a domain-neutral identifier (e.g. "C1", "metric_err_pct").
    ``failure_set_key`` is the composite key under which this criterion's failure
    is recorded in the baseline failure set (if it was failing).
    """
    criterion_id: str
    expected_outcome: CriterionOutcome   # PASS or FAIL_PRE_EXISTING
    failure_set_key: str = ""            # non-empty iff expected_outcome is FAIL_PRE_EXISTING
    metric_path: str = ""                # dotted path into measurement mapping
    threshold: float = 0.0
    op: str = "le"


@dataclass(frozen=True)
class BaselineSnapshot:
    """A complete baseline snapshot for one architecture.

    The snapshot is content-addressed: ``digest`` is the SHA-256 of the
    canonical JSON of the records list.  ``chain_hash`` links to the previous
    snapshot for chain-of-trust verification.
    """
    arch_key: ArchKey
    records: tuple[BaselineRecord, ...]
    digest: str = ""
    chain_hash: str = ""                 # hash of previous snapshot (empty for genesis)
    created_at: str = ""                 # ISO-8601 timestamp (for audit only, not hashed)
    schema_version: str = "acceptance-gate-maturity/1.0"

    def compute_digest(self) -> str:
        """Compute the content hash of the records."""
        payload = [
            {
                "criterion_id": r.criterion_id,
                "expected_outcome": r.expected_outcome.value,
                "failure_set_key": r.failure_set_key,
                "metric_path": r.metric_path,
                "threshold": r.threshold,
                "op": r.op,
            }
            for r in sorted(self.records, key=lambda r: r.criterion_id)
        ]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def compute_chain_hash(self, prev_digest: str = "") -> str:
        """Compute chain-of-trust hash linking to previous snapshot."""
        payload = {"digest": self.digest or self.compute_digest(),
                   "prev_digest": prev_digest,
                   "arch_key": self.arch_key.to_key()}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BaselineSnapshot":
        arch = ArchKey.from_key(raw["arch_key"]) if isinstance(raw.get("arch_key"), str) \
            else ArchKey(**raw["arch_key"])
        records = tuple(
            BaselineRecord(
                criterion_id=r["criterion_id"],
                expected_outcome=CriterionOutcome(r["expected_outcome"]),
                failure_set_key=r.get("failure_set_key", ""),
                metric_path=r.get("metric_path", ""),
                threshold=float(r.get("threshold", 0.0)),
                op=r.get("op", "le"),
            )
            for r in raw["records"]
        )
        return cls(
            arch_key=arch,
            records=records,
            digest=raw.get("digest", ""),
            chain_hash=raw.get("chain_hash", ""),
            created_at=raw.get("created_at", ""),
            schema_version=raw.get("schema_version", "acceptance-gate-maturity/1.0"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "arch_key": self.arch_key.to_key(),
            "records": [
                {
                    "criterion_id": r.criterion_id,
                    "expected_outcome": r.expected_outcome.value,
                    "failure_set_key": r.failure_set_key,
                    "metric_path": r.metric_path,
                    "threshold": r.threshold,
                    "op": r.op,
                }
                for r in self.records
            ],
            "digest": self.digest,
            "chain_hash": self.chain_hash,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }


# ---------------------------------------------------------------------------
# Diff result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CriterionDiff:
    """Per-criterion diff against baseline."""
    criterion_id: str
    outcome: CriterionOutcome
    measured_value: Any = None
    baseline_expected: CriterionOutcome = CriterionOutcome.PASS
    detail: str = ""


@dataclass(frozen=True)
class DiffResult:
    """Result of diffing current measurements against a baseline snapshot."""
    new_failures: tuple[str, ...]          # criterion_ids that regressed
    pre_existing_failures: tuple[str, ...] # criterion_ids that were already failing
    resolved: tuple[str, ...]              # criterion_ids that improved
    still_passing: tuple[str, ...]         # criterion_ids still passing
    missing_artifacts: tuple[str, ...]     # criterion_ids with missing data
    per_criterion: tuple[CriterionDiff, ...]

    @property
    def has_new_failures(self) -> bool:
        return len(self.new_failures) > 0

    @property
    def all_pre_existing(self) -> bool:
        return len(self.new_failures) == 0 and len(self.pre_existing_failures) > 0

    def gate_verdict(self) -> GateVerdict:
        if self.missing_artifacts:
            return GateVerdict.MISSING_ARTIFACT
        if self.has_new_failures:
            return GateVerdict.FAIL_NEW
        if self.pre_existing_failures:
            return GateVerdict.PASS_WITH_KNOWN
        return GateVerdict.PASS
