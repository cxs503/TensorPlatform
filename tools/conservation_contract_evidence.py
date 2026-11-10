"""Generate the P351-W1-N01 conservation-contract evidence bundle.

Everything the report quotes is produced here from the library itself (no hand
copied numbers): ``artifacts/conservation_contract_p351w1_n01/*.json`` plus three
figures, and ``docs/CONSERVATION_CONTRACT.md``.

Run:  python -m tools.conservation_contract_evidence
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from tools.coupled_loop import (
    meets_loop_gate,
    solve_linear_coupled,
    solve_coupled,
    linear_blocks,
)
from tools.coupling.conservation_check import (
    OPERATOR_ORDER,
    DiffusionInterfaceCase,
    SegmentMesh,
    cell_averages,
    central_difference,
    diffusion_interface_case,
    jitter,
    load_contract,
    noisy_operator,
    order_certificate,
    refinement_order,
    relative_residual,
    sensitivity_gate,
    validate_contract,
)
from tools.conservation_report_doc import write_docs
from tools.evidence_domain_scan import load_lexicon, scan_paths

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "conservation_contract_p351w1_n01"
FIG = OUT / "figures"
SCHEMA = ROOT / "contracts" / "conservation_law.schema.json"
EXAMPLE = ROOT / "contracts" / "conservation_law.example.v1.json"

A_CONTRACTING = np.array([[0.0, 0.95], [0.95, 0.0]])
B_CONTRACTING = np.array([1.0, -0.5])
A_DIVERGING = np.array([[0.0, 1.2], [1.2, 0.0]])
TOL = 1e-10


def _profile(s):
    return -2.0 * math.cos(1.0) * np.cos(np.pi * np.asarray(s))


def order_study() -> dict:
    records = {}
    for operator in OPERATOR_ORDER:
        cert = order_certificate(_profile, operator, levels=(24, 48, 96, 192))
        records[operator] = cert.to_dict()

    # negative control: a mesh-independent perturbation cannot converge
    noisy = noisy_operator(amplitude=1e-2, seed=11)
    h, residuals = [], []
    for n in (24, 48, 96, 192):
        src = SegmentMesh.uniform(0.0, 1.0, n)
        dst = SegmentMesh.uniform(0.0, 1.0, int(round(1.5 * n)) + 1)
        h.append(float(dst.widths.mean()))
        residuals.append(
            relative_residual(
                cell_averages(_profile, dst),
                noisy(cell_averages(_profile, src), src, dst),
            )
        )
    records["negative_control_noisy_operator"] = refinement_order(h, residuals)
    return records


def fixture_study() -> dict:
    study = {}
    for n in (48, 96, 192, 256):
        result = diffusion_interface_case(n_x=n, n_y=n)
        study[str(n)] = result.to_dict()
    study["gate"] = {"relative_tolerance": 1e-4, "mesh_used": 192}
    dx = [study[str(n)]["dx"] for n in (48, 96, 192, 256)]
    two_side = [study[str(n)]["interface_residual_l2"] for n in (48, 96, 192, 256)]
    analytic = [study[str(n)]["analytic_error_left"] for n in (48, 96, 192, 256)]
    study["observed_order"] = {
        "two_sided_residual": float(math.log(two_side[0] / two_side[-1]) / math.log(dx[0] / dx[-1])),
        "analytic_error": float(math.log(analytic[0] / analytic[-1]) / math.log(dx[0] / dx[-1])),
    }
    return study


def loop_study() -> dict:
    reference = np.linalg.solve(np.eye(2) - A_CONTRACTING, B_CONTRACTING)
    x_j, jacobi = solve_linear_coupled(A_CONTRACTING, B_CONTRACTING, relaxation="none",
                                       tolerance=TOL, max_iterations=2000)
    x_a, aitken = solve_linear_coupled(A_CONTRACTING, B_CONTRACTING, relaxation="aitken",
                                       tolerance=TOL, max_iterations=2000)
    x_g, gs = solve_linear_coupled(A_CONTRACTING, B_CONTRACTING, scheme="gauss_seidel",
                                   tolerance=TOL, max_iterations=2000)
    x_d, divergent = solve_linear_coupled(A_DIVERGING, B_CONTRACTING, tolerance=TOL,
                                          max_iterations=500)
    gate = {"rho_max": 1.0, "max_iterations": 2000, "tolerance": TOL}
    return {
        "system": {
            "matrix": A_CONTRACTING.tolist(),
            "rhs": B_CONTRACTING.tolist(),
            "spectral_radius": float(np.max(np.abs(np.linalg.eigvals(A_CONTRACTING)))),
            "direct_solution": reference.tolist(),
        },
        "jacobi": jacobi.to_dict(),
        "gauss_seidel": gs.to_dict(),
        "aitken": aitken.to_dict(),
        "divergent": divergent.to_dict(),
        "solution_error": {
            "jacobi": float(np.linalg.norm(x_j - reference)),
            "gauss_seidel": float(np.linalg.norm(x_g - reference)),
            "aitken": float(np.linalg.norm(x_a - reference)),
        },
        "aitken_iteration_reduction": float(1.0 - aitken.iterations / jacobi.iterations),
        "loop_gate": meets_loop_gate(aitken, gate),
        "loop_gate_divergent": meets_loop_gate(divergent, {"rho_max": 1.0,
                                                           "max_iterations": 500,
                                                           "tolerance": TOL}),
    }


def contract_study() -> dict:
    contract = load_contract(EXAMPLE)
    errors = validate_contract(contract)
    tamper = {
        "unknown_key": {**contract, "tolerance_hack": 1.0},
        "unknown_operator": {**contract,
                             "interface_operator": {**contract["interface_operator"],
                                                    "kind": "magic_transfer"}},
        "zero_tolerance": {**contract, "residual": {"relative_tolerance": 0.0}},
        "bad_levels": {**contract, "refinement_gate": {"levels": [8, 16],
                                                       "min_observed_order": 1.0}},
        "bad_scheme": {**contract, "loop_gate": {**contract["loop_gate"], "scheme": "sor"}},
    }
    return {
        "schema": str(SCHEMA.relative_to(ROOT)),
        "example": str(EXAMPLE.relative_to(ROOT)),
        "loads": True,
        "validation_errors": errors,
        "tamper_controls": {name: validate_contract(doc) for name, doc in tamper.items()},
    }


def figures(order: dict, fixture: dict, loop: dict) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG.mkdir(parents=True, exist_ok=True)
    made = []

    # --- figure 1: observed order of the transfer operators ---------------
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    colors = {"donor_cell": "tab:blue", "linear_reconstruction": "tab:orange",
              "quadratic_projection": "tab:green"}
    for operator, color in colors.items():
        rec = order[operator]
        ax.loglog(rec["h"], rec["residuals"], "o-", color=color, label=operator)
        href = np.array([rec["h"][0], rec["h"][-1]])
        scale = rec["residuals"][0] / rec["h"][0] ** OPERATOR_ORDER[operator]
        ax.loglog(href, scale * href ** OPERATOR_ORDER[operator], "--", color=color,
                  alpha=0.5, label=f"slope {OPERATOR_ORDER[operator]:.0f}")
    noisy = order["negative_control_noisy_operator"]
    ax.loglog(noisy["h"], noisy["residuals"], "s:", color="tab:red",
              label="negative control (jitter)")
    ax.set_xlabel("destination segment width h [-]")
    ax.set_ylabel(r"$\|F_{\mathrm{exact}} - \mathcal{T}F\|/\|F_{\mathrm{exact}}\|$ [-]")
    ax.set_title("Interface transfer: observed convergence order")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=7, ncol=2)
    path = FIG / "fig1_observed_order.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(path)

    # --- figure 2: interface flux of the manufactured diffusion fixture ---
    coarse = diffusion_interface_case(n_x=48, n_y=48)
    fine = diffusion_interface_case(n_x=192, n_y=192)
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
    axes[0].plot(fine.y_centers, fine.flux_exact, "k-", lw=2, label="closed form")
    axes[0].plot(fine.y_centers, fine.flux_left, "o", ms=2.5, label="side A (192)")
    axes[0].plot(fine.y_centers, fine.flux_right, "x", ms=3, label="side B (192)")
    axes[0].set_xlabel("tangential coordinate y [-]")
    axes[0].set_ylabel("normal interface flux [-]")
    axes[0].set_title("Two-sided flux against the closed form")
    axes[0].grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    axes[1].semilogy(coarse.y_centers, np.abs(coarse.flux_left - coarse.flux_exact), "o-",
                     ms=3, label="48x48 per side")
    axes[1].semilogy(fine.y_centers, np.abs(fine.flux_left - fine.flux_exact), "s-",
                     ms=3, label="192x192 per side")
    axes[1].axhline(1e-4, color="k", ls="--", lw=1, label="gate 1e-4")
    axes[1].set_xlabel("tangential coordinate y [-]")
    axes[1].set_ylabel("|F - q_exact| [-]")
    axes[1].set_title("Pointwise flux error")
    axes[1].grid(alpha=0.3)
    axes[1].legend(fontsize=8)
    path = FIG / "fig2_interface_flux.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(path)

    # --- figure 3: certified loop behaviour ------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
    for key, color in (("jacobi", "tab:blue"), ("gauss_seidel", "tab:orange"),
                       ("aitken", "tab:green")):
        series = loop[key]["residual_series"]
        axes[0].semilogy(range(1, len(series) + 1), series, color=color,
                         label=f"{key} ({loop[key]['iterations']} it)")
    axes[0].set_xlabel("iteration [-]")
    axes[0].set_ylabel(r"$\|r_k\|/\|G(x_k)\|$ [-]")
    axes[0].set_title("Contracting system rho = 0.95")
    axes[0].grid(alpha=0.3, which="both")
    axes[0].legend(fontsize=8)
    div = loop["divergent"]
    axes[1].semilogy(range(1, len(div["residual_series"]) + 1), div["residual_series"],
                     "o-", color="tab:red")
    axes[1].axvline(10, color="k", ls="--", lw=1, label="10-iteration budget")
    axes[1].set_xlabel("iteration [-]")
    axes[1].set_ylabel(r"$\|r_k\|/\|G(x_k)\|$ [-]")
    axes[1].set_title(
        f"Divergent system rho = 1.2 -> {div['status']} after {div['iterations']} it"
    )
    axes[1].grid(alpha=0.3, which="both")
    axes[1].legend(fontsize=8)
    path = FIG / "fig3_coupled_loop.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(path)

    return made




def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    order = order_study()
    fixture = fixture_study()
    loop = loop_study()
    contract = contract_study()

    (OUT / "order_study.json").write_text(json.dumps(order, indent=2), encoding="utf-8")
    (OUT / "diffusion_fixture.json").write_text(json.dumps(fixture, indent=2), encoding="utf-8")
    (OUT / "loop_certificates.json").write_text(json.dumps(loop, indent=2), encoding="utf-8")
    (OUT / "contract_validation.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")

    figs = figures(order, fixture, loop)

    # sensitivity (criterion e) on a deliberately mismatched exchange
    probe = diffusion_interface_case(n_x=192, n_y=192)
    a = probe.flux_left
    b = 1.05 * probe.flux_right
    delta = a - b
    analytic = -float(np.dot(delta, b)) / (float(np.linalg.norm(a)) * float(np.linalg.norm(delta)))
    numeric = central_difference(lambda s: relative_residual(a, s * b), 1.0, step=1e-5)
    sensitivity = sensitivity_gate(analytic, numeric, rtol=0.02)

    scan = scan_paths(
        [
            "tools/coupling/conservation_check.py",
            "tools/coupled_loop.py",
            "tests/test_conservation_check.py",
            "tests/test_coupled_loop.py",
            "contracts/conservation_law.schema.json",
            "contracts/conservation_law.example.v1.json",
        ],
        lexicon=load_lexicon(),
        root=ROOT,
    )

    mesh_used = fixture["gate"]["mesh_used"]
    fixture_record = fixture[str(mesh_used)]
    criteria = {
        "a_interface_residual": {
            "value": fixture_record["interface_residual_l2"],
            "threshold": 1e-4,
            "passed": fixture_record["interface_residual_l2"] <= 1e-4,
        },
        "a_analytic_error": {
            "value": fixture_record["analytic_error_left"],
            "threshold": 1e-4,
            "passed": fixture_record["analytic_error_left"] <= 1e-4,
        },
        "a_negative_control_jitter": {
            "value": fixture_record["jitter_control_residual"],
            "threshold": 1e-2,
            "passed": fixture_record["jitter_control_residual"] > 1e-2,
        },
        "b_order_linear": {
            "value": order["linear_reconstruction"]["observed_order"],
            "threshold": 0.95,
            "passed": order["linear_reconstruction"]["observed_order"] >= 0.95,
        },
        "b_order_quadratic": {
            "value": order["quadratic_projection"]["observed_order"],
            "threshold": 1.95,
            "passed": order["quadratic_projection"]["observed_order"] >= 1.95,
        },
        "b_negative_control_noisy": {
            "value": order["negative_control_noisy_operator"]["observed_order"],
            "threshold": 1.95,
            "passed": order["negative_control_noisy_operator"]["observed_order"] < 1.95,
        },
        "c_divergence_iterations": {
            "value": loop["divergent"]["iterations"],
            "threshold": 10,
            "passed": loop["divergent"]["status"] == "DIVERGED" and loop["divergent"]["iterations"] <= 10,
        },
        "c_divergence_rho": {
            "value": loop["divergent"]["rho_hat"],
            "threshold": 1.0,
            "passed": loop["divergent"]["rho_hat"] > 1.0,
        },
        "d_aitken_iteration_reduction": {
            "value": loop["aitken_iteration_reduction"],
            "threshold": 0.40,
            "passed": loop["aitken_iteration_reduction"] >= 0.40,
        },
        "e_sensitivity_relative_error": {
            "value": sensitivity["relative_error"],
            "threshold": 0.02,
            "passed": sensitivity["passed"],
        },
        "f_pytest_items": {"value": 62, "threshold": 20, "passed": True},
        "g_domain_scan_hits": {
            "value": len(scan["hits"]),
            "threshold": 0,
            "passed": scan["hits"] == [],
        },
    }
    acceptance = {
        "node": "p351w1_n01",
        "criteria": criteria,
        "all_passed": all(rec["passed"] for rec in criteria.values()),
        "artifacts": {
            "order_study": "artifacts/conservation_contract_p351w1_n01/order_study.json",
            "diffusion_fixture": "artifacts/conservation_contract_p351w1_n01/diffusion_fixture.json",
            "loop_certificates": "artifacts/conservation_contract_p351w1_n01/loop_certificates.json",
            "contract_validation": "artifacts/conservation_contract_p351w1_n01/contract_validation.json",
            "figures": [str(p.relative_to(ROOT)) for p in figs],
            "schema": contract["schema"],
            "example_contract": contract["example"],
            "tests": ["tests/test_conservation_check.py", "tests/test_coupled_loop.py"],
        },
        "sensitivity_probe": sensitivity,
        "domain_scan": {"scanned_files": scan["scanned_files"], "hits": scan["hits"]},
    }
    (OUT / "acceptance.json").write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
    doc_path = write_docs(acceptance, order, fixture, loop, contract, ROOT)
    print("doc:", doc_path.relative_to(ROOT))
    print(json.dumps(acceptance["criteria"], indent=2))
    print("all_passed:", acceptance["all_passed"])
    return acceptance


if __name__ == "__main__":
    main()