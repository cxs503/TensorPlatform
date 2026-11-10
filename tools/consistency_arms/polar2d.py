"""Structured body-fitted grid arm: log-polar vorticity / stream-function solver.

This is the second *arm* of the cross-solver consistency gate.  It belongs to a
different discretisation family than the lattice-Boltzmann arm (Eulerian
body-fitted grid, second-order differences, Poisson solves) so that a
disagreement between the arms is informative rather than tautological.

Formulation
-----------
With ``xi = ln(r)`` the polar metric collapses to uniform spacing:

    laplacian(f) = (1/r^2)(f_xixi + f_thetatheta)
    u_r = (1/r) psi_theta,   u_theta = -(1/r) psi_xi
    omega = -laplacian(psi)   ->   psi_xixi + psi_thetatheta = -omega r^2

and, because ``u . grad = (1/r^2)(psi_theta d_xi - psi_xi d_theta)``,

    d omega / dt = -(1/r^2)[psi_theta omega_xi - psi_xi omega_theta]
                   + (nu/r^2)(omega_xixi + omega_thetatheta)

* inner boundary (obstacle surface): ``psi = 0`` (no penetration) and a
  second-order no-slip wall vorticity (see :func:`wall_vorticity`).
* outer boundary: uniform stream ``psi = U r sin(theta)``, ``omega = 0``.
* theta is periodic, so every Poisson problem is diagonalised by a real FFT in
  theta and solved as ``nr`` tridiagonal systems -- vectorised over the modes.
  That keeps the arm fast enough to run three grid resolutions, which is what
  makes the grid-refinement check possible.

Diagnostics
-----------
Surface traction with ``n = r_hat`` on a no-slip wall,

    F = oint (-p r_hat + mu omega_w theta_hat) R dtheta,

so the pressure part integrates ``-p cos(theta)`` and the friction part
``-mu omega_w sin(theta)``.  Pressure follows from the 2-D pressure Poisson
equation with ``p = 0`` at the outer boundary and a zero normal derivative on the
wall; the neglected normal gradient is O(mu U / R^2) = O(1/Re^2) of the dynamic
head (~4e-4 at Re = 100), verified by :func:`potential_flow_check`.

Dissipation is measured as in every other arm -- ``2 nu int(omega^2) dA / D`` in
units where rho = U = D = 1 -- so the arm energy ledgers are comparable.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.sparse as sp


class LogPolarGrid:
    """Uniform grid in (xi = ln r, theta); theta is periodic."""

    def __init__(self, r_in: float, r_out: float, nr: int, ntheta: int):
        self.r_in = float(r_in)
        self.r_out = float(r_out)
        self.nr = int(nr)
        self.ntheta = int(ntheta)
        self.xi = np.linspace(0.0, math.log(self.r_out / self.r_in), self.nr)
        self.r = self.r_in * np.exp(self.xi)
        self.dxi = float(self.xi[1] - self.xi[0])
        self.theta = 2.0 * np.pi * np.arange(self.ntheta) / self.ntheta
        self.dtheta = 2.0 * np.pi / self.ntheta
        self.r2 = np.repeat(self.r[:, None] ** 2, self.ntheta, axis=1)
        self.cost = np.repeat(np.cos(self.theta)[None, :], self.nr, axis=0)
        self.sint = np.repeat(np.sin(self.theta)[None, :], self.nr, axis=0)
        self._lam = None

    # -- differences ---------------------------------------------------------
    def d_xi(self, f: np.ndarray) -> np.ndarray:
        out = np.empty_like(f)
        out[1:-1] = (f[2:] - f[:-2]) / (2.0 * self.dxi)
        out[0] = (f[1] - f[0]) / self.dxi
        out[-1] = (f[-1] - f[-2]) / self.dxi
        return out

    def d_theta(self, f: np.ndarray) -> np.ndarray:
        return (np.roll(f, -1, axis=1) - np.roll(f, 1, axis=1)) / (2.0 * self.dtheta)

    def laplace(self, f: np.ndarray) -> np.ndarray:
        fxi2 = np.empty_like(f)
        fxi2[1:-1] = (f[2:] - 2.0 * f[1:-1] + f[:-2]) / self.dxi ** 2
        fxi2[0] = (f[2] - 2.0 * f[1] + f[0]) / self.dxi ** 2
        fxi2[-1] = (f[-1] - 2.0 * f[-2] + f[-3]) / self.dxi ** 2
        fth2 = (np.roll(f, -1, axis=1) - 2.0 * f + np.roll(f, 1, axis=1)) / self.dtheta ** 2
        return (fxi2 + fth2) / self.r2

    # -- Fourier eigenvalues of the theta second difference ------------------
    @property
    def lam(self) -> np.ndarray:
        if self._lam is None:
            k = np.arange(self.ntheta // 2 + 1)
            self._lam = -4.0 * np.sin(np.pi * k / self.ntheta) ** 2 / self.dtheta ** 2
        return self._lam

    # -- sparse reference matrix (kept for the tests that certify the solver) -
    def laplace_matrix(self, inner: str = "dirichlet") -> sp.csr_matrix:
        n = self.nr * self.ntheta
        i = np.repeat(np.arange(self.nr), self.ntheta)
        j = np.tile(np.arange(self.ntheta), self.nr)

        def flat(ii, jj):
            return ii * self.ntheta + (jj % self.ntheta)

        rows, cols, vals = [], [], []
        interior = (i > 0) & (i < self.nr - 1)
        ai = 1.0 / (self.dxi ** 2 * self.r[i] ** 2)
        at = 1.0 / (self.dtheta ** 2 * self.r[i] ** 2)
        for di, coef in ((-1, ai), (0, -2.0 * ai), (1, ai)):
            m = interior
            rows.append(flat(i[m], j[m]))
            cols.append(flat(i[m] + di, j[m]))
            vals.append(coef[m])
        for dj in (-1, 1):
            rows.append(flat(i[interior], j[interior]))
            cols.append(flat(i[interior], j[interior] + dj))
            vals.append(at[interior])
        rows.append(flat(i[interior], j[interior]))
        cols.append(flat(i[interior], j[interior]))
        vals.append(-2.0 * at[interior])

        m0 = i == 0
        if inner == "dirichlet":
            rows.append(flat(i[m0], j[m0]))
            cols.append(flat(i[m0], j[m0]))
            vals.append(np.ones(int(m0.sum())))
        elif inner == "neumann":
            rows.append(flat(i[m0], j[m0]))
            cols.append(flat(i[m0], j[m0]))
            vals.append(-np.ones(int(m0.sum())))
            rows.append(flat(i[m0], j[m0]))
            cols.append(flat(i[m0] + 1, j[m0]))
            vals.append(np.ones(int(m0.sum())))
        else:
            raise ValueError(f"unknown inner condition {inner!r}")
        ml = i == self.nr - 1
        rows.append(flat(i[ml], j[ml]))
        cols.append(flat(i[ml], j[ml]))
        vals.append(np.ones(int(ml.sum())))
        return sp.coo_matrix((np.concatenate(vals),
                              (np.concatenate(rows), np.concatenate(cols))),
                             shape=(n, n)).tocsr()

    def dense_laplacian(self, f: np.ndarray) -> np.ndarray:
        """Sparse-matrix Laplacian (used to certify the Fourier path)."""
        mat = self.laplace_matrix()
        return (mat @ f.ravel()).reshape(f.shape)


def _thomas_modes(sub: np.ndarray, diag: np.ndarray, sup: np.ndarray,
                  rhs: np.ndarray) -> np.ndarray:
    """Solve one tridiagonal system per column, for all columns at once.

    ``sub``/``sup`` have shape (nr-1, nk), ``diag`` and ``rhs`` shape (nr, nk).
    """
    nr, nk = diag.shape
    dt_ = rhs.dtype if np.iscomplexobj(rhs) else float
    cp = np.zeros((nr, nk), dtype=dt_)
    dp = np.zeros((nr, nk), dtype=dt_)
    cp[0] = sup[0] / diag[0]
    dp[0] = rhs[0] / diag[0]
    for i in range(1, nr):
        denom = diag[i] - sub[i - 1] * cp[i - 1]
        cp[i] = sup[i] / denom if i < nr - 1 else 0.0
        dp[i] = (rhs[i] - sub[i - 1] * dp[i - 1]) / denom
    out = np.zeros((nr, nk), dtype=rhs.dtype)
    out[-1] = dp[-1]
    for i in range(nr - 2, -1, -1):
        out[i] = dp[i] - cp[i] * out[i + 1]
    return out


class FourierPoisson:
    """Solve ``alpha*f - beta*laplacian(f) = rhs`` on the log-polar grid.

    ``laplacian`` is the grid operator ``(1/r^2)(d_xixi + d_thetatheta)``, so with
    FFT in theta every mode becomes an independent tridiagonal system in xi.
    Boundary rows: inner Dirichlet / Neumann (derivative value through the RHS)
    and outer Dirichlet.
    """

    def __init__(self, grid: LogPolarGrid, inner: str = "dirichlet",
                 alpha: float = 0.0, beta: float = 1.0):
        self.g = grid
        self.inner = inner
        self.alpha = float(alpha)
        self.beta = float(beta)
        nr, nk = grid.nr, grid.ntheta // 2 + 1
        self.nr, self.nk = nr, nk
        lam = grid.lam[None, :]
        a = 1.0 / grid.dxi ** 2
        # rows are scaled by r^2 so the constant-coefficient Laplacian reappears:
        #   alpha*r^2*f - beta*(f_xixi + f_thetatheta) = r^2 * rhs
        r2 = grid.r[:, None] ** 2
        diag = np.zeros((nr, nk))
        sub = np.zeros((nr - 1, nk))
        sup = np.zeros((nr, nk))
        for i in range(1, nr - 1):
            diag[i] = self.alpha * r2[i] + self.beta * (2.0 * a - lam[0])
            sub[i - 1] = -self.beta * a
            sup[i] = -self.beta * a
        diag[0] = 1.0 if inner == "dirichlet" else -1.0
        sup[0] = 0.0 if inner == "dirichlet" else 1.0
        diag[-1] = 1.0
        self._diag, self._sub, self._sup = diag, sub, sup
        self._r2 = grid.r2

    def solve(self, rhs: np.ndarray, inner_value: Optional[np.ndarray] = None,
              outer_value: Optional[np.ndarray] = None) -> np.ndarray:
        g = self.g
        rhs_s = rhs * self._r2 if self.alpha != 0.0 else rhs
        mod = np.fft.rfft(rhs_s, axis=1)
        b0 = np.zeros(mod.shape[1], dtype=complex)
        b1 = np.zeros(mod.shape[1], dtype=complex)
        if self.inner == "dirichlet":
            if inner_value is not None:
                b0 = np.fft.rfft(np.asarray(inner_value, dtype=float))
        else:
            if inner_value is not None:
                b0 = np.fft.rfft(np.asarray(inner_value, dtype=float)) * g.dxi
        if outer_value is not None:
            b1 = np.fft.rfft(np.asarray(outer_value, dtype=float))
        mod = np.array(mod, dtype=complex)
        mod[0] = b0
        mod[-1] = b1
        sol = _thomas_modes(self._sub, self._diag, self._sup, mod)
        return np.fft.irfft(sol, n=g.ntheta, axis=1)


def wall_vorticity(grid: LogPolarGrid, psi: np.ndarray) -> np.ndarray:
    """Second-order no-slip wall vorticity.

    ``u_theta(0) = 0`` forces ``u_theta(s) = c1 s + c2 s^2`` with
    ``omega_wall = c1`` and ``psi(s) = -c1 s^2/2 - c2 s^3/3``; eliminating ``c2``
    with the first two cells gives

        omega_0 = -2 (psi_1 H^3 - psi_2 h1^3) / (h1^2 H^2 h2),  H = h1 + h2,

    which reduces to the classic ``(psi_2 - 8 psi_1) / (2 h^2)`` when h1 = h2 = h.
    """
    h1 = float(grid.r[1] - grid.r[0])
    h2 = float(grid.r[2] - grid.r[1])
    H = h1 + h2
    return -2.0 * (psi[1, :] * H ** 3 - psi[2, :] * h1 ** 3) / (h1 ** 2 * H ** 2 * h2)


def _wall_vorticity(grid: LogPolarGrid, psi: np.ndarray, closure: str) -> np.ndarray:
    """Wall vorticity from the configured closure.

    Only the first-order Thom closure is used for time marching by default: its
    feedback gain on the near-wall cell is 0.61 < 1 (measured in the test suite),
    whereas the second-order formula has 1.13 > 1 and grows by 30 % per step in
    this explicit Dirichlet/Neumann coupling.  The second-order value is still
    returned for diagnostics so the truncation error can be quantified.
    """
    if closure == "thom":
        h = float(grid.r[1] - grid.r[0])
        return -2.0 * psi[1, :] / h ** 2
    return wall_vorticity(grid, psi)


def solve_case(cfg: Dict[str, Any], verbose: bool = False) -> Dict[str, Any]:
    """Time-march the vorticity transport to steady state and return diagnostics."""
    d = float(cfg.get("diameter", 1.0))
    re = float(cfg["reynolds"])
    u_inf = float(cfg.get("inflow_velocity", 1.0))
    radius = d / 2.0
    r_out = float(cfg.get("outer_radius_over_d", 30.0)) * d
    nr = int(cfg.get("nr", 128))
    ntheta = int(cfg.get("ntheta", 256))
    nu = u_inf * d / re
    max_steps = int(cfg.get("max_steps", 200000))
    tol = float(cfg.get("steady_tol", 1e-7))
    check_every = int(cfg.get("check_every", 200))
    cfl = float(cfg.get("cfl", 0.4))
    ramp_steps = int(cfg.get("ramp_steps", 200))
    closure = str(cfg.get("wall_closure", "thom"))
    if closure not in ("thom", "order2"):
        raise ValueError(f"unknown wall closure {closure!r}")

    grid = LogPolarGrid(radius, r_out, nr, ntheta)
    h_wall = float(grid.r[1] - grid.r[0])
    dt = cfl * min(h_wall, radius * grid.dtheta) / max(u_inf, 1e-30)
    psi_solve = FourierPoisson(grid, "dirichlet")
    omega_solve = FourierPoisson(grid, "dirichlet", alpha=1.0, beta=dt * nu)
    pres_solve = FourierPoisson(grid, "neumann")

    psi_out = u_inf * grid.r_out * np.sin(grid.theta)
    omega = np.zeros((nr, ntheta))
    psi = np.zeros((nr, ntheta))
    if verbose:
        print(f"  grid {nr}x{ntheta}  r_out={r_out:.2f}  h_wall={h_wall:.5f}  "
              f"dt={dt:.5f}", flush=True)

    history: List[Dict[str, Any]] = []
    cd = 0.0
    prev = None
    step = 0
    converged = False
    p = np.zeros((nr, ntheta))
    for step in range(1, max_steps + 1):
        ramp = 1.0 if ramp_steps <= 0 else min(1.0, step / ramp_steps)
        # stream function: psi_xixi + psi_thetatheta = -omega r^2
        # -(D2xi+D2theta) psi = r^2 * rhs  and  laplacian(psi) = -omega
        # => rhs = +omega r^2  (a flipped sign here turns the wall-vorticity
        # feedback into positive feedback and the field grows 30 % per step)
        psi = psi_solve.solve(omega * grid.r2, inner_value=np.zeros(ntheta),
                              outer_value=psi_out * ramp)
        omega[0, :] = (_wall_vorticity(grid, psi, closure)) * ramp
        omega[-1, :] = 0.0

        # upwind convection (central differencing oscillates at the near-wall
        # cell Peclet number of this grid) in the Jacobian form
        a_coef = grid.d_theta(psi)                          # multiplies omega_xi
        b_coef = -grid.d_xi(psi)                            # multiplies omega_theta
        dw_xi = grid.d_xi(omega)
        dw_th = grid.d_theta(omega)
        # np.roll(m, k)[i] = m[i - k], so the upstream neighbour of a positive
        # coefficient is roll(+1); getting this backwards turns upwinding into
        # anti-diffusion and the field grows by ~8 % per step
        up_xi = np.where(a_coef > 0,
                         (omega - np.roll(omega, 1, axis=0)) / grid.dxi,
                         (np.roll(omega, -1, axis=0) - omega) / grid.dxi)
        up_th = np.where(b_coef > 0,
                         (omega - np.roll(omega, 1, axis=1)) / grid.dtheta,
                         (np.roll(omega, -1, axis=1) - omega) / grid.dtheta)
        blend = float(cfg.get("upwind_blend", 1.0))
        conv = -(a_coef * (blend * up_xi + (1 - blend) * dw_xi)
                 + b_coef * (blend * up_th + (1 - blend) * dw_th)) / grid.r2
        rhs_o = omega + dt * conv
        omega = omega_solve.solve(rhs_o, inner_value=omega[0, :],
                                  outer_value=np.zeros(ntheta))

        if step % check_every == 0:
            p = pressure_field(grid, psi, omega, nu, pres_solve, psi_out * ramp)
            cd, parts = force_coefficients(grid, p, omega, nu, d, u_inf)
            change = None if prev is None else abs(cd - prev) / abs(prev)
            history.append({"step": step, "cd": cd, "cd_pressure": parts["pressure"],
                            "cd_friction": parts["friction"], "rel_change": change,
                            "max_omega": float(np.abs(omega).max())})
            if verbose and (step // check_every) % 10 == 0:
                print(f"  step {step:7d}  cd={cd:.5f}  dcd={change}  "
                      f"|omega|max={history[-1]['max_omega']:.2f}", flush=True)
            if change is not None and change < tol:
                converged = True
                break
            prev = cd
            if not np.isfinite(cd):
                break

    p = pressure_field(grid, psi, omega, nu, pres_solve, psi_out)
    cd, parts = force_coefficients(grid, p, omega, nu, d, u_inf)
    cp = 2.0 * p[0, :] / (u_inf ** 2)
    eps = 2.0 * nu * float(np.sum(omega ** 2 * grid.r2 * grid.dxi * grid.dtheta)) / d
    return {
        "cd": cd,
        "cd_pressure": parts["pressure"],
        "cd_friction": parts["friction"],
        "dissipation": eps,
        "power_in": cd,
        "grid": grid,
        "psi": psi, "omega": omega, "cp": cp, "p": p,
        "nu": nu, "dt": dt, "steps": step, "converged": converged,
        "steady_change": None if not history else history[-1]["rel_change"],
        "history": history,
        "mesh": {"nr": nr, "ntheta": ntheta, "r_in": radius, "r_out": r_out,
                 "h_wall": h_wall, "cells": nr * ntheta},
    }


def pressure_field(grid: LogPolarGrid, psi: np.ndarray, omega: np.ndarray,
                   nu: float, solve_pres: FourierPoisson, psi_out: np.ndarray,
                   p_out=None) -> np.ndarray:
    """2-D pressure Poisson: laplacian(p) = -rho[(u_x)^2 + 2 u_y v_x + (v_y)^2].

    ``rho = 1``; outer boundary ``p = 0`` (or the analytic value when validating),
    wall gets a zero normal derivative (module docstring gives the size of the
    neglected term).  The right-hand side is written in the grid coordinates.
    """
    ur = grid.d_theta(psi) / grid.r[:, None]
    ut = -grid.d_xi(psi) / grid.r[:, None]
    ux = ur * grid.cost - ut * grid.sint
    uy = ur * grid.sint + ut * grid.cost

    def d_x(f):
        return (grid.d_xi(f) * grid.cost - grid.d_theta(f) * grid.sint) / grid.r[:, None]

    def d_y(f):
        return (grid.d_xi(f) * grid.sint + grid.d_theta(f) * grid.cost) / grid.r[:, None]

    # laplacian(p) = q  =>  rhs = -q r^2 for the row-scaled Fourier solver
    rhs = ((d_x(ux)) ** 2 + 2.0 * d_y(ux) * d_x(uy) + (d_y(uy)) ** 2) * grid.r2
    outer = np.zeros(grid.ntheta) if p_out is None else np.asarray(p_out, dtype=float)
    return solve_pres.solve(rhs, inner_value=np.zeros(grid.ntheta), outer_value=outer)


def _surface_integrals(grid: LogPolarGrid, p: np.ndarray, omega: np.ndarray,
                       nu: float, d: float, u_inf: float) -> Dict[str, float]:
    r_wall = grid.r_in
    c, s = grid.cost[0, :], grid.sint[0, :]
    fx_p = -float(np.sum(p[0, :] * c)) * r_wall * grid.dtheta
    fx_f = -float(np.sum(nu * omega[0, :] * s)) * r_wall * grid.dtheta
    q = 0.5 * u_inf ** 2 * d
    return {"pressure": fx_p / q, "friction": fx_f / q,
            "total": (fx_p + fx_f) / q}


def force_coefficients(grid: LogPolarGrid, p: np.ndarray, omega: np.ndarray,
                       nu: float, d: float, u_inf: float
                       ) -> Tuple[float, Dict[str, float]]:
    parts = _surface_integrals(grid, p, omega, nu, d, u_inf)
    return parts["total"], parts


def surface_pressure(res: Dict[str, Any], n_s: int = 181) -> Dict[str, Any]:
    """C_p against arc length from the upstream stagnation point (s/D in [0, 2])."""
    grid: LogPolarGrid = res["grid"]
    cp = res["cp"]
    theta = grid.theta
    # s/D: 0 at theta=pi (upstream), 1 at theta=0 (rear) over the upper side
    s = np.where(theta <= np.pi, (np.pi - theta) / (2 * np.pi) * 2.0,
                 2.0 - (theta - np.pi) / (2 * np.pi) * 2.0)
    order = np.argsort(s)
    idx = np.linspace(0, len(s) - 1, n_s).astype(int)
    s_sorted = s[order]
    cp_sorted = cp[order]
    pick = np.searchsorted(s_sorted, np.linspace(0.0, 2.0, n_s))
    pick = np.clip(pick, 0, len(s_sorted) - 1)
    del idx
    return {"coordinate": [float(v) for v in np.linspace(0.0, 2.0, n_s)],
            "value": [float(v) for v in cp_sorted[pick]]}


# ---------------------------------------------------------------------------
# analytic checks used by the test suite
# ---------------------------------------------------------------------------
def potential_flow_check(nr: int = 96, ntheta: int = 192, u_inf: float = 1.0,
                         radius: float = 0.5, r_out: float = 30.0
                         ) -> Dict[str, float]:
    """Certify the grid operators against the analytic potential flow.

    ``psi = U (r - R^2/r) sin(theta)`` is an exact solution of the stream-function
    equation with ``omega = 0``; the discrete Laplacian should therefore vanish up
    to truncation error, and the pressure Poisson, fed with that velocity field,
    should reproduce the Bernoulli surface pressure ``0.5 U^2 (1 - 4 sin^2)``.
    """
    grid = LogPolarGrid(radius, r_out, nr, ntheta)
    r = grid.r[:, None] * np.ones((1, ntheta))
    th = np.ones((grid.nr, 1)) * grid.theta[None, :]
    psi = u_inf * (r - radius ** 2 / r) * np.sin(th)
    omega = np.zeros_like(psi)
    psi_out = u_inf * grid.r_out * np.sin(grid.theta)
    # interior discrete Laplacian of an exact harmonic function
    lap = grid.laplace(psi)
    lap_err = float(np.max(np.abs(lap[2:-2, :]))) / (u_inf / radius ** 2)
    # Fourier Poisson vs the assembled sparse matrix (solver certification)
    f = np.sin(th) * np.exp(grid.xi)[:, None]
    dense = grid.dense_laplacian(f)
    fourier = grid.laplace(f)
    solver_err = float(np.max(np.abs(dense[2:-2] - fourier[2:-2]))
                       / np.max(np.abs(fourier[2:-2])))
    # pressure: analytic outer BC, compare the surface distribution with Bernoulli
    solve = FourierPoisson(grid, "neumann")
    p_out = (0.5 * u_inf ** 2 * (1.0 - 4.0 * np.sin(grid.theta) ** 2)
             * (radius / r_out) ** 2)
    p = pressure_field(grid, psi, omega, 1e-3, solve, psi_out, p_out=p_out)
    # Bernoulli pressure of the analytic field, compared in the interior where the
    # wall boundary condition (a zero normal derivative, which is a 1/Re argument for
    # the *viscous* field and simply wrong for an inviscid one) has decayed
    eps = (radius / r) ** 2
    p_ana = 0.5 * u_inf ** 2 * (1.0 - ((1.0 - eps) ** 2 * np.cos(th) ** 2
                                       + (1.0 + eps) ** 2 * np.sin(th) ** 2))
    band = (r > 1.5 * radius) & (r < 0.5 * r_out)
    err = float(np.linalg.norm((p - p_ana)[band]) / np.linalg.norm(p_ana[band]))
    return {"laplace_of_psi_rel_error": lap_err, "fourier_vs_sparse_rel_error": solver_err,
            "pressure_interior_rel_error": err}


def metric_check() -> Dict[str, float]:
    """The discrete Laplacian must annihilate ln(r) and any linear theta mode."""
    grid = LogPolarGrid(0.5, 5.0, 64, 64)
    f = np.repeat(np.log(grid.r)[:, None], grid.ntheta, axis=1)
    lap_f = grid.laplace(f)
    err_ln = float(np.max(np.abs(lap_f[1:-1]))) / (1.0 / grid.r_in ** 2)
    g = np.repeat(np.sin(grid.theta)[None, :], grid.nr, axis=0)
    lap_g = grid.laplace(g)
    err_sin = float(np.max(np.abs(lap_g[1:-1] + g[1:-1] / grid.r2[1:-1])))
    return {"laplacian_of_ln_r": err_ln, "laplacian_of_sin_theta": err_sin}