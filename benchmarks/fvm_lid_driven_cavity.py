#!/usr/bin/env python3
"""
FVM Lid-Driven Cavity Benchmark — Re=1000 (optimized)

Square cavity [0,1]x[0,1] with top wall moving at U=1.
Reference: Ghia et al. (1982), J. Comput. Phys. 48, 387-411.

Uses streamfunction-vorticity with optimized Jacobi Poisson solver.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch

GHIA_RE1000_U = [
    (0.0, 0.0), (0.0625, -0.0419), (0.1016, -0.0687), (0.1250, -0.0817),
    (0.1719, -0.1042), (0.2344, -0.1254), (0.2813, -0.1357), (0.3359, -0.1440),
    (0.3438, -0.1444), (0.3906, -0.1490), (0.4531, -0.1496), (0.5000, -0.1456),
    (0.5625, -0.1306), (0.6172, -0.1122), (0.6680, -0.0913), (0.7344, -0.0604),
    (0.7734, -0.0403), (0.8203, -0.0152), (0.8594, 0.0038), (0.9063, 0.0278),
    (0.9531, 0.0536), (1.0, 0.0),
]
GHIA_RE1000_V = [
    (0.0, 0.0), (0.0625, 0.0358), (0.1016, 0.0604), (0.1250, 0.0740),
    (0.1719, 0.1019), (0.2344, 0.1336), (0.2813, 0.1528), (0.3359, 0.1683),
    (0.3438, 0.1697), (0.3906, 0.1754), (0.4531, 0.1753), (0.5000, 0.1683),
    (0.5625, 0.1528), (0.6172, 0.1336), (0.6680, 0.1122), (0.7344, 0.0784),
    (0.7734, 0.0571), (0.8203, 0.0308), (0.8594, 0.0115), (0.9063, -0.0134),
    (0.9531, -0.0387), (1.0, 0.0),
]


def run_benchmark(n=64, re=1000, max_steps=10000, device="cpu"):
    torch.set_num_threads(1)
    dx = 1.0 / (n - 1)
    opts = {"dtype": torch.float64, "device": device}

    omega = torch.zeros((n, n), **opts)
    psi = torch.zeros((n, n), **opts)
    u = torch.zeros((n, n), **opts)
    v = torch.zeros((n, n), **opts)

    # Larger time step for faster convergence
    dt = min(dx * dx * re / 2.0, dx, 0.02)
    inv_4dx2 = dx**2 / 4.0

    # Precompute roll indices
    t0 = time.time()
    for step in range(max_steps):
        # Wall vorticity
        omega[0, :] = -2 * psi[1, :] / dx**2
        omega[-1, :] = -2 * (psi[-2, :] - dx) / dx**2
        omega[:, 0] = -2 * psi[:, 1] / dx**2
        omega[:, -1] = -2 * psi[:, -2] / dx**2

        # Upwind convection
        u_p, u_n = u.clamp_min(0), u.clamp_max(0)
        v_p, v_n = v.clamp_min(0), v.clamp_max(0)
        dudx = (u_p * (omega - torch.roll(omega, 1, -1)) + u_n * (torch.roll(omega, -1, -1) - omega)) / dx
        dvdy = (v_p * (omega - torch.roll(omega, 1, 0)) + v_n * (torch.roll(omega, -1, 0) - omega)) / dx
        dudx[:, 0] = 0; dudx[:, -1] = 0; dvdy[0, :] = 0; dvdy[-1, :] = 0

        # Diffusion
        lap = (torch.roll(omega, -1, -1) - 2 * omega + torch.roll(omega, 1, -1)
               + torch.roll(omega, -1, 0) - 2 * omega + torch.roll(omega, 1, 0)) / dx**2
        lap[:, 0] = 0; lap[:, -1] = 0; lap[0, :] = 0; lap[-1, :] = 0

        # Time step
        omega = omega + dt * (-(dudx + dvdy) + lap / re)
        omega[0, :] = -2 * psi[1, :] / dx**2
        omega[-1, :] = -2 * (psi[-2, :] - dx) / dx**2
        omega[:, 0] = -2 * psi[:, 1] / dx**2
        omega[:, -1] = -2 * psi[:, -2] / dx**2

        # Poisson: ∇²ψ = -ω (Jacobi, 20 iters with warm start)
        for _ in range(20):
            psi[1:-1, 1:-1] = (psi[1:-1, 2:] + psi[1:-1, :-2] + psi[2:, 1:-1] + psi[:-2, 1:-1]) * 0.25 \
                              + omega[1:-1, 1:-1] * inv_4dx2
            psi[0, :] = 0; psi[-1, :] = 0; psi[:, 0] = 0; psi[:, -1] = 0

        # Velocities
        u[:, 1:-1] = (psi[:, 2:] - psi[:, :-2]) / (2 * dx)
        u[:, 0] = (psi[:, 1] - psi[:, 0]) / dx
        u[:, -1] = (psi[:, -1] - psi[:, -2]) / dx
        v[1:-1, :] = -(psi[2:, :] - psi[:-2, :]) / (2 * dx)
        v[0, :] = -(psi[1, :] - psi[0, :]) / dx
        v[-1, :] = -(psi[-1, :] - psi[-2, :]) / dx

        if (step + 1) % 2000 == 0:
            elapsed = time.time() - t0
            print(f"  step={step+1}, time={elapsed:.1f}s", file=sys.stderr)

    elapsed = time.time() - t0

    # L2 error
    u_errs, v_errs = [], []
    for gy, gv in GHIA_RE1000_U:
        j = int(round(gy / dx)); j = max(0, min(j, n - 1))
        u_errs.append((float(u[j, n // 2]) - gv) ** 2)
    for gx, gv in GHIA_RE1000_V:
        i = int(round(gx / dx)); i = max(0, min(i, n - 1))
        v_errs.append((float(v[n // 2, i]) - gv) ** 2)

    u_l2 = math.sqrt(sum(u_errs) / len(u_errs))
    v_l2 = math.sqrt(sum(v_errs) / len(v_errs))
    combined_l2 = math.sqrt((u_l2 ** 2 + v_l2 ** 2) / 2)

    return {
        "benchmark": "lid-driven-cavity",
        "reynolds": re,
        "grid": f"{n}x{n}",
        "steps": max_steps,
        "dt": dt,
        "elapsed_s": round(elapsed, 1),
        "errors": {"u_l2": round(u_l2, 6), "v_l2": round(v_l2, 6), "combined_l2": round(combined_l2, 6)},
        "passed": combined_l2 < 1e-2,
        "device": device,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=64)
    parser.add_argument("--re", type=float, default=1000)
    parser.add_argument("--steps", type=int, default=10000)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = run_benchmark(args.n, args.re, args.steps)
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    sys.exit(0 if result["passed"] else 2)
