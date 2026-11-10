"""Regression suite for the multi-solver coupling energy-conservation module.

Covered invariants
------------------
* contract validation detects missing fields, wrong units and sign-convention
  violations (negative controls -- the validator must not be vacuous),
* contracts are data-driven (JSON) with a built-in fallback, and >= 3 solver
  pairs are registered,
* each solver reproduces the closed-form solution to its discretization order,
* each discrete operator is conservative (``q_b - q_a = integral(f)``),
* the Dirichlet--Neumann coupling reaches a residual <= 1e-8 and interface flux
  closure <= 1e-6 for every registered pair, and an injected 1 % flux error is
  detected,
* refinement shows second-order convergence of the interface quantities.

Synthetic solver names are used where the test targets the generic machinery, so
the suite stays free of application vocabulary.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.multi_solver_coupling import contracts as C
from services.multi_solver_coupling.coupling import DirichletNeumannCoupler
from services.multi_solver_coupling.energy import (
    coupled_energy_report,
    dirichlet_energy,
    flux_closure_error,
    global_energy_balance,
)
from services.multi_solver_coupling.solvers.base import (
    CANONICAL_X,
    OWN_OUTWARD,
    dirichlet,
    import_solver,
    neumann,
)
from services.multi_solver_coupling.solvers.bem1d import BEM1D
from services.multi_solver_coupling.solvers.bem2d import (
    harmonic_linear,
    harmonic_linear_flux,
    harmonic_quadratic,
    harmonic_quadratic_flux,
    solve_disk_dirichlet,
)
from services.multi_solver_coupling.solvers.fem1d import FEM1D
from services.multi_solver_coupling.solvers.fvm1d import FVM1D
from services.multi_solver_coupling.verification import (
    CASE_PIECEWISE_K,
    CASE_VARIABLE_K,
    FLUX_TOL,
    RESIDUAL_TOL,
    build_pair,
    interface_states,
    refinement_study,
    run_all,
    run_pair,
    tamper_controls,
)

E = float(np.e)


# ---------------------------------------------------------------------------
# Contract face
# ---------------------------------------------------------------------------


@pytest.fixture()
def restored_registry():
    """Snapshot/restore the global contract registry around a test."""
    snapshot = dict(C.CONTRACT_REGISTRY)
    try:
        yield
    finally:
        C.clear_registry()
        for pair, contract in snapshot.items():
            C.register_contract(contract)


def test_config_declares_at_least_three_pairs():
    path = C.default_config_path()
    assert path.is_file(), f"contract config missing: {path}"
    contracts = C.load_contracts(path)
    assert len(contracts) >= 3
    pairs = {tuple(c.pair) for c in contracts}
    assert ("FVM", "FEM") in pairs and ("FVM", "BEM") in pairs and ("FEM", "BEM") in pairs
    for contract in contracts:
        assert contract.state_fields and contract.flux_fields


def test_registry_falls_back_to_builtin_defaults(restored_registry):
    missing = C.default_config_path().with_name("does_not_exist.json")
    contracts = C.load_contracts(missing)
    assert len(contracts) >= 3
    assert C.load_contracts(C.default_config_path())  # restore from config


def _synthetic_contract():
    state = C.FieldSpec("state_q", "state", "K", aliases=("u",))
    flux = C.FieldSpec("flux_q", "flux", "W/m", aliases=("q",))
    return C.InterfaceContract(
        pair=("solver_p", "solver_r"),
        state_fields=[state],
        flux_fields=[flux],
        field_aliases={"flux_q": {"solver_p": "p_flux", "solver_r": "r_flux"}},
    )


def test_contract_alias_resolution():
    contract = _synthetic_contract()
    a = C.InterfaceState("solver_p", {"u": np.array([2.0]), "p_flux": np.array([-1.0])})
    b = C.InterfaceState("solver_r", {"u": np.array([2.0]), "r_flux": np.array([-1.0])})
    report = contract.validate(a, b)
    assert report.ok, report.as_dict()
    assert report.n_violations == 0


def test_contract_detects_missing_field():
    contract = _synthetic_contract()
    a = C.InterfaceState("solver_p", {"u": np.array([2.0]), "p_flux": np.array([-1.0])})
    b = C.InterfaceState("solver_r", {"u": np.array([2.0])})
    report = contract.validate(a, b)
    assert not report.ok
    assert "flux_q" in report.missing_b


def test_contract_detects_unit_mismatch():
    """Regression: the unit check used to be a stub that could never fire."""
    contract = _synthetic_contract()
    a = C.InterfaceState(
        "solver_p", {"u": np.array([2.0]), "p_flux": np.array([-1.0])}, units={"p_flux": "W/m"}
    )
    b = C.InterfaceState(
        "solver_r", {"u": np.array([2.0]), "r_flux": np.array([-1.0])}, units={"r_flux": "W/mm"}
    )
    report = contract.validate(a, b)
    assert not report.ok
    assert report.unit_mismatches and "W/mm" in report.unit_mismatches[0]


def test_contract_detects_sign_convention_violation():
    contract = _synthetic_contract()
    a = C.InterfaceState("solver_p", {"u": np.array([2.0]), "p_flux": np.array([-1.0])})
    b = C.InterfaceState("solver_r", {"u": np.array([2.0]), "r_flux": np.array([1.0])})
    report = contract.validate(a, b)
    assert not report.ok
    assert report.sign_violations


def test_boundary_condition_orientation_semantics():
    """Own-outward flux at the right end is the negative canonical flux."""
    bc = neumann(-1.5, orientation=OWN_OUTWARD)
    assert bc.canonical_flux("right") == pytest.approx(1.5)
    assert bc.canonical_flux("left") == pytest.approx(-1.5)
    assert neumann(-1.5, orientation=CANONICAL_X).canonical_flux("right") == pytest.approx(-1.5)


# ---------------------------------------------------------------------------
# Solver face
# ---------------------------------------------------------------------------


def _analytic_case(family: str, n: int):
    sol = import_solver(
        family, 0.0, 1.0, n,
        k=(lambda x: 1.0 + x),
        source=(lambda x: -(2.0 + x) * np.exp(x)),
    )
    return sol.solve(dirichlet(1.0), dirichlet(E))


@pytest.mark.parametrize("family", ["FVM", "FEM"])
def test_solver_matches_closed_form(family):
    x = np.linspace(0.05, 0.95, 40)
    errors = []
    for n in (40, 160):
        sol = _analytic_case(family, n)
        T = np.interp(x, sol.x, sol.T)
        errors.append(float(np.max(np.abs(T - np.exp(x)))) / E)
    assert errors[-1] < 5e-3
    # second-order convergence on the interior samples
    order = np.log2(errors[0] / errors[-1])
    assert order > 1.7, (errors, order)


@pytest.mark.parametrize("family", ["FVM", "FEM"])
def test_discrete_operator_is_conservative(family):
    """q(b) - q(a) = discrete integral(f) holds to roundoff for each operator."""
    from services.multi_solver_coupling.verification import _source_integral

    for n in (50, 200):
        solver = import_solver(family, 0.0, 1.0, n,
                               k=(lambda x: 1.0 + x),
                               source=(lambda x: -(2.0 + x) * np.exp(x)))
        sol = solver.solve(dirichlet(1.0), dirichlet(E))
        discrete = solver.source_integral()
        assert global_energy_balance(sol, discrete)["rel_error"] < 1e-9  # sparse-solve roundoff
        # and the discrete integral converges to the analytic one (2nd order)
        analytic = _source_integral(CASE_VARIABLE_K)
        assert abs(discrete - analytic) / abs(analytic) < 1e-3


def test_fem_recovers_imposed_neumann_flux_exactly():
    sol = FEM1D(0.0, 0.5, 25, k=1.5).solve(neumann(-2.0), dirichlet(3.0))
    assert sol.q_left == pytest.approx(-2.0, rel=0, abs=1e-12)


def test_bem_solves_linear_field_and_representation_is_consistent():
    bem = BEM1D(0.0, 1.0, 50, k=2.5)
    sol = bem.solve(dirichlet(0.0), dirichlet(2.5))  # T = 2.5 x
    assert np.allclose(sol.T, 2.5 * sol.x, rtol=0, atol=1e-12)
    assert sol.q_left == pytest.approx(-2.5 * 2.5, rel=0, abs=1e-12)
    assert bem.representation_residual(sol) < 1e-12
    assert np.isfinite(dirichlet_energy(sol, k=2.5))


def test_bem_rejects_source_and_variable_k():
    with pytest.raises(TypeError):
        BEM1D(0.0, 1.0, 10, k=lambda x: 1.0 + x)
    with pytest.raises(TypeError):
        BEM1D(0.0, 1.0, 10, k=1.0, source=lambda x: 1.0)
    with pytest.raises(ValueError):
        BEM1D(0.0, 1.0, 10, k=1.0).solve(
            neumann(1.0), neumann(2.0)  # inconsistent Neumann pair -> singular
        )


def test_bem_2d_kernel_matches_harmonic_solutions():
    results = [solve_disk_dirichlet(harmonic_linear, harmonic_linear_flux, n=n) for n in (48, 96)]
    assert results[-1].max_rel_error < 5e-2
    assert results[-1].max_rel_error < results[0].max_rel_error
    quad = solve_disk_dirichlet(harmonic_quadratic, harmonic_quadratic_flux, n=96)
    assert quad.max_rel_error < 5e-2


def test_fvm_rejects_undetermined_problem_and_bad_stability():
    with pytest.raises(ValueError):
        FVM1D(0.0, 1.0, 10).solve(neumann(1.0), neumann(1.0))
    with pytest.raises(ValueError):
        FVM1D(0.0, 1.0, 10, alpha=1.0).solve_transient(1e-3, 1.0, lambda t: 0.0, lambda t: 1.0)


# ---------------------------------------------------------------------------
# Coupling face
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case,pair",
    [
        (CASE_VARIABLE_K, ("FVM", "FEM")),
        (CASE_PIECEWISE_K, ("FVM", "BEM")),
        (CASE_PIECEWISE_K, ("FEM", "BEM")),
    ],
)
def test_coupling_converges_and_conserves_flux(case, pair):
    res = run_pair(case, pair)
    assert res.converged
    assert res.final_residual <= RESIDUAL_TOL
    assert res.flux_rel_error <= FLUX_TOL
    assert res.iterations <= 100
    assert res.energy["left_balance_rel_error"] < 1e-10
    assert res.energy["right_balance_rel_error"] < 1e-10


def test_coupling_residual_decreases_monotonically():
    res = run_pair(CASE_VARIABLE_K, ("FVM", "FEM"))
    hist = np.asarray(res.residual_history)
    assert np.all(np.diff(hist) < 1e-3 + 1e-3 * hist[:-1])


def test_coupling_matches_closed_form_interface_trace():
    res = run_pair(CASE_PIECEWISE_K, ("FVM", "BEM"))
    assert res.t_interface == pytest.approx(0.75, abs=1e-10)


def test_aitken_accelerates_fixed_relaxation():
    sol_a, sol_b = build_pair(CASE_PIECEWISE_K, ("FVM", "BEM"))
    common = dict(tol=RESIDUAL_TOL, max_iter=300)
    fast = DirichletNeumannCoupler(
        *build_pair(CASE_PIECEWISE_K, ("FVM", "BEM")), 0.5,
        dirichlet(0.0), dirichlet(1.0), relaxation="aitken", **common,
    ).run()
    slow = DirichletNeumannCoupler(
        *build_pair(CASE_PIECEWISE_K, ("FVM", "BEM")), 0.5,
        dirichlet(0.0), dirichlet(1.0), relaxation=0.5, **common,
    ).run()
    del sol_a, sol_b
    assert fast.iterations < slow.iterations
    assert slow.final_residual <= RESIDUAL_TOL


def test_unconverged_iteration_is_reported_honestly():
    res = DirichletNeumannCoupler(
        *build_pair(CASE_PIECEWISE_K, ("FVM", "BEM")), 0.5,
        dirichlet(0.0), dirichlet(1.0),
        tol=RESIDUAL_TOL, max_iter=3, relaxation=0.5,
    ).run()
    assert not res.converged
    assert res.final_residual > RESIDUAL_TOL


def test_injected_interface_violations_are_detected():
    from services.multi_solver_coupling.verification import flux_closure_controls

    control = flux_closure_controls(CASE_VARIABLE_K, ("FVM", "FEM"))
    assert control["baseline_clean"]
    assert control["sign_flip_detected"] and control["sign_flip_closure"] > 1.5
    assert control["one_percent_detected"] and control["one_percent_closure"] > FLUX_TOL
    # the metric reproduces the injected magnitude (not just a boolean flag)
    assert control["one_percent_closure"] == pytest.approx(0.0099, abs=2e-3)


def test_flux_closure_error_metric():
    ok = flux_closure_error(-2.0, -2.0)
    assert ok["rel_error"] == 0.0
    bad = flux_closure_error(-2.0, 2.0)
    assert bad["rel_error"] == pytest.approx(2.0)
    report = coupled_energy_report(*_simple_solutions(), 0.0, 0.0)
    assert report["interface_flux_rel_error"] <= FLUX_TOL
    with pytest.raises(TypeError):
        coupled_energy_report(  # a single domain-wide source integral is ambiguous
            *_simple_solutions(), source_integral=0.0
        )


def _simple_solutions():
    """Two consistent sub-domains for ``T = 2x`` (q = -2 everywhere)."""
    a = FVM1D(0.0, 0.5, 20, k=1.0).solve(dirichlet(0.0), dirichlet(1.0))
    b = FEM1D(0.5, 1.0, 10, k=1.0).solve(dirichlet(1.0), dirichlet(2.0))
    return a, b


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------


def test_live_contract_validation_covers_all_pairs():
    """Every registered pair is validated against states from a real run."""
    results = {
        ("FVM", "FEM"): run_pair(CASE_VARIABLE_K, ("FVM", "FEM")),
        ("FVM", "BEM"): run_pair(CASE_PIECEWISE_K, ("FVM", "BEM")),
        ("FEM", "BEM"): run_pair(CASE_PIECEWISE_K, ("FEM", "BEM")),
    }
    for pair, res in results.items():
        contract = C.get_contract(pair)
        state_a, state_b = interface_states(res)
        assert contract.validate(state_a, state_b).ok
        control = tamper_controls(pair, res)
        assert control["missing_field_detected"]
        assert control["unit_mismatch_detected"]
        assert control["sign_flip_detected"]


def test_refinement_is_second_order():
    refined = refinement_study(levels=(16, 32, 64))
    assert refined["observed_order_T"] == pytest.approx(2.0, abs=0.35)
    assert refined["observed_order_q"] == pytest.approx(2.0, abs=0.35)
    assert refined["rows"][-1]["q_rel_error"] < 1e-3


def test_verification_run_passes_machine_criteria():
    payload = run_all(refine=False)
    c = payload["criteria"]
    assert c["c1_energy_flux_conservation"]["pass"], c["c1_energy_flux_conservation"]
    assert c["c2_contract_standardization"]["pass"], c["c2_contract_standardization"]
    assert c["c3_coupling_convergence"]["pass"], c["c3_coupling_convergence"]
    assert c["c2_contract_standardization"]["n_pairs"] >= 3
    assert c["c1_energy_flux_conservation"]["negative_control"]["detected"]
    assert c["c1_energy_flux_conservation"]["max_coupled_global_balance_rel_error"] <= FLUX_TOL
    assert payload["module_sha256"], "provenance fingerprints must be present"