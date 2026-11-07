"""Experiment framework for scorer robustness randomized trials.

Runs controlled experiments measuring:
- flip_rate: fraction of reports where grade changes after intervention
- Cohen's d: effect size of intervention on scores
- Negative control: same report scored twice (should have flip_rate=0)
- Positive control: different reports (should have flip_rate>20%)
"""

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .scorer import ReportScorer, ScoreBreakdown
from .interventions import (
    CitationInflation,
    InterventionResult,
    NumberFabrication,
    RhetoricalInflation,
)


@dataclass
class TrialResult:
    """Result of a single scoring trial."""
    report_id: str
    original_score: ScoreBreakdown
    intervened_score: Optional[ScoreBreakdown] = None
    intervention_type: Optional[str] = None
    flipped: bool = False
    score_diff: float = 0.0


@dataclass
class ExperimentResult:
    """Aggregated results for one intervention type."""
    intervention_type: str
    n_trials: int
    n_flips: int
    flip_rate: float
    mean_original: float
    mean_intervened: float
    std_original: float
    std_intervened: float
    cohens_d: float
    trials: List[TrialResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "intervention_type": self.intervention_type,
            "n_trials": self.n_trials,
            "n_flips": self.n_flips,
            "flip_rate": round(self.flip_rate, 4),
            "mean_original": round(self.mean_original, 4),
            "mean_intervened": round(self.mean_intervened, 4),
            "std_original": round(self.std_original, 4),
            "std_intervened": round(self.std_intervened, 4),
            "cohens_d": round(self.cohens_d, 4),
        }


@dataclass
class FullExperimentResult:
    """Complete experiment results across all interventions."""
    rhetorical: ExperimentResult
    number_fabrication: ExperimentResult
    citation_inflation: ExperimentResult
    negative_control: ExperimentResult
    positive_control: ExperimentResult
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rhetorical_inflation": self.rhetorical.to_dict(),
            "number_fabrication": self.number_fabrication.to_dict(),
            "citation_inflation": self.citation_inflation.to_dict(),
            "negative_control": self.negative_control.to_dict(),
            "positive_control": self.positive_control.to_dict(),
            "summary": self.summary,
        }


def cohens_d(mean1: float, mean2: float, std1: float, std2: float,
             n1: int, n2: int) -> float:
    """Compute Cohen's d effect size.

    Uses pooled standard deviation:
    d = (mean1 - mean2) / sqrt(((n1-1)*std1^2 + (n2-1)*std2^2) / (n1+n2-2))
    """
    if n1 + n2 < 3:
        return 0.0

    pooled_std = math.sqrt(
        ((n1 - 1) * std1 ** 2 + (n2 - 1) * std2 ** 2) / (n1 + n2 - 2)
    )
    if pooled_std < 1e-10:
        return 0.0

    return (mean1 - mean2) / pooled_std


def compute_flip_rate(trials: List[TrialResult]) -> float:
    """Compute fraction of trials where grade changed."""
    if not trials:
        return 0.0
    n_flips = sum(1 for t in trials if t.flipped)
    return n_flips / len(trials)


def compute_stats(scores: List[float]) -> Tuple[float, float]:
    """Compute mean and standard deviation."""
    if not scores:
        return 0.0, 0.0
    n = len(scores)
    mean = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / max(n - 1, 1)
    std = math.sqrt(variance)
    return mean, std


class RobustnessExperiment:
    """Run robustness experiments on a report scorer."""

    def __init__(self, scorer: ReportScorer, seed: int = 42):
        self.scorer = scorer
        self.rng = random.Random(seed)
        self.seed = seed

    def run_intervention_trial(
        self,
        reports: List[str],
        intervention: Callable[[str], InterventionResult],
        intervention_type: str,
    ) -> ExperimentResult:
        """Run trials for one intervention type."""
        trials = []
        original_scores = []
        intervened_scores = []

        for i, report in enumerate(reports):
            orig_score = self.scorer.score(report)
            result = intervention(report)
            int_score = self.scorer.score(result.intervened)

            flipped = orig_score.grade != int_score.grade
            score_diff = int_score.total - orig_score.total

            trial = TrialResult(
                report_id=f"report_{i}",
                original_score=orig_score,
                intervened_score=int_score,
                intervention_type=intervention_type,
                flipped=flipped,
                score_diff=score_diff,
            )
            trials.append(trial)
            original_scores.append(orig_score.total)
            intervened_scores.append(int_score.total)

        n_flips = sum(1 for t in trials if t.flipped)
        mean_orig, std_orig = compute_stats(original_scores)
        mean_int, std_int = compute_stats(intervened_scores)
        d = cohens_d(mean_orig, mean_int, std_orig, std_int,
                     len(original_scores), len(intervened_scores))

        return ExperimentResult(
            intervention_type=intervention_type,
            n_trials=len(trials),
            n_flips=n_flips,
            flip_rate=n_flips / max(len(trials), 1),
            mean_original=mean_orig,
            mean_intervened=mean_int,
            std_original=std_orig,
            std_intervened=std_int,
            cohens_d=d,
            trials=trials,
        )

    def run_negative_control(self, reports: List[str]) -> ExperimentResult:
        """Negative control: score same report twice, expect flip_rate=0."""
        trials = []
        scores_run1 = []
        scores_run2 = []

        for i, report in enumerate(reports):
            score1 = self.scorer.score(report)
            score2 = self.scorer.score(report)  # Same report, should be identical

            flipped = score1.grade != score2.grade
            score_diff = score2.total - score1.total

            trial = TrialResult(
                report_id=f"report_{i}",
                original_score=score1,
                intervened_score=score2,
                intervention_type="negative_control",
                flipped=flipped,
                score_diff=score_diff,
            )
            trials.append(trial)
            scores_run1.append(score1.total)
            scores_run2.append(score2.total)

        n_flips = sum(1 for t in trials if t.flipped)
        mean1, std1 = compute_stats(scores_run1)
        mean2, std2 = compute_stats(scores_run2)
        d = cohens_d(mean1, mean2, std1, std2, len(scores_run1), len(scores_run2))

        return ExperimentResult(
            intervention_type="negative_control",
            n_trials=len(trials),
            n_flips=n_flips,
            flip_rate=n_flips / max(len(trials), 1),
            mean_original=mean1,
            mean_intervened=mean2,
            std_original=std1,
            std_intervened=std2,
            cohens_d=d,
            trials=trials,
        )

    def run_positive_control(
        self,
        report_pairs: List[Tuple[str, str]],
    ) -> ExperimentResult:
        """Positive control: score different reports, expect flip_rate>20%."""
        trials = []
        scores_a = []
        scores_b = []

        for i, (report_a, report_b) in enumerate(report_pairs):
            score_a = self.scorer.score(report_a)
            score_b = self.scorer.score(report_b)

            flipped = score_a.grade != score_b.grade
            score_diff = score_b.total - score_a.total

            trial = TrialResult(
                report_id=f"pair_{i}",
                original_score=score_a,
                intervened_score=score_b,
                intervention_type="positive_control",
                flipped=flipped,
                score_diff=score_diff,
            )
            trials.append(trial)
            scores_a.append(score_a.total)
            scores_b.append(score_b.total)

        n_flips = sum(1 for t in trials if t.flipped)
        mean_a, std_a = compute_stats(scores_a)
        mean_b, std_b = compute_stats(scores_b)
        d = cohens_d(mean_a, mean_b, std_a, std_b, len(scores_a), len(scores_b))

        return ExperimentResult(
            intervention_type="positive_control",
            n_trials=len(trials),
            n_flips=n_flips,
            flip_rate=n_flips / max(len(trials), 1),
            mean_original=mean_a,
            mean_intervened=mean_b,
            std_original=std_a,
            std_intervened=std_b,
            cohens_d=d,
            trials=trials,
        )

    def run_full_experiment(
        self,
        reports: List[str],
        n_rhetorical: int = 30,
        n_number_fab: int = 30,
        n_citation_inf: int = 30,
        n_negative: int = 30,
        n_positive: int = 30,
    ) -> FullExperimentResult:
        """Run complete robustness experiment."""
        # Sample reports for each intervention
        rhetorical_reports = self.rng.sample(
            reports, min(n_rhetorical, len(reports))
        )
        number_fab_reports = self.rng.sample(
            reports, min(n_number_fab, len(reports))
        )
        citation_inf_reports = self.rng.sample(
            reports, min(n_citation_inf, len(reports))
        )
        negative_reports = self.rng.sample(
            reports, min(n_negative, len(reports))
        )

        # Create positive control pairs (different reports)
        positive_pairs = []
        available = list(reports)
        for _ in range(n_positive):
            if len(available) >= 2:
                a, b = self.rng.sample(available, 2)
                positive_pairs.append((a, b))

        # Run interventions
        rhetorical = self.run_intervention_trial(
            rhetorical_reports,
            lambda r: RhetoricalInflation(seed=self.seed).apply(r),
            "rhetorical_inflation",
        )

        number_fabrication = self.run_intervention_trial(
            number_fab_reports,
            lambda r: NumberFabrication(seed=self.seed).apply(r),
            "number_fabrication",
        )

        citation_inflation = self.run_intervention_trial(
            citation_inf_reports,
            lambda r: CitationInflation(seed=self.seed).apply(r),
            "citation_inflation",
        )

        negative_control = self.run_negative_control(negative_reports)
        positive_control = self.run_positive_control(positive_pairs)

        # Build summary
        summary = {
            "rhetorical_flip_rate": rhetorical.flip_rate,
            "rhetorical_cohens_d": rhetorical.cohens_d,
            "number_fabrication_flip_rate": number_fabrication.flip_rate,
            "number_fabrication_cohens_d": number_fabrication.cohens_d,
            "citation_inflation_flip_rate": citation_inflation.flip_rate,
            "citation_inflation_cohens_d": citation_inflation.cohens_d,
            "negative_control_flip_rate": negative_control.flip_rate,
            "positive_control_flip_rate": positive_control.flip_rate,
            "all_flip_rates_le_5pct": (
                rhetorical.flip_rate <= 0.05
                and number_fabrication.flip_rate <= 0.05
                and citation_inflation.flip_rate <= 0.05
            ),
            "all_cohens_d_lt_02": (
                abs(rhetorical.cohens_d) < 0.2
                and abs(number_fabrication.cohens_d) < 0.2
                and abs(citation_inflation.cohens_d) < 0.2
            ),
            "negative_control_flip_rate_zero": negative_control.flip_rate == 0.0,
            "positive_control_flip_rate_gt_20pct": positive_control.flip_rate > 0.20,
        }

        return FullExperimentResult(
            rhetorical=rhetorical,
            number_fabrication=number_fabrication,
            citation_inflation=citation_inflation,
            negative_control=negative_control,
            positive_control=positive_control,
            summary=summary,
        )
