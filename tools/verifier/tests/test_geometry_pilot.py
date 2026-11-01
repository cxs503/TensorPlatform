"""Tests for the geometry / hydrostatics verification pilot.

Covers the three things that can silently rot:
  1. the closed-form baselines themselves (analytic values are pinned),
  2. the reference integrator against those baselines (accuracy + order),
  3. the gate's teeth — every planted defect must be rejected, and the
     candidate protocol must separate a real implementation from a MOCK.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

from verifier.geometry.analytic_cases import ANALYTIC_CASES, METRICS, box_barge, build_case, sineform, wigley
from verifier.geometry.mutants import MUTANTS
from verifier.geometry.ref_hydrostatics import HullForm, compute_hydrostatics, convergence_study
import verifier.tp_ver_geometry as pilot


# --------------------------------------------------------------------------- #
# 1. closed-form baselines are pinned                                        #
# --------------------------------------------------------------------------- #
def test_box_barge_closed_form():
    form, exact = box_barge(100.0, 20.0, 8.0)
    assert exact["VOL"] == pytest.approx(100 * 20 * 8)
    for k in ("Cb", "Cm", "Cp", "Cw"):
        assert exact[k] == pytest.approx(1.0)
    assert exact["KB"] == pytest.approx(4.0)
    assert exact["BM"] == pytest.approx(20 ** 2 / (12 * 8))
    assert exact["LCB"] == pytest.approx(0.0)


def test_wigley_closed_form_against_full_depth_limit():
    """D -> T is the textbook Wigley: Cb = 4/9, Cm = 2/3, Cp = 2/3, KB = 0.375 T."""
    _, exact = wigley(100.0, 20.0, 8.0, 8.0)
    assert exact["Cb"] == pytest.approx(4 / 9, rel=1e-12)
    assert exact["Cm"] == pytest.approx(2 / 3, rel=1e-12)
    assert exact["Cp"] == pytest.approx(2 / 3, rel=1e-12)
    assert exact["KB"] == pytest.approx(0.375 * 8.0, rel=1e-12)
    assert exact["Cw"] == pytest.approx(0.0, abs=1e-12)


def test_wigley_deep_limit_recovers_two_thirds():
    """D >> T -> wall-sided sections: Cm -> 1 and Cb -> 2/3."""
    _, exact = wigley(100.0, 20.0, 8.0, 1.0e6)
    assert exact["Cm"] == pytest.approx(1.0, rel=1e-9)
    assert exact["Cb"] == pytest.approx(2 / 3, rel=1e-9)
    assert exact["KB"] == pytest.approx(0.5 * 8.0, rel=1e-9)


def test_sineform_closed_form():
    _, exact = sineform(120.0, 22.0, 7.5, 11.25)
    assert exact["Cp"] == pytest.approx(2 / np.pi, rel=1e-12)
    assert exact["Cw"] == pytest.approx((2 / np.pi) * (1 - 7.5 ** 2 / 11.25 ** 2), rel=1e-12)
    assert exact["KB"] == pytest.approx(
        0.5 * 7.5 * (1 - 7.5 ** 2 / (2 * 11.25 ** 2)) / (1 - 7.5 ** 2 / (3 * 11.25 ** 2)), rel=1e-12)


def test_depth_must_not_be_less_than_draft():
    with pytest.raises(ValueError):
        wigley(100.0, 20.0, 8.0, 7.0)


# --------------------------------------------------------------------------- #
# 2. reference integrator vs the baselines                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", list(ANALYTIC_CASES))
def test_reference_integrator_matches_closed_form(case):
    form, exact, skip = build_case(case)
    r = compute_hydrostatics(form, n_x=160, n_z=80)
    for k in METRICS:
        if k in skip:
            continue
        assert pilot._rel(r[k], exact[k]) < 1e-6, f"{case}:{k} = {r[k]} vs {exact[k]}"


@pytest.mark.parametrize("case", ["wigley_D=2T", "sineform_D=1.5T"])
def test_simpson_order_is_four(case):
    """Observed convergence order must match Simpson (4), not trapezoid (2)."""
    form, exact, _ = build_case(case)
    conv = convergence_study(form, exact, quantities=("I_T",), grids=(40, 80, 160))
    orders = [o for o in conv["I_T"]["order"] if np.isfinite(o)]
    assert orders and min(orders) > 3.5


def test_trapezoid_integrator_is_second_order():
    """Second-order integration is detectable by the order criterion alone.

    Uses the polynomial (Wigley) case for the displacement: on the cosine hull the
    integrand is smooth and periodic, so trapezoid converges much faster than h^2
    and the order criterion would not separate it there.
    """
    form, exact, _ = build_case("wigley_D=2T")
    errs = []
    for n in (40, 80, 160):
        r = compute_hydrostatics(form, n_x=n, n_z=n // 2, integrate="trapezoid")
        errs.append(pilot._rel(r["VOL"], exact["VOL"]))
    order = np.log(errs[0] / errs[1]) / np.log(2.0)
    assert 1.6 < order < 2.4, f"trapezoid order {order:.2f} (errs={errs})"


def test_identities_hold_to_round_off():
    for case in ANALYTIC_CASES:
        form, exact, _ = build_case(case)
        r = compute_hydrostatics(form, n_x=80, n_z=40)
        ident = pilot.identities(r, form)
        assert max(ident.values()) < 1e-12, f"{case}: {ident}"


def test_grid_argument_validation():
    form, _, _ = build_case("box_barge")
    with pytest.raises(ValueError):
        compute_hydrostatics(form, n_x=41, n_z=40, integrate="simpson") if False else \
            pilot.compute_hydrostatics.__globals__["_simpson"](np.ones((3, 4)), np.zeros(4), axis=1)
    bad = HullForm(L=10.0, B=2.0, T=1.0, half_breadth=lambda x, z: np.full(np.broadcast(x, z).shape, -1.0))
    with pytest.raises(ValueError):
        compute_hydrostatics(bad, n_x=20, n_z=20)


# --------------------------------------------------------------------------- #
# 3. the gate has teeth                                                     #
# --------------------------------------------------------------------------- #
def test_every_mutant_is_rejected():
    """A mutant that survives means the criteria are too weak."""
    rep = pilot.self_audit(n=80)
    detail = rep["criteria"]["C4_mutation_gate"]["detail"]
    assert set(detail) == set(MUTANTS)
    survived = [k for k, v in detail.items() if not v["rejected"]]
    assert not survived, f"surviving mutants: {survived}"
    assert rep["verdict"] == "PASS"


def test_self_audit_report_shape():
    rep = pilot.self_audit(n=80)
    assert rep["verdict"] == "PASS"
    for name in ANALYTIC_CASES:
        c = rep["cases"][name]
        assert set(METRICS).issubset(c["exact"])
        assert c["worst_rel_err"] < 1e-6
    assert rep["criteria"]["C1_analytic_agreement"]["pass"] is True
    assert rep["criteria"]["C3_identities"]["pass"] is True


# --------------------------------------------------------------------------- #
# 4. candidate protocol (subprocess)                                        #
# --------------------------------------------------------------------------- #
CANDIDATE_REAL = '''
import json, sys
import numpy as np

spec = json.load(sys.stdin)["specification"]
x = np.asarray(spec["x"], float); z = np.asarray(spec["z"], float)
y = np.asarray(spec["y"], float); L, B, T = spec["L"], spec["B"], spec["T"]

def trap(v, g, axis):
    return np.trapezoid(v, g, axis=axis)
S = trap(2 * y, z, axis=1)
wl = 2 * y[:, -1]
vol = float(trap(S, x, axis=0))
aw = float(trap(wl, x, axis=0))
it = float(trap((2.0 / 3.0) * y[:, -1] ** 3, x, axis=0))
s_mid = float(S[len(S) // 2])
res = {
    "VOL": vol, "Cb": vol / (L * B * T), "Cm": s_mid / (B * T), "Cp": vol / (s_mid * L),
    "A_W": aw, "Cw": aw / (L * B), "I_T": it,
    "KB": float(trap(trap(2 * y * z[None, :], z, axis=1), x, axis=0)) / vol,
    "BM": it / vol,
    "LCB": float(trap(S * x, x, axis=0)) / vol,
    "LCF": float(trap(wl * x, x, axis=0)) / aw if aw > 0 else 0.0,
}
print(json.dumps({"result": res}))
'''

CANDIDATE_MOCK = '''
import json, sys
print(json.dumps({"result": {
    "VOL": 1000.0, "Cb": 0.65, "Cm": 0.98, "Cp": 0.663, "A_W": 900.0, "Cw": 0.75,
    "I_T": 5000.0, "KB": 4.0, "BM": 3.0, "LCB": 0.0, "LCF": 0.0}}))
'''


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_candidate_protocol_accepts_independent_implementation(tmp_path):
    cand = _write(tmp_path, "cand_real.py", CANDIDATE_REAL)
    cases = pilot.heldout_specs(seed=7, n_cases=3)
    res = pilot.run_candidate(cand, cases)
    assert res["verdict"] == "PASS", res
    assert res["passed"] == res["total"]
    for name, c in res["cases"].items():
        assert "error" not in c, (name, c.get("error"))


def test_candidate_protocol_rejects_mock(tmp_path):
    cand = _write(tmp_path, "cand_mock.py", CANDIDATE_MOCK)
    cases = pilot.heldout_specs(seed=7, n_cases=3)
    res = pilot.run_candidate(cand, cases)
    assert res["verdict"] == "FAIL"
    assert res["passed"] == 0


def test_candidate_protocol_reports_crash(tmp_path):
    cand = _write(tmp_path, "cand_crash.py", "raise SystemExit(3)\n")
    cases = pilot.heldout_specs(seed=7, n_cases=1)
    res = pilot.run_candidate(cand, cases)
    assert res["verdict"] == "FAIL"
    assert "error" in list(res["cases"].values())[0]


# --------------------------------------------------------------------------- #
# 5. held-out set construction + CLI end-to-end                             #
# --------------------------------------------------------------------------- #
def test_heldout_specs_are_consistent_and_symmetric():
    cases = pilot.heldout_specs(seed=11, n_cases=3)
    assert len(cases) == 3
    for c in cases:
        spec = c["spec"]
        y = np.asarray(spec["y"])
        assert y.shape == (len(spec["x"]), len(spec["z"]))
        assert (y >= 0).all()
        # symmetric hull: y must not depend on the sign of x
        assert np.allclose(y, y[::-1], atol=1e-6)
        assert 0.40 < c["exact"]["Cb"] <= 1.0 + 1e-12


def test_cli_self_audit_end_to_end(tmp_path):
    out = tmp_path / "rep.json"
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "tools", "verifier", "tp_ver_geometry.py"),
         "--self-audit", "--n-panels", "80", "--output", str(out)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rep = json.loads(out.read_text())
    assert rep["verdict"] == "PASS"
    assert rep["criteria"]["C4_mutation_gate"]["rejected"] == len(MUTANTS)


def test_cli_rejects_failing_candidate(tmp_path):
    cand = _write(tmp_path, "cand_mock.py", CANDIDATE_MOCK)
    out = tmp_path / "rep.json"
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "tools", "verifier", "tp_ver_geometry.py"),
         "--candidate", cand, "--candidate-id", "mock", "--output", str(out)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert proc.returncode == 1
    assert json.loads(out.read_text())["verdict"] == "FAIL"