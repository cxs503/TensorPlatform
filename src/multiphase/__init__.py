"""Shan-Chen pseudopotential multiphase LBM kernels (TensorPlatform domain module).

Added by node IN59-P1 (physical domain: multiphase) because the platform had no
auditable two-phase kernel: see ``docs/IN59-P1_droplet_entry_anomaly.md``.
"""

from .sc_multiphase import (
    CS2,
    SCHEMES,
    ShanChen,
    StepStats,
    advect_markers,
    bilinear_sample,
    eos_pressure,
    pseudopotential,
)
from .droplet_entry import (
    CYLINDER_ADDED_MASS,
    coexistence_densities,
    droplet_field,
    potential_flow_impact_ratio,
    run_entry,
    run_static_droplet,
    slab_field,
)

__all__ = [
    "CS2",
    "SCHEMES",
    "ShanChen",
    "StepStats",
    "advect_markers",
    "bilinear_sample",
    "eos_pressure",
    "pseudopotential",
    "CYLINDER_ADDED_MASS",
    "coexistence_densities",
    "droplet_field",
    "potential_flow_impact_ratio",
    "run_entry",
    "run_static_droplet",
    "slab_field",
]