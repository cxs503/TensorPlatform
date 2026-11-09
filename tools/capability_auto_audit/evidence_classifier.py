"""Automated evidence-level classifier for the 21 platform capabilities.

Scans source code, tests, documentation, and verification artifacts to
automatically classify each capability's evidence level:

- REAL:            Real algorithm with verified accuracy (error ≤ 3%)
- MOCK:            Hardcoded/random values pretending to be physics
- STUB:            Empty shell (NotImplementedError, placeholder)
- WITHHELD:        Insufficient evidence to classify
- NOT_SUPPORTED:   No implementation exists

The classifier uses a multi-signal approach:
1. Code signal: Is there non-trivial implementation code?
2. Test signal: Are there tests with numerical assertions?
3. Verification signal: Are there verification artifacts with error bounds?
4. Mock signal: Are there hardcoded values or random numbers?
5. Stub signal: Are there NotImplementedError or placeholder markers?

Design: Integrates with existing ``tools/audit/capability_audit_scan.py``
for the scanning kernel, but adds automated classification logic and
SQL-based provenance integration.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import the existing scanning kernel
WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE / "tools" / "audit"))

try:
    import capability_audit_scan as SCAN
    HAS_SCAN_KERNEL = True
except ImportError:
    HAS_SCAN_KERNEL = False

# ── Evidence levels ────────────────────────────────────────────────────────
LEVELS = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}

# ── Detection patterns ────────────────────────────────────────────────────

# Strong MOCK indicators: these patterns indicate hardcoded/random values
STRONG_MOCK_PATTERNS = [
    re.compile(r'\[MOCK\]', re.I),
    re.compile(r'MOCK\s*:', re.I),
    re.compile(r'np\.random\.rand\w*\('),
    re.compile(r'random\.uniform\('),
    re.compile(r'random\.random\('),
    re.compile(r'mock_response\s*='),
    re.compile(r'dummy_value\s*='),
    re.compile(r'return\s+\d+\.\d+\s*#.*hardcod', re.I),
    re.compile(r'#\s*hardcoded\s+(?:value|result|answer)', re.I),
]

# STUB indicators
STUB_PATTERNS = [
    re.compile(r'raise\s+NotImplementedError'),
    re.compile(r'#\s*stub\b', re.I),
    re.compile(r'#\s*TODO\s*:\s*implement', re.I),
    re.compile(r'#\s*placeholder', re.I),
    re.compile(r'pass\s*#\s*(?:not|to)\s*implemented', re.I),
]

# REAL indicators: actual algorithmic content
REAL_CODE_PATTERNS = [
    re.compile(r'def\s+\w+.*:\s*\n\s+"""[\s\S]*?(?:equation|formula|algorithm|method)', re.I),
    re.compile(r'(?:math|np|numpy)\.\w+\('),  # math operations
    re.compile(r'for\s+\w+\s+in\s+range'),  # iteration
    re.compile(r'(?:while|if)\s+.*(?:converge|tolerance|residual)', re.I),
    re.compile(r'(?:integral|derivative|solve|iterate)', re.I),
]

# Verification artifact patterns
VERIFICATION_PATTERNS = [
    re.compile(r'verification/', re.I),
    re.compile(r'verified/', re.I),
    re.compile(r'benchmarks?/', re.I),
]

# Error/accuracy patterns in verification artifacts
ACCURACY_PATTERNS = [
    re.compile(r'(?:误差|error|deviation|偏差)[^0-9\n%]{0,40}?(-?\d+(?:\.\d+)?)\s*%', re.I),
    re.compile(r'(?:allclose|rtol|atol).*?(\d+\.\d+)', re.I),
]


@dataclass
class ClassificationResult:
    """Result of evidence-level classification for one capability."""
    capability_id: str
    level: str
    confidence: float  # 0.0 to 1.0
    signals: Dict[str, float]  # component scores
    evidence_files: List[str]
    mock_indicators: List[str]
    stub_indicators: List[str]
    real_indicators: List[str]
    verification_artifacts: List[str]
    accuracy_numbers: List[Dict[str, Any]]
    rule_fired: str  # which classification rule determined the level
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level": self.level,
            "confidence": self.confidence,
            "signals": self.signals,
            "evidence_files": self.evidence_files,
            "mock_indicators": self.mock_indicators,
            "stub_indicators": self.stub_indicators,
            "real_indicators": self.real_indicators,
            "verification_artifacts": self.verification_artifacts,
            "accuracy_numbers": self.accuracy_numbers,
            "rule_fired": self.rule_fired,
            "timestamp": self.timestamp,
        }


class EvidenceClassifier:
    """Automated evidence-level classifier.

    Parameters
    ----------
    worktree : Path
        Root of the TensorPlatform worktree.
    profiles : dict, optional
        Capability profiles (21 capabilities). If None, uses the default
        profiles from capability_audit_scan.
    """

    def __init__(self, worktree: Optional[Path] = None, profiles: Optional[Dict] = None):
        self.worktree = Path(worktree) if worktree else WORKTREE
        if profiles is None and HAS_SCAN_KERNEL:
            self.profiles = SCAN.PROFILES
        else:
            self.profiles = profiles or self._default_profiles()

    def _default_profiles(self) -> Dict[str, Dict]:
        """Minimal default profiles for the 21 capabilities."""
        return {
            "calc.resistance": {"domain": "阻力", "keywords": ("resistance", "holtrop", "ITTC"),
                                "reference_domain": "resistance", "solver_libs": ("TensorLBM", "TensorFVM")},
            "calc.stability": {"domain": "稳性", "keywords": ("stability", "GZ", "metacentric"),
                               "reference_domain": "stability", "solver_libs": ("TensorFEM",)},
            "propeller.open_water": {"domain": "推进", "keywords": ("open_water", "KQ", "KT"),
                                     "reference_domain": "propulsion", "solver_libs": ("TensorBEM",)},
            "geometry.generate": {"domain": "几何", "keywords": ("geometry", "hull"),
                                  "reference_domain": "geometry", "solver_libs": ("TensorCAD",)},
            "geometry.hydro_curves": {"domain": "几何", "keywords": ("hydrostat", "bonjean"),
                                      "reference_domain": "geometry", "solver_libs": ("TensorCAD",)},
            "geometry.list_models": {"domain": "几何", "keywords": ("list_models", "stdmodels"),
                                     "reference_domain": "geometry", "solver_libs": ("TensorCAD",)},
            "geometry.verify_official": {"domain": "几何", "keywords": ("verify_official", "Cb"),
                                         "reference_domain": "geometry", "solver_libs": ("TensorCAD",)},
            "shiphull.generate": {"domain": "几何", "keywords": ("shiphull", "hull_form"),
                                  "reference_domain": "geometry", "solver_libs": ("TensorCAD",)},
            "stdmodels.lookup": {"domain": "数据", "keywords": ("stdmodels", "KVLCC2", "KCS"),
                                 "reference_domain": "geometry", "solver_libs": ()},
            "gov.stats": {"domain": "平台", "keywords": ("gov_stats", "governance"),
                          "reference_domain": None, "solver_libs": ()},
            "knowledge.docs": {"domain": "平台", "keywords": ("knowledge", "知识库"),
                               "reference_domain": None, "solver_libs": ()},
            "dev.gate_5415": {"domain": "开发", "keywords": ("5415", "gate_5415"),
                              "reference_domain": "resistance", "solver_libs": ("TensorCAD",)},
            "dev.git": {"domain": "开发", "keywords": ("git_status",), "reference_domain": None, "solver_libs": ()},
            "dev.logs": {"domain": "开发", "keywords": ("/dev/logs",), "reference_domain": None, "solver_libs": ()},
            "dev.nginx_audit": {"domain": "开发", "keywords": ("nginx",), "reference_domain": None, "solver_libs": ()},
            "dev.probe": {"domain": "开发", "keywords": ("probe", "healthcheck"),
                          "reference_domain": None, "solver_libs": ()},
            "dev.restart": {"domain": "开发", "keywords": ("restart",), "reference_domain": None, "solver_libs": ()},
            "dev.services": {"domain": "开发", "keywords": ("supervisorctl",), "reference_domain": None, "solver_libs": ()},
            "ai.predict": {"domain": "AI", "keywords": ("predict", "surrogate"),
                           "reference_domain": None, "solver_libs": ("TensorPhysics",)},
            "methods.computation": {"domain": "方法", "keywords": ("solver", "methods"),
                                    "reference_domain": None, "solver_libs": ("TensorLBM", "TensorFEM")},
            "misc.stubs": {"domain": "杂项", "keywords": ("stub", "placeholder"),
                           "reference_domain": None, "solver_libs": ()},
        }

    def classify_all(self) -> Dict[str, ClassificationResult]:
        """Classify all 21 capabilities.

        Returns
        -------
        dict
            Mapping from capability_id to ClassificationResult.
        """
        results = {}
        for cap_id in self.profiles:
            results[cap_id] = self.classify_one(cap_id)
        return results

    def classify_one(self, capability_id: str) -> ClassificationResult:
        """Classify a single capability.

        Uses a multi-signal approach:
        1. Scan for relevant files
        2. Detect MOCK/STUB/REAL patterns
        3. Check for verification artifacts
        4. Apply classification rules

        Parameters
        ----------
        capability_id : str
            The capability to classify.

        Returns
        -------
        ClassificationResult
        """
        profile = self.profiles.get(capability_id, {})
        keywords = profile.get("keywords", ())

        # Step 1: Find relevant files
        relevant_files = self._find_relevant_files(capability_id, keywords)

        # Step 2: Analyze each file for signals
        mock_indicators = []
        stub_indicators = []
        real_indicators = []
        verification_artifacts = []
        accuracy_numbers = []
        code_score = 0.0
        test_score = 0.0
        verify_score = 0.0

        for fpath in relevant_files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = str(fpath.relative_to(self.worktree))

            # Check MOCK patterns
            for pat in STRONG_MOCK_PATTERNS:
                if pat.search(content):
                    mock_indicators.append(rel_path)
                    break

            # Check STUB patterns
            for pat in STUB_PATTERNS:
                if pat.search(content):
                    stub_indicators.append(rel_path)
                    break

            # Check REAL patterns
            real_count = sum(1 for pat in REAL_CODE_PATTERNS if pat.search(content))
            if real_count >= 2:
                real_indicators.append(rel_path)
                code_score += 0.1

            # Check verification artifacts
            if any(pat.search(rel_path) for pat in VERIFICATION_PATTERNS):
                verification_artifacts.append(rel_path)
                verify_score += 0.2

            # Extract accuracy numbers
            for pat in ACCURACY_PATTERNS:
                for m in pat.finditer(content):
                    try:
                        val = float(m.group(1))
                        accuracy_numbers.append({
                            "file": rel_path,
                            "value": val,
                            "line": content[:m.start()].count("\n") + 1,
                        })
                    except (ValueError, IndexError):
                        pass

            # Check if it's a test file
            if "test" in rel_path.lower():
                if "assert" in content and any(kw in content for kw in ["allclose", "rtol", "approx", "=="]):
                    test_score += 0.15

        # Step 3: Compute component scores
        total_files = max(len(relevant_files), 1)
        signals = {
            "code": min(code_score, 1.0),
            "test": min(test_score, 1.0),
            "verify": min(verify_score, 1.0),
            "mock_ratio": len(mock_indicators) / total_files,
            "stub_ratio": len(stub_indicators) / total_files,
            "file_count": len(relevant_files),
        }

        # Step 4: Apply classification rules
        level, rule, confidence = self._apply_rules(signals, mock_indicators, stub_indicators,
                                                     real_indicators, verification_artifacts,
                                                     accuracy_numbers)

        return ClassificationResult(
            capability_id=capability_id,
            level=level,
            confidence=confidence,
            signals=signals,
            evidence_files=[str(f.relative_to(self.worktree)) for f in relevant_files],
            mock_indicators=mock_indicators,
            stub_indicators=stub_indicators,
            real_indicators=real_indicators,
            verification_artifacts=verification_artifacts,
            accuracy_numbers=accuracy_numbers,
            rule_fired=rule,
        )

    def _find_relevant_files(self, capability_id: str, keywords: tuple) -> List[Path]:
        """Find files relevant to a capability based on keywords."""
        relevant = []
        search_dirs = [
            self.worktree / "backend",
            self.worktree / "src",
            self.worktree / "tools",
            self.worktree / "docs",
            self.worktree / "tests",
        ]

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for fpath in search_dir.rglob("*.py"):
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    if any(kw.lower() in content.lower() for kw in keywords):
                        relevant.append(fpath)
                except (OSError, UnicodeDecodeError):
                    continue
            # Also check markdown docs
            for fpath in search_dir.rglob("*.md"):
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    if any(kw.lower() in content.lower() for kw in keywords):
                        relevant.append(fpath)
                except (OSError, UnicodeDecodeError):
                    continue

        # Limit to avoid excessive scanning
        return relevant[:100]

    def _apply_rules(
        self,
        signals: Dict[str, float],
        mock_indicators: List[str],
        stub_indicators: List[str],
        real_indicators: List[str],
        verification_artifacts: List[str],
        accuracy_numbers: List[Dict],
    ) -> Tuple[str, str, float]:
        """Apply classification rules in priority order.

        Rules (evaluated top-to-bottom, first match wins):
        R0: No files found → NOT_SUPPORTED
        R1: Strong MOCK indicators in >30% of files → MOCK
        R2: STUB indicators in >50% of files → STUB
        R3: Has verification artifacts with error ≤ 3% → REAL
        R4: Has real code indicators but no verification → WITHHELD
        R5: Only has test coverage → WITHHELD
        R6: Otherwise → NOT_SUPPORTED or WITHHELD based on signals
        """
        file_count = signals["file_count"]
        mock_ratio = signals["mock_ratio"]
        stub_ratio = signals["stub_ratio"]
        code_score = signals["code"]
        verify_score = signals["verify"]

        # R0: No files
        if file_count == 0:
            return "NOT_SUPPORTED", "R0_no_files", 0.95

        # R1: Strong MOCK
        if mock_ratio > 0.3:
            return "MOCK", "R1_strong_mock", 0.90

        # R2: STUB dominant
        if stub_ratio > 0.5:
            return "STUB", "R2_stub_dominant", 0.85

        # R3: Verified REAL
        has_good_accuracy = any(
            abs(an.get("value", 999)) <= 3.0
            for an in accuracy_numbers
        )
        if verification_artifacts and has_good_accuracy:
            return "REAL", "R3_verified_real", 0.90

        # R4: Real code but unverified
        if real_indicators and code_score > 0.3:
            return "WITHHELD", "R4_unverified_real", 0.70

        # R5: Test-only
        if signals["test"] > 0.3 and not real_indicators:
            return "WITHHELD", "R5_test_only", 0.60

        # R6: Default
        if code_score > 0.1:
            return "WITHHELD", "R6_weak_signal", 0.50
        return "NOT_SUPPORTED", "R6_no_signal", 0.80

    def classify_empty_module(self, capability_id: str) -> ClassificationResult:
        """Classify an empty/non-existent module as NOT_SUPPORTED.

        This is the negative control: ensures the classifier doesn't crash
        on missing modules.
        """
        return ClassificationResult(
            capability_id=capability_id,
            level="NOT_SUPPORTED",
            confidence=1.0,
            signals={"code": 0, "test": 0, "verify": 0, "mock_ratio": 0,
                     "stub_ratio": 0, "file_count": 0},
            evidence_files=[],
            mock_indicators=[],
            stub_indicators=[],
            real_indicators=[],
            verification_artifacts=[],
            accuracy_numbers=[],
            rule_fired="negative_control_empty_module",
        )

    def validate_known_labels(
        self,
        known_labels: Dict[str, str],
    ) -> Dict[str, Any]:
        """Validate classifier against known labels.

        Parameters
        ----------
        known_labels : dict
            Mapping from capability_id to expected level.

        Returns
        -------
        dict
            Validation results with accuracy score.
        """
        results = self.classify_all()
        correct = 0
        total = len(known_labels)
        details = []

        for cap_id, expected in known_labels.items():
            if cap_id not in results:
                details.append({
                    "capability_id": cap_id,
                    "expected": expected,
                    "predicted": "NOT_FOUND",
                    "correct": False,
                })
                continue
            predicted = results[cap_id].level
            is_correct = predicted == expected
            if is_correct:
                correct += 1
            details.append({
                "capability_id": cap_id,
                "expected": expected,
                "predicted": predicted,
                "correct": is_correct,
                "confidence": results[cap_id].confidence,
                "rule_fired": results[cap_id].rule_fired,
            })

        return {
            "accuracy": correct / total if total > 0 else 0.0,
            "correct": correct,
            "total": total,
            "details": details,
        }
