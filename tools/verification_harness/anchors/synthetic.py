#!/usr/bin/env python3
"""Synthetic anchors: the harness applied to mathematics, not to a physical domain.

Purpose
-------
The harness (:mod:`registry`, :mod:`runner`, :mod:`evidence`) must be provably
usable by *any* domain without any of that domain leaking into the mechanism.
The cheapest honest proof is to instantiate it on purely synthetic problems
where an independent authority exists in closed form:

===============  ====================================  ==============================
anchor           implementation (code under test)      independent authority
===============  ====================================  ==============================
parabolic form   composite midpoint quadrature          closed-form integrals
oscillator       classical RK4                         closed-form solution
slab             finite-difference tridiagonal solve    linear/quadratic closed form
friction         Newton iteration                       bisection on the same residual
spectrum         power iteration + Rayleigh quotient    eigenvalue closed form
convergence      measured error ratio between two grids  theoretical order of accuracy
===============  ====================================  ==============================

Every callable takes only keyword arguments taken verbatim from the case file's
``inputs`` block and returns ``{quantity_name: float}``.  Two callables of one
case must therefore accept the same keyword set; surplus keywords are declared
in the case file and ignored by the analytic side.

No real vessel name, no real waterway and no real operating condition appears
here: the test geometry is a *mathematical* parabolic hull form (Wigley-type
shape functions), which is why this module can only be used as a
self-consistency anchor, never as physical evidence.
"""
from __future__ import annotations

import math
from typing import Dict, List

# ---------------------------------------------------------------------------
# 1. Parabolic (Wigley-type) hull form — closed-form hydrostatic integrals
# ---------------------------------------------------------------------------
def parabolic_half_breadth(x: float, z: float, length: float, beam: float,
                           draft: float) -> float:
    """Half-breadth of the synthetic parabolic form.

    ``y(x, z) = (B/2) (1 - (2x/L)^2) (1 - (z/T)^2)`` inside the box
    ``|x| <= L/2``, ``0 <= z <= T``; zero outside (i.e. above the deck line and
    beyond the ends).
    """
    if length <= 0.0 or beam <= 0.0 or draft <= 0.0:
        raise ValueError("length, beam and draft must be positive")
    if abs(x) > 0.5 * length or z < 0.0 or z > draft:
        return 0.0
    return 0.5 * beam * (1.0 - (2.0 * x / length) ** 2) * (1.0 - (z / draft) ** 2)


def parabolic_form_quadrature(length: float, beam: float, draft: float,
                              nx: int = 200, nz: int = 200) -> Dict[str, float]:
    """Displacement volume and waterplane area by composite midpoint rule.

    The error of the composite midpoint rule on this integrand is ``O(h^2)``,
    which is what makes the anchor informative: it *does not* agree exactly with
    the closed form, so the harness is really comparing two independent routes.
    """
    nx, nz = int(nx), int(nz)
    if nx < 2 or nz < 2:
        raise ValueError("nx and nz must be >= 2")
    dx, dz = length / nx, draft / nz
    volume = 0.0
    waterplane = 0.0
    for i in range(nx):
        x = -0.5 * length + (i + 0.5) * dx
        for j in range(nz):
            z = (j + 0.5) * dz
            volume += 2.0 * parabolic_half_breadth(x, z, length, beam, draft) * dx * dz
        waterplane += 2.0 * parabolic_half_breadth(x, 0.0, length, beam, draft) * dx
    return {"volume": volume, "waterplane_area": waterplane, "block_coefficient": volume / (length * beam * draft)}


def parabolic_form_closed_form(length: float, beam: float, draft: float,
                               nx: int = 200, nz: int = 200) -> Dict[str, float]:
    """Closed-form integrals of the same form (``nx``/``nz`` are ignored).

    ``∫∫ 2 y dx dz = (4/9) L B T`` and ``∫ 2 y(x, 0) dx = (2/3) L B``.
    """
    volume = (4.0 / 9.0) * length * beam * draft
    waterplane = (2.0 / 3.0) * length * beam
    return {"volume": volume, "waterplane_area": waterplane,
            "block_coefficient": 4.0 / 9.0}


# ---------------------------------------------------------------------------
# 2. Damped linear oscillator — RK4 versus closed form
# ---------------------------------------------------------------------------
def damped_oscillator_rk4(omega: float, zeta: float, x0: float,
                          duration: float, steps: int) -> Dict[str, float]:
    """Classical RK4 on ``x'' + 2 zeta omega x' + omega^2 x = 0``."""
    omega, zeta, x0, duration, steps = float(omega), float(zeta), float(x0), float(duration), int(steps)
    if omega <= 0.0:
        raise ValueError("omega must be positive")
    if not 0.0 <= zeta < 1.0:
        raise ValueError("only the underdamped branch (0 <= zeta < 1) is anchored")
    if duration <= 0.0 or steps < 1:
        raise ValueError("duration must be positive and steps >= 1")

    def deriv(state: List[float]) -> List[float]:
        x, v = state
        return [v, -2.0 * zeta * omega * v - omega * omega * x]

    state = [x0, 0.0]
    h = duration / steps
    for _ in range(steps):
        k1 = deriv(state)
        k2 = deriv([state[0] + 0.5 * h * k1[0], state[1] + 0.5 * h * k1[1]])
        k3 = deriv([state[0] + 0.5 * h * k2[0], state[1] + 0.5 * h * k2[1]])
        k4 = deriv([state[0] + h * k3[0], state[1] + h * k3[1]])
        state = [state[0] + h * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]) / 6.0,
                 state[1] + h * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]) / 6.0]
    return {"x_end": state[0], "v_end": state[1]}


def damped_oscillator_closed_form(omega: float, zeta: float, x0: float,
                                  duration: float, steps: int = 0) -> Dict[str, float]:
    """Exact underdamped solution ``x(t)`, ``v(t)`` (``steps`` is ignored)."""
    omega, zeta, x0, duration = float(omega), float(zeta), float(x0), float(duration)
    if not 0.0 <= zeta < 1.0:
        raise ValueError("only the underdamped branch (0 <= zeta < 1) is anchored")
    decay = zeta * omega
    wd = omega * math.sqrt(1.0 - zeta * zeta)
    envelope = x0 * math.exp(-decay * duration)
    x = envelope * (math.cos(wd * duration) + (decay / wd) * math.sin(wd * duration))
    v = -envelope * (omega * omega / wd) * math.sin(wd * duration)
    return {"x_end": x, "v_end": v}


# ---------------------------------------------------------------------------
# 3. One-dimensional steady diffusion with uniform source
# ---------------------------------------------------------------------------
def slab_finite_difference(conductivity: float, source: float, length: float,
                           t_left: float, t_right: float, nodes: int) -> Dict[str, float]:
    """Tridiagonal finite-difference solve of ``-k T'' = q`` with Dirichlet ends."""
    k, q, big_l = float(conductivity), float(source), float(length)
    t0, t1, n = float(t_left), float(t_right), int(nodes)
    if k <= 0.0 or big_l <= 0.0 or n < 3:
        raise ValueError("conductivity and length must be positive, nodes >= 3")
    h = big_l / (n - 1)
    # -k T'' = q  =>  T_{i-1} - 2 T_i + T_{i+1} = -q h^2 / k ;
    # the assembled operator is tridiag(-1, 2, -1) = -(that), so its rhs flips sign.
    rhs_const = q * h * h / k
    # Thomas algorithm on the interior unknowns 1..n-2.
    lower = [-1.0] * (n - 2)
    diag = [2.0] * (n - 2)
    upper = [-1.0] * (n - 2)
    rhs = [rhs_const] * (n - 2)
    rhs[0] += t0   # row 1: 2 T_1 - T_2 = rhs + T_0 (the known end value moves right)
    rhs[-1] += t1  # last row: 2 T_m - T_{m-1} = rhs + T_{n-1}
    for i in range(1, n - 2):
        factor = lower[i] / diag[i - 1]
        diag[i] -= factor * upper[i - 1]
        rhs[i] -= factor * rhs[i - 1]
    interior = [0.0] * (n - 2)
    interior[-1] = rhs[-1] / diag[-1]
    for i in range(n - 4, -1, -1):
        interior[i] = (rhs[i] - upper[i] * interior[i + 1]) / diag[i]
    profile = [t0] + interior + [t1]
    mid = (n - 1) // 2
    return {"t_mid": profile[mid], "t_quarter": profile[(n - 1) // 4]}


def slab_closed_form(conductivity: float, source: float, length: float,
                     t_left: float, t_right: float, nodes: int = 0) -> Dict[str, float]:
    """``T(x) = t_left + (t_right - t_left) x/L + q (L x - x^2) / (2 k)``."""
    k, q, big_l = float(conductivity), float(source), float(length)
    t0, t1 = float(t_left), float(t_right)

    def profile(x: float) -> float:
        return t0 + (t1 - t0) * x / big_l + q * (big_l * x - x * x) / (2.0 * k)

    return {"t_mid": profile(0.5 * big_l), "t_quarter": profile(0.25 * big_l)}


# ---------------------------------------------------------------------------
# 4. Implicit algebraic law (Colebrook-type residual): Newton vs bisection
# ---------------------------------------------------------------------------
def _colebrook_residual(f: float, reynolds: float, roughness_ratio: float) -> float:
    return 1.0 / math.sqrt(f) + 2.0 * math.log10(roughness_ratio / 3.7 + 2.51 / (reynolds * math.sqrt(f)))


def algebraic_law_newton(reynolds: float, roughness_ratio: float) -> Dict[str, float]:
    """Newton iteration on the implicit friction-factor law (seed ``f = 0.02``)."""
    reynolds, roughness_ratio = float(reynolds), float(roughness_ratio)
    if reynolds <= 0.0 or roughness_ratio < 0.0:
        raise ValueError("reynolds must be positive and roughness_ratio >= 0")
    f = 0.02
    for _ in range(200):
        residual = _colebrook_residual(f, reynolds, roughness_ratio)
        # d/df of 1/sqrt(f) is -1/(2 f^{3/2}); the log term derivative is analytic too.
        log_argument = roughness_ratio / 3.7 + 2.51 / (reynolds * math.sqrt(f))
        derivative = (-0.5 * f ** -1.5
                      + (2.0 / math.log(10.0)) * (2.51 / reynolds) * (-0.5 * f ** -1.5) / log_argument)
        step = residual / derivative
        f -= step
        if abs(step) <= 1e-16 * max(1.0, abs(f)):
            break
    return {"friction_factor": f}


def algebraic_law_bisection(reynolds: float, roughness_ratio: float) -> Dict[str, float]:
    """Bisection on the same residual over ``[1e-3, 2e-1]`` (independent algorithm)."""
    reynolds, roughness_ratio = float(reynolds), float(roughness_ratio)
    if reynolds <= 0.0 or roughness_ratio < 0.0:
        raise ValueError("reynolds must be positive and roughness_ratio >= 0")
    lo, hi = 1e-3, 2e-1
    f_lo = _colebrook_residual(lo, reynolds, roughness_ratio)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = _colebrook_residual(mid, reynolds, roughness_ratio)
        if (f_mid > 0.0) == (f_lo > 0.0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
        if hi - lo <= 1e-16 * max(1.0, hi):
            break
    return {"friction_factor": 0.5 * (lo + hi)}


# ---------------------------------------------------------------------------
# 5. Tridiagonal spectrum: power iteration vs closed-form eigenvalues
# ---------------------------------------------------------------------------
def _tridiagonal_matvec(vector: List[float]) -> List[float]:
    """T = tridiag(-1, 2, -1) mat-vec (the 1-D discrete Laplacian)."""
    n = len(vector)
    out = [0.0] * n
    for i in range(n):
        out[i] = 2.0 * vector[i]
        if i > 0:
            out[i] -= vector[i - 1]
        if i < n - 1:
            out[i] -= vector[i + 1]
    return out


def tridiagonal_spectrum_power(n: int, iterations: int) -> Dict[str, float]:
    """Power iteration + Rayleigh quotient for the largest eigenvalue."""
    n, iterations = int(n), int(iterations)
    if n < 2 or iterations < 1:
        raise ValueError("n >= 2 and iterations >= 1")
    vector = [1.0] * n
    for _ in range(iterations):
        image = _tridiagonal_matvec(vector)
        norm = math.sqrt(sum(component * component for component in image))
        if norm == 0.0:
            raise ValueError("power iteration collapsed to the zero vector")
        vector = [component / norm for component in image]
    image = _tridiagonal_matvec(vector)
    rayleigh = sum(v * w for v, w in zip(vector, image)) / sum(v * v for v in vector)
    return {"spectral_radius": rayleigh}


def tridiagonal_spectrum_closed_form(n: int, iterations: int = 0) -> Dict[str, float]:
    """``lambda_k = 2 - 2 cos(k pi / (n + 1))``; the largest one is ``k = n``.

    Hence ``lambda_max = 2 - 2 cos(n pi / (n + 1)) = 2 + 2 cos(pi / (n + 1))``
    (``iterations`` is ignored).
    """
    n = int(n)
    if n < 2:
        raise ValueError("n >= 2")
    return {"spectral_radius": 2.0 - 2.0 * math.cos(n * math.pi / (n + 1))}


# ---------------------------------------------------------------------------
# 6. Convergence-order anchor: measured order vs theoretical order
# ---------------------------------------------------------------------------
def _rk4_error(rate: float, duration: float, x0: float, steps: int) -> float:
    """Relative error of RK4 against the exact exponential decay at ``t = duration``."""
    exact = x0 * math.exp(-rate * duration)
    state = [x0]
    h = duration / steps

    def deriv(state: List[float]) -> List[float]:
        return [-rate * state[0]]

    for _ in range(steps):
        k1 = deriv(state)
        k2 = deriv([state[0] + 0.5 * h * k1[0]])
        k3 = deriv([state[0] + 0.5 * h * k2[0]])
        k4 = deriv([state[0] + h * k3[0]])
        state = [state[0] + h * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]) / 6.0]
    return abs(state[0] - exact) / abs(exact)


def convergence_observed_order(rate: float, duration: float, x0: float,
                               coarse_steps: int, refinement: int) -> Dict[str, float]:
    """Measure the order of accuracy ``p = log(e_c / e_f) / log(refinement)``."""
    coarse_steps, refinement = int(coarse_steps), int(refinement)
    if coarse_steps < 2 or refinement < 2:
        raise ValueError("coarse_steps >= 2 and refinement >= 2")
    fine_steps = coarse_steps * refinement
    e_coarse = _rk4_error(float(rate), float(duration), float(x0), coarse_steps)
    e_fine = _rk4_error(float(rate), float(duration), float(x0), fine_steps)
    return {"observed_order": math.log(e_coarse / e_fine) / math.log(refinement),
            "coarse_error": e_coarse, "fine_error": e_fine}


def convergence_theoretical_order(rate: float = 1.0, duration: float = 1.0, x0: float = 1.0,
                                  coarse_steps: int = 0, refinement: int = 0) -> Dict[str, float]:
    """Theoretical order of classical RK4 on a linear problem (surplus inputs ignored)."""
    return {"observed_order": 4.0, "coarse_error": 0.0, "fine_error": 0.0}