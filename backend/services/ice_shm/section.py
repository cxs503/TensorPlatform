"""T-stiffener section properties for ice-belt frame girders."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class BeamSection:
    """T-stiffener (web + flange) cross-section of an ice-belt frame girder.

    Geometry is validated on construction (all dimensions finite and strictly
    positive, and geometrically consistent); every section property is derived
    from the geometry and can never be supplied by the caller, so an
    unvalidated "trust me" input is impossible.
    """

    web_height_m: float = 0.45
    web_thickness_m: float = 0.012
    flange_width_m: float = 0.12
    flange_thickness_m: float = 0.018

    def __post_init__(self) -> None:
        self.validate()

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        """Raise ``ValueError`` when the geometry is not physically admissible."""
        dims = {
            "web_height_m": self.web_height_m,
            "web_thickness_m": self.web_thickness_m,
            "flange_width_m": self.flange_width_m,
            "flange_thickness_m": self.flange_thickness_m,
        }
        for name, value in dims.items():
            try:
                v = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number, got {value!r}") from None
            if not math.isfinite(v) or v <= 0.0:
                raise ValueError(f"{name} must be a finite positive length, got {value!r}")
        if self.flange_width_m < self.web_thickness_m:
            raise ValueError("flange_width_m must not be smaller than web_thickness_m")
        if self.web_height_m <= self.flange_thickness_m:
            raise ValueError("web_height_m must exceed flange_thickness_m")

    # -- derived properties -------------------------------------------------

    @property
    def web_area_m2(self) -> float:
        return self.web_height_m * self.web_thickness_m

    @property
    def flange_area_m2(self) -> float:
        return self.flange_width_m * self.flange_thickness_m

    @property
    def area_m2(self) -> float:
        return self.web_area_m2 + self.flange_area_m2

    @property
    def height_m(self) -> float:
        return self.web_height_m + self.flange_thickness_m

    @property
    def neutral_axis_from_plating_m(self) -> float:
        """Centroid of the T-section measured from the attached plating side."""
        a_w = self.web_area_m2
        a_f = self.flange_area_m2
        y_w = 0.5 * self.web_height_m
        y_f = self.web_height_m + 0.5 * self.flange_thickness_m
        return (a_w * y_w + a_f * y_f) / (a_w + a_f)

    @property
    def inertia_m4(self) -> float:
        """Second moment of area about the section neutral axis."""
        y_bar = self.neutral_axis_from_plating_m
        i_web = self.web_thickness_m * self.web_height_m ** 3 / 12.0
        i_web += self.web_area_m2 * (0.5 * self.web_height_m - y_bar) ** 2
        i_fl = self.flange_width_m * self.flange_thickness_m ** 3 / 12.0
        i_fl += self.flange_area_m2 * (
            self.web_height_m + 0.5 * self.flange_thickness_m - y_bar
        ) ** 2
        return i_web + i_fl

    @property
    def fibre_from_flange_m(self) -> float:
        """Distance from the neutral axis to the flange (free-edge) fibre."""
        return self.height_m - self.neutral_axis_from_plating_m

    @property
    def fibre_from_plating_m(self) -> float:
        """Distance from the neutral axis to the plating fibre."""
        return self.neutral_axis_from_plating_m

    @property
    def extreme_fibre_offset_m(self) -> float:
        """Largest distance from the neutral axis (governs the extreme fibre)."""
        return max(self.fibre_from_flange_m, self.fibre_from_plating_m)

    @property
    def section_modulus_m3(self) -> float:
        return self.inertia_m4 / self.extreme_fibre_offset_m

    @property
    def shear_area_m2(self) -> float:
        """Effective shear area (web only; flanges contribute negligibly)."""
        return self.web_area_m2

    def as_dict(self) -> Dict[str, Any]:
        return {
            "web_height_m": self.web_height_m,
            "web_thickness_m": self.web_thickness_m,
            "flange_width_m": self.flange_width_m,
            "flange_thickness_m": self.flange_thickness_m,
            "height_m": self.height_m,
            "area_m2": self.area_m2,
            "inertia_m4": self.inertia_m4,
            "neutral_axis_from_plating_m": self.neutral_axis_from_plating_m,
            "fibre_from_flange_m": self.fibre_from_flange_m,
            "fibre_from_plating_m": self.fibre_from_plating_m,
            "extreme_fibre_offset_m": self.extreme_fibre_offset_m,
            "section_modulus_m3": self.section_modulus_m3,
            "shear_area_m2": self.shear_area_m2,
        }