"""Open-water propeller performance from blade-element / momentum theory (BEM).

Motivation
----------
The legacy ``tools/batch_scan/batch_executor.py::ShipCalculationEngine.calc_propeller``
returned hard-coded linear surrogates::

    KT = max(0.05, 0.5 - 0.3 * J)
    KQ = max(0.01, 0.05 + 0.02 * J)
    eta0 = clip(J * KT / (2*pi*KQ), 0.3, 0.85)

That surrogate violates elementary propeller physics: ``KQ`` *increases* with the
advance ratio (a real propeller's torque must fall), the efficiency is clipped to
an artificial plateau, ``KT``/``KQ`` do not depend on pitch ratio, blade area or
blade count at all, and no zero-thrust advance ratio ``J0`` exists. It was
therefore MOCK-grade evidence behind a capability advertised as REAL.

This module replaces it with a first-principles model:

* blade-element strip integration of section lift/drag with Prandtl tip and hub
  loss,
* momentum-consistent induced velocities. The unknown is the **induced axial
  velocity** ``u`` (not the induction factor ``a = u/Va``), so the formulation
  stays regular in the bollard-pull limit ``J -> 0`` where ``a -> 1``. Per strip::

      dT_be(u) = 4 pi rho r u (Va + u) dr        (axial momentum, annulus)
      dQ_be(a') = 4 pi rho r^3 (Va + u) omega a' dr   (angular momentum)

  ``u`` from bisection on the thrust balance, ``a'`` from a short fixed point,
* non-dimensionalisation ``KT = T/(rho n^2 D^4)``, ``KQ = Q/(rho n^2 D^5)``,
  ``eta0 = J KT/(2 pi KQ)``.

Analytic benchmark available without external data: the ideal (actuator-disc)
efficiency ``eta_ideal = 2/(1 + sqrt(1 + C_T))``, with
``C_T = T/(0.5 rho V^2 A0) = 8 KT/(pi J^2)``, is an upper bound for any propulsor
delivering the same thrust; a physical model must satisfy ``0 < eta0 < eta_ideal``.

All outputs are non-dimensional, hence exactly invariant under Froude scaling
(``D -> k D`` with ``n -> n/k`` keeps ``J``, ``KT``, ``KQ``).

No literature point data was reachable offline for mission 1393, so the published
verification is analytic-bound + internal-consistency only; see
``docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "PropellerGeometry",
    "blade_element_section",
    "open_water_point",
    "open_water_curve",
    "solve_zero_thrust_advance_ratio",
    "best_efficiency_point",
    "ideal_efficiency_bound",
]

CL_MAX = 1.10
CL_SLOPE = 2.0 * math.pi
CD0 = 0.012


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PropellerGeometry:
    """Radial geometry of a Wageningen-style propeller.

    ``chord_shape`` is a normalised radial chord distribution, rescaled so the
    total blade area equals ``(Ae/A0) * pi D^2/4``; the declared blade-area ratio
    is therefore honoured exactly (checked in ``tests/test_propeller_open_water.py``).
    """

    diameter_m: float = 5.0
    pitch_ratio: float = 1.0          # P/D
    blade_area_ratio: float = 0.55    # Ae/A0
    blade_count: int = 4              # Z
    hub_ratio: float = 0.18           # r_hub / R
    sections: int = 40

    def __post_init__(self) -> None:
        if self.diameter_m <= 0:
            raise ValueError("diameter must be positive")
        if self.pitch_ratio < 0:
            raise ValueError("pitch ratio must be non-negative")
        if not (0.0 < self.blade_area_ratio < 1.5):
            raise ValueError("blade area ratio out of range (0, 1.5)")
        if self.blade_count < 2 or self.blade_count > 7:
            raise ValueError("blade count out of range [2, 7]")
        if not (0.05 <= self.hub_ratio < 0.5):
            raise ValueError("hub ratio out of range [0.05, 0.5)")
        if self.sections < 8:
            raise ValueError("need at least 8 radial sections")
        # precompute the chord scaling (Simpson normalisation) once: it is used
        # by every radial strip at every advance ratio
        object.__setattr__(self, "_chord_scale", self.blade_area_m2
                           / (self.blade_count * self.chord_normaliser()))

    @property
    def radius_m(self) -> float:
        return 0.5 * self.diameter_m

    @property
    def disk_area_m2(self) -> float:
        return math.pi * self.radius_m ** 2

    @property
    def blade_area_m2(self) -> float:
        return self.blade_area_ratio * self.disk_area_m2

    def chord_shape(self, x: float) -> float:
        t = (x - self.hub_ratio) / (1.0 - self.hub_ratio)
        t = min(max(t, 0.0), 1.0)
        return math.sin(math.pi * t) ** 0.55

    def chord_normaliser(self) -> float:
        """``int_{Rhub}^{R} chord_shape dr`` (Simpson)."""
        n = self.sections if self.sections % 2 == 0 else self.sections + 1
        h = (1.0 - self.hub_ratio) / n
        total = 0.0
        for k in range(n + 1):
            x = self.hub_ratio + k * h
            w = 1.0 if k in (0, n) else (4.0 if k % 2 else 2.0)
            total += w * self.chord_shape(x)
        return total * h / 3.0 * self.radius_m

    def chord_m(self, x: float) -> float:
        return self._chord_scale * self.chord_shape(x)

    def pitch_angle_rad(self, x: float) -> float:
        if x <= 0:
            raise ValueError("x must be positive")
        return math.atan(self.pitch_ratio / (math.pi * x))

    def as_dict(self) -> Dict[str, float]:
        return {
            "diameter_m": self.diameter_m,
            "pitch_ratio": self.pitch_ratio,
            "blade_area_ratio": self.blade_area_ratio,
            "blade_count": self.blade_count,
            "hub_ratio": self.hub_ratio,
            "disk_area_m2": self.disk_area_m2,
            "blade_area_m2": self.blade_area_m2,
        }


# ---------------------------------------------------------------------------
# Section aerodynamics and losses
# ---------------------------------------------------------------------------
def blade_element_section(alpha_rad: float) -> Tuple[float, float]:
    """Section lift/drag: ``Cl = 2 pi sin(alpha)`` capped at ``CL_MAX``,
    ``Cd = CD0 + alpha^2`` clamped to ``[0.008, 0.10]`` with ``CD0 = 0.012``.

    Documented simplification (no section database); the cap is what keeps root
    sections at high advance ratio physical. ``CD0 = 0.012`` is a model-scale
    section drag level (Re ~ 1e6, NACA a=0.8 / 66-mod type sections, including a
    roughness allowance); the sensitivity of the delivered numbers to this choice
    is tabulated in docs/verifier/PROPELLER_OPEN_WATER_VERIFICATION.md instead of
    being tuned to any chart.
    """
    cl = CL_SLOPE * math.sin(alpha_rad)
    cl = min(max(cl, -CL_MAX), CL_MAX)
    cd = min(max(CD0 + 1.0 * alpha_rad * alpha_rad, 0.008), 0.10)
    return cl, cd


def _prandtl_loss(z_blades: int, x: float, phi: float, hub_ratio: float) -> float:
    """Prandtl tip loss times a hub loss factor (0..1)."""
    if phi <= 0.0:
        return 1.0
    s = max(math.sin(abs(phi)), 1e-6)
    f_tip = 0.5 * z_blades * (1.0 - x) / s
    f_hub = 0.5 * z_blades * (x - hub_ratio) / (x * s) if x > hub_ratio else 0.0
    out = 1.0
    for f in (f_tip, f_hub):
        if f > 1e-9:
            out *= max(0.0, min(1.0, (2.0 / math.pi) * math.acos(math.exp(-f))))
    return out


# ---------------------------------------------------------------------------
# BEM at one advance ratio
# ---------------------------------------------------------------------------
def open_water_point(
    geom: PropellerGeometry,
    J: float,
    rho: float = 1025.0,
    n_rps: float = 1.0,
    axial_induction: bool = True,
) -> Dict[str, float]:
    """Open-water point for advance coefficient ``J = Va/(n D)``.

    ``axial_induction=False`` selects the pure blade-element approximation
    (``u = a' = 0``) which is kept as an independent cross-check of the
    momentum-coupled solution.
    """
    if J < 0:
        raise ValueError("J must be non-negative")
    R = geom.radius_m
    Z = geom.blade_count
    D = geom.diameter_m
    n = n_rps
    omega = 2.0 * math.pi * n
    Va = J * n * D

    th = (1.0 - geom.hub_ratio) / geom.sections
    thrust = 0.0
    torque = 0.0
    converged = True

    for k in range(geom.sections):
        x = geom.hub_ratio + (k + 0.5) * th
        r = x * R
        dr = th * R
        c = geom.chord_m(x)
        beta = geom.pitch_angle_rad(x)

        u = 0.0
        a_p = 0.0

        if axial_induction:
            def strip(u_try: float) -> Tuple[float, float, float]:
                """Return (dT_be - dT_mo, dQ_be, phi) for a trial induced velocity."""
                a_p_l = 0.0
                for _ in range(8):
                    phi_l = math.atan((Va + u_try) / (omega * r * (1.0 - a_p_l)))
                    cl_l, cd_l = blade_element_section(beta - phi_l)
                    F_l = _prandtl_loss(Z, x, phi_l, geom.hub_ratio)
                    w2 = (Va + u_try) ** 2 + (omega * r * (1.0 - a_p_l)) ** 2
                    dQ_l = 0.5 * rho * w2 * Z * c * (
                        cl_l * math.sin(phi_l) + cd_l * math.cos(phi_l)) * F_l * r * dr
                    denom = 4.0 * math.pi * rho * r ** 3 * omega * (Va + u_try) * dr
                    a_p_new = dQ_l / denom if denom > 1e-14 else 0.0
                    a_p_l = max(0.0, min(0.5, 0.5 * (a_p_l + a_p_new)))
                phi_l = math.atan((Va + u_try) / (omega * r * (1.0 - a_p_l)))
                cl_l, cd_l = blade_element_section(beta - phi_l)
                F_l = _prandtl_loss(Z, x, phi_l, geom.hub_ratio)
                w2 = (Va + u_try) ** 2 + (omega * r * (1.0 - a_p_l)) ** 2
                dT_be = 0.5 * rho * w2 * Z * c * (
                    cl_l * math.cos(phi_l) - cd_l * math.sin(phi_l)) * F_l * dr
                dT_mo = 4.0 * math.pi * rho * r * u_try * (Va + u_try) * dr
                dQ_be = 0.5 * rho * w2 * Z * c * (
                    cl_l * math.sin(phi_l) + cd_l * math.cos(phi_l)) * F_l * r * dr
                return dT_be - dT_mo, dQ_be, phi_l

        if axial_induction:
            # Momentum theory stays valid for a > -1, i.e. Va + u > 0.  The lower
            # bound must therefore be allowed to go *negative*: beyond the
            # zero-thrust advance ratio ``J0`` the annulus extracts energy from the
            # flow (a < 0, windmill/brake state), and a one-sided band
            # ``u >= 0`` silently degrades this branch into uncoupled
            # blade-element theory, which over-predicts the braking thrust by up
            # to 8.7x against the Wageningen B-series regression (measured in
            # docs/audit/m1637_bem_vs_bseries_verification.md).
            lo = max(-0.999 * Va, -0.9 * omega * r)
            hi = 3.0 * omega * r
            res_lo = strip(lo)[0]
            res_hi = strip(hi)[0]
            if res_lo * res_hi > 0.0:
                # no crossing in the physical band: both residuals positive means
                # the strip is momentum-limited (heavy loading, clamp to the top of
                # the band); both negative means the strip already produces braking
                # thrust, so the induced velocity is zero
                converged = False
                u = hi if (res_lo > 0.0 and res_hi > 0.0) else lo
            else:
                for _ in range(44):
                    mid = 0.5 * (lo + hi)
                    if strip(mid)[0] > 0.0:
                        lo = mid
                    else:
                        hi = mid
                    if hi - lo < 1e-7 * omega * r:
                        break
                u = 0.5 * (lo + hi)
        # recompute a' and section forces consistently at the converged u
        a_p_l = 0.0
        for _ in range(20):
            phi_l = math.atan((Va + u) / (omega * r * (1.0 - a_p_l)))
            cl_l, cd_l = blade_element_section(beta - phi_l)
            F_l = _prandtl_loss(Z, x, phi_l, geom.hub_ratio)
            w2 = (Va + u) ** 2 + (omega * r * (1.0 - a_p_l)) ** 2
            dQ_l = 0.5 * rho * w2 * Z * c * (
                cl_l * math.sin(phi_l) + cd_l * math.cos(phi_l)) * F_l * r * dr
            denom = 4.0 * math.pi * rho * r ** 3 * omega * (Va + u) * dr
            a_p_new = dQ_l / denom if denom > 1e-14 else 0.0
            a_p_new = max(0.0, min(0.5, a_p_new))
            if abs(a_p_new - a_p_l) < 1e-9:
                a_p_l = a_p_new
                break
            a_p_l = a_p_new
        a_p = a_p_l

        phi = math.atan((Va + u) / (omega * r * (1.0 - a_p)))
        cl, cd = blade_element_section(beta - phi)
        F = _prandtl_loss(Z, x, phi, geom.hub_ratio)
        w2 = (Va + u) ** 2 + (omega * r * (1.0 - a_p)) ** 2
        dT = 0.5 * rho * w2 * Z * c * (cl * math.cos(phi) - cd * math.sin(phi)) * F * dr
        dQ = 0.5 * rho * w2 * Z * c * (cl * math.sin(phi) + cd * math.cos(phi)) * F * r * dr
        thrust += dT
        torque += dQ

    KT = thrust / (rho * n ** 2 * D ** 4)
    KQ = torque / (rho * n ** 2 * D ** 5)
    eta0 = J * KT / (2.0 * math.pi * KQ) if KQ > 1e-14 else 0.0
    return {
        "J": J,
        "KT": KT,
        "KQ": KQ,
        "eta0": max(0.0, eta0),
        "thrust_n": thrust,
        "torque_nm": torque,
        "induction_converged": converged,
        "eta_ideal": ideal_efficiency_bound(KT, J, geom),
    }


def ideal_efficiency_bound(KT: float, J: float, geom: PropellerGeometry) -> float:
    """Actuator-disc efficiency upper bound ``2/(1+sqrt(1+C_T))`` with
    ``C_T = T/(0.5 rho V^2 A0) = 8 KT/(pi J^2)`` (rho and n cancel)."""
    if J <= 1e-9:
        return 1.0
    CT = 8.0 * KT / (math.pi * J * J)
    if CT <= 0.0:
        return 1.0
    return 2.0 / (1.0 + math.sqrt(1.0 + CT))


def open_water_curve(
    geom: PropellerGeometry,
    J_values: Optional[Sequence[float]] = None,
    rho: float = 1025.0,
) -> List[Dict[str, float]]:
    if J_values is None:
        J0 = solve_zero_thrust_advance_ratio(geom, rho=rho)
        J_values = [round(k * J0 / 12.0, 6) for k in range(13)]
    return [open_water_point(geom, float(J), rho=rho) for J in J_values]


def solve_zero_thrust_advance_ratio(
    geom: PropellerGeometry,
    rho: float = 1025.0,
    hi: float = 3.0,
    tol: float = 1e-4,
) -> float:
    """Advance ratio ``J0`` where ``KT`` crosses zero (tolerance 1e-4 in J)."""
    if open_water_point(geom, 0.0, rho=rho)["KT"] <= 0.0:
        return 0.0
    if open_water_point(geom, hi, rho=rho)["KT"] > 0.0:
        raise ValueError(f"KT does not cross zero below J={hi}")
    lo = 0.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if open_water_point(geom, mid, rho=rho)["KT"] > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def best_efficiency_point(
    geom: PropellerGeometry,
    rho: float = 1025.0,
    n_scan: int = 97,
) -> Dict[str, float]:
    """``(J_opt, eta0_max)`` by scan + golden-section refinement."""
    J0 = solve_zero_thrust_advance_ratio(geom, rho=rho)
    if J0 <= 0:
        return {"J": 0.0, "eta0": 0.0, "KT": 0.0, "KQ": 0.0, "J0": 0.0}
    best_J, best_eta = 0.0, -1.0
    for k in range(1, n_scan):
        J = J0 * k / n_scan
        eta = open_water_point(geom, J, rho=rho)["eta0"]
        if eta > best_eta:
            best_J, best_eta = J, eta
    lo = max(0.0, best_J - J0 / n_scan)
    hi = min(J0, best_J + J0 / n_scan)
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    for _ in range(50):
        c1 = hi - gr * (hi - lo)
        c2 = lo + gr * (hi - lo)
        if open_water_point(geom, c1, rho=rho)["eta0"] > open_water_point(geom, c2, rho=rho)["eta0"]:
            hi = c2
        else:
            lo = c1
    J_opt = 0.5 * (lo + hi)
    pt = open_water_point(geom, J_opt, rho=rho)
    return {"J": J_opt, "eta0": pt["eta0"], "KT": pt["KT"], "KQ": pt["KQ"],
            "eta_ideal": pt["eta_ideal"], "J0": J0}