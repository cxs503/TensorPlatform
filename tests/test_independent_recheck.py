"""Tests for the independent LBM benchmark re-check (cross-family verification).

The point of these tests is that the *judging instrument itself* is tested before it is
pointed at real artifacts: planted-truth synthetic cases must be recovered and judged
with the documented thresholds, so a FAIL/̄PASS on a real case is attributable to the case
and not to the checker.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.benchmarks import independent_recheck as rc  # noqa: E402
from tools.benchmarks import lbm_reference_registry as reg  # noqa: E402


# --------------------------------------------------------------------------------------
# 1. Verdict vocabulary and thresholds
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("err,expected", [
    (0.0, "PASS"),
    (0.029999, "PASS"),
    (0.03, "PASS"),          # inclusive lower band
    (0.0301, "MARGINAL"),
    (0.15, "MARGINAL"),
    (0.150001, "FAIL"),
    (1.5, "FAIL"),
    (float("nan"), "INDETERMINATE"),
])
def test_classify_thresholds(err, expected):
    assert reg.classify(err) == expected


def test_thresholds_match_platform_standard():
    # platform standard: >15% unacceptable, <3% target
    assert reg.TOL_TARGET == 0.03
    assert reg.TOL_ACCEPTABLE == 0.15


def test_rel_error_basic():
    assert reg.rel_error(1.03, 1.0) == pytest.approx(0.03, rel=1e-12)
    assert reg.rel_error(0.97, 1.0) == pytest.approx(0.03, rel=1e-12)
    assert reg.rel_error(-0.2714, -0.2751) == pytest.approx(0.01345, abs=1e-4)
    with pytest.raises(ValueError):
        reg.rel_error(1.0, 0.0)


# --------------------------------------------------------------------------------------
# 2. Numerics helpers
# --------------------------------------------------------------------------------------
def test_interp_linear_hits_nodes_and_flags_out_of_range():
    xs, ys = [0.0, 1.0, 2.0], [0.0, 10.0, 20.0]
    assert reg.interp_linear(xs, ys, 1.0) == (10.0, True)
    assert reg.interp_linear(xs, ys, 0.5) == (5.0, True)
    assert reg.interp_linear(xs, ys, -0.1) == (0.0, False)
    assert reg.interp_linear(xs, ys, 2.5) == (20.0, False)


def test_curve_errors_exact_match_passes():
    out = reg.curve_errors([0, 1, 2], [1.0, 2.0, 3.0], [0.5, 1.5], [1.5, 2.5])
    assert out["n_points"] == 2
    assert out["l_inf"] == pytest.approx(0.0, abs=1e-12)
    assert out["verdict"] == "PASS"


def test_curve_errors_detects_planted_offset():
    ref_x, ref_y = [0.5, 1.5], [1.0, 2.0]
    # Same abscissae as the reference, amplitudes scaled by a planted factor.
    assert reg.curve_errors(ref_x, [1.02, 2.04], ref_x, ref_y)["verdict"] == "PASS"
    assert reg.curve_errors(ref_x, [1.10, 2.20], ref_x, ref_y)["verdict"] == "MARGINAL"
    assert reg.curve_errors(ref_x, [1.20, 2.40], ref_x, ref_y)["verdict"] == "FAIL"
    # Interpolated comparison over a denser sim curve behaves the same way.
    sim_x = [0.0, 0.5, 1.0, 1.5, 2.0]
    sim_y = [0.0, 1.02, 1.53, 2.04, 2.55]
    assert reg.curve_errors(sim_x, sim_y, ref_x, ref_y)["verdict"] == "PASS"


def test_curve_errors_never_extrapolates():
    # Reference point at T=5 while the simulation only reaches T=2 -> excluded.
    out = reg.curve_errors([0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [1.0, 5.0], [2.0, 99.0])
    assert out["n_points"] == 1
    assert out["verdict"] == "PASS"


def test_curve_errors_empty_when_no_overlap():
    out = reg.curve_errors([0.0, 1.0], [1.0, 2.0], [5.0], [1.0])
    assert out["n_points"] == 0
    assert out["verdict"] == "INDETERMINATE"
    assert math.isnan(out["l_inf"])


# --------------------------------------------------------------------------------------
# 3. Analytic baselines
# --------------------------------------------------------------------------------------
def test_poiseuille_planar_matches_textbook_and_mean_ratio():
    out = reg.poiseuille_planar(grad_p=8.888888888888889e-06, H=60.0, nu=0.1)
    assert out["u_max"] == pytest.approx(0.04, rel=1e-12)
    assert out["u_mean"] == pytest.approx(2.0 / 3.0 * out["u_max"], rel=1e-12)


def test_darcy_forchheimer_reduces_to_darcy_and_round_trips():
    mu, perm, rho, grad = 1.0, 0.01, 1.0, 2.0
    u_darcy = reg.darcy_forchheimer_flux(grad, mu, perm, beta=0.0, rho=rho)
    assert u_darcy == pytest.approx(grad * perm / mu, rel=1e-12)
    # With inertia the flux must drop; and the returned root must satisfy the balance.
    beta = 5.0
    u = reg.darcy_forchheimer_flux(grad, mu, perm, beta=beta, rho=rho)
    assert u < u_darcy
    assert mu / perm * u + beta * rho * u * u == pytest.approx(grad, rel=1e-12)


# --------------------------------------------------------------------------------------
# 4. Registry data integrity (guards against transcription typos)
# --------------------------------------------------------------------------------------
def test_ghia_tables_shape_and_extrema():
    for re_, table in reg.GHIA_U_CENTERLINE.items():
        assert len(table) == 17, f"Ghia Re={re_} table must have 17 points"
        ys = [y for y, _ in table]
        assert ys == sorted(ys, reverse=True)
        assert table[0] == (1.0, 1.0)
        assert table[-1][1] == pytest.approx(0.0, abs=1e-12)
    # Published minima (Ghia et al. 1982): Re=100 -0.21090, Re=1000 -0.38289
    assert min(u for _, u in reg.GHIA_U_CENTERLINE[100]) == pytest.approx(-0.21090)
    assert min(u for _, u in reg.GHIA_U_CENTERLINE[1000]) == pytest.approx(-0.38289)
    assert min(u for _, u in reg.GHIA_U_CENTERLINE[400]) == pytest.approx(-0.32726)


def test_martin_moyce_table_monotone_and_aligned():
    t, z = reg.MARTIN_MOYCE_1952["T"], reg.MARTIN_MOYCE_1952["Z"]
    assert len(t) == len(z) == 14
    assert t == sorted(t)
    assert all(b > a for a, b in zip(z, z[1:]))
    assert z[0] == 1.0  # initial column, Z = x_front/a = 1


def test_ku_1987_reference_ordering():
    # 3D must be less negative than 2D at Re=1000 (spanwise friction), by ~28%.
    u3d = reg.KU_1987_3D["u_min"]
    u2d = min(u for _, u in reg.GHIA_U_CENTERLINE[1000])
    assert u2d < u3d < 0.0
    assert 0.20 < (u3d - u2d) / abs(u2d) < 0.35


# --------------------------------------------------------------------------------------
# 5. Synthetic artifacts: end-to-end recovery of planted truth
# --------------------------------------------------------------------------------------
def _write_cavity_npz(path, u_min, n=16, u_lid=0.06, normalised=True):
    """Field with a driven lid at j=n-1 and a parabolic recirculation of depth u_min."""
    ux = np.zeros((n, n, n))
    y = np.linspace(0.0, 1.0, n)
    prof = u_min * np.sin(np.pi * y) * 3.4  # scaled so the min equals u_min
    prof = prof / np.max(np.abs(prof)) * abs(u_min)
    for i in range(n):
        for k in range(n):
            ux[i, :, k] = prof
    ux[:, -1, :] = 1.0 if normalised else u_lid
    if not normalised:
        ux *= u_lid
    np.savez(path, ux=ux, uy=np.zeros_like(ux), uz=np.zeros_like(ux),
             nx=n, ny=n, nz=n, u_lid=u_lid, re=1000.0)


def test_check_cavity_3d_recovers_planted_minimum(tmp_path):
    p = str(tmp_path / "case.npz")
    _write_cavity_npz(p, u_min=-0.2751)
    out = rc.check_cavity_3d(p)
    assert out["measured"]["u_min_normalised"] == pytest.approx(-0.2751, rel=2e-2)
    assert out["reference"][0]["rel_err"] < 0.02
    assert out["verdict"] == "PASS"
    assert out["independence"] == reg.TIER_FIELD


def test_check_cavity_3d_handles_dimensional_field(tmp_path):
    p = str(tmp_path / "case_dim.npz")
    _write_cavity_npz(p, u_min=-0.2751, normalised=False)
    out = rc.check_cavity_3d(p)
    assert out["measured"]["field_already_normalised"] is False
    assert out["verdict"] == "PASS"


def test_check_cavity_3d_flags_biased_field(tmp_path):
    p = str(tmp_path / "case_bad.npz")
    _write_cavity_npz(p, u_min=-0.2751 * 1.25)
    out = rc.check_cavity_3d(p)
    assert out["verdict"] == "FAIL"


def _write_hist(path, t, x, t_code=None):
    fields = ["step", "T_MM", "X_toe"] + (["T_code"] if t_code else [])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for idx, (ti, xi) in enumerate(zip(t, x)):
            row = [idx, ti, xi]
            if t_code:
                row.append(t_code[idx])
            w.writerow(row)


def test_check_dam_break_exact_reference_passes(tmp_path):
    p = str(tmp_path / "hist.csv")
    _write_hist(p, reg.MARTIN_MOYCE_1952["T"], reg.MARTIN_MOYCE_1952["Z"])
    out = rc.check_dam_break(p, "synthetic")
    assert out["reference"][0]["n_points"] == 14
    assert out["verdict"] == "PASS"


def test_check_dam_break_systematic_lag_fails(tmp_path):
    p = str(tmp_path / "hist_lag.csv")
    z = [v * 0.85 for v in reg.MARTIN_MOYCE_1952["Z"]]
    _write_hist(p, reg.MARTIN_MOYCE_1952["T"], z)
    out = rc.check_dam_break(p, "synthetic-lag")
    assert out["verdict"] == "FAIL"


def test_check_dam_break_quantifies_sqrt2_convention(tmp_path):
    p = str(tmp_path / "hist_conv.csv")
    t_mm = reg.MARTIN_MOYCE_1952["T"]
    t_code = [t / math.sqrt(2.0) for t in t_mm]
    _write_hist(p, t_mm, reg.MARTIN_MOYCE_1952["Z"], t_code=t_code)
    out = rc.check_dam_break(p, "convention")
    q = out["quality"]
    assert q["convention_correct_axis"]["verdict"] == "PASS"
    assert q["convention_legacy_axis"]["verdict"] == "FAIL"
    # the sqrt(2) bookkeeping identity must hold to round-off
    assert q["sqrt2_consistency_max_abs"] < 1e-9


def test_check_dam_break_requires_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("step,foo\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        rc.check_dam_break(str(p))


def test_check_forchheimer_recomputes_and_judges(tmp_path):
    p = str(tmp_path / "result.json")
    rows = [
        {"re_target": 25.2, "d": 52, "re_ach": 25.2, "R_sim": 1.18, "R_ref_A": 1.18227,
         "err_A": 0.0019, "y0_f_form": 217.89, "steady": True, "mass_drift": 1e-6},
        {"re_target": 103.6, "d": 416, "re_ach": 103.6, "R_sim": 1.20, "R_ref_A": 1.4355,
         "err_A": 0.164, "y0_f_form": 217.89, "steady": True, "mass_drift": 1e-6},
    ]
    json.dump({"reference": {"paper": "synthetic"}, "rows": rows}, open(p, "w"))
    out = rc.check_forchheimer(p)
    assert out["measured"]["n_rows"] == 2
    assert out["measured"]["worst_rel_err"] == pytest.approx(
        reg.rel_error(1.20, 1.4355), rel=1e-9)
    assert out["verdict"] == "FAIL"
    # arithmetic gap vs the case's own err_A column is reported (audit hook)
    assert out["quality"]["max_arith_gap_vs_reported"] is not None


def test_check_poiseuille_audits_baseline_and_bookkeeping(tmp_path):
    p = str(tmp_path / "poise.json")
    d = {"delta_rho": 0.0048, "nx": 180, "ny": 62, "H_eff": 60, "nu_lb": 0.1,
         "u_max_ana": 0.04, "u_mean_ana": 0.02666666666666666,
         "u_max_num": 0.04006, "u_max_err_pct": 0.15}
    json.dump(d, open(p, "w"))
    out = rc.check_poiseuille(p)
    assert out["verdict"] == "PASS"
    assert out["reference"][0]["rel_err"] < 1e-12
    assert out["reference"][2]["verdict"] == "PASS"   # H_eff = ny - 2
    d["H_eff"] = 61
    json.dump(d, open(p, "w"))
    assert rc.check_poiseuille(p)["reference"][2]["verdict"] == "FAIL"


def test_check_poiseuille_detects_wrong_baseline(tmp_path):
    p = str(tmp_path / "poise_bad.json")
    json.dump({"delta_rho": 0.0048, "nx": 180, "ny": 62, "H_eff": 60, "nu_lb": 0.1,
               "u_max_ana": 0.05, "u_mean_ana": 0.0333, "u_max_num": 0.0501},
              open(p, "w"))
    out = rc.check_poiseuille(p)
    assert out["verdict"] == "FAIL"


# --------------------------------------------------------------------------------------
# 6. Orchestration robustness
# --------------------------------------------------------------------------------------
def test_run_on_empty_root_skips_everything_without_raising(tmp_path):
    out = rc.run(str(tmp_path))
    assert out["checks"] == []
    assert out["summary"]["verdicts"] == {}
    assert out["summary"]["worst"] == "INDETERMINATE"
    assert {s["case"] for s in out["skipped"]} >= {"cavity_3d_full", "forchheimer"}


def test_to_markdown_reports_verdicts():
    md = rc.to_markdown({
        "lbm_root": "/tmp/x",
        "checks": [{"case": "poiseuille_2d", "independence": "baseline-audit",
                    "measured": {}, "verdict": "PASS",
                    "reference": [{"rel_err": 0.0}]}],
        "skipped": [{"case": "dam_break_sc", "reason": "no history csv"}],
        "summary": {"n_checks": 1, "n_skipped": 1, "verdicts": {"PASS": 1}},
    })
    assert "poiseuille_2d" in md
    assert "PASS ≤ 3%" in md.replace("3%", "3%")
    assert "no history csv" in md