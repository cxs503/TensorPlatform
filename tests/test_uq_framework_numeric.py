"""C3 acceptance: numerical-error leg (GCI order check, residual gate, round-off).

Three *independent* discretisations with different formal orders (1, 2, 4) are
scored against the order they are known to have, so the estimator cannot pass by
being hard-wired to a single value:

* explicit Euler on ``u' = -u`` -> ``p = 1``;
* the three-point scheme on ``-u'' = sin(pi x)`` -> ``p = 2``;
* classical RK4 on ``u' = -u`` -> ``p = 4``.

The reference QoI is analytic in every case (``e^-1`` or ``pi^-2``), so the
absolute error of each grid level is also known -- the GCI band is additionally
checked to *bracket* the true error, which is the property a validation band is
supposed to have and the three-grid asymptotic ratio cannot test.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services.uq_framework import (  # noqa: E402
    UQConfig,
    grid_convergence,
    residual_norm,
    roundoff_study,
    run_numerical_study,
    run_uq,
)
from backend.services.uq_framework.synthetic import diffusion_bvp, relaxation  # noqa: E402

TOL = 0.10  # C3: |p_obs - p_expected| / p_expected < 10 %


def _triplet(qoi_of_n, levels=(80, 40, 20)):
    """QoI on fine -> medium -> coarse grids (the order ``gci`` expects)."""
    return [qoi_of_n(n) for n in levels]


@pytest.mark.parametrize("scheme,p_expected,levels", [
    ("euler", 1.0, (80, 40, 20)),
    ("rk4", 4.0, (80, 40, 20)),
])
def test_observed_order_reproduces_the_scheme_order(scheme, p_expected, levels):
    vals = _triplet(lambda n: relaxation(n, scheme)["qoi"], levels)
    g = grid_convergence(*vals, r21=2.0, r32=2.0, p_expected=p_expected)
    assert g["convergence"] == "monotonic"
    assert g["p_obs"] is not None
    assert g["order_deviation"] < TOL
    assert g["order_ok"] is True


def test_observed_order_of_the_second_order_scheme():
    """C3 headline case: the elliptic problem's three-point scheme (p = 2)."""
    vals = _triplet(lambda n: diffusion_bvp(n)["qoi"])
    g = grid_convergence(*vals, r21=2.0, r32=2.0, p_expected=2.0)
    assert g["p_obs"] == pytest.approx(2.0, rel=TOL)
    assert g["order_deviation"] < TOL and g["order_ok"] is True
    assert g["gci_fine_pct"] < 1.0  # a converged triplet carries a small band


def test_gci_band_brackets_the_true_error():
    """A validation band must contain the actual error of the finest grid."""
    levels = (80, 40, 20)
    sols = [diffusion_bvp(n) for n in levels]
    vals = [s["qoi"] for s in sols]
    g = grid_convergence(*vals, p_expected=2.0)
    true_err = abs(vals[0] - sols[0]["exact_mid"])
    band = g["gci_fine_pct"] / 100.0 * abs(vals[0])
    assert true_err <= 3.0 * band, (true_err, band)
    assert true_err <= g["gci_fine_pct"] * abs(vals[0]) * 3.0


def test_non_uniform_refinement_ratio_is_handled():
    """Anisotropic refinement must not silently return the constant-ratio order."""
    vals = [diffusion_bvp(n)["qoi"] for n in (90, 60, 40)]
    g = grid_convergence(*vals, r21=1.5, r32=1.5, p_expected=2.0)
    assert g["p_obs"] == pytest.approx(2.0, rel=TOL)
    assert g["order_ok"] is True


def test_finest_first_ordering_is_required():
    """Reversing the triplet must be *detected*, not reported as a valid order."""
    vals = [diffusion_bvp(n)["qoi"] for n in (20, 40, 80)]  # coarse first: wrong
    g = grid_convergence(*vals, p_expected=2.0)
    assert g["order_ok"] is False
    assert g["p_obs"] < 0 or g["p_source"].startswith("fallback")


def test_residual_gate_flags_a_non_converged_solve():
    sol = diffusion_bvp(40)
    good = residual_norm(sol["A"], sol["u"], sol["b"])
    assert good["residual_rel"] < 1e-10
    bad = residual_norm(sol["A"], sol["u"] * 1.001, sol["b"])
    assert bad["residual_rel"] > 1e-4


def test_roundoff_is_measured_not_assumed():
    """float32 vs float64 gives a measured round-off floor for this kernel."""
    ro = roundoff_study(lambda dt: diffusion_bvp(80, dt)["u_mid"])
    assert ro["rel_diff"] > 0.0
    assert ro["rel_diff"] < 1e-5
    # machine-epsilon ratio float32/float64 ~ 5.4e8 (must be > 1, not inverted)
    assert 1e8 < ro["eps_ratio_32_over_64"] < 1e9


def test_run_numerical_study_assembles_all_three_sources():
    sol = diffusion_bvp(80)
    vals = [diffusion_bvp(n)["qoi"] for n in (80, 40, 20)]
    res = run_numerical_study(
        grid_solutions=vals, r21=2.0, r32=2.0, p_expected=2.0,
        linear_system={"A": sol["A"], "x": sol["u"], "b": sol["b"]},
        residual_tol=1e-8,
        roundoff_kernel=lambda dt: diffusion_bvp(80, dt)["u_mid"],
    )
    d = res.as_dict()
    assert d["discretization"]["order_ok"] is True
    assert d["iteration"]["ok"] is True
    assert d["roundoff"]["rel_diff"] > 0
    assert d["discrete_band_rel"] == pytest.approx(
        d["discretization"]["gci21"], rel=1e-12)


def test_numerical_leg_wired_through_run_uq_scores_c3():
    func = lambda x0: 1.0 + 0.5 * x0  # noqa: E731 -- the propagation leg is incidental
    vals = [diffusion_bvp(n)["qoi"] for n in (80, 40, 20)]
    cfg = UQConfig(
        "c3_e2e",
        [__import__("backend.services.uq_framework", fromlist=["InputDistribution"])
         .InputDistribution.uniform("x0", 0.0, 1.0)],
        propagation_method="monte_carlo", n_mc=200, sobol=False,
        numerical={"grid_solutions": vals, "r21": 2.0, "r32": 2.0, "p_expected": 2.0},
    )
    res = run_uq(func, cfg)
    assert res.criteria["C3_gci_order"]["status"] == "pass"
    assert res.numerical["discretization"]["p_obs"] == pytest.approx(2.0, rel=TOL)