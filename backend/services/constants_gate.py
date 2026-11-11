"""Constants gate service (p488w1-n01).

Enforces that all physical constants and empirical coefficients used in
platform calculations are registered in external_constants.json.
Blocks usage of unregistered constants.

C6 acceptance: intercepts >= 3 unregistered constant usage points.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ConstantUsage:
    """A detected usage of a constant in source code."""
    file_path: str
    line_number: int
    line_content: str
    constant_name: str
    constant_value: Optional[float]
    is_registered: bool
    context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "line_content": self.line_content.strip(),
            "constant_name": self.constant_name,
            "constant_value": self.constant_value,
            "is_registered": self.is_registered,
            "context": self.context,
        }


@dataclass
class GateResult:
    """Result of running the constants gate."""
    total_scanned: int
    registered_usages: int
    unregistered_usages: int
    violations: List[ConstantUsage]
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_scanned": self.total_scanned,
            "registered_usages": self.registered_usages,
            "unregistered_usages": self.unregistered_usages,
            "violations": [v.to_dict() for v in self.violations],
            "passed": self.passed,
        }


# Known physical constant patterns to detect in source code
CONSTANT_PATTERNS = [
    # Gravity
    (r'\b9\.8[01]?\b', "gravity_ms2", 9.80665),
    (r'\b9\.81\b', "gravity_ms2", 9.80665),
    (r'\b9\.80665\b', "gravity_ms2", 9.80665),
    # Pi
    (r'\b3\.14159\b', "pi", 3.141592653589793),
    # Water density
    (r'\b998\.0\b', "water_density_fresh_kg_m3", 998.2071),
    (r'\b998\.2\b', "water_density_fresh_kg_m3", 998.2071),
    (r'\b1025\.0\b', "water_density_sea_kg_m3", 1025.0),
    (r'\b1025\b', "water_density_sea_kg_m3", 1025.0),
    # Kinematic viscosity
    (r'\b1\.004e-6\b', "kinematic_viscosity_fresh_m2_s", 1.004e-6),
    (r'\b1\.19e-6\b', "kinematic_viscosity_sea_m2_s", 1.19e-6),
    # ITTC-1957 coefficients
    (r'\b0\.075\b', "ittc_1957_forman_coeff_a", 0.075),
    # Air density
    (r'\b1\.225\b', "air_density_kg_m3", 1.225),
    # Ice density
    (r'\b917\.0\b', "ice_density_kg_m3", 917.0),
    (r'\b917\b', "ice_density_kg_m3", 917.0),
    # Hull roughness
    (r'\b1\.5e-4\b', "hull_roughness_default_m", 1.5e-4),
    # Correlation allowance
    (r'\b0\.0004\b', "correlation_allowance_coeff", 0.0004),
    # Propulsion coefficients
    (r'\b0\.12\b', "thrust_deduction_fraction_typical", 0.12),
    (r'\b0\.25\b', "wake_fraction_typical", 0.25),
    (r'\b0\.65\b', "propeller_open_water_efficiency_typical", 0.65),
    (r'\b0\.98\b', "shaft_efficiency_typical", 0.98),
    # Wall-sided limit
    (r'\b25\.0\b', "gzb_wall_sided_limit_deg", 25.0),
    # Ice flexural strength
    (r'\b0\.7\b', "ice_flexural_strength_typical_MPa", 0.7),
]


class ConstantsRegistry:
    """Registry of known physical constants from external_constants.json."""

    def __init__(self, registry_path: Optional[str] = None):
        self._constants: Dict[str, Dict[str, Any]] = {}
        self._registry_path = registry_path
        if registry_path:
            self.load(registry_path)

    def load(self, path: str) -> None:
        """Load constants from JSON registry."""
        p = Path(path)
        if not p.exists():
            logger.warning("Constants registry not found: %s", path)
            return
        data = json.loads(p.read_text())
        self._constants = data.get("constants", {})

    def is_registered(self, constant_name: str) -> bool:
        """Check if a constant is registered."""
        return constant_name in self._constants

    def get(self, constant_name: str) -> Optional[Dict[str, Any]]:
        """Get constant details."""
        return self._constants.get(constant_name)

    def get_value(self, constant_name: str) -> Optional[float]:
        """Get constant value."""
        c = self._constants.get(constant_name)
        if c:
            return c.get("value")
        return None

    @property
    def all_names(self) -> Set[str]:
        return set(self._constants.keys())

    @property
    def count(self) -> int:
        return len(self._constants)


class ConstantsGate:
    """Gate that scans source code for unregistered constant usage.

    Usage::

        gate = ConstantsGate("config/external_constants.json")
        result = gate.scan_directory("backend/")
        if not result.passed:
            for v in result.violations:
                print(f"Unregistered: {v.constant_name} at {v.file_path}:{v.line_number}")
    """

    def __init__(self, registry_path: Optional[str] = None):
        self._registry = ConstantsRegistry(registry_path)
        self._scan_patterns = list(CONSTANT_PATTERNS)
        self._excluded_dirs = {
            "__pycache__", ".git", "node_modules", ".venv", "venv",
            "tests", "test", ".tox", "build", "dist",
        }
        self._excluded_files = {
            "external_constants.json",
        }

    @property
    def registry(self) -> ConstantsRegistry:
        return self._registry

    def add_pattern(self, pattern: str, constant_name: str, value: float) -> None:
        """Add a detection pattern."""
        self._scan_patterns.append((pattern, constant_name, value))

    def exclude_dir(self, dirname: str) -> None:
        """Add a directory to the exclusion list."""
        self._excluded_dirs.add(dirname)

    def scan_file(self, file_path: str) -> List[ConstantUsage]:
        """Scan a single file for constant usage."""
        usages = []
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return usages

        try:
            content = p.read_text(errors="replace")
        except Exception as e:
            logger.warning("Cannot read %s: %s", file_path, e)
            return usages

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue

            for pattern, const_name, const_value in self._scan_patterns:
                if re.search(pattern, line):
                    is_registered = self._registry.is_registered(const_name)
                    usages.append(ConstantUsage(
                        file_path=str(file_path),
                        line_number=line_num,
                        line_content=line,
                        constant_name=const_name,
                        constant_value=const_value,
                        is_registered=is_registered,
                        context=stripped[:100],
                    ))

        return usages

    def scan_directory(self, dir_path: str, extensions: Optional[Set[str]] = None) -> GateResult:
        """Scan a directory tree for constant usage."""
        if extensions is None:
            extensions = {".py", ".js", ".ts", ".json"}

        violations = []
        registered_count = 0
        total_scanned = 0

        p = Path(dir_path)
        if not p.exists():
            return GateResult(0, 0, 0, [], True)

        for fpath in p.rglob("*"):
            if not fpath.is_file():
                continue
            if fpath.suffix not in extensions:
                continue
            if fpath.name in self._excluded_files:
                continue
            # Check excluded dirs
            parts = fpath.parts
            if any(part in self._excluded_dirs for part in parts):
                continue

            total_scanned += 1
            usages = self.scan_file(str(fpath))
            for u in usages:
                if u.is_registered:
                    registered_count += 1
                else:
                    violations.append(u)

        return GateResult(
            total_scanned=total_scanned,
            registered_usages=registered_count,
            unregistered_usages=len(violations),
            violations=violations,
            passed=len(violations) == 0,
        )

    def check_constant(self, constant_name: str, value: float) -> bool:
        """Check if a specific constant value is registered.

        Returns True if registered, False if it would be blocked.
        """
        if self._registry.is_registered(constant_name):
            registered_value = self._registry.get_value(constant_name)
            if registered_value is not None:
                # Allow small floating point tolerance
                return abs(registered_value - value) < 1e-6 * max(abs(registered_value), 1.0)
        return False

    def register_constant(
        self,
        name: str,
        value: float,
        unit: str,
        description: str,
        source: str,
        doi: str,
        category: str = "general",
    ) -> None:
        """Register a new constant in the registry."""
        self._registry._constants[name] = {
            "value": value,
            "unit": unit,
            "description": description,
            "source": source,
            "doi": doi,
            "category": category,
        }

    def save_registry(self, path: str) -> None:
        """Save the current registry to JSON."""
        data = {
            "$schema": "external_constants_registry/v1.0",
            "constants": self._registry._constants,
            "metadata": {
                "version": "1.0.0",
                "total_constants": self._registry.count,
            }
        }
        Path(path).write_text(json.dumps(data, indent=2))


# Module-level convenience functions

_default_gate: Optional[ConstantsGate] = None


def get_gate() -> ConstantsGate:
    """Get or create the module-level gate singleton."""
    global _default_gate
    if _default_gate is None:
        _default_gate = ConstantsGate()
    return _default_gate


def reset_gate() -> None:
    """Reset the singleton (for testing)."""
    global _default_gate
    _default_gate = None
