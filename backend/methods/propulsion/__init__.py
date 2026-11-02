"""Propulsion (推进) domain: open-water propeller anchors, gates and reference model."""
from .open_water import (
    B_SERIES_TERMS,
    OpenWaterReference,
    audit_open_water_curve,
    axial_induction_factor,
    derived_quantities,
    evaluate_b_series,
    fit_reference_model,
    ideal_efficiency_from_CT,
    ideal_efficiency_from_KT_J,
    ideal_wake_fraction,
    open_water_efficiency,
    thrust_loading_coefficient,
)
from .audited_serving import SERVING_PROVENANCE, serving_wageningen_b, load_reference_dataset

__all__ = [
    "B_SERIES_TERMS", "OpenWaterReference", "audit_open_water_curve",
    "axial_induction_factor", "derived_quantities", "evaluate_b_series",
    "fit_reference_model", "ideal_efficiency_from_CT", "ideal_efficiency_from_KT_J",
    "ideal_wake_fraction", "open_water_efficiency", "thrust_loading_coefficient",
    "SERVING_PROVENANCE", "serving_wageningen_b", "load_reference_dataset",
]
