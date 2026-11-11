"""One machine verdict for the coupled-loop / conservation maturity node.

The node's acceptance criteria are seven statements, (a)..(g).  Each of them is
already computable from the modules next to this file — what was missing is a
single artefact that runs all of them, records the *numbers* next to the
thresholds, and states pass/fail per criterion so a gate (or a reviewer) never
has to re-derive the verdict from prose.

``run_maturity_audit`` returns a plain-JSON record::

    {"criteria": {"a": {...}, ..., "g": {...}},
     "summary": {"passed": [...], "failed": [...], "all_passed": bool},
     "inputs": {...}, "artifacts": {...}}

Run it as a script to write the artefact::

    python -m tools.coupling.maturity_check --out artifacts/<dir>/coupled_loop_maturity.json

Nothing here is domain-specific: the fixtures' quantities are opaque, the test
count is obtained by parsing the test modules with :mod:`ast` (no shell-out), and
the neutrality scan is the platform's shared lexicon scanner.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..coupled_loop import DIVERGED, solve_linear_coupled
from .conservation_check import (
    SegmentMesh,
    cell_averages,
    diffusion_interface_case,
    jitter,
    noisy_operator,
    order_certificate,
    refinement_order,
    relative_residual,
)
from .loop_contract import (
    COUPLED_LOOP_SCHEMA_VERSION,
    contract_gate,
    load_coupled_loop_contract,
    validate_coupled_loop_contract,
)
from .sensitivity import (
    CoupledSystem,
    Discipline,
    linear_two_discipline_system,
    nonlinear_two_discipline_system,
    scale_declared_partials,
    sensitivity_consistency_gate,
)

ROOT = Path(__file__).resolve().parents[2]
PLAIN_CONTRACT = ROOT / "contracts" / "coupled_loop.example.v1.json"
AITKEN_CONTRACT = ROOT / "contracts" / "coupled_loop.example.aitken.v1.json"

CONSERVATION_TOLERANCE = 1e-4
LINEAR_ORDER_GATE = 0.95
QUADRATIC_ORDER_GATE = 1.95
SPECTRAL_DEVIATION_GATE = 0.05
DIVERGENCE_ITERATION_CAP = 10
AITKEN_REDUCTION_GATE = 0.40
SENSITIVITY_RTOL = 0.02
MIN_TEST_COUNT = 20

TEST_MODULES = (
    "tests/test_coupling_sensitivity.py",
    "tests/test_coupled_loop_contract.py",
    "tests/test_coupling_maturity.py",
)
NEUTRALITY_TARGETS = (
    "tools/coupling/sensitivity.py",
    "tools/coupling/loop_contract.py",
    "tools/coupling/maturity_check.py",
    "contracts/coupled_loop.schema.json",
    "contracts/coupled_loop.example.v1.json",
    "contracts/coupled_loop.example.aitken.v1.json",
    "tests/test_coupling_sensitivity.py",
    "tests/test_coupled_loop_contract.py",
    "tests/test_coupling_maturity.py",
)


def _criterion(cid: str, name: str, checks: Dict[str, bool], numbers: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": cid,
        "name": name,
        "passed": bool(all(checks.values())),
        "checks": {k: bool(v) for k, v in checks.items()},
        "numbers": numbers,
    }


# ---------------------------------------------------------------------------
# (a) conservation residual on the two-sided interface
# ---------------------------------------------------------------------------


def criterion_a() -> Dict[str, Any]:
    fixture = diffusion_interface_case(n_x=192, n_y=192)
    residual_l2 = fixture.residual("l2")
    residual_linf = fixture.residual("linf")
    analytic_left = fixture.analytic_error("left")
    analytic_right = fixture.analytic_error("right")
    # negative control: a jittered flux must break the same gate
    corrupted = fixture.flux_left + jitter(fixture.flux_left, amplitude=0.3, seed=7)
    corrupted_residual = relative_residual(fixture.flux_left, corrupted)
    checks = {
        "two_sided_residual_within_1e_4": residual_l2 <= CONSERVATION_TOLERANCE,
        "analytic_flux_left_within_1e_4": analytic_left <= CONSERVATION_TOLERANCE,
        "analytic_flux_right_within_1e_4": analytic_right <= CONSERVATION_TOLERANCE,
        "jittered_flux_breaks_gate": corrupted_residual > CONSERVATION_TOLERANCE,
    }
    return _criterion(
        "a",
        "interface conservation residual vs analytic flux",
        checks,
        {
            "residual_l2": residual_l2,
            "residual_linf": residual_linf,
            "analytic_error_left": analytic_left,
            "analytic_error_right": analytic_right,
            "jittered_residual": corrupted_residual,
            "tolerance": CONSERVATION_TOLERANCE,
            "cells_per_side": int(fixture.flux_left.size),
        },
    )


# ---------------------------------------------------------------------------
# (b) observed refinement order of the interface operators
# ---------------------------------------------------------------------------


def _profile(x: np.ndarray) -> np.ndarray:
    """Smooth analytic field used by the refinement study (opaque to the gate)."""
    return np.sin(2.0 * np.pi * x) + 0.5 * x ** 3


def criterion_b() -> Dict[str, Any]:
    linear = order_certificate(_profile, operator="linear_reconstruction", required_order=LINEAR_ORDER_GATE)
    quadratic = order_certificate(
        _profile, operator="quadratic_projection", required_order=QUADRATIC_ORDER_GATE
    )
    donor = order_certificate(_profile, operator="donor_cell")

    # negative control: a mesh-independent perturbation cannot converge at a rate
    noisy = noisy_operator(amplitude=1e-2, seed=11)
    h: List[float] = []
    residuals: List[float] = []
    for n in (24, 48, 96, 192):
        src = SegmentMesh.uniform(0.0, 1.0, n)
        dst = SegmentMesh.uniform(0.0, 1.0, int(round(1.5 * n)) + 1)
        h.append(float(dst.widths.mean()))
        residuals.append(
            relative_residual(cell_averages(_profile, dst), noisy(cell_averages(_profile, src), src, dst))
        )
    noisy_record = refinement_order(h, residuals)
    checks = {
        "linear_order_at_least_095": linear.observed_order >= LINEAR_ORDER_GATE,
        "quadratic_order_at_least_195": quadratic.observed_order >= QUADRATIC_ORDER_GATE,
        "quadratic_beats_linear": quadratic.observed_order > linear.observed_order,
        "donor_cell_is_first_order": 0.5 <= donor.observed_order < 1.5,
        "noisy_operator_breaks_order_gate": noisy_record["observed_order"] < QUADRATIC_ORDER_GATE,
        "declared_gates_pass_their_certificates": bool(linear.passed and quadratic.passed),
    }
    return _criterion(
        "b",
        "observed convergence order of the interface operators",
        checks,
        {
            "linear_reconstruction_order": linear.observed_order,
            "quadratic_projection_order": quadratic.observed_order,
            "donor_cell_order": donor.observed_order,
            "noisy_operator_order": noisy_record["observed_order"],
            "linear_gate": LINEAR_ORDER_GATE,
            "quadratic_gate": QUADRATIC_ORDER_GATE,
            "levels": list(linear.levels),
        },
    )


# ---------------------------------------------------------------------------
# (c) analytic anchor: measured contraction vs spectral radius; (e) Aitken saving
# ---------------------------------------------------------------------------


def _rho_deviation(measured: float, analytic: float) -> float:
    return float(abs(measured - analytic) / max(abs(analytic), 1e-300))


def criterion_c() -> Dict[str, Any]:
    fixture = linear_two_discipline_system(0.5)
    system = fixture.to_system()
    contract = load_coupled_loop_contract(PLAIN_CONTRACT)
    x, certificate = system.solve(
        [1.0], scheme=contract["scheme"], relaxation=contract.get("relaxation", "none")
    )
    analytic_solution = np.linalg.solve(
        np.eye(fixture.matrix.shape[0]) - fixture.matrix,
        fixture.rhs0 + fixture.rhs1 * 1.0,
    )
    solution_error = float(
        np.linalg.norm(x - analytic_solution) / max(np.linalg.norm(analytic_solution), 1e-300)
    )
    deviation = _rho_deviation(certificate.rho_hat, fixture.spectral_radius)
    loop_verdict = contract_gate(
        contract, certificate, spectral_radius=fixture.spectral_radius
    )
    checks = {
        "loop_converged": bool(certificate.converged),
        "rho_deviation_within_5_percent": deviation <= SPECTRAL_DEVIATION_GATE,
        "solution_matches_direct_solve": solution_error <= 1e-9,
        "contract_loop_gate_passed": bool(loop_verdict["passed"]),
    }
    return _criterion(
        "c",
        "measured contraction rate vs spectral radius (rho = 0.5)",
        checks,
        {
            "rho_hat": float(certificate.rho_hat),
            "spectral_radius": fixture.spectral_radius,
            "rho_deviation": deviation,
            "deviation_gate": SPECTRAL_DEVIATION_GATE,
            "iterations": int(certificate.iterations),
            "solution_error": solution_error,
            "contract_loop_id": contract["loop_id"],
        },
    )


def criterion_e() -> Dict[str, Any]:
    fixture = linear_two_discipline_system(0.85)
    system = fixture.to_system()
    plain = {"iterations": None, "status": None}
    contract = load_coupled_loop_contract(AITKEN_CONTRACT)
    _, cert_plain = system.solve([1.0], scheme=contract["scheme"], relaxation="none")
    _, cert_aitken = system.solve(
        [1.0], scheme=contract["scheme"], relaxation=contract.get("relaxation", "aitken")
    )
    plain["iterations"] = int(cert_plain.iterations)
    plain["status"] = cert_plain.status
    reduction = 1.0 - cert_aitken.iterations / max(cert_plain.iterations, 1)
    xa, _ = system.solve([1.0], relaxation="aitken")
    xp, _ = system.solve([1.0], relaxation="none")
    same_point = float(np.linalg.norm(xa - xp) / max(np.linalg.norm(xp), 1e-300))
    loop_verdict = contract_gate(contract, cert_aitken, unrelaxed_iterations=cert_plain.iterations)
    checks = {
        "aitken_saves_at_least_40_percent": reduction >= AITKEN_REDUCTION_GATE,
        "aitken_reaches_the_same_fixed_point": same_point <= 1e-9,
        "aitken_converged": bool(cert_aitken.converged),
        "contract_relaxation_gate_passed": bool(loop_verdict["passed"]),
    }
    return _criterion(
        "e",
        "Aitken relaxation saving on the same system",
        checks,
        {
            "iterations_plain": int(cert_plain.iterations),
            "iterations_aitken": int(cert_aitken.iterations),
            "iteration_reduction": float(reduction),
            "reduction_gate": AITKEN_REDUCTION_GATE,
            "fixed_point_gap": same_point,
            "rho_spectral": fixture.spectral_radius,
        },
    )


# ---------------------------------------------------------------------------
# (d) divergence negative control
# ---------------------------------------------------------------------------


def criterion_d() -> Dict[str, Any]:
    fixture = linear_two_discipline_system(1.2)
    system = fixture.to_system()
    _, certificate = system.solve([1.0], max_iterations=200)
    contract = load_coupled_loop_contract(PLAIN_CONTRACT)
    loop_verdict = contract_gate(contract, certificate)
    checks = {
        "status_is_diverged": certificate.status == DIVERGED,
        "flagged_within_10_iterations": certificate.iterations <= DIVERGENCE_ITERATION_CAP,
        "not_confused_with_iteration_cap": certificate.status != "MAX_ITERATIONS",
        "contract_gate_rejects": not loop_verdict["passed"],
    }
    return _criterion(
        "d",
        "divergent system (rho = 1.2) flagged early",
        checks,
        {
            "status": certificate.status,
            "iterations": int(certificate.iterations),
            "iteration_cap": DIVERGENCE_ITERATION_CAP,
            "rho_hat": float(certificate.rho_hat),
            "spectral_radius": fixture.spectral_radius,
            "reason": certificate.reason,
        },
    )


# ---------------------------------------------------------------------------
# (f) sensitivity consistency gate, plus its negative controls
# ---------------------------------------------------------------------------


def _corrupted_system(state_scale: float = 1.0, param_scale: float = 1.0) -> CoupledSystem:
    base = linear_two_discipline_system(0.5).to_system()
    disciplines: List[Discipline] = [
        base.disciplines[0],
        scale_declared_partials(base.disciplines[1], state_scale=state_scale, param_scale=param_scale),
    ]
    return CoupledSystem(disciplines=disciplines, output=base.output, name="corrupted_two_discipline")


def criterion_f() -> Dict[str, Any]:
    fixture = linear_two_discipline_system(0.5)
    system = fixture.to_system()
    record = sensitivity_consistency_gate(
        system, [1.0], analytic=fixture.analytic_sensitivity, rtol=SENSITIVITY_RTOL
    )
    nonlinear = sensitivity_consistency_gate(
        nonlinear_two_discipline_system(), [0.5], rtol=SENSITIVITY_RTOL
    )
    control_state = sensitivity_consistency_gate(
        _corrupted_system(state_scale=1.5), [1.0], rtol=SENSITIVITY_RTOL
    )
    control_param = sensitivity_consistency_gate(
        _corrupted_system(param_scale=1.25), [1.0], rtol=SENSITIVITY_RTOL
    )
    contract = load_coupled_loop_contract(PLAIN_CONTRACT)
    _x_star, cert_star = system.solve([1.0])
    sens_verdict = contract_gate(contract, cert_star, sensitivity=record)
    checks = {
        "composed_matches_resolved_within_2_percent": record["relative_error"] <= SENSITIVITY_RTOL,
        "composed_matches_analytic_within_2_percent": (
            record["relative_error_composed_vs_analytic"] is not None
            and record["relative_error_composed_vs_analytic"] <= SENSITIVITY_RTOL
        ),
        "nonlinear_system_consistent": bool(nonlinear["passed"]),
        "corrupted_coupling_jacobian_fails": not control_state["passed"],
        "corrupted_param_derivative_fails": not control_param["passed"],
        "contract_sensitivity_gate_passed": bool(sens_verdict["passed"]),
    }
    return _criterion(
        "f",
        "sensitivity consistency: composed vs re-resolved derivative",
        checks,
        {
            "analytic": record["analytic"],
            "composed": record["composed"],
            "resolved": record["total"],
            "relative_error": record["relative_error"],
            "relative_error_composed_vs_analytic": record["relative_error_composed_vs_analytic"],
            "rtol": SENSITIVITY_RTOL,
            "nonlinear_composed": nonlinear["composed"],
            "nonlinear_resolved": nonlinear["total"],
            "nonlinear_relative_error": nonlinear["relative_error"],
            "corrupted_coupling_relative_error": control_state["relative_error"],
            "corrupted_coupling_passed": bool(control_state["passed"]),
            "corrupted_param_relative_error": control_param["relative_error"],
            "corrupted_param_passed": bool(control_param["passed"]),
            "resolvent_condition": record["detail"]["composed"]["resolvent_condition"],
        },
    )


# ---------------------------------------------------------------------------
# (g) test count and platform neutrality
# ---------------------------------------------------------------------------


def count_tests(root: Path = ROOT, modules=TEST_MODULES) -> Dict[str, int]:
    """Count ``test_*`` functions per module (AST, no execution)."""
    counts: Dict[str, int] = {}
    for rel in modules:
        path = root / rel
        if not path.exists():
            counts[rel] = 0
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        counts[rel] = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        )
    return counts


def criterion_g(root: Path = ROOT) -> Dict[str, Any]:
    counts = count_tests(root)
    total = int(sum(counts.values()))
    schema_errors = validate_coupled_loop_contract(
        json.loads(PLAIN_CONTRACT.read_text(encoding="utf-8"))
    ) + validate_coupled_loop_contract(json.loads(AITKEN_CONTRACT.read_text(encoding="utf-8")))
    try:
        from ..evidence_domain_scan import load_lexicon, scan_paths

        scan = scan_paths(list(NEUTRALITY_TARGETS), lexicon=load_lexicon(), root=root)
        hits = scan["hits"]
        scanned = scan["scanned_files"]
    except Exception as exc:  # pragma: no cover - scanner always present in-tree
        hits, scanned = [{"error": str(exc)}], 0
    checks = {
        "at_least_20_tests": total >= MIN_TEST_COUNT,
        "no_domain_vocabulary": not hits,
        "example_contract_valid": not schema_errors,
        "schema_version_is_coupled_loop_v1": COUPLED_LOOP_SCHEMA_VERSION == "coupled-loop.v1",
    }
    return _criterion(
        "g",
        "test coverage and platform neutrality",
        checks,
        {
            "tests_per_module": counts,
            "tests_total": total,
            "test_floor": MIN_TEST_COUNT,
            "neutrality_scanned_files": scanned,
            "neutrality_hits": hits[:5],
            "schema_errors": schema_errors,
        },
    )


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def run_maturity_audit(root: Path = ROOT) -> Dict[str, Any]:
    """Run criteria (a)..(f) and the static part of (g); return the record."""
    criteria = {
        "a": criterion_a(),
        "b": criterion_b(),
        "c": criterion_c(),
        "d": criterion_d(),
        "e": criterion_e(),
        "f": criterion_f(),
        "g": criterion_g(root),
    }
    passed = [cid for cid, rec in criteria.items() if rec["passed"]]
    failed = [cid for cid, rec in criteria.items() if not rec["passed"]]
    return {
        "node": "P350W2-N09",
        "kind": "coupled-loop-and-conservation-maturity",
        "criteria": criteria,
        "summary": {
            "passed": passed,
            "failed": failed,
            "all_passed": not failed,
            "n_passed": len(passed),
            "n_total": len(criteria),
        },
        "inputs": {
            "schema": "contracts/coupled_loop.schema.json",
            "example_contract": "contracts/coupled_loop.example.v1.json",
            "example_contract_aitken": "contracts/coupled_loop.example.aitken.v1.json",
            "conservation_schema": "contracts/conservation_law.schema.json",
        },
        "artifacts": {
            "record": "artifacts/p350w2_n09/coupled_loop_maturity.json",
            "figures": "artifacts/p350w2_n09/",
            "tests": list(TEST_MODULES),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(ROOT / "artifacts" / "p350w2_n09" / "coupled_loop_maturity.json"),
        help="path of the JSON verdict artefact",
    )
    args = parser.parse_args(argv)
    record = run_maturity_audit()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = record["summary"]
    print(
        f"criteria passed {summary['n_passed']}/{summary['n_total']}"
        f" -> {out}"
    )
    if summary["failed"]:
        for cid in summary["failed"]:
            print(f"  FAILED {cid}: {record['criteria'][cid]['checks']}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())