"""证据等级判定级联的单测：逐条分支 + NaN/None 安全。"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from tools.capability_semantics.evidence import (REASON_TEXT, accuracy_flag, audit_evidence,
                                                 derive_level, grade_triples)
from tools.capability_semantics.model import DeliveryArtifact, EvidenceRecord, SemanticTriple
from tools.capability_semantics.sources import SemanticsConfig

CFG = SemanticsConfig()


def _code(path="a.py") -> DeliveryArtifact:
    return DeliveryArtifact(capability_id="synth.x", kind="code", path=path)


def _test(numeric_asserts=2) -> DeliveryArtifact:
    return DeliveryArtifact(capability_id="synth.x", kind="test", path="t.py",
                            numeric_asserts=numeric_asserts)


def _verified(pass_=True, err=1.0, subject_hit=True) -> DeliveryArtifact:
    return DeliveryArtifact(capability_id="synth.x", kind="verified", path="v.md",
                            numeric_pass=pass_, numeric_error_pct=err,
                            subject_hit=subject_hit)


def test_contradiction_takes_weakest_verdict():
    t = SemanticTriple(capability_id="synth.x", declared_level="REAL")
    t.deliveries = [_code(), _test(), _verified()]
    t.evidences = [
        EvidenceRecord(source="s", ref="1", stance="contradicts", asserted_level="MOCK"),
        EvidenceRecord(source="s", ref="2", stance="contradicts", asserted_level="STUB"),
    ]
    level, code, _acc = derive_level(t, CFG)
    assert (level, code) == ("STUB", "CONTRADICTED_BY_AUDIT")


def test_no_delivery_no_evidence_is_not_supported():
    level, code, acc = derive_level(SemanticTriple(capability_id="synth.x"), CFG)
    assert (level, code, acc) == ("NOT_SUPPORTED", "NO_DELIVERY_NO_EVIDENCE", "no_numeric")


def test_evidence_without_code_is_withheld():
    t = SemanticTriple(capability_id="synth.x")
    t.evidences = [EvidenceRecord(source="s", ref="1", stance="supports_real",
                                  asserted_level="REAL")]
    assert derive_level(t, CFG)[:2] == ("WITHHELD", "NO_CODE_DELIVERY_WITH_EVIDENCE")


def test_test_reference_without_code_is_withheld():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_test()]
    assert derive_level(t, CFG)[:2] == ("WITHHELD", "NO_CODE_DELIVERY_WITH_TEST_REFERENCE")


def test_numeric_pass_promotes_real():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_code(), _test(), _verified(pass_=True, err=2.5)]
    level, code, acc = derive_level(t, CFG)
    assert (level, code, acc) == ("REAL", "NUMERIC_PASS_VERIFIED", "within_pass")


def test_numeric_pass_above_pass_threshold_not_real():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_code(), _test(), _verified(pass_=True, err=9.0)]
    level, code, acc = derive_level(t, CFG)
    assert level == "WITHHELD" and code == "UNVERIFIED_IMPLEMENTATION"
    assert acc == "above_pass"


def test_code_and_numeric_tests_is_withheld():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_code(), _test(numeric_asserts=5)]
    assert derive_level(t, CFG)[:2] == ("WITHHELD", "UNVERIFIED_IMPLEMENTATION")


def test_code_and_non_numeric_tests_is_withheld():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_code(), _test(numeric_asserts=0)]
    assert derive_level(t, CFG)[:2] == ("WITHHELD", "TEST_WITHOUT_NUMERIC_ASSERT")


def test_code_only_is_stub():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_code()]
    assert derive_level(t, CFG)[:2] == ("STUB", "CODE_ONLY")


@pytest.mark.parametrize("err,expected", [
    (0.0, "within_pass"), (3.0, "within_pass"), (3.0001, "above_pass"),
    (15.0, "above_pass"), (15.01, "above_accept"), (None, "no_numeric"),
    (float("nan"), "no_numeric"), ("bad", "no_numeric"),
])
def test_accuracy_flag_branches(err, expected):
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [DeliveryArtifact(capability_id="synth.x", kind="verified", path="v.md",
                                     numeric_error_pct=err)]
    assert accuracy_flag(t, CFG) == expected


def test_nan_does_not_propagate_to_numeric_passes():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_code(), DeliveryArtifact(capability_id="synth.x", kind="verified",
                                              path="v.md", numeric_pass=True,
                                              numeric_error_pct=float("nan"))]
    level, reason, acc = derive_level(t, CFG)
    assert level == "STUB" and reason == "CODE_ONLY" and acc == "no_numeric"
    assert math.isnan(t.deliveries[-1].numeric_error_pct)


def test_subject_backed_view_and_empty_error_list():
    t = SemanticTriple(capability_id="synth.x")
    t.deliveries = [_code(), _verified(pass_=True, err=1.0, subject_hit=False)]
    assert len(t.numeric_passes) == 1
    assert t.numeric_passes_subject_backed == []
    empty = SemanticTriple(capability_id="synth.y")
    assert empty.min_error_pct is None and empty.numeric_asserts == 0


def test_grade_triples_override_and_audit_report():
    a = SemanticTriple(capability_id="synth.a", declared_level="NOT_SUPPORTED",
                       domain="d1")
    a.deliveries = [_code()]
    b = SemanticTriple(capability_id="synth.b", declared_level="REAL", domain="d1")
    b.deliveries = [_code(), _test(), _verified()]
    grade_triples([a, b], CFG, overrides={"synth.a": ("MOCK", "PATCHED")})
    assert a.derived_level == "MOCK" and a.reason_code == "PATCHED"
    assert b.derived_level == "REAL"
    report = audit_evidence([a, b], CFG)
    assert report["graded_entries"] == 2
    assert report["criterion_graded_ok"] is False  # 少于 20 条
    assert report["level_distribution"]["REAL"] == 1
    assert report["real_subject_backed"] == ["synth.b"]
    assert 0.0 <= report["withheld_ratio"] <= 1.0


def test_audit_report_empty_is_safe():
    report = audit_evidence([], CFG)
    assert report["graded_entries"] == 0
    assert report["withheld_ratio"] == 0.0
    assert report["criterion_graded_ok"] is False
    assert report["withheld_breakdown"] == {}


def test_reason_text_covers_all_codes():
    for code in ("CONTRADICTED_BY_AUDIT", "NO_DELIVERY_NO_EVIDENCE",
                 "NO_CODE_DELIVERY_WITH_EVIDENCE", "NO_CODE_DELIVERY_WITH_TEST_REFERENCE",
                 "NUMERIC_PASS_VERIFIED", "UNVERIFIED_IMPLEMENTATION",
                 "TEST_WITHOUT_NUMERIC_ASSERT", "CODE_ONLY"):
        assert code in REASON_TEXT


def test_thresholds_read_from_config(tmp_path: Path):
    from tools.capability_semantics.sources import load_semantics_config
    from tools.capability_semantics.tests.conftest import write_json

    p = write_json(tmp_path / "cfg.json", {"thresholds": {"numeric_pass_pct": 0.5,
                                                          "bogus": "x"}})
    cfg = load_semantics_config(p)
    assert cfg.threshold("numeric_pass_pct") == 0.5
    assert cfg.threshold("bogus") == 0.0  # 非数值阈值 → 0.0，不抛
    assert cfg.threshold("numeric_accept_pct") == 15.0  # 默认值兜底