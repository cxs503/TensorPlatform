"""Tests for the propulsion verification harness (mission m1498).

Two layers:

1. *Analytic layer* — the criteria used to grade the platform are exact
   identities / bounds, so they can be unit-tested on their own.
2. *Gate layer* — the grading logic must be able to say "MOCK" for a fabricated
   open-water curve **and** must not fire on a physically admissible one
   (a gate that cannot fail is worthless — PB-005).

The recorded live responses in ``artifacts/m1498_propulsion_audit.json`` are
re-graded offline, so the audit result is reproducible without the :8001 service.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify import propulsion_audit as pa  # noqa: E402

CACHE = ROOT / "artifacts" / "m1498_propulsion_audit.json"


# --------------------------------------------------------------------------- #
# analytic layer
# --------------------------------------------------------------------------- #
def test_eta_identity_is_exact():
    # eta_O = J*KT/(2*pi*KQ); check with a hand-computed case
    kt, kq, j = 0.2011, 0.0371, 0.7
    assert pa.eta_from_coefficients(j, kt, kq) == pytest.approx(
        j * kt / (2 * math.pi * kq), rel=1e-12)
    assert pa.eta_from_coefficients(j, kt, kq) == pytest.approx(0.6037, abs=1e-3)


def test_eta_requires_positive_torque():
    with pytest.raises(ValueError):
        pa.eta_from_coefficients(0.7, 0.2, 0.0)


def test_ideal_efficiency_bounds():
    # a lightly loaded disc approaches 1, a heavily loaded one approaches 0
    assert pa.ideal_efficiency(1.0, 1e-6) > 0.999
    assert pa.ideal_efficiency(0.5, 5.0) < 0.5
    # monotone decreasing in KT at fixed J
    vals = [pa.ideal_efficiency(0.7, kt) for kt in (0.05, 0.1, 0.2, 0.4)]
    assert all(a > b for a, b in zip(vals, vals[1:]))


def test_find_kt_zero_interpolates():
    assert pa.find_kt_zero({0.0: 0.5, 0.5: 0.2, 1.0: -0.1}) == pytest.approx(0.8333, abs=1e-3)
    assert pa.find_kt_zero({0.0: 0.5, 0.5: 0.2}) is None      # no root -> no J0


def test_burrill_parameters_are_dimensionless_and_positive():
    tau = pa.thrust_loading_coefficient(2.2e6, 24.02, 0.70 * math.pi * 16.0)
    assert 0.05 < tau < 5.0
    sigma = pa.cavitation_number(101325 + 1025 * 9.80665 * 8.0, 2300.0, 24.02)
    assert 0.1 < sigma < 10.0


def test_propulsive_chain_identity():
    chain = pa.propulsive_chain(eta_o=0.62, w=0.35, t=0.20, eta_r=1.0)
    assert chain["eta_H"] == pytest.approx((1 - 0.2) / (1 - 0.35))
    assert chain["eta_D"] == pytest.approx(0.62 * chain["eta_H"])


def test_ittc78_anchor_matches_hand_algebra():
    anchor = pa.ittc78_anchor()
    re = pa.KVLCC2["speed"] * 0.514444 * pa.KVLCC2["L"] / 1.19e-6
    cf = 0.075 / (math.log10(re) - 2) ** 2
    ct = 1.20 * cf + 4.0e-4
    rt = 0.5 * 1025 * (pa.KVLCC2["speed"] * 0.514444) ** 2 * pa.KVLCC2["wetted_surface"] * ct
    assert anchor["R_T_N"] == pytest.approx(rt, rel=1e-9)
    # sanity: a 320 m VLCC at 15.5 kn needs O(1 MN) of thrust, not O(10 kN)
    assert 1.2e6 < anchor["R_T_N"] < 2.5e6
    assert 1e7 < anchor["P_E_W"] < 2e7


# --------------------------------------------------------------------------- #
# gate layer: synthetic curves
# --------------------------------------------------------------------------- #
def _resp(kt, kq, j, eta=None):
    eta = pa.eta_from_coefficients(j, kt, kq) if (eta is None and j > 0) else (
        eta if eta is not None else 0.0)
    return {"KT": kt, "KQ": kq, "eta0": round(eta, 4), "method": "synthetic"}


def _fabricated_curves():
    """Same signature as the live endpoint: KT=0.36*P/D-0.24J floored 0.05, KQ=KT/10."""
    j_grid = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2)
    curves = {}
    for pd in (0.6, 0.8, 1.0, 1.2, 1.4):
        pts = {}
        for j in j_grid:
            kt = max(0.36 * pd - 0.24 * j, 0.05)
            kq = kt / 10.0
            eta = 0.5 if j == 0 else min(1.5915 * j, 0.85)
            pts[j] = _resp(kt, kq, j, eta)
        curves[f"PD{pd}_Z4_AE0.7"] = pts
    return curves


def _admissible_curves():
    """Physically admissible B-series-like family: KT and KQ both polynomial in J,
    KT vanishing at a finite J0 that grows with P/D, eta peaking near 0.6."""
    j_grid = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4, 1.5)
    curves = {}
    for pd in (0.6, 0.8, 1.0, 1.2, 1.4):
        pts = {}
        for j in j_grid:
            kt = 0.50 * pd - 0.35 * j - 0.11 * j * j
            if j > 0 and kt <= 0:
                continue                      # open-water charts end at the zero-thrust point
            kq = 0.055 * pd ** 1.5 - 0.020 * j - 0.008 * j * j
            if kq <= 0:
                kq = 0.005
            eta = 0.0 if j == 0 else pa.eta_from_coefficients(j, kt, kq)
            pts[j] = _resp(kt, kq, j, round(eta, 4))
        curves[f"PD{pd}_Z4_AE0.7"] = pts
    # sensitivity sweeps used by P5
    for z in (3, 4, 5, 6, 7):
        curves[f"sens_PD1.0_Z{z}_AE0.70"] = {
            0.5: _resp(0.50 - 0.175 - 0.0275 - 0.002 * (z - 4),
                       0.055 - 0.010 - 0.002, 0.5)}
    for ae in (0.35, 0.55, 0.70, 0.95):
        curves[f"sens_PD1.0_Z4_AE{ae}"] = {
            0.5: _resp(0.2975 + 0.10 * (ae - 0.70), 0.043, 0.5)}
    return curves


def test_gate_rejects_fabricated_open_water_curve():
    out = pa.check_open_water(_fabricated_curves())
    assert out["grade"] == "MOCK"
    for cid in ("P0_eta_at_zero_advance", "P1_eta_identity", "P2_torque_ratio_vs_j",
                "P3_J0_vs_PD", "P4_momentum_bound", "P6_eta_vs_PD"):
        assert cid in out["failed_checks"], (cid, out["failed_checks"])


def test_gate_accepts_admissible_open_water_curve():
    out = pa.check_open_water(_admissible_curves())
    assert out["grade"] != "MOCK", out["failed_checks"]


def test_gate_rejects_out_of_range_efficiency():
    ep = {
        "calculations_propulsion": {
            "thrust": 14335.04, "torque": 6928.2, "delivered_power": 58041.8,
            "open_water_eff": 1.575, "hull_eff": 1.1, "relative_rot_eff": 1.0,
            "propulsive_eff": 1.733},
        "calculations_propulsion_alt": {
            "open_water_eff": 1.5, "hull_eff": 1.1, "relative_rot_eff": 1.0,
            "thrust": 100.0},
    }
    out = pa.check_chain(ep)
    assert out["grade"] == "MOCK"
    assert "C1_range" in out["failed_checks"]
    assert "C3_thrust_vs_anchor" in out["failed_checks"]
    assert "C6_hardcode_probe" in out["failed_checks"]


def test_gate_accepts_physically_closed_chain():
    anchor = pa.ittc78_anchor()
    t_req = anchor["R_T_N"] / 0.8
    pd_req = anchor["P_E_W"] / 0.72
    ep = {
        "calculations_propulsion": {
            "thrust": t_req, "torque": pd_req / (2 * math.pi * 80 / 60),
            "delivered_power": pd_req, "open_water_eff": 0.62,
            "hull_eff": (1 - 0.2) / (1 - 0.35), "relative_rot_eff": 1.0,
            "propulsive_eff": 0.62 * (1 - 0.2) / (1 - 0.35)},
        "calculations_propulsion_alt": {
            "open_water_eff": 0.58, "hull_eff": 1.05, "relative_rot_eff": 1.0,
            "thrust": 3.0e5},
    }
    out = pa.check_chain(ep)
    assert out["grade"] == "REAL", out["failed_checks"]


# --------------------------------------------------------------------------- #
# recorded live audit (offline re-grading)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not CACHE.exists(), reason="cached audit artefact missing")
def test_cached_audit_regrades_identically():
    audit = json.loads(CACHE.read_text())
    ow = pa.check_open_water(audit["open_water_source"])
    chain = pa.check_chain(audit["endpoints"])
    assert ow["grade"] == audit["open_water"]["grade"] == "MOCK"
    assert chain["grade"] == audit["chain"]["grade"] == "MOCK"
    assert audit["grades"]["open_water"] == "MOCK"
    assert audit["grades"]["propeller-design_design"] == "MOCK"


@pytest.mark.skipif(not CACHE.exists(), reason="cached audit artefact missing")
def test_cached_audit_records_the_defect_signatures():
    audit = json.loads(CACHE.read_text())
    pts = audit["open_water_source"]["PD1.0_Z4_AE0.7"]
    assert pts["0.0"]["eta0"] == 0.5                     # eta_O(J=0) must be 0
    assert pts["0.7"]["KQ"] == pytest.approx(pts["0.7"]["KT"] / 10.0)   # KQ = KT/10
    assert pts["0.7"]["eta0"] == 0.85                    # capped efficiency
    floored = audit["open_water_source"]["PD0.6_Z4_AE0.7"]
    assert min(v["KT"] for v in floored.values()) == pytest.approx(0.05)  # KT floor, no J0
    chain = audit["endpoints"]["calculations_propulsion"]
    assert chain["open_water_eff"] > 1.0
    assert chain["propulsive_eff"] > 1.0
    assert chain["hull_eff"] == 1.1


@pytest.mark.skipif(not CACHE.exists(), reason="cached audit artefact missing")
def test_cached_audit_figures_exist():
    audit = json.loads(CACHE.read_text())
    assert len(audit["figures"]) == 3
    for rel in audit["figures"]:
        assert (ROOT / rel).exists(), rel