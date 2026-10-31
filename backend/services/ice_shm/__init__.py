"""Ice load inversion & structural-health-monitoring (SHM) integration.

End-to-end capability for ice-going ships / Polar Class hulls:

1. **Forward model** — ice-belt frame girder as Hermite-cubic Euler-Bernoulli
   beam elements (TensorPhysics / TensorFEM ``hermite_cubic_euler_bernoulli``
   formulation); stiffness assembly ``K`` and the strain influence matrix
   ``H = B K^-1 P`` giving ``eps = H F_ice``.
2. **Inverse problem** — Tikhonov regularisation
   ``F = argmin ||H F - eps_meas||^2 + lam ||F||^2`` with the weight ``lam``
   selected by the L-curve criterion.
3. **Real-time monitoring** — :class:`StructuralHealthMonitor` turns a strain
   stream into inverted ice loads, von Mises stress and the safety factor
   ``SF = sigma_yield / sigma_vm`` classified against the acceptance bands.
4. **Uncertainty quantification** — gauge noise -> load covariance
   ``Cov(F) = A diag(sigma_eps^2) A^T`` -> first-order and Monte-Carlo
   safety-factor scatter.

See :mod:`services.ice_shm.frame`, :mod:`services.ice_shm.inversion` and
:mod:`services.ice_shm.monitor` for the implementation details.
"""
from __future__ import annotations

from .constants import (
    DEFAULT_YIELD_PA,
    DEFAULT_YOUNG_MODULUS_PA,
    MIN_SLENDERNESS_L_OVER_H,
    SF_FAIL_THRESHOLD,
    SF_SAFE_THRESHOLD,
    STEEL_YIELD_PA,
)
from .frame import (
    IceBeltFrameModel,
    StrainInfluence,
    validate_structural_geometry,
)
from .inversion import (
    InversionResult,
    invert_ice_load,
    l_curve_scan,
    load_covariance,
    relative_load_uncertainty,
    relative_noise_level,
    synthesize_strain,
    tikhonov_solve,
)
from .monitor import (
    StructuralHealthMonitor,
    classify_safety_factor,
    safety_factor_with_uncertainty,
)
from .section import BeamSection

__all__ = [
    # constants
    "STEEL_YIELD_PA",
    "DEFAULT_YIELD_PA",
    "DEFAULT_YOUNG_MODULUS_PA",
    "SF_SAFE_THRESHOLD",
    "SF_FAIL_THRESHOLD",
    "MIN_SLENDERNESS_L_OVER_H",
    # model
    "BeamSection",
    "IceBeltFrameModel",
    "StrainInfluence",
    "validate_structural_geometry",
    # inversion
    "InversionResult",
    "invert_ice_load",
    "l_curve_scan",
    "tikhonov_solve",
    "load_covariance",
    "relative_noise_level",
    "relative_load_uncertainty",
    "synthesize_strain",
    # health / monitoring
    "classify_safety_factor",
    "safety_factor_with_uncertainty",
    "StructuralHealthMonitor",
]