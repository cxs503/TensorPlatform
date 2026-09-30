"""Deterministic rotating-equipment degradation and bearing-fault fixture."""

from __future__ import annotations

import math
import random

from scenarios.lib.core import clip, progress


def generate_row(index: int, rows: int, mode: str, rng: random.Random) -> dict:
    phase = 2 * math.pi * index / 30
    pct = progress(index, rows)
    drift = max(0.0, (pct - 0.55) / 0.45) if mode == "drift" else 0.0
    fault_progress = max(0.0, (pct - 0.55) / 0.45) if mode == "fault" else 0.0
    speed = clip(1480 + 22 * math.sin(phase) + rng.gauss(0, 3), 1350, 1550)
    load = clip(68 + 8 * math.sin(phase / 2) + rng.gauss(0, 1), 45, 90)
    vibration = clip(1.7 + 0.012 * load + 1.1 * drift + 4.8 * fault_progress + rng.gauss(0, 0.08), 1, 9)
    bearing_temperature = clip(48 + 0.17 * load + 4 * drift + 22 * fault_progress + rng.gauss(0, 0.5), 45, 95)
    motor_current = clip(52 + 0.42 * load + 5 * fault_progress + rng.gauss(0, 0.8), 60, 105)
    acoustic_rms = clip(0.35 + 0.04 * vibration + 0.8 * fault_progress + rng.gauss(0, 0.02), 0.25, 1.6)
    rul = clip(720 - 95 * drift - 690 * fault_progress + rng.gauss(0, 3), 12, 750)
    label = "bearing_fault" if fault_progress > 0 else "sensor_drift" if drift else "healthy"
    return {
        "shaft_speed_rpm": speed,
        "motor_load_pct": load,
        "vibration_rms_mm_s": vibration,
        "bearing_temperature_c": bearing_temperature,
        "motor_current_a": motor_current,
        "acoustic_rms": acoustic_rms,
        "remaining_useful_life_h": rul,
        "health_label": label,
    }
