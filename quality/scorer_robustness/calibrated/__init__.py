"""Calibrated scorer robustness (v2): numeric-fidelity dimension + randomized trials.

Subpackage layout
-----------------
``scorer_v2``   calibrated scorer (v1 rubric + results-table numeric fidelity)
``reports``     seeded synthetic structured-report factory (latent quality knob)
``trials``      randomized paired trials, effect sizes, defense gate
``acceptance``  campaign criteria (a)-(e) evaluated against the measured numbers
``run``         end-to-end CLI: results.json + figures + acceptance.json
"""

from .scorer_v2 import (  # noqa: F401
    FIDELITY_WEIGHT,
    TAU_HI,
    TAU_LO,
    CalibratedScore,
    CalibratedScorer,
    TableRow,
    V1BaselineScorer,
    parse_results_table,
    row_credit,
)
from .reports import SyntheticReport, SyntheticReportFactory  # noqa: F401
from .trials import (  # noqa: F401
    ARM_NAMES,
    MAGNITUDES,
    PREREG,
    ArmResult,
    apply_numeric_perturbation,
    cohens_d_paired,
    cohens_d_pooled,
    defense_gate,
    run_arm,
    run_experiment,
    wilson_interval,
)
from .acceptance import evaluate_acceptance  # noqa: F401

__all__ = [
    "FIDELITY_WEIGHT",
    "TAU_LO",
    "TAU_HI",
    "CalibratedScorer",
    "CalibratedScore",
    "TableRow",
    "parse_results_table",
    "row_credit",
    "V1BaselineScorer",
    "SyntheticReport",
    "SyntheticReportFactory",
    "ARM_NAMES",
    "MAGNITUDES",
    "PREREG",
    "ArmResult",
    "apply_numeric_perturbation",
    "cohens_d_paired",
    "cohens_d_pooled",
    "defense_gate",
    "run_arm",
    "run_experiment",
    "wilson_interval",
    "evaluate_acceptance",
]