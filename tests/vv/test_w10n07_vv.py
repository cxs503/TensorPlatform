"""W10-N07 numerical-uncertainty certification test suite.

Run with ``make vv-test`` (or ``pytest tests/vv -q``).

Design rules for this suite:
* every test has an *external* reference or a *falsifiable* property - no test
  merely re-states the implementation;
* nothing runs longer than a few seconds, so the suite is CI-safe; the full
  certification sweep is ``make vv-w10n07``;
* the numerics under test are deterministic, so exact-equality assertions are
  used where the code is exactly reproducible.
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.vv import benchmarks as B          # noqa: E402
from scripts.vv import gci as G                 # noqa: E402
from scripts.vv import structure as S           # noqa: E402
from scripts.vv.cases import ChannelCase, build_case, solid_mask  # noqa: E402
from scripts.vv.force import surface_force, shift_field          # noqa: E402
from scripts.vv.fvm import FVMSolver, run_fvm                    # noqa: E402
from scripts.vv.lbm import LBMSolver, run_lbm                    # noqa: E402


# ---------------------------------------------------------------------------
# GCI kernel
# ---------------------------------------------------------------------------
def test_gci_uniform_second_order_closed_form():
    """Exactly second-order data reproduces the closed-form GCI."""
    f1, f2, f3 = 1.0 + 4.0 * 0.125 ** 2, 1.0 + 4.0 * 0.25 ** 2, 1.0 + 4.0 * 0.5 ** 2
    study = G.gci_study("p2", (f1, f2, f3), h=(0.125, 0.25, 0.5),
                        p_expected=2.0, eps_convention="absolute")
    assert study.p_obs == pytest.approx(2.0, abs=1e-12)
    eps21 = (f2 - f1) / abs(f1)
    assert study.gci_fine == pytest.approx(1.25 * abs(eps21) / (2 ** 2 - 1), rel=1e-12)
    assert study.phi_ext == pytest.approx(1.0, abs=1e-12)
    assert study.certifiable is True


def test_gci_general_refinement_ratio_differs_from_uniform_formula():
    """r21 != r32: the uniform closed form is wrong, the general solver is not."""
    values = (1.0 - 2.0 * 0.05 ** 2, 1.0 - 2.0 * 0.075 ** 2, 1.0 - 2.0 * 0.1 ** 2)
    study = G.gci_study("general", values, h=(0.05, 0.075, 0.1),
                        eps_convention="absolute")
    naive = G.observed_order_uniform(study.r21, study.eps21, study.eps32)
    assert study.p_obs == pytest.approx(2.0, abs=1e-10)
    assert abs(naive - 2.0) > 1e-3          # the closed form is measurably wrong


def test_gci_non_monotonic_is_degraded_not_hidden():
    """R < 0 (oscillatory) must be labelled and refused as a band."""
    study = G.gci_study("osc", (1.0, 1.02, 1.01), r21=2.0)
    assert study.regime == G.OSCILLATORY
    assert study.R < 0.0
    assert study.certifiable is False
    assert study.degradation is not None
    assert study.p_used == G.P_LIMIT_DEFAULT           # ITTC limiting order
    assert study.gci_fine > 0.0                        # still reported for traceability
    assert any("not monotonically convergent" in n for n in study.notes)


def test_gci_divergent_triplet_flagged():
    """|R| > 1 (divergent) is not certifiable either."""
    study = G.gci_study("div", (1.0, 1.1, 1.11), r21=2.0)
    assert study.regime == G.DIVERGENT
    assert study.certifiable is False


def test_gci_order_above_pmax_falls_back():
    study = G.gci_study("p5", (1.0 - 0.02, 1.0 - 0.02 * 32, 1.0 - 0.02 * 1024), r21=2.0)
    assert study.p_source.startswith("fallback")
    assert study.degradation == "p_obs_above_max"
    assert study.certifiable is False


def test_asymptotic_test_can_fail(m2033_counterexample=(1.0, 1.0 + 4 * 0.5 ** 2 + 30 * 0.5 ** 3,
                                                       1.0 + 4 * 1.0 ** 2 + 30 * 1.0 ** 3)):
    """The m2033 audit showed the legacy asymptotic indicator was tautological.

    ``phi = 1 + 4h^2 + 30h^3`` is a formally second-order family whose observed
    order is 2.44; the kernel must *fail* the asymptotic-range test for it.
    """
    f1, f2, f3 = m2033_counterexample
    study = G.gci_study("m2033", (f1, f2, f3), r21=2.0, p_expected=2.0, order_tol=0.10,
                        eps_convention="absolute")
    assert study.p_obs is not None and abs(study.p_obs - 2.0) > 0.2
    assert study.asymptotic is False
    assert study.certifiable is False


def test_asymptotic_test_passes_for_clean_second_order():
    values = (1.0 + 0.03125, 1.0 + 0.125, 1.0 + 0.5)   # exact h**2, r = 2
    study = G.gci_study("clean", values, r21=2.0, p_expected=2.0,
                        eps_convention="absolute")
    assert study.asymptotic is True
    assert study.certifiable is True


def test_refinement_ratio_floor_blocks_certification():
    """r < 1.25 cannot certify, even for a perfectly monotone triplet."""
    values = (1.0 - 0.01, 1.0 - 0.0104, 1.0 - 0.0108)   # r = 1.04
    study = G.gci_study("tiny_r", values, h=(0.96, 1.0, 1.04))
    assert min(study.r21, study.r32) < G.R_MIN
    assert study.certifiable is False


def test_uncertainty_synthesis_is_quadrature():
    assert G.synthesize_u_sn(0.03, 0.04) == pytest.approx(0.05)
    assert G.synthesize_u_v(0.03, 0.04) == pytest.approx(0.05)
    with pytest.raises(ValueError):
        G.synthesize_u_sn(-1.0, 0.0)


def test_adjudicator_withheld_when_band_covers_gate():
    adj = G.adjudicate("x", deviation=0.02, u_gci=0.20, u_input=0.0, u_d=0.0)
    assert adj.verdict == G.WITHHELD
    assert adj.discriminating is False


def test_adjudicator_achieved_and_not_achieved():
    ok = G.adjudicate("ok", deviation=0.03, u_gci=0.01, u_d=0.05)
    assert ok.verdict == G.VALIDATION_ACHIEVED and ok.ittc_consistent is True
    bad = G.adjudicate("bad", deviation=0.12, u_gci=0.01, u_d=0.02)
    assert bad.verdict == G.VALIDATION_NOT_ACHIEVED and bad.ittc_consistent is False


def test_adjudicator_refuses_band_wider_than_claimed():
    """A claim of 5 % cannot be certified when U_V = 10 %."""
    adj = G.adjudicate("claim", deviation=0.01, u_gci=0.0, u_d=0.10, claimed_band=0.05)
    assert adj.claim_certified is False
    assert adj.certified_band == pytest.approx(0.10)


def test_adjudicator_withholds_on_non_certifiable_study():
    study = G.gci_study("osc", (1.0, 1.02, 1.01), r21=2.0)
    adj = G.adjudicate("osc-case", study=study, deviation=0.0, u_d=0.01)
    assert adj.verdict == G.WITHHELD


def test_historical_claims_registry_is_re_adjudicated():
    claims = json.load(open(os.path.join(REPO_ROOT, "scripts", "vv",
                                         "claims_registry.json")))["claims"]
    rows = {c["name"]: c for c in claims}
    mv = rows["lindqvist/MV_Arctic"]
    # the -8.88 % Lindqvist MV Arctic deviation is quoted from the platform report
    deviation = (mv["simulation"] - mv["experiment"]) / mv["experiment"]
    assert -0.09 < deviation < -0.087
    adj = G.re_adjudicate_claim(mv, u_d=0.10)
    assert adj.u_v == pytest.approx(0.10)
    assert adj.claim_certified is True      # a 10 % claim is exactly at the noise level
    adj5 = G.re_adjudicate_claim(dict(mv, claimed_band=0.05), u_d=0.10)
    assert adj5.claim_certified is False


# ---------------------------------------------------------------------------
# force extractor
# ---------------------------------------------------------------------------
def test_shift_field_convention():
    a = np.arange(12, dtype=float).reshape(3, 4)
    out, valid = shift_field(a, 1, 0)          # out[j,i] = a[j, i+1] (periodic)
    assert np.allclose(out[:, :3], a[:, 1:])
    assert np.allclose(out[:, 3], a[:, 0])
    assert valid.all()
    out, valid = shift_field(a, 0, 1)          # non-periodic in y
    assert np.allclose(out[:2], a[1:])
    assert not valid[2].any()


def test_surface_force_pressure_sign():
    """A linearly decreasing pressure must push the body downstream (+x)."""
    ny, nx = 12, 20
    solid = np.zeros((ny, nx), dtype=bool)
    solid[4:8, 8:12] = True
    X, _Y = np.meshgrid(np.arange(nx) + 0.5, np.arange(ny) + 0.5)
    p = -0.001 * X
    fx, fy = surface_force(p, np.zeros((ny, nx)), np.zeros((ny, nx)), solid, 1.0, 0.0)
    assert fx == pytest.approx(4 * 4 * 0.001, rel=1e-12)
    assert fy == pytest.approx(0.0, abs=1e-15)


def test_wall_friction_extractor_matches_analytic():
    case = ChannelCase(name="poi", kind="empty", D=9.0, re_ref=20.0)
    out = B.wall_friction_extractor(case, 38, 72)
    assert out["rel_error"] < 0.02


# ---------------------------------------------------------------------------
# solvers: analytic plane Poiseuille
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("solver", ["fvm", "lbm"])
def test_poiseuille_bulk_velocity_and_profile(solver):
    case = ChannelCase(name="poi", kind="empty", D=6.0, re_ref=20.0, u0=0.06)
    res = B.poiseuille_convergence(solver, [(48, 26)], steps=4000 if solver == "fvm" else 6000)
    row = res["rows"][0]
    assert row["u_bulk_rel_error"] < 5e-3
    assert row["profile_l2_rel"] < 2e-2


@pytest.mark.parametrize("solver", ["fvm", "lbm"])
def test_channel_solver_is_deterministic(solver):
    case = ChannelCase(name="poi", kind="empty", D=6.0, re_ref=20.0)
    if solver == "fvm":
        a = run_fvm(case, 48, 26, max_steps=400, window=200, tol=1e-12)
        b = run_fvm(case, 48, 26, max_steps=400, window=200, tol=1e-12)
    else:
        a = run_lbm(case, 48, 26, max_steps=400, window=200, tol=1e-12)
        b = run_lbm(case, 48, 26, max_steps=400, window=200, tol=1e-12)
    assert a.u_bar == b.u_bar          # bit-for-bit
    assert a.drag == b.drag


@pytest.mark.parametrize("solver", ["fvm", "lbm"])
def test_obstructed_case_stays_finite_and_drags(solver):
    case, nx, ny = build_case("level", 0)
    if solver == "fvm":
        res = run_fvm(case, nx, ny, max_steps=1500, window=300, tol=1e-12)
    else:
        res = run_lbm(case, nx, ny, max_steps=1500, window=300, tol=1e-12)
    assert math.isfinite(res.c_d)
    assert res.drag > 0.0
    assert 0.0 < res.u_bar < case.u0          # the obstruction throttles the flow


# ---------------------------------------------------------------------------
# case library
# ---------------------------------------------------------------------------
def test_grid_triples_satisfy_refinement_floor():
    for kind in ("level", "brash"):
        case0, _, _ = build_case(kind, 0)
        case1, _, _ = build_case(kind, 1)
        case2, _, _ = build_case(kind, 2)
        assert case1.H / case0.H > 1.2
        assert case2.H / case1.H > 1.2


def test_obstruction_geometry_is_self_similar_across_levels():
    """The discretised body must be identical in cells at every level."""
    for lvl, expected in ((0, 81), (1, 144), (2, 225)):
        case, nx, ny = build_case("level", lvl)
        body = solid_mask(case, nx, ny)
        body[0, :] = False
        body[-1, :] = False
        assert int(body.sum()) == expected
    for lvl, expected in ((0, 27), (1, 48), (2, 75)):
        case, nx, ny = build_case("brash", lvl)
        body = solid_mask(case, nx, ny)
        body[0, :] = False
        body[-1, :] = False
        assert int(body.sum()) == expected


def test_solid_mask_keeps_walls_and_channel_open():
    case, nx, ny = build_case("level", 0)
    mask = solid_mask(case, nx, ny)
    assert mask[0].all() and mask[-1].all()
    assert mask[1:-1].any() and not mask[1:-1].all()


# ---------------------------------------------------------------------------
# structural FEM
# ---------------------------------------------------------------------------
def test_buckling_matches_euler_for_zero_foundation():
    case = S.PanelCase(name="euler", E=2.06e11, I=3.4e-6, L=2.4, k=0.0,
                       area=1.8e-3, sigma_yield=315e6)
    euler = math.pi ** 2 * case.EI / case.L ** 2
    res = S.buckling_fem(case, 80)
    assert res["p_cr"] == pytest.approx(euler, rel=1e-4)


def test_buckling_converges_to_closed_form_with_foundation():
    study = S.run_buckling_study()
    assert study["errors"][0] > study["errors"][-1]
    assert study["errors"][-1] < 1e-4
    assert study["analytic"]["mode"] == 1


def test_buckling_gci_is_monotone_and_reports_order():
    study = S.run_buckling_study()
    st = G.gci_study("pcr", [study["results"][i]["p_cr"] for i in (2, 1, 0)],
                     h=[1.0 / study["meshes"][i] for i in (2, 1, 0)])
    assert st.regime == G.MONOTONIC and st.R < 1.0
    assert st.p_obs is not None and 1.0 < st.p_obs < 4.5
    assert st.gci_fine < 1e-3
    assert abs(st.phi_ext - study["analytic"]["p_cr"]) / study["analytic"]["p_cr"] < 1e-4


# ---------------------------------------------------------------------------
# benchmark anchors
# ---------------------------------------------------------------------------
def test_roache_power_law_against_60_digit_reference():
    out = B.roache_power_law()
    assert out["pass"], out
    assert out["max_p_rel_dev"] < 1e-6
    assert out["max_gci_rel_dev"] < 1e-6


def test_locked_ittc_anchors_reproduce():
    out = B.ittc_locked_anchors()
    assert out["available"]
    assert out["n_anchors"] >= 8
    assert out["max_gci_rel_dev"] < 1e-9


def test_relative_difference_convention_is_biased_for_large_corrections():
    """Documented trap: the ITTC relative convention biases p when |eps| is large."""
    f = lambda h: 1.0 + 4.0 * h ** 2      # noqa: E731
    rel = G.gci_study("rel", (f(0.25), f(0.5), f(1.0)), r21=2.0)
    abs_ = G.gci_study("abs", (f(0.25), f(0.5), f(1.0)), r21=2.0,
                       eps_convention="absolute")
    assert abs_.p_obs == pytest.approx(2.0, abs=1e-12)
    assert rel.p_obs < 1.5                       # measurably biased low
    assert abs(rel.p_obs - 2.0) > 0.3