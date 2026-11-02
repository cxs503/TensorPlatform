"""Physics reference models behind the digital-twin surrogates."""
from .geometry import HullForm
from .resistance import holtrop_resistance, lindqvist_ice_resistance, total_resistance
from .seakeeping import irregular_seastate, regular_response
from .manoeuvring import turning_circle, zigzag
from .stability import kn_curve, stability_summary, wall_sided_gz
from .fatigue import spectral_fatigue, rainflow_damage, synthesise_stress
from .vibration import (HullGirderSection, blade_rate_frequency, cabin_noise,
                        comfort_grade, deck_velocity_spectrum, hull_girder_vertical,
                        mass_law_tl, plate_natural_frequency, resonance_risk,
                        vibration_summary)

__all__ = [
    "HullForm", "holtrop_resistance", "lindqvist_ice_resistance", "total_resistance",
    "irregular_seastate", "regular_response", "turning_circle", "zigzag",
    "kn_curve", "stability_summary", "wall_sided_gz",
    "spectral_fatigue", "rainflow_damage", "synthesise_stress",
    "HullGirderSection", "hull_girder_vertical", "blade_rate_frequency",
    "deck_velocity_spectrum", "plate_natural_frequency", "mass_law_tl",
    "cabin_noise", "comfort_grade", "resonance_risk", "vibration_summary",
]
