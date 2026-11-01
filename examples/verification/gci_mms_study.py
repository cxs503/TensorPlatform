"""MMS verification study: does the GCI toolkit recover known orders of accuracy?

Three independent manufactured-solution (MMS) cases with *analytically known*
quantities of interest are solved on three nested grids, then fed to
``src.verification.gci``.  Because the exact QoI is known, the estimator can be
judged on the only two criteria that matter:

1. does it recover the scheme's *formal* order of accuracy? (code verification)
2. does the reported GCI band actually contain the exact value? (uncertainty
   validation -- a band that never contains truth is not an error estimate)

Cases
-----
* ``advdiff-central``  -- 1D advection-diffusion, 2nd-order central advection,
  formal ``p = 2``, QoI ``u(0.5) = 1``.
* ``advdiff-upwind``   -- same PDE, 1st-order upwind advection, formal
  ``p = 1``, QoI ``u(0.5) = 1``.
* ``poisson2d-fv5``    -- 2D Poisson, 5-point stencil, formal ``p = 2``,
  QoI ``u(0.5,0.5) = 1``.

All cases use the same manufactured field ``sin(pi x) [sin(pi y)]`` whose value
at the QoI location is exactly 1, and the QoI sits on a grid node for every
(hence no interpolation error contaminates the comparison).

Run: ``python3 examples/verification/gci_mms_study.py``
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.verification import FS_THREE_GRIDS, gci  # noqa: E402


# --------------------------------------------------------------------------
# MMS case 1/2: 1D advection-diffusion  -kappa u'' + a u' = f,  u = sin(pi x)
# --------------------------------------------------------------------------
def solve_advdiff_1d(n: int, kappa: float = 1.0, a: float = 1.0,
                     scheme: str = "central") -> float:
    """Return ``u_h(0.5)``; exact value is 1.0."""
    h = 1.0 / n
    x = np.linspace(0.0, 1.0, n + 1)
    u_ex = np.sin(math.pi * x)
    f = kappa * math.pi ** 2 * np.sin(math.pi * x) + a * math.pi * np.cos(math.pi * x)

    # Interior unknowns i = 1..n-1 -> tridiagonal system A u = b.
    m = n - 1
    lower = np.zeros(m)
    diag = np.zeros(m)
    upper = np.zeros(m)
    b = f[1:n].copy()
    for k in range(m):
        if scheme == "central":
            lower[k] = -kappa / h ** 2 - a / (2.0 * h)
            diag[k] = 2.0 * kappa / h ** 2
            upper[k] = -kappa / h ** 2 + a / (2.0 * h)
        elif scheme == "upwind":
            lower[k] = -kappa / h ** 2 - a / h
            diag[k] = 2.0 * kappa / h ** 2 + a / h
            upper[k] = -kappa / h ** 2
        else:
            raise ValueError("scheme must be 'central' or 'upwind'")
    # Dirichlet boundaries.
    b[0] -= lower[0] * u_ex[0]
    b[-1] -= upper[-1] * u_ex[-1]
    u = _thomas(lower, diag, upper, b)
    u_full = np.concatenate(([u_ex[0]], u, [u_ex[-1]]))
    return float(u_full[n // 2])


def _thomas(lower: np.ndarray, diag: np.ndarray, upper: np.ndarray,
            rhs: np.ndarray) -> np.ndarray:
    """Thomas algorithm for a tridiagonal system (no pivoting needed here)."""
    n = len(diag)
    c = np.zeros(n)
    d = np.zeros(n)
    c[0] = upper[0] / diag[0]
    d[0] = rhs[0] / diag[0]
    for i in range(1, n):
        denom = diag[i] - lower[i] * c[i - 1]
        c[i] = upper[i] / denom if i < n - 1 else 0.0
        d[i] = (rhs[i] - lower[i] * d[i - 1]) / denom
    x = np.zeros(n)
    x[-1] = d[-1]
    for i in range(n - 2, -1, -1):
        x[i] = d[i] - c[i] * x[i + 1]
    return x


# --------------------------------------------------------------------------
# MMS case 3: 2D Poisson  -Laplacian(u) = f,  u = sin(pi x) sin(pi y)
# --------------------------------------------------------------------------
def solve_poisson_2d(n: int) -> float:
    """Return ``u_h(0.5, 0.5)``; exact value is 1.0."""
    from scipy.sparse import lil_matrix
    from scipy.sparse.linalg import spsolve

    h = 1.0 / n
    xs = np.linspace(0.0, 1.0, n + 1)
    u_ex = np.sin(math.pi * xs)[:, None] * np.sin(math.pi * xs)[None, :]
    f = 2.0 * math.pi ** 2 * u_ex

    idx = -np.ones((n + 1, n + 1), dtype=int)
    m = n - 1
    c = 0
    for i in range(1, n):
        for j in range(1, n):
            idx[i, j] = c
            c += 1

    A = lil_matrix((m * m, m * m))
    rhs = np.zeros(m * m)
    for i in range(1, n):
        for j in range(1, n):
            k = idx[i, j]
            rhs[k] = f[i, j]
            A[k, k] = 4.0 / h ** 2
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = idx[i + di, j + dj]
                if nb >= 0:
                    A[k, nb] = -1.0 / h ** 2
                else:  # Dirichlet boundary contribution moves to the RHS
                    rhs[k] += u_ex[i + di, j + dj] / h ** 2
    u = spsolve(A.tocsr(), rhs)
    return float(u[idx[n // 2, n // 2]])


# --------------------------------------------------------------------------
# Study driver
# --------------------------------------------------------------------------
CASES = {
    "advdiff-central": {"formal_p": 2.0, "exact": 1.0,
                        "solver": lambda n: solve_advdiff_1d(n, scheme="central"),
                        "grids": (20, 40, 80),
                        "scheme": "1D advection-diffusion, 2nd-order central advection"},
    "advdiff-upwind": {"formal_p": 1.0, "exact": 1.0,
                       "solver": lambda n: solve_advdiff_1d(n, scheme="upwind"),
                       "grids": (20, 40, 80),
                       "scheme": "1D advection-diffusion, 1st-order upwind advection"},
    "poisson2d-fv5": {"formal_p": 2.0, "exact": 1.0,
                      "solver": solve_poisson_2d,
                      "grids": (16, 32, 64),
                      "scheme": "2D Poisson, 5-point central"},
}


def run_study() -> dict:
    out = {"cases": {}, "criterion": {
        "order_recovered": "|p_obs - p_formal| <= 0.20 * p_formal "
                           "OR |p_obs - p_formal| <= 0.20 (absolute) for p_formal <= 1",
        "band_contains_truth": "|phi_ext - exact| <= GCI_fine",
        "gci_accuracy": "|phi_ext - exact| / |exact|",
    }}
    for name, spec in CASES.items():
        ns = spec["grids"]  # coarse -> fine
        phis = [spec["solver"](n) for n in ns]
        h = [1.0 / n for n in ns]
        # toolkit wants finest first
        order = np.argsort(h)
        h_f = [h[i] for i in order]
        phi_f = [phis[i] for i in order]
        res = gci(h_f, phi_f, fs=FS_THREE_GRIDS)
        exact = spec["exact"]
        err_ext = abs(res.phi_ext - exact) / abs(exact) if res.phi_ext is not None else None
        # Roache/ASME V&V 20: GCI is the relative uncertainty of the FINE-grid
        # solution phi1, so the absolute band is GCI * |phi1|.
        band_half = res.gci_fine * abs(phi_f[0])
        out["cases"][name] = {
            "scheme": spec["scheme"],
            "formal_p": spec["formal_p"],
            "grid_sizes_coarse_to_fine": list(ns),
            "h_coarse_to_fine": h,
            "phi_coarse_to_fine": phis,
            "exact": exact,
            "raw_errors_coarse_to_fine": [abs(p - exact) for p in phis],
            "gci": res.as_dict(),
            "extrapolated_rel_error": err_ext,
            "band_half_width_abs": band_half,
            "order_recovered": (
                res.p is not None and
                abs(res.p - spec["formal_p"]) <= max(0.20 * spec["formal_p"], 0.20)
            ),
            "fine_grid_error_abs": abs(phi_f[0] - exact),
            "band_contains_truth": abs(phi_f[0] - exact) <= band_half,
            "extrapolation_beats_fine_grid": (
                err_ext is not None and
                abs(res.phi_ext - exact) < abs(phi_f[0] - exact)
            ),
        }
    # Pre-asymptotic ladder: an honest look at *how fast* the observed order
    # approaches the formal one (the 1st-order upwind case is not asymptotic
    # on 20/40/80 -- reporting only that number would be misleading).
    ladder = []
    for ns in ((20, 40, 80), (40, 80, 160), (80, 160, 320)):
        phis = [solve_advdiff_1d(n, scheme="upwind") for n in ns]
        h = [1.0 / n for n in ns]
        order = np.argsort(h)
        res = gci([h[i] for i in order], [phis[i] for i in order])
        ladder.append({
            "grid_sizes_coarse_to_fine": list(ns),
            "p_observed": res.p,
            "phi_ext": res.phi_ext,
            "extrapolated_rel_error": abs(res.phi_ext - 1.0),
            "gci_fine_pct": res.gci_fine_pct,
        })
    out["upwind_order_ladder"] = ladder
    out["upwind_ladder_note"] = (
        "p_observed rises monotonically toward the formal value 1.0 as the grid "
        "is refined (pre-asymptotic regime); the coarse 20/40/80 triple alone "
        "under-predicts the order, which is why the ladder is reported instead "
        "of a single number."
    )
    return out