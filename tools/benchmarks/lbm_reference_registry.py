"""Reference-value registry for independent re-checking of LBM benchmark cases.

Purpose
-------
Platform-wide recurring debt ("质检独立性 / cross-family 评审可终局", 9 篇报告复发):
a benchmark case usually judges itself — the pass/fail gate is computed by the same
script that produced the number.  This registry pins *independent* ground truth so a
second, different code (``tools/benchmarks/independent_recheck.py``) can re-derive the
measured quantity from the raw field / history and re-judge the case.

Every entry carries explicit provenance (paper + equation/table + how it was obtained).
Values are transcribed from primary literature, not from the case result files, EXCEPT
where a field is explicitly marked ``transcription='case-provided digitisation'`` — such
entries are flagged ``independence='baseline-audit-only'`` and are never promoted to
"independent" evidence on their own.

Independence tiers used downstream
----------------------------------
``field-recompute``   : the raw field (npz/csv) is re-post-processed by this repo's own
                        metric implementation -> strongest tier.
``error-recompute``   : scalars reported by the case are re-combined into the metric
                        (catches arithmetic/denominator/threshold errors only).
``baseline-audit``    : the analytic/literature baseline itself is re-derived from first
                        principles (catches a wrong baseline, not a wrong solver).
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------------------
# Provenance helpers
# --------------------------------------------------------------------------------------
TIER_FIELD = "field-recompute"
TIER_ERROR = "error-recompute"
TIER_BASELINE = "baseline-audit"

#: Platform acceptance standard: >15% is unacceptable, <3% is the target.
TOL_TARGET = 0.03
TOL_ACCEPTABLE = 0.15


def rel_error(value: float, reference: float) -> float:
    """Symmetric-free relative error |value-reference|/|reference| (reference != 0)."""
    if reference == 0.0:
        raise ValueError("reference must be non-zero for relative error")
    return abs(value - reference) / abs(reference)


def classify(err: float, tol_target: float = TOL_TARGET,
             tol_ok: float = TOL_ACCEPTABLE) -> str:
    """Map a relative error to the platform verdict vocabulary."""
    if math.isnan(err):
        return "INDETERMINATE"
    if err <= tol_target:
        return "PASS"
    if err <= tol_ok:
        return "MARGINAL"
    return "FAIL"


# --------------------------------------------------------------------------------------
# 1. Lid-driven cavity — 2D Ghia et al. (1982) and 3D Ku, Hirsh & Taylor (1987)
# --------------------------------------------------------------------------------------
#: u-velocity on the vertical centreline x = 0.5 (Ghia, Ghia & Shin 1982,
#: J. Comput. Phys. 48:387, Table I "u-velocity along vertical centerline").
#: Rows: (y/H, u/u_lid).  Reproduced here as the standard 17-point table.
GHIA_U_CENTERLINE = {
    100: [
        (1.0000, 1.00000), (0.9766, 0.84123), (0.9688, 0.78871), (0.9609, 0.73722),
        (0.9531, 0.68717), (0.8516, 0.23151), (0.7344, 0.00332), (0.6172, -0.13641),
        (0.5000, -0.20581), (0.4531, -0.21090), (0.2813, -0.15662), (0.1719, -0.10150),
        (0.1016, -0.06434), (0.0703, -0.04775), (0.0625, -0.04192), (0.0547, -0.03717),
        (0.0000, 0.00000),
    ],
    400: [
        (1.0000, 1.00000), (0.9766, 0.75837), (0.9688, 0.68439), (0.9609, 0.61756),
        (0.9531, 0.55892), (0.8516, 0.29093), (0.7344, 0.16256), (0.6172, 0.02135),
        (0.5000, -0.11477), (0.4531, -0.17119), (0.2813, -0.32726), (0.1719, -0.24299),
        (0.1016, -0.14612), (0.0703, -0.10338), (0.0625, -0.09266), (0.0547, -0.08186),
        (0.0000, 0.00000),
    ],
    1000: [
        (1.0000, 1.00000), (0.9766, 0.65928), (0.9688, 0.57492), (0.9609, 0.51117),
        (0.9531, 0.46604), (0.8516, 0.33304), (0.7344, 0.18719), (0.6172, 0.05702),
        (0.5000, -0.06080), (0.4531, -0.10648), (0.2813, -0.27805), (0.1719, -0.38289),
        (0.1016, -0.29730), (0.0703, -0.22220), (0.0625, -0.20196), (0.0547, -0.18109),
        (0.0000, 0.00000),
    ],
}

GHIA_PROVENANCE = {
    "paper": "Ghia, Ghia & Shin 1982, J. Comput. Phys. 48:387-411",
    "table": "Table I/II (u along vertical centreline x=0.5), Re=100/400/1000",
    "mesh": "129x129 multigrid, u_lid=1, H=1",
    "note": "standard 17-point table; u_min(Re=1000) = -0.38289 at y=0.1719",
}

#: 3D cubic cavity, Re=1000: min of u on the vertical centreline at (x,z)=(0.5,0.5).
#: Ku, Hirsh & Taylor 1987, J. Comput. Phys. 70:439-462 (Chebyshev spectral).
KU_1987_3D = {
    "u_min": -0.2751,
    "provenance": "Ku, Hirsh & Taylor 1987, J. Comput. Phys. 70:439, Re=1000 3D cube",
    "note": "3D correction vs 2D Ghia -0.38289 is about -28% (spanwise friction)",
}

# --------------------------------------------------------------------------------------
# 2. Dam break — Martin & Moyce (1952)
# --------------------------------------------------------------------------------------
#: Column-collapse front position: Z = x_front/a vs T = t*sqrt(2g/a).
#: Martin & Moyce 1952, Phil. Trans. R. Soc. A 244:312, doi:10.1098/rsta.1952.0006.
#: Digitisation follows the Lethe dam-break post-processing example (x_list/L1,
#: time_list*sqrt(2g/L1)), cross-checked against the K&O / Trixi family digitisation.
MARTIN_MOYCE_1952 = {
    "T": [0.00, 0.41, 0.84, 1.19, 1.43, 1.63, 1.82, 1.97, 2.20, 2.32, 2.50, 2.64, 2.82, 2.96],
    "Z": [1.00, 1.11, 1.23, 1.44, 1.67, 1.89, 2.11, 2.33, 2.56, 2.78, 3.00, 3.22, 3.44, 3.67],
    "provenance": {
        "paper": "Martin & Moyce 1952, Phil. Trans. R. Soc. A 244:312",
        "doi": "10.1098/rsta.1952.0006",
        "digitisation": "case-provided digitisation (Lethe example convention), "
                        "independence='baseline-audit-only' for absolute accuracy",
        "convention": "T = t*sqrt(2g/a), Z = x_front/a, a = initial column width",
    },
}

#: Time-convention audit: the repo kernel uses T = t*sqrt(2g/L) (correct, M&M),
#: while older drivers used T = t*sqrt(g/a) -> T_MM = sqrt(2)*T_code.
DAM_BREAK_TIME_FACTOR = math.sqrt(2.0)

# --------------------------------------------------------------------------------------
# 3. Forchheimer / permeability of a square cylinder array
# --------------------------------------------------------------------------------------
#: Sangani & Acrivos 1982 (J. Fluid Mech. 148:... ) drag coefficient for a square array
#: of circular cylinders at solid fraction phi_s = 0.40, Stokes limit:
#: f(0.40) = 217.89  (as transcribed in Forslund et al. 2023, Transp. Porous Med. 148:545,
#: where the measured y-intercept matched it to 0.26%).
SANGANI_ACRIVOS_F40 = {
    "f_stokes": 217.89,
    "phi_s": 0.40,
    "provenance": "Sangani & Acrivos 1982 (square array, Stokes); value as reported in "
                  "Forslund et al. 2023 Transp. Porous Med. 148:545 (Fig. 6 discussion)",
}


def darcy_forchheimer_flux(grad_p: float, mu: float, perm: float,
                           beta: float, rho: float) -> float:
    """Exact 1D Darcy-Forchheimer inverse: solve mu/K u + beta*rho*u^2 = -dp/dx.

    Positive root of the quadratic; used to audit whether a reported "Forchheimer fit"
    is dimensionally consistent with the measured (grad_p, u, K, beta).
    """
    a = beta * rho
    b = mu / perm
    c = -grad_p
    if a == 0.0:
        return -c / b
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        raise ValueError("no real Forchheimer root")
    return (-b + math.sqrt(disc)) / (2.0 * a)


# --------------------------------------------------------------------------------------
# 4. Plane Poiseuille (analytic baseline audit)
# --------------------------------------------------------------------------------------
def poiseuille_planar(grad_p: float, H: float, nu: float) -> dict:
    """Plane Poiseuille in lattice units: u_max = G H^2/(8 nu), u_mean = 2/3 u_max.

    ``grad_p`` is the lattice body force / pressure gradient per unit length,
    ``H`` the effective wall-to-wall height, ``nu`` the kinematic viscosity.
    """
    u_max = grad_p * H * H / (8.0 * nu)
    return {"u_max": u_max, "u_mean": 2.0 / 3.0 * u_max,
            "formula": "u_max = G*H^2/(8*nu); u_mean = 2/3*u_max"}


# --------------------------------------------------------------------------------------
# Small numerics helpers (kept here so the recheck tool stays dependency-light)
# --------------------------------------------------------------------------------------
def interp_linear(xs, ys, x):
    """Piecewise-linear interpolation with clamped ends; returns (value, on_range)."""
    if x < xs[0] or x > xs[-1]:
        return (ys[0] if x < xs[0] else ys[-1]), False
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    if xs[hi] == xs[lo]:
        return ys[lo], True
    w = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + w * (ys[hi] - ys[lo]), True


def curve_errors(sim_x, sim_y, ref_x, ref_y, tol_target: float = TOL_TARGET,
                 tol_ok: float = TOL_ACCEPTABLE) -> dict:
    """Compare a simulated curve against reference points.

    Only reference points strictly inside the simulated x-range are used (never
    extrapolate); returns per-point table plus L-inf / RMS / mean verdict.
    """
    points = []
    for rx, ry in zip(ref_x, ref_y):
        if rx < sim_x[0] or rx > sim_x[-1]:
            continue
        sy, inside = interp_linear(sim_x, sim_y, rx)
        if not inside:
            continue
        denom = abs(ry)
        err = abs(sy - ry) / denom if denom > 0 else float("nan")
        points.append({"x": rx, "sim": sy, "ref": ry, "rel_err": err})
    errs = [p["rel_err"] for p in points]
    if not errs:
        return {"n_points": 0, "l_inf": float("nan"), "rms": float("nan"),
                "verdict": "INDETERMINATE", "points": []}
    l_inf = max(errs)
    rms = math.sqrt(sum(e * e for e in errs) / len(errs))
    return {
        "n_points": len(points),
        "l_inf": l_inf,
        "rms": rms,
        "mean": sum(errs) / len(errs),
        "verdict": classify(l_inf, tol_target, tol_ok),
        "tol_target": tol_target,
        "tol_acceptable": tol_ok,
        "points": points,
    }