"""Absolute-magnitude verification of ``propeller.open_water`` against the
Wageningen B-series regression (verification debt m1413 §6.5).

Two classes of assertion, deliberately separated:

* **Reference integrity** -- the shipped reference table
  (``backend/data/propulsion_benchmark_reference.json``) must be reproducible from
  the polynomial and must itself be physically sane.  If this block goes red the
  *benchmark* is broken, and no comparison against it means anything.
* **Model envelope** -- the BEM model's deviation from the regression is pinned at
  the value measured in ``artifacts/m1637/bem_vs_bseries_final.json``.  These
  bands are a *ratchet*: they do not certify accuracy, they guarantee that a
  future change cannot silently make the model worse.  The residual bias itself
  (KT +23%, KQ +12%) is an open debt recorded in
  ``docs/audit/m1637_bem_vs_bseries_verification.md``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
from tools.verify.propeller_open_water import bem_vs_bseries as bench  # noqa: E402
from tools.verify.propeller_open_water import reference_bseries as ref  # noqa: E402

REFERENCE_JSON = ROOT / "backend/data/propulsion_benchmark_reference.json"


@pytest.fixture(scope="module")
def report():
    return bench.build()


# ---------------------------------------------------------------------------
# reference integrity
# ---------------------------------------------------------------------------
def test_reference_json_exists_and_is_self_describing():
    data = json.loads(REFERENCE_JSON.read_text(encoding="utf-8"))
    assert data["schema"] == "propulsion_benchmark_reference/v1"
    assert "Oosterveld" in data["provenance"]["source"]
    assert len(data["cases"]) == 3


def test_reference_json_reproduces_polynomial():
    data = json.loads(REFERENCE_JSON.read_text(encoding="utf-8"))
    for case in data["cases"]:
        for row in case["points"]:
            c = ref.open_water_coefficients(row["J"], case["P_D"], case["Ae_A0"], case["Z"])
            assert row["KT"] == pytest.approx(c["KT"], rel=1e-5, abs=1e-6)
            assert row["KQ"] == pytest.approx(c["KQ"], rel=1e-5, abs=1e-6)
        assert case["J0"] == pytest.approx(
            ref.zero_thrust_J(case["P_D"], case["Ae_A0"], case["Z"]), rel=1e-5
        )


def test_reference_polynomial_is_physically_sane():
    """Independent of the platform model: the benchmark itself must be sane."""
    for pd in (0.8, 1.0, 1.2):
        rows = [
            ref.open_water_coefficients(J, pd, 0.70, 4) for J in (0.0, 0.2, 0.4, 0.6, 0.8)
        ]
        kt = [r["KT"] for r in rows]
        kq = [r["KQ"] for r in rows]
        eta = [r["eta_0"] for r in rows]
        assert all(b < a for a, b in zip(kt, kt[1:])), f"KT not decreasing at P/D={pd}"
        assert all(b < a for a, b in zip(kq, kq[1:])), f"KQ not decreasing at P/D={pd}"
        assert eta[2] > eta[0] and eta[3] > eta[1], f"eta not rising in the low-J band at P/D={pd}"
        assert 0.5 < ref.zero_thrust_J(pd, 0.70, 4) < 1.4
        assert 0.55 < ref.peak_efficiency(pd, 0.70, 4)["eta_0"] < 0.80


# ---------------------------------------------------------------------------
# model envelope (ratchet on the measured deviation)
# ---------------------------------------------------------------------------
def test_propeller_regime_deviation_within_measured_envelope(report):
    s = report["summary"]
    assert s["mean_abs_err_KT_pct"] <= 26.0, s
    assert s["mean_abs_err_KQ_pct"] <= 14.0, s
    assert s["mean_abs_err_eta0_pct"] <= 12.0, s
    assert s["worst_J0_err_pct"] <= 9.0, s
    assert s["worst_peak_eta_err_pct"] <= 7.0, s


def test_brake_branch_is_momentum_coupled(report):
    """Beyond J0 the induced velocity must go negative (windmill/brake state).

    With the one-sided search band ``u >= 0`` the model degenerated to uncoupled
    blade-element theory and over-predicted the braking thrust by 8.7x.  Bounds
    below are the post-fix measurement plus margin.
    """
    for case in report["cases"]:
        assert case["brake"]["KT_brake_bem"] < 0.0
        assert abs(case["brake"]["KT_brake_ratio"]) <= 4.5, case["brake"]
        assert all(row["induction_converged"] for row in case["rows"]), case["P_D"]


def test_brake_branch_improves_on_uncoupled_blade_element():
    """The coupled solution must differ measurably from the uncoupled one past J0."""
    geom = PropellerGeometry(pitch_ratio=1.0, blade_area_ratio=0.70, blade_count=4, sections=40)
    j0 = ref.zero_thrust_J(1.0, 0.70, 4)
    coupled = open_water_point(geom, 1.15 * j0)["KT"]
    uncoupled = open_water_point(geom, 1.15 * j0, axial_induction=False)["KT"]
    assert coupled > uncoupled, (coupled, uncoupled)      # less braking, i.e. closer to zero
    assert abs(coupled) < 0.6 * abs(uncoupled), (coupled, uncoupled)


def test_efficiency_below_actuator_disc_bound(report):
    for case in report["cases"]:
        for row in case["rows"]:
            if row["KT_bem"] > 0.0 and row["J"] > 1e-9:
                assert 0.0 < row["eta0_bem"] < row["eta_ideal"] + 1e-9, row


def test_zero_thrust_matches_reference_within_debt():
    for pd in (0.8, 1.0, 1.2):
        geom = PropellerGeometry(pitch_ratio=pd, blade_area_ratio=0.70, blade_count=4)
        j0_bem = solve_zero_thrust_advance_ratio(geom)
        j0_ref = ref.zero_thrust_J(pd, 0.70, 4)
        assert abs(j0_bem - j0_ref) / j0_ref <= 0.09, (pd, j0_bem, j0_ref)


def test_benchmark_is_deterministic():
    """The artifact published with the mission must be reproducible bit for bit."""
    a = bench.build(pd_list=(1.0,))
    b = bench.build(pd_list=(1.0,))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert math.isfinite(a["summary"]["mean_abs_err_KT_pct"])