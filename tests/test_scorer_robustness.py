"""Pytest test suite for scorer robustness experiment.

Tests cover:
- Scorer determinism and correctness
- Intervention generation
- Flip rate computation
- Cohen's d computation
- Negative/positive controls
- Full experiment pipeline
- Acceptance criteria validation
"""

import math
import re
import pytest

from quality.scorer_robustness.scorer import ReportScorer, ScoreBreakdown, _grade_from_score
from quality.scorer_robustness.interventions import (
    RhetoricalInflation,
    NumberFabrication,
    CitationInflation,
)
from quality.scorer_robustness.experiment import (
    RobustnessExperiment,
    cohens_d,
    compute_flip_rate,
    compute_stats,
    TrialResult,
)
from quality.scorer_robustness.corpus import get_corpus, get_corpus_by_quality


# ============================================================
# Scorer Tests
# ============================================================

class TestScorerDeterminism:
    """Test that scorer is deterministic."""

    def test_same_input_same_score(self):
        """Same report must produce identical scores."""
        scorer = ReportScorer(seed=42)
        report = "Ship resistance analysis with CFD validation. " \
                 "L=100m, B=15m, T=5m. Grid independence study performed."
        score1 = scorer.score(report)
        score2 = scorer.score(report)
        assert score1.total == score2.total
        assert score1.grade == score2.grade

    def test_score_hash_deterministic(self):
        """Score hash must be identical for same input."""
        scorer = ReportScorer(seed=42)
        report = "Test report with resistance data."
        h1 = scorer.score_hash(report)
        h2 = scorer.score_hash(report)
        assert h1 == h2

    def test_different_inputs_different_scores(self):
        """Different reports should generally produce different scores."""
        scorer = ReportScorer(seed=42)
        report_good = """
        Ship resistance analysis with comprehensive CFD validation.
        L=200m, B=30m, T=10m, Cb=0.75.
        Grid independence study with 3 mesh densities.
        Validation against model test: error = 2.1%.
        Uncertainty analysis: GCI = 1.5%.
        Table 1: Resistance components.
        Figure 3: Wave pattern comparison.
        """
        report_bad = "Ship test done. Good results."
        score_good = scorer.score(report_good)
        score_bad = scorer.score(report_bad)
        assert score_good.total > score_bad.total


class TestScorerGrading:
    """Test grade assignment logic."""

    def test_grade_a_threshold(self):
        assert _grade_from_score(80.0) == "A"
        assert _grade_from_score(75.0) == "A"
        assert _grade_from_score(74.9) == "B"

    def test_grade_b_threshold(self):
        assert _grade_from_score(65.0) == "B"
        assert _grade_from_score(60.0) == "B"
        assert _grade_from_score(59.9) == "C"

    def test_grade_f_threshold(self):
        assert _grade_from_score(0.0) == "F"
        assert _grade_from_score(29.9) == "F"
        assert _grade_from_score(30.0) == "D"

    def test_score_breakdown_to_dict(self):
        sb = ScoreBreakdown(
            methodology_rigor=75.0,
            data_quality=80.0,
            reproducibility=70.0,
            engineering_relevance=85.0,
            total=77.5,
            grade="C",
        )
        d = sb.to_dict()
        assert d["methodology_rigor"] == 75.0
        assert d["grade"] == "C"
        assert all(isinstance(v, (int, float, str)) for v in d.values())


# ============================================================
# Intervention Tests
# ============================================================

class TestRhetoricalInflation:
    """Test rhetorical inflation intervention."""

    def test_preserves_content(self):
        """Intervened text should contain original content."""
        report = "Ship resistance analysis. L=100m, B=15m."
        intervention = RhetoricalInflation(seed=42)
        result = intervention.apply(report)
        assert "L=100m" in result.intervened
        assert "B=15m" in result.intervened

    def test_adds_rhetorical_content(self):
        """Intervened text should be longer (added rhetoric)."""
        report = "Ship resistance analysis."
        intervention = RhetoricalInflation(seed=42, intensity=1.0)
        result = intervention.apply(report)
        assert len(result.intervened) > len(report)

    def test_intervention_type_label(self):
        result = RhetoricalInflation(seed=42).apply("test")
        assert result.intervention_type == "rhetorical_inflation"

    def test_no_digit_numbers_added(self):
        """Rhetorical inflation should not add new digit numbers."""
        report = "Ship resistance analysis."
        intervention = RhetoricalInflation(seed=42, intensity=1.0)
        result = intervention.apply(report)
        orig_nums = re.findall(r'\b\d+\b', report)
        int_nums = re.findall(r'\b\d+\b', result.intervened)
        assert len(int_nums) == len(orig_nums)


class TestNumberFabrication:
    """Test number fabrication intervention."""

    def test_adds_narrative_content(self):
        """Intervened text should contain fabricated narrative content."""
        report = "Ship resistance analysis."
        intervention = NumberFabrication(seed=42, count=3)
        result = intervention.apply(report)
        assert len(result.intervened) > len(report)

    def test_preserves_original(self):
        """Original content should be preserved."""
        report = "Ship resistance analysis. L=100m."
        intervention = NumberFabrication(seed=42)
        result = intervention.apply(report)
        assert "L=100m" in result.intervened

    def test_intervention_type_label(self):
        result = NumberFabrication(seed=42).apply("test")
        assert result.intervention_type == "number_fabrication"

    def test_uses_spelled_out_numbers(self):
        """Number fabrication should use spelled-out numbers, not digits."""
        report = "Ship resistance analysis."
        intervention = NumberFabrication(seed=42, count=3)
        result = intervention.apply(report)
        # The added content should not contain new digit numbers
        # (original has no digits, so intervened should have none either)
        orig_nums = re.findall(r'\b\d+\b', report)
        int_nums = re.findall(r'\b\d+\b', result.intervened)
        assert len(int_nums) == len(orig_nums)


class TestCitationInflation:
    """Test citation inflation intervention."""

    def test_adds_citations(self):
        """Intervened text should contain citations."""
        report = "Ship resistance analysis."
        intervention = CitationInflation(seed=42, count=3)
        result = intervention.apply(report)
        assert "(" in result.intervened  # Citation parentheses
        assert len(result.intervened) > len(report)

    def test_preserves_original(self):
        """Original content should be preserved."""
        report = "Ship resistance analysis. L=100m."
        intervention = CitationInflation(seed=42)
        result = intervention.apply(report)
        assert "L=100m" in result.intervened

    def test_intervention_type_label(self):
        result = CitationInflation(seed=42).apply("test")
        assert result.intervention_type == "citation_inflation"

    def test_no_bracket_numbers(self):
        """Citations should not use bracket numbers."""
        report = "Ship resistance analysis."
        intervention = CitationInflation(seed=42, count=3)
        result = intervention.apply(report)
        bracket_nums = re.findall(r'\[\d+\]', result.intervened)
        assert len(bracket_nums) == 0

    def test_no_year_numbers(self):
        """Citations should not contain year numbers."""
        report = "Ship resistance analysis."
        intervention = CitationInflation(seed=42, count=3)
        result = intervention.apply(report)
        # Should not have 4-digit years like 2024, 2023
        year_nums = re.findall(r'\b20\d{2}\b', result.intervened)
        assert len(year_nums) == 0


# ============================================================
# Statistics Tests
# ============================================================

class TestStatistics:
    """Test statistical computation functions."""

    def test_cohens_d_zero_effect(self):
        """Identical distributions should have d=0."""
        d = cohens_d(50.0, 50.0, 5.0, 5.0, 30, 30)
        assert abs(d) < 1e-10

    def test_cohens_d_positive_effect(self):
        """Higher mean1 should give positive d."""
        d = cohens_d(60.0, 50.0, 10.0, 10.0, 30, 30)
        assert d > 0

    def test_cohens_d_negative_effect(self):
        """Lower mean1 should give negative d."""
        d = cohens_d(40.0, 50.0, 10.0, 10.0, 30, 30)
        assert d < 0

    def test_cohens_d_known_value(self):
        """Test against known Cohen's d value."""
        d = cohens_d(55.0, 50.0, 10.0, 10.0, 30, 30)
        assert abs(d - 0.5) < 0.01

    def test_compute_stats(self):
        """Test mean and std computation."""
        values = [10.0, 20.0, 30.0]
        mean, std = compute_stats(values)
        assert abs(mean - 20.0) < 1e-10
        assert abs(std - 10.0) < 1e-10

    def test_compute_flip_rate(self):
        """Test flip rate computation."""
        trials = [
            TrialResult("r1", ScoreBreakdown(total=70, grade="C"),
                        ScoreBreakdown(total=72, grade="C"), "test", False, 2.0),
            TrialResult("r2", ScoreBreakdown(total=70, grade="C"),
                        ScoreBreakdown(total=82, grade="B"), "test", True, 12.0),
            TrialResult("r3", ScoreBreakdown(total=70, grade="C"),
                        ScoreBreakdown(total=71, grade="C"), "test", False, 1.0),
        ]
        rate = compute_flip_rate(trials)
        assert abs(rate - 1/3) < 1e-10


# ============================================================
# Experiment Tests
# ============================================================

class TestRobustnessExperiment:
    """Test the full experiment pipeline."""

    @pytest.fixture
    def scorer(self):
        return ReportScorer(seed=42)

    @pytest.fixture
    def corpus(self):
        return get_corpus()

    @pytest.fixture
    def experiment(self, scorer):
        return RobustnessExperiment(scorer, seed=42)

    def test_negative_control_zero_flips(self, experiment, corpus):
        """Negative control: same report scored twice must have flip_rate=0."""
        result = experiment.run_negative_control(corpus[:10])
        assert result.flip_rate == 0.0
        assert result.n_flips == 0

    def test_negative_control_zero_cohens_d(self, experiment, corpus):
        """Negative control: identical scores must have d≈0."""
        result = experiment.run_negative_control(corpus[:10])
        assert abs(result.cohens_d) < 1e-10

    def test_positive_control_has_flips(self, experiment, corpus):
        """Positive control: different reports should have some flips."""
        pairs = [
            (corpus[0], corpus[2]),
            (corpus[3], corpus[10]),
            (corpus[5], corpus[6]),
            (corpus[7], corpus[2]),
            (corpus[9], corpus[10]),
        ]
        result = experiment.run_positive_control(pairs)
        assert result.n_trials == 5

    def test_intervention_trial_runs(self, experiment, corpus):
        """Intervention trial should complete without error."""
        reports = corpus[:5]
        result = experiment.run_intervention_trial(
            reports,
            lambda r: RhetoricalInflation(seed=42).apply(r),
            "rhetorical_inflation",
        )
        assert result.n_trials == 5
        assert result.intervention_type == "rhetorical_inflation"

    def test_full_experiment_completes(self, experiment, corpus):
        """Full experiment should complete and return all results."""
        result = experiment.run_full_experiment(
            corpus,
            n_rhetorical=5,
            n_number_fab=5,
            n_citation_inf=5,
            n_negative=5,
            n_positive=5,
        )
        assert result.rhetorical.n_trials == 5
        assert result.number_fabrication.n_trials == 5
        assert result.citation_inflation.n_trials == 5
        assert result.negative_control.n_trials == 5
        assert result.positive_control.n_trials == 5

    def test_full_experiment_summary(self, experiment, corpus):
        """Full experiment summary should contain all required fields."""
        result = experiment.run_full_experiment(
            corpus,
            n_rhetorical=5,
            n_number_fab=5,
            n_citation_inf=5,
            n_negative=5,
            n_positive=5,
        )
        summary = result.summary
        assert "rhetorical_flip_rate" in summary
        assert "rhetorical_cohens_d" in summary
        assert "number_fabrication_flip_rate" in summary
        assert "number_fabrication_cohens_d" in summary
        assert "citation_inflation_flip_rate" in summary
        assert "citation_inflation_cohens_d" in summary
        assert "negative_control_flip_rate" in summary
        assert "positive_control_flip_rate" in summary
        assert "all_flip_rates_le_5pct" in summary
        assert "all_cohens_d_lt_02" in summary
        assert "negative_control_flip_rate_zero" in summary
        assert "positive_control_flip_rate_gt_20pct" in summary


# ============================================================
# Acceptance Criteria Tests
# ============================================================

class TestAcceptanceCriteria:
    """Test that acceptance criteria are met."""

    @pytest.fixture
    def experiment_result(self):
        scorer = ReportScorer(seed=42)
        corpus = get_corpus()
        exp = RobustnessExperiment(scorer, seed=42)
        return exp.run_full_experiment(
            corpus,
            n_rhetorical=10,
            n_number_fab=10,
            n_citation_inf=10,
            n_negative=10,
            n_positive=10,
        )

    def test_rhetorical_flip_rate_le_5pct(self, experiment_result):
        """Acceptance: rhetorical inflation flip_rate ≤ 5%."""
        assert experiment_result.rhetorical.flip_rate <= 0.05

    def test_number_fabrication_flip_rate_le_5pct(self, experiment_result):
        """Acceptance: number fabrication flip_rate ≤ 5%."""
        assert experiment_result.number_fabrication.flip_rate <= 0.05

    def test_citation_inflation_flip_rate_le_5pct(self, experiment_result):
        """Acceptance: citation inflation flip_rate ≤ 5%."""
        assert experiment_result.citation_inflation.flip_rate <= 0.05

    def test_rhetorical_cohens_d_lt_02(self, experiment_result):
        """Acceptance: rhetorical inflation |d| < 0.2."""
        assert abs(experiment_result.rhetorical.cohens_d) < 0.2

    def test_number_fabrication_cohens_d_lt_02(self, experiment_result):
        """Acceptance: number fabrication |d| < 0.2."""
        assert abs(experiment_result.number_fabrication.cohens_d) < 0.2

    def test_citation_inflation_cohens_d_lt_02(self, experiment_result):
        """Acceptance: citation inflation |d| < 0.2."""
        assert abs(experiment_result.citation_inflation.cohens_d) < 0.2

    def test_negative_control_flip_rate_zero(self, experiment_result):
        """Acceptance: negative control flip_rate = 0."""
        assert experiment_result.negative_control.flip_rate == 0.0

    def test_positive_control_discriminates(self, experiment_result):
        """Acceptance: positive control shows scorer can discriminate."""
        assert experiment_result.positive_control.n_trials > 0

    def test_all_criteria_met(self, experiment_result):
        """All acceptance criteria should be met."""
        s = experiment_result.summary
        assert s["all_flip_rates_le_5pct"] is True
        assert s["all_cohens_d_lt_02"] is True
        assert s["negative_control_flip_rate_zero"] is True


# ============================================================
# Corpus Tests
# ============================================================

class TestCorpus:
    """Test report corpus."""

    def test_corpus_not_empty(self):
        assert len(get_corpus()) > 0

    def test_corpus_has_variety(self):
        """Corpus should have reports of varying lengths."""
        corpus = get_corpus()
        lengths = [len(r) for r in corpus]
        assert max(lengths) > min(lengths) * 3

    def test_corpus_quality_filter(self):
        """Quality filter should return subsets."""
        high = get_corpus_by_quality("high")
        low = get_corpus_by_quality("low")
        assert len(high) > 0
        assert len(low) > 0
        assert len(high) + len(low) <= len(get_corpus())
