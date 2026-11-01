"""Regression gate for the resistance endpoint contract audit (mission 1582).

Two kinds of assertion live here, deliberately:

* INVARIANTS (must always hold, whatever the runtime does): the reference chain
  itself is closed form, and every MATH check of the audit closes to <3 %.
* PINS (documented current state): the five contract defects are asserted with
  their measured magnitudes.  When the runtime implementation is fixed, these
  tests fail on purpose -> refresh `captures.json` with a new probe and downgrade
  the corresponding entry in `audit_resistance_contract.analyse()["defects"]`.
  That is how the gate flips from FAIL to PASS without anyone re-deriving the
  reference by hand.

Run: python -m pytest tools/verifier/tests/test_resistance_contract.py -q
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.verifier import audit_resistance_contract as gate

ROOT = Path(__file__).resolve().parents[3]
CAPTURES = ROOT / "artifacts/verifier/resistance_contract/captures.json"


@pytest.fixture(scope="module")
def captures():
    return json.loads(CAPTURES.read_text())


@pytest.fixture(scope="module")
def report(captures):
    return gate.analyse(captures)


# --------------------------------------------------------------- invariants ---
@pytest.mark.parametrize("re_,cf", [
    (1.0e6, 0.075 / 16.0),      # 0.075/(6-2)^2
    (1.0e7, 0.075 / 25.0),      # 0.075/(7-2)^2
    (1.0e8, 0.075 / 36.0),
])
def test_ittc1957_anchor_values(re_, cf):
    """The reference friction line is exact algebra, not a fitting residual."""
    assert gate.ittc1957_cf(re_) == pytest.approx(cf, rel=1e-12)


def test_reference_chain_constants_are_frozen():
    assert gate.KN_TO_MS == pytest.approx(1852.0 / 3600.0, rel=0.0)
    assert gate.G == 9.81
    assert gate.RHO == 1025.0
    assert gate.NU == 1.19e-6
    assert gate.DCF_ROUGHNESS == 4.0e-4


def test_capture_schema(captures):
    assert len(captures["cases"]) == 7
    keys = {"reynolds_number", "froude_number", "wetted_surface", "cf", "cr",
            "ct", "rf", "rr", "rt", "pe"}
    for case in captures["cases"]:
        assert "case_id" in case and "request" in case
        if case["http_status"] == 200:
            assert keys <= set(case["response"]), case["case_id"]


def test_all_math_checks_pass(report):
    """C0-C7 and C12: the endpoint computes what its `method` string claims."""
    math_rows = [r for r in report["checks"] if r["kind"] == "MATH"]
    failed = [(r["check"], r["case"], r["rel_err_pct"]) for r in math_rows
              if r["verdict"] == "FAIL"]
    assert not failed, f"MATH checks regressed: {failed}"
    assert report["summary"]["math_worst_abs_err_pct"] < gate.TOL_TARGET
    assert report["summary"]["under_3pct"] is True


def test_math_errors_are_quantization_limited(report):
    """Worst MATH deviation must stay below 1 % -- i.e. print-precision-limited.

    The endpoint prints coefficients to 6 d.p. and forces to 2 d.p.; that alone
    explains the observed 0.17 % worst case (C6, dCf back-solved from a rounded
    force).  Anything larger would mean a real modelling difference, not rounding.
    """
    assert report["summary"]["math_worst_abs_err_pct"] < 2 * gate.TOL


def test_recovered_knot_conversion(report):
    """C0: the speed unit is knots, recovered from Fr to 1e-4 relative accuracy."""
    c0 = next(r for r in report["checks"] if r["check"] == "C0")
    assert c0["platform"] == pytest.approx(gate.KN_TO_MS_PLATFORM, abs=5e-5)
    assert c0["platform"] == pytest.approx(gate.KN_TO_MS, rel=2e-4)
    assert c0["verdict"] == "PASS"


# ------------------------------------------------------------- known pins -----
def test_d2_cf_and_rf_are_not_the_same_quantity(report):
    """PINS D2: cf+cr < ct because `cf` omits the 0.400e-3 roughness allowance.

    If this ever passes, the endpoint was fixed -> update captures.json + defects.
    """
    c8 = [r for r in report["checks"] if r["check"] == "C8"]
    assert len(c8) == 6
    gaps = {r["case"]: abs(r["rel_err_pct"]) for r in c8}
    # measured 1.8 % .. 6.6 % today
    assert all(1.0 < g < 10.0 for g in gaps.values()), gaps
    # and the missing increment is one and the same constant in every case
    c6 = [r for r in report["checks"] if r["check"] == "C6"]
    dcf = [r["platform"] for r in c6]
    assert max(dcf) - min(dcf) < 2e-6, dcf
    assert sum(dcf) / len(dcf) == pytest.approx(gate.DCF_ROUGHNESS, rel=1e-3)


def test_d3_impossible_block_coefficient_is_accepted(captures):
    """PINS D3: Cb = 1.2 (nabla > L*B*T) is answered with HTTP 200."""
    bad = next(c for c in captures["cases"] if c["case_id"] == "invalid_cb_1p2")
    assert bad["http_status"] == 200
    req = bad["request"]
    assert req["cb"] > 1.0
    assert req["length"] * req["beam"] * req["draft"] * req["cb"] > \
        req["length"] * req["beam"] * req["draft"]


def test_d4_displacement_is_ignored(captures):
    """PINS D4: 16400 t and 8000 t produce bit-identical responses."""
    heavy = next(c for c in captures["cases"] if c["case_id"] == "boxbarge_10kn")
    light = next(c for c in captures["cases"] if c["case_id"] == "boxbarge_10kn_disp8000")
    assert heavy["request"]["displacement"] > 2.0 * light["request"]["displacement"] * 0.9
    assert heavy["response"] == light["response"]


def test_d5_zero_speed_is_a_server_error(captures):
    """PINS D5: V=0 -> HTTP 500 instead of a 4xx validation error."""
    zero = next(c for c in captures["cases"] if c["case_id"] == "zero_speed")
    assert zero["http_status"] == 500


def test_d1_unit_misread_consequence_is_large(report):
    """PINS D1: the undocumented knots contract is not a cosmetic issue."""
    c12 = next(r for r in report["checks"] if r["check"] == "C12")
    loss = c12["detail"]["consequence_if_knots_read_as_ms_pct"]
    assert c12["detail"]["loglog_slope"] == pytest.approx(2.66, abs=0.15)
    assert loss < -70.0, loss        # -82.9 % today


def test_figures_and_gate_report_are_published():
    base = ROOT / "artifacts/verifier/resistance_contract"
    for name in ("gate_report.json", "fig1_cf_vs_ittc1957.png",
                 "fig2_error_bars.png", "fig3_rt_powerlaw_and_coef_gap.png"):
        p = base / name
        assert p.exists() and p.stat().st_size > 1000, p
    rep = json.loads((base / "gate_report.json").read_text())
    assert rep["summary"]["verdict_math"] == "PASS"
    assert {d["id"] for d in rep["defects"]} == {"D1", "D2", "D3", "D4", "D5"}