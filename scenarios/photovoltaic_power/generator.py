"""Deterministic PV fixture with weather drift and inverter derating."""

from __future__ import annotations

import math
import random

from scenarios.lib.core import clip, progress


def generate_row(index: int, rows: int, mode: str, rng: random.Random) -> dict:
    hour = (index % 96) / 4
    pct = progress(index, rows)
    active = pct >= 0.55
    drift = max(0.0, (pct - 0.55) / 0.45) if mode == "drift" else 0.0
    fault = 1.0 if mode == "fault" and active else 0.0
    daylight = max(0.0, math.sin(math.pi * (hour - 6) / 12))
    cloud_cover = clip(24 + 14 * math.sin(2 * math.pi * index / 37) + 35 * drift + rng.gauss(0, 3), 0, 95)
    irradiance = clip(950 * daylight * (1 - 0.0065 * cloud_cover) + rng.gauss(0, 10) * daylight, 0, 1050)
    ambient = clip(15 + 11 * math.sin(2 * math.pi * (hour - 7) / 24) + rng.gauss(0, 0.4), -5, 42)
    module_temperature = clip(ambient + 0.028 * irradiance + rng.gauss(0, 0.7), -5, 75)
    availability = clip(0.985 - 0.37 * fault, 0.5, 1.0, 4)
    efficiency_factor = max(0.78, 1 - 0.0042 * max(module_temperature - 25, 0))
    power = clip(1000 * (irradiance / 1000) * efficiency_factor * availability + rng.gauss(0, 2) * daylight, 0, 1000)
    label = "inverter_derating" if fault else "weather_drift" if drift else "normal"
    return {
        "hour_of_day": round(hour, 2),
        "global_irradiance_w_m2": irradiance,
        "ambient_temperature_c": ambient,
        "module_temperature_c": module_temperature,
        "cloud_cover_pct": cloud_cover,
        "inverter_availability": availability,
        "active_power_kw": power,
        "condition_label": label,
    }
