"""Fidelity reliability layer: error propagation, credible-domain guard, switch gate.

``tools/multifidelity`` decides *where to spend budget*; this package decides
*whether an answer may be believed where it is being used*.  Both are generic
mechanisms: tiers, calibration values, weights and sensitivities are injected by the
caller, and no domain vocabulary appears in the code.

Public surface (``multi_fidelity.py``)
--------------------------------------
``TrustDomain``          calibration box and normalised outside distance
``TierModel``            calibrated tier: bias, conformal quantiles, Lipschitz envelope
``FidelityChain``        per-tier bounds, propagated total bound, credible intervals
``FidelitySwitchGate``   lowest admissible tier per operating point + reason codes
``ExtrapolationLaw``     ``eps(d) = C d**p`` risk law, ``p >= 1``, fitted from a study
``interval_coverage``    empirical coverage check (used by the C2 criterion)
``check_conservativeness`` bound-vs-actual audit (used by the C1 criterion)

Typical use
-----------
>>> chain = FidelityChain({
...     0: calibrate_tier(0, "coarse", f0, cal),
...     1: calibrate_tier(1, "fine",   f1, cal),
... }, links={0: ChainLink(weight=0.5, sensitivity=2.0), 1: ChainLink(0.5, 2.0)})
>>> total_bound = chain.composed_bound(X_new, mode="guard")
>>> decision = chain.gate().advice(X_new)[0]
>>> decision.escalate, decision.reasons
"""

from __future__ import annotations

from .multi_fidelity import (
    CalibrationSet,
    ChainLink,
    ExtrapolationLaw,
    FidelityChain,
    FidelitySwitchGate,
    GateDecision,
    TierModel,
    TrustDomain,
    calibrate_tier,
    check_conservativeness,
    conformal_quantile,
    fit_extrapolation_law,
    interval_coverage,
)

__all__ = [
    "CalibrationSet",
    "ChainLink",
    "ExtrapolationLaw",
    "FidelityChain",
    "FidelitySwitchGate",
    "GateDecision",
    "TierModel",
    "TrustDomain",
    "calibrate_tier",
    "check_conservativeness",
    "conformal_quantile",
    "fit_extrapolation_law",
    "interval_coverage",
]

__version__ = "1.0.0"