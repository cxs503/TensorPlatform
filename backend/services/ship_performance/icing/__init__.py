"""Superstructure sea-spray icing (upper-air/superstructure marine icing).

Public API
----------
``Metocean``, ``VesselSpec``, ``run_icing_case`` — scenario definition and the
full icing -> stability chain;
``makiranta_reference_icing_rate``, ``layered_gm_loss`` — analytic/lumped
cross-checks used by the acceptance criteria;
``BargeHull``, ``LoadingCondition`` — stability primitives.

The discipline kernel lives in this package (pure ``math``); the HTTP surface is
``backend/api/domain/ship_icing.py``.
"""
from __future__ import annotations

from .accumulation import (
    AccretionHistory,
    PanelIceState,
    integrate_icing,
    panel_ice_table,
    step_icing,
)
from .heat_balance import (
    PanelHeatBalance,
    TransferCoefficients,
    panel_heat_balance,
    solve_equilibrium_surface_temperature,
    surface_heat_loss,
    transfer_coefficients,
)
from .mesh import Block, Panel, block_panels, build_superstructure_mesh, panel_area_summary
from .model import (
    PRESETS,
    IcingCaseResult,
    Metocean,
    VesselSpec,
    preset_ice_class_bulker,
    preset_polar_fishing_vessel,
    run_icing_case,
)
from .properties import (
    RHO_ICE,
    AirState,
    WaterState,
    seawater_freezing_point,
    vapour_density,
)
from .reference import (
    ReferenceIcingRate,
    layered_gm_loss,
    makiranta_reference_icing_rate,
)
from .spray import (
    CALIBRATION,
    ZONE_EXPOSURE,
    SprayCondition,
    effective_spray_content,
    significant_wave_height,
    spray_mass_flux,
)
from .stability import (
    POLAR_CODE_ICE_ALLOWANCE,
    BargeHull,
    IceLoad,
    LoadingCondition,
    ice_load_from_panels,
    polar_code_design_ice_mass,
)

__all__ = [
    "AccretionHistory",
    "AirState",
    "BargeHull",
    "Block",
    "CALIBRATION",
    "IceLoad",
    "IcingCaseResult",
    "LoadingCondition",
    "Metocean",
    "POLAR_CODE_ICE_ALLOWANCE",
    "PRESETS",
    "RHO_ICE",
    "Panel",
    "PanelHeatBalance",
    "PanelIceState",
    "ReferenceIcingRate",
    "SprayCondition",
    "TransferCoefficients",
    "VesselSpec",
    "WaterState",
    "ZONE_EXPOSURE",
    "block_panels",
    "build_superstructure_mesh",
    "effective_spray_content",
    "ice_load_from_panels",
    "integrate_icing",
    "layered_gm_loss",
    "makiranta_reference_icing_rate",
    "panel_area_summary",
    "panel_heat_balance",
    "panel_ice_table",
    "polar_code_design_ice_mass",
    "preset_ice_class_bulker",
    "preset_polar_fishing_vessel",
    "run_icing_case",
    "seawater_freezing_point",
    "significant_wave_height",
    "solve_equilibrium_surface_temperature",
    "spray_mass_flux",
    "step_icing",
    "surface_heat_loss",
    "transfer_coefficients",
    "vapour_density",
]