"""Synthetic structured-report factory for randomized scorer trials.

Design constraints
------------------
* **No new domain literals.**  The vocabulary is sampled from the v1 scorer's
  own lexicons (``ReportScorer.METHOD_KEYWORDS`` / ``DOMAIN_TERMS``), so this
  module introduces no domain vocabulary of its own (platform zero-domain rule).
* **Latent quality knob.**  A single scalar ``quality in [0, 1]`` drives every
  surface/numerical component, so the factory can produce a *continuum* of
  reports spanning the whole 0-100 score range (needed for a non-degenerate
  flip-rate measurement: grading flips only exist near a grade boundary).
* **Truth is in the report.**  Each report carries a results table with
  ``computed | benchmark | stated_error_pct``.  The benchmark column is the
  reference; ``computed`` is the (honest, small-error) claim.  Fabrication
  interventions perturb ``computed`` only, leaving the reference and the stated
  error untouched -- exactly the failure mode a reviewer must catch.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List

from ..scorer import ReportScorer

__all__ = ["TableRowTruth", "SyntheticReport", "SyntheticReportFactory", "TABLE_HEADER"]

TABLE_HEADER = [
    "| quantity | computed | benchmark | stated_error_pct |",
    "|---|---|---|---|",
]

# Generic (domain-free) engineering parameter tokens.  They only need to be
# recognisable to the v1 reproducibility patterns, not domain specific.
_PARAM_TEMPLATES = [
    "length = {v:.2f}",
    "speed = {v:.2f}",
    "time = {v:.2f}",
    "count = {v:.0f}",
]

_UNITS = ["kN", "mm", "m/s", "deg"]


@dataclass
class TableRowTruth:
    """Ground truth of one results-table row."""

    quantity: str
    computed: float
    benchmark: float
    stated_error_pct: float


@dataclass
class SyntheticReport:
    """A generated report plus its ground-truth metadata."""

    report_id: str
    quality: float
    text: str
    rows: List[TableRowTruth] = field(default_factory=list)


class SyntheticReportFactory:
    """Deterministic, seeded factory of structured engineering-style reports."""

    def __init__(self, seed: int = 20261110):
        self.seed = seed
        # Vocabulary is *inherited* from the v1 scorer; nothing is hardcoded.
        self.method_terms = sorted(ReportScorer.METHOD_KEYWORDS)
        self.domain_terms = sorted(ReportScorer.DOMAIN_TERMS)
        self.struct_templates = [
            "Table {n}: summary of measured quantities",
            "Figure {n}: comparison against the benchmark values",
            "Equation {n}: definition of the derived quantity",
            "Section {n}: discussion of the residuals",
        ]

    # -- helpers ---------------------------------------------------------
    def _rng(self, report_id: str, purpose: str = "build") -> random.Random:
        return random.Random(f"{self.seed}:{report_id}:{purpose}")

    @staticmethod
    def _quantize(values: List[str], k: int) -> List[str]:
        if k <= 0 or not values:
            return []
        return values[: min(k, len(values))]

    # -- main API --------------------------------------------------------
    def build(self, index: int, quality: float) -> SyntheticReport:
        """Build report #``index`` with latent quality ``quality in [0, 1]``."""
        report_id = f"r{index:04d}"
        rng = self._rng(report_id)
        q = max(0.0, min(1.0, quality))

        n_method = max(1, round(1 + 9 * q))
        n_domain = round(11 * q)
        n_numbers = max(3, round(4 + 26 * q))
        n_struct = round(4 * q)
        n_params = round(4 * q)
        n_units = max(1, round(1 + 3 * q))
        n_rows = 3 + rng.randint(0, 3)

        # Sample (without replacement) then expose *all* selected tokens so the
        # score responds smoothly to q; per-token repeat count gives finer grain.
        method_sel = self._quantize(rng.sample(self.method_terms, len(self.method_terms)), n_method)
        domain_sel = self._quantize(rng.sample(self.domain_terms, len(self.domain_terms)), n_domain)
        for _ in range(rng.randint(0, 1)):
            if method_sel:
                method_sel.append(rng.choice(method_sel))
        for _ in range(rng.randint(0, 1)):
            if domain_sel:
                domain_sel.append(rng.choice(domain_sel))

        lines: List[str] = [f"Synthetic engineering report {report_id} (latent quality {q:.2f})"]

        # Methodology paragraph
        if method_sel:
            lines.append("Methodology: " + ", ".join(method_sel) + ".")
        else:
            lines.append("Methodology: standard procedure.")

        # Results paragraph with plain numbers
        number_tokens = [f"{rng.uniform(0.5, 99.5):.2f}" for _ in range(n_numbers)]
        lines.append("Results (plain values): " + ", ".join(number_tokens) + ".")
        if n_units:
            unit_str = " ".join(f"{rng.uniform(1.0, 90.0):.2f} {u}" for u in _UNITS[:n_units])
            lines.append(f"Measured quantities: {unit_str}")

        # Results table: computed vs benchmark (honest, small error)
        rows: List[TableRowTruth] = []
        lines.extend(TABLE_HEADER)
        for j in range(n_rows):
            benchmark = math.exp(rng.uniform(math.log(1.0), math.log(1000.0)))
            dev = rng.uniform(-0.03, 0.03)
            computed = benchmark * (1.0 + dev)
            lines.append(
                f"| Q{j + 1} | {computed:.4f} | {benchmark:.4f} | {abs(dev) * 100:.2f} |"
            )
            rows.append(
                TableRowTruth(
                    quantity=f"Q{j + 1}",
                    computed=computed,
                    benchmark=benchmark,
                    stated_error_pct=abs(dev) * 100,
                )
            )
        lines.append("All rows above are reported against the same benchmark table.")

        # Structure + parameter reporting
        for j in range(n_struct):
            lines.append(self.struct_templates[j % len(self.struct_templates)].format(n=j + 1))
        for j in range(n_params):
            lines.append(
                "Parameters: " + _PARAM_TEMPLATES[j % len(_PARAM_TEMPLATES)].format(v=rng.uniform(1, 99))
            )
        if domain_sel:
            lines.append("Scope: " + ", ".join(domain_sel) + ".")

        return SyntheticReport(report_id=report_id, quality=q, text="\n".join(lines), rows=rows)

    def build_cohort(self, n: int, jitter: bool = True) -> List[SyntheticReport]:
        """Latin-hypercube-style cohort: quality spread uniformly over [0, 1]."""
        reports = []
        for i in range(n):
            q = (i + 0.5) / n
            if jitter:
                q += self._rng(f"cohort{i}").uniform(-0.4, 0.4) / n
            reports.append(self.build(i, q))
        return reports