#!/usr/bin/env python3
"""
BEM Sphere Acoustic Scattering Benchmark (Neumann formulation)

Rigid sphere of radius a=1 scattering a plane wave exp(ikx).
Analytical solution: Mie series (partial wave expansion).

Uses single-layer representation for exterior Neumann problem.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, "/srv/tensor/workspace/TensorBEM/src")
from tensorbem.geometry3d import icosphere


def compute_mie_coefficients(ka, n_max=50):
    j = np.zeros(n_max + 1, dtype=np.complex128)
    h = np.zeros(n_max + 1, dtype=np.complex128)
    j[0] = np.sin(ka) / ka
    j[1] = np.sin(ka) / ka**2 - np.cos(ka) / ka
    h[0] = np.exp(1j * ka) / (1j * ka)
    h[1] = -np.exp(1j * ka) / ka * (1 + 1j / ka)
    for n in range(2, n_max + 1):
        j[n] = (2 * n - 1) / ka * j[n - 1] - j[n - 2]
        h[n] = (2 * n - 1) / ka * h[n - 1] - h[n - 2]
    j_deriv = np.zeros(n_max + 1, dtype=np.complex128)
    h_deriv = np.zeros(n_max + 1, dtype=np.complex128)
    j_deriv[0] = -j[1]; h_deriv[0] = -h[1]
    for n in range(1, n_max + 1):
        j_deriv[n] = j[n - 1] - (n + 1) / ka * j[n]
        h_deriv[n] = h[n - 1] - (n + 1) / ka * h[n]
    return -j_deriv / h_deriv, h


def legendre_p_vectorized(n_max, x):
    P = np.zeros((n_max + 1, len(x)))
    P[0] = 1.0
    if n_max >= 1: P[1] = x
    for n in range(2, n_max + 1):
        P[n] = ((2 * n - 1) * x * P[n - 1] - (n - 1) * P[n - 2]) / n
    return P


def run_benchmark(subdivisions=2, ka=2.0, device="cpu"):
    t0 = time.time()
    mesh = icosphere(subdivisions, radius=1.0)
    n_panels = mesh.panel_count
    centroids = mesh.centroids
    normals = mesh.normals
    areas = mesh.areas
    k = ka
    n_max = max(50, int(ka + 10))

    print(f"Panels: {n_panels}, ka={ka}", file=sys.stderr)

    # Distance matrix
    diff = centroids[:, np.newaxis, :] - centroids[np.newaxis, :, :]
    r = np.linalg.norm(diff, axis=2)
    np.fill_diagonal(r, 1.0)

    # Green's function
    G = np.exp(1j * k * r) / (4 * np.pi * r)

    # Single-layer matrix V[i,j] = G(x_i, y_j) * A_j
    V = G * areas[np.newaxis, :]

    # Adjoint double-layer K'[i,j] = ∂G(x_i,y_j)/∂n_{x_i} * A_j
    # ∂G/∂n_x = -(ik - 1/r) * G * (r·n_x) / r  (note the minus sign for exterior)
    r_dot_nx = np.einsum('ijk,ik->ij', diff, normals)
    K_adj = -(1j * k - 1.0 / r) * G * r_dot_nx / r * areas[np.newaxis, :]
    np.fill_diagonal(K_adj, 0.5)  # self-term for exterior

    # Incident wave and its normal derivative
    phi_inc = np.exp(1j * k * centroids[:, 0])
    dphi_inc_dn = 1j * k * normals[:, 0] * phi_inc

    # Exterior Neumann: (1/2 I + K') σ = -∂φ_inc/∂n
    # Use CFIE: (1/2 I + K' + iη V) σ = -∂φ_inc/∂n
    eta = k
    A_mat = 0.5 * np.eye(n_panels, dtype=np.complex128) + K_adj + 1j * eta * V
    sigma = np.linalg.solve(A_mat, -dphi_inc_dn)

    # Scattered potential: φ_scat = V σ
    p_scat_surface = V @ sigma

    # Analytical
    A_mie, h_mie = compute_mie_coefficients(ka, n_max)
    cos_thetas = centroids[:, 0] / np.linalg.norm(centroids, axis=1)
    P = legendre_p_vectorized(n_max, cos_thetas)
    n_arr = np.arange(n_max + 1)
    coeffs = (2 * n_arr + 1) * (1j) ** n_arr * A_mie * h_mie
    p_scat_ana = coeffs @ P

    # Errors
    p_num_mag = np.abs(p_scat_surface)
    p_ana_mag = np.abs(p_scat_ana)
    error_l2 = float(np.sqrt(np.sum((p_num_mag - p_ana_mag) ** 2) / (np.sum(p_ana_mag ** 2) + 1e-30)))

    spl_num = 20 * np.log10(p_num_mag + 1e-30)
    spl_ana = 20 * np.log10(p_ana_mag + 1e-30)
    spl_error = float(np.sqrt(np.mean((spl_num - spl_ana) ** 2)))

    elapsed = time.time() - t0

    result = {
        "benchmark": "sphere-acoustic-scattering",
        "subdivisions": subdivisions,
        "panels": n_panels,
        "ka": ka,
        "elapsed_s": round(elapsed, 1),
        "errors": {
            "pressure_l2_relative": round(error_l2, 6),
            "spl_error_db": round(spl_error, 4),
        },
        "passed": spl_error <= 5.0,
        "device": device,
    }
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--subdivisions", type=int, default=2)
    parser.add_argument("--ka", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = run_benchmark(args.subdivisions, args.ka)
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    sys.exit(0 if result["passed"] else 2)
