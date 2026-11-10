"""Tests for the multi-physics coupling verification framework.

Acceptance mapping (node P250-N08):

  A1  interface flux conservation < 1 % on 3 coupling faces, and the gate has to
      reject a deliberately non-conservative exchange;
  A2  global mass/momentum/energy budget < 2 %, same discrimination requirement;
  A3  coupling iteration: geometric decay, observed order >= 1 on 3 contrasts,
      measured ratio equal to the value predicted for the damped interface
      Newton scheme, and the gate has to reject the two slow schemes;
  A4  error propagation: |S| < 1 for 5 input parameters, matching the closed form;
  A5  negative control: coupling off reproduces the single-domain solution
      (< 0.1 %) while an injected 1 % interface leak is caught.

Every expected number is either derived from the closed-form chain or from a
stated threshold in ``tools.coupling.multiphysics.THRESHOLDS`` — nothing is
copied from a previous run.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from tools.coupling.multiphysics import (
    THRESHOLDS,
    Block,
    InterfaceCase,
    analytic_block_values,
    analytic_cell_values,
    analytic_solution,
    budget_anchor,
    convergence_metrics,
    decoupling_anchor,
    interface_anchor,
    perturb_case,
    run_coupled,
    sensitivity_anchor,
    solve_block_dirichlet,
)

LENGTHS = (1.0, 0.8, 1.2, 1.0)
SOURCES_VOLUME = (0.05, 0.02, 0.03, 0.05)
SOURCES_FACE = (0.05, -0.03, 0.04)
DAMPING = 0.5
CONTRASTS = {
    "matched": (1.0, 0.25, 4.0, 0.75),
    "contrast_40": (1.0, 4.0, 0.25, 0.1),
    "reversed": (0.1, 0.25, 4.0, 1.0),
}


def make_case(name="matched", coefficients=None, sources=None, quantity="conserved", **kw):
    blocks = tuple(
        Block(length=length, coefficient=coef, source=src, n_cells=12, grading=1.04)
        for length, coef, src in zip(
            LENGTHS, coefficients or CONTRASTS["matched"], sources or SOURCES_VOLUME
        )
    )
    kw.setdefault("tolerance", 1e-12)
    kw.setdefault("max_iterations", 400)
    kw.setdefault("relaxation", DAMPING)
    return InterfaceCase(
        name=name,
        blocks=blocks,
        interface_sources=SOURCES_FACE,
        u_left=-1.0,
        u_right=1.0,
        quantity=quantity,
        **kw,
    )


# ---------------------------------------------------------------------------
# reference: the single-domain solver reproduces the closed form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_cells", [8, 16, 32, 64])
def test_finite_volume_block_matches_closed_form(n_cells):
    block = Block(length=1.0, coefficient=0.4, source=0.07, n_cells=n_cells, grading=1.03)
    sol = solve_block_dirichlet(block, -1.0, 1.0)
    ref = analytic_block_values(block, -1.0, 1.0, sol.centers)
    err = float(np.max(np.abs(sol.values - ref)) / np.max(np.abs(ref)))
    assert err < 5e-3


def test_mesh_refinement_reduces_the_discretisation_error():
    errors = []
    for n_cells in (8, 16, 32):
        block = Block(length=1.0, coefficient=0.4, source=0.07, n_cells=n_cells, grading=1.03)
        sol = solve_block_dirichlet(block, -1.0, 1.0)
        ref = analytic_block_values(block, -1.0, 1.0, sol.centers)
        errors.append(float(np.max(np.abs(sol.values - ref))))
    assert errors[1] < errors[0] and errors[2] < errors[1]
    # second-order convergence: each refinement should gain at least a factor 2
    assert errors[1] < errors[0] / 2.0


def test_closed_form_chain_satisfies_the_interface_jump_condition():
    case = make_case()
    analytic = analytic_solution(case)
    jump = analytic.flux_right_side - analytic.flux_left_side
    assert jump == pytest.approx(np.asarray(case.interface_sources), abs=1e-10)
    # and the coupled run reproduces that closed form
    run = run_coupled(case)
    values = analytic_cell_values(case, analytic)
    for sol, ref in zip(run.blocks, values):
        assert float(np.max(np.abs(sol.values - ref))) < 5e-3


# ---------------------------------------------------------------------------
# A1: interface flux conservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CONTRASTS))
def test_interface_flux_conserved_on_every_face(name):
    case = make_case(f"a1_{name}", CONTRASTS[name])
    anchor = interface_anchor(run_coupled(case))
    assert anchor["n_faces"] == 3
    assert len(anchor["faces"]) == 3
    assert anchor["max_rel_error"] < THRESHOLDS["interface_flux_rel_error"]
    assert anchor["passes"] is True
    # each side agrees with the closed-form flux as well
    assert anchor["max_rel_error_vs_analytic"] < THRESHOLDS["interface_flux_rel_error"]


def test_interface_gate_rejects_a_non_conservative_exchange():
    """Diagnostic power: a 2 % transfer defect must trip the A1 gate."""
    run = run_coupled(make_case("a1_defect"))
    good = interface_anchor(run, transfer_scale=1.0)
    bad = interface_anchor(run, transfer_scale=1.02)
    assert good["passes"] is True
    assert bad["passes"] is False
    assert bad["max_rel_error"] > THRESHOLDS["interface_flux_rel_error"]
    # the measured error is the injected defect, not something arbitrary
    assert bad["max_rel_error"] == pytest.approx(0.02, rel=1e-3)


# ---------------------------------------------------------------------------
# A2: global conservation ledger
# ---------------------------------------------------------------------------


def test_global_budget_closes_for_each_quantity():
    from scripts.run_p250_n08_multiphysics import QUANTITY_CASES

    for quantity, spec in QUANTITY_CASES.items():
        case = make_case(f"budget_{quantity}", spec["coefficients"], spec["sources"], quantity)
        anchor = budget_anchor(run_coupled(case))
        assert anchor["quantity"] == quantity
        assert anchor["exchanged_rel_error"] < THRESHOLDS["global_budget_rel_error"]
        assert anchor["passes"] is True
        # each block's own discrete balance is exact for a consistent scheme
        assert anchor["max_block_balance_rel"] < 1e-10


def test_budget_gate_rejects_a_non_conservative_exchange():
    run = run_coupled(make_case("a2_defect"))
    bad = budget_anchor(run, transfer_scale=1.05)
    assert bad["passes"] is False
    assert bad["exchanged_rel_error"] > THRESHOLDS["global_budget_rel_error"]


# ---------------------------------------------------------------------------
# A3: coupling iteration convergence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CONTRASTS))
def test_coupling_iteration_converges_with_order_one(name):
    run = run_coupled(make_case(f"a3_{name}", CONTRASTS[name]))
    metrics = convergence_metrics(run)
    assert metrics["converged"] is True
    assert 0.0 < metrics["spectral_ratio"] < THRESHOLDS["spectral_ratio"]
    assert (
        metrics["order"]
        >= THRESHOLDS["convergence_order"] - THRESHOLDS["order_estimator_tolerance"]
    )
    assert metrics["passes"] is True
    # damped interface Newton on a linear map: the rate is *predicted*
    assert metrics["spectral_ratio"] == pytest.approx(abs(1.0 - DAMPING), abs=2e-3)
    assert metrics["decades_per_iteration"] == pytest.approx(
        -math.log10(abs(1.0 - DAMPING)), abs=2e-3
    )


def test_order_estimator_reproduces_a_known_geometric_series():
    run = run_coupled(make_case("a3_estimator"))
    synthetic = replace(run, residual_series=[0.5**n for n in range(1, 60)], converged=True)
    metrics = convergence_metrics(synthetic)
    assert metrics["spectral_ratio"] == pytest.approx(0.5, abs=1e-6)
    assert metrics["order"] == pytest.approx(1.0, abs=1e-3)
    assert metrics["decades_per_iteration"] == pytest.approx(math.log10(2.0), abs=1e-6)


def test_gate_rejects_scalar_relaxation_and_diagonal_preconditioning():
    """The naive Dirichlet-Neumann iteration must not be certified."""
    case = make_case("a3_schemes")
    scalar = run_coupled(case, scheme="scalar")
    jacobi = run_coupled(case, scheme="jacobi")
    newton = run_coupled(case, scheme="newton")
    assert scalar.converged is False
    assert convergence_metrics(scalar)["passes"] is False
    # jacobi decays but far too slowly to reach the tolerance in the budget
    jacobi_metrics = convergence_metrics(jacobi)
    assert jacobi_metrics["spectral_ratio"] > 0.9
    assert jacobi_metrics["passes"] is False
    assert convergence_metrics(newton)["passes"] is True


def test_run_is_deterministic():
    case = make_case("determinism")
    first = run_coupled(case)
    second = run_coupled(make_case("determinism"))
    assert first.run_digest == second.run_digest
    assert first.interface_values == pytest.approx(second.interface_values, rel=0, abs=1e-14)


# ---------------------------------------------------------------------------
# A4: error propagation
# ---------------------------------------------------------------------------


def test_five_input_sensitivities_stay_below_one():
    case = make_case("a4")
    parameters = (
        ("coefficient_k1", "coefficient", 0),
        ("coefficient_k3", "coefficient", 2),
        ("u_left", "u_left", 0),
        ("u_right", "u_right", 0),
        ("source_face_s1", "source_interface", 0),
    )
    anchor = sensitivity_anchor(case, parameters)
    assert len(anchor["parameters"]) == 5
    assert anchor["passes"] is True
    for row in anchor["parameters"]:
        assert abs(row["sensitivity"]) < THRESHOLDS["sensitivity_coefficient"]
        assert row["sensitivity"] == pytest.approx(row["analytic_sensitivity"], abs=1e-6)
    # the output itself is the closed-form throughput
    assert anchor["output"] == pytest.approx(anchor["analytic_output"], rel=1e-9)


def test_perturbation_touches_only_the_named_parameter():
    case = make_case("a4_perturb")
    bumped = perturb_case(case, "coefficient", 1, 1.5)
    assert bumped.blocks[1].coefficient == pytest.approx(case.blocks[1].coefficient * 1.5)
    assert bumped.blocks[0] == case.blocks[0]
    assert bumped.u_left == case.u_left


# ---------------------------------------------------------------------------
# A5: negative control
# ---------------------------------------------------------------------------


def test_coupling_off_reproduces_the_single_domain_solution():
    anchor = decoupling_anchor(make_case("a5"))
    assert anchor["max_deviation"] < THRESHOLDS["decoupled_deviation"]
    assert anchor["max_deviation"] == 0.0
    assert anchor["passes"] is True


def test_negative_control_catches_an_injected_leak():
    anchor = decoupling_anchor(make_case("a5_leak"), injected_leak=1e-2)
    assert anchor["injected_leak_detected"] is True
    assert anchor["injected_leak_deviation"] > THRESHOLDS["decoupled_deviation"]
    # switching the coupling on must move the solution away from the decoupled one
    trend = [row["max_deviation"] for row in anchor["trend_with_coupling"]]
    assert trend == sorted(trend)
    assert trend[-1] > 10.0 * THRESHOLDS["decoupled_deviation"]


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_case_rejects_inconsistent_interface_data():
    blocks = (Block(length=1.0, coefficient=1.0), Block(length=1.0, coefficient=1.0))
    with pytest.raises(ValueError):
        InterfaceCase(
            name="bad", blocks=blocks, interface_sources=(0.1, 0.2), u_left=0.0, u_right=1.0
        )
    with pytest.raises(ValueError):
        InterfaceCase(
            name="bad",
            blocks=blocks,
            interface_sources=(0.1,),
            u_left=0.0,
            u_right=1.0,
            transfer="unknown",
        )
    with pytest.raises(ValueError):
        InterfaceCase(
            name="bad",
            blocks=blocks,
            interface_sources=(0.1,),
            u_left=0.0,
            u_right=1.0,
            relaxation=3.0,
        )


def test_block_mesh_spans_the_requested_length():
    block = Block(length=2.5, coefficient=1.0, n_cells=17, grading=1.05)
    edges = block.edges()
    assert edges.size == 18
    assert edges[0] == 0.0 and edges[-1] == pytest.approx(2.5)
    assert np.all(np.diff(edges) > 0.0)


# ---------------------------------------------------------------------------
# artifact contract (the numbers the report quotes must come from a run)
# ---------------------------------------------------------------------------


def test_acceptance_record_covers_a1_to_a5():
    from scripts.run_p250_n08_multiphysics import build_cases
    from tools.coupling.multiphysics import acceptance_record

    cases = build_cases()
    record = acceptance_record(
        interface_cases=cases["interface"],
        budget_cases=cases["budget"],
        sensitivity_case=cases["sensitivity"],
        sensitivity_parameters=(
            ("coefficient_k1", "coefficient", 0),
            ("u_left", "u_left", 0),
        ),
        decoupling_case=cases["decoupling"],
    )
    for key in (
        "A1_interface_flux",
        "A2_global_budget",
        "A3_convergence",
        "A4_error_propagation",
        "A5_negative_control",
    ):
        assert key in record
        assert record[key]["passes"] is True
    assert record["passes"] is True
    assert record["thresholds"] == THRESHOLDS
