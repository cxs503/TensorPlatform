"""Validation faces for the multi-objective optimisation framework.

Three verifiable faces, all pinned to closed-form references (no MOCK data):

1. **analytic** — ZDT1/ZDT2/ZDT3 fronts reproduced against their closed forms
   (GD <= 0.01, IGD, coverage) — :mod:`..problems`, :mod:`..metrics`;
2. **convergence** — the generation at which GD crosses the acceptance
   threshold, checked against two independent anchors: a second in-tree
   NSGA-II implementation (Deb 2002) and the power-law extrapolation of the
   measured GD(g) trajectory;
3. **diversity + throughput** — Schott spacing on the obtained front and the
   wall-clock speed-up of process-parallel over serial evaluation.
"""

from __future__ import annotations

from .metrics import (
    coverage,
    evaluate_front,
    generational_distance,
    hypervolume_2d,
    inverted_generational_distance,
    spacing,
)
from .nsga2 import NSGA2Config, NSGA2Result, run_nsga2
from .problems import ZDT1, ZDT2, ZDT3, get_problem

__all__ = [
    "NSGA2Config",
    "NSGA2Result",
    "ZDT1",
    "ZDT2",
    "ZDT3",
    "coverage",
    "evaluate_front",
    "generational_distance",
    "get_problem",
    "hypervolume_2d",
    "inverted_generational_distance",
    "run_nsga2",
    "spacing",
]