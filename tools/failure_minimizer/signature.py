"""
Failure signature definition and computation.

A failure signature is a normalized tuple of:
- exit_code: process exit code
- fail_stage: where in the pipeline it failed
- first_exception_hash: hash of first exception/assertion text
- missing_artifacts: sorted list of missing artifact paths

This signature serves as the oracle for delta debugging.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class FailureSignature:
    """Immutable failure signature for oracle comparison."""
    exit_code: int
    fail_stage: str
    first_exception_hash: str
    missing_artifacts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "exit_code": self.exit_code,
            "fail_stage": self.fail_stage,
            "first_exception_hash": self.first_exception_hash,
            "missing_artifacts": list(self.missing_artifacts),
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FailureSignature":
        """Deserialize from dictionary."""
        return cls(
            exit_code=d["exit_code"],
            fail_stage=d["fail_stage"],
            first_exception_hash=d["first_exception_hash"],
            missing_artifacts=tuple(d["missing_artifacts"]),
        )

    def matches(self, other: "FailureSignature") -> bool:
        """Check if two signatures match (oracle comparison)."""
        return (
            self.exit_code == other.exit_code
            and self.fail_stage == other.fail_stage
            and self.first_exception_hash == other.first_exception_hash
            and self.missing_artifacts == other.missing_artifacts
        )


def compute_signature(
    exit_code: int,
    fail_stage: str,
    exception_text: str | None,
    missing_artifacts: list[str] | None,
) -> FailureSignature:
    """
    Compute a failure signature from execution results.

    Args:
        exit_code: Process exit code (0=success, non-zero=failure)
        fail_stage: Pipeline stage where failure occurred
        exception_text: First exception or assertion error text
        missing_artifacts: List of expected but missing artifact paths

    Returns:
        FailureSignature instance
    """
    # Hash the exception text for normalization
    if exception_text:
        # Normalize whitespace and compute SHA256
        normalized = " ".join(exception_text.split())
        exc_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    else:
        exc_hash = "no_exception"

    # Normalize missing artifacts
    artifacts = tuple(sorted(set(missing_artifacts or [])))

    return FailureSignature(
        exit_code=exit_code,
        fail_stage=fail_stage or "unknown",
        first_exception_hash=exc_hash,
        missing_artifacts=artifacts,
    )


def signature_from_result(result: dict[str, Any]) -> FailureSignature:
    """
    Extract failure signature from a mission result dictionary.

    Expected result structure:
    {
        "exit_code": int,
        "fail_stage": str,
        "exception": str or None,
        "missing_artifacts": list[str] or None,
        ...
    }
    """
    return compute_signature(
        exit_code=result.get("exit_code", -1),
        fail_stage=result.get("fail_stage", "unknown"),
        exception_text=result.get("exception"),
        missing_artifacts=result.get("missing_artifacts"),
    )
