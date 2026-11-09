"""Randomized paired trials: rhetoric / number fabrication / citation inflation.

Design (pre-registered before the numbers were computed; see ``PREREG``)
-----------------------------------------------------------------------
* Unit of analysis: one synthetic structured report (paired design -- the same
  report is scored before and after the intervention, which cancels
  between-report difficulty).
* Arms
    - ``rhetorical``   : v1 rhetorical inflation (adds modifiers/phrases; no
                         digits, no bracket numbers, no lexicon hits added).
    - ``citation``     : v1 citation inflation (author-journal style, no years).
    - ``numeric``      : perturb every ``computed`` cell of the results table by
                         +-magnitude (sign drawn per row); the benchmark column
                         and the stated error are left untouched.
    - ``negative``     : zero-operation control (score the same text twice).
    - ``positive``     : destructive control (computed := 3 x benchmark), proving
                         the apparatus can detect a real numeric defect at all.
* Randomization: per (report, arm, magnitude) RNG seeded by
  ``f"{SEED}:{report_id}:{arm}:{magnitude}"`` -> byte-reproducible.
* Flip = the *grade letter* changes between the original and the intervened
  score of the same report.
* Effect size: campaign convention ``d = (mean_int - mean_orig) / pooled_sd``
  plus the paired ``d_z = mean(diff) / sd(diff)``.
* delta S (``delta_s``) = |mean(intervened) - mean(original)| / 100, i.e. the
  shift expressed as a fraction of the full 0-100 score scale.

Defense gate
------------
``flip_rate > FLIP_REVIEW_THRESHOLD`` for an arm marks that arm as requiring
human review (a defensive response: never silently let a rhetoric/fabrication
pattern flip a grade).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..interventions import CitationInflation, RhetoricalInflation
from .scorer_v2 import CalibratedScore, CalibratedScorer, _ROW_RE

__all__ = [
    "PREREG",
    "MAGNITUDES",
    "ARM_NAMES",
    "ArmResult",
    "TrialRecord",
    "apply_numeric_perturbation",
    "cohens_d_pooled",
    "cohens_d_paired",
    "wilson_interval",
    "run_arm",
    "run_experiment",
    "defense_gate",
]

# ---------------------------------------------------------------------------
# Pre-registration block (fixed BEFORE data collection; asserted by tests).
# ---------------------------------------------------------------------------
PREREG: Dict[str, object] = {
    "seed": 20261110,
    "n_reports": 200,
    "magnitudes": (0.05, 0.10, 0.20),
    "exploratory_magnitudes": (0.02, 0.075, 0.125, 0.15, 0.30),
    "flip_review_threshold": 0.10,
    "row_coverage_prob": 0.85,
    "max_phrase_gap": 2,
    "delta_s_formula": "abs(mean_intervened - mean_original) / 100",
    "criteria": {
        "a_rhetorical_delta_s_max": 0.10,
        "b_numeric_flip_rate": {"0.05": 0.10, "0.10": 0.30, "0.20": 0.50},
        "c_citation_delta_s_max": 0.05,
        "d_negative_control_flip_rate": 0.0,
        "e_cohens_d_sign": "numeric arms < 0; |d| <= 0.2 for rhetorical/citation",
    },
}

MAGNITUDES: Tuple[float, ...] = tuple(PREREG["magnitudes"])  # type: ignore[arg-type]
EXPLORATORY_MAGNITUDES: Tuple[float, ...] = tuple(PREREG["exploratory_magnitudes"])  # type: ignore[arg-type]
FLIP_REVIEW_THRESHOLD: float = float(PREREG["flip_review_threshold"])
ARM_NAMES = ("rhetorical", "citation", "numeric", "negative", "positive")


# ---------------------------------------------------------------------------
# Interventions
# ---------------------------------------------------------------------------
def apply_numeric_perturbation(
    text: str,
    magnitude: float,
    rng: random.Random,
    coverage_prob: float = 1.0,
) -> Tuple[str, List[Tuple[str, float, float]]]:
    """Perturb ``computed`` cells by +-``magnitude``; reference cells untouched.

    ``coverage_prob`` randomises *how many* rows the fabricator touches (each
    row independently).  Coverage 1.0 = edit the whole table (uniform worst
    case, every trial loses exactly the same score); the default from
    ``PREREG`` keeps a small uncovered fraction, which yields a heterogeneous
    per-trial effect and therefore a non-degenerate effect-size estimate.

    Returns the intervened text and a per-row audit list
    ``[(quantity, computed_before, computed_after), ...]``.
    """
    if magnitude < 0:
        raise ValueError("magnitude must be >= 0")
    if not (0.0 <= coverage_prob <= 1.0):
        raise ValueError("coverage_prob must be in [0, 1]")
    out_lines: List[str] = []
    audit: List[Tuple[str, float, float]] = []
    for line in text.split("\n"):
        m = _ROW_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        quantity = m.group("q")
        computed = float(m.group("c"))
        benchmark = float(m.group("b"))
        stated = float(m.group("e"))
        sign = 1.0 if rng.random() < 0.5 else -1.0
        if rng.random() >= coverage_prob:
            out_lines.append(line)
            continue
        new_computed = computed * (1.0 + sign * magnitude)
        audit.append((quantity, computed, new_computed))
        out_lines.append(f"| {quantity} | {new_computed:.4f} | {benchmark:.4f} | {stated:.2f} |")
    return "\n".join(out_lines), audit


def _rng_for(seed: int, report_id: str, arm: str, magnitude: Optional[float]) -> random.Random:
    return random.Random(f"{seed}:{report_id}:{arm}:{magnitude}")


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def cohens_d_pooled(a: Sequence[float], b: Sequence[float]) -> float:
    """Campaign convention: (mean_b - mean_a) / pooled_sd (ddof=1)."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / (len(a) + len(b) - 2))
    if pooled == 0.0:
        return 0.0
    return (mb - ma) / pooled


def cohens_d_paired(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Paired variant: mean(b - a) / sd(b - a), ddof=1.

    Returns ``None`` (undefined, *not* infinity) when every paired difference is
    identical: with sd(diff) = 0 the standardised effect is infinite, which is a
    degenerate quantity and must be reported as such rather than as a number.
    """
    diffs = [y - x for x, y in zip(a, b)]
    if len(diffs) < 2:
        return None
    md = sum(diffs) / len(diffs)
    sd = math.sqrt(sum((d - md) ** 2 for d in diffs) / (len(diffs) - 1))
    # Relative guard: floating-point residue on a *uniform* shift (e.g. every
    # trial loses exactly the fidelity weight x 100) yields sd ~ 1e-15, which is
    # not a real dispersion and must not be turned into a 1e15 effect size.
    if sd <= 1e-9 * max(1.0, abs(md)):
        return None
    return md / sd


def wilson_interval(k: int, n: int, z: float = 1.959963985) -> Tuple[float, float]:
    """95 % Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---------------------------------------------------------------------------
# Trial containers
# ---------------------------------------------------------------------------
@dataclass
class TrialRecord:
    report_id: str
    arm: str
    magnitude: Optional[float]
    total_original: float
    total_intervened: float
    grade_original: str
    grade_intervened: str
    flipped: bool
    delta: float
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "arm": self.arm,
            "magnitude": self.magnitude,
            "total_original": round(self.total_original, 4),
            "total_intervened": round(self.total_intervened, 4),
            "grade_original": self.grade_original,
            "grade_intervened": self.grade_intervened,
            "flipped": self.flipped,
            "delta": round(self.delta, 4),
        }


@dataclass
class ArmResult:
    arm: str
    magnitude: Optional[float]
    n_trials: int
    n_flips: int
    flip_rate: float
    flip_ci95: Tuple[float, float]
    mean_original: float
    mean_intervened: float
    sd_original: float
    sd_intervened: float
    cohens_d: float
    cohens_d_z: Optional[float]
    delta_s: float
    mean_delta: float
    trials: List[TrialRecord] = field(default_factory=list)

    def to_dict(self, with_trials: bool = False) -> dict:
        d = {
            "arm": self.arm,
            "magnitude": self.magnitude,
            "n_trials": self.n_trials,
            "n_flips": self.n_flips,
            "flip_rate": round(self.flip_rate, 6),
            "flip_ci95": [round(self.flip_ci95[0], 6), round(self.flip_ci95[1], 6)],
            "mean_original": round(self.mean_original, 4),
            "mean_intervened": round(self.mean_intervened, 4),
            "sd_original": round(self.sd_original, 4),
            "sd_intervened": round(self.sd_intervened, 4),
            "cohens_d": round(self.cohens_d, 4),
            "cohens_d_z": None if self.cohens_d_z is None else round(self.cohens_d_z, 4),
            "delta_s": round(self.delta_s, 6),
            "mean_delta": round(self.mean_delta, 4),
        }
        if with_trials:
            d["trials"] = [t.to_dict() for t in self.trials]
        return d


def _summarize(
    arm: str,
    magnitude: Optional[float],
    trials: List[TrialRecord],
) -> ArmResult:
    orig = [t.total_original for t in trials]
    intr = [t.total_intervened for t in trials]
    n = len(trials)
    n_flips = sum(1 for t in trials if t.flipped)
    return ArmResult(
        arm=arm,
        magnitude=magnitude,
        n_trials=n,
        n_flips=n_flips,
        flip_rate=n_flips / n if n else 0.0,
        flip_ci95=wilson_interval(n_flips, n),
        mean_original=sum(orig) / n if n else 0.0,
        mean_intervened=sum(intr) / n if n else 0.0,
        sd_original=(sum((x - sum(orig) / n) ** 2 for x in orig) / (n - 1)) ** 0.5 if n > 1 else 0.0,
        sd_intervened=(sum((x - sum(intr) / n) ** 2 for x in intr) / (n - 1)) ** 0.5 if n > 1 else 0.0,
        cohens_d=cohens_d_pooled(orig, intr),
        cohens_d_z=cohens_d_paired(orig, intr),
        delta_s=abs(sum(intr) / n - sum(orig) / n) / 100.0 if n else 0.0,
        mean_delta=sum(t.delta for t in trials) / n if n else 0.0,
        trials=trials,
    )


# ---------------------------------------------------------------------------
# Arm runners
# ---------------------------------------------------------------------------
def run_arm(
    scorer: CalibratedScorer,
    reports: Sequence,
    arm: str,
    magnitude: Optional[float] = None,
    seed: int = int(PREREG["seed"]),
    coverage_prob: float = float(PREREG["row_coverage_prob"]),
) -> ArmResult:
    """Run one arm over a cohort of reports (paired design)."""
    trials: List[TrialRecord] = []
    for rep in reports:
        base_score: CalibratedScore = scorer.score(rep.text)
        rng = _rng_for(seed, rep.report_id, arm, magnitude)

        if arm == "rhetorical":
            intervened = RhetoricalInflation(seed=rng.randrange(2**31)).apply(rep.text).intervened
            meta = {"intensity": 0.5}
        elif arm == "citation":
            intervened = CitationInflation(
                seed=rng.randrange(2**31), count=rng.randint(3, 5)
            ).apply(rep.text).intervened
            meta = {"count": None}
        elif arm == "numeric":
            assert magnitude is not None
            intervened, audit = apply_numeric_perturbation(
                rep.text, magnitude, rng, coverage_prob=coverage_prob
            )
            base_after = scorer.base.score(intervened)
            meta = {
                "n_rows_perturbed": len(audit),
                "base_total_drift": abs(base_after.total - base_score.base.total),
            }
        elif arm == "negative":
            intervened = rep.text
            meta = {"noop": True}
        elif arm == "positive":
            intervened, audit = apply_numeric_perturbation(rep.text, 2.0, rng, coverage_prob=1.0)
            meta = {"n_rows_perturbed": len(audit), "destructive": True}
        else:  # pragma: no cover
            raise ValueError(f"unknown arm {arm}")

        intr_score = scorer.score(intervened)
        trials.append(
            TrialRecord(
                report_id=rep.report_id,
                arm=arm,
                magnitude=magnitude,
                total_original=base_score.total,
                total_intervened=intr_score.total,
                grade_original=base_score.grade,
                grade_intervened=intr_score.grade,
                flipped=base_score.grade != intr_score.grade,
                delta=intr_score.total - base_score.total,
                meta=meta,
            )
        )
    return _summarize(arm, magnitude, trials)


def run_experiment(
    reports: Sequence,
    scorer: Optional[CalibratedScorer] = None,
    magnitudes: Sequence[float] = MAGNITUDES,
    exploratory_magnitudes: Sequence[float] = EXPLORATORY_MAGNITUDES,
    seed: int = int(PREREG["seed"]),
    with_trials: bool = True,
    include_baseline: bool = True,
) -> dict:
    """Run the full randomized experiment and return a JSON-ready payload."""
    scorer = scorer or CalibratedScorer()
    out: Dict[str, object] = {
        "prereg": {k: (list(v) if isinstance(v, tuple) else v) for k, v in PREREG.items()},
        "n_reports": len(reports),
        "scorer": {
            "type": type(scorer).__name__,
            "tau_lo": getattr(scorer, "tau_lo", None),
            "tau_hi": getattr(scorer, "tau_hi", None),
            "fidelity_weight": getattr(scorer, "fidelity_weight", None),
            "max_phrase_gap": getattr(scorer, "max_phrase_gap", None),
        },
        "base_score_summary": _score_summary(scorer, reports),
        "arms": {},
        "exploratory_numeric": {},
    }

    arms: Dict[str, dict] = {}
    arms["rhetorical"] = run_arm(scorer, reports, "rhetorical", seed=seed).to_dict(with_trials)
    arms["citation"] = run_arm(scorer, reports, "citation", seed=seed).to_dict(with_trials)
    arms["negative"] = run_arm(scorer, reports, "negative", seed=seed).to_dict(with_trials)
    arms["positive"] = run_arm(scorer, reports, "positive", seed=seed).to_dict(with_trials)
    for mag in magnitudes:
        arms[f"numeric@{mag:.2f}"] = run_arm(
            scorer, reports, "numeric", magnitude=float(mag), seed=seed
        ).to_dict(with_trials)
    out["arms"] = arms

    expl = {}
    for mag in exploratory_magnitudes:
        expl[f"{mag:.3f}"] = run_arm(
            scorer, reports, "numeric", magnitude=float(mag), seed=seed
        ).to_dict(with_trials=False)
    out["exploratory_numeric"] = expl

    out["defense_gate"] = defense_gate(arms)

    if include_baseline:
        from .scorer_v2 import CalibratedScorer, V1BaselineScorer

        # Ablation: v2 with gap-0 phrase matching (= v1 matcher + fidelity dim).
        # Isolates the contribution of the tolerant matcher to rhetoric immunity.
        gap0 = CalibratedScorer(seed=seed, max_phrase_gap=0)
        out["ablation_v2_gap0"] = {
            "scorer": "CalibratedScorer(max_phrase_gap=0)",
            "arms": {
                "rhetorical": run_arm(gap0, reports, "rhetorical", seed=seed).to_dict(False),
                "citation": run_arm(gap0, reports, "citation", seed=seed).to_dict(False),
            },
        }


        v1 = V1BaselineScorer(seed=seed)
        baseline_arms = {
            "rhetorical": run_arm(v1, reports, "rhetorical", seed=seed).to_dict(False),
            "citation": run_arm(v1, reports, "citation", seed=seed).to_dict(False),
            "negative": run_arm(v1, reports, "negative", seed=seed).to_dict(False),
        }
        for mag in magnitudes:
            baseline_arms[f"numeric@{mag:.2f}"] = run_arm(
                v1, reports, "numeric", magnitude=float(mag), seed=seed
            ).to_dict(False)
        out["baseline_v1"] = {
            "scorer": "V1BaselineScorer(ReportScorer)",
            "base_score_summary": _score_summary(v1, reports),
            "arms": baseline_arms,
            "defense_gate": defense_gate(baseline_arms),
        }
    return out


def _score_summary(scorer: CalibratedScorer, reports: Sequence) -> dict:
    totals = []
    grades: Dict[str, int] = {}
    fidelities = []
    for rep in reports:
        s = scorer.score(rep.text)
        totals.append(s.total)
        grades[s.grade] = grades.get(s.grade, 0) + 1
        if s.numeric_fidelity is not None:
            fidelities.append(s.numeric_fidelity)
    n = len(totals)
    mean = sum(totals) / n if n else 0.0
    sd = (sum((x - mean) ** 2 for x in totals) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return {
        "n": n,
        "mean_total": round(mean, 4),
        "sd_total": round(sd, 4),
        "min_total": round(min(totals), 4) if n else 0.0,
        "max_total": round(max(totals), 4) if n else 0.0,
        "grade_counts": dict(sorted(grades.items())),
        "mean_numeric_fidelity": round(sum(fidelities) / len(fidelities), 4) if fidelities else None,
        "reports_with_table": len(fidelities),
    }


def defense_gate(arms: Dict[str, dict], threshold: float = FLIP_REVIEW_THRESHOLD) -> dict:
    """Flag arms whose flip rate exceeds ``threshold`` for mandatory human review."""
    decisions = {}
    for name, res in arms.items():
        fr = res["flip_rate"]
        decisions[name] = {
            "flip_rate": fr,
            "threshold": threshold,
            "requires_human_review": bool(fr > threshold),
            "action": "human_review" if fr > threshold else "auto_accept",
        }
    return {
        "threshold": threshold,
        "triggered_arms": sorted(k for k, v in decisions.items() if v["requires_human_review"]),
        "decisions": decisions,
    }