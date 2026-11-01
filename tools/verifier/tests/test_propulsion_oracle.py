"""Tests for the propulsion verification oracle and pilot (tp-ver-7).

Run:
    python -m pytest tools/verifier/tests/test_propulsion_oracle.py -q

The tests are offline: the pilot replay reads the cached API capture committed
under ``artifacts/verifier/propulsion/platform_capture.json``.
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFIER_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(VERIFIER_DIR))
sys.path.insert(0, os.path.join(VERIFIER_DIR, "oracles"))

import propulsion_oracle as oracle  # noqa: E402

CAPTURE = os.path.join(REPO, "artifacts", "verifier", "propulsion", "platform_capture.json")


# ---------------------------------------------------------------------------
# stratum (a): exact definitions
# ---------------------------------------------------------------------------


def test_advance_ratio_definition():
    # 7.2 m/s, 1.5 rps, D=8 m -> J = 0.6
    assert oracle.advance_ratio(7.2, 1.5, 8.0) == pytest.approx(0.6)


def test_thrust_and_torque_coefficients():
    # K_T = T/(rho n^2 D^4); 1 N of reference scaling
    t = 0.2 * 1025.0 * 2.25 * 8.0 ** 4
    assert oracle.thrust_coefficient(t, 1.5, 8.0) == pytest.approx(0.2)
    q = 0.045 * 1025.0 * 2.25 * 8.0 ** 5
    assert oracle.torque_coefficient(q, 1.5, 8.0) == pytest.approx(0.045)


def test_open_water_efficiency_identity():
    # eta = J K_T /(2 pi K_Q): J=0.6, KT=0.2, KQ=0.03 -> 0.6366
    assert oracle.open_water_efficiency(0.6, 0.2, 0.03) == pytest.approx(
        0.6 * 0.2 / (2 * math.pi * 0.03), rel=1e-12)
    assert oracle.open_water_efficiency(0.5, 0.3, 0.0) == 0.0


# ---------------------------------------------------------------------------
# stratum (b): momentum bound
# ---------------------------------------------------------------------------


def test_thrust_loading_formula():
    # C_T = 8 K_T/(pi J^2); J=0.5, K_T=0.25 -> 8*0.25/(pi*0.25) = 2.5465
    assert oracle.thrust_loading(0.5, 0.25) == pytest.approx(8 * 0.25 / (math.pi * 0.25))


def test_ideal_efficiency_limits():
    assert oracle.ideal_efficiency(0.0) == pytest.approx(1.0)   # zero loading
    assert oracle.ideal_efficiency(3.0) == pytest.approx(2.0 / (1.0 + 2.0))
    # monotonically decreasing in thrust loading
    vals = [oracle.ideal_efficiency(ct) for ct in (0.1, 1.0, 5.0, 20.0)]
    assert vals == sorted(vals, reverse=True)


def test_momentum_bound_flags_overloaded_efficiency():
    """An eta_0 above the actuator-disk limit must be rejected."""
    j, kt = 0.5, 0.25
    eta_i = oracle.ideal_efficiency(oracle.thrust_loading(j, kt))
    # same K_T but K_Q inflated (efficiency above the bound) -> must be flagged
    series = {"rows": [
        {"J": 0.1, "P_D": 1.0, "KT": 0.32, "KQ": 0.02, "eta0": 0.3},
        {"J": j,   "P_D": 1.0, "KT": kt,  "KQ": 0.02, "eta0": min(0.95, eta_i + 0.1)},
        {"J": 0.7, "P_D": 1.0, "KT": 0.1, "KQ": 0.01, "eta0": 0.4},
    ]}
    res = oracle.constraint_momentum_bound(series)
    assert res.passed is False
    assert res.measured["n_violations"] >= 1


# ---------------------------------------------------------------------------
# oracle discriminating power: a physically sane model passes, a toy fails
# ---------------------------------------------------------------------------


def _sane_propeller_series() -> dict:
    """Plausible merchant-propeller curves (monotone K_T, eta peak ~0.55)."""
    rows = []
    for i in range(21):
        j = round(0.05 * i, 2)
        kt = 0.42 - 0.40 * j - 0.08 * j * j        # quadratic, zero thrust at J0 ~ 0.89
        kq = 0.050 - 0.040 * j - 0.010 * j * j     # quadratic, different J-dependence
        if kq <= 1e-3:
            continue
        eta = oracle.open_water_efficiency(j, kt, kq)
        rows.append({"J": j, "P_D": 1.0, "KT": kt, "KQ": kq, "eta0": eta})
    return {"rows": rows}


def test_sane_propeller_passes_hard_constraints():
    res = {c.cid: c for c in oracle.evaluate_series(_sane_propeller_series())}
    for cid in ("CP-01", "CP-03", "CP-04", "CP-06", "CP-07", "CP-08", "CP-14"):
        assert res[cid].passed, f"{cid} falsely rejected a sane model: {res[cid].measured}"


def test_sane_propeller_efficiency_peak_in_published_band():
    rows = _sane_propeller_series()["rows"]
    eta_max = max(r["eta0"] for r in rows)
    assert 0.45 <= eta_max <= 0.80, f"generator sanity: eta_max={eta_max:.3f}"


# ---------------------------------------------------------------------------
# replay of the captured platform responses (regression on the findings)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def capture():
    with open(CAPTURE, encoding="utf-8") as fh:
        return json.load(fh)


def test_capture_covers_both_capabilities(capture):
    assert capture["series"]["endpoint"].endswith("/propeller-design/wageningen-b")
    assert capture["end_to_end"]["endpoint"].endswith("/calculations/propulsion")
    assert len(capture["series"]["rows"]) == 3 * 21
    assert capture["geometry_sweep"]


def test_platform_open_water_fails_fabrication_criteria(capture):
    res = {c.cid: c for c in oracle.evaluate_series(capture["series"],
                                                    capture["geometry_sweep"])}
    # decisive fabrication fingerprints
    assert res["CP-05"].passed is False, "eta_0 must depend on pitch ratio"
    assert res["CP-08"].passed is False, "K_Q/K_T must vary with J"
    assert res["CP-14"].passed is False, "K_T(J) must carry curvature"
    assert res["CP-15"].passed is False, "geometry multipliers must vary with J"
    assert res["CP-01"].passed is False, "eta_0(J=0) must be 0"
    assert res["CP-06"].passed is False, "eta_0 must not saturate"


def test_platform_open_water_violates_momentum_bound(capture):
    res = oracle.constraint_momentum_bound(capture["series"])
    assert res.passed is False
    assert res.measured["n_violations"] > 0
    # the reported design-point efficiency exceeds the actuator-disk limit
    worst = res.measured["worst_excess"]
    assert worst["excess"] > 0.03


def test_platform_end_to_end_efficiencies_exceed_unity(capture):
    endo = capture["end_to_end"]
    res = {c.cid: c for c in oracle.evaluate_end_to_end(endo)}
    assert res["CP-11"].passed is False            # eta_D = 1.413 > 1
    assert res["CP-12"].passed is False            # P_E > P_D
    assert res["CP-13"].passed is False            # implied K_T = 0.0021
    # internal identities do hold -> the defect is in the physics, not the arithmetic
    assert res["CP-09"].passed is True
    assert res["CP-10"].passed is True


def test_summarize_reports_failed_ids(capture):
    series_res = oracle.evaluate_series(capture["series"], capture["geometry_sweep"])
    summ = oracle.summarize(series_res)
    assert summ["verdict"] == "fail"
    assert "CP-03" in summ["failed_ids"]
    assert summ["n_constraints"] == len(series_res)
    assert summ["n_passed"] + summ["n_failed"] == summ["n_constraints"]