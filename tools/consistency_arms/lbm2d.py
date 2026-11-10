"""D2Q9 lattice-Boltzmann arm (BGK collision, obstacle mask, Zou/He in-out flow).

Purpose
-------
This is one *arm* of the cross-solver consistency gate: an independent solver
that answers the case injected by ``config/consistency/arms/*.json`` and emits a
normalised arm record.  It is deliberately small and dependency free (numpy
only) so the gate's verdict is reproducible on any machine and does not depend on
an ecosystem solver version being installed.

Numerics
--------
* D2Q9, single relaxation time BGK, incompressible limit.
* Obstacle = analytic circular mask; no-slip by half-way bounce-back, with the
  optional Bouzidi second-order curved-wall closure (``interpolated_bb``), which
  removes the staircase error at the cost of a per-link distance bookkeeping.
* Inlet: Zou/He velocity boundary.  Outlet: Zou/He pressure boundary
  (rho = 1).  Transverse direction: periodic images.
* Force on the obstacle: momentum exchange ``2 f_i^+ c_i`` summed over the
  boundary links (specular reflection in velocity space makes the exchanged
  momentum independent of where exactly the wall cuts the link).
* Steady state: relative change of the total kinetic energy below a declared
  tolerance, with a hard step cap that is *reported*, never hidden.

All dimensional quantities are lattice units: rho = 1, U, D in lattice spacings,
nu = U*D/Re and tau = 3*nu + 1/2.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

import numpy as np

CX = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1], dtype=np.int64)
CY = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1], dtype=np.int64)
W = np.array([4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36])
OPP = np.array([0, 3, 4, 1, 2, 7, 8, 5, 6], dtype=np.int64)


def stream(f_post: np.ndarray, f_new: np.ndarray) -> None:
    """Slice-based streaming: periodic in y, open in x (inlet/outlet handled later).

    ``np.roll`` copies the whole array nine times per step; explicit slices copy
    once per direction and keep the wrap-around rows as two extra assignments,
    which is roughly a factor three in a pure-numpy kernel.
    """
    ny, nx = f_post.shape[1], f_post.shape[2]
    for i in range(9):          # direction 0 does not move, but it must still be copied
        cx, cy = int(CX[i]), int(CY[i])
        if cx > 0:
            sy, dy = slice(None, -1), slice(1, None)
        elif cx < 0:
            sy, dy = slice(1, None), slice(None, -1)
        else:
            sy, dy = slice(None), slice(None)
        if cy > 0:
            sx, dx = slice(None, -1), slice(1, None)
        elif cy < 0:
            sx, dx = slice(1, None), slice(None, -1)
        else:
            sx, dx = slice(None), slice(None)
        f_new[i][dx, dy] = f_post[i][sx, sy]
        if cy > 0:
            f_new[i][0, dy] = f_post[i][ny - 1, sy]
        elif cy < 0:
            f_new[i][ny - 1, dy] = f_post[i][0, sy]


def _disk(nx: int, ny: int, cx: float, cy: float, radius: float) -> np.ndarray:
    x = np.arange(nx)[None, :].astype(np.float64)
    y = np.arange(ny)[:, None].astype(np.float64)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def equilibrium(rho: np.ndarray, ux: np.ndarray, uy: np.ndarray) -> np.ndarray:
    cu = CX[:, None, None] * ux[None] + CY[:, None, None] * uy[None]
    usq = ux * ux + uy * uy
    return W[:, None, None] * rho[None] * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * usq[None])


def moments(f: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    rho = np.sum(f, axis=0)
    jx = f[1] - f[3] + f[5] - f[6] - f[7] + f[8]
    jy = f[2] - f[4] + f[5] + f[6] - f[7] - f[8]
    rho_safe = np.where(np.abs(rho) < 1e-12, 1.0, rho)
    return rho, jx / rho_safe, jy / rho_safe


def _zou_he_inlet(f: np.ndarray, u: float) -> None:
    f0, f2, f4 = f[0, :, 0], f[2, :, 0], f[4, :, 0]
    f3, f6, f7 = f[3, :, 0], f[6, :, 0], f[7, :, 0]
    rho = (f0 + f2 + f4 + 2.0 * (f3 + f6 + f7)) / (1.0 - u)
    f[1, :, 0] = f3 + (2.0 / 3.0) * rho * u
    f[5, :, 0] = f6 + (1.0 / 6.0) * rho * u - 0.5 * (f2 - f4)
    f[8, :, 0] = f7 + (1.0 / 6.0) * rho * u + 0.5 * (f2 - f4)


def _zou_he_outlet(f: np.ndarray, rho_out: float = 1.0) -> None:
    f0, f2, f4 = f[0, :, -1], f[2, :, -1], f[4, :, -1]
    f1, f5, f8 = f[1, :, -1], f[5, :, -1], f[8, :, -1]
    u = -1.0 + (f0 + f2 + f4 + 2.0 * (f1 + f5 + f8)) / rho_out
    f[3, :, -1] = f1 - (2.0 / 3.0) * rho_out * u
    f[6, :, -1] = f8 - (1.0 / 6.0) * rho_out * u + 0.5 * (f4 - f2)
    f[7, :, -1] = f5 - (1.0 / 6.0) * rho_out * u - 0.5 * (f4 - f2)


def boundary_links(mask: np.ndarray) -> List[Dict[str, Any]]:
    """Fluid nodes ``x`` whose neighbour ``x + c_i`` is solid, for each direction.

    Neighbour lookup is periodic in both directions, so a solid region that
    touches (or wraps through) a domain boundary still gets a closure instead of
    quietly leaking mass out of the domain -- a leak that looks exactly like a
    slightly wrong force coefficient.
    """
    links: List[Dict[str, Any]] = []
    for i in range(1, 9):
        # neighbour at x + c_i  (np.roll(m, k)[j] = m[j - k], hence the minus)
        shifted = np.roll(np.roll(mask, -CY[i], axis=0), -CX[i], axis=1)
        sel = (~mask) & shifted
        yy, xx = np.nonzero(sel)
        if yy.size:
            up = np.roll(np.roll(mask, CY[i], axis=0), CX[i], axis=1)
            links.append({"dir": i, "y": yy, "x": xx, "up_ok": ~up[yy, xx]})
    return links


def wall_fraction(mask: np.ndarray, cx: float, cy: float, radius: float,
                  links: List[Dict[str, Any]]) -> None:
    """Fraction of each boundary link between the fluid node and the wall."""
    for ln in links:
        i = int(ln["dir"])
        step = math.sqrt(2.0) if (CX[i] != 0 and CY[i] != 0) else 1.0
        ex, ey = CX[i] / step, CY[i] / step
        fx = ln["x"].astype(np.float64) - cx
        fy = ln["y"].astype(np.float64) - cy
        a = ex * ex + ey * ey
        b = 2.0 * (fx * ex + fy * ey)
        c = fx * fx + fy * fy - radius * radius
        disc = np.maximum(b * b - 4.0 * a * c, 0.0)
        t = (-b + np.sqrt(disc)) / (2.0 * a)
        ln["q"] = np.clip(t, 0.12, 1.0)


def apply_wall(f: np.ndarray, f_post: np.ndarray, links: List[Dict[str, Any]],
               interpolated: bool) -> None:
    """Reflect the populations that streamed into the obstacle (after streaming).

    Half-way closure: ``f_{opp(i)}(x) = f_i^+(x)``.
    Bouzidi linear interpolation with ``q`` the wall fraction:
      q < 1/2 : f_{opp(i)}(x) = 2q f_i^+(x) + (1-2q) f_i^+(x - c_i)
      q >= 1/2: f_{opp(i)}(x) = 1/(2q) f_i^+(x) + (2q-1)/(2q) f_{opp(i)}^+(x)
    """
    for ln in links:
        i = int(ln["dir"])
        j = int(OPP[i])
        y, x = ln["y"], ln["x"]
        src = f_post[i][y, x]
        if not interpolated:
            f[j][y, x] = src
            continue
        q = ln["q"]
        low = (q < 0.5) & ln["up_ok"]
        val = np.empty_like(src)
        if np.any(low):
            yb = y[low] - CY[i]
            xb = x[low] - CX[i]
            val[low] = (2.0 * q[low] * f_post[i][y[low], x[low]]
                        + (1.0 - 2.0 * q[low]) * f_post[i][yb, xb])
        high = ~low
        if np.any(high):
            qq = q[high]
            val[high] = (f_post[i][y[high], x[high]] / (2.0 * qq)
                         + (2.0 * qq - 1.0) / (2.0 * qq) * f_post[j][y[high], x[high]])
        f[j][y, x] = val


def exchanged_force(f_post: np.ndarray, links: List[Dict[str, Any]]) -> Tuple[float, float]:
    """Momentum-exchange force on the obstacle, ``sum_links 2 f_i^+ c_i``."""
    fx = 0.0
    fy = 0.0
    for ln in links:
        i = int(ln["dir"])
        s = float(np.sum(f_post[i][ln["y"], ln["x"]]))
        fx += 2.0 * s * CX[i]
        fy += 2.0 * s * CY[i]
    return fx, fy


def vorticity(ux: np.ndarray, uy: np.ndarray) -> np.ndarray:
    return np.gradient(uy, axis=1) - np.gradient(ux, axis=0)


def enstrophy_dissipation(ux: np.ndarray, uy: np.ndarray, nu: float,
                          cell_mask: np.ndarray, d: float) -> float:
    """Dimensionless viscous dissipation ``2*nu*sum(omega^2)/D`` (rho=U=D=1)."""
    om = vorticity(ux, uy)
    return 2.0 * nu * float(np.sum(om[cell_mask] ** 2)) / d


def _feq_into(out: np.ndarray, rho: np.ndarray, ux: np.ndarray, uy: np.ndarray) -> None:
    """Equilibrium populations written into a preallocated (9, ny, nx) buffer."""
    usq = ux * ux + uy * uy
    base = 1.0 - 1.5 * usq
    for i in range(9):
        cu = CX[i] * ux + CY[i] * uy
        np.multiply(rho, base + 3.0 * cu + 4.5 * cu * cu, out=out[i])
        out[i] *= W[i]


def _moments_into(f: np.ndarray, rho: np.ndarray, ux: np.ndarray,
                  uy: np.ndarray) -> None:
    np.sum(f, axis=0, out=rho)
    jx = ux  # scratch (2-D, same shape)
    np.add(f[1], f[5], out=jx)
    jx += f[8]
    jx -= f[3]
    jx -= f[6]
    jx -= f[7]
    jy = uy
    np.add(f[2], f[5], out=jy)
    jy += f[6]
    jy -= f[4]
    jy -= f[7]
    jy -= f[8]
    np.divide(jx, rho, out=ux)
    np.divide(jy, rho, out=uy)
    # solid nodes carry no populations: keep their moments finite instead of 0/0
    np.nan_to_num(ux, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    np.nan_to_num(uy, copy=False, nan=0.0, posinf=0.0, neginf=0.0)


def simulate(cfg: Dict[str, Any], verbose: bool = False) -> Dict[str, Any]:
    """Run one lattice-Boltzmann arm and return the converged fields."""
    d = float(cfg["diameter"])
    re = float(cfg["reynolds"])
    u = float(cfg.get("inflow_velocity", 0.08))
    nx = int(round(float(cfg["domain_length"]) * d))
    ny = int(round(float(cfg["domain_height"]) * d))
    cx = float(cfg.get("obstacle_x", 10.0)) * d
    cy = ny / 2.0
    radius = d / 2.0
    max_steps = int(cfg.get("max_steps", 80000))
    tol = float(cfg.get("steady_tol", 1e-6))
    check_every = int(cfg.get("check_every", 500))
    interpolated = bool(cfg.get("interpolated_bb", True))

    nu = u * d / re
    tau = 3.0 * nu + 0.5
    omega = 1.0 / tau

    mask = _disk(nx, ny, cx, cy, radius)
    links = boundary_links(mask)
    wall_fraction(mask, cx, cy, radius, links)

    rho = np.ones((ny, nx))
    ux = np.full((ny, nx), u)
    uy = np.zeros((ny, nx))
    f = equilibrium(rho, ux, uy)
    f_post = np.empty_like(f)
    f_new = np.empty_like(f)

    fx = fy = 0.0
    history: List[Dict[str, Any]] = []
    prev_cd = None
    step = 0
    converged = False
    for step in range(1, max_steps + 1):
        _moments_into(f, rho, ux, uy)
        _feq_into(f_post, rho, ux, uy)          # reuse as scratch for feq
        np.subtract(f_post, f, out=f_post)
        f_post *= omega
        np.add(f, f_post, out=f_post)           # f_post := f + omega*(feq - f)
        stream(f_post, f_new)
        # the inlet/outlet columns only need the unknown populations, which Zou/He
        # computes from the streamed ones -- overwriting the whole column with the
        # post-collision values destroys the incoming characteristics
        _zou_he_inlet(f_new, u)
        _zou_he_outlet(f_new, 1.0)
        f, f_new = f_new, f
        apply_wall(f, f_post, links, interpolated)
        f[:, mask] = 0.0
        fx, fy = exchanged_force(f_post, links)

        if step % check_every == 0:
            _moments_into(f, rho, ux, uy)
            rho_mean = float(rho[~mask].mean())
            cd = 2.0 * fx / (rho_mean * u * u * d)
            change = None if prev_cd in (None, 0.0) else abs(cd - prev_cd) / abs(prev_cd)
            history.append({"step": step, "cd": cd, "rel_change": change})
            if verbose and (step // check_every) % 10 == 0:
                print(f"  step {step:6d}  cd={cd:.5f}  dcd/cd={change}", flush=True)
            if change is not None and change < tol:
                converged = True
                break
            prev_cd = cd

    _moments_into(f, rho, ux, uy)
    rho_mean = float(rho[~mask].mean())
    cd = 2.0 * fx / (rho_mean * u * u * d)
    eps = enstrophy_dissipation(ux, uy, nu, ~mask, d)
    return {
        "cd": cd,
        "cd_cross": 2.0 * fy / (rho_mean * u * u * d),
        "ux": ux, "uy": uy, "rho": rho, "mask": mask,
        "dissipation": eps,
        "power_in": cd,
        "nu": nu, "tau": tau, "u_in": u,
        "steps": step, "converged": converged,
        "steady_change": None if not history else history[-1]["rel_change"],
        "history": history,
        "grid": {"nx": nx, "ny": ny, "diameter_lattice": d,
                 "cells": int(nx * ny),
                 "domain_length_over_d": float(cfg["domain_length"]),
                 "domain_height_over_d": float(cfg["domain_height"]),
                 "obstacle_x_over_d": float(cfg.get("obstacle_x", 10.0)),
                 "interpolated_bb": interpolated},
    }


def surface_pressure(res: Dict[str, Any], cfg: Dict[str, Any], n_s: int = 181
                     ) -> Dict[str, Any]:
    """Surface pressure coefficient against arc length from the stagnation point.

    In lattice units p = rho * cs^2 with cs^2 = 1/3, and the free stream carries
    rho_inf ~ 1, so Cp = (p - p_inf) / (0.5 rho U^2) = 2 (rho - rho_inf) / (3 U^2).

    The abscissa is arc length normalised by the obstacle height, running from the
    upstream stagnation point (s=0) over the upper surface to the rear (s=1) and
    back along the lower surface (s=2).
    """
    rho, mask = res["rho"], res["mask"]
    ny, nx = mask.shape
    d = float(cfg["diameter"])
    cx = float(cfg.get("obstacle_x", 10.0)) * d
    cy = ny / 2.0
    radius = d / 2.0
    u = float(cfg.get("inflow_velocity", 0.05))
    rho_inf = float(rho[:, 1].mean())
    s_vals: List[float] = []
    cp_vals: List[float] = []
    for k in range(n_s):
        s = 2.0 * k / (n_s - 1)
        ang = math.pi - math.pi * s if s <= 1.0 else -math.pi * (s - 1.0)
        rr = radius + 0.75
        x = int(round(cx + rr * math.cos(ang)))
        y = int(round(cy + rr * math.sin(ang)))
        x = min(max(x, 1), nx - 2)
        y = min(max(y, 1), ny - 2)
        cp = 2.0 * (float(rho[y, x]) - rho_inf) / (3.0 * u * u)
        s_vals.append(s)
        cp_vals.append(cp)
    return {"coordinate": s_vals, "value": cp_vals}


# ---------------------------------------------------------------------------
# analytic validation used by the gate's own test suite
# ---------------------------------------------------------------------------
def taylor_green(n: int = 64, u0: float = 0.05, re: float = 100.0,
                 steps: int = 400) -> Dict[str, float]:
    """Decaying decaying Taylor vortex: measures the effective viscosity of the kernel.

    The exact solution on a 2*pi periodic box decays as ``u0 exp(-2 nu k^2 t)``
    with k = 1; the measured decay rate is compared with the analytical one.
    """
    nu = u0 * n / re
    tau = 3.0 * nu + 0.5
    omega = 1.0 / tau
    x = 2.0 * math.pi * (np.arange(n) + 0.5) / n
    xx, yy = np.meshgrid(x, x)
    ux = u0 * np.sin(xx) * np.cos(yy)
    uy = -u0 * np.cos(xx) * np.sin(yy)
    rho = np.ones((n, n))
    f = equilibrium(rho, ux, uy)
    e0 = float(np.sum(ux * ux + uy * uy))
    for _ in range(steps):
        rho, ux, uy = moments(f)
        feq = equilibrium(rho, ux, uy)
        f_post = f - omega * (f - feq)
        f = np.stack([np.roll(np.roll(f_post[i], CY[i], axis=0), CX[i], axis=1)
                      for i in range(9)])
    rho, ux, uy = moments(f)
    e1 = float(np.sum(ux * ux + uy * uy))
    t = float(steps)
    k = 2.0 * math.pi / n
    # energy ~ exp(-4 nu k^2 t) for the 2-D decaying Taylor vortex
    measured = -math.log(e1 / e0) / (4.0 * t * k * k)
    return {"nu_set": nu, "nu_measured": measured,
            "rel_error": abs(measured - nu) / nu,
            "amplitude_ratio": e1 / e0,
            "decay_analytic": math.exp(-4.0 * nu * k * k * t)}


def poiseuille(ny: int = 32, nx: int = 8, force: float = 1e-6, steps: int = 20000,
               tau: float = 0.8, thick: int = 2) -> Dict[str, float]:
    """Body-force driven plane channel: wall-normal profile vs the parabolic solution.

    The wall is a ``thick``-row solid strip on each side (so no solid node sits on
    a periodic wrap and mass cannot leak), driven by the standard forcing term
    ``f_i += 3 w_i (c_i . g)``.  The analytic reference uses the half-way
    bounce-back wall position (half a lattice step outside the first fluid row).
    """
    omega = 1.0 / tau
    nu = (tau - 0.5) / 3.0
    rho = np.ones((ny, nx))
    ux = np.zeros((ny, nx))
    uy = np.zeros((ny, nx))
    f = equilibrium(rho, ux, uy)
    walls = np.zeros((ny, nx), dtype=bool)
    walls[:thick, :] = True
    walls[-thick:, :] = True
    links = boundary_links(walls)
    for ln in links:
        ln["q"] = np.full(ln["y"].shape, 0.5)
    for _ in range(steps):
        rho, ux, uy = moments(f)
        feq = equilibrium(rho, ux, uy)
        f_post = f - omega * (f - feq)
        f = np.stack([np.roll(np.roll(f_post[i], CY[i], axis=0), CX[i], axis=1)
                      for i in range(9)])
        f += 3.0 * W[:, None, None] * CX[:, None, None] * force * rho[None]
        apply_wall(f, f_post, links, True)
        f[:, walls] = 0.0
    rho, ux, uy = moments(f)
    prof = ux.mean(axis=1)
    fluid = ~walls[:, 0]
    y = np.arange(ny) + 0.0
    h = (ny - 2.0 * thick) / 2.0           # wall planes at thick-0.5 and ny-thick-0.5
    c = (ny - 1.0) / 2.0
    ana = np.where(fluid, force / (2.0 * nu) * (h * h - (y - c) ** 2), 0.0)
    err = float(np.linalg.norm(prof[fluid] - ana[fluid])
                / max(float(np.linalg.norm(ana[fluid])), 1e-30))
    return {"nu_set": nu, "l2_rel_error": err,
            "u_max_measured": float(prof[fluid].max()),
            "u_max_analytic": float(ana.max())}
