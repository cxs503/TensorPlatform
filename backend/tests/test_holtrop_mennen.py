"""Unit + integration tests for the Holtrop-Mennen 1982 resistance module and for
the resistance-benchmark identity audit (tools/verify/resistance_benchmark_audit.py).

The audit tests are the important ones: they lock in the finding that the
shipped benchmark dataset cannot be used as an external accuracy baseline
without first repairing its internal physics identities.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.holtrop_mennen import (  # noqa: E402
    c7_flagged, c15_coefficient, c16_coefficient, c7_coefficient, domain_warnings,
    half_entrance_angle, holtrop_resistance, in_domain, ittc1957_cf,
    wetted_surface,
)

KVLCC2 = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098)


# ---------------------------------------------------------------------------
# elementary building blocks
# ---------------------------------------------------------------------------
def test_ittc1957_closed_form():
    re = 1.0e9
    assert ittc1957_cf(re) == pytest.approx(0.075 / (9.0 - 2.0) ** 2, rel=1e-12)


def test_ittc1957_monotone_decreasing_in_re():
    values = [ittc1957_cf(r) for r in (1e6, 1e7, 1e8, 1e9)]
    assert all(a > b for a, b in zip(values, values[1:]))


def test_ittc1957_rejects_low_re(): 
    with pytest.raises(ValueError):
        ittc1957_cf(50.0)


def test_c16_branches_continuous_at_cp_08():
    """Both 1982 branches of c16 must agree at CP = 0.8 (self-consistency check)."""
    low = 8.07981 * 0.8 - 13.8673 * 0.8 ** 2 + 6.984388 * 0.8 ** 3
    assert c16_coefficient(0.8) == pytest.approx(1.73014 - 0.7067 * 0.8, rel=1e-12)
    assert low == pytest.approx(c16_coefficient(0.7999999), rel=2e-3)


def test_c7_continuous_at_both_boundaries():  # m1674 ADJUDICATED
    """Both published breaks must be continuous and c7 positive for every B/L.

    Adjudication (m1674): this test previously asserted the *swapped* branch
    assignment, i.e. ``c7(0.11) = max(0.5 - 0.0625/0.11, 1e-3)`` and
    ``c7_flagged(0.11) is True``, and documented "the published regression is
    discontinuous at B/L = 0.11".  With the swapped assignment c7 <= 0 for
    L/B >= 8, which is inside the 3.9..9.5 validity domain declared by the same
    module, and the two published breaks cannot both be hit by chance.  The
    published assignment is continuous at 0.11 (0.229577*0.11^(1/3) = 0.11) and
    at 0.25 (B/L = 0.25 = 0.5 - 0.0625*4).
    """
    assert c7_coefficient(0.25) == pytest.approx(0.5 - 0.0625 * 4.0, rel=1e-12)
    assert c7_coefficient(0.25) == pytest.approx(0.25, rel=1e-12)
    assert c7_coefficient(0.10) == pytest.approx(0.229577 * 0.10 ** (1 / 3), rel=1e-12)
    for bl in (0.105, 0.11, 0.14, 0.18, 0.2499, 0.25, 0.30, 0.40):
        lo, hi = bl - 1e-9, bl + 1e-9
        assert abs(c7_coefficient(hi) - c7_coefficient(lo)) / c7_coefficient(bl) < 1e-4
    # middle branch is B/L itself; upper branch is 0.5 - 0.0625 L/B
    assert c7_coefficient(0.18) == pytest.approx(0.18, rel=1e-12)
    assert c7_coefficient(0.30) == pytest.approx(0.5 - 0.0625 / 0.30, rel=1e-12)
    # positive for every L/B in the declared domain (0.105 .. 0.256)
    assert all(c7_coefficient(bl) > 0 for bl in (0.105, 0.125, 0.18, 0.25, 0.256))
    assert c7_flagged(0.11) is False and c7_flagged(0.18) is False


def test_c15_three_bands_on_length_over_nabla_cube_root():  # m1674 ADJUDICATED
    """Published c15 has three bands on L^3/nabla: constant / linear / zero.

    Adjudication (m1674): the pre-fix single-branch form interpolated linearly
    all the way down (``c15(4.0) = -1.69385 + (4 - 8)/2.36``) and returned
    -1.69385 where the table has 0.  L^3/nabla = 512 -> L/nabla^(1/3) = 8.0 and
    L^3/nabla = 1727 -> 12.0 exactly.
    """
    assert c15_coefficient(4.0) == pytest.approx(-1.69385)        # L^3/nabla = 64
    assert c15_coefficient(9.0) == pytest.approx(-1.69385 + (9.0 - 8.0) / 2.36)
    assert c15_coefficient(12.5) == pytest.approx(0.0)            # L^3/nabla = 1953
    # band edges
    assert c15_coefficient(8.0) == pytest.approx(-1.69385, rel=1e-12)
    assert c15_coefficient(11.5) == pytest.approx(-1.69385 + (11.5 - 8.0) / 2.36)


def test_wetted_surface_box_limit_within_regression_scatter():
    """For a rectangular box (Cb=Cm=Cwp=1) the regression must approach the exact
    wetted area L(B+2T) within a few per cent -- it is a fitted curve, not exact."""
    L, B, T = 100.0, 20.0, 10.0
    s = wetted_surface(L, B, T, 1.0, 1.0, 1.0)
    exact = L * (B + 2.0 * T)
    assert abs(s - exact) / exact < 0.05


# ---------------------------------------------------------------------------
# whole-ship resistance
# ---------------------------------------------------------------------------
def test_kvlcc2_wetted_surface_agrees_with_independent_regression():
    """Holtrop-S vs Denny-Mumford (S = 1.7 L T + nabla/T): independent regressions,
    must agree within 3 % or something in the S formula is wrong."""
    r = holtrop_resistance(speed_ms=7.97, cm=0.9992, cwp=0.8737, **KVLCC2)
    nabla = KVLCC2["cb"] * KVLCC2["length"] * KVLCC2["beam"] * KVLCC2["draft"]
    s_dm = 1.7 * KVLCC2["length"] * KVLCC2["draft"] + nabla / KVLCC2["draft"]
    assert abs(r.wetted_surface_m2 - s_dm) / s_dm < 0.03


def test_form_factor_in_physical_band():
    r = holtrop_resistance(speed_ms=7.97, **KVLCC2)
    assert 1.05 < r.form_factor < 1.60       # full tanker, Holtrop band
    assert r.cp == pytest.approx(KVLCC2["cb"] / r.cm, rel=1e-12)


def test_components_sum_to_total():
    r = holtrop_resistance(speed_ms=7.97, **KVLCC2)
    parts = r.rf_n + r.rw_n + r.rbulb_n + r.rtr_n + r.ra_n + r.rapp_n
    assert r.rt_n == pytest.approx(parts, rel=1e-12)
    assert r.ct == pytest.approx(r.rt_n / (0.5 * 1025.0 * 7.97 ** 2
                                          * r.wetted_surface_m2), rel=1e-12)


def test_total_resistance_increases_with_speed():
    speeds = [4.0, 6.0, 8.0, 10.0, 12.0]
    rt = [holtrop_resistance(speed_ms=v, **KVLCC2).rt_n for v in speeds]
    assert all(b > a for a, b in zip(rt, rt[1:]))


def test_wave_resistance_vanishes_at_low_froude_number():
    """RW/(nabla rho g) -> 0 as Fn -> 0 (the 1982 exponent is dominated by m1 Fn^-0.9)."""
    slow = holtrop_resistance(speed_ms=0.5, **KVLCC2)
    fast = holtrop_resistance(speed_ms=7.97, **KVLCC2)
    assert slow.rw_n < 0.05 * fast.rw_n


def test_wave_regression_never_returns_negative_resistance():
    """The cosine interference term oscillates; RW must never come back negative
    (the clip branch is defensive -- it is not reached on the KVLCC2 sweep)."""
    for i in range(240):
        r = holtrop_resistance(speed_ms=0.4 + 0.05 * i, **KVLCC2)
        assert r.rw_n >= 0.0
    assert fast_rt_positive()


def fast_rt_positive() -> bool:
    r = holtrop_resistance(speed_ms=7.97, **KVLCC2)
    return r.rt_n > 0.0 and math.isfinite(r.ct)


def test_domain_guard_flags_slender_axisymmetric_body():
    """SUBOFF-like envelope (B/T = 1) is far outside the regression domain."""
    assert not in_domain(4.356, 0.508, 0.508, 0.508, 0.459)
    w = domain_warnings(4.356, 0.508, 0.508, 0.508, 0.459)
    assert any("B/T" in x for x in w) and any("Cb" in x for x in w)
    assert in_domain(320.0, 58.0, 20.8, 0.8098, 0.143)


def test_half_entrance_angle_physical_band():
    r = holtrop_resistance(speed_ms=7.97, **KVLCC2)
    assert 10.0 < r.ie_deg < 70.0


def test_derived_particulars_are_declared():
    r = holtrop_resistance(speed_ms=7.97, **KVLCC2)
    assert "cm" in r.derived and "cwp" in r.derived
    r2 = holtrop_resistance(speed_ms=7.97, cm=0.9992, cwp=0.8737, **KVLCC2)
    assert "cm" not in r2.derived


def test_bulb_and_transom_terms_activate():
    base = holtrop_resistance(speed_ms=7.97, **KVLCC2)
    with_bulb = holtrop_resistance(speed_ms=7.97, abt=50.0, hb=6.0, t_forward=20.8, **KVLCC2)
    with_transom = holtrop_resistance(speed_ms=7.97, at=200.0, **KVLCC2)
    assert with_bulb.rbulb_n > 0.0
    assert with_transom.rtr_n > 0.0
    assert with_bulb.rt_n != pytest.approx(base.rt_n)
    assert with_transom.c5 < 1.0


def test_rejects_impossible_input():
    with pytest.raises(ValueError):
        holtrop_resistance(320.0, 58.0, 20.8, 1.4, 7.97)
    with pytest.raises(ValueError):
        holtrop_resistance(320.0, 58.0, 20.8, 0.81, -1.0)
    with pytest.raises(ValueError):
        holtrop_resistance(320.0, 58.0, 20.8, 0.81, 7.97, lcb_frac=0.30)


# ---------------------------------------------------------------------------
# benchmark dataset audit (the verification debt this work pays down)
# ---------------------------------------------------------------------------
def _load_audit_module():
    path = REPO / "tools" / "verify" / "resistance_benchmark_audit.py"
    spec = importlib.util.spec_from_file_location("resistance_audit", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit():
    return _load_audit_module()


def test_audit_passes_canon_v3_identities(audit):  # m1674 ADJUDICATED
    """Canon v3.0 (m1561) already repaired the identities, so nothing is rejected.

    Adjudication (m1674): this test asserted that *every* shipped benchmark case
    is rejected with a failed identity.  It was written against the pre-m1561
    reference file and was already red on the merge base (verified by
    re-running it with the m1674 source changes stashed) — canon v3.0 replaced
    the self-inconsistent rows (SUBOFF density, DTMB speed/Fr, KVLCC2 Rt) and
    marked the survivors DERIVED/WITHHELD.  The audit's job is to test the
    *dataset*, so the guard is now: no identity fails, and the rows whose
    resistance is absent stay explicitly untested instead of silently passing.
    """
    data = json.loads((REPO / "backend" / "data" / "benchmark_reference.json").read_text())
    results = [audit.audit_case(c) for c in data["cases"]]
    assert len(results) == 12
    for r in results:
        assert r["verdict"] != "REJECT_AS_BASELINE", (r["case_id"], r["failed_identities"])
        assert r["failed_identities"] == []
    # every case still has at least one identity that cannot be closed
    assert all(r["untested_identities"] for r in results)


def test_audit_catches_synthetic_froude_error(audit):
    case = {
        "case_id": "SYN-1", "ship_type": "GENERIC", "speed_ms": 2.572,
        "froude_number": 0.061,          # wrong by a factor of 5.6, as shipped
        "experimental_ct": 0.0045, "experimental_rt_n": 8.2,
        "geometry": {"length_m": 5.72, "beam_m": 0.759, "draft_m": 0.246,
                     "cb": 0.508, "wetted_surface_m2": 4.5},
    }
    r = audit.audit_case(case)
    assert r["identities"]["I1_froude"]["pass"] is False
    assert abs(r["identities"]["I1_froude"]["rel_err"]) > 4.0


def test_audit_accepts_a_self_consistent_synthetic_case(audit):
    L, B, T, cb, V = 320.0, 58.0, 20.8, 0.8098, 7.97
    r = holtrop_resistance(speed_ms=V, **KVLCC2)
    case = {
        "case_id": "SYN-OK", "ship_type": "GENERIC", "speed_ms": V,
        "froude_number": V / math.sqrt(9.81 * L),
        "experimental_ct": r.ct, "experimental_rt_n": r.rt_n,
        "geometry": {"length_m": L, "beam_m": B, "draft_m": T, "cb": cb,
                     "wetted_surface_m2": r.wetted_surface_m2},
    }
    res = audit.audit_case(case)
    assert res["identities"]["I1_froude"]["pass"] is True
    assert res["identities"]["I2_ct_rt"]["pass"] is True
    assert res["verdict"] == "USABLE_WITH_CAVEAT"   # displacement still untested


def test_artifact_written_by_audit_is_parseable(tmp_path, audit, monkeypatch):
    monkeypatch.setattr(audit, "OUT", tmp_path)
    monkeypatch.setattr(audit, "_figures", lambda cases: None)
    assert audit.main() == 0
    payload = json.loads((tmp_path / "audit.json").read_text())
    # m1674 ADJUDICATED: canon v3.0 closes every identity, see the test above.
    assert payload["n_rejected"] == 0
    assert (tmp_path / "table.md").exists()