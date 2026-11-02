"""Independent analytic references and degeneracy detectors for the seakeeping endpoints.

Nothing in this module imports platform code: every number is either a closed form
(deep-water dispersion, Froude-Krylov long-wave limit, SDOF/HF asymptote, natural
frequency construction) or a statistic of the *returned* array that exposes a
degenerate (input- or degree-of-freedom-invariant) curve.

Anchor catalogue
----------------
A1  disp        k = omega^2 / g  and  omega_e = omega - k U cos(beta)      exact
A2  longwave    FK long-wave limit: heave -> 1 m/m, pitch -> 1 rad/(ka),
                roll -> 1 rad/(ka) as omega -> 0                           exact limit
A3  (retired)  the omega^-2 asymptote is NOT checkable in the accessible window: the
                Smith factor exp(-kT) reaches exp(-5.7) at 3 rad/s and the coupled 2-DOF
                terms re-inject energy, so no clean algebraic window exists
A4  heading     FK excitation of heave carries no beta dependence (2b dx amplitude),
                roll excitation is proportional to sin(beta) => roll(180 deg) = 0
A5  encounter   head seas raise omega_e by k U; following seas can null it
A6  nat_freq    T_heave = 2 pi sqrt((m + A33)/(rho g Aw)),
                T_roll  = 2 pi k_eff / sqrt(g GM)
A7  closure     the same omega evaluated through /seakeeping/rao and
                /seakeeping/motion must give the same RAO
A8  geom_gate   displacement must equal Cb L B T rho (platform geometry rule)
A9  roll_peak   roll RAO peaks at omega_4 = sqrt(g GM)/k_xx
A10 roll_magnif peak roll magnitude = 1/(2 zeta_44) for a SDOF oscillator

Degeneracy detectors (defect class "identical curves", i.e. the historical
legacy /seakeeping/rao failure mode)
--------------------------------------------------------------------------
* dof_degeneracy  - two different degrees of freedom returning the same array
* input_degeneracy - the response not reacting to heading / speed / frequency / Cb

Units: SI (omega rad/s, lengths m, mass kg).
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence

import numpy as np

__all__ = [
    "G", "RHO", "KNOT",
    "deep_water_k", "wavelength", "encounter_omega",
    "heave_natural_period", "roll_natural_period", "roll_peak_omega",
    "roll_peak_magnification",
    "loglog_slope", "rel_err", "abs_err",
    "dof_degeneracy", "input_degeneracy", "is_monotone", "peak_location",
]

G = 9.80665        # standard gravity (ISO 80000-3); tolerance absorbs 9.81 variants
RHO = 1025.0          # sea-water density used by the platform
KNOT = 1852.0 / 3600.0


# ---------------------------------------------------------------------------
# A1 / A5  wave kinematics
# ---------------------------------------------------------------------------
def deep_water_k(omega: float) -> float:
    """Deep-water wave number k = omega^2 / g (exact)."""
    return omega * omega / G


def wavelength(omega: float) -> float:
    """Deep-water wavelength lambda = 2 pi g / omega^2."""
    return 2.0 * math.pi * G / (omega * omega)


def encounter_omega(omega: float, speed_ms: float, heading_deg: float) -> float:
    """omega_e = omega - k U cos(beta); heading 180 deg = head seas."""
    return omega - deep_water_k(omega) * speed_ms * math.cos(math.radians(heading_deg))


# ---------------------------------------------------------------------------
# A6 / A9  natural frequencies
# ---------------------------------------------------------------------------
def heave_natural_period(mass_kg: float, a33_kg: float, aw_m2: float, rho: float = RHO) -> float:
    """T3 = 2 pi sqrt((m + A33)/C33), C33 = rho g Aw  (exact for the SDOF model)."""
    return 2.0 * math.pi * math.sqrt((mass_kg + a33_kg) / (rho * G * aw_m2))


def roll_natural_period(gm: float, k_xx_m: float) -> float:
    """T4 = 2 pi k_xx / sqrt(g GM) with the roll radius of gyration k_xx in metres.

    I44 = m k_xx^2 and C44 = m g GM, so omega_4 = sqrt(g GM)/k_xx.  ``k_xx`` must be
    the radius of gyration itself (the platform reports ``k_xx_eff`` already in metres).
    """
    return 2.0 * math.pi * k_xx_m / math.sqrt(G * gm)


def roll_peak_omega(gm: float, k_xx_m: float) -> float:
    """Natural roll frequency omega_4 = sqrt(g GM)/k_xx  [rad/s]."""
    return math.sqrt(G * gm) / k_xx_m


def roll_peak_magnification(zeta: float) -> float:
    """Amplitude at the damped natural frequency of a SDOF oscillator: 1/(2 zeta)."""
    return 1.0 / (2.0 * zeta)


# ---------------------------------------------------------------------------
# A3  high-frequency asymptote
# ---------------------------------------------------------------------------
def loglog_slope(x: Sequence[float], y: Sequence[float]) -> float:
    """Least-squares slope of log y vs log x (used for the omega^-2 asymptote)."""
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    m = (xa > 0) & (ya > 0)
    if m.sum() < 2:
        return float("nan")
    return float(np.polyfit(np.log(xa[m]), np.log(ya[m]), 1)[0])


# ---------------------------------------------------------------------------
# error helpers
# ---------------------------------------------------------------------------
def rel_err(value: float, reference: float) -> float:
    """Signed relative error (value - ref)/ref; inf when the reference vanishes."""
    if reference == 0.0:
        return float("inf") if value != 0.0 else 0.0
    return (value - reference) / reference


def abs_err(value: float, reference: float) -> float:
    return value - reference


# ---------------------------------------------------------------------------
# degeneracy detectors
# ---------------------------------------------------------------------------
def dof_degeneracy(a: Sequence[float], b: Sequence[float], tol: float = 1e-9) -> Dict[str, float]:
    """Compare two DOF arrays; an exact copy is the legacy 'identical curves' defect.

    Returns max |a-b| / max|a| (0 => the two arrays are the same curve) plus the
    number of entries that differ by more than ``tol``.
    """
    xa, xb = np.asarray(a, float), np.asarray(b, float)
    n = min(len(xa), len(xb))
    xa, xb = xa[:n], xb[:n]
    scale = np.max(np.abs(xa)) if n and np.max(np.abs(xa)) > 0 else 1.0
    diff = np.abs(xa - xb) / scale
    return {
        "n": float(n),
        "max_rel_gap": float(np.max(diff)) if n else float("nan"),
        "mean_rel_gap": float(np.mean(diff)) if n else float("nan"),
        "n_differing": float(np.sum(np.abs(xa - xb) > tol)),
    }


def input_degeneracy(payloads: Dict[str, Sequence[float]], tol: float = 1e-6) -> Dict[str, object]:
    """Detect a response that ignores its inputs.

    ``payloads`` maps a perturbation label to the returned curve.  The reference is
    the entry labelled ``"base"``.  A healthy endpoint yields gaps > ``tol`` for
    perturbations that change the physics (heading, speed, frequency, Cb); an
    invariant endpoint returns the same array for all of them.
    """
    if "base" not in payloads:
        raise KeyError("input_degeneracy expects a 'base' payload")
    base = np.asarray(payloads["base"], float)
    out: Dict[str, object] = {"invariant_to": [], "sensitive_to": []}
    for label, curve in payloads.items():
        if label == "base":
            continue
        c = np.asarray(curve, float)
        n = min(len(base), len(c))
        if n == 0:
            continue
        gap = float(np.max(np.abs(base[:n] - c[:n])))
        (out["invariant_to"] if gap <= tol else out["sensitive_to"]).append(label)
    out["n_perturbations"] = len(payloads) - 1
    out["fully_invariant"] = len(out["invariant_to"]) == len(payloads) - 1
    return out


def is_monotone(values: Sequence[float], increasing: bool = True) -> bool:
    v = np.asarray(values, float)
    d = np.diff(v)
    return bool(np.all(d >= 0) if increasing else np.all(d <= 0))


def peak_location(x: Sequence[float], y: Sequence[float]) -> float:
    """Grid argument of max y."""
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    return float(xa[int(np.argmax(ya))])