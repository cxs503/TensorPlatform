"""End-to-end acceptance gate runner.

Orchestrates the full acceptance gate pipeline:
  1. Load baseline snapshot(s) for the target architecture(s).
  2. Verify baseline integrity (tamper detection).
  3. Diff current measurements against baseline.
  4. If multi-arch: separate by arch and report drift.
  5. Produce final verdict with timing.

Domain-neutral: all inputs arrive via configuration and artifacts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .model import (
    ArchKey,
    BaselineSnapshot,
    DiffResult,
    GateVerdict,
    TamperVerdict,
)
from .baseline_diff import diff_against_baseline
from .tamper_detect import verify_baseline_integrity, TamperReport
from .arch_separation import separate_by_arch, ArchDriftReport


@dataclass(frozen=True)
class AcceptanceGateResult:
    """Complete acceptance gate result."""
    verdict: GateVerdict
    diff: DiffResult | None = None
    tamper_report: TamperReport | None = None
    arch_drift: ArchDriftReport | None = None
    elapsed_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "verdict": self.verdict.value,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }
        if self.tamper_report:
            out["tamper_report"] = self.tamper_report.to_dict()
        if self.diff:
            out["diff"] = {
                "new_failures": list(self.diff.new_failures),
                "pre_existing_failures": list(self.diff.pre_existing_failures),
                "resolved": list(self.diff.resolved),
                "still_passing": list(self.diff.still_passing),
                "missing_artifacts": list(self.diff.missing_artifacts),
            }
        if self.arch_drift:
            out["arch_drift"] = self.arch_drift.to_dict()
        if self.details:
            out["details"] = self.details
        return out


def run_acceptance_gate(
    baseline: BaselineSnapshot,
    measurements: Mapping[str, Any],
    *,
    raw_baseline: Mapping[str, Any] | None = None,
    prev_digest: str = "",
    extra_baselines: Mapping[str, BaselineSnapshot] | None = None,
    extra_measurements: Mapping[str, Mapping[str, Any]] | None = None,
) -> AcceptanceGateResult:
    """Run the full acceptance gate pipeline.

    Parameters
    ----------
    baseline : BaselineSnapshot
        Primary baseline snapshot for the target architecture.
    measurements : mapping
        Current measurements to evaluate.
    raw_baseline : mapping, optional
        Raw dict form of the baseline (for structural tamper checks).
    prev_digest : str, optional
        Previous snapshot digest (for chain-of-trust verification).
    extra_baselines : mapping, optional
        Additional baselines for other architectures (for cross-arch drift).
    extra_measurements : mapping, optional
        Measurements for other architectures.

    Returns
    -------
    AcceptanceGateResult with verdict, diff, tamper report, and timing.
    """
    t0 = time.monotonic()

    # Step 1: Tamper detection
    tamper_report = verify_baseline_integrity(
        baseline, raw=raw_baseline, prev_digest=prev_digest
    )

    if tamper_report.verdict == TamperVerdict.TAMPERED:
        elapsed = (time.monotonic() - t0) * 1000
        return AcceptanceGateResult(
            verdict=GateVerdict.BASELINE_TAMPERED,
            tamper_report=tamper_report,
            elapsed_ms=elapsed,
            details={"tamper_findings": len(tamper_report.findings)},
        )

    # Step 2: Baseline diff
    diff = diff_against_baseline(baseline, measurements)

    # Step 3: Check for missing artifacts (fail-closed)
    if diff.missing_artifacts:
        elapsed = (time.monotonic() - t0) * 1000
        return AcceptanceGateResult(
            verdict=GateVerdict.MISSING_ARTIFACT,
            diff=diff,
            tamper_report=tamper_report,
            elapsed_ms=elapsed,
            details={"missing": list(diff.missing_artifacts)},
        )

    # Step 4: Cross-architecture drift (if multi-arch)
    arch_drift = None
    if extra_baselines and extra_measurements:
        all_baselines = {baseline.arch_key.to_key(): baseline}
        all_baselines.update(extra_baselines)
        all_measurements = {baseline.arch_key.to_key(): measurements}
        all_measurements.update(extra_measurements)
        arch_drift = separate_by_arch(all_baselines, all_measurements)

    # Step 5: Final verdict
    verdict = diff.gate_verdict()
    if arch_drift and arch_drift.overall_verdict != GateVerdict.PASS:
        # Cross-arch issues can escalate the verdict
        if arch_drift.overall_verdict == GateVerdict.FAIL_NEW:
            verdict = GateVerdict.FAIL_NEW

    elapsed = (time.monotonic() - t0) * 1000
    return AcceptanceGateResult(
        verdict=verdict,
        diff=diff,
        tamper_report=tamper_report,
        arch_drift=arch_drift,
        elapsed_ms=elapsed,
    )
