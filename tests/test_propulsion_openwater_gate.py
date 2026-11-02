"""Tests for the m1584 propulsion open-water evidence gate.

The gate is deterministic: it consumes the recorded probe fixture
(tests/data/verifier_assets/propulsion/,
legacy artifacts/m1584/) and never touches the network, so
these tests are stable in CI while still adjudicating the *serving* platform.
"""
import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.verifier import propulsion_openwater_gate as gate  # noqa: E402

# Versioned capture (the artifacts/ copy was dropped by the merge gate).
FIXTURE_CANDIDATES = [
    os.path.join(ROOT, "tests/data/verifier_assets/propulsion/prop_openwater_fixture.json"),
    os.path.join(ROOT, "artifacts/m1584/prop_openwater_fixture.json"),
]
FIXTURE = next((p for p in FIXTURE_CANDIDATES if os.path.exists(p)), FIXTURE_CANDIDATES[0])


@pytest.fixture(scope="module")
def result():
    return gate.run(FIXTURE)


@pytest.fixture(scope="module")
def fixture():
    return json.load(open(FIXTURE))


def _by_id(result, cid):
    return next(c for c in result["checks"] if c["id"] == cid)


# ---------------------------------------------------------------- fixture sanity

def test_fixture_has_provenance_and_rows(fixture):
    assert fixture["provenance"]["mission"] == 1584
    assert len(fixture["b_series"]) >= 60
    ok = [r for r in fixture["b_series"] if r["status"] == 200]
    assert len(ok) >= 78, f"only {len(ok)} accepted rows"
    assert len(fixture["propulsion"]) == 3


# ---------------------------------------------------------------- core verdicts

def test_verdict_is_mock_not_real(result):
    assert result["verdict"] == "MOCK", "self-declared REAL must not survive the gate"
    assert result["failed"] >= 4


def test_eta_is_invariant_to_pitch_ratio(result):
    """A B-series regression must gain efficiency with P/D; the endpoint does not."""
    c = _by_id(result, "P1_eta0_monotone_in_pitch")
    assert c["pass"] is False
    assert c["delta"] == pytest.approx(0.0, abs=1e-9)
    vals = list(c["measured"].values())
    assert max(vals) - min(vals) < 1e-9


def test_kt_is_exact_linear_function_of_pitch(result):
    """K_T = a*(P/D)+b exactly => closed-form fabrication, not a published regression."""
    c = _by_id(result, "P2_kt_nonlinear_in_pitch")
    assert c["pass"] is False
    assert c["measured"]["max_abs_residual_of_linear_fit"] < 1e-9


def test_kq_is_fixed_fraction_of_kt(result):
    """K_Q/K_T == 0.1 for every sample: eta0 collapses to eta0 = 10*J/(2*pi)."""
    c = _by_id(result, "P3_kq_over_kt_varies")
    assert c["pass"] is False
    assert c["measured"]["min"] == pytest.approx(0.1, abs=1e-9)
    assert c["measured"]["max"] == pytest.approx(0.1, abs=1e-9)


def test_eta_saturates_at_hard_cap(result):
    c = _by_id(result, "P5_no_hard_eta_cap")
    assert c["pass"] is False
    assert c["measured"]["rows_at_0.85_cap"] > 0


def test_open_water_efficiency_violates_momentum_theory(result):
    """Hard physics violation: eta0 above the actuator-disk ideal limit is impossible.

    This check needs no literature value at all: the ideal limit is exact
    momentum theory applied to the endpoint's own K_T.
    """
    c = _by_id(result, "P8_momentum_theory_bound")
    assert c["pass"] is False
    assert c["measured"]["max_eta0_minus_eta_ideal"] > 0.05


def test_propulsion_chain_thrust_scale_is_broken(result):
    c = _by_id(result, "P7_chain_thrust_scale")
    assert c["pass"] is False
    ratio = c["measured"]["ratio_reported_over_own"]
    assert ratio < 0.1, f"reported thrust is {ratio:.3%} of its own K_T law"
    # independent ballpark (ITTC-57 friction on the same hull) agrees with the
    # K_T-based thrust, not with the reported number
    assert c["measured"]["ballpark_required_thrust_N"] > 5e4
    assert c["measured"]["thrust_reported_N"] < 1e3


# ---------------------------------------------------------------- passing checks

def test_blade_geometry_and_zero_thrust_do_respond(result):
    """Honest reporting: two checks pass, so the endpoint is not a constant stub."""
    assert _by_id(result, "P4_blade_geometry_influence")["pass"] is True
    assert _by_id(result, "P6_zero_thrust_J_tracks_pitch")["pass"] is True


def test_momentum_helper_is_exact():
    """Guard the reference formula itself (no platform involvement)."""
    # low loading: ideal efficiency must approach 1
    assert gate.eta_ideal(1e-6, 0.5) == pytest.approx(1.0, abs=1e-3)
    # thrust identity: eta_i = 2/(1+sqrt(1+C_T)) with C_T = 8K_T/(pi J^2)
    kt, J = 0.2, 0.6
    ct = 8 * kt / (math.pi * J ** 2)
    assert gate.eta_ideal(kt, J) == pytest.approx(2 / (1 + math.sqrt(1 + ct)))
    assert 0.0 < gate.eta_ideal(kt, J) < 1.0


def test_ittc57_reference_is_monotone():
    assert gate.ittc57_cf(1e7) > gate.ittc57_cf(1e9) > 0