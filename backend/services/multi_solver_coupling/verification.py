"""End-to-end verification of multi-solver coupling energy conservation.

Three verification faces, each with a machine-checkable number:

============  =====================================================  ===========
face          measurement                                            bound
============  =====================================================  ===========
energy flux   ``max_pairs |q_A - q_B| / max(|q_A|,|q_B|)`` at the      <= 1e-6
              shared interface, *plus* a coupled global balance
              ``q(b) - q(a) = integral(f)``
contract      live contract validation for every registered solver     >= 3 pairs,
              pair (>= 3), plus negative controls proving the          coverage 1.0
              validator detects missing field / wrong unit / sign
convergence   interface residual of the Dirichlet--Neumann iteration  <= 1e-8
============  =====================================================  ===========

Nothing here is empirical: the reference is the closed-form solution of the
conduction problem, so the numbers are reproducible analytic comparisons.
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .contracts import (
    InterfaceState,
    audit_coverage,
    get_contract,
    list_pairs,
)
from .coupling import CouplingResult, DirichletNeumannCoupler
from .energy import flux_closure_error, global_energy_balance
from .solvers.base import dirichlet
from .solvers.bem2d import (
    harmonic_linear,
    harmonic_linear_flux,
    harmonic_quadratic,
    harmonic_quadratic_flux,
    solve_disk_dirichlet,
)
from .solvers.base import import_solver

FLUX_TOL = 1e-6
RESIDUAL_TOL = 1e-8
MIN_PAIRS = 3

# ---------------------------------------------------------------------------
# Analytic test cases
# ---------------------------------------------------------------------------

E = float(np.e)

#: ``k(x) = 1 + x``, ``f(x) = -(2+x) exp(x)``, exact ``T = exp(x)``.
#: (Check: ``q = -(1+x) e^x``, ``dq/dx = -(2+x) e^x = f``.)
CASE_VARIABLE_K: Dict[str, object] = {
    "name": "variable_k_with_source",
    "description": "k(x)=1+x, f(x)=-(2+x)e^x, exact T=exp(x)",
    "domain": (0.0, 1.0),
    "interface": 0.5,
    "k_left": (lambda x: 1.0 + x),
    "k_right": (lambda x: 1.0 + x),
    "source_left": (lambda x: -(2.0 + x) * np.exp(x)),
    "source_right": (lambda x: -(2.0 + x) * np.exp(x)),
    "left_bc": 1.0,
    "right_bc": E,
    "exact_T": (lambda x: np.exp(x)),
    "exact_q": (lambda x: -(1.0 + x) * np.exp(x)),
    "pairs": [("FVM", "FEM")],
    "n_left": 100,
    "n_right": 50,
}

#: Piecewise-constant conductivity, no source: interface trace and flux have a
#: closed form, so the BEM side (which cannot carry a source) is exact.
K_LEFT, K_RIGHT = 1.0, 3.0
T_INTERFACE_EXACT = K_RIGHT / (K_LEFT + K_RIGHT)          # 0.75
Q_EXACT = -K_LEFT * (T_INTERFACE_EXACT - 0.0) / 0.5        # -1.5
CASE_PIECEWISE_K: Dict[str, object] = {
    "name": "piecewise_k_no_source",
    "description": "k=1 on [0,0.5], k=3 on [0.5,1], T(0)=0, T(1)=1, exact T_I=0.75, q=-1.5",
    "domain": (0.0, 1.0),
    "interface": 0.5,
    "k_left": K_LEFT,
    "k_right": K_RIGHT,
    "source_left": None,
    "source_right": None,
    "left_bc": 0.0,
    "right_bc": 1.0,
    "exact_T": lambda x: np.where(np.asarray(x) <= 0.5, 2.0 * T_INTERFACE_EXACT * np.asarray(x),
                                  T_INTERFACE_EXACT + (np.asarray(x) - 0.5) * (1.0 - T_INTERFACE_EXACT) / 0.5),
    "exact_q": (lambda x: np.full_like(np.asarray(x, dtype=float), Q_EXACT)),
    "pairs": [("FVM", "BEM"), ("FEM", "BEM")],
    "n_left": 100,
    "n_right": 50,
}

CASES: List[Dict[str, object]] = [CASE_VARIABLE_K, CASE_PIECEWISE_K]


def build_pair(case: Dict[str, object], pair: Tuple[str, str], n_left=None, n_right=None):
    """Instantiate the two sub-domain solvers of one pair for a case."""
    a, b = case["domain"]           # type: ignore[misc]
    xi = float(case["interface"])   # type: ignore[arg-type]
    fam_a, fam_b = pair
    n_left = int(n_left if n_left is not None else case["n_left"])    # type: ignore[arg-type]
    n_right = int(n_right if n_right is not None else case["n_right"])  # type: ignore[arg-type]
    sol_a = import_solver(
        fam_a, a, xi, n_left, k=case["k_left"], source=case["source_left"]
    )
    sol_b = import_solver(
        fam_b, xi, b, n_right, k=case["k_right"], source=case["source_right"]
    )
    return sol_a, sol_b


def run_pair(
    case: Dict[str, object],
    pair: Tuple[str, str],
    tol: float = RESIDUAL_TOL,
    max_iter: int = 200,
    n_left=None,
    n_right=None,
) -> CouplingResult:
    sol_a, sol_b = build_pair(case, pair, n_left=n_left, n_right=n_right)
    coupler = DirichletNeumannCoupler(
        left_solver=sol_a,
        right_solver=sol_b,
        interface=float(case["interface"]),     # type: ignore[arg-type]
        left_bc=dirichlet(float(case["left_bc"])),    # type: ignore[arg-type]
        right_bc=dirichlet(float(case["right_bc"])),  # type: ignore[arg-type]
        tol=tol,
        max_iter=max_iter,
    )
    return coupler.run()


# ---------------------------------------------------------------------------
# Contract face: live interface states + negative controls
# ---------------------------------------------------------------------------


def _alias_keys(pair: Tuple[str, str]):
    """Per-pair mapping ``solver -> {canonical name: key used on the wire}``."""
    contract = get_contract(pair)
    out: Dict[str, Dict[str, str]] = {}
    for solver in pair:
        names: Dict[str, str] = {}
        for spec in contract.state_fields + contract.flux_fields:
            names[spec.name] = contract.field_aliases.get(spec.name, {}).get(
                solver, spec.name
            )
        out[solver] = names
    return out


def interface_states(result: CouplingResult) -> Tuple[InterfaceState, InterfaceState]:
    """Build the two live interface states from a coupled run.

    Values are keyed by the *solver-specific* alias from the contract, so the
    validation really exercises the alias resolution instead of trivially hitting
    canonical names.
    """
    if result.left is None or result.right is None:
        raise ValueError("result carries no solutions")
    keys = _alias_keys(result.pair)
    sol_a, sol_b = result.left, result.right
    state_a = InterfaceState(
        solver=sol_a.solver,
        fields={
            keys[sol_a.solver]["temperature"]: np.array([sol_a.t_right]),
            keys[sol_a.solver]["heat_flux"]: np.array([sol_a.q_right]),
        },
        units={
            keys[sol_a.solver]["temperature"]: "K",
            keys[sol_a.solver]["heat_flux"]: "W/m",
        },
    )
    state_b = InterfaceState(
        solver=sol_b.solver,
        fields={
            keys[sol_b.solver]["temperature"]: np.array([sol_b.t_left]),
            keys[sol_b.solver]["heat_flux"]: np.array([sol_b.q_left]),
        },
        units={
            keys[sol_b.solver]["temperature"]: "K",
            keys[sol_b.solver]["heat_flux"]: "W/m",
        },
    )
    return state_a, state_b


def tamper_controls(pair: Tuple[str, str], result: CouplingResult) -> Dict[str, object]:
    """Negative controls: the validator must reject three injected defects."""
    contract = get_contract(pair)
    state_a, state_b = interface_states(result)
    keys = _alias_keys(pair)

    baseline = contract.validate(state_a, state_b)

    # 1) a solver stops reporting the flux field
    b_missing = InterfaceState(
        solver=state_b.solver, fields=dict(state_b.fields), units=dict(state_b.units)
    )
    b_missing.fields.pop(keys[state_b.solver]["heat_flux"], None)
    rep_missing = contract.validate(state_a, b_missing)

    # 2) unit mismatch (mm instead of m in the flux unit)
    b_unit = InterfaceState(
        solver=state_b.solver, fields=dict(state_b.fields), units=dict(state_b.units)
    )
    b_unit.units[keys[state_b.solver]["heat_flux"]] = "W/mm"
    rep_unit = contract.validate(state_a, b_unit)

    # 3) the classic coupling bug: own-outward normal instead of canonical +x
    b_sign = InterfaceState(
        solver=state_b.solver,
        fields=dict(state_b.fields),
        units=dict(state_b.units),
    )
    flux_key = keys[state_b.solver]["heat_flux"]
    b_sign.fields[flux_key] = -state_b.fields[flux_key]
    rep_sign = contract.validate(state_a, b_sign)

    return {
        "pair": list(pair),
        "baseline_ok": baseline.ok,
        "missing_field_detected": (not rep_missing.ok) and bool(rep_missing.missing_b),
        "unit_mismatch_detected": (not rep_unit.ok) and bool(rep_unit.unit_mismatches),
        "sign_flip_detected": (not rep_sign.ok) and bool(rep_sign.sign_violations),
        "sign_flip_closure": float(
            abs(state_a.fields[keys[state_a.solver]["heat_flux"]][0]
                - b_sign.fields[flux_key][0])
            / max(abs(state_a.fields[keys[state_a.solver]["heat_flux"]][0]), 1e-300)
        ),
        "details": {
            "baseline": baseline.as_dict(),
            "missing_field": rep_missing.as_dict(),
            "unit_mismatch": rep_unit.as_dict(),
            "sign_flip": rep_sign.as_dict(),
        },
    }


# ---------------------------------------------------------------------------
# Refinement study (analytic accuracy of the coupled solution)
# ---------------------------------------------------------------------------


def solver_accuracy(
    case: Dict[str, object] = CASE_VARIABLE_K, pair: Tuple[str, str] = ("FVM", "FEM")
) -> Dict[str, object]:
    """Pointwise analytic accuracy of both sub-domain solvers at the production mesh."""
    res = run_pair(case, pair)
    rows = []
    for side, sol in (("A", res.left), ("B", res.right)):
        exact = np.asarray(case["exact_T"](sol.x))     # type: ignore[operator]
        scale = max(float(np.max(np.abs(exact))), 1e-30)
        rows.append(
            {
                "side": side,
                "solver": sol.solver,
                "n_samples": int(len(sol.x)),
                "max_abs_error": float(np.max(np.abs(sol.T - exact))),
                "max_rel_error": float(np.max(np.abs(sol.T - exact)) / scale),
                "mean_abs_error": float(np.mean(np.abs(sol.T - exact))),
            }
        )
    return {"case": case["name"], "pair": list(pair), "rows": rows}


def refinement_study(
    case: Dict[str, object] = CASE_VARIABLE_K,
    pair: Tuple[str, str] = ("FVM", "FEM"),
    levels: Tuple[int, ...] = (8, 16, 32, 64, 128),
) -> Dict[str, object]:
    """Interface trace/flux error vs mesh refinement, plus realised order."""
    xi = float(case["interface"])                       # type: ignore[arg-type]
    t_exact = float(case["exact_T"](xi))                # type: ignore[operator]
    q_exact = float(case["exact_q"](xi))                # type: ignore[operator]
    rows = []
    for n in levels:
        res = run_pair(case, pair, n_left=n, n_right=max(2, n // 2))
        t_err = abs(res.t_interface - t_exact) / abs(t_exact)
        q_err = abs(0.5 * (res.left.q_right + res.right.q_left) - q_exact) / abs(q_exact)
        rows.append(
            {
                "n_left": n,
                "n_right": max(2, n // 2),
                "h": 0.5 / n,
                "t_interface": res.t_interface,
                "t_rel_error": t_err,
                "q_interface": 0.5 * (res.left.q_right + res.right.q_left),
                "q_rel_error": q_err,
                "final_residual": res.final_residual,
                "iterations": res.iterations,
            }
        )

    def order(key: str) -> Optional[float]:
        h = np.array([r["h"] for r in rows], dtype=float)
        e = np.array([r[key] for r in rows], dtype=float)
        ok = e > 0
        if ok.sum() < 2:
            return None
        p = np.polyfit(np.log(h[ok]), np.log(e[ok]), 1)[0]
        return float(p)

    return {
        "case": case["name"],
        "pair": list(pair),
        "interface": xi,
        "t_exact": t_exact,
        "q_exact": q_exact,
        "rows": rows,
        "observed_order_T": order("t_rel_error"),
        "observed_order_q": order("q_rel_error"),
    }


# ---------------------------------------------------------------------------
# BEM kernel validation (evidence that the BEM family is a real method)
# ---------------------------------------------------------------------------


def bem_kernel_validation(panels: Tuple[int, ...] = (32, 64, 128)) -> Dict[str, object]:
    rows = []
    for n in panels:
        lin = solve_disk_dirichlet(harmonic_linear, harmonic_linear_flux, n=n)
        quad = solve_disk_dirichlet(harmonic_quadratic, harmonic_quadratic_flux, n=n)
        rows.append(
            {
                "panels": n,
                "linear_max_rel_error": lin.max_rel_error,
                "linear_rms_rel_error": lin.rms_rel_error,
                "quadratic_max_rel_error": quad.max_rel_error,
                "quadratic_rms_rel_error": quad.rms_rel_error,
            }
        )
    return {
        "method": "constant-panel BEM, interior Dirichlet -> Neumann, unit disk",
        "rows": rows,
        "best_max_rel_error": min(
            min(r["linear_max_rel_error"], r["quadratic_max_rel_error"]) for r in rows
        ),
    }


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def _git_info(root: Path) -> Dict[str, str]:
    def run(args: List[str]) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=20
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    return {
        "commit": run(["rev-parse", "HEAD"]),
        "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": run(["status", "--porcelain"])[:2000],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module_fingerprints() -> Dict[str, str]:
    pkg = Path(__file__).resolve().parent
    files: Dict[str, str] = {}
    for path in sorted(pkg.rglob("*.py")):
        files[str(path.relative_to(pkg))] = _sha256(path)
    cfg = pkg.resolve().parents[2] / "config" / "multi_solver_contracts.json"
    if cfg.is_file():
        files["../../config/multi_solver_contracts.json"] = _sha256(cfg)
    return files


# ---------------------------------------------------------------------------
# Full run
# ---------------------------------------------------------------------------


def run_all(pytest_xml: Optional[Path] = None, refine: bool = True) -> Dict[str, object]:
    root = Path(__file__).resolve().parents[3]

    # -- face 1 + face 3: coupled runs over every registered pair -----------
    per_pair: List[Dict[str, object]] = []
    results: Dict[Tuple[str, str], CouplingResult] = {}
    for case in CASES:
        for pair in case["pairs"]:  # type: ignore[union-attr]
            pair = tuple(pair)  # type: ignore[assignment]
            res = run_pair(case, pair)
            results[pair] = res
            sol_a, sol_b = res.left, res.right
            src_discrete = float(res.energy["left_source_integral"]) + float(
                res.energy["right_source_integral"]
            )
            src_analytic = _source_integral(case)
            coupled_balance = global_energy_balance(
                _union_solution(sol_a, sol_b), src_discrete
            )
            quadrature_gap = (
                abs(src_discrete - src_analytic) / abs(src_analytic)
                if src_analytic
                else 0.0
            )
            per_pair.append(
                {
                    "pair": list(pair),
                    "case": case["name"],
                    "interface_flux_rel_error": res.flux_rel_error,
                    "final_residual": res.final_residual,
                    "iterations": res.iterations,
                    "converged": res.converged,
                    "t_interface": res.t_interface,
                    "q_interface": 0.5 * (sol_a.q_right + sol_b.q_left),
                    "coupled_global_balance_rel_error": coupled_balance["rel_error"],
                    "source_integral_discrete": src_discrete,
                    "source_integral_analytic": src_analytic,
                    "source_quadrature_gap_rel": quadrature_gap,
                    "monotone_decrease": _monotone(res.residual_history),
                }
            )

    flux_errors = [r["interface_flux_rel_error"] for r in per_pair]
    residuals = [r["final_residual"] for r in per_pair]
    balances = [r["coupled_global_balance_rel_error"] for r in per_pair]

    # negative control for face 1: injected sign flip and 1 % transfer error
    flip = flux_closure_controls(CASE_VARIABLE_K, ("FVM", "FEM"))

    c1 = {
        "criterion": "cross-solver energy-flux conservation",
        "threshold": FLUX_TOL,
        "max_rel_error": float(max(flux_errors)),
        "max_coupled_global_balance_rel_error": float(max(balances)),
        "max_source_quadrature_gap_rel": float(
            max(r["source_quadrature_gap_rel"] for r in per_pair)
        ),
        "pass": bool(max(flux_errors) <= FLUX_TOL and max(balances) <= FLUX_TOL),
        "per_pair": per_pair,
        "negative_control": flip,
    }

    # -- face 2: contract standardization ----------------------------------
    provider_cache: Dict[Tuple[str, str], CouplingResult] = results

    def provider(pair: Tuple[str, str]):
        return interface_states(provider_cache[tuple(pair)])

    coverage = audit_coverage(provider=provider)
    tamper = [tamper_controls(tuple(p), provider_cache[tuple(p)]) for p in provider_cache]
    tamper_ok = all(
        t["baseline_ok"]
        and t["missing_field_detected"]
        and t["unit_mismatch_detected"]
        and t["sign_flip_detected"]
        for t in tamper
    )
    bem = bem_kernel_validation()
    c2 = {
        "criterion": "interface-contract standardization",
        "min_pairs": MIN_PAIRS,
        "n_pairs": coverage["n_pairs"],
        "coverage_live": coverage["coverage"],
        "coverage": coverage,
        "negative_controls": tamper,
        "negative_controls_all_detected": bool(tamper_ok),
        "bem_kernel_validation": bem,
        "pass": bool(
            coverage["n_pairs"] >= MIN_PAIRS
            and coverage["coverage"] >= 1.0
            and tamper_ok
        ),
    }

    # -- face 3: coupling convergence --------------------------------------
    c3 = {
        "criterion": "coupling-iteration convergence",
        "threshold": RESIDUAL_TOL,
        "max_final_residual": float(max(residuals)),
        "pass": bool(max(residuals) <= RESIDUAL_TOL and all(r["converged"] for r in per_pair)),
        "per_pair": [
            {
                "pair": r["pair"],
                "final_residual": r["final_residual"],
                "iterations": r["iterations"],
                "monotone_decrease": r["monotone_decrease"],
            }
            for r in per_pair
        ],
    }

    # -- face 4: regression suite ------------------------------------------
    c4 = _pytest_summary(pytest_xml)

    refined = refinement_study() if refine else None
    accuracy = {case["name"]: solver_accuracy(case, tuple(case["pairs"][0]))
                for case in CASES}

    payload: Dict[str, object] = {
        "schema": "multi_solver_coupling_verification/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "git": _git_info(root),
        "module_sha256": module_fingerprints(),
        "contracts": {
            "pairs": [list(p) for p in list_pairs()],
        },
        "criteria": {
            "c1_energy_flux_conservation": c1,
            "c2_contract_standardization": c2,
            "c3_coupling_convergence": c3,
            "c4_regression_tests": c4,
        },
        "refinement_study": refined,
        "solver_accuracy": accuracy,
        "all_pass": bool(c1["pass"] and c2["pass"] and c3["pass"]),
    }
    return payload


# ---------------------------------------------------------------------------
# helpers used above
# ---------------------------------------------------------------------------


def _source_integral(case: Dict[str, object]) -> float:
    """Volume integral of the source over the *whole* coupled domain."""
    a, b = case["domain"]      # type: ignore[misc]
    xi = float(case["interface"])
    ks = case["source_left"]
    if ks is None:
        return 0.0
    x1 = np.linspace(a, xi, 2001)
    x2 = np.linspace(xi, b, 2001)
    f1 = np.array([float(ks(x)) for x in x1])   # type: ignore[operator]
    f2 = np.array([float(ks(x)) for x in x2])   # type: ignore[operator]
    return float(np.trapezoid(f1, x1) + np.trapezoid(f2, x2))


def _union_solution(sol_a, sol_b):
    """A synthetic ``Solution`` covering the whole domain (for balance checks)."""
    from .solvers.base import Solution

    x = np.concatenate([sol_a.x, sol_b.x])
    T = np.concatenate([sol_a.T, sol_b.T])
    order = np.argsort(x)
    return Solution(
        solver=f"{sol_a.solver}+{sol_b.solver}",
        a=sol_a.a,
        b=sol_b.b,
        x=x[order],
        T=T[order],
        q_left=sol_a.q_left,
        q_right=sol_b.q_right,
        t_left=sol_a.t_left,
        t_right=sol_b.t_right,
    )


def _monotone(history: List[float]) -> bool:
    arr = np.asarray(history, dtype=float)
    if arr.size < 2:
        return True
    return bool(np.all(np.diff(arr) <= 1e-12 + 1e-3 * arr[:-1]))


def flux_closure_controls(
    case: Dict[str, object], pair: Tuple[str, str]
) -> Dict[str, object]:
    """Calibrate the flux-closure metric against known injected violations.

    A converged run gives ``q_A == q_B`` to roundoff; the metric is then fed
    (i) the own-outward sign convention and (ii) a 1 % data-transfer error.  If
    the reported closure did not reproduce the injected magnitude, the metric
    would be vacuous -- this is the falsifiability control for face 1.
    """
    res = run_pair(case, pair)
    q_a = float(res.left.q_right)
    q_b = float(res.right.q_left)
    baseline = flux_closure_error(q_a, q_b)["rel_error"]
    flipped = flux_closure_error(q_a, -q_b)["rel_error"]
    perturbed = flux_closure_error(q_a, 1.01 * q_b)["rel_error"]
    return {
        "description": "injected interface violations vs the measured closure metric",
        "pair": list(pair),
        "case": case["name"],
        "q_a": q_a,
        "q_b": q_b,
        "baseline_closure": baseline,
        "sign_flip_closure": flipped,
        "one_percent_closure": perturbed,
        "baseline_clean": bool(baseline <= FLUX_TOL),
        "sign_flip_detected": bool(flipped > FLUX_TOL),
        "one_percent_detected": bool(perturbed > FLUX_TOL),
        "detected": bool(baseline <= FLUX_TOL and flipped > FLUX_TOL and perturbed > FLUX_TOL),
    }


def _pytest_summary(xml: Optional[Path]) -> Dict[str, object]:
    if xml is None or not Path(xml).is_file():
        return {
            "criterion": "regression tests",
            "status": "not_run",
            "pass_rate": None,
            "source": None,
        }
    import xml.etree.ElementTree as ET

    root = ET.parse(xml).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
    executed = max(tests - skipped, 1)
    return {
        "criterion": "regression tests",
        "status": "run",
        "source": str(xml),
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "pass_rate": (tests - failures - errors - skipped) / executed,
        "pass": bool(failures == 0 and errors == 0),
    }