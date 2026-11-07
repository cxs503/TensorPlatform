"""Finite-volume artificial-compressibility (ACM) channel solver.

Solver A of the W10-N07 cross-solver verification pair (solver B is the D2Q9
lattice-Boltzmann solver in :mod:`scripts.vv.lbm`).

Why artificial compressibility
------------------------------
The QoI is a *steady* integral quantity (the drag of an obstruction set) on a
staircase geometry.  The ACM formulation of Chorin (1967) marches the
incompressible equations in a pseudo-time ``tau`` without a pressure-Poisson
solve, so (i) the immersed obstacle is a pure mask condition, (ii) no elliptic
solver tolerance enters the discretisation error budget, and (iii) the identical
code path runs on every grid level.

Discretisation
--------------
* staggered MAC grid: ``u`` at x-faces (x-periodic), ``v`` at y-faces, ``p`` at
  cell centres;
* second-order central fluxes for convection, second-order central diffusion;
  closed faces carry a mirror ghost (``phi_ghost = -phi_node``), which is the
  standard second-order no-slip condition for a wall halfway between the node
  and the ghost position;
* channel walls (rows ``0`` and ``ny-1``) and obstructions use exactly the mask
  of :func:`scripts.vv.cases.solid_mask`, i.e. the same staircase geometry as
  the LBM solver;
* pseudo-time update::

      du/dtau = -div(u U) - dp/dx + nu lap(u) + g
      dv/dtau = -div(v U) - dp/dy + nu lap(v)
      dp/dtau = -beta (du/dx + dv/dy)

  explicit forward Euler with ``dt`` limited by the pseudo-acoustic CFL
  ``dt = cfl*h/(|U|+sqrt(beta))`` and by diffusion ``h**2/(4 nu)``;
* drag from the shared cell-centred extractor
  :func:`scripts.vv.force.surface_force`.

Verification
------------
Unobstructed channel: analytic plane-Poiseuille profile (L2 error < 1 %,
observed order ~2) and analytic wall friction ``g rho H Lx`` (error < 2 %) —
see :mod:`scripts.vv.benchmarks`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .cases import ChannelCase, solid_mask
from .force import surface_force

__all__ = ["FVMSolver", "FVMResult", "run_fvm"]


@dataclass
class FVMResult:
    name: str
    c_d: float
    drag: float
    u_bar: float
    re: float
    steps: int
    nx: int
    ny: int
    divergence: float
    beta: float
    dt: float
    cd_history: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "c_d": self.c_d,
            "drag": self.drag,
            "u_bar": self.u_bar,
            "re": self.re,
            "steps": self.steps,
            "nx": self.nx,
            "ny": self.ny,
            "divergence": self.divergence,
            "beta": self.beta,
            "dt": self.dt,
        }


def _advect(phi_l: np.ndarray, phi_r: np.ndarray, vel: np.ndarray,
            lam: float) -> np.ndarray:
    """Blended central/upwind transported value between two nodes.

    ``lam = 0`` is pure central differencing (second order, prone to odd-even
    decoupling at staircase corners); ``lam = 1`` is first-order upwind.  The
    default blend keeps second-order behaviour for smooth data while damping the
    odd-even mode that otherwise destabilises the ACM iteration at the sharp
    re-entrant corners of the obstacles.
    """
    cen = 0.5 * (phi_l + phi_r)
    up = np.where(vel > 0.0, phi_l, phi_r)
    return (1.0 - lam) * cen + lam * up


def _nb(a: np.ndarray, open_mask: np.ndarray, axis: int, shift: int) -> np.ndarray:
    """Shifted neighbour with mirror ghost (``-a``) where the face is closed."""
    nb = np.roll(a, shift, axis=axis)
    om = np.roll(open_mask, shift, axis=axis)
    return np.where(om, nb, -a)


class FVMSolver:
    """Explicit ACM finite-volume solver on a staggered MAC grid."""

    def __init__(
        self,
        case: ChannelCase,
        nx: int,
        ny: int,
        *,
        walls: bool = True,
        beta: Optional[float] = None,
        cfl: float = 0.35,
        upwind_blend: float = 0.25,
    ):
        self.case = case
        self.nx = int(nx)
        self.ny = int(ny)
        self.h = 1.0
        self.solid = solid_mask(case, self.nx, self.ny, walls=walls)
        self.fluid = ~self.solid
        #: bodies only: the staircase wall rows are *not* part of the drag body
        self.body = self.solid.copy()
        if walls:
            self.body[0, :] = False
            self.body[-1, :] = False
        self.nf = float(self.fluid.sum())
        self.nu = case.nu
        self.g = case.body_force()
        self.frontal_area = case.frontal_area
        # pseudo-acoustic speed must exceed the local velocity, but staying close
        # to it keeps the ACM stiffness low: beta ~ 4 U0**2 is the usual choice
        self.beta = float(beta) if beta is not None else 4.0 * case.u0 ** 2

        fluid_w = np.roll(self.fluid, 1, axis=1)
        self.u_open = self.fluid & fluid_w          # face (j,i) at x=i*h, cells i-1 and i
        self.v_open = np.zeros_like(self.fluid)
        self.v_open[1:, :] = self.fluid[1:, :] & self.fluid[:-1, :]

        self.u = np.zeros((self.ny, self.nx))
        self.v = np.zeros((self.ny, self.nx))
        self.p = np.zeros((self.ny, self.nx))
        if not (0.0 <= upwind_blend <= 1.0):
            raise ValueError("upwind_blend must be in [0, 1]")
        self.lam = float(upwind_blend)
        self.dt = self._stable_dt(cfl)
        self.iterations = 0
        self.last_force: Tuple[float, float] = (0.0, 0.0)

    # -- helpers --------------------------------------------------------
    def _stable_dt(self, cfl: float) -> float:
        # 2-D pseudo-acoustic CFL: dt*(|u|+c)*(1/h + 1/h) <= cfl
        u_max = max(self.case.u0 * 3.0, 1e-6)
        c = math.sqrt(self.beta)
        dt_conv = 0.5 * cfl * self.h / (u_max + c)
        dt_diff = 0.25 * self.h ** 2 / max(self.nu, 1e-12)
        return float(min(dt_conv, dt_diff))

    def initialise_poiseuille(self) -> None:
        """Start on the analytic plane-Poiseuille state."""
        ny = self.ny
        h_w = ny - 2.0
        y_face = np.arange(ny) + 0.5 - 1.0        # u-nodes: distance from wall
        prof = self.g / (2.0 * self.nu) * (y_face * (h_w - y_face))
        self.u = np.where(self.u_open, prof[:, None], 0.0)
        self.v = np.zeros((ny, self.nx))
        self.p = np.zeros((ny, self.nx))

    def initialise_from_cells(self, u_cell: np.ndarray, v_cell: np.ndarray) -> None:
        """Start from a given cell-centred velocity field (grid sequencing)."""
        if u_cell.shape != (self.ny, self.nx):
            raise ValueError("field shape does not match the solver grid")
        u_face = 0.5 * (u_cell + np.roll(u_cell, -1, axis=1))
        v_face = 0.5 * (v_cell + np.roll(v_cell, -1, axis=0))
        self.u = np.where(self.u_open, u_face, 0.0)
        self.v = np.where(self.v_open, v_face, 0.0)
        self.p = np.zeros((self.ny, self.nx))

    # -- one pseudo-time step -------------------------------------------
    def step(self) -> None:
        h = self.h
        nu = self.nu
        u, v, p = self.u, self.v, self.p
        uo, vo = self.u_open, self.v_open

        # neighbours with mirror ghosts at closed faces
        u_E = _nb(u, uo, 1, -1)
        u_W = _nb(u, uo, 1, 1)
        u_N = _nb(u, uo, 0, -1)
        u_S = _nb(u, uo, 0, 1)
        v_E = _nb(v, vo, 1, -1)
        v_W = _nb(v, vo, 1, 1)
        v_N = _nb(v, vo, 0, -1)
        v_S = _nb(v, vo, 0, 1)

        # ---- u momentum (x-faces, face (j,i) between cells i-1 and i) ---
        ue = 0.5 * (u + u_E)
        uw = 0.5 * (u_W + u)
        conv_x = (ue * _advect(u, u_E, ue, self.lam)
                  - uw * _advect(u_W, u, uw, self.lam)) / h
        v_at_u = 0.25 * (v + np.roll(v, -1, axis=1) + v_N + np.roll(v_N, -1, axis=1))
        v_at_u_N = _nb(v_at_u, uo, 0, -1)
        v_at_u_S = _nb(v_at_u, uo, 0, 1)
        conv_y = (v_at_u_N * _advect(u, u_N, v_at_u_N, self.lam)
                  - v_at_u_S * _advect(u_S, u, v_at_u_S, self.lam)) / h
        lap_u = (u_E + u_W + u_N + u_S - 4.0 * u) / (h * h)
        dpdx = (p - np.roll(p, 1, axis=1)) / h
        du = -conv_x - conv_y - dpdx + nu * lap_u + self.g

        # ---- v momentum (y-faces, face (j,i) at y=j*h, cells j-1 and j) --
        ve = 0.5 * (v + v_E)
        vw = 0.5 * (v_W + v)
        vn_avg = 0.5 * (v + v_N)
        conv_yv = (vn_avg * _advect(v, v_N, vn_avg, self.lam)
                   - 0.5 * (v_S + v) * _advect(v_S, v, 0.5 * (v_S + v), self.lam)) / h
        u_at_v = 0.25 * (u + np.roll(u, -1, axis=1) + u_S + np.roll(u_S, -1, axis=1))
        u_at_v_E = np.roll(u_at_v, -1, axis=1)
        u_at_v_W = np.roll(u_at_v, 1, axis=1)
        conv_xv = (u_at_v_E * _advect(v, v_E, ve, self.lam)
                   - u_at_v_W * _advect(v_W, v, vw, self.lam)) / h
        lap_v = (v_E + v_W + v_N + v_S - 4.0 * v) / (h * h)
        dpdy = (p - np.roll(p, 1, axis=0)) / h
        dv = -conv_xv - conv_yv - dpdy + nu * lap_v

        # ---- pressure (pseudo-compressibility) --------------------------
        div = (np.roll(u, -1, axis=1) - u) / h + (np.roll(v, -1, axis=0) - v) / h
        dp = -self.beta * div

        # ---- explicit update with wall conditions -----------------------
        self.u = np.where(uo, u + self.dt * du, 0.0)
        self.v = np.where(vo, v + self.dt * dv, 0.0)
        self.p = p + self.dt * dp
        self.iterations += 1

    # -- diagnostics ----------------------------------------------------
    def cell_centred(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        u = 0.5 * (self.u + np.roll(self.u, -1, axis=1))   # west/east faces of cell i
        v = 0.5 * (self.v + np.roll(self.v, -1, axis=0))   # south/north faces of cell j
        return self.p, u, v

    def bulk_velocity(self) -> float:
        _p, u, _v = self.cell_centred()
        return float(u[self.fluid].sum() / self.nf)

    def divergence_rms(self) -> float:
        h = self.h
        div = (np.roll(self.u, -1, axis=1) - self.u) / h + (np.roll(self.v, -1, axis=0) - self.v) / h
        return float(np.sqrt((div[self.fluid] ** 2).mean()))

    def force(self) -> Tuple[float, float]:
        p, u, v = self.cell_centred()
        return surface_force(p, u, v, self.body, self.h, self.nu)


def run_fvm(
    case: ChannelCase,
    nx: int,
    ny: int,
    *,
    max_steps: int = 400_000,
    window: int = 200,
    tol: float = 1e-6,
    walls: bool = True,
    keep_history: bool = False,
    beta: Optional[float] = None,
    cfl: float = 0.35,
    upwind_blend: float = 0.25,
) -> FVMResult:
    """March the ACM solver to steady state and return the drag QoI."""
    solver = FVMSolver(case, nx, ny, walls=walls, beta=beta, cfl=cfl,
                       upwind_blend=upwind_blend)
    solver.initialise_poiseuille()
    hist: List[float] = []
    prev: Optional[float] = None
    steps = 0
    for step in range(1, max_steps + 1):
        solver.step()
        steps = step
        if step % window == 0:
            fx, _fy = solver.force()
            ubar = solver.bulk_velocity()
            cd = (fx / (0.5 * ubar ** 2 * solver.frontal_area)
                  if solver.frontal_area else float("nan"))
            hist.append(cd)
            if prev is not None and abs(cd - prev) <= tol * max(abs(prev), 1e-12):
                break
            prev = cd
    fx, _fy = solver.force()
    ubar = solver.bulk_velocity()
    return FVMResult(
        name=case.name,
        c_d=fx / (0.5 * ubar ** 2 * solver.frontal_area) if solver.frontal_area else float("nan"),
        drag=fx,
        u_bar=ubar,
        re=ubar * case.d_ref / case.nu,
        steps=steps,
        nx=nx,
        ny=ny,
        divergence=solver.divergence_rms(),
        beta=solver.beta,
        dt=solver.dt,
        cd_history=hist if keep_history else [],
    )