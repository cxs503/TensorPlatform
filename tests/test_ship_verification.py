"""Tests for the independent ship-performance verification suite (mission m1536).

These tests validate the *reference* side of the audit — they must be green without
any network access so that the audit's conclusions stay reproducible offline.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ship_verification import holtrop1984 as hm  # noqa: E402
from ship_verification import audit_resistance as au  # noqa: E402


# ---------------------------------------------------------------------------
# Friction lines: analytic anchors
# ---------------------------------------------------------------------------

def test_ittc1957_published_values():
    """ITTC-57 gives exactly 3.000e-3 at Re=1e7 and 2.083e-3 at Re=1e8 (log10 8 -> 36)."""
    assert hm.cf_ittc1957(1e7) == pytest.approx(3.000e-3, rel=1e-9)
    assert hm.cf_ittc1957(1e8) == pytest.approx(0.075 / 36.0, rel=1e-12)
    assert hm.cf_ittc1957(1e9) == pytest.approx(0.075 / 49.0, rel=1e-12)


def test_ittc1957_monotone_decreasing():
    re = [1e6 * 2 ** k for k in range(14)]
    cf = [hm.cf_ittc1957(r) for r in re]
    assert all(b < a for a, b in zip(cf, cf[1:]))


def test_ittc1957_rejects_low_re():
    with pytest.raises(ValueError):
        hm.cf_ittc1957(50.0)


@pytest.mark.parametrize("re", [1e7, 5e7, 1e8, 5e8, 1e9])
def test_schoenherr_matches_ittc1957(re):
    """The ITTC-57 line was fitted to Schoenherr's line: agreement within 3%."""
    cf_s = hm.cf_schoenherr(re)
    assert hm.cf_ittc1957(re) == pytest.approx(cf_s, rel=0.03)


# ---------------------------------------------------------------------------
# Geometry: published benchmark particulars
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hull", [hm.KVLCC2, hm.KCS, hm.AFRAMAX])
def test_cb_consistent_with_volume(hull):
    """Cb must reproduce vol/(L B T) before any resistance value is trusted."""
    assert hull.vol / (hull.l * hull.b * hull.t) == pytest.approx(hull.cb, rel=0.005)


def test_kvlcc2_wetted_surface_vs_published():
    """KVLCC2 published wetted surface is 27194 m^2 (Gothenburg 2010 / Tokyo 2015)."""
    s = hm.wetted_surface(320.0, 58.0, 20.8, 0.8098, 0.9980, 0.8548)
    assert abs(s - 27194.0) / 27194.0 < 0.03


def test_kcs_wetted_surface_bounded():
    """KCS published wetted surface is 9494 m^2; the Cwp/lcb approximation is coarser here,
    so this is only a bounded consistency check (documented limitation, not a pass gate)."""
    s = hm.wetted_surface(230.0, 32.2, 10.8, 0.6505, 0.9846, 0.7350)
    assert abs(s - 9494.0) / 9494.0 < 0.10


@pytest.mark.parametrize("hull", [hm.KVLCC2, hm.KCS, hm.AFRAMAX])
def test_form_factor_in_published_range(hull):
    """Full-form ships fall in 1.1 < (1+k) < 1.6 (Holtrop 1984 calibration range)."""
    v = hm.form_factor_1pk(hull.l, hull.b, hull.t, hull.cb, hull.cm, hull.vol)
    assert 1.1 < v < 1.6


def test_denny_mumford_close_to_holtrop_wetted_surface():
    s_holtrop = hm.wetted_surface(320.0, 58.0, 20.8, 0.8098, 0.9980, 0.8548)
    s_dm = hm.wetted_surface_denny_mumford(320.0, 58.0, 20.8, 0.8098)
    assert abs(s_dm - s_holtrop) / s_holtrop < 0.10


# ---------------------------------------------------------------------------
# Holtrop-Mennen 1984 total resistance
# ---------------------------------------------------------------------------

def test_kvlcc2_froude_number_at_15kn():
    r = hm.holtrop1984_resistance(hm.KVLCC2, 15.0 * au.KM_TO_MS)
    assert r.fn == pytest.approx(0.13772, abs=5e-4)


def test_kvlcc2_pe_in_physical_band():
    """A 312k-dwt VLCC at 15 kn needs roughly 8-20 MW effective power."""
    r = hm.holtrop1984_resistance(hm.KVLCC2, 15.0 * au.KM_TO_MS)
    assert 8.0e3 < r.pe_kw < 20.0e3


def test_resistance_increases_with_speed():
    speeds = [10.0, 12.0, 14.0, 15.0, 16.0]
    rts = [hm.holtrop1984_resistance(hm.KVLCC2, v * au.KM_TO_MS).rt for v in speeds]
    assert all(b > a for a, b in zip(rts, rts[1:]))
    # growth is super-linear but below V^2 (friction ~V^1.85 dominates at these Fn)
    ratio = rts[-1] / rts[0]
    speed_ratio = 16.0 / 10.0
    assert speed_ratio ** 1.4 < ratio < speed_ratio ** 2.2


def test_components_sum_to_total():
    r = hm.holtrop1984_resistance(hm.KVLCC2, 15.0 * au.KM_TO_MS)
    assert r.rt == pytest.approx(r.rf_1pk + r.rw + r.rb + r.rtr + r.ra)
    assert r.rf_1pk == pytest.approx(r.form_factor_1pk * r.rf)


def test_form_effect_applied_once():
    """Guard against the (1+k) double-counting bug found while building this module."""
    r = hm.holtrop1984_resistance(hm.KVLCC2, 15.0 * au.KM_TO_MS)
    assert r.rt < r.form_factor_1pk * r.rf + r.ra + 0.02 * r.rt + r.rw + 1e-6


def test_rhow_domain_flag():
    assert hm.holtrop1984_resistance(hm.KVLCC2, 15.0 * au.KM_TO_MS).fn_in_rhow_domain
    fast = hm.holtrop1984_resistance(hm.KCS, 32.0 * au.KM_TO_MS)  # Fn ~ 0.35 -> still <= 0.4
    assert fast.fn_in_rhow_domain
    faster = hm.holtrop1984_resistance(hm.KCS, 38.0 * au.KM_TO_MS)  # Fn > 0.4
    assert faster.fn_in_rhow_domain is False
    assert faster.notes, "out-of-range evaluation must be annotated"


def test_bulb_and_transom_terms_activate():
    hull = hm.HullGeometry(
        name="bulb-test", l=230.0, b=32.2, t=10.8, cb=0.6505, cm=0.9846,
        cwp=0.7350, lcb_pct=-0.7, vol=52030.0, abt=8.0, hb=3.0, at=20.0,
    )
    r = hm.holtrop1984_resistance(hull, 24.0 * au.KM_TO_MS)
    assert r.rb > 0.0 and r.rtr > 0.0


# ---------------------------------------------------------------------------
# EEDI cross-check helpers
# ---------------------------------------------------------------------------

def test_eedi_reference_line_and_inverse():
    ref = hm.eedi_reference("tanker", 312622.0)
    p = hm.required_shaft_power_for_eedi(ref, 312622.0, 15.0)
    assert hm.eedi_from_shaft_power(p, 312622.0, 15.0) == pytest.approx(ref, rel=1e-9)
    assert 1.0e4 < p < 4.0e4


# ---------------------------------------------------------------------------
# Audit comparison logic (pure functions, no network)
# ---------------------------------------------------------------------------

def _fake_platform(geom: hm.HullGeometry, v_kn: float, scale: float = 1.0) -> dict:
    """Synthesise a platform response with resistance scaled by ``scale``."""
    ref = hm.holtrop1984_resistance(geom, v_kn * au.KM_TO_MS)
    return {
        "reynolds_number": ref.re,
        "froude_number": ref.fn,
        "wetted_surface": ref.s_ref,
        "cf": ref.cf,
        "cr": ref.cw,
        "ct": ref.ct * scale,
        "rf": ref.rf / 1e3 * scale,
        "rr": ref.rw / 1e3 * scale,
        "rt": ref.rt / 1e3 * scale,
        "pe": ref.pe_kw / 1e3 * scale,
        "method": "synthetic",
    }


def test_rel_err_pct():
    assert au.rel_err_pct(110.0, 100.0) == pytest.approx(10.0)
    assert au.rel_err_pct(0.0, 0.0) is None


def test_compare_case_passes_when_reference_is_reproduced():
    plat = _fake_platform(hm.KVLCC2, 15.0, scale=1.0)
    out = au.compare_case(hm.KVLCC2, 15.0, plat)
    rows = {r["quantity"]: r for r in out["rows"]}
    assert rows["total resistance RT"]["status"] == "PASS"
    assert rows["friction coefficient Cf"]["status"] == "PASS"
    assert rows["total resistance RT"]["rel_err_pct"] == pytest.approx(0.0, abs=1e-6)


def test_compare_case_detects_threefold_overprediction():
    plat = _fake_platform(hm.KVLCC2, 15.0, scale=3.0)
    out = au.compare_case(hm.KVLCC2, 15.0, plat)
    rows = {r["quantity"]: r for r in out["rows"]}
    assert rows["total resistance RT"]["status"] == "FAIL"
    assert rows["total resistance RT"]["rel_err_pct"] == pytest.approx(200.0, abs=1e-3)
    assert rows["effective power PE"]["status"] == "FAIL"


def test_displacement_probe_flags_inert_input():
    geom = hm.KVLCC2

    def inert(payload, **_kw):
        return {"rt": 100.0, "pe": 10.0}

    out = au.check_displacement_used(geom, 15.0, inert)
    assert out["displacement_has_no_effect"] is True
    assert out["verdict"].startswith("FAIL")

    def reactive(payload, **_kw):
        return {"rt": 100.0 + payload["displacement"] * 1e-6, "pe": 10.0}

    out2 = au.check_displacement_used(geom, 15.0, reactive)
    assert out2["displacement_has_no_effect"] is False


def test_benchmark_dataset_integrity_check(tmp_path):
    import json

    payload = {
        "version": "test",
        "description": "synthetic",
        "cases": [
            {
                "case_id": "GOOD",
                "speed_ms": 3.0,
                "experimental_ct": 0.004,
                "experimental_rt_n": 0.5 * 998.0 * 9.0 * 5.0 * 0.004,
                "geometry": {"wetted_surface_m2": 5.0},
            },
            {
                "case_id": "BAD",
                "speed_ms": 3.0,
                "experimental_ct": 0.004,
                "experimental_rt_n": 5.0,
                "geometry": {"wetted_surface_m2": 5.0},
            },
        ],
    }
    p = tmp_path / "ref.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    out = au.check_benchmark_dataset(str(p))
    status = {c["case_id"]: c["status"] for c in out["cases"]}
    assert status["GOOD"] == "PASS"
    assert status["BAD"] == "FAIL"
    assert out["n_fail"] == 1


def test_render_markdown_contains_all_sections():
    plat = _fake_platform(hm.KVLCC2, 15.0, scale=3.0)
    case = au.compare_case(hm.KVLCC2, 15.0, plat)
    audit = {
        "platform_method": "synthetic",
        "cases": [case],
        "sweeps": {},
        "displacement_check": {
            "hull": "KVLCC2", "speed_kn": 15.0, "rt_normal_kn": 1.0,
            "rt_displacement_x10_kn": 1.0, "verdict": "FAIL: test",
        },
        "eedi_crosscheck": None,
        "benchmark_dataset": None,
        "verdict_note": "verdict",
        "summary": {},
    }
    md = au.render_markdown(audit)
    for needle in ("## 1.", "## 2.", "## 3.", "## 5.", "verdict"):
        assert needle in md