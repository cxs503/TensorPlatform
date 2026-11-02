"""Tests for the independent structure-domain audit harness.

Three layers:
  * baselines  -- compared against hand-computed textbook/rule values (literature
                  numbers, not platform numbers)
  * criteria   -- could-be-wrong-both-ways check: the analytic positive control must
                  pass every non-circular criterion, while four minimal "mock" replicas
                  of the observed platform behaviour must fail the decisive criteria
  * audit CLI  -- deterministic re-grading of the cached probe (offline)
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.structure_audit import baselines as B          # noqa: E402
from tools.structure_audit import criteria as C          # noqa: E402
from tools.structure_audit import probe as P             # noqa: E402
from tools.structure_audit import reference_candidate as RC  # noqa: E402

CACHE = os.path.join(ROOT, "artifacts", "structure_audit", "live_records.json")


# ------------------------------------------------------------------ baselines

def test_buckling_coefficient_textbook_values():
    # Timoshenko & Gere: k_min = 4 at integer aspect ratios, 8.41 at a/b = 0.4,
    # 4.694 at a/b = 1.5, 4.134 at a/b = 2.5
    assert B.plate_buckling_coefficient(1.0) == pytest.approx((4.0, 1), rel=1e-12)
    assert B.plate_buckling_coefficient(2.0) == pytest.approx((4.0, 2), rel=1e-12)
    assert B.plate_buckling_coefficient(4.0) == pytest.approx((4.0, 4), rel=1e-12)
    assert B.plate_buckling_coefficient(0.4) == pytest.approx((8.41, 1), rel=1e-12)
    # k(m) minimised over m: at a/b = 1.5 the minimum sits on m = 2 (4.3403),
    # the m = 1 branch alone would give 4.694
    assert B.plate_buckling_coefficient(1.5) == pytest.approx((4.3403, 2), rel=1e-4)
    assert B.plate_buckling_coefficient(2.5) == pytest.approx((4.1344, 3), rel=1e-4)


def test_plate_buckling_stress_hand_computed():
    # b = 1.2 m, t = 12 mm, E = 210 GPa, nu = 0.3, a/b = 1  ->  75.92 MPa
    th = B.plate_buckling_stress(1.2, 1.2, 0.012)
    assert th["sigma_cr"] == pytest.approx(75.92, rel=2e-3)
    assert th["edge_force_kN"] == pytest.approx(th["sigma_cr"] * 1.2 * 0.012 / 1e3,
                                                rel=1e-12)


def test_plate_buckling_scaling_laws():
    lo = B.plate_buckling_stress(3.0, 1.2, 0.006)["sigma_cr"]
    hi = B.plate_buckling_stress(3.0, 1.2, 0.012)["sigma_cr"]
    assert hi / lo == pytest.approx(4.0, rel=1e-9)          # sigma ~ t^2
    big = B.plate_buckling_stress(6.0, 2.4, 0.012)["sigma_cr"]
    assert big / hi == pytest.approx(0.25, rel=1e-9)        # sigma ~ 1/b^2
    assert B.plate_buckling_stress(3.0, 1.2, 0.012, E=105000.0)["sigma_cr"] \
        == pytest.approx(0.5 * hi, rel=1e-9)                # sigma ~ E


def test_iacs_wave_moment_and_section_modulus():
    # L = 230 m, B = 32.2 m, Cb = 0.651  ->  C = 10.75 - 0.7^1.5 = 10.1643
    ref = B.iacs_wave_bending_moment(230.0, 32.2, 0.651)
    assert ref["C"] == pytest.approx(10.1643, abs=1e-4)
    assert ref["M_wh"] == pytest.approx(0.19 * ref["C"] * 230.0 ** 2 * 32.2 * 0.651,
                                        rel=1e-12)
    assert ref["M_wh"] == pytest.approx(2.142e6, rel=1e-3)   # kN*m
    W = B.iacs_min_section_modulus(230.0, 32.2, 0.651)
    assert W == pytest.approx(23.39, rel=1e-3)               # m^3
    stress = B.hull_girder_stress_rule(230.0, 32.2, 0.651)
    assert stress["sigma_sag_MPa"] == pytest.approx(110.0, rel=2e-3)
    assert stress["sigma_hog_MPa"] == pytest.approx(91.6, rel=2e-3)


def test_sn_curve_branches_are_continuous_at_the_knee():
    for curve in (B.DNV_C, B.DNV_D):
        # the published log(a) values are rounded to 3 decimals, so continuity holds
        # only to ~1e-3 (0.03 %) -- that rounding is itself the main absolute
        # uncertainty of the S-N baseline
        assert curve.stress_at_knee() == pytest.approx(curve.stress_at_knee_second_branch(),
                                                       rel=1e-3)
        assert curve.cycles(curve.stress_at_knee()) == pytest.approx(1e7, rel=1e-6)


def test_sn_curve_known_knee_stresses():
    # DNV-RP-C203 Table 2-1 in air: knee stress ranges 73.1 MPa (C) and 52.6 MPa (D)
    assert B.DNV_C.stress_at_knee() == pytest.approx(73.13, abs=0.05)
    assert B.DNV_D.stress_at_knee() == pytest.approx(52.63, abs=0.05)
    # C curve, m = 3 branch
    assert B.DNV_C.cycles(100.0) == pytest.approx(10 ** 12.592 * 100 ** -3, rel=1e-12)


def test_miner_and_implied_rate_round_trip():
    S = 44.0
    n = 5.0e6
    life = B.miner_life_years([S], [n])
    assert B.implied_annual_cycles(S, life) == pytest.approx(n, rel=1e-9)
    lo, hi = B.plausible_wave_cycle_band()
    assert lo < hi and 2.0e6 < lo < hi < 7.0e6


# ------------------------------------------------------------------- criteria

def test_positive_control_passes_every_non_circular_criterion():
    results = C.evaluate(P.probe_reference())
    bad = [(r.cid, r.verdict) for r in results if r.verdict != "PASS" and not r.circular]
    assert bad == []
    grades = C.grade(results)
    assert {ep: g["grade"] for ep, g in grades.items()} == {
        "/fea/plate-buckling": "REAL",
        "/structure/analyze": "REAL",
        "/structure/plate": "REAL",
        "/structure/fatigue": "REAL",
    }


def _records(endpoint, mutate):
    """Build records for the probe plan of `endpoint`, applying `mutate(inputs)`."""
    out = []
    for case, payload in P.PROBE_PLAN[endpoint]:
        out.append(C.Record(case=case, endpoint=endpoint, inputs=payload,
                            output=mutate(payload)))
    return out


def _k4_only(payload):
    """Mock A: correct formula but the aspect-ratio minimisation is dropped (k = 4)."""
    th = B.plate_buckling_stress(payload["a"], payload["b"], payload["t"],
                                 payload.get("E", 210000.0), payload.get("nu", 0.3))
    sigma = 4.0 * math.pi ** 2 * th["D"] / (payload["b"] ** 2 * payload["t"])
    return {"critical_stress": sigma, "critical_load": 0.0, "half_waves": th["m"],
            "safety_margin": payload["yield_stress"] / sigma - 1.0}


def test_mock_a_hard_coded_k_fails_aspect_criteria_but_passes_scaling():
    recs = _records(P.EP_PB, _k4_only)
    res = {r.cid: r for r in C.evaluate(recs)}
    assert res["PB1"].verdict == "FAIL"          # identity over the alpha grid
    assert res["PB2"].verdict == "FAIL"          # a/b response
    assert res["PB7"].verdict == "FAIL"          # edge force identically zero
    # the pure scaling laws are satisfied by this mock, so they are not the detector
    assert res["PB3"].verdict == "PASS"
    assert res["PB4"].verdict == "PASS"
    assert res["PB5"].verdict == "PASS"


def _mixed_units(payload):
    """Mock B: correct stress in MPa but Pa/MPa mixed in the utilization ratio."""
    g = RC.reference_hull_girder(payload)
    return {"max_stress": g["sigma_MPa"], "allowable_stress": g["sigma_allow_MPa"],
            "utilization": g["sigma_MPa"] * 1e6 / g["sigma_allow_MPa"],
            "fatigue_life": g["life"],
            "status": "需校核"}


def test_mock_b_mixed_units_fails_only_the_unit_criterion():
    recs = _records(P.EP_SA, _mixed_units)
    res = {r.cid: r for r in C.evaluate(recs)}
    assert res["SA1"].verdict == "FAIL"
    assert res["SA1"].measured == pytest.approx(1e6 - 1, rel=1e-6)
    assert res["SA2"].verdict == "PASS"          # the stress itself is fine
    assert res["SA6"].verdict == "PASS"          # plating thickness is honoured


def _range_from_yield(payload):
    """Mock C: S-N curve honoured, but the stress range is 30 % of the yield stress."""
    sy = float(payload.get("yield_stress", 235.0))
    rng = 0.3 * sy
    life = B.DNV_C.cycles(rng) / 420930.0
    return {"stress_range": rng, "design_life": 25.0, "calculated_life": life,
            "s_n_curve": "DNV-C", "status": "合格" if life >= 25.0 else "不合格"}


def test_mock_c_yield_scaled_range_fails_geometry_and_rate_criteria():
    recs = _records(P.EP_SF, _range_from_yield)
    res = {r.cid: r for r in C.evaluate(recs)}
    assert res["SF5"].verdict == "FAIL"          # range is a yield fraction
    assert res["SF2"].verdict == "FAIL"          # implied cycle rate unphysical
    assert res["SF3"].verdict == "PASS"          # the S-N exponent itself is correct


def test_missing_field_is_withheld_not_passed():
    recs = _records(P.EP_SF, lambda p: {"stress_range": 70.5, "calculated_life": 28.5,
                                        "design_life": 25.0, "s_n_curve": "DNV-C",
                                        "status": "合格"})
    res = {r.cid: r for r in C.evaluate(recs)}
    assert res["SF3"].verdict == "WITHHELD"      # single stress range -> not identifiable
    assert res["SF5"].verdict == "FAIL"


def test_plate_endpoint_yield_cap_is_steelmanned():
    """A yield-capped but otherwise correct panel check must pass SP1."""
    recs = _records(P.EP_SP, lambda p: {
        "critical_stress": min(B.plate_buckling_stress(
            p["frame_spacing"], p["frame_spacing"], p["plate_thickness"])["sigma_cr"],
            float(p["yield_stress"])),
        "actual_stress": 100.0, "safety_factor": 2.0, "status": "合格"})
    res = {r.cid: r for r in C.evaluate(recs)}
    assert res["SP1"].verdict == "PASS"
    assert res["SP2"].verdict == "PASS"


# ------------------------------------------------------------------ audit CLI

@pytest.mark.skipif(not os.path.exists(CACHE), reason="no cached live probe")
def test_cached_regrade_is_deterministic_and_matches_committed_report():
    out1 = "/tmp/struct_audit_a.json"
    out2 = "/tmp/struct_audit_b.json"
    cmd = [sys.executable, "-m", "tools.structure_audit.audit", "--cached",
           "--cache", CACHE, "--no-figures"]
    for out in (out1, out2):
        subprocess.run(cmd + ["--out", out], cwd=ROOT, check=True,
                       capture_output=True)
    a, b = json.load(open(out1)), json.load(open(out2))
    assert a["live"]["endpoint_grades"] == b["live"]["endpoint_grades"]
    assert a["control"]["unexpected_failures"] == []
    committed = os.path.join(ROOT, "artifacts", "structure_audit",
                             "structure_audit_report.json")
    ref = json.load(open(committed))
    assert a["live"]["endpoint_grades"] == ref["live"]["endpoint_grades"]
    assert a["live"]["counts"] == ref["live"]["counts"]