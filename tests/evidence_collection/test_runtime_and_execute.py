"""判据求值 / 参考误差复算 / 测试执行器 的单元测试（全部离线，合成数据）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.evidence_collection import execute, reference, runtime


def _samples(payloads, status=200):
    return [{"status": status, "response": p, "error": None} for p in payloads]


def test_status_criterion_pass_and_fail():
    crit = [{"id": "s", "kind": "status", "expect": [200], "class": "contract"}]
    assert runtime.evaluate_criteria(crit, _samples([{}], 200), None)[0]["verdict"] == runtime.PASS
    assert runtime.evaluate_criteria(crit, _samples([{}], 500), None)[0]["verdict"] == runtime.FAIL


def test_unexecuted_probe_is_not_a_pass():
    crit = [{"id": "s", "kind": "status", "expect": [200], "class": "contract"}]
    rows = runtime.evaluate_criteria(crit, [{"status": None, "response": None}], None)
    assert rows[0]["verdict"] == runtime.NOT_EXECUTED


def test_field_range_missing_field_fails_and_root_list_len_passes():
    rows = runtime.evaluate_criteria(
        [
            {"id": "r", "kind": "field_range", "path": "x", "min": 0.0, "max": 1.0, "class": "contract"},
            {"id": "n", "kind": "list_min_len", "path": "", "min_len": 2, "class": "contract"},
        ],
        _samples([[{"k": 1}, {"k": 2}]]), None)
    assert rows[0]["verdict"] == runtime.FAIL   # 数值字段缺失 → 判据失败（不当通过）
    assert rows[1]["verdict"] == runtime.PASS   # 根路径长度 2 ≥ 2


def test_field_range_out_of_bounds_fails():
    rows = runtime.evaluate_criteria(
        [{"id": "r", "kind": "field_range", "path": "x", "min": 1.0, "max": 2.0, "class": "contract"}],
        _samples([{"x": 5.0}]), None)
    assert rows[0]["verdict"] == runtime.FAIL


def test_list_min_len_counts_items():
    rows = runtime.evaluate_criteria(
        [{"id": "n", "kind": "list_min_len", "path": "items", "min_len": 3, "class": "contract"}],
        _samples([{"items": [1, 2]}]), None)
    assert rows[0]["verdict"] == runtime.FAIL


def test_identity_lte_detects_physical_bound_violation():
    crit = [{"id": "b", "kind": "identity", "lhs": "y", "rhs": "x", "mode": "lte",
             "tolerance": 0.0, "class": "invariant"}]
    ok = runtime.evaluate_criteria(crit, _samples([{"x": 2.0, "y": 1.0}]), None)[0]
    bad = runtime.evaluate_criteria(crit, _samples([{"x": 1.0, "y": 2.0}]), None)[0]
    assert ok["verdict"] == runtime.PASS
    assert bad["verdict"] == runtime.FAIL


def test_monotonic_and_constant_detection():
    body = {"J": None, "KT": None}
    crit = [
        {"id": "m", "kind": "monotonic", "path": "KT", "direction": "decreasing", "class": "invariant"},
        {"id": "n", "kind": "non_constant", "path": "KT", "min_rel_spread": 1e-2, "class": "invariant"},
    ]
    good = runtime.evaluate_criteria(crit, _samples([{"KT": 0.4}, {"KT": 0.2}]), None)
    flat = runtime.evaluate_criteria(crit, _samples([{"KT": 0.4}, {"KT": 0.4}]), None)
    assert [r["verdict"] for r in good] == [runtime.PASS, runtime.PASS]
    assert [r["verdict"] for r in flat] == [runtime.FAIL, runtime.FAIL]
    del body


def test_surface_check_present_and_absent():
    ok = runtime.surface_check(["/a"], ["/a", "/b"])
    miss = runtime.surface_check(["/zzz"], ["/a"])
    assert ok["status"] == runtime.PASS and ok["present"][0]["matches"] == ["/a"]
    assert miss["status"] == runtime.FAIL and miss["absent"][0]["pattern"] == "/zzz"


def test_reference_recompute_detects_hand_copied_error(tmp_path: Path):
    art = tmp_path / "art.json"
    art.write_text(json.dumps({"rows": [
        {"c": 0.0064, "r": 0.0032, "err": 5.0},
        {"c": 0.0043, "r": 0.0045, "err": 4.44},
    ]}))
    entry = {"path": str(art), "value_path": "rows.*.err", "aggregate": "worst",
             "tolerance_pct": 3.0,
             "recompute": {"computed_path": "rows.*.c", "reference_path": "rows.*.r",
                           "declared_error_path": "rows.*.err", "declaration_tolerance_pct": 0.01}}
    res = reference.evaluate_reference(entry)
    assert res["status"] == reference.FAIL
    assert res["declaration_mismatch"] is True
    assert res["worst_pct"] > 3.0


def test_reference_recompute_clean_artifact_passes(tmp_path: Path):
    art = tmp_path / "art.json"
    art.write_text(json.dumps({"rows": [
        {"c": 0.0031, "r": 0.0031, "err": 0.0},
        {"c": 0.0045, "r": 0.0045, "err": 0.0},
    ]}))
    entry = {"path": str(art), "value_path": "rows.*.err", "aggregate": "worst",
             "tolerance_pct": 3.0,
             "recompute": {"computed_path": "rows.*.c", "reference_path": "rows.*.r",
                           "declared_error_path": "rows.*.err"}}
    res = reference.evaluate_reference(entry)
    assert res["status"] == reference.PASS and res["worst_pct"] == 0.0


def test_reference_missing_artifact_is_missing_not_pass(tmp_path: Path):
    entry = {"path": str(tmp_path / "nope.json"), "value_path": "a", "tolerance_pct": 3.0}
    assert reference.evaluate_reference(entry)["status"] == "MISSING"


def test_reference_wildcard_nested_path(tmp_path: Path):
    art = tmp_path / "art.json"
    art.write_text(json.dumps({"cases": [{"e": {"canon": 0.5}}, {"e": {"canon": 2.5}}]}))
    entry = {"path": str(art), "value_path": "cases.*.e.canon", "aggregate": "worst",
             "tolerance_pct": 3.0}
    res = reference.evaluate_reference(entry)
    assert res["worst_pct"] == pytest.approx(2.5) and res["status"] == reference.PASS


def test_execute_missing_module_is_not_success():
    res = execute.run_tests(["tests/evidence_collection/definitely_absent.py"])
    assert res["status"] == "MISSING" and res["totals"]["passed"] == 0


def test_execute_real_module_collects_counts():
    res = execute.run_tests(["tests/evidence_collection/test_spec_and_expr.py"])
    assert res["status"] in {"PASSED", "FAILED"}
    assert res["totals"]["tests"] > 0
    assert res["modules_digest"] and len(res["modules_digest"]) == 64