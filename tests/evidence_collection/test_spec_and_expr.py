"""规格与表达式判据的单元测试（夹具全部合成，零领域词条）。"""
from __future__ import annotations

import json

import pytest

from tools.evidence_collection import expr, spec


def _minimal(**over):
    base = {
        "schema": spec.SCHEMA,
        "thresholds": {"real_tolerance_pct": 3.0, "accept_tolerance_pct": 15.0},
        "sources": {"ledger": "l.json", "out_dir": "out", "runtime": {"base_url": "http://x"}},
        "capabilities": {
            "syn.alpha": {
                "surface": ["/a"],
                "probes": [{"id": "p", "method": "GET", "path": "/a",
                            "criteria": [{"id": "c", "kind": "status", "expect": [200]}]}],
            }
        },
    }
    base.update(over)
    return base


def test_spec_valid_and_digest_stable():
    doc = spec.validate_spec(_minimal())
    assert spec.spec_sha256(doc) == spec.spec_sha256(json.loads(json.dumps(doc)))


def test_spec_rejects_wrong_schema():
    with pytest.raises(spec.SpecError):
        spec.validate_spec(_minimal(schema="nope"))


def test_spec_rejects_unknown_criterion_kind():
    bad = _minimal()
    bad["capabilities"]["syn.alpha"]["probes"][0]["criteria"][0]["kind"] = "magic"
    with pytest.raises(spec.SpecError):
        spec.validate_spec(bad)


def test_spec_rejects_inverted_tolerance():
    with pytest.raises(spec.SpecError):
        spec.validate_spec(_minimal(thresholds={"real_tolerance_pct": 20.0,
                                                "accept_tolerance_pct": 15.0}))


def test_spec_rejects_capability_without_evidence_source():
    bad = _minimal()
    bad["capabilities"] = {"syn.alpha": {}}
    with pytest.raises(spec.SpecError):
        spec.validate_spec(bad)


def test_spec_allows_root_path_for_len_check():
    doc = _minimal()
    doc["capabilities"]["syn.alpha"]["probes"][0]["criteria"] = [
        {"id": "n", "kind": "list_min_len", "path": "", "min_len": 2}
    ]
    assert spec.validate_spec(doc)


def test_expr_identity_over_response_fields():
    payload = {"a": 0.25162, "b": 0.055658, "c": 0.5, "eta": 0.35976}
    value, symbols = expr.evaluate("c*a/(2*pi*b)", payload)
    assert abs(value - payload["eta"]) / payload["eta"] < 1e-4
    assert set(symbols) == {"a", "b", "c"}


def test_expr_rejects_unsafe_syntax():
    payload = {"a": 1.0}
    for bad in ("__import__('os')", "a.__class__", "lambda: 1", "open('x')", "a[0]"):
        with pytest.raises(expr.ExprError):
            expr.evaluate(bad, payload)


def test_expr_division_by_zero_is_expr_error():
    with pytest.raises(expr.ExprError):
        expr.evaluate("1/a", {"a": 0.0})


def test_expr_nested_field_access():
    payload = {"outer": {"inner": 2.0}}
    value, _ = expr.evaluate("field('outer.inner')*3", payload)
    assert value == pytest.approx(6.0)


def test_rel_error_definition():
    assert expr.rel_error(101.0, 100.0) == pytest.approx(0.01)
    assert expr.rel_error(0.0, 0.0) == 0.0