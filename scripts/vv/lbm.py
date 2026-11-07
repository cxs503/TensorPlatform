"""D2Q9 lattice-Boltzmann channel solver with halfway bounce-back and Guo forcing.

Solver B of the W10-N07 cross-solver verification pair (solver A is the
finite-volume artificial-compressibility solver in :mod:`scripts.vv.fvm`).

Discretisation
--------------
* D2Q9, single-relaxation-time BGK collision, ``tau = 3 nu + 0.5``;
* Guo, Zheng & Shi (2002) forcing, complete source term, second order in the
  lattice Mach number::

      S_i = (1 - dt/(2 tau)) w_i [ 3 (c_i.F - u.F) + 9 (c_i.u)(c_i.F) ]

  with the shifted equilibrium velocity ``u_g = u + F/(2 rho)`` and the
  macroscopic velocity ``u = sum(c_i f_i) + F/(2 rho)``;
* halfway bounce-back on the staircase solid mask (walls and obstructions),
  placing the no-slip surface midway between a fluid and a solid node;
* x periodicity by construction (no inlet/outlet condition enters the error
  budget);
* drag from the momentum-exchange (ME) sum over boundary links: each reflected
  population delivers ``2 c_i f_i`` momentum to the wall.

Verification anchors (see :mod:`scripts.vv.benchmarks`)
------------------------------------------------------
* unobstructed channel: total ME force over both walls ``== g rho H Lx``
  (momentum balance, relative error < 1 %) and the velocity profile
  ``== g/(2 nu) y (H - y)`` (plane Poiseuille, L2 error < 2 %);
* the drag of the obstruction set is the QoI of the GCI study.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .cases import ChannelCase, solid_mask

__all__ = ["LBMSolver", "LBMResult", "run_lbm"]

_W = np.array([4.0 / 9.0] + [1.0 / 9.0] * 4 + [1.0 / 36.0] * 4)
_CX = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1])
_CY = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1])
_OPP = np.array([0, 3, 4, 1, 2, 7, 8, 5, 6])


def _equilibrium(rho: np.ndarray, ux: np.ndarray, uy: np.ndarray) -> np.ndarray:
    cu = 3.0 * (_CX[:, None, None] * ux[None] + _CY[:, None, None] * uy[None])
    usq = 1.5 * (ux * ux + uy * uy)
    return rho[None] * _W[:, None, None] * (1.0 + cu + 0.5 * cu * cu - usq[None])


@dataclass
class LBMResult:
    name: str
    c_d: float
    drag: float                 # total streamwise force on the obstructions
    u_bar: float                # measured bulk velocity
    re: float                   # measured Re = u_bar * d_ref / nu
    tau: float
    steps: int
    nx: int
    ny: int
    mass_drift: float
    mach: float
    me_drag_diagnostic: float
    cd_history: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "c_d": self.c_d,
            "drag": self.drag,
            "u_bar": self.u_bar,
            "re": self.re,
            "tau": self.tau,
            "steps": self.steps,
            "nx": self.nx,
            "ny": self.ny,
            "mass_drift": self.mass_drift,
            "mach": self.mach,
            "me_drag_diagnostic": self.me_drag_diagnostic,
        }


class LBMSolver:
    """SRT D2Q9 channel solver on a staircase geometry."""

    def __init__(self, case: ChannelCase, nx: int, ny: int, *, walls: bool = True):
        self.case = case
        self.nx = int(nx)
        self.ny = int(ny)
        self.solid = solid_mask(case, self.nx, self.ny, walls=walls)
        self.fluid = ~self.solid
        #: bodies only: the staircase wall rows are *not* part of the drag body
        self.body = self.solid.copy()
        if walls:
            self.body[0, :] = False
            self.body[-1, :] = False
        self.nf = float(self.fluid.sum())
        self.nu = case.nu
        self.tau = 3.0 * self.nu + 0.5
        if not (0.5 < self.tau < 1.0):
            raise ValueError(
                f"tau={self.tau:.4f} outside the stable BGK window (0.5, 1); "
                "refine the grid or change Re"
            )
        self.g = case.body_force()
        self.frontal_area = case.frontal_area
        self.omega = 1.0 / self.tau
        self.force_coeff = 1.0 - 0.5 * self.omega
        # boundary links: fluid node x whose neighbour x + c_k is solid
        self.links: List[Tuple[int, np.ndarray, np.ndarray, float]] = []
        self._stream_masks: List[Tuple[int, np.ndarray]] = []
        ones = np.ones_like(self.solid)
        for k in range(1, 9):
            back = np.roll(np.roll(self.solid, _CY[k], axis=0), _CX[k], axis=1)
            mask = self.fluid & back
            if mask.any():
                jj, ii = np.nonzero(mask)
                self.links.append((k, jj, ii, float(_CX[k])))
            src_fluid = np.roll(np.roll(self.fluid, _CY[k], axis=0), _CX[k], axis=1)
            wall_up = np.roll(np.roll(self.solid, _CY[k], axis=0), _CX[k], axis=1)
            self._stream_masks.append((k, (self.fluid & src_fluid,
                                           self.fluid & ~src_fluid & wall_up)))
        self.rho = np.ones((self.ny, self.nx))
        self.f = _equilibrium(self.rho, np.zeros_like(self.rho), np.zeros_like(self.rho))
        self.last_drag = 0.0
        self.iterations = 0

    # -- initialisation -------------------------------------------------
    def initialise_poiseuille(self) -> None:
        """Start from the analytic plane-Poiseuille profile."""
        ny = self.ny
        j = np.arange(ny)[:, None] + 0.5
        h_w = ny - 2.0            # wall-to-wall height in lattice units
        y = j - 1.0               # distance from the bottom wall (=0 on the wall)
        u = self.g / (2.0 * self.nu) * (y * (h_w - y))
        u = np.broadcast_to(u, (ny, self.nx)).copy()
        u[~self.fluid] = 0.0
        self.f = _equilibrium(np.ones((ny, self.nx)), u, np.zeros_like(u))

    def initialise_from_cells(self, u_cell: np.ndarray, v_cell: np.ndarray) -> None:
        """Start from a given cell-centred velocity field (grid sequencing)."""
        if u_cell.shape != (self.ny, self.nx):
            raise ValueError("field shape does not match the solver grid")
        u = np.where(self.fluid, u_cell, 0.0)
        v = np.where(self.fluid, v_cell, 0.0)
        self.f = _equilibrium(np.ones((self.ny, self.nx)), u, v)

    # -- one time step --------------------------------------------------
    def step(self) -> float:
        """Advance one lattice step and return the ME drag on the obstructions."""
        f = self.f
        rho = f.sum(axis=0)
        rho_s = np.where(self.fluid, rho, 1.0)
        ux = (f * _CX[:, None, None]).sum(axis=0) / rho_s
        uy = (f * _CY[:, None, None]).sum(axis=0) / rho_s
        fx = np.where(self.fluid, self.g * rho, 0.0)
        ux_g = ux + 0.5 * fx / rho_s
        uy_g = uy
        feq = _equilibrium(rho_s, ux_g, uy_g)

        ciF = _CX[:, None, None] * fx[None]
        ciU = _CX[:, None, None] * ux_g[None] + _CY[:, None, None] * uy_g[None]
        uF = ux_g * fx
        src = self.force_coeff * _W[:, None, None] * (3.0 * (ciF - uF[None]) + 9.0 * ciU * ciF)
        f_post = f - (f - feq) * self.omega + src
        f_post[:, self.solid] = 0.0

        drag = 0.0
        for k, jj, ii, cx in self.links:
            if cx == 0.0:
                continue
            drag += 2.0 * cx * float(f_post[k][jj, ii].sum())
        self.last_drag = drag

        f_new = np.zeros_like(f)
        for k, (dst_fluid, dst_wall) in self._stream_masks:
            g_shift = np.roll(np.roll(f_post[k], _CY[k], axis=0), _CX[k], axis=1)
            val = np.where(dst_fluid, g_shift, 0.0)
            val = np.where(dst_wall, f_post[_OPP[k]], val)
            f_new[k] = val
        f_new[0] = f_post[0]
        self.f = f_new
        self.iterations += 1
        return drag

    # -- diagnostics ----------------------------------------------------
    def moments(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Macroscopic moments including the Guo half-force velocity shift."""
        f = self.f
        rho = f.sum(axis=0)
        rho_s = np.where(self.fluid, rho, 1.0)
        ux = (f * _CX[:, None, None]).sum(axis=0) / rho_s + 0.5 * self.g
        uy = (f * _CY[:, None, None]).sum(axis=0) / rho_s
        ux = np.where(self.fluid, ux, 0.0)
        uy = np.where(self.fluid, uy, 0.0)
        return rho_s, ux, uy

    def bulk_velocity(self) -> float:
        _rho, ux, _uy = self.moments()
        return float(ux[self.fluid].sum() / self.nf)

    def drag(self) -> float:
        """Drag on the obstructions (force convention: +x accelerates the body)."""
        return self.last_drag

    def wall_force(self) -> float:
        """Total ME force on the channel walls (momentum-balance check)."""
        total = 0.0
        f = self.f
        rho = f.sum(axis=0)
        rho_s = np.where(self.fluid, rho, 1.0)
        ux = (f * _CX[:, None, None]).sum(axis=0) / rho_s
        uy = (f * _CY[:, None, None]).sum(axis=0) / rho_s
        fx = np.where(self.fluid, self.g * rho, 0.0)
        ux_g = ux + 0.5 * fx / rho_s
        feq = _equilibrium(rho_s, ux_g, uy)
        ciF = _CX[:, None, None] * fx[None]
        ciU = _CX[:, None, None] * ux_g[None] + _CY[:, None, None] * uy[None]
        uF = ux_g * fx
        src = self.force_coeff * _W[:, None, None] * (3.0 * (ciF - uF[None]) + 9.0 * ciU * ciF)
        f_post = f - (f - feq) * self.omega + src
        for k, jj, ii, cx in self.links:
            if cx == 0.0:
                continue
            total += 2.0 * cx * float(f_post[k][jj, ii].sum())
        return total


def run_lbm(
    case: ChannelCase,
    nx: int,
    ny: int,
    *,
    max_steps: int = 200_000,
    window: int = 200,
    tol: float = 1e-7,
    walls: bool = True,
    keep_history: bool = False,
) -> LBMResult:
    """March the LBM to statistical steady state and return the drag QoI.

    Convergence is declared when the drag coefficient changes by less than
    ``tol`` (relative) between two consecutive averages over ``window`` steps.
    The step count is a deterministic function of the data, so runs are exactly
    reproducible.
    """
    solver = LBMSolver(case, nx, ny, walls=walls)
    solver.initialise_poiseuille()
    rho0 = float(solver.moments()[0][solver.fluid].mean())

    hist: List[float] = []
    prev: Optional[float] = None
    steps = 0
    for step in range(1, max_steps + 1):
        solver.step()
        steps = step
        if step % window == 0:
            u_bar_w = solver.bulk_velocity()
            cd = (abs(solver.last_drag) / (0.5 * u_bar_w ** 2 * solver.frontal_area)
                  if solver.frontal_area else u_bar_w)
            hist.append(cd)
            if prev is not None and abs(cd - prev) <= tol * abs(prev):
                break
            prev = cd

    u_bar = solver.bulk_velocity()
    rho, ux, uy = solver.moments()
    # the QoI is measured with the shared surface-integration extractor so that
    # the FVM and the LBM define the drag identically; the raw momentum-exchange
    # sum is kept only as a diagnostic (it carries the equilibrium momentum flux
    # of the flow through the wall, which does not vanish for a straight wall)
    from .force import surface_force
    drag, _fy = surface_force(rho / 3.0, ux, uy, solver.body, 1.0, case.nu)
    drag = abs(drag)
    return LBMResult(
        name=case.name,
        c_d=drag / (0.5 * u_bar ** 2 * solver.frontal_area) if solver.frontal_area else float("nan"),
        drag=drag,
        u_bar=u_bar,
        re=u_bar * case.d_ref / case.nu,
        tau=solver.tau,
        steps=steps,
        nx=nx,
        ny=ny,
        mass_drift=abs(float(rho[solver.fluid].mean()) - rho0) / rho0,
        mach=float(np.sqrt((ux[solver.fluid] ** 2).max()) / math.sqrt(1.0 / 3.0)),
        me_drag_diagnostic=abs(solver.drag()),
        cd_history=hist if keep_history else [],
    )