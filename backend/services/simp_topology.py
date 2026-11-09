"""SIMP (Solid Isotropic Material with Penalization) topology optimization.

Reference implementation for the TensorPlatform structural optimization domain.
Implements the classical SIMP method for compliance minimization under volume
constraint, using a 2D finite element discretization.

Physics
-------
Minimize compliance C = U^T K U = F^T U
subject to:
    V(x) / V_0 <= f   (volume fraction constraint)
    0 < x_min <= x_e <= 1  (design variable bounds)

Where:
    K = sum_e x_e^p K_e  (penalized stiffness, p = 3 typically)
    U = K^{-1} F         (equilibrium)
    x_e = relative density of element e

The penalization p > 1 drives the solution toward a 0-1 (black-white) design.

Verification
------------
For a standard cantilever beam (L=60, H=20, 60x20 mesh, f=0.5, p=3):
- The compliance should decrease monotonically with iterations
- The final volume fraction should be <= f * V_0
- The solution should show a clear load path (Michell structure)

This is a reference implementation for platform integration testing.
Production use should couple with TensorFEM for the FE solver.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np


@dataclass
class SIMPResult:
    """Result of a SIMP optimization."""
    density: np.ndarray           # Final density field (nelx x nely)
    compliance_history: List[float]  # Compliance at each iteration
    volume_history: List[float]     # Volume fraction at each iteration
    final_compliance: float
    final_volume_fraction: float
    n_iterations: int
    converged: bool


def _ke_element(E: float = 1.0, nu: float = 0.3) -> np.ndarray:
    """Element stiffness matrix for a 4-node quad (plane stress).

    Uses the standard bilinear quadrilateral with unit element size.
    Node ordering: counterclockwise from bottom-left.
    """
    k = np.array([
        1/2 - nu/6, 1/8 + nu/8, -1/4 - nu/12, -1/8 + 3*nu/8,
        -1/4 + nu/12, -1/8 - nu/8, nu/6, 1/8 - 3*nu/8
    ])
    KE = E / (1 - nu**2) * np.array([
        [k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7]],
        [k[1], k[0], k[7], k[6], k[5], k[4], k[3], k[2]],
        [k[2], k[7], k[0], k[5], k[6], k[3], k[4], k[1]],
        [k[3], k[6], k[5], k[0], k[7], k[2], k[1], k[4]],
        [k[4], k[5], k[6], k[7], k[0], k[1], k[2], k[3]],
        [k[5], k[4], k[3], k[2], k[1], k[0], k[7], k[6]],
        [k[6], k[3], k[4], k[1], k[2], k[7], k[0], k[5]],
        [k[7], k[2], k[1], k[4], k[3], k[6], k[5], k[0]],
    ])
    return KE


def simp_2d(
    nelx: int,
    nely: int,
    volfrac: float,
    penalty: float = 3.0,
    rmin: float = 1.5,
    max_iter: int = 100,
    tol: float = 1e-3,
    E: float = 1.0,
    nu: float = 0.3,
    bc_type: str = "cantilever",
) -> SIMPResult:
    """Run SIMP topology optimization on a 2D domain.

    Parameters
    ----------
    nelx, nely : int
        Number of elements in x and y directions.
    volfrac : float
        Volume fraction constraint (0 < volfrac < 1).
    penalty : float
        SIMP penalization power (typically 3).
    rmin : float
        Filter radius for mesh-independence.
    max_iter : int
        Maximum number of OC iterations.
    tol : float
        Convergence tolerance on design change.
    E : float
        Young's modulus of solid material.
    nu : float
        Poisson's ratio.
    bc_type : str
        Boundary condition type: "cantilever" or "mbb_beam".

    Returns
    -------
    SIMPResult
        Optimization result with density field and convergence history.
    """
    if not (0.0 < volfrac < 1.0):
        raise ValueError(f"volfrac must be in (0, 1), got {volfrac}")
    if penalty < 1.0:
        raise ValueError(f"penalty must be >= 1, got {penalty}")

    n_dof = 2 * (nelx + 1) * (nely + 1)
    KE = _ke_element(E, nu)
    x_min = 1e-3  # minimum density to avoid singularity

    # Initialize design variables
    x = np.full((nelx, nely), volfrac)

    # Build element connectivity and DOF mapping
    # Node numbering: node(i,j) = i*(nely+1) + j
    # Element nodes (counterclockwise from bottom-left):
    #   n1=(elx,ely), n2=(elx,ely+1), n3=(elx+1,ely+1), n4=(elx+1,ely)
    edof = np.zeros((nelx * nely, 8), dtype=int)
    for elx in range(nelx):
        for ely in range(nely):
            el_idx = elx * nely + ely
            n1 = elx * (nely + 1) + ely
            n2 = elx * (nely + 1) + ely + 1
            n3 = (elx + 1) * (nely + 1) + ely + 1
            n4 = (elx + 1) * (nely + 1) + ely
            edof[el_idx, :] = [
                2*n1, 2*n1+1, 2*n2, 2*n2+1,
                2*n3, 2*n3+1, 2*n4, 2*n4+1
            ]

    # Build filter matrix (density filter for mesh independence)
    iH = np.zeros(nelx * nely * int((2*rmin+1)**2))
    jH = np.zeros_like(iH)
    sH = np.zeros_like(iH)
    idx = 0
    for elx in range(nelx):
        for ely in range(nely):
            e1 = elx * nely + ely
            for kx in range(max(0, elx - int(rmin)), min(nelx, elx + int(rmin) + 1)):
                for ky in range(max(0, ely - int(rmin)), min(nely, ely + int(rmin) + 1)):
                    e2 = kx * nely + ky
                    dist = math.sqrt((elx - kx)**2 + (ely - ky)**2)
                    if dist < rmin:
                        iH[idx] = e1
                        jH[idx] = e2
                        sH[idx] = max(0, rmin - dist)
                        idx += 1
    iH = iH[:idx]
    jH = jH[:idx]
    sH = sH[:idx]

    # Build sparse filter matrix
    from scipy import sparse
    H = sparse.coo_matrix((sH, (iH, jH)), shape=(nelx*nely, nelx*nely)).tocsr()
    Hs = np.array(H.sum(axis=1)).flatten()

    # Apply boundary conditions and load
    f = np.zeros(n_dof)
    fixed_dofs = []

    if bc_type == "cantilever":
        # Cantilever: fixed on left edge (x=0), load at right edge mid-height
        # Left edge nodes: (0, ely) for ely=0..nely
        for ely in range(nely + 1):
            node = ely  # node(0, ely) = 0*(nely+1) + ely
            fixed_dofs.extend([2 * node, 2 * node + 1])
        # Point load at mid-right: node(nelx, nely//2)
        load_node = nelx * (nely + 1) + nely // 2
        f[2 * load_node + 1] = -1.0
    elif bc_type == "mbb_beam":
        # MBB beam (simply supported with symmetric load):
        # - Pin at bottom-left: node(0, 0) -> x and y fixed
        # - Roller at bottom-right: node(nelx, 0) -> y fixed only
        # - Downward load at top-center: node(nelx//2, nely)
        
        # Pin at bottom-left
        node_pin = 0  # node(0, 0)
        fixed_dofs.extend([2 * node_pin, 2 * node_pin + 1])
        
        # Roller at bottom-right
        node_roller = nelx * (nely + 1)  # node(nelx, 0)
        fixed_dofs.append(2 * node_roller + 1)  # y only
        
        # Load at top center
        load_node = (nelx // 2) * (nely + 1) + nely
        f[2 * load_node + 1] = -1.0
    else:
        raise ValueError(f"Unknown bc_type: {bc_type}")

    fixed_dofs = np.array(sorted(set(fixed_dofs)), dtype=int)
    free_dofs = np.setdiff1d(np.arange(n_dof), fixed_dofs)

    # Precompute element indices for assembly
    n_elem = nelx * nely

    compliance_history = []
    volume_history = []
    change_history = []

    for iteration in range(max_iter):
        # Assemble global stiffness matrix
        K_data = np.zeros(n_elem * 64)
        K_rows = np.zeros(n_elem * 64, dtype=int)
        K_cols = np.zeros(n_elem * 64, dtype=int)

        for el_idx in range(n_elem):
            elx = el_idx // nely
            ely = el_idx % nely
            # Penalized element stiffness
            x_pen = x_min + (x[elx, ely] ** penalty) * (1 - x_min)
            ke_scaled = x_pen * KE
            dofs = edof[el_idx]
            for i in range(8):
                for j in range(8):
                    idx_k = el_idx * 64 + i * 8 + j
                    K_data[idx_k] = ke_scaled[i, j]
                    K_rows[idx_k] = dofs[i]
                    K_cols[idx_k] = dofs[j]

        K = sparse.coo_matrix((K_data, (K_rows, K_cols)), shape=(n_dof, n_dof)).tocsr()

        # Solve
        K_ff = K[free_dofs, :][:, free_dofs]
        f_f = f[free_dofs]

        try:
            from scipy.sparse.linalg import spsolve
            u_f = spsolve(K_ff.tocsc(), f_f)
        except Exception:
            # Fallback to dense solve for small problems
            u_f = np.linalg.solve(K_ff.toarray(), f_f)

        u = np.zeros(n_dof)
        u[free_dofs] = u_f

        # Compute compliance and sensitivities
        compliance = float(f_f @ u_f)
        volume_frac = float(x.mean())

        compliance_history.append(compliance)
        volume_history.append(volume_frac)

        # Element sensitivities (dc/dx < 0 for compliance minimization)
        dc = np.zeros((nelx, nely))
        for el_idx in range(n_elem):
            elx = el_idx // nely
            ely = el_idx % nely
            dofs = edof[el_idx]
            ue = u[dofs]
            # dc/dx = -p * x^(p-1) * (1-x_min) * u^T K_e u
            dc[elx, ely] = -penalty * x[elx, ely] ** (penalty - 1) * (1 - x_min) * (ue @ KE @ ue)

        # Apply filter to sensitivities
        dc_flat = dc.flatten()
        x_flat = x.flatten()
        dc_filtered = np.array(H @ (dc_flat * x_flat / Hs)).flatten()
        dc = dc_filtered.reshape(nelx, nely) / np.maximum(x, 1e-6)

        # Ensure sensitivities are negative (compliance decreases with more material)
        dc = np.minimum(dc, -1e-30)

        # Optimality criteria update (Sigmund's algorithm)
        l1, l2 = 0.0, 1e9
        move = 0.2
        for _oc_iter in range(50):  # max OC bisection iterations
            lmid = 0.5 * (l1 + l2)
            if lmid < 1e-30:
                lmid = 1e-30
            # OC update: x_new = x * sqrt(-dc / lambda)
            update = np.sqrt(np.maximum(-dc / lmid, 0.0))
            x_new = x * update
            # Apply move limit
            x_new = np.maximum(x_new, np.maximum(x_min, x - move))
            x_new = np.minimum(x_new, np.minimum(1.0, x + move))
            x_new = np.clip(x_new, x_min, 1.0)

            if x_new.mean() > volfrac + 1e-6:
                l1 = lmid
            else:
                l2 = lmid
            if (l2 - l1) / max(l1 + l2, 1e-30) < 1e-4:
                break

        change = float(np.max(np.abs(x_new - x)))
        change_history.append(change)
        x = x_new

        # Check convergence
        if change < tol and iteration > 10:
            break

    return SIMPResult(
        density=x,
        compliance_history=compliance_history,
        volume_history=volume_history,
        final_compliance=compliance_history[-1],
        final_volume_fraction=volume_history[-1],
        n_iterations=len(compliance_history),
        converged=change_history[-1] < tol if change_history else False,
    )


def validate_simp_result(result: SIMPResult, volfrac: float) -> Dict[str, object]:
    """Validate SIMP optimization result.

    Checks:
    1. Compliance monotonically decreases (with small tolerance)
    2. Final volume fraction <= volfrac + tolerance
    3. Density values in valid range [x_min, 1]
    4. No NaN or Inf values
    """
    checks = {}

    # Check 1: Compliance should generally decrease
    c = np.array(result.compliance_history)
    if len(c) > 1:
        # Allow small increases due to filtering
        n_increases = np.sum(np.diff(c) > 0.01 * c[:-1])
        checks["compliance_monotone"] = bool(n_increases < len(c) * 0.1)

    # Check 2: Volume fraction
    checks["volume_constraint"] = result.final_volume_fraction <= volfrac + 0.05

    # Check 3: Density range
    checks["density_range"] = bool(np.all(result.density >= 0) and np.all(result.density <= 1.0))

    # Check 4: No NaN/Inf
    checks["no_nan_inf"] = bool(
        not np.any(np.isnan(result.density)) and
        not np.any(np.isinf(result.density)) and
        not np.any(np.isnan(result.compliance_history))
    )

    # Check 5: Convergence
    checks["converged"] = result.converged

    checks["all_pass"] = all(checks.values())
    return checks
