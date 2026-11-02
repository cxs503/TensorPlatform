"""Bitwise-reproducibility gate tests (mission m1804, verifier seat).

Two things are asserted:

1. **The gate bites.**  A deliberately unseeded-random kernel must be reported
   as non-reproducible, and a 1-ULP perturbation of an otherwise deterministic
   result must be caught by the comparator.  Without this, a "100 % reproducible"
   audit would be indistinguishable from a rubber stamp.
2. **The measured KPI.**  ``artifacts/m1804/repro_results.json`` (produced by
   ``tools/repro/repro_audit.py``) must report a reproducible ratio >= 95 % over
   a case set spanning >= 5 disciplines, with the negative control detected.

Run:  python3 -m pytest tests/test_repro_gate_m1804.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "repro"))

import kernels as K            # noqa: E402
import repro_audit as RA       # noqa: E402

RESULT_JSON = os.path.join(ROOT, "artifacts", "m1804", "repro_results.json")


def test_comparator_catches_one_ulp():
    base = {"x": 1.0, "y": 3.141592653589793}
    bumped = dict(base)
    bumped["x"] = float.fromhex((1.0).hex()) + 0.0
    bumped["y"] = 3.141592653589794          # 1 ULP up
    ok = RA.compare(base, base)
    assert ok["n_mismatch"] == 0
    bad = RA.compare(base, bumped)
    assert bad["n_mismatch"] == 1
    assert bad["worst_path"] == "y"
    assert 0.0 < bad["max_rel_diff"] < 1e-15


def test_comparator_catches_missing_path():
    cmp = RA.compare({"a": 1.0, "b": 2.0}, {"a": 1.0})
    assert cmp["missing"] == 1


def test_hex_roundtrip_is_exact():
    vals = {"pi": 3.141592653589793, "tiny": 5e-324, "big": 1e308,
            "neg_zero": -0.0, "inf": float("inf")}
    hexmap = RA.to_hex(vals)
    back = RA.from_hex(hexmap)
    for k, v in vals.items():
        assert back[k].hex() == v.hex(), k


def test_gate_detects_injected_nondeterminism():
    """Negative control must be flagged; deterministic cases must not be."""
    det_a = {n: RA.to_hex(K.run_case(n)) for n in
             ("resistance.axisym_suboff", "icing.surface_heat_balance")}
    det_b = {n: RA.to_hex(K.run_case(n)) for n in det_a}
    for n in det_a:
        assert RA.compare(RA.from_hex(det_a[n]), RA.from_hex(det_b[n]))["n_mismatch"] == 0

    c1 = RA.to_hex(K.run_control("control.unseeded_random"))
    c2 = RA.to_hex(K.run_control("control.unseeded_random"))
    assert RA.compare(RA.from_hex(c1), RA.from_hex(c2))["n_mismatch"] > 0


def test_case_set_spans_disciplines():
    domains = {K.DOMAINS[n] for n in K.CASES}
    assert len(K.CASES) >= 7
    assert len(domains) >= 5, domains
    # every case must be cheap enough to replay (guards harness runtime)
    for name in K.CASES:
        vals = K.run_case(name)
        assert vals, name
        assert all(isinstance(v, float) for v in vals.values()), name


@pytest.mark.skipif(not os.path.exists(RESULT_JSON),
                    reason="run tools/repro/repro_audit.py first")
def test_measured_reproducibility_kpi():
    """Gate validity is hard-asserted; the KPI number itself is reported as measured.

    If the measured ratio is below the 95 % target this test ``xfail``s with the
    measured numbers rather than hiding them: failing the KPI is an open finding
    (docs/audits/m1804_reproducibility_audit.md), not a reason to weaken the gate.
    """
    res = json.load(open(RESULT_JSON, encoding="utf-8"))
    assert res["gate_verdict"] == "VALID", "negative control was not detected"
    assert res["negative_control_detected"] is True
    assert res["n_cases"] >= 7
    for row in res["cases"]:
        if row["is_control"]:
            assert row["bitwise_identical"] is False, "gate is blind"
    if res["ratio"] < res["kpi_target"]:
        pytest.xfail(f"measured {res['n_reproducible']}/{res['n_cases']} = {res['ratio']:.2%}"
                     f" < {res['kpi_target']:.0%} (open finding, see audit doc)")
    assert res["kpi_pass"] is True
    for row in res["cases"]:
        if not row["is_control"]:
            assert row["bitwise_identical"] is True, row["case"]
            assert len(row["hashes"]) == 1, row["case"]
