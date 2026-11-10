"""Tests of the cross-solver consistency gate (node P218-N02).

Two layers are covered:

* the *protocol* -- difference metrics, verdict bands, configuration handling,
  arm admission, digest/provenance -- tested on synthetic arms whose numbers are
  chosen by hand, so a wrong verdict is directly visible;
* the *arms* used by the real case -- the lattice-Boltzmann kernel is certified
  against two analytic solutions and the body-fitted grid operators against the
  analytic potential flow around a circle.

Everything in the protocol layer uses synthetic domain names on purpose: the gate
must not know any physics, and the tests prove it by swapping the configuration
without touching the code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backend.services.consistency import (  # noqa: E402
    ConfigError, ConsistencyGate, load_config, pair_stats, pairwise,
    profile_l2_rel, rel_diff, sha256_of, verdict_of, verify_report, worst,
    write_report, validate_config,
)

CONFIG_DIR = REPO / "config" / "consistency"
CASE_ARM_DIR = REPO / "artifacts" / "consistency" / "bluff-body-2d-re100"


# ---------------------------------------------------------------------------
# synthetic fixtures
# ---------------------------------------------------------------------------
def synthetic_config(**over):
    cfg = {
        "schema_version": "1.0.0",
        "case_id": "synthetic-a",
        "quantities": [
            {"name": "q1", "reference": 2.0, "reference_tolerance": 0.1},
            {"name": "q2"},
        ],
        "profiles": [{"name": "p1", "coordinate": "s"}],
        "energy": {"input_term": "src", "dissipation_term": "sink",
                   "closure_tolerance": 0.05},
        "verdict": {"consistent": 0.05, "acceptable": 0.15},
        "arms": {"required": ["A", "B"], "minimum": 2},
    }
    cfg.update(over)
    return cfg


def synthetic_arm(arm_id="a1", family="A", q1=2.0, q2=1.0, src=1.0, sink=0.99,
                  shift=0.0, declarations=("measured on a declared grid",)):
    s = np.linspace(0.0, 1.0, 11)
    prof = {"coordinate": list(s), "value": list(1.0 + 0.5 * s + shift)}
    return {
        "arm_id": arm_id, "solver_family": family, "solver_version": "t",
        "case_id": "synthetic-a",
        "quantities": {"q1": q1, "q2": q2},
        "profiles": {"p1": prof},
        "energy": {"terms": {"src": src, "sink": sink}},
        "declarations": list(declarations),
        "provenance": {"input_sha256": sha256_of(arm_id), "git_commit": "deadbeef",
                       "generated_utc": "2026-01-01T00:00:00+00:00"},
    }


def run_synthetic(**over):
    cfg = synthetic_config(**over)
    gate = ConsistencyGate(cfg)
    arms = over.pop("_arms", None)
    if arms is None:
        arms = [synthetic_arm("a1", "A"), synthetic_arm("b1", "B", q1=2.02)]
    return gate.run(arms)


# ---------------------------------------------------------------------------
# metric layer
# ---------------------------------------------------------------------------
def test_rel_diff_is_symmetric_and_bounded():
    assert rel_diff(1.0, 1.0) == 0.0
    assert rel_diff(0.0, 0.0) == 0.0
    assert rel_diff(0.0, 2.0) == 1.0
    assert rel_diff(2.0, 0.0) == 1.0
    assert rel_diff(1.0, 2.0) == pytest.approx(0.5)
    assert rel_diff(2.0, 1.0) == pytest.approx(0.5)      # symmetry is a contract
    assert rel_diff(1.0, float("nan")) is None


def test_pair_stats_reports_mean_std_and_max():
    stats = pair_stats([0.01, 0.02, 0.09])
    assert stats["n_pairs"] == 3
    assert stats["mean_dev"] == pytest.approx(0.04)
    assert stats["max_dev"] == pytest.approx(0.09)
    assert stats["std_dev"] == pytest.approx(np.std([0.01, 0.02, 0.09]))
    assert pair_stats([])["max_dev"] is None


def test_profile_l2_is_scale_free_and_sampling_independent():
    s = np.linspace(0.0, 2.0, 21)
    dense = {"coordinate": list(np.linspace(0.0, 2.0, 401)),
             "value": list(1.0 - 0.3 * np.linspace(0.0, 2.0, 401) ** 2)}
    coarse = {"coordinate": list(s), "value": list(1.0 - 0.3 * s ** 2)}
    same = profile_l2_rel(dense, coarse)
    assert same["l2_rel"] < 1e-3, "the same curve on two grids must agree"
    shifted = {"coordinate": list(s), "value": list(1.0 - 0.3 * s ** 2 + 0.1)}
    other = profile_l2_rel(dense, shifted)
    assert other["l2_rel"] > same["l2_rel"]
    # symmetric in the argument order
    assert other["l2_rel"] == pytest.approx(profile_l2_rel(shifted, dense)["l2_rel"])


def test_verdict_bands_and_undeclared_band():
    rule = {"consistent": 0.05, "acceptable": 0.15}
    assert verdict_of(0.0, rule) == "consistent"
    assert verdict_of(0.0499, rule) == "consistent"
    assert verdict_of(0.05, rule) == "acceptable"
    assert verdict_of(0.1499, rule) == "acceptable"
    assert verdict_of(0.15, rule) == "inconsistent"
    assert verdict_of(0.9, rule) == "inconsistent"
    assert verdict_of(0.08, rule, declared=False) == "acceptable_undeclared"
    assert verdict_of(None, rule) == "withheld"
    assert worst(["consistent", "inconsistent", "acceptable"]) == "inconsistent"
    assert worst(["withheld", "inconsistent"]) == "withheld"


# ---------------------------------------------------------------------------
# configuration and admission layer
# ---------------------------------------------------------------------------
def test_config_validation_rejects_broken_variants():
    with pytest.raises(ConfigError):
        validate_config({"schema_version": "1", "case_id": "x", "quantities": [],
                         "verdict": {"consistent": 0.05, "acceptable": 0.15}})
    with pytest.raises(ConfigError):
        validate_config(synthetic_config(verdict={"consistent": 0.2, "acceptable": 0.1}))
    bad = synthetic_config()
    bad["quantities"] = [{"name": "q1"}, {"name": "q1"}]
    with pytest.raises(ConfigError):
        validate_config(bad)
    with pytest.raises(ConfigError):
        load_config(REPO / "config" / "consistency" / "does-not-exist.json")


def test_arm_admission_rejects_mismatched_case_duplicates_and_untraceable():
    gate = ConsistencyGate(synthetic_config())
    arms = [synthetic_arm("a1", "A"), synthetic_arm("b1", "B", q1=2.01),
            synthetic_arm("a1", "B"),                       # duplicate id
            dict(synthetic_arm("c1", "B"), case_id="synthetic-b"),   # wrong case
            dict(synthetic_arm("d1", "B"), excluded=True,
                 excluded_reason="conservation_fail")]
    admitted, rejected = gate.admit(arms)
    assert [a["arm_id"] for a in admitted] == ["a1", "b1"]
    reasons = sorted(r["reason"] for r in rejected)
    assert reasons == ["case_mismatch", "duplicate_arm_id", "self_excluded"]
    # traceability is reported, and a missing digest is visible, not fatal
    report = gate.run([dict(synthetic_arm("a1", "A"), provenance={"git_commit": "x"}),
                       synthetic_arm("b1", "B", q1=2.01)])
    assert any(not a["traceable"] for a in report["arms"])


def test_single_arm_is_withheld_not_consistent():
    gate = ConsistencyGate(synthetic_config())
    report = gate.run([synthetic_arm("a1", "A")])
    assert report["verdict"] == "withheld"
    for dim in report["dimensions"]:
        assert dim["verdict"] == "withheld"


def test_reference_comparison_flags_arm_outside_reference_band():
    report = run_synthetic(_arms=[synthetic_arm("a1", "A", q1=2.0),
                                  synthetic_arm("b1", "B", q1=2.9)])
    dim = next(d for d in report["dimensions"] if d.get("name") == "q1")
    assert dim["reference"]["verdict"] == "inconsistent"
    assert dim["reference"]["per_arm"]["a1"]["within_tolerance"] is True
    assert dim["reference"]["per_arm"]["b1"]["within_tolerance"] is False
    assert any(inv["dimension"] == "reference" for inv in report["investigations"])


def test_energy_ledger_detects_a_non_closing_arm():
    report = run_synthetic(_arms=[synthetic_arm("a1", "A"),
                                  synthetic_arm("b1", "B", q1=2.01, src=1.0, sink=0.60)])
    edim = next(d for d in report["dimensions"] if d["dimension"] == "energy")
    assert edim["closure_fail_arms"] == ["b1"]
    assert edim["per_arm"]["b1"]["closure_residual"] == pytest.approx(0.40)
    assert edim["verdict"] == "inconsistent"
    assert any(inv["dimension"] == "energy_closure" for inv in report["investigations"])


def test_matrix_is_complete_and_symmetric():
    report = run_synthetic(_arms=[synthetic_arm("a1", "A"), synthetic_arm("b1", "B", q1=2.02),
                                  synthetic_arm("c1", "B", q1=2.04)])
    dim = next(d for d in report["dimensions"] if d.get("name") == "q1")
    mat = dim["matrix"]
    assert set(mat) == {"a1", "b1", "c1"}
    for a in mat:
        assert mat[a][a] == 0.0
        for b in mat:
            assert mat[a][b] == mat[b][a]
    assert report["matrix"]["q1"] == mat
    # three arms -> three pairs
    assert dim["n_pairs"] == 3
    assert dim["max_dev"] == pytest.approx(abs(2.04 - 2.0) / 2.04)


# ---------------------------------------------------------------------------
# report layer
# ---------------------------------------------------------------------------
def test_report_digest_is_stable_and_tampering_is_detected(tmp_path):
    report = run_synthetic()
    path = tmp_path / "report.json"
    digest = write_report(report, path)
    first = verify_report(path)
    assert first["ok"] and first["claimed"] == digest
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["dimensions"][0]["max_dev"] = 0.0        # forge an agreement
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert verify_report(path)["ok"] is False


def test_missing_required_solver_family_forces_a_withheld_verdict():
    """A configured family that never shows up must never be papered over."""
    gate = ConsistencyGate(synthetic_config())
    report = gate.run([synthetic_arm("a1", "A"), synthetic_arm("a2", "A", q1=2.001)])
    assert report["missing_solver_families"] == ["B"]
    assert report["verdict"] == "withheld"
    # an undeclared family is not an error, it is simply not requested
    cfg = synthetic_config()
    cfg["arms"] = {"required": ["A"], "minimum": 2}
    gate2 = ConsistencyGate(cfg)
    report2 = gate2.run([synthetic_arm("a1", "A"), synthetic_arm("a2", "A", q1=2.001)])
    assert report2["missing_solver_families"] == []
    assert report2["verdict"] == "consistent"


def test_configuration_swap_needs_no_code_change():
    """The second domain (plane channel) runs through the same gate object."""
    cfg = load_config(CONFIG_DIR / "plane-channel-laminar-re100.json")
    gate = ConsistencyGate(cfg)
    arms = []
    for arm_id, family, f, uc in (("fdm", "FDM", 0.96, 1.50),
                                  ("spectral", "SPECTRAL", 0.965, 1.505)):
        s = np.linspace(0.0, 1.0, 21)
        arms.append({
            "arm_id": arm_id, "solver_family": family, "solver_version": "1",
            "case_id": cfg["case_id"],
            "quantities": {"friction_factor": f, "centerline_over_mean": uc},
            "profiles": {"u_profile": {"coordinate": list(s),
                                       "value": list(1.5 * (1.0 - (2 * s - 1) ** 2))}},
            "energy": {"terms": {"pump_power": 1.0, "viscous_dissipation": 0.998}},
            "declarations": ["analytic benchmark"],
            "provenance": {"input_sha256": sha256_of(arm_id), "git_commit": "cafe"},
        })
    report = gate.run(arms)
    assert report["case_id"] == "plane-channel-laminar-re100"
    assert report["verdict"] == "consistent"
    names = {d.get("name") for d in report["dimensions"]}
    assert {"friction_factor", "centerline_over_mean", "u_profile", "energy_ledger"} <= names
    # and the reference verdict uses the injected analytic values
    fdim = next(d for d in report["dimensions"] if d.get("name") == "friction_factor")
    assert fdim["reference"]["value"] == pytest.approx(0.96)


# ---------------------------------------------------------------------------
# the arms themselves: analytic certification
# ---------------------------------------------------------------------------
def test_lattice_boltzmann_kernel_matches_two_analytic_solutions():
    from tools.consistency_arms import lbm2d

    tg = lbm2d.taylor_green(n=64, steps=400)
    assert tg["rel_error"] < 0.02, tg          # viscosity from the decay rate
    ch = lbm2d.poiseuille(ny=32, steps=12000)
    assert ch["l2_rel_error"] < 0.01, ch       # wall-normal profile vs parabola
    coarse = lbm2d.poiseuille(ny=16, steps=6000)
    assert coarse["l2_rel_error"] > ch["l2_rel_error"], "refinement must help"


def test_body_fitted_grid_operators_match_the_analytic_potential_flow():
    from tools.consistency_arms import polar2d

    metric = polar2d.metric_check()
    assert metric["laplacian_of_ln_r"] < 1e-9
    chk = polar2d.potential_flow_check(nr=96, ntheta=192)
    assert chk["laplace_of_psi_rel_error"] < 1e-3
    assert chk["fourier_vs_sparse_rel_error"] < 1e-6


# ---------------------------------------------------------------------------
# the shipped case: arm records and the report contract
# ---------------------------------------------------------------------------
def _arm_records():
    return sorted(CASE_ARM_DIR.glob("arm_*.json"))


@pytest.mark.skipif(not _arm_records(), reason="case arm records not generated")
def test_case_arms_carry_provenance_and_finite_numbers():
    for path in _arm_records():
        arm = json.loads(path.read_text(encoding="utf-8"))
        assert arm["provenance"]["input_sha256"]
        assert arm["provenance"]["config_sha256"]
        assert arm["provenance"]["git_commit"]
        assert arm["provenance"]["generated_utc"]
        cd = arm["quantities"]["cd"]
        assert np.isfinite(cd) and cd > 0
        assert arm["profiles"]["cp_surface"]["coordinate"]
        assert len(arm["profiles"]["cp_surface"]["coordinate"]) == \
            len(arm["profiles"]["cp_surface"]["value"])


@pytest.mark.skipif(not (CASE_ARM_DIR / "consistency_report.json").exists(),
                    reason="case report not generated")
def test_case_report_contains_all_three_dimensions_and_a_digest():
    report = json.loads((CASE_ARM_DIR / "consistency_report.json").read_text(encoding="utf-8"))
    assert verify_report(CASE_ARM_DIR / "consistency_report.json")["ok"]
    dims = {d["dimension"] for d in report["dimensions"]}
    assert dims == {"scalar", "profile", "energy"}
    assert report["config"]["verdict_rule"] == {"consistent": 0.05, "acceptable": 0.15}
    # the verdict is one of the declared words and the investigation list is not
    # allowed to hide an inconsistency
    assert report["verdict"] in {"consistent", "acceptable", "acceptable_undeclared",
                                 "inconsistent", "withheld"}
    if report["verdict"] == "inconsistent":
        assert report["investigations"], "an inconsistent verdict must queue an action"
    # the measured cross-solver spread is a number, not a claim
    assert report["deviation_summary"]["cd"]["max_dev"] is not None