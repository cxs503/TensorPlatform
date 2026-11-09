"""Tests for the p422w3-n12 calibrated scorer robustness trials.

Covers, in order:
  1. results-table parsing / fidelity arithmetic (the new scoring channel);
  2. *channel isolation*: a numeric perturbation must not move any v1 dimension
     (otherwise a flip could not be attributed to fabrication);
  3. adversarial cover-up: rewriting ``stated_error_pct`` to match the fabricated
     values must not restore credit (the scorer uses computed-vs-benchmark, not
     the report's own error claim);
  4. the rhetoric leak that motivated the tolerant matcher (gap-0 vs gap-2);
  5. prose-only fallback (no table -> v1 rubric unchanged);
  6. randomized-trial criteria (a)-(e) on a small cohort + defense gate;
  7. reproducibility of the committed artifact vs a fresh run;
  8. pre-registration immutability.
"""

from __future__ import annotations

import json
import os

import pytest

from quality.scorer_robustness.calibrated import (
    FIDELITY_WEIGHT,
    PREREG,
    TAU_HI,
    TAU_LO,
    CalibratedScorer,
    SyntheticReportFactory,
    V1BaselineScorer,
    apply_numeric_perturbation,
    cohens_d_paired,
    cohens_d_pooled,
    evaluate_acceptance,
    parse_results_table,
    row_credit,
    run_arm,
    run_experiment,
    wilson_interval,
)
from quality.scorer_robustness.calibrated.run import GRADES
from quality.scorer_robustness.scorer import ReportScorer

ARTIFACT = "artifacts/m8374/scorer_robustness_trials/results.json"
TABLE = """Report with a results table
| quantity | computed | benchmark | stated_error_pct |
|---|---|---|---|
| Q1 | 102.0000 | 100.0000 | 2.00 |
| Q2 | 49.5000 | 50.0000 | 1.00 |
"""


# --------------------------------------------------------------------------
# 1. parsing / fidelity arithmetic
# --------------------------------------------------------------------------
def test_parse_results_table_skips_header_and_separator():
    rows = parse_results_table(TABLE)
    assert [r.quantity for r in rows] == ["Q1", "Q2"]
    assert rows[0].computed == pytest.approx(102.0)
    assert rows[0].benchmark == pytest.approx(100.0)
    assert rows[0].stated_error_pct == pytest.approx(2.0)


def test_parse_results_table_no_table():
    assert parse_results_table("just prose, no pipes") == []


@pytest.mark.parametrize(
    "dev,expected",
    [(0.0, 1.0), (0.09, 1.0), (TAU_LO, 1.0), (0.125, 0.5), (TAU_HI, 0.0), (0.40, 0.0)],
)
def test_row_credit_tolerance_band(dev, expected):
    assert row_credit(dev) == pytest.approx(expected)


def test_fidelity_penalty_is_weight_times_loss():
    scorer = CalibratedScorer()
    honest = scorer.score(TABLE)
    assert honest.numeric_fidelity == pytest.approx(100.0)

    # Corrupt exactly one of two rows beyond TAU_HI -> fidelity 50.
    corrupted = TABLE.replace("| Q1 | 102.0000 |", "| Q1 | 130.0000 |")
    s = scorer.score(corrupted)
    assert s.numeric_fidelity == pytest.approx(50.0)
    expected_drop = FIDELITY_WEIGHT * 50.0
    assert honest.total - s.total == pytest.approx(expected_drop, abs=1e-9)


# --------------------------------------------------------------------------
# 2. channel isolation
# --------------------------------------------------------------------------
def test_numeric_perturbation_does_not_move_v1_dimensions():
    """A numeric perturbation must only move the fidelity channel."""
    import random

    reports = SyntheticReportFactory(seed=7).build_cohort(5)
    v1 = ReportScorer()
    for rep in reports:
        perturbed, audit = apply_numeric_perturbation(
            rep.text, 0.20, random.Random(f"iso:{rep.report_id}"), coverage_prob=1.0
        )
        assert audit, "table rows must be perturbed"
        a, b = v1.score(rep.text), v1.score(perturbed)
        assert a.total == pytest.approx(b.total, abs=1e-9)
        assert a.data_quality == pytest.approx(b.data_quality, abs=1e-9)
        assert a.reproducibility == pytest.approx(b.reproducibility, abs=1e-9)


# --------------------------------------------------------------------------
# 3. adversarial cover-up
# --------------------------------------------------------------------------
def test_cover_up_of_stated_error_does_not_restore_credit():
    """Rewriting the stated error to match the fabricated numbers is futile."""
    import random

    scorer = CalibratedScorer()
    rep = SyntheticReportFactory(seed=11).build_cohort(3)[0]
    rng = random.Random("coverup")
    plain, audit = apply_numeric_perturbation(rep.text, 0.20, rng, coverage_prob=1.0)

    covered_lines = []
    audit_iter = iter(audit)
    for line in plain.split("\n"):
        rows = parse_results_table(line)
        if rows:
            r = rows[0]
            dev = abs(r.computed - r.benchmark) / abs(r.benchmark)
            covered_lines.append(
                f"| {r.quantity} | {r.computed:.4f} | {r.benchmark:.4f} | {dev * 100:.2f} |"
            )
            next(audit_iter, None)
        else:
            covered_lines.append(line)
    covered = "\n".join(covered_lines)

    s_plain = scorer.score(plain)
    s_covered = scorer.score(covered)
    assert s_covered.numeric_fidelity == pytest.approx(s_plain.numeric_fidelity)
    assert s_covered.total == pytest.approx(s_plain.total, abs=1e-9)
    assert s_plain.numeric_fidelity == pytest.approx(0.0)


# --------------------------------------------------------------------------
# 4. the rhetoric leak and its fix
# --------------------------------------------------------------------------
def test_tolerant_matcher_survives_spliced_modifier():
    text = "Methodology: error systematic analysis and model test."
    v1 = ReportScorer().score(text)
    v2 = CalibratedScorer().score(text)
    assert v1.methodology_rigor < v2.methodology_tolerant  # v1 loses the phrase
    assert v2.methodology_tolerant == pytest.approx(
        ReportScorer().score("Methodology: error analysis and model test.").methodology_rigor
    )


def test_gap0_ablation_reproduces_rhetoric_leak_gap2_fixes_it():
    """Pinned regression: the leak was 1 flip / 200 reports at gap 0."""
    reports = SyntheticReportFactory(seed=int(PREREG["seed"])).build_cohort(
        int(PREREG["n_reports"])
    )
    gap0 = CalibratedScorer(max_phrase_gap=0)
    r0 = run_arm(gap0, reports, "rhetorical", seed=int(PREREG["seed"]))
    r2 = run_arm(CalibratedScorer(), reports, "rhetorical", seed=int(PREREG["seed"]))
    assert r0.n_flips == 1 and r0.n_trials == 200
    assert r2.n_flips == 0
    assert abs(r2.delta_s) <= abs(r0.delta_s) + 1e-12


# --------------------------------------------------------------------------
# 5. prose-only fallback
# --------------------------------------------------------------------------
def test_v1_baseline_is_blind_to_numeric_fabrication():
    """v1 has no fidelity channel: fabricated numbers cannot move its score."""
    import random

    v1 = V1BaselineScorer()
    for rep in SyntheticReportFactory(seed=3).build_cohort(5):
        perturbed, _ = apply_numeric_perturbation(
            rep.text, 0.20, random.Random(f"blind:{rep.report_id}"), coverage_prob=1.0
        )
        assert v1.score(perturbed).total == pytest.approx(v1.score(rep.text).total, abs=1e-9)


def test_prose_only_report_falls_back_to_rubric():
    text = "Methodology: validation against a benchmark with experimental data."
    score = CalibratedScorer().score(text)
    assert score.numeric_fidelity is None
    assert score.n_table_rows == 0
    assert score.total == pytest.approx(score.base.total)


# --------------------------------------------------------------------------
# 6. randomized trials, criteria and defense gate
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def small_experiment():
    factory = SyntheticReportFactory(seed=int(PREREG["seed"]))
    reports = factory.build_cohort(40)
    return run_experiment(reports, scorer=CalibratedScorer(), with_trials=True)


def test_negative_control_never_flips(small_experiment):
    neg = small_experiment["arms"]["negative"]
    assert neg["n_flips"] == 0
    assert neg["flip_rate"] == 0.0


def test_positive_control_detects_defect(small_experiment):
    pos = small_experiment["arms"]["positive"]
    assert pos["flip_rate"] > 0.5
    assert pos["cohens_d"] < 0.0


def test_all_five_criteria_on_small_cohort(small_experiment):
    verdict = evaluate_acceptance(small_experiment)
    failed = [c["criterion"] for c in verdict["criteria"] if not c["passed"]]
    assert verdict["all_criteria_pass"], failed
    assert verdict["n_criteria"] == 5


def test_defense_gate_triggers_on_strong_fabrication(small_experiment):
    gate = small_experiment["defense_gate"]
    assert "numeric@0.20" in gate["triggered_arms"]
    assert "rhetorical" not in gate["triggered_arms"]
    assert "citation" not in gate["triggered_arms"]
    assert gate["decisions"]["numeric@0.20"]["action"] == "human_review"
    assert gate["decisions"]["rhetorical"]["action"] == "auto_accept"


def test_flip_rate_monotone_in_magnitude(small_experiment):
    arms = small_experiment["arms"]
    fr = [arms[f"numeric@{m:.2f}"]["flip_rate"] for m in (0.05, 0.10, 0.20)]
    assert fr[0] <= fr[1] <= fr[2]
    assert fr[2] > 0.5


# --------------------------------------------------------------------------
# 7. statistics helpers
# --------------------------------------------------------------------------
def test_cohens_d_pooled_and_paired():
    a = [10.0, 12.0, 14.0]
    b = [8.0, 10.0, 11.0]
    assert cohens_d_pooled(a, b) < 0
    assert cohens_d_paired(a, b) < 0
    # uniform shift -> paired effect undefined, reported as None (not infinity)
    assert cohens_d_paired([1.0, 2.0, 3.0], [0.0, 1.0, 2.0]) is None


def test_wilson_interval_bounds():
    lo, hi = wilson_interval(0, 200)
    assert 0.0 <= lo < hi < 0.05
    lo, hi = wilson_interval(195, 200)
    assert hi <= 1.0 and lo > 0.9


# --------------------------------------------------------------------------
# 8. reproducibility of the committed artifact + pre-registration
# --------------------------------------------------------------------------
def test_prereg_is_pinned():
    assert PREREG["seed"] == 20261110
    assert tuple(PREREG["magnitudes"]) == (0.05, 0.10, 0.20)
    assert PREREG["flip_review_threshold"] == 0.10
    assert PREREG["criteria"]["a_rhetorical_delta_s_max"] == 0.10
    assert PREREG["criteria"]["c_citation_delta_s_max"] == 0.05


def test_committed_artifact_matches_fresh_run(small_experiment):
    if not os.path.exists(ARTIFACT):
        pytest.skip("artifact not generated in this checkout")
    with open(ARTIFACT, encoding="utf-8") as fh:
        payload = json.load(fh)
    committed = payload["results"]
    assert committed["prereg"]["seed"] == PREREG["seed"]
    assert committed["n_reports"] == PREREG["n_reports"]
    assert payload["acceptance"]["all_criteria_pass"] is True
    # small-cohort run must reproduce the committed per-arm ordering
    for arm in ("rhetorical", "citation", "negative"):
        assert small_experiment["arms"][arm]["flip_rate"] == 0.0
        assert committed["arms"][arm]["flip_rate"] == 0.0
    assert (
        committed["arms"]["numeric@0.20"]["flip_rate"]
        > committed["arms"]["numeric@0.10"]["flip_rate"]
    )


def test_artifact_grade_matrix_totals_consistent():
    if not os.path.exists(ARTIFACT):
        pytest.skip("artifact not generated in this checkout")
    with open(ARTIFACT, encoding="utf-8") as fh:
        payload = json.load(fh)
    trials = payload["results"]["arms"]["numeric@0.20"]["trials"]
    assert len(trials) == payload["results"]["n_reports"]
    for t in trials:
        assert t["grade_original"] in GRADES
        assert t["grade_intervened"] in GRADES
        assert t["flipped"] == (t["grade_original"] != t["grade_intervened"])
        assert t["total_intervened"] <= t["total_original"] + 1e-9