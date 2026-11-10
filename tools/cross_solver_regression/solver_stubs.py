"""Synthetic solver stubs for cross-solver regression testing.

Zero domain semantics: these are pure numerical functions that simulate
the behavior of different solver types (LBM, FEM, DEM, FVM) using
synthetic mathematical problems. Each solver stub:

1. Takes a problem specification (synthetic domain name, parameters)
2. Returns numerical results with controllable accuracy
3. Has a known reference solution for regression comparison

The "solvers" are deterministic, pure functions returning dicts.
"""
from __future__ import annotations

import math
from typing import Any, Dict

__all__ = [
    "lbm_solver", "fem_solver", "dem_solver", "fvm_solver",
    "lbm_reference", "fem_reference", "dem_reference", "fvm_reference",
    "SOLVER_REGISTRY",
]


# ── TensorLBM: Lattice-Boltzmann-style (diffusion/convection problems) ──────

def lbm_solver(
    grid_size: int = 64,
    diffusion_coeff: float = 0.1,
    time_steps: int = 100,
    problem_id: str = "lbm_diffusion_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Synthetic LBM-style diffusion solver.

    Solves 1D diffusion equation ∂u/∂t = D ∂²u/∂x² using explicit FTCS scheme.
    Initial condition: Gaussian pulse centered at x=0.5.
    Uses sub-stepping (10 substeps per output step) for better accuracy.
    """
    D = float(diffusion_coeff)
    N = int(grid_size)
    nt = int(time_steps)
    dx = 1.0 / N
    dt_base = 0.4 * dx * dx / D  # CFL stability limit
    n_substeps = 10
    dt = dt_base / n_substeps

    # Initial Gaussian pulse
    x = [(i + 0.5) * dx for i in range(N)]
    u = [math.exp(-((xi - 0.5) ** 2) / 0.01) for xi in x]

    # FTCS time stepping with sub-stepping
    r = D * dt / (dx * dx)
    total_steps = nt * n_substeps
    for _ in range(total_steps):
        u_new = list(u)
        for i in range(1, N - 1):
            u_new[i] = u[i] + r * (u[i + 1] - 2 * u[i] + u[i - 1])
        u = u_new

    # Compute L2 norm and max value as metrics
    l2_norm = math.sqrt(sum(ui * ui for ui in u) * dx)
    max_val = max(u)
    mass = sum(u) * dx  # should be conserved

    return {
        "l2_norm": l2_norm,
        "max_value": max_val,
        "mass": mass,
        "grid_size": N,
        "time_steps": nt,
        "diffusion_coeff": D,
        "problem_id": problem_id,
        "solver": "TensorLBM",
    }


def lbm_reference(
    grid_size: int = 64,
    diffusion_coeff: float = 0.1,
    time_steps: int = 100,
    problem_id: str = "lbm_diffusion_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Analytical reference for LBM diffusion problem.

    Exact solution of 1D diffusion with Gaussian IC on periodic domain.
    """
    D = float(diffusion_coeff)
    N = int(grid_size)
    nt = int(time_steps)
    dx = 1.0 / N
    dt_base = 0.4 * dx * dx / D
    n_substeps = 10
    dt = dt_base / n_substeps
    t_final = nt * n_substeps * dt

    # Analytical: Gaussian spreads as sigma^2(t) = sigma_0^2 + 2*D*t
    sigma0_sq = 0.005  # initial sigma^2 (half of 0.01 in IC)
    sigma_t_sq = sigma0_sq + 2.0 * D * t_final

    # L2 norm of Gaussian: sqrt(integral of exp(-2*(x-0.5)^2/sigma^2) dx)
    # = sqrt(sigma * sqrt(pi/2))
    l2_exact = math.sqrt(math.sqrt(math.pi / 2.0) * math.sqrt(sigma_t_sq))

    # Max value: 1/sqrt(1 + 4*D*t/sigma0^2) (for normalized Gaussian)
    max_exact = 1.0 / math.sqrt(1.0 + 4.0 * D * t_final / sigma0_sq)

    # Mass is conserved exactly
    mass_exact = math.sqrt(math.pi * sigma0_sq)  # integral of initial Gaussian

    return {
        "l2_norm": l2_exact,
        "max_value": max_exact,
        "mass": mass_exact,
        "grid_size": N,
        "time_steps": nt,
        "diffusion_coeff": D,
        "problem_id": problem_id,
        "solver": "reference",
    }


# ── TensorFEM: Finite-Element-style (elliptic/Poisson problems) ─────────────

def fem_solver(
    n_elements: int = 32,
    source_amplitude: float = 1.0,
    polynomial_order: int = 1,
    problem_id: str = "fem_poisson_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Synthetic FEM-style Poisson solver.

    Solves -u'' = f on [0,1] with u(0)=u(1)=0 using piecewise polynomial FEM.
    Source: f(x) = A * sin(pi * x), exact solution: u(x) = A/(pi^2) * sin(pi*x).
    """
    A = float(source_amplitude)
    ne = int(n_elements)
    p = int(polynomial_order)
    h = 1.0 / ne

    # Nodal positions
    nodes = [i * h for i in range(ne + 1)]

    # FEM assembly (simplified: lumped mass approach for synthetic benchmark)
    # For p=1 (linear elements), the FEM solution is very close to exact
    # For higher p, we get spectral-like convergence
    pi_sq = math.pi ** 2

    # Compute FEM solution at nodes (interior nodes only)
    u_fem = [0.0]  # u(0) = 0
    for i in range(1, ne):
        x = nodes[i]
        # FEM with linear elements: slight numerical diffusion
        # Error scales as O(h^(2p)) for smooth problems
        error_factor = 1.0 - (h ** (2 * p)) / (12.0 * p + 1)
        u_exact_i = A / pi_sq * math.sin(math.pi * x)
        u_fem.append(u_exact_i * error_factor)
    u_fem.append(0.0)  # u(1) = 0

    # Metrics
    l2_error = math.sqrt(sum((u_fem[i] - A / pi_sq * math.sin(math.pi * nodes[i])) ** 2
                             * h for i in range(ne + 1)))
    max_error = max(abs(u_fem[i] - A / pi_sq * math.sin(math.pi * nodes[i]))
                    for i in range(ne + 1))
    energy = sum(u_fem[i] ** 2 * h for i in range(ne + 1))

    return {
        "l2_error": l2_error,
        "max_error": max_error,
        "energy_norm": energy,
        "n_elements": ne,
        "polynomial_order": p,
        "source_amplitude": A,
        "problem_id": problem_id,
        "solver": "TensorFEM",
    }


def fem_reference(
    n_elements: int = 32,
    source_amplitude: float = 1.0,
    polynomial_order: int = 1,
    problem_id: str = "fem_poisson_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Exact reference for FEM Poisson problem.
    
    Note: l2_error and max_error are not included because they are error metrics
    (zero for exact solution). Only solution metrics (energy_norm) are compared.
    """
    A = float(source_amplitude)
    ne = int(n_elements)
    p = int(polynomial_order)
    h = 1.0 / ne
    pi_sq = math.pi ** 2

    # Exact solution: u(x) = A/pi^2 * sin(pi*x)
    # Energy norm: integral of u^2 dx = A^2/(2*pi^4)
    energy_exact = A ** 2 / (2.0 * pi_sq ** 2)

    return {
        "energy_norm": energy_exact,
        "n_elements": ne,
        "polynomial_order": p,
        "source_amplitude": A,
        "problem_id": problem_id,
        "solver": "reference",
    }


# ── TensorDEM: Discrete-Element-style (contact/packing problems) ────────────

def dem_solver(
    n_particles: int = 100,
    packing_fraction: float = 0.6,
    stiffness: float = 1e4,
    problem_id: str = "dem_packing_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Synthetic DEM-style packing solver.

    Simulates random packing of spheres using a simplified contact model.
    Returns coordination number, pressure, and energy metrics.
    """
    n = int(n_particles)
    phi = float(packing_fraction)
    k = float(stiffness)

    # Synthetic coordination number (scales with packing fraction)
    # Isostatic value for frictionless spheres: Z = 2*d = 6 (3D)
    z_mean = 6.0 * (phi / 0.64)  # random close packing ~0.64
    z_std = 1.2 * math.sqrt(1.0 - phi / 0.74)  # max packing ~0.74

    # Synthetic pressure from Hertzian contact
    # p ~ k * (phi - phi_c)^alpha where phi_c ~ 0.58 (jamming)
    phi_c = 0.58
    if phi > phi_c:
        pressure = k * ((phi - phi_c) / (1.0 - phi_c)) ** 0.5 / n
    else:
        pressure = 0.0

    # Elastic energy
    energy = 0.5 * k * n * (phi - phi_c) ** 2 / (1.0 - phi_c) ** 2 if phi > phi_c else 0.0

    # Contact force distribution (synthetic Weibull-like)
    f_mean = pressure * math.pi / (n * z_mean) if n > 0 and z_mean > 0 else 0.0

    return {
        "coordination_number": z_mean,
        "coordination_std": z_std,
        "pressure": pressure,
        "elastic_energy": energy,
        "mean_contact_force": f_mean,
        "n_particles": n,
        "packing_fraction": phi,
        "stiffness": k,
        "problem_id": problem_id,
        "solver": "TensorDEM",
    }


def dem_reference(
    n_particles: int = 100,
    packing_fraction: float = 0.6,
    stiffness: float = 1e4,
    problem_id: str = "dem_packing_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Reference values for DEM packing (same model as solver, for regression testing)."""
    n = int(n_particles)
    phi = float(packing_fraction)
    k = float(stiffness)

    # Same coordination number model as solver
    z_ref = 6.0 * (phi / 0.64)
    z_std_ref = 1.2 * math.sqrt(max(0, 1.0 - phi / 0.74))

    # Same pressure model as solver
    phi_c = 0.58
    if phi > phi_c:
        p_ref = k * ((phi - phi_c) / (1.0 - phi_c)) ** 0.5 / n
    else:
        p_ref = 0.0

    # Same energy model as solver
    energy_ref = 0.5 * k * n * (phi - phi_c) ** 2 / (1.0 - phi_c) ** 2 if phi > phi_c else 0.0

    f_ref = p_ref * math.pi / (n * z_ref) if n > 0 and z_ref > 0 else 0.0

    return {
        "coordination_number": z_ref,
        "coordination_std": z_std_ref,
        "pressure": p_ref,
        "elastic_energy": energy_ref,
        "mean_contact_force": f_ref,
        "n_particles": n,
        "packing_fraction": phi,
        "stiffness": k,
        "problem_id": problem_id,
        "solver": "reference",
    }


# ── TensorFVM: Finite-Volume-style (advection problems) ─────────────────────

def fvm_solver(
    n_cells: int = 64,
    velocity: float = 1.0,
    time_steps: int = 50,
    problem_id: str = "fvm_advection_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Synthetic FVM-style linear advection solver.

    Solves ∂u/∂t + c ∂u/∂x = 0 on periodic domain [0,1] using upwind scheme.
    Initial condition: square pulse (challenging for advection schemes).
    """
    c = float(velocity)
    nc = int(n_cells)
    nt = int(time_steps)
    dx = 1.0 / nc
    dt = 0.8 * dx / abs(c)  # CFL = 0.8

    # Initial square pulse
    u = [1.0 if 0.2 <= (i + 0.5) * dx <= 0.4 else 0.0 for i in range(nc)]

    # Upwind advection
    nu = c * dt / dx  # CFL number
    for _ in range(nt):
        u_new = list(u)
        for i in range(nc):
            i_left = (i - 1) % nc
            if c > 0:
                u_new[i] = u[i] - nu * (u[i] - u[i_left])
            else:
                i_right = (i + 1) % nc
                u_new[i] = u[i] - nu * (u[i_right] - u[i])
        u = u_new

    # Metrics
    mass = sum(u) * dx
    l2_norm = math.sqrt(sum(ui * ui for ui in u) * dx)
    max_val = max(u)
    min_val = min(u)
    # Monotonicity violation (overshoot/undershoot)
    monotonicity_violation = max(0.0, max_val - 1.0) + max(0.0, -min_val)

    return {
        "l2_norm": l2_norm,
        "max_value": max_val,
        "min_value": min_val,
        "mass": mass,
        "monotonicity_violation": monotonicity_violation,
        "n_cells": nc,
        "time_steps": nt,
        "velocity": c,
        "problem_id": problem_id,
        "solver": "TensorFVM",
    }


def fvm_reference(
    n_cells: int = 64,
    velocity: float = 1.0,
    time_steps: int = 50,
    problem_id: str = "fvm_advection_01",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Exact reference for FVM advection (exact translation for periodic BC)."""
    c = float(velocity)
    nc = int(n_cells)
    nt = int(time_steps)
    dx = 1.0 / nc
    dt = 0.8 * dx / abs(c)
    t_final = nt * dt

    # Exact: initial profile translated by c*t (periodic)
    shift = (c * t_final) % 1.0
    shift_cells = int(round(shift / dx))

    u_exact = [1.0 if 0.2 <= ((i + 0.5) * dx - shift) % 1.0 <= 0.4 else 0.0
               for i in range(nc)]

    mass_exact = sum(u_exact) * dx
    l2_exact = math.sqrt(sum(ui * ui for ui in u_exact) * dx)

    return {
        "l2_norm": l2_exact,
        "max_value": 1.0,
        "mass": mass_exact,
        "n_cells": nc,
        "time_steps": nt,
        "velocity": c,
        "problem_id": problem_id,
        "solver": "reference",
    }


# ── Registry ────────────────────────────────────────────────────────────────

SOLVER_REGISTRY = {
    "TensorLBM": {"solver": lbm_solver, "reference": lbm_reference},
    "TensorFEM": {"solver": fem_solver, "reference": fem_reference},
    "TensorDEM": {"solver": dem_solver, "reference": dem_reference},
    "TensorFVM": {"solver": fvm_solver, "reference": fvm_reference},
}
