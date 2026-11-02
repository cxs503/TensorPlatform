"""Reference verification tests for the CMA-ES kernel (m1866).

Before this file the optimiser core (``optimizer/core/cmaes.py``) — the engine
behind the KCS bow-form loop and the "optimiser library" tool class in the
capability dashboard — had *no* test at all (``grep -rn CMAES`` matched only the
implementation and its single caller).  The tests below check it against
benchmarks whose optima are known in closed form (Hansen 2016,
arXiv:1604.00772), i.e. against an external reference rather than against
itself.

Deliberately avoided: asserting "the optimiser improves the objective".  A
stalled/degenerate optimiser can improve a badly scaled objective by accident,
so every assertion here is anchored on a known optimum value or on an invariant
predicted by CMA-ES theory (box feasibility, linear convergence, scale/box
translation behaviour, bitwise reproducibility).

Runtime: ~15 s on one core (no GPU, no external data).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optimizer.verify.cmaes_reference import (  # noqa: E402
    _box,
    _cigar,
    _ellipsoid,
    _rastrigin,
    _rosenbrock,
    _shifted_sphere,
    _sphere,
    run_case,
    run_verification,
)
from optimizer.verify.cmaes_study import run_study  # noqa: E402

LAMBDA_N4 = 4 + int(np.floor(3 * np.log(4)))


# --------------------------------------------------------------------------- #
# reference integrity: the benchmarks themselves must have the stated optima
# --------------------------------------------------------------------------- #
def test_reference_functions_have_the_stated_optima():
    for factory in (_sphere(4), _ellipsoid(4), _cigar(4), _rosenbrock(3),
                    _rastrigin(3), _shifted_sphere(3)):
        fn, x_opt, f_opt = factory
        assert abs(fn(x_opt) - f_opt) < 1e-12, "reference optimum mismatch"
        # a small move away from x_opt must increase the objective
        for k in range(len(x_opt)):
            x = np.array(x_opt, dtype=float)
            x[k] += 1e-2
            assert fn(x) > f_opt + 1e-12


# --------------------------------------------------------------------------- #
# C1: closed-form reference accuracy
# --------------------------------------------------------------------------- #
def test_sphere_optimum_recovered_to_machine_precision():
    fn, x_opt, f_opt = _sphere(4)
    rec = run_case("sphere_n4", fn, x_opt, f_opt, _box(-1, 1, 4), 600)
    assert rec["err_norm"] < 1e-6, rec["err_norm"]
    assert rec["x_err"] < 1e-6, rec["x_err"]


def test_ill_conditioned_ellipsoid_solved_via_covariance_adaptation():
    """cond = 1e4 ellipsoid: only possible if the C update actually adapts."""
    fn, x_opt, f_opt = _ellipsoid(4, 1e4)
    rec = run_case("ellipsoid_n4_cond1e4", fn, x_opt, f_opt, _box(-1, 1, 4), 3000)
    assert rec["err_norm"] < 1e-6, rec["err_norm"]


def test_rosenbrock_curved_valley_known_optimum():
    fn, x_opt, f_opt = _rosenbrock(2)
    rec = run_case("rosenbrock_n2", fn, x_opt, f_opt, _box(-2, 2, 2), 2000)
    assert rec["err_norm"] < 1e-6, rec["err_norm"]
    assert np.allclose(rec["best_params"], 1.0, atol=1e-2)


# --------------------------------------------------------------------------- #
# invariants: feasibility, budget, reproducibility, box translation
# --------------------------------------------------------------------------- #
def test_never_evaluates_outside_box_and_respects_budget():
    fn, x_opt, f_opt = _sphere(4)
    bounds = _box(-1, 1, 4)
    rec = run_case("sphere_n4", fn, x_opt, f_opt, bounds, 200)
    assert rec["max_box_violation"] <= 0.0, rec["max_box_violation"]
    assert rec["evaluations"] <= rec["max_evals"] + LAMBDA_N4


def test_bitwise_reproducible_for_fixed_seed():
    fn, x_opt, f_opt = _sphere(4)
    a = run_case("a", fn, x_opt, f_opt, _box(-1, 1, 4), 200, seed=11)
    b = run_case("b", fn, x_opt, f_opt, _box(-1, 1, 4), 200, seed=11)
    assert a["f_best"] == b["f_best"]
    assert a["best_params"] == b["best_params"]
    assert a["evaluations"] == b["evaluations"]


def test_box_translation_equivalence():
    """Same problem, box centred on the optimum vs translated box [0,2]^n.

    CMA-ES initialises its mean at the centre of the search box; a hard-wired
    ``m0 = 0`` would make the translated case asymmetric against the lower wall
    (half the samples clip) and degrade converged accuracy by orders of
    magnitude.  Both runs must reach the same closed-form optimum.
    """
    fn, x_opt, f_opt = _sphere(4)
    centred = run_case("centred", fn, x_opt, f_opt, _box(-1, 1, 4), 1200)
    sh_fn, sh_x, sh_f = _shifted_sphere(4)
    shifted = run_case("shifted", sh_fn, sh_x, sh_f, _box(0, 2, 4), 1200)

    assert centred["err_norm"] < 1e-6, centred["err_norm"]
    assert shifted["err_norm"] < 1e-6, shifted["err_norm"]
    assert shifted["err_norm"] <= max(centred["err_norm"] * 100.0, 1e-6)


# --------------------------------------------------------------------------- #
# theory-predicted qualitative behaviour: linear (geometric) convergence
# --------------------------------------------------------------------------- #
def test_linear_convergence_on_sphere():
    fn, x_opt, f_opt = _sphere(4)
    rec = run_case("sphere_n4", fn, x_opt, f_opt, _box(-1, 1, 4), 600)
    hist = rec["history"]
    ev = np.array([h["eval_count"] for h in hist], dtype=float)
    fv = np.array([max(h["best_fitness"], 1e-30) for h in hist], dtype=float)
    lo, hi = 2, max(4, len(fv) // 2)
    slope = np.polyfit(ev[lo:hi], np.log10(fv[lo:hi]), 1)
    assert slope[0] < 0, "fitness must decrease geometrically"
    assert np.log10(fv[lo]) - np.log10(fv[hi - 1]) > 3.0, "expected >3 decades"


# --------------------------------------------------------------------------- #
# end-to-end harness + multi-seed study
# --------------------------------------------------------------------------- #
def test_harness_runs_and_writes_json(tmp_path):
    rep = run_verification(out_dir=str(tmp_path), quick=True)
    path = tmp_path / "cmaes_reference.json"
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert set(on_disk["checks"]) == {
        "C1_reference_error", "C2_covariance_adaptation", "C3_box_translation",
        "C4_reproducibility", "C5_box_feasibility", "C6_budget",
        "C7_sigma_sanity",
    }
    # invariants that must hold even at reduced budget
    assert on_disk["checks"]["C5_box_feasibility"]["pass"]
    assert on_disk["checks"]["C6_budget"]["pass"]
    assert on_disk["checks"]["C4_reproducibility"]["pass"]


def test_study_reports_success_and_budget_trend(tmp_path):
    rep = run_study(out_dir=str(tmp_path), max_evals=400, seeds=(1, 2),
                    budget_grid=[100, 400, 1600])
    floor = rep["budget_noise_floor_sphere_n8"]
    # more budget must never make the reference error worse (median of 2 seeds)
    meds = [f["median_err_norm"] for f in floor]
    assert meds[0] > meds[-1], meds
    assert all(r["seeds"] == 2 for r in rep["multiseed"])
    assert (tmp_path / "cmaes_study.json").exists()


def test_study_ert_orders_by_conditioning():
    """Theory: ERT must grow with the condition number (sphere < cigar, cond 1e6)."""
    rep = run_study(max_evals=1500, seeds=(3, 4), budget_grid=[100, 200])
    rows = {r["benchmark"]: r for r in rep["multiseed"]}
    sph = rows["sphere_n8"]
    cigar = rows["cigar_n8_cond1e6"]
    assert sph["success_rate"] >= 0.5, sph
    assert sph["ert_evals"] > 0
    assert sph["ert_evals"] < cigar["ert_evals"], (sph["ert_evals"], cigar["ert_evals"])
