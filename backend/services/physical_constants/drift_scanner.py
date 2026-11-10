"""AST-based drift scanner for physical constants.

Scans Python source files for assignments to known constant names (aliases
from the SSOT registry) and reports conflicts when values diverge beyond
tolerance. This is the mechanism that turns the ice-density 917/900/920 and
gravity 9.81/9.80665分歧 into detectable, actionable drift.

Domain-free: the scanner knows nothing about ice or gravity; it only knows
the alias→canonical mapping from the registry.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import PhysicalConstantsRegistry, get_registry


@dataclass
class ExtractedConstant:
    """One constant assignment found by the AST scanner."""
    file: str
    line: int
    name: str
    value: float
    canonical_key: str
    context: str  # surrounding code snippet for audit

    def as_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "name": self.name,
            "value": self.value,
            "canonical_key": self.canonical_key,
            "context": self.context,
        }


@dataclass
class DriftConflict:
    """A detected conflict between extracted values and the SSOT registry."""
    canonical_key: str
    ssot_value: float
    ssot_unit: str
    extracted: List[ExtractedConstant]
    max_relative_deviation: float
    status: str  # "OK", "DRIFT", "CONFLICT"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "canonical_key": self.canonical_key,
            "ssot_value": self.ssot_value,
            "ssot_unit": self.ssot_unit,
            "extracted": [e.as_dict() for e in self.extracted],
            "max_relative_deviation": self.max_relative_deviation,
            "status": self.status,
        }


class ConstantScanner:
    """AST-based scanner for physical constant assignments.

    Parameters
    ----------
    registry : PhysicalConstantsRegistry or None
        The SSOT registry to compare against. Defaults to the global singleton.
    relative_tolerance : float
        Maximum allowed relative deviation before flagging as DRIFT.
    """

    def __init__(
        self,
        registry: Optional[PhysicalConstantsRegistry] = None,
        relative_tolerance: float = 1e-3,
    ) -> None:
        self._registry = registry or get_registry()
        self._tol = float(relative_tolerance)
        # Build alias → canonical key map
        self._alias_map: Dict[str, str] = {}
        for const in self._registry.all_constants():
            for alias in const.aliases:
                self._alias_map[alias] = const.key

    def scan_file(self, path: Path) -> List[ExtractedConstant]:
        """Extract constant assignments from a single Python file."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                source = fh.read()
        except (OSError, IOError):
            return []
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return []
        lines = source.splitlines()
        extracted: List[ExtractedConstant] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in self._alias_map:
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                            name = target.id
                            value = float(node.value.value)
                            line_no = node.lineno
                            context = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
                            extracted.append(ExtractedConstant(
                                file=str(path),
                                line=line_no,
                                name=name,
                                value=value,
                                canonical_key=self._alias_map[name],
                                context=context,
                            ))
        return extracted

    def scan_directory(
        self,
        root: Path,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[ExtractedConstant]:
        """Recursively scan a directory tree for constant assignments."""
        root = Path(root)
        if not root.is_dir():
            return []
        exclude_patterns = exclude_patterns or ["__pycache__", ".git", "node_modules"]
        all_extracted: List[ExtractedConstant] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Filter out excluded directories
            dirnames[:] = [d for d in dirnames if d not in exclude_patterns]
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                fpath = Path(dirpath) / fname
                # Check include patterns (if any)
                if include_patterns:
                    rel = fpath.relative_to(root)
                    if not any(rel.match(pat) for pat in include_patterns):
                        continue
                all_extracted.extend(self.scan_file(fpath))
        return all_extracted

    def detect_conflicts(self, extracted: List[ExtractedConstant]) -> List[DriftConflict]:
        """Compare extracted values against the SSOT registry and report conflicts."""
        # Group by canonical key
        by_key: Dict[str, List[ExtractedConstant]] = {}
        for ex in extracted:
            by_key.setdefault(ex.canonical_key, []).append(ex)
        conflicts: List[DriftConflict] = []
        for key, ext_list in sorted(by_key.items()):
            const = self._registry.get(key)
            ssot_val = const.value
            max_dev = 0.0
            for ex in ext_list:
                if ssot_val != 0:
                    dev = abs(ex.value - ssot_val) / abs(ssot_val)
                else:
                    dev = abs(ex.value - ssot_val)
                max_dev = max(max_dev, dev)
            if max_dev > self._tol:
                status = "CONFLICT" if max_dev > 0.01 else "DRIFT"
            else:
                status = "OK"
            conflicts.append(DriftConflict(
                canonical_key=key,
                ssot_value=ssot_val,
                ssot_unit=const.unit,
                extracted=ext_list,
                max_relative_deviation=max_dev,
                status=status,
            ))
        return conflicts

    def scan_and_report(
        self,
        root: Path,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Full scan + conflict detection, returning a structured report."""
        extracted = self.scan_directory(root, include_patterns, exclude_patterns)
        conflicts = self.detect_conflicts(extracted)
        n_ok = sum(1 for c in conflicts if c.status == "OK")
        n_drift = sum(1 for c in conflicts if c.status == "DRIFT")
        n_conflict = sum(1 for c in conflicts if c.status == "CONFLICT")
        return {
            "summary": {
                "total_extractions": len(extracted),
                "constants_scanned": len(conflicts),
                "ok": n_ok,
                "drift": n_drift,
                "conflict": n_conflict,
                "pass": n_conflict == 0 and n_drift == 0,
            },
            "conflicts": [c.as_dict() for c in conflicts],
        }


__all__ = [
    "ConstantScanner",
    "DriftConflict",
    "ExtractedConstant",
]
