#!/usr/bin/env python3
"""Application layer: synthetic mathematical models exercised by the audit.

**Dependency direction.**  These modules import nothing from the mechanism
package and the mechanism package never imports them (asserted by
``tests/invariant_audit/test_invariant_audit_neutrality.py``).  They exist only
to give the mechanism something to audit, exactly as
``tools/verification_harness/anchors`` does for the reference-comparison harness.

**Zero domain semantics.**  Every model below is arithmetic: a periodic-grid
linear transport update, a diagonal linear response map, a dimensional
post-processor.  No vessel, waterway, sea state or operating point appears; the
physical *meaning* of the quantities is supplied by the caller's labels and by
the plan configuration, never by this file.

Observable contract
-------------------
A model returns a mapping of observable name -> number | list[number].  Reserved
keys starting with ``_`` carry metadata; ``_dimensions`` maps a quantity name to
a dimension expression over the plan's injected base symbols.
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple


# ---------------------------------------------------------------------------
# periodic-grid linear transport (conservative flux form vs interpolating form)
# ---------------------------------------------------------------------------
def _mesh(config: Mapping[str, Any]) -> Tuple[List[float], List[float], float]:
    """Periodic 1-D mesh with optionally stretched cells.

    ``stretch = 0`` is a uniform mesh; ``0 < stretch < 1`` makes the cell width
    vary sinusoidally, which is what separates a cell-centred *conservative*
    update (the telescoping flux sum still cancels) from a nodal/interpolating
    update (the cell integrals drift).  All lengths come from configuration.
    """
    cells = max(int(config.get("cells", 200)), 4)
    length = float(config.get("length", 1.0))
    stretch = float(config.get("stretch", 0.0))
    if not (-0.95 < stretch < 0.95):
        raise ValueError("stretch must lie in (-0.95, 0.95) to keep widths positive")
    raw = [1.0 + stretch * math.cos(2.0 * math.pi * i / cells) for i in range(cells)]
    scale = length / math.fsum(raw)
    widths = [r * scale for r in raw]
    centers: List[float] = []
    x = 0.0
    for width in widths:
        centers.append(x + 0.5 * width)
        x += width
    return widths, centers, length


def _pulse(centers: Sequence[float], center: float, width: float) -> List[float]:
    width = max(float(width), 1e-9)
    return [math.exp(-((x - center) / width) ** 2) for x in centers]


def _cell_mass(field: Sequence[float], widths: Sequence[float]) -> float:
    """Discrete cell integral ``sum(f_i * V_i)``."""
    return math.fsum(f * v for f, v in zip(field, widths))


def _centroid(field: Sequence[float], widths: Sequence[float],
              centers: Sequence[float]) -> float:
    total = _cell_mass(field, widths)
    if total == 0.0:
        return float("nan")
    return math.fsum(f * v * x for f, v, x in zip(field, widths, centers)) / total


def _flux_form_step(field: Sequence[float], widths: Sequence[float],
                    velocity: float, dt: float) -> List[float]:
    """Cell-centred upwind finite-volume update ``f_i -= dt/V_i (F_i - F_{i-1})``.

    The face fluxes telescope over the periodic ring, so ``sum(f_i V_i)`` is
    conserved to round-off on *any* mesh, including a stretched one.
    """
    n = len(field)
    if velocity >= 0.0:
        flux = [velocity * field[i] for i in range(n)]
    else:
        flux = [velocity * field[(i + 1) % n] for i in range(n)]
    return [field[i] - dt * (flux[i] - flux[i - 1]) / widths[i] for i in range(n)]


def _interpolating_step(field: Sequence[float], widths: Sequence[float],
                        centers: Sequence[float], length: float,
                        velocity: float, dt: float) -> List[float]:
    """Semi-Lagrangian departure-point update with donor-cell interpolation.

    Deliberately *not* in flux form: cell averages are blended between
    neighbouring centres without any face-flux bookkeeping, so on a stretched
    mesh the cell integral drifts.  On a uniform mesh this update conserves the
    integral exactly and shifts the first moment by exactly one CFL number per
    step (a consistent interpolant reproduces constants), which is itself worth
    knowing before a mass invariant is used to discriminate schemes.
    """
    n = len(field)
    out: List[float] = []
    for i in range(n):
        departure = (centers[i] - velocity * dt) % length
        j = bisect.bisect_right(centers, departure) - 1
        if j < 0:
            j = n - 1
        span = (centers[(j + 1) % n] - centers[j]) % length
        span = span if span > 0.0 else length
        frac = ((departure - centers[j]) % length) / span
        out.append((1.0 - frac) * field[j] + frac * field[(j + 1) % n])
    return out


def transport_grid(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Periodic linear transport on a (possibly stretched) 1-D grid.

    ``config`` keys (all injected): ``cells``, ``length``, ``stretch``,
    ``velocity``, ``velocity_modulation``, ``dt``, ``steps``, ``pulse_center``,
    ``pulse_width``, ``scheme`` (``flux`` | ``interpolating``), ``emit_flux``
    (report the boundary flux term), ``trace`` (return the per-step cell mass).
    """
    widths, centers, length = _mesh(config)
    field = _pulse(centers, float(config.get("pulse_center", 0.3)),
                   float(config.get("pulse_width", 0.05)))
    velocity = float(config.get("velocity", 0.2))
    dt = float(config.get("dt", 0.01))
    steps = max(int(config.get("steps", 50)), 1)
    scheme = str(config.get("scheme", "flux"))
    modulation = float(config.get("velocity_modulation", 0.0))

    mass_trace = [_cell_mass(field, widths)]
    centroid_before = _centroid(field, widths, centers)
    for k in range(steps):
        factor = 1.0 + modulation * math.sin(2.0 * math.pi * k / steps)
        step_velocity = velocity * factor
        if scheme == "interpolating":
            field = _interpolating_step(field, widths, centers, length, step_velocity, dt)
        else:
            field = _flux_form_step(field, widths, step_velocity, dt)
        mass_trace.append(_cell_mass(field, widths))
    centroid_after = _centroid(field, widths, centers)

    out: Dict[str, Any] = {
        "mass_initial": mass_trace[0],
        "mass_final": mass_trace[-1],
        "field": list(field),
        "displacement": centroid_after - centroid_before,
        "max_cell_width": max(widths),
        "min_cell_width": min(widths),
    }
    if config.get("emit_flux", True) and scheme == "flux":
        # A flux-form scheme reports its net flux through the periodic boundary
        # (identically zero here); a scheme without a flux ledger simply omits the
        # observable, which the audit must classify as WITHHELD, not as conserved.
        out["boundary_flux"] = 0.0
    if config.get("trace", False):
        out["mass_trace"] = mass_trace
    return out


# ---------------------------------------------------------------------------
# linear response maps (rotation / reflection covariance)
# ---------------------------------------------------------------------------
def response_diagonal(config: Mapping[str, Any]) -> Dict[str, Any]:
    """``y = diag(weights) x``.

    With equal weights the map commutes with every orthogonal transform, so it is
    exactly covariant under any rotation/reflection.  With unequal weights it is
    invariant only under the transforms that commute with the diagonal matrix
    (axis reflections, axis permutations), i.e. the grid-aligned point group.
    """
    vector = [float(v) for v in config.get("input_vector", [1.0, 1.0])]
    weights = [float(w) for w in config.get("weights", [1.0] * len(vector))]
    if len(weights) != len(vector):
        raise ValueError("weights and input_vector must have the same length")
    return {"response": [w * v for w, v in zip(weights, vector)]}


def boost_response_heuristic(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Non-covariant surrogate: displacement with a velocity-dependent gain.

    ``d(u) = u T (1 + k u)`` mimics a limiter that introduces an absolute
    velocity scale.  Changing the reference frame then changes the *shape* of the
    response, so the boost covariance invariant must fail while a consistent
    model passes.
    """
    velocity = float(config.get("velocity", 0.2))
    duration = float(config.get("duration", 0.5))
    gain = float(config.get("gain", 0.05))
    return {"displacement": velocity * duration * (1.0 + gain * velocity)}


def displacement_exact(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Covariant reference behaviour: ``d = u T`` (no absolute velocity scale)."""
    velocity = float(config.get("velocity", 0.2))
    duration = float(config.get("duration", 0.5))
    return {"displacement": velocity * duration}


# ---------------------------------------------------------------------------
# dimensional post-processors (declared units vs implemented formula)
# ---------------------------------------------------------------------------
_BASE_DECLARATIONS = {
    "mass": "mass",
    "acceleration": "length*time**-2",
    "velocity": "length*time**-1",
}


def postprocess_dimensions_ok(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Correctly declares and computes a force-like quantity."""
    mass = float(config.get("mass", 2.0))
    acceleration = float(config.get("acceleration", 3.5))
    duration = float(config.get("duration", 1.5))
    velocity = acceleration * duration
    return {
        "mass": mass,
        "acceleration": acceleration,
        "velocity": velocity,
        "reported_force": mass * acceleration,
        "_dimensions": {**_BASE_DECLARATIONS,
                        "reported_force": "mass*length*time**-2"},
    }


def postprocess_dimensions_unit_bug(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Mislabels the output unit (records a momentum dimension for a force)."""
    mass = float(config.get("mass", 2.0))
    acceleration = float(config.get("acceleration", 3.5))
    duration = float(config.get("duration", 1.5))
    velocity = acceleration * duration
    return {
        "mass": mass,
        "acceleration": acceleration,
        "velocity": velocity,
        "reported_force": mass * acceleration,
        "_dimensions": {**_BASE_DECLARATIONS,
                        "reported_force": "mass*length*time**-1"},
    }


def postprocess_dimensions_formula_bug(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Computes ``mass * velocity`` (momentum) while documenting it as a force."""
    mass = float(config.get("mass", 2.0))
    acceleration = float(config.get("acceleration", 3.5))
    duration = float(config.get("duration", 1.5))
    velocity = acceleration * duration
    return {
        "mass": mass,
        "acceleration": acceleration,
        "velocity": velocity,
        "reported_force": mass * velocity,
        "_dimensions": {**_BASE_DECLARATIONS,
                        "reported_force": "mass*length*time**-2"},
    }