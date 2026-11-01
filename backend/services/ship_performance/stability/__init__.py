"""Intact-stability domain package.

Currently holds the IS Code 2008 Part A §2.3 severe wind and rolling
("weather") criterion, which the rest of the platform (icing kernel,
ice-integration chain) declares out of scope; A.2.2 general criteria live
with their respective load cases.
"""

from .weather_criterion import (  # noqa: F401
    COEFFICIENT_PROVENANCE,
    LW2_FACTOR,
    WIND_PRESSURE_PA,
    WeatherCriterionResult,
    area_under_curve,
    r_factor,
    s_roll_period,
    simpson_integrate,
    theta1_is_code,
    wall_sided_area_analytic,
    weather_criterion as evaluate_weather_criterion,
    wind_heeling_levers,
    x1_beam_draft,
    x2_block_coefficient,
)

__all__ = [
    "COEFFICIENT_PROVENANCE",
    "LW2_FACTOR",
    "WIND_PRESSURE_PA",
    "WeatherCriterionResult",
    "area_under_curve",
    "r_factor",
    "s_roll_period",
    "simpson_integrate",
    "theta1_is_code",
    "wall_sided_area_analytic",
    "evaluate_weather_criterion",
    "wind_heeling_levers",
    "x1_beam_draft",
    "x2_block_coefficient",
]