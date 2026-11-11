"""Auto-scan engine: traverse worktree and determine evidence levels for 21 capabilities.

Uses AST analysis + keyword matching to classify each capability as:
- REAL: real algorithm + tests with numeric verification
- MOCK: hardcoded/random values pretending to be physics
- STUB: empty shell (NotImplementedError, pass, etc.)
- WITHHELD: insufficient evidence to classify
- NOT_SUPPORTED: capability not implemented

The scanner integrates with existing tools/capability_audit_gate infrastructure
while providing a unified interface for the annotator pipeline.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.capability_annotator.registry import Capability, CapabilityRegistry, VALID_LEVELS

# Mock indicators - strong signals that code is fake
STRONG_MOCK_MARKERS = (
    "[MOCK]", "MOCK:", "np.random.rand", "random.uniform",
    "random.random", "mock_response", "dummy_value",
    "hardcoded_value", "fake_result",
)

# Stub indicators
STUB_MARKERS = (
    "NotImplementedError", "TODO: implement", "# stub", "placeholder",
    "pass  # not implemented", "raise NotImplementedError",
)

# Patterns for detecting numeric assertions in tests
NUMERIC_ASSERT = re.compile(
    r"assert .*(?:allclose|rtol|atol|approx|abs\(|pytest\.approx|\d+\.\d+)", re.I
)

# Patterns for detecting real algorithm indicators
REAL_ALGORITHM_MARKERS = (
    "def calculate", "def compute", "def solve", "def simulate",
    "math.", "numpy", "scipy", "np.", "import numpy",
    "equation", "formula", "coefficient", "iteration",
    "convergence", "residual", "boundary",
)


@dataclass
class ScanResult:
    """Result of scanning a single capability."""
    capability_id: str
    level: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    evidence_files: list[str] = field(default_factory=list)
    scan_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level": self.level,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "signals": self.signals,
            "evidence_files": self.evidence_files,
            "scan_time_ms": self.scan_time_ms,
        }


@dataclass
class ScanReport:
    """Complete scan report for all capabilities."""
    schema_version: str = "2.0"
    generated_at: str = ""
    worktree_root: str = ""
    capability_count: int = 0
    results: list[ScanResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    total_scan_time_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "worktree_root": self.worktree_root,
            "capability_count": self.capability_count,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "total_scan_time_sec": self.total_scan_time_sec,
        }


class CapabilityScanner:
    """Scan worktree and determine evidence levels for all 21 capabilities."""

    def __init__(self, worktree_root: str | Path, registry: CapabilityRegistry | None = None,
                 spec_path: str | Path | None = None):
        self.root = Path(worktree_root)
        self.registry = registry or CapabilityRegistry()
        self.spec_path = Path(spec_path) if spec_path else self.root / "config" / "capability_audit_gate" / "spec.json"
        self._cache: dict[str, ScanResult] = {}
        self._file_index: dict[str, list[str]] | None = None  # Lazy file index

    def scan_all(self) -> ScanReport:
        """Scan all 21 capabilities and return complete report."""
        t0 = time.perf_counter()
        results = []

        for cap in self.registry.capabilities:
            result = self._scan_capability(cap)
            results.append(result)
            self._cache[cap.capability_id] = result

        runtime = time.perf_counter() - t0

        # Build summary
        level_tally = {}
        for r in results:
            level_tally[r.level] = level_tally.get(r.level, 0) + 1

        report = ScanReport(
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            worktree_root=str(self.root),
            capability_count=len(results),
            results=results,
            summary={
                "level_tally": level_tally,
                "total_capabilities": len(results),
                "real_count": level_tally.get("REAL", 0),
                "mock_count": level_tally.get("MOCK", 0),
                "stub_count": level_tally.get("STUB", 0),
                "withheld_count": level_tally.get("WITHHELD", 0),
                "not_supported_count": level_tally.get("NOT_SUPPORTED", 0),
            },
            total_scan_time_sec=round(runtime, 3),
        )
        return report

    def scan_single(self, capability_id: str) -> ScanResult:
        """Scan a single capability."""
        cap = self.registry.get(capability_id)
        if not cap:
            return ScanResult(
                capability_id=capability_id,
                level="NOT_SUPPORTED",
                confidence=0.95,
                reasons=["CAPABILITY_NOT_IN_REGISTRY"],
            )
        return self._scan_capability(cap)

    def _scan_capability(self, cap: Capability) -> ScanResult:
        """Scan a single capability and determine its evidence level."""
        t0 = time.perf_counter()
        signals = {}
        reasons = []
        warnings = []
        evidence_files = []

        # 1. Check bound files existence (fast path)
        code_files = self._resolve_files(cap.bindings_code)
        test_files = self._resolve_files(cap.bindings_tests)

        # 2. If no bound files, skip expensive pattern matching for speed
        # Just mark as NOT_SUPPORTED or WITHHELD based on whether patterns exist
        if not code_files and not test_files:
            # Check if patterns are defined (capability should exist but files missing)
            if cap.code_patterns or cap.test_patterns:
                level = "WITHHELD"
                reason = "BINDINGS_NOT_RESOLVED"
            else:
                level = "NOT_SUPPORTED"
                reason = "NO_EVIDENCE_FOUND"

            scan_time = (time.perf_counter() - t0) * 1000
            return ScanResult(
                capability_id=cap.capability_id,
                level=level,
                confidence=0.7,
                reasons=[reason],
                signals={"code_files": 0, "test_files": 0},
                evidence_files=[],
                scan_time_ms=round(scan_time, 2),
            )

        signals["code_files"] = len(code_files)
        signals["test_files"] = len(test_files)
        evidence_files.extend(code_files)
        evidence_files.extend(test_files)

        # 3. Analyze code files for stub/mock/real indicators
        impl_signals = self._analyze_impl_files(code_files)
        test_signals = self._analyze_test_files(test_files)

        signals.update(impl_signals)
        signals.update(test_signals)

        # 4. Determine level using cascade rules
        level, level_reasons, level_warnings = self._determine_level(
            cap, impl_signals, test_signals, code_files, test_files
        )
        reasons.extend(level_reasons)
        warnings.extend(level_warnings)

        # 5. Calculate confidence
        confidence = self._calculate_confidence(level, signals, reasons)

        scan_time = (time.perf_counter() - t0) * 1000
        return ScanResult(
            capability_id=cap.capability_id,
            level=level,
            confidence=confidence,
            reasons=reasons,
            warnings=warnings,
            signals=signals,
            evidence_files=evidence_files,
            scan_time_ms=round(scan_time, 2),
        )

    def _resolve_files(self, patterns: list[str]) -> list[str]:
        """Resolve file patterns to actual file paths."""
        found = []
        for pattern in patterns:
            path = self.root / pattern
            if path.exists() and path.is_file():
                found.append(str(path.relative_to(self.root)))
        return found

    def _analyze_impl_files(self, files: list[str]) -> dict[str, Any]:
        """Analyze implementation files for code quality signals."""
        signals = {
            "total_sloc": 0,
            "total_functions": 0,
            "stub_functions": 0,
            "constant_returns": 0,
            "random_calls": 0,
            "mock_marker_hits": 0,
            "stub_marker_hits": 0,
            "real_algorithm_hits": 0,
            "parse_errors": 0,
        }

        for f in files:
            path = self.root / f
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                text_signals = self._analyze_text(content)
                for k, v in text_signals.items():
                    if k in signals:
                        signals[k] += v
            except Exception:
                signals["parse_errors"] += 1

        # Calculate ratios
        if signals["total_functions"] > 0:
            signals["stub_ratio"] = signals["stub_functions"] / signals["total_functions"]
            signals["constant_return_ratio"] = signals["constant_returns"] / signals["total_functions"]
        else:
            signals["stub_ratio"] = 1.0 if signals["total_sloc"] == 0 else 0.0
            signals["constant_return_ratio"] = 0.0

        return signals

    def _analyze_test_files(self, files: list[str]) -> dict[str, Any]:
        """Analyze test files for test quality signals."""
        signals = {
            "test_count": 0,
            "numeric_assertions": 0,
            "test_sloc": 0,
        }

        for f in files:
            path = self.root / f
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                # Count test functions
                signals["test_count"] += len(re.findall(r"def test_\w+", content))
                # Count numeric assertions
                signals["numeric_assertions"] += len(NUMERIC_ASSERT.findall(content))
                # Count SLOC
                signals["test_sloc"] += sum(
                    1 for line in content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
            except Exception:
                pass

        return signals

    def _analyze_text(self, content: str) -> dict[str, int]:
        """Analyze text content for various signals."""
        signals = {
            "total_sloc": 0,
            "total_functions": 0,
            "stub_functions": 0,
            "constant_returns": 0,
            "random_calls": 0,
            "mock_marker_hits": 0,
            "stub_marker_hits": 0,
            "real_algorithm_hits": 0,
        }

        # Count SLOC
        signals["total_sloc"] = sum(
            1 for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

        # Try AST analysis
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    signals["total_functions"] += 1
                    if self._is_stub_function(node):
                        signals["stub_functions"] += 1
                    if self._returns_constant(node):
                        signals["constant_returns"] += 1
                elif isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    if name in ("random", "rand", "randn", "randint", "uniform", "choice"):
                        signals["random_calls"] += 1
        except SyntaxError:
            pass

        # Count markers
        for marker in STRONG_MOCK_MARKERS:
            signals["mock_marker_hits"] += content.count(marker)
        for marker in STUB_MARKERS:
            signals["stub_marker_hits"] += content.count(marker)
        for marker in REAL_ALGORITHM_MARKERS:
            if marker.lower() in content.lower():
                signals["real_algorithm_hits"] += 1

        return signals

    def _is_stub_function(self, node: ast.AST) -> bool:
        """Check if a function is a stub."""
        body = list(getattr(node, "body", []))
        # Remove docstring
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]

        if not body:
            return True
        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                return True
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                if stmt.value.value is Ellipsis:
                    return True
            if isinstance(stmt, ast.Raise) and stmt.exc is not None:
                name = ast.unparse(stmt.exc) if hasattr(ast, "unparse") else ""
                if "NotImplementedError" in name:
                    return True
        return False

    def _returns_constant(self, node: ast.AST) -> bool:
        """Check if function just returns a constant."""
        body = list(getattr(node, "body", []))
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]
        return (len(body) == 1 and isinstance(body[0], ast.Return)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, (int, float))
                and not isinstance(body[0].value.value, bool))

    def _determine_level(self, cap: Capability, impl: dict, tests: dict,
                         code_files: list[str], test_files: list[str]) -> tuple[str, list[str], list[str]]:
        """Determine evidence level using cascade rules."""
        reasons = []
        warnings = []

        # Rule 1: No code files
        if not code_files:
            return "NOT_SUPPORTED", ["NO_CODE_FILES"], []

        # Rule 2: Stub dominated
        if impl["total_functions"] == 0 or impl.get("stub_ratio", 0) >= 0.5:
            return "STUB", ["STUB_DOMINATED"], []

        # Rule 3: Low SLOC
        if impl["total_sloc"] < 6:
            warnings.append("LOW_SLOC")

        # Rule 4: Mock signals
        mock_signals = 0
        if impl["mock_marker_hits"] >= 2:
            mock_signals += 1
        if impl["random_calls"] >= 2:
            mock_signals += 1
        if impl.get("constant_return_ratio", 0) >= 0.5:
            mock_signals += 1

        if mock_signals >= 2:
            return "MOCK", ["MOCK_SIGNAL_DETECTED"], []

        # Rule 5: No tests
        if not test_files or tests["test_count"] == 0:
            return "WITHHELD", ["NO_TESTS"], []

        # Rule 6: No numeric assertions in tests
        if tests["numeric_assertions"] == 0:
            return "WITHHELD", ["TESTS_NON_NUMERIC"], []

        # Rule 7: Has real algorithm markers + tests with numeric assertions
        if impl["real_algorithm_hits"] >= 2 and tests["numeric_assertions"] >= 1:
            return "REAL", ["REAL_ALGORITHM_WITH_TESTS"], []

        # Rule 8: Has tests but weak evidence
        if tests["test_count"] >= 1:
            return "WITHHELD", ["INSUFFICIENT_EVIDENCE"], []

        return "WITHHELD", ["EVIDENCE_NOT_BOUND"], []

    def _calculate_confidence(self, level: str, signals: dict, reasons: list[str]) -> float:
        """Calculate confidence score for the level determination."""
        base_confidence = {
            "REAL": 0.9,
            "MOCK": 0.85,
            "STUB": 0.85,
            "WITHHELD": 0.6,
            "NOT_SUPPORTED": 0.95,
        }.get(level, 0.5)

        # Adjust based on evidence strength
        if level == "REAL":
            if signals.get("numeric_assertions", 0) >= 3:
                base_confidence += 0.05
            if signals.get("real_algorithm_hits", 0) >= 5:
                base_confidence += 0.05
        elif level == "WITHHELD":
            if "EVIDENCE_NOT_BOUND" in reasons:
                base_confidence = 0.5

        return min(1.0, base_confidence)

    def save_report(self, report: ScanReport, path: str | Path):
        """Save scan report to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
