"""Geometry / hydrostatics verification pilot (analytic baselines).

Provides an independent reference hydrostatics integrator plus closed-form
analytic hull cases, so that any candidate hydrostatics implementation
(serving endpoint, repo module, third-party code) can be adjudicated against
exact values instead of against itself.

Modules
-------
ref_hydrostatics : Simpson-rule hydrostatics on a sampled hull surface.
analytic_cases   : hulls with exact closed-form hydrostatics (box, Wigley, sine-form).
mutants          : deliberately broken variants used to prove the gate is not vacuous.
"""

from .ref_hydrostatics import HullForm, compute_hydrostatics, convergence_study
from .analytic_cases import ANALYTIC_CASES, build_case

__all__ = [
    "HullForm",
    "compute_hydrostatics",
    "convergence_study",
    "ANALYTIC_CASES",
    "build_case",
]