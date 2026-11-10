"""判定级联与金标准语料的一致率测试（10 项五级均衡标注，真值由构造给定）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.capability_audit_gate import engine, spec as spec_mod

GOLDEN = REPO / "config" / "capability_audit_gate" / "golden"


@pytest.fixture(scope="module")
def golden_report():
    spec = spec_mod.load_spec(GOLDEN / "spec.json")
    return engine.run_audit(spec, root=GOLDEN)


def _verdict(report, cid):
    return next(v for v in report["capabilities"] if v["capability"] == cid)


def test_golden_corpus_is_labelled_and_balanced(golden_report):
    labels = [v["expected_level"] for v in golden_report["capabilities"]]
    assert len(labels) == 10
    assert {lv: labels.count(lv) for lv in set(labels)} == {
        "REAL": 2, "MOCK": 3, "STUB": 2, "WITHHELD": 2, "NOT_SUPPORTED": 1}


def test_golden_agreement_reaches_predeclared_90pct(golden_report):
    v = golden_report["validation"]
    assert v["labeled"] == 10
    assert v["agreement"] >= 0.9, v["disagreements"]
    assert v["disagreements"] == []


def test_golden_beats_majority_class_baseline(golden_report):
    v = golden_report["validation"]
    assert v["majority_level"] == "MOCK"          # 3/10 —— 多数类基线只有 30%
    assert v["agreement_gain"] >= 0.5


def test_golden_coverage_is_complete(golden_report):
    assert golden_report["summary"]["coverage"] == 1.0
    assert golden_report["summary"]["audited"] == 10


@pytest.mark.parametrize("cid,expected", [
    ("alpha.compute", "REAL"),
    ("beta.solve", "REAL"),
    ("gamma.estimate", "MOCK"),
    ("delta.infer", "MOCK"),
    ("lambda.mesh", "MOCK"),
    ("epsilon.transform", "STUB"),
    ("zeta.utility", "STUB"),
    ("eta.blend", "WITHHELD"),
    ("theta.merge", "WITHHELD"),
    ("iota.registry", "NOT_SUPPORTED"),
])
def test_each_golden_capability_level(golden_report, cid, expected):
    v = _verdict(golden_report, cid)
    assert v["level"] == expected, v["reasons"]


def test_analytic_benchmark_violation_is_mock_not_withheld(golden_report):
    v = _verdict(golden_report, "lambda.mesh")
    assert v["level"] == "MOCK"
    assert "PHYSICS_INVARIANT_VIOLATED" in v["reasons"]
    assert v["evidence"]["benchmark"]["check_class"] == "analytic"


def test_experimental_benchmark_over_tolerance_is_withheld(golden_report):
    """同一越界在实验型判据下只否决 REAL（证据不足），不指控造假。"""
    v = _verdict(golden_report, "theta.merge")
    assert v["level"] == "WITHHELD"
    assert "BELOW_REAL_TOLERANCE" in v["reasons"] or "TESTS_NON_NUMERIC" in v["reasons"]


def test_small_file_is_warning_not_stub(golden_report):
    """find#1：5 行含造假指纹的文件是 MOCK，不是 STUB（尺寸只作弱信号）。"""
    v = _verdict(golden_report, "gamma.estimate")
    assert v["level"] == "MOCK"
    assert not any(r == "LOW_SLOC" for r in v["reasons"])


def test_mock_requires_two_independent_signal_families(tmp_path):
    """单信号（仅未播种随机）不得判 MOCK：降噪纪律。"""
    root = tmp_path
    (root / "tools").mkdir()
    (root / "tools" / "single.py").write_text(
        "import random\n\n\ndef f(x):\n    return x + random.random()\n", encoding="utf-8")
    spec = spec_mod.validate_spec({
        "schema_version": "1.0",
        "capabilities": [{"id": "one.signal", "bindings": {"code": ["tools/single.py"]},
                          "patterns": {}, "benchmark": None}],
    })
    report = engine.run_audit(spec, root=root)
    v = report["capabilities"][0]
    assert v["level"] == "WITHHELD"
    assert "UNSEEDED_RANDOM" in v["warnings"]
    assert "UNSEEDED_RANDOM" not in v["reasons"]


def test_pattern_only_refutation_is_suppressed_by_default_then_enabled(tmp_path):
    root = tmp_path
    (root / "tools").mkdir()
    (root / "tools" / "fake_module.py").write_text(
        "import random\n\n\ndef g(x):\n    # MOCK: hardcoded\n    # hardcoded answer\n"
        "    return 1.0\n\n\ndef h():\n    return random.random()\n", encoding="utf-8")
    base = {"schema_version": "1.0",
            "capabilities": [{"id": "pat.only", "bindings": {},
                              "patterns": {"code": ["module"], "tests": []},
                              "benchmark": None}]}
    suppressed = engine.run_audit(spec_mod.validate_spec(base), root=root)["capabilities"][0]
    assert suppressed["level"] == "WITHHELD"
    assert suppressed["pattern_refutation_suppressed"]["would_be_level"] == "MOCK"

    base["thresholds"] = {"pattern_can_refute": True}
    enabled = engine.run_audit(spec_mod.validate_spec(base), root=root)["capabilities"][0]
    assert enabled["level"] == "MOCK"


def test_missing_binding_target_is_not_supported_but_unbound_is_withheld(golden_report, tmp_path):
    assert _verdict(golden_report, "iota.registry")["level"] == "NOT_SUPPORTED"
    spec = spec_mod.validate_spec({
        "schema_version": "1.0",
        "capabilities": [{"id": "a.b", "bindings": {}, "patterns": {"code": ["no_such_thing"]}}],
    })
    report = engine.run_audit(spec, root=tmp_path)
    assert report["capabilities"][0]["level"] == "WITHHELD"
    assert "EVIDENCE_NOT_BOUND" in report["capabilities"][0]["reasons"]


def test_report_records_the_rule_set_version(golden_report):
    assert golden_report["ruleset_version"].startswith("cag-rules-")
    assert golden_report["inputs"]["thresholds"]["real_tolerance_pct"] == 3.0