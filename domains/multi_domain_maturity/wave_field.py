"""Airy wave field, Stokes drift, wave drift force (real formulas)."""

from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.81
RHO_WATER = 1025.0


def dispersion(k: float, h: float, g: float = G) -> float:
    if k <= 0 or h <= 0:
        raise ValueError("k 与 h 必须为正")
    return math.sqrt(g * k * math.tanh(k * h))


@dataclass(frozen=True)
class AiryWave:
    a: float
    L: float
    h: float
    g: float = G

    def __post_init__(self) -> None:
        if self.a <= 0 or self.L <= 0 or self.h <= 0:
            raise ValueError("a, L, h 必须为正")

    @property
    def k(self) -> float:
        return 2.0 * math.pi / self.L

    @property
    def omega(self) -> float:
        return dispersion(self.k, self.h, self.g)

    @property
    def T(self) -> float:
        return 2.0 * math.pi / self.omega

    @property
    def c(self) -> float:
        return self.omega / self.k

    def eta(self, x: float, t: float) -> float:
        return self.a * math.cos(self.k * x - self.omega * t)

    def velocity(self, x: float, z: float, t: float) -> tuple[float, float]:
        if z > 1e-12 or z < -self.h - 1e-12:
            raise ValueError("z 必须位于 [-h, 0]")
        s = math.sinh(self.k * self.h)
        u = (self.a * self.omega / s) * math.cosh(self.k * (z + self.h)) * math.sin(
            self.k * x - self.omega * t
        )
        w = (self.a * self.omega / s) * math.cos(self.k * (z + self.h)) * math.cos(
            self.k * x - self.omega * t
        )
        return u, w

    def stokes_drift(self, z: float) -> float:
        if z > 1e-12 or z < -self.h - 1e-12:
            raise ValueError("z 必须位于 [-h, 0]")
        sh = math.sinh(self.k * self.h)
        return (
            self.k * self.a**2 * self.omega / 2.0
        ) * math.cosh(2.0 * self.k * (z + self.h)) / (sh * sh)

    def drift_force(self, C_D: float, A: float, z: float, rho: float = RHO_WATER) -> float:
        if C_D < 0 or A <= 0:
            raise ValueError("C_D>=0, A>0")
        us = self.stokes_drift(z)
        return 0.5 * rho * C_D * A * us * us


def ship_surge_terminal_velocity(
    F_drift: float, C_D: float, A: float, rho: float = RHO_WATER
) -> float:
    if C_D <= 0 or A <= 0:
        raise ValueError("C_D>0, A>0")
    if F_drift < 0:
        raise ValueError("F_drift 必须 >= 0")
    return math.sqrt(2.0 * F_drift / (rho * C_D * A))


def ship_surge_transient(
    F_drift: float, C_D: float, A: float, m: float, t_end: float, dt: float,
    rho: float = RHO_WATER,
) -> list[tuple[float, float]]:
    if m <= 0 or dt <= 0 or t_end <= 0:
        raise ValueError("m>0, dt>0, t_end>0")
    if C_D <= 0 or A <= 0:
        raise ValueError("C_D>0, A>0")

    def accel(v: float) -> float:
        return (F_drift - 0.5 * rho * C_D * A * v * v) / m

    t, v = 0.0, 0.0
    out = [(0.0, 0.0)]
    n = int(round(t_end / dt))
    for _ in range(n):
        k1 = accel(v)
        k2 = accel(v + 0.5 * dt * k1)
        k3 = accel(v + 0.5 * dt * k2)
        k4 = accel(v + dt * k3)
        v += dt * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        t += dt
        out.append((t, v))
    return out

def ship_surge_exact(t: float, F_drift: float, C_D: float, A: float, m: float,
                     rho: float = RHO_WATER) -> float:
    """纵荡 ODE 的精确解 v(t) = v_t tanh(t/tau)，tau = m v_t / F。

    dv/dt = (F/m)(1 - v^2/v_t^2)，v(0)=0，v_t = sqrt(2F/(rho C_D A))。
    """
    if C_D <= 0 or A <= 0 or m <= 0:
        raise ValueError("C_D>0, A>0, m>0")
    if F_drift <= 0:
        return 0.0
    v_t = math.sqrt(2.0 * F_drift / (rho * C_D * A))
    tau = m * v_t / F_drift
    return v_t * math.tanh(t / tau)
