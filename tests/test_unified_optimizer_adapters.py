"""Unified optimizer -- engine adapters (SciPy / NLopt / pyoptsparse interface).

Covers criteria C2 (SciPy on Rosenbrock < 1e-6), C3 (NLopt on G06 < 0.1%),
C4-style KKT quality on an equality+inequality problem, the explicit
``unsupported`` / ``unavailable`` outcomes, and the pyoptsparse API path via a
stub module (pyoptsparse itself is not installable on this platform).
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from tools.optimizer.unified import (
    ENGINES,
    AdapterUnavailable,
    CompiledProblem,
    NLoptAdapter,
    PyOptSparseAdapter,
    ScipyAdapter,
    algorithm_matrix,
    available_engines,
    get_adapter,
    get_problem,
    probe_all,
    probe_requested_algorithms,
    solve,
)


def test_registry_lists_the_three_engines():
    assert set(ENGINES) == {"scipy", "nlopt", "pyoptsparse"}
    assert "scipy" in available_engines()
    assert "nlopt" in available_engines()


def test_probe_reports_missing_sboa_honestly():
    probe = NLoptAdapter.probe()
    assert probe.available
    assert "SBOA" in probe.missing_algorithms
    assert "COBYLA" in probe.algorithms and "MMA" in probe.algorithms


def test_requested_algorithm_matrix_covers_interface_names():
    table = probe_requested_algorithms()
    assert set(table) >= {"SLSQP", "COBYLA", "MMA", "SNOPT"}
    assert "nlopt" in table["COBYLA"]
    assert "nlopt" in table["MMA"]
    # pyoptsparse algorithms resolve to no available engine here
    assert table["SNOPT"] == []
    rows = algorithm_matrix()
    assert any(row["engine"] == "nlopt" and row["algorithm"] == "MMA" and row["available"] for row in rows)


def test_scipy_rosenbrock_reaches_global_optimum():
    spec = get_problem("rosenbrock_2d")
    result = solve(spec, engine="scipy", algorithm="SLSQP")
    assert result.status == "converged"
    assert abs(result.objective) < 1e-6
    assert abs(result.x["x"] - 1.0) < 1e-3 and abs(result.x["y"] - 1.0) < 1e-3


def test_scipy_lbfgsb_and_nelder_mead_agree_on_rosenbrock():
    spec = get_problem("rosenbrock_2d")
    lbfgs = solve(spec, engine="scipy", algorithm="L-BFGS-B")
    nm = solve(spec, engine="scipy", algorithm="Nelder-Mead")
    assert lbfgs.status == "converged" and nm.status == "converged"
    assert abs(lbfgs.objective - nm.objective) < 1e-6


def test_nlopt_cobyla_g06_reaches_published_optimum():
    spec = get_problem("g06")
    result = solve(spec, engine="nlopt", algorithm="COBYLA", options={"max_eval": 5000})
    assert result.status == "converged"
    ref = spec.reference["objective"]
    assert abs(result.objective - ref) / abs(ref) < 1e-3
    assert result.constraint_violation < 1e-6


def test_nlopt_lbfgs_rosenbrock():
    spec = get_problem("rosenbrock_2d")
    result = solve(spec, engine="nlopt", algorithm="LBFGS")
    assert result.status == "converged"
    assert abs(result.objective) < 1e-8


def test_nlopt_handles_equality_and_inequality_together():
    spec = get_problem("sphere_eq_ineq")
    for algo in ("SLSQP", "AUGLAG"):
        result = solve(spec, engine="nlopt", algorithm=algo, options={"max_eval": 5000})
        assert result.status == "converged", (algo, result.message)
        assert abs(result.objective - 3.0) < 1e-6
        assert result.constraint_violation < 1e-9
        assert result.kkt["kkt_residual"] < 1e-4


def test_nlopt_inequality_sign_convention_is_respected():
    """Regression for the NLopt <=0 vs SciPy >=0 convention trap.

    A constraint that is violated at the start must not be "solved" by ignoring
    it: the returned point has to be feasible for the canonical g >= 0 form.
    """
    spec = get_problem("g08")
    result = solve(spec, engine="nlopt", algorithm="SLSQP", options={"max_eval": 5000})
    assert result.status == "converged"
    assert result.constraint_violation < 1e-6


def test_unsupported_algorithm_problem_combination_is_explicit():
    spec = get_problem("g06")  # constrained
    result = solve(spec, engine="scipy", algorithm="Nelder-Mead")
    assert result.status == "unsupported"
    assert "cannot handle" in result.message
    assert not result.success


def test_unknown_engine_and_algorithm_are_reported_not_raised():
    spec = get_problem("rosenbrock_2d")
    unknown_engine = solve(spec, engine="ipopt-cli", algorithm="SLSQP")
    assert unknown_engine.status == "unavailable"
    unknown_algo = solve(spec, engine="nlopt", algorithm="SBOA")
    assert unknown_algo.status == "unavailable"
    assert "SBOA" in unknown_algo.message


def test_pyoptsparse_engine_is_unavailable_and_claims_no_number():
    assert not PyOptSparseAdapter.available()
    probe = PyOptSparseAdapter.probe()
    assert probe.available is False
    assert set(probe.missing_algorithms) >= {"SNOPT", "IPOPT"}
    spec = get_problem("sphere_eq_ineq")
    result = solve(spec, engine="pyoptsparse", algorithm="SNOPT")
    assert result.status == "unavailable"
    doc = result.to_dict()
    assert doc["objective"] is None                     # no fabricated optimum
    assert all(v is None for v in doc["x"].values())


def test_pyoptsparse_adapter_api_path_with_stub_module(monkeypatch):
    """Exercise addVar/addObj/addCon/sens/optInform against a stub pyoptsparse.

    This is an interface-contract test: the stub solves the recorded problem with
    SciPy.  It proves the adapter speaks pyoptsparse's API correctly; it is *not*
    evidence that SNOPT produces numbers on this machine.
    """
    from scipy.optimize import minimize

    class StubOptimization:
        def __init__(self, name, objfunc):
            self.name = name
            self.objfunc = objfunc
            self.vars = {}
            self.cons = {}

        def addVar(self, name, vartype="c", value=0.0, lower=-1e30, upper=1e30):
            self.vars[name] = {"value": value, "lower": lower, "upper": upper}

        def addObj(self, name, **kwargs):
            self.obj_name = name

        def addCon(self, name, conType="e", lower=None, upper=None):
            self.cons[name] = {"type": conType, "lower": lower, "upper": upper}

    class StubOPT:
        def __init__(self, name, options=None):
            self.name = name

        def __call__(self, opt_prob, sens=None, **kwargs):
            names = list(opt_prob.vars)

            def f(x):
                return float(opt_prob.objfunc(dict(zip(names, x)))["obj"])

            cons = []
            for cname, spec in opt_prob.cons.items():
                cons.append(
                    {
                        "type": "eq" if spec["type"] == "e" else "ineq",
                        "fun": (lambda z, c=cname: float(opt_prob.objfunc(dict(zip(names, z)))[c])),
                    }
                )
            res = minimize(
                f,
                np.array([opt_prob.vars[n]["value"] for n in names]),
                method="SLSQP",
                bounds=[(opt_prob.vars[n]["lower"], opt_prob.vars[n]["upper"]) for n in names],
                constraints=cons,
            )
            return types.SimpleNamespace(
                xStar=dict(zip(names, res.x.tolist())),
                fStar={opt_prob.obj_name: float(res.fun)},
                optInform={"value": 1 if res.success else 2},
            )

    stub = types.ModuleType("pyoptsparse")
    stub.Optimization = StubOptimization
    stub.OPT = StubOPT
    stub.__version__ = "stub-0.1"
    monkeypatch.setitem(sys.modules, "pyoptsparse", stub)

    spec = get_problem("sphere_eq_ineq")
    result = solve(spec, engine="pyoptsparse", algorithm="SNOPT")
    assert result.status == "converged"
    assert result.objective == pytest.approx(3.0, abs=1e-6)
    assert result.constraint_violation < 1e-8
    assert result.engine == "pyoptsparse" and result.algorithm == "SNOPT"


def test_engine_adapter_instances_expose_algorithm_specs():
    adapter = get_adapter("nlopt")
    spec = adapter.algorithm("mma")
    assert spec.name == "MMA" and spec.ineq and not spec.eq
    with pytest.raises(AdapterUnavailable):
        adapter.algorithm("does-not-exist")