"""
Ship resistance kernel — dependency-free single source of truth.

Why this module exists
----------------------
The resistance endpoint (``api/calculations.py``) and the benchmark gate
(``api/domain/ship_benchmark.py``) used to talk to each other over HTTP with
hand-written payload keys.  The keys drifted apart (``speed`` vs ``speed_ms``,
``ct`` vs ``total_resistance_n``), so the accuracy gate silently scored a
default-speed calculation against a non-existent response field and reported
"100 % error, algorithm upgrade recommended" for every case.  Both sides now
import this kernel directly: no payload keys, no drift, and unit-testable
without FastAPI / database dependencies.

Model (unchanged physics, verbatim from the original endpoint):
  S  = pi * B * L * (0.5 + 0.25 Cb)      axisymmetric-body wetted-surface fit
  Cf = 0.075 / (log10(Re) - 2)^2          ITTC-1957 friction line
  Cr = 0.002 (1 + 2 Cb) Fn^2.5           simplified residual-resistance fit
  Ct = Rt / (0.5 rho S V^2)               definitional total coefficient

Validity: calibrated for Cb > 0.55 merchant ships / SUBOFF-like bodies of
revolution.  It is NOT a general surface-ship resistance model — see the audit
report for its measured error against experimental references.
"""
from __future__ import annotations

import math
from typing import Dict

__all__ = ["compute_resistance_core", "GRAVITY", "check_geometry"]


GRAVITY = 9.81

# Physical plausibility window for a displacement hull.  Beyond these bounds the
# geometry input itself is wrong, so no accuracy statement can be attached to the
# output (the platform standard is "verify Cb/L/B before any error claim").
CB_MIN = 0.30
CB_MAX = 1.00
FN_WARN = 0.60          # above this a towed displacement hull is out of model-test range


def check_geometry(length: float, beam: float, draft: float, cb: float,
                   speed_ms: float = None) -> Dict[str, object]:
    """Return geometry self-consistency diagnostics for a hull/speed pair."""
    problems = []
    if not (CB_MIN < cb < CB_MAX):
        problems.append(f"Cb={cb} outside [{CB_MIN}, {CB_MAX}]")
    if min(length, beam, draft) <= 0:
        problems.append("non-positive principal dimension")
    diag = {
        "length": length, "beam": beam, "draft": draft, "cb": cb,
        "displacement_volume_m3": cb * length * beam * draft,
        "problems": problems,
        "ok": not problems,
    }
    if speed_ms is not None and length > 0:
        fn = speed_ms / math.sqrt(GRAVITY * length)
        diag["froude_number"] = fn
        diag["froude_number_ok"] = fn <= FN_WARN
        if fn > FN_WARN:
            problems.append(f"Fn={fn:.3f} > {FN_WARN} (not a model-test regime)")
    diag["ok"] = not problems          # keep `ok` consistent with the final problem list
    return diag


def compute_resistance_core(
    length: float,
    beam: float,
    draft: float,
    cb: float,
    speed_ms: float,
    water_density: float = 998.0,
    kinematic_viscosity: float = 1.004e-6,
) -> Dict[str, float]:
    """Pure resistance kernel — the single source of truth for resistance.

    Both the HTTP endpoint and the in-process benchmark gate call this function, so
    the two can never drift apart again (they previously did: see
    docs/reports/m1395_benchmark_gate_audit.md).

    Model: ITTC-1957 friction line + a simplified residual-resistance fit
    Cr = 0.002 (1 + 2 Cb) Fn^2.5, wetted surface S = pi B L (0.5 + 0.25 Cb).

    Returns non-dimensional and dimensional quantities (SI).
    """
    if not (0.3 < cb < 1.0):
        raise ValueError(f"Cb={cb} out of physical range [0.3, 1.0]")
    if length <= 0 or beam <= 0 or draft <= 0:
        raise ValueError("Dimensions must be positive")
    if speed_ms <= 0:
        raise ValueError("speed_ms must be positive")

    g = 9.81
    rho = water_density
    nu = kinematic_viscosity
    V = speed_ms

    # Wetted surface area (approximation for axisymmetric body)
    S = math.pi * beam * length * (0.5 + 0.25 * cb)

    # Reynolds number
    Re = V * length / nu

    # ITTC-1957 friction line
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    # Frictional resistance
    Rf = 0.5 * rho * S * Cf * V ** 2

    # Froude number
    Fn = V / math.sqrt(g * length)

    # Empirical residual resistance coefficient for SUBOFF-like bodies
    # Based on curve-fitting to experimental data
    Cr = 0.002 * (1 + 2.0 * cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2

    Rt = Rf + Rr
    Ct = Rt / (0.5 * rho * S * V ** 2)

    return {
        "wetted_surface_m2": S,
        "reynolds_number": Re,
        "froude_number": Fn,
        "friction_coeff": Cf,
        "frictional_resistance_n": Rf,
        "residual_coeff": Cr,
        "residual_resistance_n": Rr,
        "total_resistance_n": Rt,
        "total_resistance_coeff": Ct,
    }
