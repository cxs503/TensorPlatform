"""Calibrated report scorer (v2): base rubric + numeric-fidelity dimension.

Motivation
----------
The v1 scorer (`quality.scorer_robustness.scorer.ReportScorer`) is *immune* to
surface text noise (rhetoric, citations) because it only counts substantive
lexicon hits and bare numbers.  Immunity is necessary but not sufficient: a
scorer that is blind to numbers is also blind to **fabricated results**, which
is the dominant Goodhart channel for a report-based review process (the report
adds impressive-looking numbers that nobody can reproduce).

v2 therefore keeps the v1 rubric untouched and adds exactly one dimension:

    numeric_fidelity  -- agreement between the reported values and the
                         *benchmark/reference* values printed in the same
                         results table.

The dimension is computed from the report's own results table: every row carries
``computed``, ``benchmark`` and ``stated_error_pct``.  The scorer recomputes the
implied deviation ``|computed - benchmark| / |benchmark|`` and turns it into a
per-row credit via a two-sided tolerance band:

    dev <= TAU_LO           -> credit 1.0      (within grace band)
    TAU_LO < dev < TAU_HI   -> linear decay
    dev >= TAU_HI           -> credit 0.0      (unacceptable)

``TAU_LO = 0.10`` and ``TAU_HI = 0.15`` are **not free parameters**: 15 % is the
platform-wide precision standard ("error > 15 % is not acceptable"), so a row
that deviates by >= 15 % earns zero credit; the 10 % grace band absorbs
rounding, unit-conversion and benchmark-model scatter.  ``FIDELITY_WEIGHT =
0.25`` makes a fully fabricated numeric dimension cost 25 score points, i.e.
roughly 1.5 grade bands (grade bands are 15 points wide).

No new domain vocabulary is introduced here: the v2 scorer reuses the v1
lexicons as-is (zero-domain-semantics rule for platform code).

If a report has no parseable results table the fidelity dimension is
``None`` and the score falls back to the v1 total, so prose-only documents keep
their v1 grade (verified in tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..scorer import GRADE_THRESHOLDS, ReportScorer, ScoreBreakdown, _grade_from_score

__all__ = [
    "TAU_LO",
    "TAU_HI",
    "FIDELITY_WEIGHT",
    "MAX_PHRASE_GAP",
    "tolerant_hits",
    "TableRow",
    "parse_results_table",
    "row_credit",
    "CalibratedScorer",
    "CalibratedScore",
    "V1BaselineScorer",
]

# Tolerance band (see module docstring for the justification).
TAU_LO: float = 0.10
TAU_HI: float = 0.15

# Weight of the numeric-fidelity dimension in the v2 total.
FIDELITY_WEIGHT: float = 0.25

# Multi-word keyphrases are matched with a bounded gap, so that a modifier
# spliced inside a term ("error analysis" -> "error systematic analysis") does
# not erase the term.  Measured motivation: with gap 0 (v1 behaviour) a purely
# rhetorical intervention flipped 1/200 grades (see docs/M8374_*.md sec.5).
MAX_PHRASE_GAP: int = 2

_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"

# A results-table data row: | quantity | computed | benchmark | stated_error_pct |
_ROW_RE = re.compile(
    r"^\s*\|\s*(?P<q>[^|]+?)\s*\|\s*(?P<c>" + _NUM + r")\s*\|"
    r"\s*(?P<b>" + _NUM + r")\s*\|\s*(?P<e>" + _NUM + r")\s*\|\s*$"
)


_PHRASE_RE_CACHE: dict = {}


def _phrase_regex(term: str, max_gap: int = MAX_PHRASE_GAP) -> "re.Pattern[str]":
    """Regex for ``term`` allowing at most ``max_gap`` intervening tokens."""
    key = (term, max_gap)
    cached = _PHRASE_RE_CACHE.get(key)
    if cached is not None:
        return cached
    parts = [re.escape(p) for p in term.split()]
    if len(parts) == 1:
        pattern = r"\b" + parts[0] + r"\b"
    else:
        gap = r"(?:\s+[A-Za-z0-9%\u00b0\-]+){0," + str(max_gap) + r"}\s+"
        pattern = r"\b" + gap.join(parts) + r"\b"
    compiled = re.compile(pattern, re.IGNORECASE)
    _PHRASE_RE_CACHE[key] = compiled
    return compiled


def tolerant_hits(text: str, term: str, max_gap: int = MAX_PHRASE_GAP) -> int:
    """Count occurrences of ``term`` tolerating up to ``max_gap`` spliced tokens."""
    return len(_phrase_regex(term, max_gap).findall(text))


@dataclass(frozen=True)
class TableRow:
    """One parsed results-table data row."""

    line_index: int
    quantity: str
    computed: float
    benchmark: float
    stated_error_pct: float

    @property
    def deviation(self) -> Optional[float]:
        """Relative deviation from the benchmark, or None if benchmark == 0."""
        if self.benchmark == 0.0:
            return None
        return abs(self.computed - self.benchmark) / abs(self.benchmark)


def parse_results_table(text: str) -> List[TableRow]:
    """Extract data rows of the standard results table from report text.

    Header rows and markdown separator rows are skipped automatically because
    they do not match the four-numeric-field pattern.
    """
    rows: List[TableRow] = []
    for idx, line in enumerate(text.split("\n")):
        m = _ROW_RE.match(line)
        if not m:
            continue
        try:
            computed = float(m.group("c"))
            benchmark = float(m.group("b"))
            stated = float(m.group("e"))
        except ValueError:  # pragma: no cover - guarded by the regex
            continue
        rows.append(
            TableRow(
                line_index=idx,
                quantity=m.group("q"),
                computed=computed,
                benchmark=benchmark,
                stated_error_pct=stated,
            )
        )
    return rows


def row_credit(deviation: Optional[float]) -> float:
    """Map a row deviation onto [0, 1] credit with the calibrated tolerance band."""
    if deviation is None:
        return 1.0
    if deviation <= TAU_LO:
        return 1.0
    if deviation >= TAU_HI:
        return 0.0
    return (TAU_HI - deviation) / (TAU_HI - TAU_LO)


def numeric_fidelity(text: str) -> Tuple[Optional[float], List[Tuple[str, float, float, float]]]:
    """Return (fidelity 0-100 or None, per-row detail tuples)."""
    rows = parse_results_table(text)
    if not rows:
        return None, []
    detail = []
    credits = []
    for r in rows:
        dev = r.deviation
        cr = row_credit(dev)
        credits.append(cr)
        detail.append((r.quantity, r.computed, r.benchmark, cr))
    return 100.0 * sum(credits) / len(credits), detail


@dataclass
class CalibratedScore:
    """Score produced by :class:`CalibratedScorer`."""

    base: ScoreBreakdown
    numeric_fidelity: Optional[float]
    total: float
    grade: str
    n_table_rows: int = 0
    methodology_tolerant: Optional[float] = None

    def to_dict(self) -> dict:
        d = self.base.to_dict()
        d["numeric_fidelity"] = (
            None if self.numeric_fidelity is None else round(self.numeric_fidelity, 4)
        )
        if self.methodology_tolerant is not None:
            d["methodology_tolerant"] = round(self.methodology_tolerant, 4)
        d["total_v2"] = round(self.total, 4)
        d["grade_v2"] = self.grade
        d["n_table_rows"] = self.n_table_rows
        return d


class CalibratedScorer:
    """v2 scorer = v1 rubric (weight 1-w) + numeric fidelity (weight w)."""

    def __init__(
        self,
        seed: int = 42,
        tau_lo: float = TAU_LO,
        tau_hi: float = TAU_HI,
        fidelity_weight: float = FIDELITY_WEIGHT,
        max_phrase_gap: int = MAX_PHRASE_GAP,
    ):
        if not (0.0 <= tau_lo < tau_hi <= 1.0):
            raise ValueError("require 0 <= tau_lo < tau_hi <= 1")
        if not (0.0 <= fidelity_weight <= 1.0):
            raise ValueError("require 0 <= fidelity_weight <= 1")
        self.base = ReportScorer(seed=seed)
        self.tau_lo = tau_lo
        self.tau_hi = tau_hi
        self.fidelity_weight = fidelity_weight
        self.max_phrase_gap = max_phrase_gap

    def _credit(self, deviation: Optional[float]) -> float:
        if deviation is None:
            return 1.0
        if deviation <= self.tau_lo:
            return 1.0
        if deviation >= self.tau_hi:
            return 0.0
        return (self.tau_hi - deviation) / (self.tau_hi - self.tau_lo)

    def methodology_tolerant(self, report_text: str) -> float:
        """v1 methodology rubric, but with gap-tolerant multi-word phrase matching."""
        method_score = 0.0
        for keyword, weight in ReportScorer.METHOD_KEYWORDS.items():
            hits = tolerant_hits(report_text, keyword, self.max_phrase_gap)
            method_score += min(hits * weight, weight * 3)
        return min(method_score / 15.0 * 100.0, 100.0)

    def rubric_v2(self, report_text: str) -> Tuple[ScoreBreakdown, float]:
        """Return (v1 breakdown, v2 rubric total without the fidelity dimension)."""
        base = self.base.score(report_text)
        method = self.methodology_tolerant(report_text)
        total = (
            0.30 * method
            + 0.30 * base.data_quality
            + 0.20 * base.reproducibility
            + 0.20 * base.engineering_relevance
        )
        return base, total

    def score(self, report_text: str) -> CalibratedScore:
        base, rubric_total = self.rubric_v2(report_text)
        rows = parse_results_table(report_text)
        method = self.methodology_tolerant(report_text)
        if not rows:
            return CalibratedScore(
                base=base,
                numeric_fidelity=None,
                total=rubric_total,
                grade=_grade_from_score(rubric_total),
                n_table_rows=0,
                methodology_tolerant=method,
            )
        credits = [self._credit(r.deviation) for r in rows]
        fidelity = 100.0 * sum(credits) / len(credits)
        total = (1.0 - self.fidelity_weight) * rubric_total + self.fidelity_weight * fidelity
        return CalibratedScore(
            base=base,
            numeric_fidelity=fidelity,
            total=total,
            grade=_grade_from_score(total),
            n_table_rows=len(rows),
            methodology_tolerant=method,
        )

    def score_hash(self, report_text: str) -> str:
        import hashlib
        import json

        payload = json.dumps(self.score(report_text).to_dict(), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class V1BaselineScorer:
    """v1 rubric only (no fidelity dimension, gap-0 phrase matching).

    Used as the *before* baseline of the maturity comparison: it is the scorer
    that the randomized trials are meant to stress, unchanged and unpatched.
    """

    name = "v1"

    def __init__(self, seed: int = 42):
        self._inner = ReportScorer(seed=seed)
        self.base = self._inner  # `.base.score(text)` -> v1 ScoreBreakdown
        self.tau_lo = None
        self.tau_hi = None
        self.fidelity_weight = 0.0
        self.max_phrase_gap = 0

    def score(self, report_text: str) -> CalibratedScore:
        breakdown = self._inner.score(report_text)
        rows = parse_results_table(report_text)
        return CalibratedScore(
            base=breakdown,
            numeric_fidelity=None,
            total=breakdown.total,
            grade=breakdown.grade,
            n_table_rows=len(rows),
            methodology_tolerant=breakdown.methodology_rigor,
        )


# Re-exported for callers that want the v1 thresholds without importing the
# private name.
GRADE_CUTOFFS = GRADE_THRESHOLDS