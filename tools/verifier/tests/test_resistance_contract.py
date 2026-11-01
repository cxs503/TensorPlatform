"""Regression gate for `POST /api/v1/calculations/resistance` (calc.resistance).

Mission 1582 pinned five contract defects (D1-D5) with tests that asserted the
*broken* behaviour.  Mission 1611 fixed all five in the runtime repo, so every
pin was rewritten into its converse: the assertions now say what a usable
endpoint must do.  The five "was" anchors are quoted in the docstrings -- that
is the proof the defect is gone, not a tolerance relaxation.

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
KNOTS = 1852.0 / 3600.0


@pytest.fixture(scope="module")
def captures():
    return json.loads(CAPTURES.read_text())


@pytest.fixture(scope="module")
def report(captures):
    return gate.analyse(captures)


@pytest.fixture(scope="module")
def ok_cases(captures):
    return {c["case_id"]: c for c in captures["cases"] if c["http_status"] == 200}


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
    assert gate.KN_TO_MS_LEGACY == 0.5144          # the pre-fix constant, kept as history
    assert gate.G == 9.81
    assert gate.RHO == 1025.0
    assert gate.NU == 1.19e-6
    assert gate.DCF_ROUGHNESS == 4.0e-4


def test_capture_schema(captures):
    assert len(captures["cases"]) == 10
    keys = {"reynolds_number", "froude_number", "wetted_surface", "cf", "dcf",
            "cf_total", "cr", "ct", "rf", "rr", "rt", "pe", "speed_ms",
            "speed_unit", "cb_from_dims", "displacement_consistency_pct"}
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


def test_all_contract_checks_pass(report):
    """C8-C14 (field closure, units, geometry, speed bounds) must all pass."""
    contract = [r for r in report["checks"] if r["kind"] == "CONTRACT"] \
        + report["observations"]
    failed = [(r["check"], r["case"]) for r in contract if r["verdict"] == "FAIL"]
    assert not failed, f"CONTRACT checks regressed: {failed}"
    assert report["summary"]["verdict_contract"] == "PASS"
    assert {r["check"] for r in contract} >= {"C8", "C9", "C10", "C11", "C13", "C14"}


def test_recovered_knot_conversion_is_now_exact(report):
    """C0 (D1): the knot is converted with 1852/3600, not with 0.5144.

    Pre-fix the recovered factor was 0.514423 -> -0.0086% bias on Re/Fr and
    ~-0.023% on Rt; now the recovery is bit-exact, which is what C0 measures.
    """
    c0 = next(r for r in report["checks"] if r["check"] == "C0")
    assert c0["platform"] == pytest.approx(KNOTS, rel=1e-12)
    assert c0["rel_err_pct"] == pytest.approx(0.0, abs=1e-9)
    assert c0["verdict"] == "PASS"


# ------------------------------------------------------- former pins (now +) --
def test_d2_coefficient_field_closes(ok_cases, report):
    """FIXED D2 pin.  Was: cf+cr sat 1.8-6.6 % below ct (dCf hidden in rf only).

    Now the endpoint returns dcf and cf_total, and cf_total+cr==ct exactly.
    """
    c8 = [r for r in report["checks"] if r["check"] == "C8"]
    assert len(c8) == len(ok_cases) == 6
    assert all(r["verdict"] == "PASS" for r in c8)
    assert report["summary"]["c8_worst_abs_err_pct"] < 1e-6
    for case in ok_cases.values():
        r = case["response"]
        assert r["dcf"] == pytest.approx(gate.DCF_ROUGHNESS, rel=1e-6)
        assert r["cf_total"] == pytest.approx(r["cf"] + r["dcf"], abs=1e-9)
        assert r["cf_total"] + r["cr"] == pytest.approx(r["ct"], abs=1e-9)


def test_d3_impossible_block_coefficient_is_rejected(captures):
    """FIXED D3 pin.  Was: Cb=1.2 (nabla > L*B*T) answered with HTTP 200."""
    bad = next(c for c in captures["cases"] if c["case_id"] == "invalid_cb_1p2")
    assert bad["http_status"] == 422
    msg = json.dumps(bad["error_body"], ensure_ascii=False)
    assert "Cb" in msg and "422" not in msg          # names the offending parameter
    assert bad["request"]["cb"] > gate.CB_MAX
    # consistent geometry with the same Cb window still answers
    good = next(c for c in captures["cases"] if c["case_id"] == "midcb_10kn")
    assert good["http_status"] == 200


def test_d4_displacement_is_not_ignored(captures):
    """FIXED D4 pin.  Was: 16400 m3 and 8000 m3 were bit-identical responses."""
    light = next(c for c in captures["cases"] if c["case_id"] == "boxbarge_10kn_disp8000")
    assert light["http_status"] == 422
    msg = json.dumps(light["error_body"], ensure_ascii=False)
    assert "displacement" in msg and "几何不自洽" in msg
    # and the accepted cases echo the geometry verdict that displacement drove
    base = next(c for c in captures["cases"] if c["case_id"] == "boxbarge_10kn")
    req, res = base["request"], base["response"]
    assert res["cb_from_dims"] == pytest.approx(
        req["displacement"] / (req["length"] * req["beam"] * req["draft"]), rel=1e-6)
    assert res["displacement_consistency_pct"] < gate.GEOM_TOL_PCT


def test_d5_non_positive_speed_is_a_client_error(captures):
    """FIXED D5 pin.  Was: V=0 -> HTTP 500 (unhandled ZeroDivision)."""
    for cid in ("zero_speed", "negative_speed"):
        case = next(c for c in captures["cases"] if c["case_id"] == cid)
        assert 400 <= case["http_status"] < 500, cid
        assert case["http_status"] == 422


def test_d1_unit_contract_is_declared_and_honoured(captures, report):
    """FIXED D1 pin.  Was: schema had only `speed`; m/s callers were off by 83%.

    Two independent requirements: the schema declares the unit (C13) and the
    m/s path is numerically identical to the knots path (C14).
    """
    oa = captures["meta"]["openapi"]
    props = oa["request_properties"]
    assert props["speed_unit"]["enum"] == ["kn", "m/s"]
    assert props["speed_unit"]["default"] == "kn"
    assert any(k in props["speed"]["description"].lower()
               for k in ("kn", "节", "m/s"))
    assert {"dcf", "cf_total", "speed_ms"} <= set(oa["response_properties"])

    c14 = next(o for o in report["observations"] if o["check"] == "C14")
    assert c14["verdict"] == "PASS"
    loss = next(r for r in report["checks"] if r["check"] == "C12")["detail"][
        "consequence_if_knots_read_as_ms_pct"]
    # the pre-fix unit misread is still quantified: it is a declared, avoided trap
    assert loss == pytest.approx(-82.9, abs=1.0)
    assert c14["detail"]["rt_ms_case_kN"] == pytest.approx(
        c14["detail"]["rt_knots_case_kN"], rel=1e-4)


def test_defects_table_is_all_fixed(report):
    """Every pinned defect must be marked FIXED with its 'was' evidence kept."""
    assert {d["id"] for d in report["defects"]} == {"D1", "D2", "D3", "D4", "D5"}
    assert all(d["status"] == "FIXED" for d in report["defects"]), report["defects"]
    assert all(d["was"] and d["fix"] for d in report["defects"])


def test_residual_resistance_diagnostic_is_reported(report):
    """C15: coefficient closure does NOT certify the Rt magnitude.

    The endpoint's Cr sub-model is a simplified placeholder: for the Cb=0.651,
    Fn=0.168 hull it returns Cr/Cf=3.2, i.e. the residual part outweighs
    friction -- implausible for a fine hull, where Holtrop's (1+k) form implies
    Cr/Cf ~ 0.1-0.3.  The diagnostic must stay *out* of the pass/fail summary
    (no external benchmark curve is available here), otherwise the gate would be
    certifying an absolute level it never measured.
    """
    diags = {d["case"]: d for d in report["diagnostics"]}
    kcs = diags["kcs_like_15p5kn"]
    assert kcs["cr_over_cf"] > 1.0
    assert kcs["flag"] == "HIGH_RESIDUAL"
    assert kcs["note"]
    assert not any(r["check"] == "C15" for r in report["checks"])
    assert not any(o["check"] == "C15" for o in report["observations"])


def test_figures_and_gate_report_are_published():
    base = ROOT / "artifacts/verifier/resistance_contract"
    for name in ("gate_report.json", "fig1_cf_vs_ittc1957.png",
                 "fig2_error_bars.png", "fig3_rt_powerlaw_and_coef_gap.png"):
        p = base / name
        assert p.exists() and p.stat().st_size > 1000, p
    rep = json.loads((base / "gate_report.json").read_text())
    assert rep["summary"]["verdict_math"] == "PASS"
    assert rep["summary"]["verdict_contract"] == "PASS"
