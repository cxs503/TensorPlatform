"""Tests for tools/verifier/benchmark_audit.py.

The audit's whole value rests on being able to *prove* an inconsistency from
algebra alone, so the tests pin (a) the physics helpers against closed-form
values, (b) the verdict logic on synthetic datasets that are known-consistent or
known-broken, and (c) the current state of the in-repo reference dataset so that
a silent "repair" of the numbers cannot pass unnoticed.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.verifier import benchmark_audit as ba  # noqa: E402


# ---------------------------------------------------------------- helpers
def _case(ct=0.0040, v=3.0, L=4.356, B=0.508, D=0.508, S=4.4, rt=None):
    """A synthetic, internally consistent body-of-revolution case."""
    if rt is None:
        rt = ct * 0.5 * ba.RHO_FRESH_20C * S * v * v
    return {
        "case_id": "SYN-01", "ship_type": "SYN",
        "speed_ms": v,
        "froude_number": round(ba.froude(v, L), 6),
        "experimental_ct": ct,
        "experimental_rt_n": round(rt, 4),
        "geometry": {"length_m": L, "diameter_m": D, "beam_m": B, "wetted_surface_m2": S},
    }


# ---------------------------------------------------------------- physics
def test_ittc57_closed_form_at_one_million():
    # 0.075 / (log10(1e6) - 2)^2 = 0.075 / 16
    assert ba.ittc57_cf(1e6) == pytest.approx(0.075 / 16.0, rel=1e-12)


def test_froude_and_reynolds_definitions():
    assert ba.froude(3.0, 4.356) == pytest.approx(3.0 / math.sqrt(9.80665 * 4.356), rel=1e-12)
    assert ba.reynolds(3.0, 4.356, 1.0e-6) == pytest.approx(1.3068e7, rel=1e-4)


def test_body_of_revolution_band_brackets_cylinder():
    lo, hi = ba.body_of_revolution_band(4.356, 0.508)
    assert lo < hi
    assert hi == pytest.approx(math.pi * 0.508 * 4.356, rel=1e-12)


# ---------------------------------------------------------------- identities
def test_consistent_case_passes_every_identity():
    res = ba.audit_case(_case())
    assert res["verdict"] == "USABLE", res["reason"]
    assert all(c["verdict"] == "PASS" for c in res["checks"])


def test_froude_column_built_with_wrong_length_is_flagged():
    case = _case()
    # A F_r column produced with a 181 m reference length is 1/5.63 of the
    # correct value for this 4.356 m body; the implied length must come back out.
    case["froude_number"] = round(case["froude_number"] / math.sqrt(180.0 / 4.356), 6)
    chk = ba.check_froude_identity(case)
    assert chk.verdict == "FAIL"
    assert chk.implied["length_m_implied_by_stated_Fr"] == pytest.approx(181.0, rel=2e-2)


def test_rt_column_scale_error_is_flagged_with_correction_factor():
    case = _case()
    case["experimental_rt_n"] *= 8.0 / 1.0   # stored force 8x the coefficient implies
    conv = ba.infer_dataset_convention([case])
    chk = ba.check_ct_rt_identity(case, conv)
    assert chk.verdict == "FAIL"
    assert chk.implied["correction_factor_on_rt"] == pytest.approx(1 / 8.0, rel=0.05)
    assert chk.implied["implied_within_band"] is False


def test_dataset_convention_inference_prefers_the_consistent_convention():
    cases = [_case(v=v) for v in (2.0, 3.0, 4.0)]
    conv = ba.infer_dataset_convention(cases)
    assert conv["verdict"] == "PASS"
    assert conv["best"]["rho"] == "fresh_998.2"
    assert conv["best"]["force_unit"] == "N"
    assert conv["best"]["max_deviation_pct"] < 1e-6


def test_dataset_convention_cannot_be_rescued_by_any_unit():
    cases = [_case(v=v) for v in (2.0, 3.0, 4.0)]
    for c in cases:
        c["experimental_rt_n"] = 12.5 + 3.0 * c["speed_ms"]     # arbitrary, non-proportional column
    conv = ba.infer_dataset_convention(cases)
    assert conv["verdict"] == "FAIL"
    assert conv["best"]["max_deviation_pct"] > ba.TOL_COEFF_PCT


def test_ct_below_friction_line_is_inadmissible():
    case = _case(ct=0.0010)          # well under ITTC-57 Cf at Re ~ 1e7
    chk = ba.check_ct_above_cf(case)
    assert chk.verdict == "FAIL"


# ---------------------------------------------------------------- API side
def test_detect_speed_unit_recognises_knots():
    rows = []
    for v in (1.0, 2.0, 3.05):
        rows.append((v, {"froude_number": ba.froude(v * ba.KNOT_TO_MS, 4.356)}))
    det = ba.detect_speed_unit(rows, 4.356)
    assert det["verdict"] == "MISMATCH(silent knots)"
    assert det["ratio_mean"] == pytest.approx(ba.KNOT_TO_MS, rel=1e-6)


def test_detect_speed_unit_recognises_metres_per_second():
    rows = [(v, {"froude_number": ba.froude(v, 4.356)}) for v in (1.0, 2.0, 3.05)]
    det = ba.detect_speed_unit(rows, 4.356)
    assert det["verdict"] == "PASS(m/s)"


def test_detect_speed_unit_reports_instability():
    rows = [(1.0, {"froude_number": ba.froude(1.0, 4.356)}),
            (3.05, {"froude_number": ba.froude(3.05 * ba.KNOT_TO_MS, 4.356)})]
    det = ba.detect_speed_unit(rows, 4.356)
    assert det["verdict"].startswith("FAIL")


# ---------------------------------------------------------------- regression on the shipped dataset
def test_shipped_reference_dataset_is_withheld():
    """Pins the audit finding: the in-repo reference table cannot grade a solver."""
    path = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"
    if not path.exists():
        pytest.skip("reference dataset not present")
    ds = ba.audit_dataset(path)
    assert ds["n_cases"] == 12
    assert ds["n_usable"] == 0
    assert ds["convention_inference"]["verdict"] == "FAIL"
    assert ds["convention_inference"]["best"]["max_deviation_pct"] > 15.0   # > the 15 % acceptance gate
    assert ds["failed_checks"]["I2_rt_from_ct"] == 12
    assert ds["failed_checks"]["I1_froude_identity"] == 4                   # the four DTMB-5415 rows


def test_shipped_dataset_dtmb_froude_used_a_180m_reference_length():
    path = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"
    if not path.exists():
        pytest.skip("reference dataset not present")
    data = json.loads(path.read_text())
    for case in data["cases"]:
        if case["ship_type"] != "DTMB-5415":
            continue
        chk = ba.check_froude_identity(case)
        assert chk.verdict == "FAIL"
        assert 178.0 <= chk.implied["length_m_implied_by_stated_Fr"] <= 185.0


def test_report_serialises_without_nan():
    path = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"
    if not path.exists():
        pytest.skip("reference dataset not present")
    ds = ba.audit_dataset(path)
    json.dumps(ds, allow_nan=False)   # must be strictly valid JSON for the platform report
