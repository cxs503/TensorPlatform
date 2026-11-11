"""Synthetic solver benchmarks for cross-architecture reproducibility.

Three numerical patterns that mirror the computational structure of common
iterative/stencil solvers, sparse linear system solvers, and dense oscillatory
system solvers — without any domain vocabulary.

Each benchmark produces:
  - scalar key quantities (analogous to observables in numerical simulations)
  - field quantities (analogous to solution vectors)
  - convergence history (for iterative solvers)

All values are deterministic given a seed. Variants simulate cross-arch
differences through controlled perturbation of reduction order, FMA contraction,
and summation algorithm.

Zero domain semantics: names are bench_N/key_N/field_N, no physics terms.
"""

from __future__ import annotations

import math
import hashlib
from typing import Any, Dict, List, Tuple, Optional

from . import numerics


# --------------------------------------------------------------------------- #
# Reproducible PRNG (same seed → same sequence on any ISA)
# --------------------------------------------------------------------------- #
class DetRng:
    """Deterministic PRNG using integer LCG + float conversion.

    Produces identical sequences on any IEEE-754 conforming platform
    because all operations are integer arithmetic until the final
    conversion to double (which is exact for 52-bit mantissa).
    """

    def __init__(self, seed: int = 42):
        self._state = seed & 0xFFFFFFFFFFFFFFFF

    def _next(self) -> int:
        # LCG parameters from Numerical Recipes
        self._state = (6364136223846793005 * self._state + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        return self._state

    def uniform(self) -> float:
        """Uniform in [0, 1)."""
        return (self._next() >> 11) / (1 << 53)

    def normal(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """Box-Muller transform (deterministic given seed)."""
        u1 = max(self.uniform(), 1e-300)
        u2 = self.uniform()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return mu + sigma * z

    def uniform_array(self, n: int) -> List[float]:
        return [self.uniform() for _ in range(n)]


# --------------------------------------------------------------------------- #
# Benchmark 1: Iterative stencil (mimics lattice-based flow solvers)
# --------------------------------------------------------------------------- #
def bench_iterative_stencil(
    nx: int = 32,
    ny: int = 32,
    max_iter: int = 200,
    viscosity_param: float = 1000.0,
    seed: int = 1000,
    sum_variant: str = "naive",
    fma: bool = False,
) -> Dict[str, Any]:
    """2D iterative stencil solver on a uniform grid.

    Solves a discrete Laplacian + advection-diffusion equation.
    Key quantities: max value, mean kinetic energy, enstrophy analog.
    Field: solution values at each grid point.

    sum_variant: "naive" | "reverse" | "kahan" | "fsum"
    fma: if True, emulate FMA contraction in residual computation
    """
    rng = DetRng(seed)
    dx = 1.0 / (nx - 1)
    dy = 1.0 / (ny - 1)
    nu = 1.0 / viscosity_param

    # Initial condition: small random perturbation around base state
    u = [[0.0] * ny for _ in range(nx)]
    for i in range(nx):
        for j in range(ny):
            if i == nx - 1:  # boundary
                u[i][j] = 1.0
            else:
                u[i][j] = 0.01 * rng.uniform()

    # Jacobi iteration with advection-diffusion
    residuals: List[float] = []
    sum_fn = _get_sum_fn(sum_variant)

    for iteration in range(max_iter):
        u_new = [[0.0] * ny for _ in range(nx)]
        for i in range(1, nx - 1):
            for j in range(1, ny - 1):
                # Diffusion (5-point Laplacian)
                lap = (u[i+1][j] + u[i-1][j] - 2.0*u[i][j]) / (dx*dx) + \
                      (u[i][j+1] + u[i][j-1] - 2.0*u[i][j]) / (dy*dy)
                # Advection (central difference)
                adv_x = (u[i+1][j] - u[i-1][j]) / (2.0*dx) * u[i][j]
                adv_y = (u[i][j+1] - u[i][j-1]) / (2.0*dy) * u[i][j]
                if fma:
                    u_new[i][j] = numerics.exact_fma(u[i][j], 1.0, 0.01 * (nu * lap - adv_x - adv_y))
                else:
                    u_new[i][j] = u[i][j] + 0.01 * (nu * lap - adv_x - adv_y)
        # Boundary conditions
        for j in range(ny):
            u_new[0][j] = 0.0
            u_new[nx-1][j] = 1.0
        for i in range(nx):
            u_new[i][0] = u_new[i][1]
            u_new[i][ny-1] = 0.0

        # Residual
        res_vals = []
        for i in range(1, nx-1):
            for j in range(1, ny-1):
                res_vals.append(abs(u_new[i][j] - u[i][j]))
        residual = sum_fn(res_vals) / len(res_vals)
        residuals.append(residual)
        u = u_new

    # Key quantities
    flat_u = [u[i][j] for i in range(nx) for j in range(ny)]
    max_vel = max(flat_u)
    kinetic_energy = sum_fn([v * v for v in flat_u]) / len(flat_u)
    mean_vel = sum_fn(flat_u) / len(flat_u)

    # Enstrophy analog (vorticity squared, simplified)
    enstrophy_vals = []
    for i in range(1, nx-1):
        for j in range(1, ny-1):
            omega = (u[i][j+1] - u[i][j-1]) / (2.0*dy) - (u[i+1][j] - u[i-1][j]) / (2.0*dx)
            enstrophy_vals.append(omega * omega)
    enstrophy = sum_fn(enstrophy_vals) / len(enstrophy_vals)

    return {
        "bench": "iterative_stencil",
        "scalars": {
            "key_max_value": max_vel,
            "key_kinetic_energy": kinetic_energy,
            "key_mean_value": mean_vel,
            "key_enstrophy": enstrophy,
        },
        "fields": {
            "field_profile_x_mid": [u[nx//2][j] for j in range(ny)],
            "field_profile_y_mid": [u[i][ny//2] for i in range(nx)],
        },
        "convergence": residuals,
        "meta": {
            "nx": nx, "ny": ny, "max_iter": max_iter,
            "viscosity_param": viscosity_param, "seed": seed,
            "sum_variant": sum_variant, "fma": fma,
        },
    }


# --------------------------------------------------------------------------- #
# Benchmark 2: Sparse SPD solve (mimics finite element static analysis)
# --------------------------------------------------------------------------- #
def bench_sparse_spd_solve(
    n: int = 100,
    seed: int = 2000,
    sum_variant: str = "naive",
    fma: bool = False,
) -> Dict[str, Any]:
    """Sparse symmetric positive-definite system solve (Jacobi preconditioned CG).

    Mimics static structural analysis: K*u = f where K is banded SPD.
    Key quantities: max solution value, strain energy analog, compliance analog.
    Field: solution vector.

    sum_variant: "naive" | "reverse" | "kahan" | "fsum"
    """
    rng = DetRng(seed)
    sum_fn = _get_sum_fn(sum_variant)

    # Build banded SPD matrix (tridiagonal + diagonal dominance)
    # K[i][i] = 4 + rng, K[i][i±1] = -1
    diag = [4.0 + 0.1 * rng.uniform() for _ in range(n)]
    off_diag = [-1.0 + 0.01 * rng.uniform() for _ in range(n - 1)]

    # Load vector
    f = [rng.uniform() - 0.5 for _ in range(n)]
    # Add concentrated load at midspan
    f[n // 2] += 2.0

    # Conjugate gradient with Jacobi preconditioner
    x = [0.0] * n
    r = list(f)  # r = f - K*x, x=0 so r=f
    z = [r[i] / diag[i] for i in range(n)]
    p = list(z)
    rz = sum_fn([r[i] * z[i] for i in range(n)])

    residuals = []
    max_iter = 300

    for iteration in range(max_iter):
        # K*p
        kp = [diag[i] * p[i] for i in range(n)]
        for i in range(n - 1):
            kp[i] += off_diag[i] * p[i + 1]
            kp[i + 1] += off_diag[i] * p[i]

        alpha_denom = sum_fn([p[i] * kp[i] for i in range(n)])
        if abs(alpha_denom) < 1e-30:
            break
        alpha = rz / alpha_denom

        for i in range(n):
            x[i] += alpha * p[i]
            r[i] -= alpha * kp[i]

        res_norm = math.sqrt(sum_fn([ri * ri for ri in r]))
        residuals.append(res_norm)

        if res_norm < 1e-12:
            break

        z = [r[i] / diag[i] for i in range(n)]
        rz_new = sum_fn([r[i] * z[i] for i in range(n)])
        beta = rz_new / rz if abs(rz) > 1e-30 else 0.0
        rz = rz_new

        for i in range(n):
            p[i] = z[i] + beta * p[i]

    # Key quantities
    max_disp = max(abs(xi) for xi in x)
    mid_disp = x[n // 2]

    # Strain energy analog: 0.5 * x^T * K * x
    kx = [diag[i] * x[i] for i in range(n)]
    for i in range(n - 1):
        kx[i] += off_diag[i] * x[i + 1]
        kx[i + 1] += off_diag[i] * x[i]
    strain_energy = 0.5 * sum_fn([x[i] * kx[i] for i in range(n)])

    # Compliance analog: f^T * x
    compliance = sum_fn([f[i] * x[i] for i in range(n)])

    # L2 norm of solution
    l2_disp = math.sqrt(sum_fn([xi * xi for xi in x]))

    return {
        "bench": "sparse_spd_solve",
        "scalars": {
            "key_max_value": max_disp,
            "key_mid_value": mid_disp,
            "key_strain_energy": strain_energy,
            "key_compliance": compliance,
            "key_l2_solution": l2_disp,
        },
        "fields": {
            "field_solution": list(x),
            "field_residual_history": residuals,
        },
        "convergence": residuals,
        "meta": {
            "n": n, "seed": seed,
            "sum_variant": sum_variant, "fma": fma,
            "iterations": len(residuals),
        },
    }


# --------------------------------------------------------------------------- #
# Benchmark 3: Dense oscillatory system (mimics boundary element methods)
# --------------------------------------------------------------------------- #
def bench_dense_oscillatory_solve(
    n: int = 60,
    ka: float = 1.5,
    seed: int = 3000,
    sum_variant: str = "naive",
    fma: bool = False,
) -> Dict[str, Any]:
    """Dense complex linear system with oscillatory kernel.

    Mimics wave scattering problems: dense matrix with oscillatory Green's function.
    Solves real/imag parts as 2n×2n real system.
    Key quantities: added mass analog, damping analog, scattering cross-section analog.
    Field: potential on surface panels.

    sum_variant: "naive" | "reverse" | "kahan" | "fsum"
    """
    rng = DetRng(seed)
    sum_fn = _get_sum_fn(sum_variant)

    # Panel "coordinates" on unit circle
    theta = [2.0 * math.pi * i / n for i in range(n)]

    # Build dense oscillatory matrix (real part of Green's function)
    # A[i][j] = cos(ka * |x_i - x_j|) / (4*pi) + delta_ij
    # B[i][j] = sin(ka * |x_i - x_j|) / (4*pi)
    # System: [A -B; B A] [re; im] = [rhs_re; rhs_im]

    def dist(i, j):
        dx = math.cos(theta[i]) - math.cos(theta[j])
        dy = math.sin(theta[i]) - math.sin(theta[j])
        return math.sqrt(dx*dx + dy*dy)

    A = [[0.0]*n for _ in range(n)]
    B = [[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                A[i][j] = 1.0 + 0.5  # diagonal dominance
                B[i][j] = 0.0
            else:
                r = dist(i, j)
                arg = ka * r
                A[i][j] = math.cos(arg) / (4.0 * math.pi * max(r, 1e-10))
                B[i][j] = math.sin(arg) / (4.0 * math.pi * max(r, 1e-10))

    # RHS: plane wave incidence
    rhs_re = [math.cos(ka * math.cos(theta[i])) for i in range(n)]
    rhs_im = [math.sin(ka * math.cos(theta[i])) for i in range(n)]

    # Solve 2n×2n system via Richardson iteration with diagonal preconditioner
    # [A -B; B A] has diagonal [A_ii, A_ii] = [1.5, 1.5]
    x_re = [0.0] * n
    x_im = [0.0] * n
    omega = 0.6  # relaxation

    residuals = []
    max_iter = 500

    for iteration in range(max_iter):
        # Residual
        res_re = [0.0] * n
        res_im = [0.0] * n
        for i in range(n):
            ax_re = sum_fn([A[i][j] * x_re[j] - B[i][j] * x_im[j] for j in range(n)])
            ax_im = sum_fn([B[i][j] * x_re[j] + A[i][j] * x_im[j] for j in range(n)])
            res_re[i] = rhs_re[i] - ax_re
            res_im[i] = rhs_im[i] - ax_im

        res_norm = math.sqrt(sum_fn([res_re[i]**2 + res_im[i]**2 for i in range(n)]))
        residuals.append(res_norm)

        if res_norm < 1e-10:
            break

        # Update with diagonal preconditioner (1/1.5)
        for i in range(n):
            x_re[i] += omega * res_re[i] / 1.5
            x_im[i] += omega * res_im[i] / 1.5

    # Key quantities
    # "Added mass" analog: integral of Re(potential) * normal
    added_mass = sum_fn([x_re[i] * math.cos(theta[i]) for i in range(n)]) * (2.0 * math.pi / n)

    # "Damping" analog: integral of Im(potential) * normal
    damping = sum_fn([x_im[i] * math.cos(theta[i]) for i in range(n)]) * (2.0 * math.pi / n)

    # "Scattering cross-section" analog
    scat = sum_fn([x_re[i]**2 + x_im[i]**2 for i in range(n)]) * (2.0 * math.pi / n)

    # L2 norm of potential
    l2_potential = math.sqrt(sum_fn([x_re[i]**2 + x_im[i]**2 for i in range(n)]))

    return {
        "bench": "dense_oscillatory_solve",
        "scalars": {
            "key_added_mass_analog": added_mass,
            "key_damping_analog": damping,
            "key_scattering_analog": scat,
            "key_l2_potential": l2_potential,
        },
        "fields": {
            "field_real_potential": list(x_re),
            "field_imag_potential": list(x_im),
        },
        "convergence": residuals,
        "meta": {
            "n": n, "ka": ka, "seed": seed,
            "sum_variant": sum_variant, "fma": fma,
            "iterations": len(residuals),
        },
    }


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #
def _get_sum_fn(variant: str):
    if variant == "fsum":
        return math.fsum
    elif variant == "reverse":
        def s(vals):
            t = 0.0
            for v in reversed(list(vals)):
                t += v
            return t
        return s
    elif variant == "kahan":
        def s(vals):
            t = 0.0
            c = 0.0
            for v in vals:
                y = v - c
                tt = t + y
                c = (tt - t) - y
                t = tt
            return t
        return s
    else:  # naive
        def s(vals):
            t = 0.0
            for v in vals:
                t += v
            return t
        return s


def run_all_benchmarks(
    sum_variant: str = "naive",
    fma: bool = False,
    seeds: Optional[Dict[str, int]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run all three benchmarks and return combined results."""
    seeds = seeds or {}
    return {
        "iterative_stencil": bench_iterative_stencil(
            seed=seeds.get("iterative_stencil", 1000),
            sum_variant=sum_variant, fma=fma),
        "sparse_spd_solve": bench_sparse_spd_solve(
            seed=seeds.get("sparse_spd_solve", 2000),
            sum_variant=sum_variant, fma=fma),
        "dense_oscillatory_solve": bench_dense_oscillatory_solve(
            seed=seeds.get("dense_oscillatory_solve", 3000),
            sum_variant=sum_variant, fma=fma),
    }


def record_fingerprint(record: Dict[str, Any]) -> str:
    """Compute SHA-256 fingerprint of a benchmark record (for byte-identity check)."""
    canonical = json_deterministic(record)
    return hashlib.sha256(canonical.encode()).hexdigest()


def json_deterministic(obj: Any) -> str:
    """Deterministic JSON serialization (sorted keys, no whitespace)."""
    import json
    return json.dumps(obj, sort_keys=True, separators=(',', ':'))
