"""Deterministic fan-efficiency fixture with resistance and bag faults."""

from __future__ import annotations

import math
import random

from scenarios.lib.core import clip, progress


def generate_row(index: int, rows: int, mode: str, rng: random.Random) -> dict:
    phase = 2 * math.pi * index / 24
    pct = progress(index, rows)
    active = pct >= 0.55
    drift = max(0.0, (pct - 0.55) / 0.45) if mode == "drift" else 0.0
    fault = 1.0 if mode == "fault" and active else 0.0
    fan_frequency = clip(42 + 3.8 * math.sin(phase) + 2.5 * drift + rng.gauss(0, 0.3), 32, 50)
    airflow = clip(220000 + 5800 * (fan_frequency - 40) - 26000 * fault + rng.gauss(0, 2500), 140000, 310000)
    pressure_drop = clip(1420 + 24 * (fan_frequency - 40) + 260 * drift + 610 * fault + rng.gauss(0, 22), 900, 2500)
    inlet_dust = clip(19 + 2.4 * math.sin(phase / 2) + rng.gauss(0, 0.4), 10, 28)
    outlet_dust = clip(7.5 + 0.13 * inlet_dust + 13 * fault + 2.5 * drift + rng.gauss(0, 0.5), 2, 40)
    fan_power = clip((airflow * pressure_drop) / (3.6e6 * 0.72) + 30 + 45 * fault + rng.gauss(0, 3), 80, 330)
    specific_energy = clip(fan_power / (airflow / 1000), 0.35, 1.8, 5)
    label = "bag_blockage" if fault else "resistance_drift" if drift else "normal"
    return {
        "fan_frequency_hz": fan_frequency,
        "airflow_m3_h": airflow,
        "pressure_drop_pa": pressure_drop,
        "inlet_dust_g_m3": inlet_dust,
        "outlet_dust_mg_m3": outlet_dust,
        "fan_power_kw": fan_power,
        "specific_energy_kwh_per_1000m3": specific_energy,
        "condition_label": label,
    }
