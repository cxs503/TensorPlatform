"""Acceptance tests for the tournament-scorer red team (IN25-T5).

The suite is layered so that a silent error in the guard cannot pass:

1. **anchors**   -- provenance completeness, hash chain tamper/order sensitivity,
   independent re-derivation of every anchor value to <= 1e-12;
2. **analytic**  -- the closed-form score limits (100 / 50 / 0) are pinned to
   machine precision, monotone and deterministic; an exact submission earns
   exactly 100 (known solution -> known score);
3. **attacks**   -- every Goodhart attack case is rejected with its reason code,
   the cherry-picking case is capped;
4. **physics**   -- conservation/invariance gates pass consistent claims and flag
   violated ones;
5. **mutation**  -- all semantic mutants are killed and all control mutants
   survive (negative controls for the tests themselves);
6. **prereg**    -- the version lock round-trips and detects anchor / spec /
   case-suite drift.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.scorer_guard import anchors as A  # noqa: E402
from tools.scorer_guard import cli as CLI  # noqa: E402
from tools.scorer_guard import mutation as M  # noqa: E402
from tools.scorer_guard import physics  # noqa: E402
from tools.scorer_guard import prereg  # noqa: E402
from tools.scorer_guard import redteam as R  # noqa: E402
from tools.scorer_guard import score as S  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def registry():
    return A.build_default_registry()


@pytest.fixture(scope="module")
def ctx(registry):
    return R.make_ctx(registry)


@pytest.fixture(scope="module")
def report(ctx):
    return R.run_cases(ctx)


@pytest.fixture(scope="module")
def mutation_result():
    return M.run_mutation()


def _case(report, case_id):
    for rec in report["cases"]:
        if rec["case_id"] == case_id:
            return rec
    raise KeyError(case_id)


# --------------------------------------------------------------------------- #
# 1. anchors / provenance
# --------------------------------------------------------------------------- #
def test_anchor_provenance_complete_and_ledger_verified(registry):
    ledger = A.verify_ledger(registry)
    assert ledger["ok"], ledger
    statuses = [rec["provenance_status"] for rec in registry["anchors"]]
    assert statuses.count("VERIFIED") == 5
    assert statuses.count("QUARANTINED") == 1
    for rec in registry["anchors"]:
        problems = A.validate_anchor(rec)
        assert problems == [], (rec["anchor_id"], problems)
        if rec["provenance_status"] == "VERIFIED":
            assert rec["value"] is not None
            assert rec["source"]["citation"]
            assert rec["source"]["formula_id"]


def test_anchor_values_independently_rederived(registry):
    prov = A.provenance_report(registry)
    assert prov["max_rel_dev_verified"] <= 1e-12, prov["max_rel_dev_verified"]
    for row in prov["anchors"]:
        if row["provenance_status"] == "VERIFIED":
            assert row["rel_dev"] <= 1e-12
            assert row["recompute_method"]
    by_id = {a.anchor_id: a for a in A.anchors_from(registry)}
    # ITTC-1957 at Re=1e7 is the exact rational 3/40/25 = 0.003
    assert by_id["A-CF-ITTC57-RE1E7"].value == 0.003
    # two independent friction lines must agree to <1% at Re=1e9 (physical sanity)
    ks = by_id["A-CF-KS-RE1E9"].value
    ittc = by_id["A-CF-ITTC57-RE1E9"].value
    assert abs(ks - ittc) / ittc < 0.01


def test_anchor_ledger_detects_tamper_and_reorder(registry):
    tampered = json.loads(json.dumps(registry))
    tampered["anchors"][1]["value"] = float(tampered["anchors"][1]["value"]) * 1.05
    assert not A.verify_ledger(tampered)["ok"]
    reordered = json.loads(json.dumps(registry))
    reordered["anchors"][0], reordered["anchors"][1] = (
        reordered["anchors"][1], reordered["anchors"][0],
    )
    assert A.chain_head(reordered["anchors"]) != A.chain_head(registry["anchors"])


# --------------------------------------------------------------------------- #
# 2. analytic limits: known solution -> known score
# --------------------------------------------------------------------------- #
def test_analytic_limits_are_exact(ctx):
    cf7 = R.by_id(ctx["anchors"], "A-CF-ITTC57-RE1E7")
    value, emax = float(cf7.value), float(cf7.score_e_max)
    assert S.anchor_score(value, cf7)["score"] == pytest.approx(100.0, abs=1e-12)
    assert S.anchor_score(value * (1 + emax / 2), cf7)["score"] == pytest.approx(50.0, abs=1e-9)
    assert S.anchor_score(value * (1 - emax / 2), cf7)["score"] == pytest.approx(50.0, abs=1e-9)
    assert S.anchor_score(value * (1 + emax), cf7)["score"] == pytest.approx(0.0, abs=1e-12)
    assert S.anchor_score(value * (1 + 5 * emax), cf7)["score"] == pytest.approx(0.0, abs=1e-12)


def test_score_is_monotone_and_deterministic(ctx):
    cf7 = R.by_id(ctx["anchors"], "A-CF-ITTC57-RE1E7")
    v = float(cf7.value)
    scores = [S.anchor_score(v * (1 + i / 100.0), cf7)["score"] for i in range(0, 51)]
    assert all(scores[i] >= scores[i + 1] - 1e-12 for i in range(len(scores) - 1)), scores
    a = S.score_submission(R.entries(ctx["anchors"]), ctx["anchors"], anchor_head=ctx["head"])
    b = S.score_submission(R.entries(ctx["anchors"]), ctx["anchors"], anchor_head=ctx["head"])
    assert S.canonical_json(a) == S.canonical_json(b)


def test_exact_submission_earns_full_marks_and_responds_to_perturbation(ctx):
    exact = S.score_submission(R.entries(ctx["anchors"]), ctx["anchors"],
                               anchor_head=ctx["head"], expected_fingerprint=ctx["fingerprint"])
    assert exact["verdict"] == "ACCEPTED"
    assert exact["score"] == pytest.approx(100.0, abs=1e-9)
    assert exact["per_anchor"]["A-CF-ITTC57-RE1E7"]["rel_err"] == 0.0

    # sensitivity floor: a 1% error must cost 20 points on an e_max=5% anchor,
    # i.e. the scorer may never be a flat line (Goodhart's silent enabler)
    pert = S.score_submission(R.entries(ctx["anchors"], {"A-CF-ITTC57-RE1E7": 0.01}),
                              ctx["anchors"], anchor_head=ctx["head"])
    drop = 100.0 - pert["per_anchor"]["A-CF-ITTC57-RE1E7"]["score"]
    assert drop == pytest.approx(20.0, abs=1e-6)
    assert pert["score"] < 100.0


# --------------------------------------------------------------------------- #
# 3. Goodhart attacks must be intercepted
# --------------------------------------------------------------------------- #
ATTACK_IDS = [
    "A1-constant-output", "A2-unit-decade", "A3-nan", "A4-out-of-envelope",
    "A5-regime-slop", "A6-missing-anchor", "A7-quarantined-anchor", "A8-anchor-leak",
    "A9-physics-froude", "A10-physics-thrust", "A11-replay", "A12-unlocked-scorer",
]


@pytest.mark.parametrize("case_id", ATTACK_IDS)
def test_each_attack_is_intercepted(report, case_id):
    rec = _case(report, case_id)
    assert rec["must_intercept"] is True
    assert rec["passed"], rec["problems"]
    out = rec["output"]
    assert out["verdict"] == "REJECTED", out
    assert out["score"] == 0.0
    assert rec["expect"]["code"] in out["codes"], out["codes"]


def test_all_cases_pass_and_all_attacks_counted(report):
    assert report["n_failed"] == 0, [c["case_id"] for c in report["cases"] if not c["passed"]]
    assert report["n_cases"] >= 30
    assert report["n_attacks"] == len(ATTACK_IDS)
    assert report["n_attacks_intercepted"] == report["n_attacks"]


def test_cherry_picking_is_capped_by_worst_case_half(report):
    rec = _case(report, "A13-cherry-pick")
    assert rec["passed"], rec["problems"]
    out = rec["output"]
    assert out["verdict"] == "ACCEPTED"
    # 4 anchors aced + 1 anchor bombed: mean 80, worst 0 -> 40, never 80
    assert out["score"] == pytest.approx(40.0, abs=1e-9)
    assert out["score"] < 60.0
    assert out["mean"] > out["score"] > out["worst"]


# --------------------------------------------------------------------------- #
# 4. physics / conservation cross-checks
# --------------------------------------------------------------------------- #
def test_physics_invariants_pass_and_flag():
    ok_claims = {
        "froude_invariance": {"cw_model": 0.001234, "cw_ship": 0.001234},
        "additivity": {"ct": (1.15) * 0.003 + 0.0009, "cf": 0.003, "form_factor": 0.15,
                       "cw": 0.0009},
        "hydrostatic_mass": {"rho": 1025.0, "cb": 0.65, "length": 120.0, "beam": 20.0,
                             "draught": 7.0,
                             "delta_reported": physics.hydrostatic_mass(1025.0, 0.65, 120.0,
                                                                       20.0, 7.0)},
        "thrust_balance": {"thrust": 1000.0, "thrust_deduction": 0.2, "resistance": 800.0},
    }
    assert physics.check_claims(ok_claims) == []

    bad = json.loads(json.dumps(ok_claims))
    bad["froude_invariance"]["cw_ship"] *= 1.05        # Froude invariance broken
    bad["thrust_balance"]["resistance"] *= 1.2         # self-propulsion not in equilibrium
    bad["additivity"]["ct"] *= 1.001                   # decomposition does not close
    bad["hydrostatic_mass"]["delta_reported"] *= 0.98  # mass identity broken
    flagged = {v["name"] for v in physics.check_claims(bad)}
    assert flagged == {"froude_invariance", "thrust_balance", "additivity", "hydrostatic_mass"}


# --------------------------------------------------------------------------- #
# 5. mutation negative controls
# --------------------------------------------------------------------------- #
def test_all_semantic_mutants_killed(mutation_result):
    assert mutation_result["n_semantic"] >= 15
    killed = [m for m in mutation_result["mutants"] if m["semantic"] and m["status"] == "KILLED"]
    assert len(killed) == mutation_result["n_semantic"], [
        m["mutant_id"] for m in mutation_result["mutants"]
        if m["semantic"] and m["status"] != "KILLED"
    ]
    assert mutation_result["mutation_score"] == 1.0
    assert mutation_result["ok"] is True


def test_control_mutants_survive(mutation_result):
    controls = [m for m in mutation_result["mutants"] if not m["semantic"]]
    assert controls, "no control mutants defined"
    survivors = [m for m in controls if m["status"] == "SURVIVED"]
    assert len(survivors) == len(controls), [m["mutant_id"] for m in controls
                                             if m["status"] != "SURVIVED"]


def test_each_killed_mutant_has_a_negative_example(mutation_result):
    for m in mutation_result["mutants"]:
        if m["semantic"]:
            ex = m.get("negative_example") or {}
            assert ex.get("case_id"), m["mutant_id"]
            assert "pristine" in ex and "mutant" in ex, m["mutant_id"]
            assert m["differing_cases"], m["mutant_id"]


# --------------------------------------------------------------------------- #
# 6. pre-registration lock
# --------------------------------------------------------------------------- #
def test_prereg_lock_roundtrip_and_drift_detection(registry, report):
    case_ids = [c["case_id"] for c in report["cases"]]
    lock = prereg.build_lock(registry, case_ids)
    assert prereg.verify_lock(lock, registry, case_ids)["ok"] is True
    assert lock["fingerprint"] == S.scorer_fingerprint(lock["anchor_head"])

    drifted = json.loads(json.dumps(registry))
    drifted["anchors"][2]["value"] = float(drifted["anchors"][2]["value"]) * 1.02
    drift = prereg.verify_lock(lock, drifted, case_ids)
    assert drift["ok"] is False
    assert "ANCHOR_HEAD_DRIFT" in drift["drift"]
    assert "FINGERPRINT_DRIFT" in drift["drift"]

    suite = prereg.verify_lock(lock, registry, case_ids + ["X-not-in-lock"])
    assert "CASE_SUITE_DRIFT" in suite["drift"]


def test_scorer_fingerprint_binds_head_and_version(ctx, registry):
    fp = S.scorer_fingerprint(ctx["head"])
    other = A.build_default_registry()
    other["anchors"][0]["value"] = float(other["anchors"][0]["value"]) * 1.01
    assert S.scorer_fingerprint(A.chain_head(other["anchors"])) != fp
    assert S.scorer_fingerprint(ctx["head"], version="9.9.9") != fp
    # unscored drift: the lock gate itself must refuse
    out = S.score_submission(R.entries(ctx["anchors"]), ctx["anchors"],
                             anchor_head=ctx["head"], expected_fingerprint="0" * 64)
    assert out["verdict"] == "REJECTED"
    assert any(r["code"] == "SCORER_LOCK_MISMATCH" for r in out["reasons"])


# --------------------------------------------------------------------------- #
# 7. artifacts
# --------------------------------------------------------------------------- #
def test_cli_produces_complete_artifact_set(tmp_path):
    out_dir = tmp_path / "artifacts"
    doc = tmp_path / "docs" / "report.md"
    summary = CLI.build_all(out_dir, doc, write_registry=False)
    assert set(summary["artifacts"]) >= {"registry", "lock", "redteam", "mutation",
                                         "provenance", "doc", "figures"}
    for name in ("redteam_report.json", "mutation_negative_examples.json",
                 "anchor_provenance.json", "scorer_prereg_lock_v1.json",
                 "fig1_score_response.png", "fig2_mutation_matrix.png",
                 "fig3_anchor_provenance.png"):
        assert (out_dir / name).is_file(), name
    assert doc.is_file() and doc.read_text(encoding="utf-8").startswith("# IN25-T5")
    assert summary["n_cases_passed"] == summary["n_cases"]
    assert summary["n_attacks_intercepted"] == summary["n_attacks"]
    assert summary["mutation_score"] == 1.0
    assert summary["lock_ok"] is True
    assert summary["max_anchor_rel_dev"] <= 1e-12


def test_committed_artifacts_match_generator(registry):
    """The committed registry must equal the freshly derived one (no hand edits)."""
    committed = A.load_registry()
    assert committed["ledger"] == registry["ledger"]
    assert [r["anchor_id"] for r in committed["anchors"]] == [
        r["anchor_id"] for r in registry["anchors"]
    ]
    for a, b in zip(committed["anchors"], registry["anchors"]):
        assert a["value"] == b["value"], a["anchor_id"]
    lock_path = ROOT / "artifacts" / "scorer_guard" / "scorer_prereg_lock_v1.json"
    assert lock_path.is_file()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["anchor_head"] == registry["ledger"]["head"]