"""Deterministic plant load fixture with demand drift and peak events."""

from __future__ import annotations

import math
import random

from scenarios.lib.core import clip, progress


def generate_row(index: int, rows: int, mode: str, rng: random.Random) -> dict:
    hour = index % 24
    day = index // 24
    pct = progress(index, rows)
    active = pct >= 0.55
    drift = max(0.0, (pct - 0.55) / 0.45) if mode == "drift" else 0.0
    fault = 1.0 if mode == "fault" and active and hour in {10, 11, 12, 18, 19} else 0.0
    ambient = clip(18 + 7 * math.sin(2 * math.pi * (hour - 7) / 24) + rng.gauss(0, 0.5), -5, 38)
    steel_output = clip(420 + 35 * math.sin(2 * math.pi * hour / 24) + 65 * drift + rng.gauss(0, 7), 280, 560)
    oxygen_flow = clip(21500 + 32 * steel_output + 1300 * math.cos(2 * math.pi * hour / 8) + rng.gauss(0, 250), 27000, 43000)
    shift = (hour // 8) + 1
    weekend = 1 if day % 7 in {5, 6} else 0
    load = clip(
        82
        + 0.105 * steel_output
        + 0.0011 * oxygen_flow
        + 0.55 * max(ambient - 24, 0)
        - 5 * weekend
        + 18 * drift
        + 42 * fault
        + rng.gauss(0, 1.4),
        110,
        250,
    )
    label = "unplanned_peak" if fault else "demand_drift" if drift else "normal"
    return {
        "hour_of_day": hour,
        "shift_id": shift,
        "is_weekend": weekend,
        "ambient_temperature_c": ambient,
        "steel_output_t_h": steel_output,
        "oxygen_flow_nm3_h": oxygen_flow,
        "plant_load_mw": load,
        "event_label": label,
    }
