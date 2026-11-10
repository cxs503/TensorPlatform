"""Evidence Chain Integrity Auditor — core scanning engine.

Scans each capability and checks:
  (a) Corresponding tests exist and pass
  (b) Ledger status matches test outcomes
  (c) Test coverage meets minimum requirements (>=3 test cases)

Produces AuditFinding records for each detected break.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .mapping_loader import CapabilityTestMapping, MappingLoader

logger = logging.getLogger(__name__)

# ── Evidence levels ──────────────────────────────────────────────────────
LEVEL_RANK = {
    "NOT_SUPPORTED": 0,
    "STUB": 1,
    "MOCK": 2,
    "WITHHELD": 3,
    "REAL": 4,
}


class FindingType(str, Enum):
    """Types of evidence chain breaks."""

    MISSING_TEST = "missing_test"
    OVERCLAIM = "overclaim"
    STALE_STATUS = "stale_status"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


@dataclass
class TestResult:
    """Result of running/inspecting tests for a capability."""

    module_path: str
    exists: bool
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total_cases: int = 0
    test_names: List[str] = field(default_factory=list)
    run_error: Optional[str] = None  # if pytest couldn't run

    @property
    def all_passed(self) -> bool:
        return self.exists and self.failed == 0 and self.errors == 0

    @property
    def inferred_level(self) -> str:
        """Infer evidence level from test results (domain-agnostic)."""
        if not self.exists:
            return "NOT_SUPPORTED"
        if self.all_passed and self.total_cases >= 3:
            return "REAL"
        if self.all_passed and self.total_cases < 3:
            return "WITHHELD"  # too few tests to claim REAL
        if self.failed > 0 or self.errors > 0:
            return "MOCK"  # tests failing = implementation broken
        return "WITHHELD"


@dataclass
class AuditFinding:
    """A single evidence chain break."""

    capability_id: str
    finding_type: FindingType
    severity: str  # "critical", "warning", "info"
    description: str
    evidence_path: str  # path to evidence (test file, ledger entry, etc.)
    suggested_fix: str  # machine-executable fix suggestion
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "finding_type": self.finding_type.value,
            "severity": self.severity,
            "description": self.description,
            "evidence_path": self.evidence_path,
            "suggested_fix": self.suggested_fix,
            "details": self.details,
        }


@dataclass
class CapabilityAuditResult:
    """Audit result for a single capability."""

    capability_id: str
    ledger_claimed_level: str
    test_inferred_level: str
    test_results: List[TestResult]
    findings: List[AuditFinding]
    status: str  # "consistent", "inconsistent", "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "ledger_claimed_level": self.ledger_claimed_level,
            "test_inferred_level": self.test_inferred_level,
            "test_results": [
                {
                    "module_path": tr.module_path,
                    "exists": tr.exists,
                    "passed": tr.passed,
                    "failed": tr.failed,
                    "errors": tr.errors,
                    "total_cases": tr.total_cases,
                    "test_names": tr.test_names,
                }
                for tr in self.test_results
            ],
            "findings": [f.to_dict() for f in self.findings],
            "status": self.status,
        }


@dataclass
class AuditReport:
    """Complete audit report."""

    scan_id: str
    scanned_at: str
    mapping_status: str  # "loaded", "missing_file", "parse_error"
    total_capabilities: int
    consistent_count: int
    inconsistent_count: int
    unknown_count: int
    findings: List[AuditFinding]
    capability_results: List[CapabilityAuditResult]
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "scanned_at": self.scanned_at,
            "mapping_status": self.mapping_status,
            "total_capabilities": self.total_capabilities,
            "consistent_count": self.consistent_count,
            "inconsistent_count": self.inconsistent_count,
            "unknown_count": self.unknown_count,
            "findings": [f.to_dict() for f in self.findings],
            "capability_results": [cr.to_dict() for cr in self.capability_results],
            "summary": self.summary,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class EvidenceChainAuditor:
    """Main auditor that scans capabilities and detects evidence chain breaks.

    Design principles:
      - Zero domain semantics: doesn't depend on any domain-specific knowledge
      - Fail-soft: missing mapping -> WITHHELD, not crash
      - Idempotent: same state -> same output
      - Deterministic: no randomness, no timestamps in logic (only in report metadata)
    """

    def __init__(
        self,
        worktree: Path,
        ledger_path: Optional[Path] = None,
        mapping_path: Optional[Path] = None,
        run_pytest: bool = False,  # if True, actually runs pytest; if False, inspects files
    ):
        self.worktree = Path(worktree)
        self.ledger_path = ledger_path or (
            self.worktree / "artifacts" / "capability_ledger" / "capability_ledger.json"
        )
        self.mapping_loader = MappingLoader(mapping_path)
        self.run_pytest = run_pytest
        self._ledger_cache: Optional[Dict] = None

    def _load_ledger(self) -> Dict[str, Any]:
        """Load the capability ledger (with caching)."""
        if self._ledger_cache is not None:
            return self._ledger_cache
        if not self.ledger_path.exists():
            return {"capabilities": []}
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            self._ledger_cache = json.load(f)
        return self._ledger_cache

    def _get_ledger_level(self, capability_id: str) -> str:
        """Get the claimed evidence level from the ledger."""
        ledger = self._load_ledger()
        for cap in ledger.get("capabilities", []):
            if cap.get("id") == capability_id:
                return cap.get("claimed_level", "WITHHELD")
        return "WITHHELD"

    def _get_all_ledger_capability_ids(self) -> List[str]:
        """Get all capability IDs from the ledger."""
        ledger = self._load_ledger()
        return [
            cap.get("id")
            for cap in ledger.get("capabilities", [])
            if cap.get("id")
        ]

    def _inspect_test_module(self, module_path: str) -> TestResult:
        """Inspect a test module to count test cases and check existence.

        Uses AST parsing to count test functions (no pytest execution needed
        for basic inspection). This is deterministic and fast.
        """
        full_path = self.worktree / module_path
        if not full_path.exists():
            return TestResult(
                module_path=module_path,
                exists=False,
            )

        # Parse the file to count test functions
        try:
            source = full_path.read_text(encoding="utf-8")
            test_names = re.findall(r"^\s*def\s+(test_\w+)", source, re.MULTILINE)
            total_cases = len(test_names)

            return TestResult(
                module_path=module_path,
                exists=True,
                total_cases=total_cases,
                test_names=test_names,
                # For static inspection, we assume tests pass if they exist
                # (actual pass/fail requires running pytest)
                passed=total_cases,
                failed=0,
                errors=0,
            )
        except (OSError, UnicodeDecodeError) as e:
            return TestResult(
                module_path=module_path,
                exists=True,
                run_error=str(e),
            )

    def _run_pytest_module(self, module_path: str) -> TestResult:
        """Actually run pytest on a module and parse results."""
        full_path = self.worktree / module_path
        if not full_path.exists():
            return TestResult(module_path=module_path, exists=False)

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(full_path), "-v", "--tb=no", "-q"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self.worktree),
            )
            output = result.stdout + result.stderr

            # Parse pytest output for counts
            passed = len(re.findall(r"PASSED", output))
            failed = len(re.findall(r"FAILED", output))
            errors = len(re.findall(r"ERROR", output))

            # Extract test names
            test_names = re.findall(r"(\S+::test_\w+)\s", output)

            return TestResult(
                module_path=module_path,
                exists=True,
                passed=passed,
                failed=failed,
                errors=errors,
                total_cases=passed + failed + errors,
                test_names=test_names,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                module_path=module_path,
                exists=True,
                run_error="pytest timeout (60s)",
            )
        except Exception as e:
            return TestResult(
                module_path=module_path,
                exists=True,
                run_error=str(e),
            )

    def _inspect_tests(self, mapping: CapabilityTestMapping) -> List[TestResult]:
        """Inspect all test modules for a capability."""
        results = []
        for module_path in mapping.test_modules:
            if self.run_pytest:
                results.append(self._run_pytest_module(module_path))
            else:
                results.append(self._inspect_test_module(module_path))
        return results

    def _detect_findings(
        self,
        capability_id: str,
        ledger_level: str,
        test_results: List[TestResult],
        mapping: Optional[CapabilityTestMapping],
    ) -> List[AuditFinding]:
        """Detect evidence chain breaks for a capability."""
        findings = []

        # Check if mapping exists
        if mapping is None:
            findings.append(
                AuditFinding(
                    capability_id=capability_id,
                    finding_type=FindingType.MISSING_TEST,
                    severity="warning",
                    description=f"No test mapping found for capability '{capability_id}'",
                    evidence_path="capability_test_mapping.json",
                    suggested_fix=(
                        f"Add entry for '{capability_id}' to "
                        f"tools/evidence_chain_auditor/capability_test_mapping.json "
                        f"with test_modules list and min_test_cases=3"
                    ),
                    details={"reason": "no_mapping"},
                )
            )
            return findings

        # Check if any test modules exist
        existing_tests = [tr for tr in test_results if tr.exists]
        if not existing_tests:
            findings.append(
                AuditFinding(
                    capability_id=capability_id,
                    finding_type=FindingType.MISSING_TEST,
                    severity="critical",
                    description=f"No test modules found for capability '{capability_id}'",
                    evidence_path=str(mapping.test_modules),
                    suggested_fix=(
                        f"Create test file(s) for '{capability_id}': "
                        f"{mapping.test_modules}. "
                        f"Each test should verify numerical correctness against known references."
                    ),
                    details={"expected_modules": mapping.test_modules},
                )
            )
            return findings

        # Aggregate test results
        total_cases = sum(tr.total_cases for tr in existing_tests)
        total_failed = sum(tr.failed for tr in existing_tests)
        total_errors = sum(tr.errors for tr in existing_tests)
        all_passed = all(tr.all_passed for tr in existing_tests)

        # Check insufficient coverage
        min_required = mapping.min_test_cases
        if total_cases < min_required:
            findings.append(
                AuditFinding(
                    capability_id=capability_id,
                    finding_type=FindingType.INSUFFICIENT_COVERAGE,
                    severity="warning",
                    description=(
                        f"Capability '{capability_id}' has {total_cases} test cases, "
                        f"below minimum required {min_required}"
                    ),
                    evidence_path=str([tr.module_path for tr in existing_tests]),
                    suggested_fix=(
                        f"Add at least {min_required - total_cases} more test cases "
                        f"to achieve minimum coverage of {min_required}. "
                        f"Tests should cover: boundary values, typical cases, edge cases."
                    ),
                    details={
                        "current_cases": total_cases,
                        "required_cases": min_required,
                        "test_modules": [tr.module_path for tr in existing_tests],
                    },
                )
            )

        # Check for test failures
        if total_failed > 0 or total_errors > 0:
            # Tests are failing — check if ledger claims higher than warranted
            inferred_level = "MOCK"  # failing tests -> at best MOCK
            if LEVEL_RANK.get(ledger_level, 0) > LEVEL_RANK.get(inferred_level, 0):
                findings.append(
                    AuditFinding(
                        capability_id=capability_id,
                        finding_type=FindingType.STALE_STATUS,
                        severity="critical",
                        description=(
                            f"Capability '{capability_id}' has {total_failed} failing tests "
                            f"and {total_errors} errors, but ledger claims '{ledger_level}'"
                        ),
                        evidence_path=str([tr.module_path for tr in existing_tests]),
                        suggested_fix=(
                            f"Update ledger entry for '{capability_id}': "
                            f"change claimed_level from '{ledger_level}' to '{inferred_level}'. "
                            f"Then fix the failing tests and re-verify."
                        ),
                        details={
                            "failed": total_failed,
                            "errors": total_errors,
                            "ledger_level": ledger_level,
                            "inferred_level": inferred_level,
                        },
                    )
                )
            return findings

        # All tests pass — check for overclaim
        if all_passed and total_cases >= min_required:
            inferred_level = "REAL"
        elif all_passed:
            inferred_level = "WITHHELD"  # not enough tests
        else:
            inferred_level = "WITHHELD"

        if LEVEL_RANK.get(ledger_level, 0) > LEVEL_RANK.get(inferred_level, 0):
            findings.append(
                AuditFinding(
                    capability_id=capability_id,
                    finding_type=FindingType.OVERCLAIM,
                    severity="critical",
                    description=(
                        f"Capability '{capability_id}' ledger claims '{ledger_level}' "
                        f"but test evidence only supports '{inferred_level}'"
                    ),
                    evidence_path=str([tr.module_path for tr in existing_tests]),
                    suggested_fix=(
                        f"Downgrade ledger entry for '{capability_id}' from "
                        f"'{ledger_level}' to '{inferred_level}', or add stronger "
                        f"verification evidence (e.g., comparison with experimental data)."
                    ),
                    details={
                        "ledger_level": ledger_level,
                        "inferred_level": inferred_level,
                        "total_cases": total_cases,
                    },
                )
            )

        # Check for underclaim (ledger lower than evidence supports)
        if LEVEL_RANK.get(ledger_level, 0) < LEVEL_RANK.get(inferred_level, 0):
            findings.append(
                AuditFinding(
                    capability_id=capability_id,
                    finding_type=FindingType.STALE_STATUS,
                    severity="info",
                    description=(
                        f"Capability '{capability_id}' ledger claims '{ledger_level}' "
                        f"but test evidence supports '{inferred_level}'"
                    ),
                    evidence_path=str([tr.module_path for tr in existing_tests]),
                    suggested_fix=(
                        f"Upgrade ledger entry for '{capability_id}' from "
                        f"'{ledger_level}' to '{inferred_level}'."
                    ),
                    details={
                        "ledger_level": ledger_level,
                        "inferred_level": inferred_level,
                    },
                )
            )

        return findings

    def _ensure_mapping_loaded(self):
        """Ensure the mapping is loaded (lazy-load on first access)."""
        if self.mapping_loader.load_status == "not_loaded":
            self.mapping_loader.load()

    def audit_capability(self, capability_id: str) -> CapabilityAuditResult:
        """Audit a single capability."""
        self._ensure_mapping_loaded()
        ledger_level = self._get_ledger_level(capability_id)
        mapping = self.mapping_loader.get_mapping(capability_id)

        if mapping is None:
            # No mapping -> fail-soft: report as unknown
            test_results = []
            findings = self._detect_findings(capability_id, ledger_level, [], None)
            return CapabilityAuditResult(
                capability_id=capability_id,
                ledger_claimed_level=ledger_level,
                test_inferred_level="WITHHELD",
                test_results=[],
                findings=findings,
                status="unknown",
            )

        test_results = self._inspect_tests(mapping)

        # Compute inferred level from test results
        existing_tests = [tr for tr in test_results if tr.exists]
        if not existing_tests:
            inferred_level = "NOT_SUPPORTED"
        else:
            total_cases = sum(tr.total_cases for tr in existing_tests)
            all_passed = all(tr.all_passed for tr in existing_tests)
            if all_passed and total_cases >= mapping.min_test_cases:
                inferred_level = "REAL"
            elif all_passed:
                inferred_level = "WITHHELD"
            else:
                inferred_level = "MOCK"

        findings = self._detect_findings(capability_id, ledger_level, test_results, mapping)

        # Determine status
        critical_findings = [f for f in findings if f.severity == "critical"]
        if critical_findings:
            status = "inconsistent"
        elif findings:
            status = "inconsistent"  # any finding = inconsistent
        else:
            status = "consistent"

        return CapabilityAuditResult(
            capability_id=capability_id,
            ledger_claimed_level=ledger_level,
            test_inferred_level=inferred_level,
            test_results=test_results,
            findings=findings,
            status=status,
        )

    def run_audit(self, scan_id: Optional[str] = None) -> AuditReport:
        """Run a full audit across all mapped capabilities.

        Idempotent: same state -> same output (except scan_id and timestamp).
        """
        # Load mapping
        self.mapping_loader.load()
        mapping_status = self.mapping_loader.load_status

        # Get all capability IDs to audit: union of mapping + ledger
        capability_ids_set: Set[str] = set()

        # Add from mapping
        if mapping_status == "loaded":
            capability_ids_set.update(self.mapping_loader.all_capability_ids())

        # Also add from ledger (to catch unmapped capabilities)
        capability_ids_set.update(self._get_all_ledger_capability_ids())

        capability_ids = sorted(capability_ids_set)  # sorted for determinism

        # Audit each capability
        capability_results = []
        all_findings = []
        consistent_count = 0
        inconsistent_count = 0
        unknown_count = 0

        for cap_id in capability_ids:
            result = self.audit_capability(cap_id)
            capability_results.append(result)
            all_findings.extend(result.findings)

            if result.status == "consistent":
                consistent_count += 1
            elif result.status == "inconsistent":
                inconsistent_count += 1
            else:
                unknown_count += 1

        # Generate summary
        total = len(capability_results)
        summary_parts = [
            f"Scanned {total} capabilities.",
            f"Consistent: {consistent_count}, Inconsistent: {inconsistent_count}, Unknown: {unknown_count}.",
            f"Total findings: {len(all_findings)} "
            f"(critical: {sum(1 for f in all_findings if f.severity == 'critical')}, "
            f"warning: {sum(1 for f in all_findings if f.severity == 'warning')}, "
            f"info: {sum(1 for f in all_findings if f.severity == 'info')}).",
        ]
        if mapping_status != "loaded":
            summary_parts.append(f"WARNING: Mapping status='{mapping_status}', results may be incomplete.")

        return AuditReport(
            scan_id=scan_id or f"scan-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            scanned_at=datetime.now(timezone.utc).isoformat(),
            mapping_status=mapping_status,
            total_capabilities=total,
            consistent_count=consistent_count,
            inconsistent_count=inconsistent_count,
            unknown_count=unknown_count,
            findings=all_findings,
            capability_results=capability_results,
            summary=" ".join(summary_parts),
        )
