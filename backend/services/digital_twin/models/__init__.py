"""Physics reference models behind the digital-twin surrogates."""
from .geometry import HullForm
from .resistance import holtrop_resistance, lindqvist_ice_resistance, total_resistance
from .seakeeping import irregular_seastate, regular_response
from .manoeuvring import turning_circle, zigzag
from .stability import kn_curve, stability_summary, wall_sided_gz
from .fatigue import spectral_fatigue, rainflow_damage, synthesise_stress

__all__ = [
    "HullForm", "holtrop_resistance", "lindqvist_ice_resistance", "total_resistance",
    "irregular_seastate", "regular_response", "turning_circle", "zigzag",
    "kn_curve", "stability_summary", "wall_sided_gz",
    "spectral_fatigue", "rainflow_damage", "synthesise_stress",
]
