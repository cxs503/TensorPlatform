"""Hetényi elastic foundation plate — analytical solution + simplified FEM + verification tests.

This module implements:
1. Hetényi analytical solution for a plate on Winkler elastic foundation
   (infinite plate closed form + finite plate Navier series).
2. A simplified FEM solver using finite-difference equivalent (13-point biharmonic stencil).
3. Pytest verification tests comparing analytical vs FEM solutions.

Reference:
  Hetényi, M. (1946). "Beams on Elastic Foundation".
  Timoshenko & Woinowsky-Krieger (1959). "Theory of Plates and Shells", Ch. 8.
"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, Optional

import numpy as np
import pytest


# =============================================================================
# Part 1: Hetényi Analytical Solution
# =============================================================================

def flexural_rigidity(E: float, h: float, nu: float) -> float:
    """D = E h³ / [12 (1 - ν²)]  [N·m]."""
    return E * h ** 3 / (12.0 * (1.0 - nu ** 2))


def characteristic_length(E: float, h: float, nu: float, k: float) -> float:
    """l = (D / k)^(1/4)  [m]."""
    D = flexural_rigidity(E, h, nu)
    return (D / k) ** 0.25


def hetenyi_infinite_plate(P: float, E: float, h: float, nu: float, k: float) -> float:
    """w_max = P l² / (8 D) for infinite plate under central point load."""
    D = flexural_rigidity(E, h, nu)
    l = characteristic_length(E, h, nu, k)
    return P * l ** 2 / (8.0 * D)


def hetenyi_finite_plate(
    P: float, a: float, b: float, E: float, h: float, nu: float, k: float,
    x0: Optional[float] = None, y0: Optional[float] = None,
    n_terms: int = 51, nx: int = 41, ny: int = 41,
) -> Dict[str, np.ndarray]:
    """Simply-supported rectangular plate on elastic foundation (Navier double Fourier series)."""
    if x0 is None:
        x0 = a / 2.0
    if y0 is None:
        y0 = b / 2.0

    D = flexural_rigidity(E, h, nu)
    x = np.linspace(0, a, nx)
    y = np.linspace(0, b, ny)
    X, Y = np.meshgrid(x, y)
    w = np.zeros_like(X)

    for m in range(1, n_terms + 1):
        for n in range(1, n_terms + 1):
            alpha_mn_sq = (m * np.pi / a) ** 2 + (n * np.pi / b) ** 2
            denom = D * alpha_mn_sq ** 2 + k
            coeff = (4.0 * P / (a * b)) * math.sin(m * np.pi * x0 / a) * math.sin(n * np.pi * y0 / b)
            w += (coeff / denom) * np.sin(m * np.pi * X / a) * np.sin(n * np.pi * Y / b)

    return {"x": x, "y": y, "w": w, "w_max": float(np.max(np.abs(w)))}


# =============================================================================
# Part 2: Simplified FEM Solver
# =============================================================================

def fem_plate_on_foundation(
    P: float, a: float, b: float, E: float, h: float, nu: float, k: float,
    nx: int = 21, ny: int = 21,
) -> Dict[str, np.ndarray]:
    """Simplified FEM for plate on elastic foundation using 13-point biharmonic stencil.

    Solves D∇⁴w + kw = q with simply-supported BCs (w=0, M=0 at edges).

    Grid: (0..nx-1, 0..ny-1), boundaries at edges, interior at (1..nx-2, 1..ny-2).
    Interior indexing: (ii, jj) where ii=0..ni-1, jj=0..nj-1, ni=nx-2, nj=ny-2.
    Grid index: i = ii+1, j = jj+1.

    Simply-supported BC: w=0 and ∂²w/∂n²=0 at edges → ghost points: w_ghost = -w_interior.

    Point load discretisation: P applied at centre node as P/(dx·dy) to preserve total force.
    """
    D_val = flexural_rigidity(E, h, nu)
    dx = a / (nx - 1)
    dy = b / (ny - 1)
    ni, nj = nx - 2, ny - 2
    n_dof = ni * nj

    A = np.zeros((n_dof, n_dof))
    f = np.zeros(n_dof)

    def dof(ii, jj):
        return ii * nj + jj

    dx2, dy2 = dx*dx, dy*dy
    dx4, dy4 = dx2*dx2, dy2*dy2
    dx2dy2 = dx2 * dy2

    for ii in range(ni):
        for jj in range(nj):
            idx = dof(ii, jj)

            # Centre: 6/dx⁴ + 6/dy⁴ + 8/(dx²dy²)
            A[idx, idx] += 6.0/dx4 + 6.0/dy4 + 8.0/dx2dy2

            # --- x-direction ---
            if ii >= 1:
                A[idx, dof(ii-1, jj)] += -4.0/dx4 - 4.0/dx2dy2
            if ii >= 2:
                A[idx, dof(ii-2, jj)] += 1.0/dx4
            elif ii == 0:
                A[idx, dof(0, jj)] += -1.0/dx4  # ghost: w_{-1} = -w_1
            if ii < ni - 1:
                A[idx, dof(ii+1, jj)] += -4.0/dx4 - 4.0/dx2dy2
            if ii < ni - 2:
                A[idx, dof(ii+2, jj)] += 1.0/dx4
            elif ii == ni - 1:
                A[idx, dof(ni-1, jj)] += -1.0/dx4  # ghost: w_{nx} = -w_{nx-2}

            # --- y-direction ---
            if jj >= 1:
                A[idx, dof(ii, jj-1)] += -4.0/dy4 - 4.0/dx2dy2
            if jj >= 2:
                A[idx, dof(ii, jj-2)] += 1.0/dy4
            elif jj == 0:
                A[idx, dof(ii, 0)] += -1.0/dy4  # ghost
            if jj < nj - 1:
                A[idx, dof(ii, jj+1)] += -4.0/dy4 - 4.0/dx2dy2
            if jj < nj - 2:
                A[idx, dof(ii, jj+2)] += 1.0/dy4
            elif jj == nj - 1:
                A[idx, dof(ii, nj-1)] += -1.0/dy4  # ghost

            # --- Diagonal (mixed derivative) ---
            if ii >= 1 and jj >= 1:
                A[idx, dof(ii-1, jj-1)] += 2.0/dx2dy2
            if ii >= 1 and jj < nj - 1:
                A[idx, dof(ii-1, jj+1)] += 2.0/dx2dy2
            if ii < ni - 1 and jj >= 1:
                A[idx, dof(ii+1, jj-1)] += 2.0/dx2dy2
            if ii < ni - 1 and jj < nj - 1:
                A[idx, dof(ii+1, jj+1)] += 2.0/dx2dy2

            # Foundation term
            A[idx, idx] += k / D_val

    # Point load at centre: P/(dx·dy) to preserve total force ∫∫q dA = P
    ci, cj = ni // 2, nj // 2
    f[dof(ci, cj)] = P / (dx * dy) / D_val

    w_interior = np.linalg.solve(A, f)

    x = np.linspace(0, a, nx)
    y = np.linspace(0, b, ny)
    w_full = np.zeros((ny, nx))
    for ii in range(ni):
        for jj in range(nj):
            w_full[jj+1, ii+1] = w_interior[dof(ii, jj)]

    return {"x": x, "y": y, "w": w_full, "w_max": float(np.max(np.abs(w_full)))}


# =============================================================================
# Part 3: Ice sheet parameters and reference case
# =============================================================================

def ice_sheet_parameters(h_ice: float = 0.6, sigma_c_mpa: float = 1.3) -> Dict[str, float]:
    """Typical ice-sheet parameters for ice-structure interaction."""
    return {
        "E": 5.0e9, "nu": 0.33, "k": 1025.0 * 9.81, "h": h_ice,
        "rho_ice": 910.0, "rho_water": 1025.0, "g": 9.81,
        "sigma_c": sigma_c_mpa * 1e6,
    }


def compute_reference_case() -> Dict:
    """Reference verification case."""
    params = ice_sheet_parameters(h_ice=0.6, sigma_c_mpa=1.3)
    sigma_f = 0.5 * params["sigma_c"]
    B = 10.0
    P_total = sigma_f * params["h"] ** 2 * B
    l = characteristic_length(params["E"], params["h"], params["nu"], params["k"])
    D = flexural_rigidity(params["E"], params["h"], params["nu"])
    w_max_inf = hetenyi_infinite_plate(P_total, params["E"], params["h"], params["nu"], params["k"])
    return {
        "params": params, "P_total": P_total, "B": B,
        "l": l, "D": D, "w_max_infinite": w_max_inf,
    }


def run_verification(
    h_ice: float = 0.6, sigma_c_mpa: float = 1.3,
    plate_a: float = 8.0, plate_b: float = 8.0,
    n_terms: int = 51, fem_nx: int = 41, fem_ny: int = 41,
) -> Dict:
    """Run full verification: analytical vs FEM."""
    params = ice_sheet_parameters(h_ice, sigma_c_mpa)
    sigma_f = 0.5 * params["sigma_c"]
    B = 10.0
    P_total = sigma_f * h_ice ** 2 * B

    l = characteristic_length(params["E"], h_ice, params["nu"], params["k"])
    D = flexural_rigidity(params["E"], h_ice, params["nu"])

    w_analytical_inf = hetenyi_infinite_plate(P_total, params["E"], h_ice, params["nu"], params["k"])

    w_finite = hetenyi_finite_plate(
        P_total, plate_a, plate_b, params["E"], h_ice, params["nu"], params["k"],
        n_terms=n_terms, nx=41, ny=41,
    )
    w_analytical_finite = w_finite["w_max"]

    w_fem = fem_plate_on_foundation(
        P_total, plate_a, plate_b, params["E"], h_ice, params["nu"], params["k"],
        nx=fem_nx, ny=fem_ny,
    )
    w_fem_max = w_fem["w_max"]

    error_fem_vs_analytical = abs(w_fem_max - w_analytical_finite) / w_analytical_finite * 100
    error_finite_vs_infinite = abs(w_analytical_finite - w_analytical_inf) / w_analytical_inf * 100

    gate_pass = error_fem_vs_analytical < 5.0

    return {
        "parameters": {
            "h_ice": h_ice, "sigma_c_mpa": sigma_c_mpa,
            "plate_a": plate_a, "plate_b": plate_b,
            "E": params["E"], "nu": params["nu"], "k": params["k"],
            "P_total": P_total, "B": B, "l": l, "D": D,
        },
        "analytical_infinite_plate": {"w_max_m": w_analytical_inf, "w_max_mm": w_analytical_inf * 1000},
        "analytical_finite_plate": {"w_max_m": w_analytical_finite, "w_max_mm": w_analytical_finite * 1000, "n_terms": n_terms},
        "fem_solution": {"w_max_m": w_fem_max, "w_max_mm": w_fem_max * 1000, "nx": fem_nx, "ny": fem_ny},
        "error_analysis": {"fem_vs_analytical_pct": error_fem_vs_analytical, "finite_vs_infinite_pct": error_finite_vs_infinite},
        "gate_F01": "PASS" if gate_pass else "FAIL",
        "analytical_max_deflection": w_analytical_finite,
        "fem_max_deflection": w_fem_max,
        "error_pct": error_fem_vs_analytical,
    }


# =============================================================================
# Part 4: Pytest Tests
# =============================================================================

class TestHetenyiAnalytical:
    def test_flexural_rigidity(self):
        E, h, nu = 5.0e9, 0.6, 0.33
        D = flexural_rigidity(E, h, nu)
        expected = 5.0e9 * 0.6**3 / (12.0 * (1.0 - 0.33**2))
        assert abs(D - expected) / expected < 1e-10

    def test_characteristic_length(self):
        E, h, nu, k = 5.0e9, 0.6, 0.33, 1025.0 * 9.81
        D = flexural_rigidity(E, h, nu)
        l = characteristic_length(E, h, nu, k)
        assert abs(l - (D/k)**0.25) / (D/k)**0.25 < 1e-10

    def test_infinite_plate_reference(self):
        P = 1e6; E, h, nu, k = 5.0e9, 0.6, 0.33, 1025.0*9.81
        D = flexural_rigidity(E, h, nu); l = characteristic_length(E, h, nu, k)
        w_max = hetenyi_infinite_plate(P, E, h, nu, k)
        assert abs(w_max - P*l**2/(8*D)) / (P*l**2/(8*D)) * 100 < 1.0

    def test_finite_plate_convergence(self):
        P = 1e6; E, h, nu = 5.0e9, 0.6, 0.33; k = 1025.0*9.81
        w_inf = hetenyi_infinite_plate(P, E, h, nu, k)
        w_f = hetenyi_finite_plate(P, 100, 100, E, h, nu, k, n_terms=31, nx=21, ny=21)
        assert abs(w_f["w_max"] - w_inf) / w_inf * 100 < 5.0

    def test_finite_plate_symmetry(self):
        P = 1e6; E, h, nu = 5.0e9, 0.6, 0.33; k = 1025.0*9.81
        r = hetenyi_finite_plate(P, 8, 8, E, h, nu, k, n_terms=21, nx=21, ny=21)
        assert np.max(np.abs(r["w"] - r["w"].T)) < 1e-10


class TestFEMSolver:
    def test_fem_symmetry(self):
        P = 1e6; E, h, nu = 5.0e9, 0.6, 0.33; k = 1025.0*9.81
        r = fem_plate_on_foundation(P, 8, 8, E, h, nu, k, nx=21, ny=21)
        assert np.max(np.abs(r["w"] - r["w"].T)) < 1e-8

    def test_fem_boundary_conditions(self):
        P = 1e6; E, h, nu = 5.0e9, 0.6, 0.33; k = 1025.0*9.81
        r = fem_plate_on_foundation(P, 8, 8, E, h, nu, k, nx=21, ny=21)
        w = r["w"]
        assert np.allclose(w[0,:], 0, atol=1e-12)
        assert np.allclose(w[-1,:], 0, atol=1e-12)
        assert np.allclose(w[:,0], 0, atol=1e-12)
        assert np.allclose(w[:,-1], 0, atol=1e-12)

    def test_fem_positive_deflection(self):
        P = 1e6; E, h, nu = 5.0e9, 0.6, 0.33; k = 1025.0*9.81
        r = fem_plate_on_foundation(P, 8, 8, E, h, nu, k, nx=21, ny=21)
        assert np.all(r["w"] >= -1e-12), f"Min deflection: {np.min(r['w']):.2e}"

    def test_fem_1d_beam_validation(self):
        """Validate x-direction biharmonic stencil against 1D beam analytical solution."""
        L, EI, q = 8.0, 1.01e8, 1e4
        nx, dx, ni = 41, L/40, 39
        A = np.zeros((ni, ni)); f = np.full(ni, q/EI)
        for ii in range(ni):
            A[ii, ii] = 6.0/dx**4
            if ii >= 1: A[ii, ii-1] = -4.0/dx**4
            if ii >= 2: A[ii, ii-2] = 1.0/dx**4
            elif ii == 0: A[ii, ii] += -1.0/dx**4
            if ii < ni-1: A[ii, ii+1] = -4.0/dx**4
            if ii < ni-2: A[ii, ii+2] = 1.0/dx**4
            elif ii == ni-1: A[ii, ii] += -1.0/dx**4
        w = np.linalg.solve(A, f)
        w_max_analytical = 5*q*L**4/(384*EI)
        assert abs(np.max(w) - w_max_analytical)/w_max_analytical*100 < 2.0


class TestVerification:
    def test_analytical_vs_fem_error(self):
        r = run_verification(h_ice=0.6, sigma_c_mpa=1.3, fem_nx=41, fem_ny=41)
        assert r["error_pct"] < 5.0, f"Error {r['error_pct']:.2f}% > 5%"

    def test_gate_f01_pass(self):
        r = run_verification(h_ice=0.6, sigma_c_mpa=1.3, fem_nx=41, fem_ny=41)
        assert r["gate_F01"] == "PASS"

    def test_reference_case_values(self):
        ref = compute_reference_case()
        assert 0.001 < ref["w_max_infinite"] < 1.0
        assert 5.0 < ref["l"] < 20.0


class TestArtifacts:
    def test_verification_json_structure(self):
        r = run_verification(h_ice=0.6, sigma_c_mpa=1.3, fem_nx=41, fem_ny=41)
        for key in ["analytical_max_deflection", "fem_max_deflection", "error_pct", "gate_F01"]:
            assert key in r
        assert r["gate_F01"] in ("PASS", "FAIL")


if __name__ == "__main__":
    result = run_verification()
    print("=== Hetényi Plate on Elastic Foundation — Verification ===")
    p = result["parameters"]
    print(f"Ice thickness: {p['h_ice']:.3f} m, Plate: {p['plate_a']}×{p['plate_b']} m")
    print(f"E={p['E']:.2e} Pa, k={p['k']:.2f} N/m³, l={p['l']:.4f} m, P={p['P_total']:.2e} N")
    a = result["analytical_infinite_plate"]
    print(f"Analytical (infinite): w_max = {a['w_max_mm']:.4f} mm")
    af = result["analytical_finite_plate"]
    print(f"Analytical (finite):   w_max = {af['w_max_mm']:.4f} mm")
    f = result["fem_solution"]
    print(f"FEM solution:          w_max = {f['w_max_mm']:.4f} mm")
    e = result["error_analysis"]
    print(f"FEM vs analytical: {e['fem_vs_analytical_pct']:.2f}%")
    print(f"Finite vs infinite: {e['finite_vs_infinite_pct']:.2f}%")
    print(f"F01 Gate: {result['gate_F01']}")
