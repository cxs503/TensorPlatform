"""Shan-Chen pseudopotential multiphase lattice-Boltzmann solver (D2Q9, SRT-BGK).

Why this module exists
----------------------
The platform's multiphase work (``Wave-10`` campaign) had no first-principles
two-phase kernel of its own: every ``CAC``/``MCMP`` statement in the tree was
either a port of a legacy code path or an unverifiable claim.  This module is a
minimal, auditable SC kernel with **two interchangeable force-coupling routes**
so that the "droplet water-entry anomaly" can be *characterised* (measured) and
*improved* (attacked) under a controlled single-variable comparison.

Both routes share the same pseudopotential, the same force stencil, the same
collision operator and the same initial data.  They differ only in how the
interaction force is coupled to the populations:

``scheme="shifted"``  (route A, legacy)
    The whole force is folded into the equilibrium velocity,
    ``u_eq = u + tau * F / rho``, and no source term is added.  This is the
    coupling used by most MCMP/CAC production ports.
``scheme="guo"``      (route B, balanced)
    Guo, Zheng & Shi (2002) complete forcing term

        S_i = (1 - 1/(2 tau)) w_i [ 3 (c_i - u).F + 9 (c_i.u)(c_i.F) ]

    with the half-force velocity ``u = sum(c_i f_i) + F/(2 rho)``.

Conventions (all lattice units, ``dt = dx = 1``)
-----------------------------------------------
* pseudopotential ``psi(rho) = rho0 (1 - exp(-rho/rho0))``  (Shan & Chen 1993)
* interaction force ``F(x) = -G psi(x) sum_i w_i psi(x + c_i) c_i``
* equation of state ``p = cs^2 rho + (G cs^2 / 2) psi^2``, ``cs^2 = 1/3``
* periodic in x and y (no solid boundary enters any error budget here)
* ``u_raw = sum(c_i f_i) / rho``      -- scheme-agnostic macroscopic velocity
* ``u_phys = u_raw + F / (2 rho)``    -- half-force velocity (physical transport)
* ``tau = 3 nu + 1/2``

References
----------
Shan & Chen, Phys. Rev. E 47 (1993) 1815; Guo, Zheng & Shi, Phys. Fluids 14
(2002) 2007; Wagner, Phys. Rev. E 67 (2003) 056703; Sbragaglia et al., J.
Comput. Phys. 222 (2007) 471; Huang, Sukop & Lu, Phys. Rev. E 91 (2015) 013310.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

CS2 = 1.0 / 3.0
CX = np.array([0.0, 1.0, 0.0, -1.0, 0.0, 1.0, -1.0, -1.0, 1.0])
CY = np.array([0.0, 0.0, 1.0, 0.0, -1.0, 1.0, 1.0, -1.0, -1.0])
W = np.array([4.0 / 9.0] + [1.0 / 9.0] * 4 + [1.0 / 36.0] * 4)
SCHEMES = ("shifted", "guo")

__all__ = [
    "ShanChen",
    "pseudopotential",
    "eos_pressure",
    "equilibrium",
    "bilinear_sample",
    "advect_markers",
    "CS2",
    "W",
]


def pseudopotential(rho: np.ndarray, rho0: float = 1.0) -> np.ndarray:
    """Shan-Chen effective mass ``psi = rho0 (1 - exp(-rho/rho0))``."""
    return rho0 * (1.0 - np.exp(-np.asarray(rho, dtype=float) / rho0))


def eos_pressure(rho: np.ndarray, G: float, rho0: float = 1.0) -> np.ndarray:
    """Shan-Chen equation of state ``p = cs^2 rho + (G cs^2 / 2) psi^2``."""
    psi = pseudopotential(rho, rho0)
    return CS2 * rho + 0.5 * CS2 * G * psi * psi


def equilibrium(rho: np.ndarray, ux: np.ndarray, uy: np.ndarray) -> np.ndarray:
    """D2Q9 second-order equilibrium, shape ``(9, ny, nx)``."""
    cu = 3.0 * (CX[:, None, None] * ux[None] + CY[:, None, None] * uy[None])
    usq = 1.5 * (ux * ux + uy * uy)
    return rho[None] * W[:, None, None] * (1.0 + cu + 0.5 * cu * cu - usq[None])


def bilinear_sample(field: np.ndarray, py: np.ndarray, px: np.ndarray) -> np.ndarray:
    """Periodic bilinear interpolation of ``field[iy, ix]`` at floating positions."""
    ny, nx = field.shape
    y0 = np.floor(py).astype(int)
    x0 = np.floor(px).astype(int)
    ty = py - y0
    tx = px - x0
    y0 %= ny
    x0 %= nx
    y1 = (y0 + 1) % ny
    x1 = (x0 + 1) % nx
    return (
        field[y0, x0] * (1 - ty) * (1 - tx)
        + field[y1, x0] * ty * (1 - tx)
        + field[y0, x1] * (1 - ty) * tx
        + field[y1, x1] * ty * tx
    )


def advect_markers(
    px: np.ndarray,
    py: np.ndarray,
    ux: np.ndarray,
    uy: np.ndarray,
    dt: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Midpoint (RK2) advection of passive markers in the periodic velocity field.

    Markers are a *Lagrangian* diagnostic: they answer "how fast does this piece
    of droplet fluid move?" without the Eulerian ambiguity that appears once the
    droplet coalesces with the pool.
    """
    u1x = bilinear_sample(ux, py, px)
    u1y = bilinear_sample(uy, py, px)
    mx = px + 0.5 * dt * u1x
    my = py + 0.5 * dt * u1y
    u2x = bilinear_sample(ux, my, mx)
    u2y = bilinear_sample(uy, my, mx)
    return px + dt * u2x, py + dt * u2y


@dataclass
class StepStats:
    """Per-call diagnostics (cheap; computed every ``diag_every`` steps)."""

    step: int
    t: float
    mass: float
    momentum_x: float
    momentum_y: float
    kinetic: float
    max_speed: float
    mach: float
    rho_min: float
    rho_max: float
    extra: Dict[str, float] = field(default_factory=dict)


class ShanChen:
    """D2Q9 single-component Shan-Chen multiphase solver on a periodic box."""

    def __init__(
        self,
        nx: int,
        ny: int,
        *,
        G: float = -5.0,
        rho0: float = 1.0,
        tau: float = 1.0,
        scheme: str = "guo",
        g_ramp: int = 0,
        rho_init: Optional[np.ndarray] = None,
        ux_init: Optional[np.ndarray] = None,
        uy_init: Optional[np.ndarray] = None,
    ) -> None:
        if scheme not in SCHEMES:
            raise ValueError(f"scheme must be one of {SCHEMES}, got {scheme!r}")
        if tau <= 0.5:
            raise ValueError(f"tau={tau} must exceed 0.5 for BGK stability")
        self.nx = int(nx)
        self.ny = int(ny)
        self.G = float(G)
        self.rho0 = float(rho0)
        self.tau = float(tau)
        self.scheme = scheme
        self.G_ramp = int(g_ramp)
        self.nu = (self.tau - 0.5) / 3.0
        self.steps_done = 0
        rho = np.ones((self.ny, self.nx)) if rho_init is None else np.array(rho_init, float)
        ux = np.zeros_like(rho) if ux_init is None else np.array(ux_init, float)
        uy = np.zeros_like(rho) if uy_init is None else np.array(uy_init, float)
        if rho.shape != (self.ny, self.nx):
            raise ValueError(f"rho_init shape {rho.shape} != {(self.ny, self.nx)}")
        self.f = equilibrium(rho, ux, uy)
        self._u_raw = (ux.copy(), uy.copy())
        self._F = np.zeros((2, self.ny, self.nx))
        #: optional externally supplied body force density, added to the interaction
        #: force (used to spin a droplet up to a prescribed impact velocity without
        #: resetting the populations, which would create a vortex sheet)
        self.body_F = np.zeros((2, self.ny, self.nx))
        self._refresh()

    # ------------------------------------------------------------------ fields
    def _refresh(self) -> None:
        rho = self.f.sum(axis=0)
        self._u_raw = (
            (self.f * CX[:, None, None]).sum(axis=0) / rho,
            (self.f * CY[:, None, None]).sum(axis=0) / rho,
        )
        self._rho = rho
        self._F = self._interaction_force(rho)

    @property
    def rho(self) -> np.ndarray:
        return self._rho

    @property
    def u_raw(self) -> Tuple[np.ndarray, np.ndarray]:
        """``sum(c_i f_i)/rho`` -- computed from populations only (no scheme bias)."""
        return self._u_raw

    @property
    def u_phys(self) -> Tuple[np.ndarray, np.ndarray]:
        """Half-force velocity ``u + F/(2 rho)`` -- the transport velocity."""
        ux, uy = self._u_raw
        return (
            ux + 0.5 * self._F[0] / self._rho,
            uy + 0.5 * self._F[1] / self._rho,
        )

    @property
    def force(self) -> np.ndarray:
        return self._F

    @property
    def pressure(self) -> np.ndarray:
        return eos_pressure(self._rho, self.G, self.rho0)

    @property
    def G_effective(self) -> float:
        """Interaction strength actually applied (generation up-ramped if requested).

        Starting a Shan-Chen run directly at ``G = -5`` from a sharp initial
        interface produces a lattice force of order 0.4 (Mach ~ 0.3) and blows up;
        ramping ``G`` over a few hundred steps is the standard cure and is used
        everywhere in this module's case definitions.
        """
        if self.G_ramp <= 0:
            return self.G
        return self.G * min(1.0, self.steps_done / float(self.G_ramp))

    def _interaction_force(self, rho: np.ndarray) -> np.ndarray:
        psi = pseudopotential(rho, self.rho0)
        geff = self.G_effective
        fx = np.zeros_like(rho)
        fy = np.zeros_like(rho)
        for k in range(1, 9):
            psi_shift = np.roll(np.roll(psi, -int(CY[k]), axis=0), -int(CX[k]), axis=1)
            contrib = -geff * W[k] * psi * psi_shift
            fx += contrib * CX[k]
            fy += contrib * CY[k]
        return np.stack([fx, fy]) + self.body_F

    # --------------------------------------------------------------- transport
    def step(self, n: int = 1, *, diag_every: int = 0,
             diag_hook=None) -> List[StepStats]:
        """Advance ``n`` lattice steps.  Optional ``diag_hook(solver, step)``."""
        out: List[StepStats] = []
        self._refresh()
        for _ in range(int(n)):
            self._advance()
            self.steps_done += 1
            if diag_every and diag_hook is not None and self.steps_done % diag_every == 0:
                out.append(diag_hook(self, self.steps_done))
        return out

    def _advance(self) -> None:
        rho = self._rho
        ux_raw, uy_raw = self._u_raw
        Fx, Fy = self._F
        tau = self.tau
        if self.scheme == "shifted":
            ux = ux_raw + tau * Fx / rho
            uy = uy_raw + tau * Fy / rho
            feq = equilibrium(rho, ux, uy)
            f_post = self.f - (self.f - feq) / tau
        else:  # "guo"
            ux = ux_raw + 0.5 * Fx / rho
            uy = uy_raw + 0.5 * Fy / rho
            feq = equilibrium(rho, ux, uy)
            om = 1.0 / tau
            cu = CX[:, None, None] * ux[None] + CY[:, None, None] * uy[None]
            cf = CX[:, None, None] * Fx[None] + CY[:, None, None] * Fy[None]
            uf = (ux * Fx + uy * Fy)[None]
            src = (1.0 - 0.5 * om) * W[:, None, None] * (3.0 * cf - 3.0 * uf + 9.0 * cu * cf)
            f_post = self.f - om * (self.f - feq) + src
        # stream: f_k(x + c_k, t+1) = f_post_k(x, t)
        f_new = np.empty_like(f_post)
        f_new[0] = f_post[0]
        for k in range(1, 9):
            f_new[k] = np.roll(np.roll(f_post[k], int(CY[k]), axis=0), int(CX[k]), axis=1)
        self.f = f_new
        # refresh macros so the next step sees them (one force evaluation per step)
        self._refresh()

    # -------------------------------------------------------------- diagnostics
    def stats(self, extra: Optional[Dict[str, float]] = None) -> StepStats:
        rho = self._rho
        ux, uy = self._u_raw
        speed = np.sqrt(ux * ux + uy * uy)
        return StepStats(
            step=self.steps_done,
            t=float(self.steps_done),
            mass=float(rho.sum()),
            momentum_x=float((rho * ux).sum()),
            momentum_y=float((rho * uy).sum()),
            kinetic=float(0.5 * (rho * (ux * ux + uy * uy)).sum()),
            max_speed=float(speed.max()),
            mach=float(speed.max() / np.sqrt(CS2)),
            rho_min=float(rho.min()),
            rho_max=float(rho.max()),
            extra=dict(extra or {}),
        )

    def max_spurious_speed(self) -> float:
        """``max|u_raw|`` -- the standard parasitic-current amplitude."""
        ux, uy = self._u_raw
        return float(np.sqrt(ux * ux + uy * uy).max())

    def blob_radius(self, threshold: float, *, phase: str = "dense") -> float:
        """Area-equivalent radius of the ``rho > threshold`` (or ``<``) region."""
        mask = self._rho > threshold if phase == "dense" else self._rho < threshold
        area = float(mask.sum())
        if area <= 0.0:
            return 0.0
        return float(np.sqrt(area / np.pi))