"""Propulsion performance (推进) calculations.

The ship overall-performance platform shipped ``calc.resistance`` (阻力) and
``calc.stability`` (稳性) as REAL capabilities, but had **no** propulsion code at
all: open-water efficiency and self-propulsion were only reachable through the
external ship service (``:8001``/``:8002``), which is not part of this repo and
has no in-repo verification.  This module closes that gap with four
self-contained building blocks:

* :func:`actuator_disk`        - Lanchester-Glauert actuator-disk momentum
  theory: induced velocity, ideal efficiency, pressure jump, energy flux.
* :func:`open_water`           - open-water point evaluation of a propeller
  curve ``(K_T(J), K_Q(J))``.
* :func:`self_propulsion_point`- hull/propeller equilibrium: solves the shaft
  rate that balances thrust and resistance through wake fraction ``w`` and
  thrust deduction ``t``, then reports the quasi-propulsive coefficient.
* :func:`attained_eedi`        - IMO MEPC.308(73) attained EEDI (green shipping).

Verification philosophy
-----------------------
Every routine here is closed form or root-solved against identities that are
**analytic**, so accuracy is checkable to machine precision without an
empirical dataset:

* ideal efficiency ``eta_i = 2 / (1 + sqrt(1 + C_T))`` (Lanchester-Glauert),
* momentum balance ``T = 2 rho A v_a (V + v_a)``,
* pressure jump ``dP = T / A = 2 rho v_a (V + v_a)``,
* energy flux ``T (V + v_a) = 0.5 rho A (V + v_a) [(V + 2 v_a)^2 - V^2]``,
* open-water definition ``eta_O = J K_T / (2 pi K_Q)``,
* quasi-propulsive chain ``eta_D = eta_O eta_R (1 - t) / (1 - w)``.

Empirical quantities (wake fraction, thrust deduction, K_T/K_Q curves) are
**caller supplied inputs**.  Their absolute accuracy is out of scope for this
module and is never fabricated here; only their limits and monotonicity are
asserted.

SI units throughout (m, s, N, W, rps).  Angles in radians unless named ``_deg``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

#: Standard gravity used by the platform's other ship calculations (m/s^2).
GRAVITY = 9.80665
#: Sea water density (kg/m^3); 1025 is the platform default for propulsion work.
DEFAULT_RHO = 1025.0
#: Two pi, cached for readability of the torque/power relations.
TWO_PI = 2.0 * math.pi

CurveFn = Callable[[float], float]


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def disk_area(diameter_m: float) -> float:
    """Disc area of a propeller of diameter ``diameter_m`` (m^2)."""
    if diameter_m <= 0.0:
        raise ValueError(f"diameter must be > 0, got {diameter_m}")
    return 0.25 * math.pi * diameter_m ** 2


def thrust_coefficient(thrust_n: float, diameter_m: float, speed_ms: float,
                       rho: float = DEFAULT_RHO) -> float:
    """Thrust loading coefficient ``C_T = T / (0.5 rho A V^2)``."""
    if speed_ms <= 0.0:
        raise ValueError(f"speed must be > 0, got {speed_ms}")
    if rho <= 0.0:
        raise ValueError(f"density must be > 0, got {rho}")
    return thrust_n / (0.5 * rho * disk_area(diameter_m) * speed_ms ** 2)


# ---------------------------------------------------------------------------
# actuator-disk / momentum theory
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActuatorDiskResult:
    """Result of the actuator-disk momentum calculation."""

    disk_area_m2: float
    thrust_coefficient: float
    induced_velocity_ms: float
    induced_velocity_analytic_ms: float
    advance_ratio: float
    ideal_efficiency: float
    ideal_efficiency_analytic: float
    momentum_residual_n: float
    pressure_jump_pa: float
    energy_flux_w: float
    ideal_power_w: float
    effective_power_w: float


def actuator_disk(speed_ms: float, thrust_n: float, diameter_m: float,
                  rho: float = DEFAULT_RHO) -> ActuatorDiskResult:
    """Lanchester-Glauert actuator-disk solution for a prescribed thrust.

    Given the advance speed ``V`` and thrust ``T`` of a propeller of diameter
    ``D``, the axial induced velocity ``v_a`` at the disc follows from the
    momentum balance

    ``T = 2 rho A v_a (V + v_a)``      (A = pi D^2 / 4)

    which is solved in closed form.  Two independent routes are evaluated:

    * direct dimensional quadratic solve,
    * dimensionless analytic root ``x = (sqrt(1 + C_T) - 1) / 2`` with
      ``v_a = x V`` and ``eta_i = 2 / (1 + sqrt(1 + C_T))``.

    Their difference (``momentum_residual_n`` for the restated balance, and the
    two induced velocities) is the machine-precision verification signal used
    by ``backend/tests/test_propulsion.py``.
    """
    if speed_ms <= 0.0:
        raise ValueError(f"speed must be > 0, got {speed_ms}")
    if thrust_n < 0.0:
        raise ValueError(f"thrust must be >= 0, got {thrust_n}")
    if rho <= 0.0:
        raise ValueError(f"density must be > 0, got {rho}")

    area = disk_area(diameter_m)
    ct = thrust_n / (0.5 * rho * area * speed_ms ** 2)

    # direct quadratic solve of 2 rho A v_a^2 + 2 rho A V v_a - T = 0
    disc = speed_ms ** 2 + 2.0 * thrust_n / (rho * area)
    va = 0.5 * (math.sqrt(disc) - speed_ms)

    # dimensionless analytic root (independent route)
    x = 0.5 * (math.sqrt(1.0 + ct) - 1.0)
    va_analytic = x * speed_ms

    ideal_efficiency = speed_ms / (speed_ms + va)
    ideal_efficiency_analytic = 2.0 / (1.0 + math.sqrt(1.0 + ct))

    thrust_from_momentum = 2.0 * rho * area * va * (speed_ms + va)
    pressure_jump = thrust_n / area
    energy_flux = (0.5 * rho * area * (speed_ms + va)
                   * ((speed_ms + 2.0 * va) ** 2 - speed_ms ** 2))

    return ActuatorDiskResult(
        disk_area_m2=area,
        thrust_coefficient=ct,
        induced_velocity_ms=va,
        induced_velocity_analytic_ms=va_analytic,
        advance_ratio=x,
        ideal_efficiency=ideal_efficiency,
        ideal_efficiency_analytic=ideal_efficiency_analytic,
        momentum_residual_n=thrust_from_momentum - thrust_n,
        pressure_jump_pa=pressure_jump,
        energy_flux_w=energy_flux,
        ideal_power_w=thrust_n * (speed_ms + va),
        effective_power_w=thrust_n * speed_ms,
    )


# ---------------------------------------------------------------------------
# open-water point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenWaterResult:
    """Open-water point of a propeller at a prescribed shaft rate."""

    advance_ratio: float
    thrust_coefficient: float
    torque_coefficient: float
    open_water_efficiency: float
    open_water_efficiency_identity: float
    thrust_n: float
    torque_nm: float
    delivered_power_w: float


def open_water(n_rps: float, advance_speed_ms: float, diameter_m: float,
               kt_fn: CurveFn, kq_fn: CurveFn,
               rho: float = DEFAULT_RHO) -> OpenWaterResult:
    """Evaluate an open-water point from user supplied ``K_T(J)``/``K_Q(J)``.

    ``advance_speed_ms`` is the speed of advance ``V_a`` (i.e. ``V (1 - w)`` for
    a hull-mounted propeller), so ``J = V_a / (n D)``.

    The returned ``open_water_efficiency`` is computed from the force/moment
    output (``P_E / P_D``) while ``open_water_efficiency_identity`` uses the
    closed form ``J K_T / (2 pi K_Q)``; the two must agree to machine precision.
    """
    if n_rps <= 0.0:
        raise ValueError(f"shaft rate must be > 0, got {n_rps}")
    if advance_speed_ms < 0.0:
        raise ValueError(f"advance speed must be >= 0, got {advance_speed_ms}")
    if rho <= 0.0:
        raise ValueError(f"density must be > 0, got {rho}")

    area_term = disk_area(diameter_m)  # validates diameter
    _ = area_term
    j = advance_speed_ms / (n_rps * diameter_m)
    kt = kt_fn(j)
    kq = kq_fn(j)

    thrust = kt * rho * n_rps ** 2 * diameter_m ** 4
    torque = kq * rho * n_rps ** 2 * diameter_m ** 5
    delivered = TWO_PI * n_rps * torque
    effective = thrust * advance_speed_ms

    eta_force = effective / delivered if delivered > 0.0 else 0.0
    eta_identity = (j * kt / (TWO_PI * kq)) if kq > 0.0 else 0.0

    return OpenWaterResult(
        advance_ratio=j,
        thrust_coefficient=kt,
        torque_coefficient=kq,
        open_water_efficiency=eta_force,
        open_water_efficiency_identity=eta_identity,
        thrust_n=thrust,
        torque_nm=torque,
        delivered_power_w=delivered,
    )


# ---------------------------------------------------------------------------
# self-propulsion point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SelfPropulsionResult:
    """Hull/propeller equilibrium at a prescribed ship speed."""

    ship_speed_ms: float
    resistance_n: float
    required_thrust_n: float
    wake_fraction: float
    thrust_deduction: float
    relative_rotative_efficiency: float
    revolutions_per_s: float
    advance_ratio: float
    thrust_coefficient: float
    torque_coefficient: float
    open_water_efficiency: float
    thrust_n: float
    torque_nm: float
    effective_power_w: float
    delivered_power_w: float
    quasi_propulsive_efficiency: float
    quasi_propulsive_efficiency_identity: float
    thrust_balance_residual_n: float
    iterations: int


def self_propulsion_point(resistance_n: float, ship_speed_ms: float,
                          diameter_m: float, kt_fn: CurveFn, kq_fn: CurveFn,
                          wake_fraction: float, thrust_deduction: float,
                          relative_rotative_efficiency: float = 1.0,
                          rho: float = DEFAULT_RHO,
                          n_lo: float = 1.0e-3,
                          n_hi: float = 60.0,
                          n_max_hard: float = 100.0,
                          tol: float = 1.0e-12,
                          max_iter: int = 200) -> SelfPropulsionResult:
    """Solve the hull/propeller equilibrium (self-propulsion point).

    Physics: the propeller must supply the *thrust* that balances the towed
    resistance after thrust deduction,

        ``T_req = R_T / (1 - t)``,

    while it works in the hull wake, so the speed of advance is
    ``V_a = V (1 - w)`` and ``J = V_a / (n D)``.  The shaft rate ``n`` is found
    by bisection on

        ``f(n) = rho n^2 D^4 K_T(V_a / (n D)) - T_req``

    which is monotonically increasing in ``n`` for a monotone decreasing
    ``K_T(J)`` curve.  ``kq_fn`` supplies the torque curve.

    Checks carried in the result: the force-balance residual
    (``thrust_balance_residual_n``) and the quasi-propulsive identity
    ``eta_D = eta_O eta_R (1 - t) / (1 - w)``
    (``quasi_propulsive_efficiency_identity``).
    """
    if resistance_n < 0.0:
        raise ValueError(f"resistance must be >= 0, got {resistance_n}")
    if ship_speed_ms <= 0.0:
        raise ValueError(f"ship speed must be > 0, got {ship_speed_ms}")
    if not 0.0 <= wake_fraction < 1.0:
        raise ValueError(f"wake fraction must be in [0, 1), got {wake_fraction}")
    if not 0.0 <= thrust_deduction < 1.0:
        raise ValueError(f"thrust deduction must be in [0, 1), got {thrust_deduction}")
    if not 0.0 < relative_rotative_efficiency <= 1.5:
        raise ValueError(
            f"relative rotative efficiency must be in (0, 1.5], got "
            f"{relative_rotative_efficiency}")
    if rho <= 0.0:
        raise ValueError(f"density must be > 0, got {rho}")

    _ = disk_area(diameter_m)  # validates diameter
    required_thrust = resistance_n / (1.0 - thrust_deduction)
    advance_speed = ship_speed_ms * (1.0 - wake_fraction)
    d4 = diameter_m ** 4

    def residual(n: float) -> float:
        j = advance_speed / (n * diameter_m)
        return rho * n ** 2 * d4 * kt_fn(j) - required_thrust

    if n_max_hard <= n_hi:
        raise ValueError(
            f"n_max_hard ({n_max_hard}) must exceed the initial bracket {n_hi}")
    lo, hi = n_lo, n_hi
    f_lo = residual(lo)
    f_hi = residual(hi)
    # widen the upper bracket if the required shaft rate is large, but never
    # beyond the feasible marine-propulsor limit n_max_hard
    while f_hi < 0.0 and hi < n_max_hard:
        hi = min(hi * 1.5, n_max_hard)
        f_hi = residual(hi)
    if f_lo > 0.0:
        raise ValueError(
            "no self-propulsion point: K_T at the lowest shaft rate already "
            f"over-supplies thrust (residual {f_lo:.3e} N)")
    if f_hi < 0.0:
        raise ValueError(
            "no feasible self-propulsion point: the required thrust cannot be "
            f"reached below n_max_hard={n_max_hard} rps "
            f"(residual at n={hi:.2f} rps is {f_hi:.3e} N)")

    iterations = 0
    n = 0.5 * (lo + hi)
    for iterations in range(1, max_iter + 1):
        n = 0.5 * (lo + hi)
        f_mid = residual(n)
        if abs(f_mid) <= tol * max(required_thrust, 1.0):
            break
        if f_mid < 0.0:
            lo = n
        else:
            hi = n

    j = advance_speed / (n * diameter_m)
    kt = kt_fn(j)
    kq = kq_fn(j)
    thrust = kt * rho * n ** 2 * d4
    torque = kq * rho * n ** 2 * diameter_m ** 5
    # eta_R = open-water torque / behind-hull torque, so the shaft power that
    # produces the same thrust is the open-water power divided by eta_R.
    delivered = TWO_PI * n * torque / relative_rotative_efficiency
    effective = resistance_n * ship_speed_ms

    eta_d = effective / delivered if delivered > 0.0 else 0.0
    eta_o = (j * kt / (TWO_PI * kq)) if kq > 0.0 else 0.0
    eta_d_identity = (eta_o * relative_rotative_efficiency
                      * (1.0 - thrust_deduction) / (1.0 - wake_fraction))

    return SelfPropulsionResult(
        ship_speed_ms=ship_speed_ms,
        resistance_n=resistance_n,
        required_thrust_n=required_thrust,
        wake_fraction=wake_fraction,
        thrust_deduction=thrust_deduction,
        relative_rotative_efficiency=relative_rotative_efficiency,
        revolutions_per_s=n,
        advance_ratio=j,
        thrust_coefficient=kt,
        torque_coefficient=kq,
        open_water_efficiency=eta_o,
        thrust_n=thrust,
        torque_nm=torque,
        effective_power_w=effective,
        delivered_power_w=delivered,
        quasi_propulsive_efficiency=eta_d,
        quasi_propulsive_efficiency_identity=eta_d_identity,
        thrust_balance_residual_n=thrust - required_thrust,
        iterations=iterations,
    )


# ---------------------------------------------------------------------------
# green shipping: attained EEDI (IMO MEPC.308(73))
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EediResult:
    """Attained EEDI and its additive components (g CO2 per tonne-mile)."""

    eedi_g_co2_per_t_nm: float
    main_engine_term_g_per_h: float
    auxiliary_engine_term_g_per_h: float
    innovative_term_g_per_h: float
    numerator_g_per_h: float
    denominator_t_nm_per_h: float


def attained_eedi(capacity_t: float, v_ref_kn: float,
                  p_me_kw: float, sfc_me_g_kwh: float, cf_me: float,
                  p_ae_kw: float = 0.0, sfc_ae_g_kwh: float = 0.0,
                  cf_ae: Optional[float] = None,
                  p_eff_kw: float = 0.0, sfc_eff_g_kwh: float = 0.0,
                  cf_eff: Optional[float] = None, f_eff: float = 1.0,
                  f_i: float = 1.0, f_c: float = 1.0, f_l: float = 1.0,
                  f_w: Optional[float] = None) -> EediResult:
    """Attained EEDI per IMO MEPC.308(73).

    ``EEDI = [(prod f_j)(sum P_ME C_F SFC_ME) + (P_AE C_F_AE SFC_AE)
    - (sum f_eff P_eff C_F SFC_ME)] / (f_i f_c f_l Capacity f_w V_ref)``

    Unit chain: kW * g/kWh -> g/h; capacity (t) * V (kn) -> t*nm/h, so the ratio
    is g CO2 per tonne-mile.

    ``f_w`` is the speed-decrease (weather) coefficient of MEPC.1/Circ.850 and
    defaults to 1.0 when no approved value is supplied; it must not be aliased
    to ``f_i`` (ice-class capacity correction), which enters the denominator
    only.  ``prod f_j`` is taken as 1 (no sea-margin / ship-specific factors).
    """
    if capacity_t <= 0.0:
        raise ValueError(f"capacity must be > 0, got {capacity_t}")
    if v_ref_kn <= 0.0:
        raise ValueError(f"reference speed must be > 0, got {v_ref_kn}")
    if p_me_kw < 0.0 or p_ae_kw < 0.0 or p_eff_kw < 0.0:
        raise ValueError("engine powers must be >= 0")
    if sfc_me_g_kwh < 0.0 or sfc_ae_g_kwh < 0.0 or sfc_eff_g_kwh < 0.0:
        raise ValueError("specific fuel consumptions must be >= 0")
    if cf_me <= 0.0:
        raise ValueError(f"C_F for main engine must be > 0, got {cf_me}")
    if f_i <= 0.0 or f_c <= 0.0 or f_l <= 0.0:
        raise ValueError("capacity/cubic/load correction factors must be > 0")
    cf_ae_resolved = cf_me if cf_ae is None else cf_ae
    cf_eff_resolved = cf_me if cf_eff is None else cf_eff
    f_w_resolved = 1.0 if f_w is None else f_w
    if f_w_resolved <= 0.0:
        raise ValueError(f"f_w must be > 0, got {f_w_resolved}")

    me_term = p_me_kw * sfc_me_g_kwh * cf_me
    ae_term = p_ae_kw * sfc_ae_g_kwh * cf_ae_resolved
    eff_term = f_eff * p_eff_kw * sfc_eff_g_kwh * cf_eff_resolved
    numerator = me_term + ae_term - eff_term
    denominator = f_i * f_c * f_l * capacity_t * f_w_resolved * v_ref_kn

    return EediResult(
        eedi_g_co2_per_t_nm=numerator / denominator,
        main_engine_term_g_per_h=me_term,
        auxiliary_engine_term_g_per_h=ae_term,
        innovative_term_g_per_h=eff_term,
        numerator_g_per_h=numerator,
        denominator_t_nm_per_h=denominator,
    )


def propeller_curve(coefficients_kt, coefficients_kq) -> tuple:
    """Build ``(kt_fn, kq_fn)`` polynomial curves from ascending-power coefficient lists.

    ``coefficients_kt = [k0, k1, k2]`` gives ``K_T(J) = k0 + k1 J + k2 J^2``.
    This is a *container* for caller-supplied curve fits (e.g. a B-series fit);
    the platform does not assert the provenance of those coefficients.
    """
    def _poly(coeffs):
        def _fn(j: float) -> float:
            value = 0.0
            power = 1.0
            for c in coeffs:
                value += c * power
                power *= j
            return value
        return _fn

    return _poly(list(coefficients_kt)), _poly(list(coefficients_kq))