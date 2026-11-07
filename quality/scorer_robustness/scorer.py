"""Deterministic report scorer for ship performance evaluation.

Scores reports on substantive engineering quality dimensions:
- methodology_rigor: presence of validated methods, error analysis
- data_quality: experimental data, validation against benchmarks
- reproducibility: clear parameters, procedures, uncertainty quantification
- engineering_relevance: practical applicability, design implications

The scorer is designed to be DETERMINISTIC (same input → same output)
and FOCUS ON SUBSTANCE (not surface text features).
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreBreakdown:
    """Detailed scoring breakdown."""
    methodology_rigor: float = 0.0
    data_quality: float = 0.0
    reproducibility: float = 0.0
    engineering_relevance: float = 0.0
    total: float = 0.0
    grade: str = "F"

    def to_dict(self) -> dict:
        return {
            "methodology_rigor": round(self.methodology_rigor, 4),
            "data_quality": round(self.data_quality, 4),
            "reproducibility": round(self.reproducibility, 4),
            "engineering_relevance": round(self.engineering_relevance, 4),
            "total": round(self.total, 4),
            "grade": self.grade,
        }


# Grade thresholds - calibrated for realistic report quality range
# Scores are designed to span 0-100 for typical engineering reports
GRADE_THRESHOLDS = [
    (75.0, "A"),
    (60.0, "B"),
    (45.0, "C"),
    (30.0, "D"),
    (0.0, "F"),
]


def _grade_from_score(score: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


class ReportScorer:
    """Deterministic scorer that evaluates engineering report quality.

    The scorer extracts substantive signals from report text:
    - Method keywords (validation, verification, uncertainty, benchmark, etc.)
    - Numerical precision (reported values with units, error bounds)
    - Structural elements (sections, tables, figures references)
    - Engineering content (ship parameters, resistance, propulsion terms)

    It deliberately IGNORES:
    - Adjectives and rhetorical flourishes
    - Citation counts (unless they reference actual methods used)
    - Formatting and presentation style
    - Citation bracket numbers like [1], [2]
    """

    # Substantive method keywords (weighted)
    METHOD_KEYWORDS = {
        "validation": 5.0,
        "verification": 5.0,
        "uncertainty": 4.0,
        "benchmark": 4.0,
        "convergence": 3.0,
        "grid independence": 4.0,
        "mesh refinement": 3.0,
        "time step": 2.5,
        "sensitivity": 3.0,
        "calibration": 3.0,
        "experimental": 4.0,
        "CFD": 3.0,
        "RANS": 2.5,
        "LES": 2.5,
        "potential flow": 2.5,
        "BEM": 2.5,
        "FEM": 2.5,
        "FVM": 2.5,
        "LBM": 2.5,
        "holtrop": 3.0,
        "ITTC": 3.0,
        "model test": 4.0,
        "sea trial": 4.0,
        "error analysis": 4.0,
        "confidence interval": 3.0,
        "standard deviation": 2.5,
        "repeatability": 3.0,
    }

    # Ship performance domain terms
    DOMAIN_TERMS = {
        "resistance": 2.0,
        "propulsion": 2.0,
        "seakeeping": 2.0,
        "manoeuvring": 2.0,
        "stability": 2.0,
        "hull": 1.5,
        "propeller": 1.5,
        "rudder": 1.5,
        "wave": 1.5,
        "drag": 1.5,
        "thrust": 1.5,
        "torque": 1.5,
        "friction": 1.5,
        "viscous": 1.5,
        "pressure": 1.5,
        "Reynolds": 1.5,
        "Froude": 1.5,
        "Cb": 1.5,
        "block coefficient": 1.5,
        "displacement": 1.5,
        "draft": 1.5,
        "beam": 1.5,
        "length": 1.5,
        "speed": 1.5,
        "knot": 1.5,
    }

    # Structural quality indicators
    STRUCT_PATTERNS = [
        (r"(?i)table\s*\d+", 2.0),
        (r"(?i)figure\s*\d+", 2.0),
        (r"(?i)section\s*\d+", 1.0),
        (r"(?i)appendix", 1.0),
        (r"(?i)equation\s*\d+", 2.0),
        (r"(?i)algorithm", 1.0),
    ]

    def __init__(self, seed: int = 42):
        """Initialize scorer with optional seed for reproducibility."""
        self.seed = seed

    def score(self, report_text: str) -> ScoreBreakdown:
        """Score a report and return breakdown + total + grade."""
        text_lower = report_text.lower()

        # 1. Methodology rigor: count method keywords
        method_score = 0.0
        for keyword, weight in self.METHOD_KEYWORDS.items():
            count = text_lower.count(keyword.lower())
            method_score += min(count * weight, weight * 3)  # cap per keyword
        method_score = min(method_score / 15.0 * 100, 100.0)  # normalize to 0-100

        # 2. Data quality: look for numerical values with units, error bounds
        data_score = self._score_data_quality(report_text)

        # 3. Reproducibility: structural elements + parameter reporting
        repro_score = self._score_reproducibility(report_text)

        # 4. Engineering relevance: domain terms
        domain_score = 0.0
        for term, weight in self.DOMAIN_TERMS.items():
            count = text_lower.count(term.lower())
            domain_score += min(count * weight, weight * 3)
        domain_score = min(domain_score / 15.0 * 100, 100.0)

        # Weighted total
        total = (
            0.30 * method_score
            + 0.30 * data_score
            + 0.20 * repro_score
            + 0.20 * domain_score
        )

        grade = _grade_from_score(total)

        return ScoreBreakdown(
            methodology_rigor=method_score,
            data_quality=data_score,
            reproducibility=repro_score,
            engineering_relevance=domain_score,
            total=total,
            grade=grade,
        )

    def _score_data_quality(self, text: str) -> float:
        """Score based on numerical data presence and quality indicators.

        IMPORTANT: Ignores citation-style bracketed numbers like [1], [2]
        to prevent citation inflation from affecting scores.
        """
        score = 0.0

        # Remove citation bracket numbers before counting numbers
        text_no_citations = re.sub(r'\[\d+\]', '', text)

        # Count numerical values (integers and floats) - excluding citation refs
        numbers = re.findall(r'\b\d+\.?\d*\b', text_no_citations)
        num_count = len(numbers)
        score += min(num_count * 0.8, 30.0)

        # Look for error/uncertainty indicators
        error_patterns = [
            r'±\s*\d+\.?\d*',
            r'error\s*[:=]\s*\d+\.?\d*%?',
            r'uncertainty\s*[:=]\s*\d+\.?\d*%?',
            r'accuracy\s*[:=]\s*\d+\.?\d*%?',
            r'precision\s*[:=]\s*\d+\.?\d*%?',
            r'%\s*error',
            r'RMSE',
            r'MAE',
            r'R²',
            r'R-squared',
        ]
        for pattern in error_patterns:
            matches = re.findall(pattern, text_no_citations, re.IGNORECASE)
            score += min(len(matches) * 8.0, 25.0)

        # Look for units (indicates real measurements)
        unit_patterns = [
            r'\bN\b', r'\bkN\b', r'\bMW\b', r'\bkW\b',
            r'\bm/s\b', r'\bkn\b', r'\bknot',
            r'\bHz\b', r'\bdeg\b', r'\brad\b',
            r'\bmm\b', r'\bcm\b', r'\bm\b',
            r'\bkg\b', r'\bt\b',
        ]
        for pattern in unit_patterns:
            matches = re.findall(pattern, text_no_citations)
            score += min(len(matches) * 3.0, 15.0)

        return min(score / 70.0 * 100, 100.0)  # normalize to 0-100

    def _score_reproducibility(self, text: str) -> float:
        """Score based on structural elements and parameter reporting."""
        score = 0.0

        # Structural patterns
        for pattern, weight in self.STRUCT_PATTERNS:
            matches = re.findall(pattern, text)
            score += min(len(matches) * weight, weight * 3)

        # Parameter reporting patterns
        param_patterns = [
            r'L\s*=\s*\d+',
            r'B\s*=\s*\d+',
            r'T\s*=\s*\d+',
            r'D\s*=\s*\d+',
            r'Cb\s*=\s*\d+\.?\d*',
            r'Fn\s*=\s*\d+\.?\d*',
            r'Re\s*=\s*\d+\.?\d*',
            r'speed\s*=\s*\d+',
            r'draft\s*=\s*\d+',
            r'length\s*=\s*\d+',
            r'beam\s*=\s*\d+',
        ]
        for pattern in param_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            score += min(len(matches) * 5.0, 15.0)

        return min(score / 30.0 * 100, 100.0)  # normalize to 0-100

    def score_hash(self, report_text: str) -> str:
        """Return deterministic hash of score for identity checking."""
        breakdown = self.score(report_text)
        score_str = json.dumps(breakdown.to_dict(), sort_keys=True)
        return hashlib.sha256(score_str.encode()).hexdigest()[:16]
