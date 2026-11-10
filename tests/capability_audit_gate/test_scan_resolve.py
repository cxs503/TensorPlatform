"""扫描器 / 路径解析的单元测试（用 tmp_path 合成 fixture，不依赖仓内数据）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.capability_audit_gate import resolve, scan, spec as spec_mod

THRESHOLDS = dict(spec_mod.DEFAULT_THRESHOLDS)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_impl_flags_empty_shell_functions(tmp_path):
    src = _write(tmp_path / "impl.py", "def a():\n    raise NotImplementedError\n\n"
                                       "def b(x):\n    pass\n\n"
                                       "def c(x):\n    return x * 2\n")
    sig = scan.scan_impl(src)
    assert sig["functions"] == 3
    assert sig["stub_functions"] == 2
    assert sig["stub_functions"] / sig["functions"] > 0.5


def test_scan_impl_detects_unseeded_random_and_mock_markers(tmp_path):
    src = _write(tmp_path / "impl.py",
                 "import random\n\n\ndef f(x):\n    # MOCK: hardcoded answer\n"
                 "    return 1.0\n\n\ndef g():\n    # placeholder sampling\n"
                 "    return random.random()\n")
    sig = scan.scan_impl(src)
    assert sig["random_calls"] == 1
    assert sig["marker_hits"] >= 2


def test_scan_impl_detects_constant_returns(tmp_path):
    src = _write(tmp_path / "impl.py",
                 "def a(x):\n    return 1.0\n\n\ndef b(x):\n    return 2.0\n\n\n"
                 "def c(x):\n    y = x * 3\n    return y\n")
    sig = scan.scan_impl(src)
    assert sig["constant_returns"] == 2
    assert sig["functions"] == 3


def test_scan_impl_marks_parse_error_instead_of_raising(tmp_path):
    src = _write(tmp_path / "impl.py", "def broken(:\n    return 1\n")
    sig = scan.scan_impl(src)
    assert sig["parse_error"] is True


def test_scan_tests_separates_numeric_from_skeleton_assertions(tmp_path):
    src = _write(tmp_path / "test_x.py",
                 "import pytest\n\n\ndef test_numeric():\n    assert 1.0 + 1.0 == pytest.approx(2.0)\n\n\n"
                 "def test_placeholder():\n    assert True\n")
    sig = scan.scan_tests(src)
    assert sig["test_names"] == ["test_numeric", "test_placeholder"]
    assert sig["numeric_assertions"] == 1
    assert sig["skeleton_assertions"] == 1


def test_benchmark_json_cases_computes_error_from_values(tmp_path):
    _write(tmp_path / "bench.json", json.dumps(
        {"cases": [{"case_id": "a", "computed": 1.02, "reference": 1.0},
                   {"case_id": "b", "computed": 1.10, "reference": 1.0}]}))
    payload = scan.scan_benchmark({"kind": "json_cases", "path": "bench.json",
                                   "cases_path": "cases",
                                   "computed_fields": ["computed"],
                                   "reference_fields": ["reference"],
                                   "check_class": "experimental", "tolerance_pct": 3.0},
                                  tmp_path)
    assert [r["error_pct"] for r in payload["rows"]] == pytest.approx([2.0, 10.0])
    assert payload["worst_error_pct"] == pytest.approx(10.0)
    assert payload["status"] == "OVER_TOLERANCE"
    assert payload["sha256"]


def test_benchmark_missing_artifact_is_reported_not_silently_passed(tmp_path):
    payload = scan.scan_benchmark({"kind": "json_cases", "path": "absent.json",
                                   "check_class": "analytic"}, tmp_path)
    assert payload["status"] == "MISSING"
    assert payload["worst_error_pct"] is None


def test_benchmark_json_values_percent_unit_reads_worst(tmp_path):
    _write(tmp_path / "c.json", json.dumps({"cases": [{"err": {"x": 0.2, "y": 1.1}}]}))
    payload = scan.scan_benchmark({"kind": "json_values", "path": "c.json",
                                   "values": ["cases[*].err.x", "cases[*].err.y"],
                                   "unit": "pct", "check_class": "analytic",
                                   "tolerance_pct": 3.0}, tmp_path)
    assert payload["worst_error_pct"] == pytest.approx(1.1)
    assert payload["status"] == "PASS"


def test_resolve_uses_token_boundaries_not_arbitrary_substring(tmp_path):
    for rel in ("tools/stats_core.py", "tools/statistics_core.py", "tools/aggregator.py"):
        _write(tmp_path / rel, "def f():\n    return 1\n")
    index = resolve.FileIndex(tmp_path, code_roots=("tools",), test_roots=())
    cap = {"id": "c", "bindings": {}, "patterns": {"code": ["stats"], "tests": []}}
    got = resolve.resolve_capability(cap, index, THRESHOLDS)
    assert got["source"] == "PATTERN"
    assert "tools/stats_core.py" in got["code"]
    assert "tools/statistics_core.py" not in got["code"]   # 子串命中不算证据
    assert "tools/aggregator.py" not in got["code"]


def test_resolve_prefers_explicit_bindings_and_reports_missing(tmp_path):
    _write(tmp_path / "tools/a.py", "def f():\n    return 1\n")
    index = resolve.FileIndex(tmp_path, code_roots=("tools",), test_roots=())
    cap = {"id": "c", "patterns": {"code": ["a"]},
           "bindings": {"code": ["tools/a.py", "tools/gone.py"]}}
    got = resolve.resolve_capability(cap, index, THRESHOLDS)
    assert got["source"] == "BINDING"
    assert got["code"] == ["tools/a.py"]
    assert got["missing"] == ["tools/gone.py"]


def test_resolve_unbound_when_nothing_matches(tmp_path):
    index = resolve.FileIndex(tmp_path, code_roots=("tools",), test_roots=())
    got = resolve.resolve_capability({"id": "c", "patterns": {"code": ["nothing"]}},
                                     index, THRESHOLDS)
    assert got["source"] == "UNBOUND"
    assert got["code"] == [] and got["tests"] == []