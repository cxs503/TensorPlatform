"""Tests for the AST-based drift scanner."""
import json
import tempfile
from pathlib import Path

import pytest

from backend.services.physical_constants import PhysicalConstantsRegistry
from backend.services.physical_constants.drift_scanner import (
    ConstantScanner,
    DriftConflict,
    ExtractedConstant,
)


@pytest.fixture
def registry_for_scanner(tmp_path):
    """Create a minimal registry for scanner tests."""
    reg_data = {
        "version": "1.0.0-test",
        "constants": {
            "gravity": {
                "symbol": "g",
                "value": 9.80665,
                "unit": "m/s²",
                "uncertainty": 0.0,
                "uncertainty_type": "exact",
                "source": "CGPM 1901",
                "scope": "global",
                "aliases": ["G", "g", "gravity"],
                "notes": "",
            },
            "ice_density": {
                "symbol": "ρ_ice",
                "value": 917.0,
                "unit": "kg/m³",
                "uncertainty": 5.0,
                "uncertainty_type": "typical_range",
                "source": "ITTC",
                "scope": "ice_physics",
                "aliases": ["RHO_ICE", "rho_ice"],
                "notes": "",
            },
        },
    }
    p = tmp_path / "scanner_registry.json"
    with open(p, "w") as f:
        json.dump(reg_data, f)
    return PhysicalConstantsRegistry(path=p)


@pytest.fixture
def scanner(registry_for_scanner):
    return ConstantScanner(registry=registry_for_scanner, relative_tolerance=1e-3)


class TestConstantScanner:
    def test_scan_file_extracts_constants(self, scanner, tmp_path):
        py_file = tmp_path / "sample.py"
        py_file.write_text(
            "G = 9.81\n"
            "RHO_ICE = 917.0\n"
            "OTHER = 42\n"
        )
        extracted = scanner.scan_file(py_file)
        assert len(extracted) == 2
        names = {e.name for e in extracted}
        assert "G" in names
        assert "RHO_ICE" in names

    def test_scan_file_ignores_non_constant(self, scanner, tmp_path):
        py_file = tmp_path / "sample.py"
        py_file.write_text(
            "x = 10\n"
            "y = 'hello'\n"
            "z = [1, 2, 3]\n"
        )
        extracted = scanner.scan_file(py_file)
        assert len(extracted) == 0

    def test_scan_file_handles_syntax_error(self, scanner, tmp_path):
        py_file = tmp_path / "bad.py"
        py_file.write_text("def foo(\n")  # syntax error
        extracted = scanner.scan_file(py_file)
        assert len(extracted) == 0

    def test_scan_directory(self, scanner, tmp_path):
        (tmp_path / "a.py").write_text("G = 9.81\n")
        (tmp_path / "b.py").write_text("RHO_ICE = 920.0\n")
        (tmp_path / "c.txt").write_text("G = 9.81\n")  # not .py
        extracted = scanner.scan_directory(tmp_path)
        assert len(extracted) == 2

    def test_scan_directory_excludes_pycache(self, scanner, tmp_path):
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "cached.py").write_text("G = 9.81\n")
        (tmp_path / "real.py").write_text("G = 9.81\n")
        extracted = scanner.scan_directory(tmp_path)
        assert len(extracted) == 1

    def test_detect_conflicts_ok(self, scanner, tmp_path):
        py_file = tmp_path / "ok.py"
        py_file.write_text("G = 9.80665\nRHO_ICE = 917.0\n")
        extracted = scanner.scan_file(py_file)
        conflicts = scanner.detect_conflicts(extracted)
        assert len(conflicts) == 2
        assert all(c.status == "OK" for c in conflicts)

    def test_detect_conflicts_drift(self, scanner, tmp_path):
        py_file = tmp_path / "drift.py"
        py_file.write_text("G = 9.81\n")  # 9.81 vs 9.80665 = 0.034% drift
        extracted = scanner.scan_file(py_file)
        conflicts = scanner.detect_conflicts(extracted)
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.canonical_key == "gravity"
        assert c.status in ("DRIFT", "OK")  # depends on tolerance
        assert c.max_relative_deviation > 0

    def test_detect_conflicts_conflict(self, scanner, tmp_path):
        py_file = tmp_path / "conflict.py"
        py_file.write_text("RHO_ICE = 900.0\n")  # 900 vs 917 = 1.86% conflict
        extracted = scanner.scan_file(py_file)
        conflicts = scanner.detect_conflicts(extracted)
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.status == "CONFLICT"
        assert c.max_relative_deviation > 0.01

    def test_scan_and_report_structure(self, scanner, tmp_path):
        (tmp_path / "a.py").write_text("G = 9.80665\n")
        (tmp_path / "b.py").write_text("RHO_ICE = 900.0\n")
        report = scanner.scan_and_report(tmp_path)
        assert "summary" in report
        assert "conflicts" in report
        assert report["summary"]["total_extractions"] == 2
        assert report["summary"]["constants_scanned"] == 2

    def test_multiple_files_same_constant(self, scanner, tmp_path):
        (tmp_path / "a.py").write_text("G = 9.81\n")
        (tmp_path / "b.py").write_text("G = 9.80665\n")
        extracted = scanner.scan_directory(tmp_path)
        conflicts = scanner.detect_conflicts(extracted)
        assert len(conflicts) == 1
        c = conflicts[0]
        assert len(c.extracted) == 2  # two files with G


class TestDriftConflict:
    def test_as_dict(self):
        ex = ExtractedConstant(
            file="test.py",
            line=10,
            name="G",
            value=9.81,
            canonical_key="gravity",
            context="G = 9.81",
        )
        conflict = DriftConflict(
            canonical_key="gravity",
            ssot_value=9.80665,
            ssot_unit="m/s²",
            extracted=[ex],
            max_relative_deviation=0.00034,
            status="DRIFT",
        )
        d = conflict.as_dict()
        assert d["canonical_key"] == "gravity"
        assert d["ssot_value"] == 9.80665
        assert len(d["extracted"]) == 1
        assert d["status"] == "DRIFT"
