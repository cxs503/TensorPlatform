"""G-OPT worked-case tests: geometry gate, model pinning, optimizer cross-agreement.

These tests exercise the *case* module (``optimizer/gopt/cases/slender_body_lb``),
whose subject is a slender body of revolution at fixed displacement.  The
domain-free kernels are tested in test_gopt_cmaes / _slsqp / _adjoint.

Run: ``pytest tests/test_gopt_case_slender_body.py -q``
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from optimizer.gopt.cases.slender_body_lb.model import (  # noqa: E402
    family_from_config,
    load_config,
    load_reference_conditions,
)
from optimizer.gopt.cases.slender_body_lb import run_case as rc  # noqa: E402

CFG = load_config()
FAM = family_from_config(CFG)


def test_fixed_displacement_invariant():
    """V = pi/6 L D^2 must be invariant along the sweep (the constraint is real)."""
    for lam in np.linspace(CFG["design"]["lower"], CFG["design"]["upper"], 17):
        L, D, _ = FAM.geometry(lam)
        V = np.pi / 6.0 * L * D ** 2
        assert V == pytest.approx(FAM.volume_m3, rel=1e-12)
        assert L / D == pytest.approx(lam, rel=1e-12)


def test_local_resistance_mirror_is_pinned_to_the_platform_solver():
    """The complex-safe mirror (used only for derivatives) must equal the platform."""
    worst = 0.0
    for lam in np.linspace(5.0, 9.0, 41):
        c = FAM.coefficients(lam)
        p = FAM.platform_resistance(lam)
        worst = max(worst,
                    abs(float(c["Ct"]) - float(p["ct"])),
                    abs(float(c["Cf"]) - float(p["cf"])),
                    abs(float(c["Cr"]) - float(p["cr"])))
        worst = max(worst, abs(float(c["Rt"]) - float(p["total_resistance_n"]))
                    / max(1e-12, float(p["total_resistance_n"])))
    assert worst < 1e-6, f"mirror drifted from the platform solver: {worst:.2e}"


def test_complex_step_gradient_matches_finite_difference():
    h = 1e-6
    for key in rc.OBJECTIVES:
        lam = 7.0
        g_cs = FAM.objective_gradient(key, lam)
        g_fd = (FAM.objective_value(key, lam + h) - FAM.objective_value(key, lam - h)) / (2 * h)
        assert abs(g_cs - g_fd) / abs(g_fd) < 1e-6, key


def test_geometry_gate_passes_and_is_quantified():
    gate = rc.geometry_gate(FAM, CFG)
    assert gate["gate_pass"] is True
    assert gate["wetted_surface_rel_err_pct"] < 5.0
    assert gate["cb_cyl_family"] >= gate["cb_cyl_lower_bound_published_triple"]
    assert gate["cb_cyl_family"] == pytest.approx(2.0 / 3.0, rel=1e-12)


def test_reference_dataset_is_the_canonical_platform_source():
    ref = load_reference_conditions(CFG)
    assert len(ref["cases"]) == 4
    assert "Roddy" in ref["citation"]
    for case in ref["cases"]:
        assert case["experimental_ct"] > 0
        assert case["form_type"] == "body_of_revolution"


def test_model_validation_inside_envelope():
    val = rc.model_validation(FAM, CFG)
    inside = [r for r in val["rows"] if r["in_validity_envelope"]]
    outside = [r for r in val["rows"] if not r["in_validity_envelope"]]
    assert len(inside) == 2 and len(outside) == 2  # Fr <= 0.62 vs beyond
    assert val["max_abs_rel_err_pct_in_envelope"] < 15.0
    # outside the calibrated envelope the fit must over-predict (platform's own claim)
    assert all(r["ct_rel_err_pct"] > 15.0 for r in outside)


def test_objective_landscape_shape():
    sweep = rc.sweep(FAM, CFG)
    arg = sweep["argmin"]
    # C_D at fixed displacement: monotone decreasing -> upper bound
    assert arg["CD_wetted"]["lambda_star"] == pytest.approx(CFG["design"]["upper"])
    assert arg["CD_wetted"]["bound_hit"] == "upper"
    # platform R_t (no form factor): monotone increasing -> lower bound
    assert arg["Rt_platform"]["lambda_star"] == pytest.approx(CFG["design"]["lower"])
    # viscous + form factor: a genuine interior optimum
    rv = arg["Rv_viscous_only"]
    assert rv["interior"] is True
    assert 5.5 < rv["lambda_star"] < 8.0


def test_optimizers_agree_on_every_objective():
    opt = rc.optimise(FAM, CFG)
    tol = CFG["acceptance"]["optimizer_cross_agreement_rel"]
    for key in rc.OBJECTIVES:
        rec = opt[key]
        assert rec["cross_agreement_rel"] <= tol, (key, rec["cross_agreement_rel"])
        assert rec["slsqp"]["success"], (key, rec["slsqp"]["message"])
        # both must land on the same objective value as well
        assert rec["objective_at_cmaes"] == pytest.approx(rec["objective_at_slsqp"], rel=1e-6)
        audit = rec["gradient_audit"]
        assert audit["passes"], (key, audit)
        assert audit["grad_rel_err"] <= CFG["acceptance"]["gradient_check_rel"]
        assert audit["taylor_order"] >= CFG["acceptance"]["taylor_order_min"]


def test_full_case_run_writes_machine_readable_artifacts():
    import json

    res = rc.main()
    outdir = os.path.join(_REPO_ROOT, "artifacts", "gopt", "slender_body_lb")
    path = os.path.join(outdir, "results.json")
    assert os.path.exists(path)
    with open(path) as fh:
        back = json.load(fh)
    assert back["geometry_gate"]["gate_pass"] is True
    assert set(back["optimisation"]) == set(rc.OBJECTIVES)
    # the literature comparison must carry its verdicts (honest, not overwritten)
    lit = back["literature"]
    assert "within_3pct" in lit["hoerner_min_drag_fineness"]
    assert lit["cd_objective_premise"]["interior"] is False
    assert res["case_id"] == CFG["case_id"]