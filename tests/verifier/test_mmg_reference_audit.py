"""操纵域复核回归测试：参考 MMG 核的 A 类自洽判据 + 平台判决判据（含正向对照）。

Run:  python3 -m pytest tests/verifier/test_maneuvering_reference.py -q
"""
import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "verifier"))

import mmg_reference as R          # noqa: E402
import maneuvering_audit as A      # noqa: E402

U = 7.97
L = 320.0


@pytest.fixture(scope="module")
def prm():
    return R.MMGParams()


@pytest.fixture(scope="module")
def n_p(prm):
    return R.self_propulsion_n(prm)


# --------------------------------------------------------------- A-class
def test_self_propulsion_balanced(prm, n_p):
    """At u'=1 the net surge force must vanish (self-propulsion point)."""
    T_p, J = prm.thrust_p(1.0, n_p)
    X = -prm.c["R0"] + (1.0 - prm.c["t_R"]) * T_p
    assert abs(X) < 1e-6, X
    assert 0.15 < J < 0.45, J


def test_zero_rudder_runs_straight(prm, n_p):
    """delta=0 must give a rectilinear course: no sway, no yaw (A-class)."""
    h = R.manoeuvre(prm, n_p, 0.0, tau_max=50.0)["hist"]
    assert np.all(np.abs(h[:, 4]) < 1e-12)          # y'
    assert np.all(np.abs(h[:, 2]) < 1e-12)          # r'
    assert h[-1, 3] == pytest.approx(50.0, rel=1e-9)  # x' == tau'


def test_port_starboard_mirror(prm, n_p):
    """+-35 deg must produce a mirror trajectory (symmetry of the model)."""
    a = R.manoeuvre(prm, n_p, 35.0)["hist"]
    b = R.manoeuvre(prm, n_p, -35.0)["hist"]
    n = min(len(a), len(b))
    assert np.max(np.abs(a[:n, 4] + b[:n, 4])) < 1e-9   # y mirrored
    assert np.max(np.abs(a[:n, 3] - b[:n, 3])) < 1e-9   # x identical
    assert np.max(np.abs(a[:n, 2] + b[:n, 2])) < 1e-9   # r mirrored


def test_rk4_step_convergence(prm, n_p):
    """Halving the RK4 step must move the 90-deg advance by < 0.5%."""
    i1 = R.turning_indices(R.manoeuvre(prm, n_p, 35.0, step=0.01), L, U)
    i2 = R.turning_indices(R.manoeuvre(prm, n_p, 35.0, step=0.005), L, U)
    assert abs(i1["advance"] - i2["advance"]) / i2["advance"] < 5e-3


def test_steady_turn_equilibrium(prm, n_p):
    """Steady-turn residual must be numerically zero (A-class)."""
    assert R.steady_turn_residual(prm, n_p, 35.0) < 1e-6


def test_reference_advance_in_literature_band(prm, n_p):
    """Advance/L must land inside the quoted SIMMAN 2008 KVLCC2 band (B-class)."""
    idx = R.turning_indices(R.manoeuvre(prm, n_p, 35.0), L, U)
    lo, hi = A.BAND["advance_L"]
    assert lo <= idx["advance"] / L <= hi, idx["advance"] / L


# ------------------------------------------------ platform-verdict logic
def _payload_35():
    p = os.path.join(ROOT, "runs", "m1460_maneuvering_audit.json")
    if not os.path.exists(p):
        pytest.skip("run tools/verifier/maneuvering_audit.py first")
    return json.load(open(p, encoding="utf-8"))


def test_platform_turning_is_delta_invariant():
    """The recorded platform payloads for delta=0 and delta=35 are identical."""
    d = _payload_35()["platform"]
    p0 = d["maneuvering/turning@0"]["payload"]
    p35 = d["maneuvering/turning@35"]["payload"]
    j = A.judge_delta_invariance(p0, p35)
    assert j["verdict"] == "MOCK", j
    assert j["max_rel_diff"] == 0.0


def test_platform_turning_scale_is_hardcoded():
    """advance/transfer/Td equal 3.5/2.0/4.5 L to machine precision."""
    d = _payload_35()["platform"]["maneuvering/turning@35"]["payload"]
    assert d["advance"] / L == pytest.approx(3.5, abs=1e-9)
    assert d["transfer"] / L == pytest.approx(2.0, abs=1e-9)
    assert d["tactical_diameter"] / L == pytest.approx(4.5, abs=1e-9)


def test_platform_mmg_turns_with_zero_rudder():
    """A zero-rudder run must not report a closed turning circle."""
    d = _payload_35()["platform"]["mmg/turning@0"]["payload"]
    j = A.judge_zero_rudder(d, L, U)
    assert j["verdict"] == "MOCK", j


def test_platform_maneuvering_mmg_is_unavailable():
    """The endpoint is not usable: exact-schema POST -> 500 (ValidationError: Hr
    missing, reproduced in-process by runs/m1460_rootcause.py); the audit probe
    sends one extra field and is rejected earlier with 422."""
    d = _payload_35()["platform"]["maneuvering-mmg/turning"]
    assert d["status"] in (422, 500), d["status"]
    assert d["payload"].get("error_text")


# positive controls: the checkers must not be always-fail
def test_checker_passes_consistent_delta_response():
    j = A.judge_delta_invariance(
        {"advance": 900.0, "transfer": 430.0, "tactical_diameter": 1000.0,
         "steady_diameter": 1100.0},
        {"advance": 1120.0, "transfer": 620.0, "tactical_diameter": 1440.0,
         "steady_diameter": 1300.0})
    assert j["verdict"] == "RESPONSIVE"


def test_checker_passes_straight_zero_rudder():
    j = A.judge_zero_rudder({"tactical_diameter": 1e9, "turning_rate": 0.0}, L, U)
    assert j["verdict"] == "RESPONSIVE"


def test_band_error_helper():
    lo, hi = A.BAND["tactical_diameter_L"]
    assert A.rel_err(0.5 * (lo + hi), lo, hi) == 0.0
    assert A.rel_err(hi * 1.5, lo, hi) == pytest.approx(0.5, rel=1e-9)
    assert A.rel_err(float("nan"), lo, hi) is None