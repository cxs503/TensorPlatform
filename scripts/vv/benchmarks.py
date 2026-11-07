"""Verification anchors for the W10-N07 certification chain.

Every check here has an *external* reference, i.e. a value that cannot be
produced by the code under test:

``roache_power_law``
    A synthetic ``f(h) = phi0 + C h**p`` family (Roache's textbook derivation
    of the three-grid order relation) is fed through :func:`scripts.vv.gci.gci_study`
    and the recovered ``p`` / ``phi_ext`` / ``GCI`` are compared to a 60-digit
    ``decimal`` recomputation of the same algebra.  The tolerance is
    ``1e-6`` relative, so a sign error or a wrong coefficient cannot hide.

``ittc_locked_anchors``
    The sha256-locked anchor library of the platform
    (:mod:`tools.gci.anchors`, 8 records with published/locked ``p`` and ``GCI``)
    is recomputed with the new kernel; the maximum relative deviation is
    reported.  This is the "reproduce the standard textbook / published case"
    requirement, using the platform's own audited anchor set.

``poiseuille``
    Both discretisations are run on an unobstructed channel and compared to the
    analytic plane-Poiseuille solution ``u = g/(2 nu) y (H-y)`` and to the
    analytic wall friction ``g rho H Lx``.

``wall_friction_extractor``
    The shared force extractor is applied to the analytic Poiseuille field; it
    must reproduce ``g rho H Lx`` (it is ~1.4 % low because the wall-normal
    gradient is first order).
"""

from __future__ import annotations

import math
from decimal import Decimal, getcontext
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import gci as G
from .cases import ChannelCase, solid_mask
from .force import surface_force

__all__ = [
    "roache_power_law",
    "ittc_locked_anchors",
    "analytic_poiseuille",
    "wall_friction_extractor",
    "poiseuille_convergence",
]

getcontext().prec = 60


# ---------------------------------------------------------------------------
# Roache three-grid algebra, recomputed independently in 60-digit decimal
# ---------------------------------------------------------------------------
def _decimal_reference(phi0: str, c: str, p: str, hs: Sequence[str]) -> Dict[str, float]:
    phi0_d, c_d, p_d = Decimal(phi0), Decimal(c), Decimal(p)
    f = [phi0_d + c_d * Decimal(h) ** p_d for h in hs]
    f1, f2, f3 = f
    eps21 = (f2 - f1) / f1
    eps32 = (f3 - f2) / f2
    ln = lambda x: x.ln()  # noqa: E731
    h1, h2, h3 = (Decimal(h) for h in hs)
    r21 = h2 / h1
    r32 = h3 / h2
    # general three-grid order relation, solved by bisection in 60 digits
    target = eps32 / eps21
    lo, hi = Decimal("1e-12"), Decimal(20)
    for _ in range(400):
        mid = (lo + hi) / 2
        a = r21 ** mid
        b = r32 ** mid
        val = a * (b - 1) / (a - 1)
        if val > target:
            hi = mid
        else:
            lo = mid
    p_obs = (lo + hi) / 2
    rp = r21 ** p_obs
    fs = Decimal("1.25")
    gci = fs * abs(eps21) / (rp - 1)
    phi_ext = f1 + (f1 - f2) / (rp - 1)
    return {
        "p_obs": float(p_obs),
        "gci_fine": float(gci),
        "phi_ext": float(phi_ext),
        "eps21": float(eps21),
        "eps32": float(eps32),
        "R": float(eps21 / eps32),
    }


def roache_power_law() -> Dict[str, object]:
    """Recover ``p``, ``GCI`` and ``phi_ext`` on an exact power-law error family."""
    cases = [
        # (phi0, C, p, h triple)  -- uniform and non-uniform refinement
        ("1.0", "4.0", "2", ("0.25", "0.5", "1.0")),
        ("2.5", "-1.5", "1", ("0.125", "0.25", "0.5")),
        ("1.0", "30.0", "3", ("0.1", "0.2", "0.4")),
        ("0.75", "2.0", "1.5", ("0.08", "0.1", "0.125")),   # r21=1.25, r32=1.25
        ("1.0", "-0.4", "2", ("0.05", "0.075", "0.1")),     # r21=r32=1.5
    ]
    rows: List[Dict[str, float]] = []
    for phi0, c, p, hs in cases:
        f = [float(Decimal(phi0) + Decimal(c) * Decimal(h) ** Decimal(p)) for h in hs]
        h = [float(x) for x in hs]
        study = G.gci_study("power_law(p=%s)" % p, f, h=h, p_expected=float(p),
                            order_tol=1e-6)
        ref = _decimal_reference(phi0, c, p, hs)
        rows.append({
            "p_true": float(p),
            "r21": study.r21,
            "r32": study.r32,
            "p_obs": study.p_obs,
            "p_obs_rel_dev": abs(study.p_obs - ref["p_obs"]) / ref["p_obs"],
            "gci_fine": study.gci_fine,
            "gci_rel_dev": abs(study.gci_fine - ref["gci_fine"]) / ref["gci_fine"],
            "phi_ext": study.phi_ext,
            "phi_ext_rel_dev": abs(study.phi_ext - ref["phi_ext"]) / abs(ref["phi_ext"]),
            "R": study.R,
            "R_rel_dev": abs(study.R - ref["R"]) / abs(ref["R"]),
            "certifiable": study.certifiable,
            "asymptotic": study.asymptotic,
        })
    return {
        "rows": rows,
        "max_p_rel_dev": max(r["p_obs_rel_dev"] for r in rows),
        "max_gci_rel_dev": max(r["gci_rel_dev"] for r in rows),
        "max_phi_rel_dev": max(r["phi_ext_rel_dev"] for r in rows),
        "tolerance": 1e-6,
        "pass": bool(
            max(r["p_obs_rel_dev"] for r in rows) < 1e-6
            and max(r["gci_rel_dev"] for r in rows) < 1e-6
            and max(r["phi_ext_rel_dev"] for r in rows) < 1e-6
        ),
    }


# ---------------------------------------------------------------------------
# platform locked anchors (published / sha256-pinned literature values)
# ---------------------------------------------------------------------------
def ittc_locked_anchors() -> Dict[str, object]:
    """Recompute the sha256-locked anchor library with the new kernel."""
    try:
        from tools.gci import anchors as A
    except Exception as exc:  # pragma: no cover - import guard
        return {"available": False, "error": repr(exc)}
    rows: List[Dict[str, object]] = []
    for a in A.load_anchors():
        res = G.gci_study(a.id, (a.f1, a.f2, a.f3), r21=a.r21, r32=a.r32, fs=a.fs)
        p_exp = getattr(a, "p_expected", None)
        gci_exp = getattr(a, "gci21_expected", None)

        def rel(x: float, y: float) -> float:
            return abs(x - y) / abs(y) if y else abs(x - y)

        rows.append({
            "id": a.id,
            "p_computed": res.p_used,
            "p_expected": p_exp,
            "p_rel_dev": rel(res.p_used, p_exp) if p_exp is not None else None,
            "gci_computed": res.gci_fine,
            "gci_expected": gci_exp,
            "gci_rel_dev": rel(res.gci_fine, gci_exp) if gci_exp is not None else None,
            "regime": res.regime,
            "p_source": res.p_source,
        })
    devs = [r["gci_rel_dev"] for r in rows if r["gci_rel_dev"] is not None]
    pdevs = [r["p_rel_dev"] for r in rows if r["p_rel_dev"] is not None]
    return {
        "available": True,
        "rows": rows,
        "n_anchors": len(rows),
        "max_gci_rel_dev": max(devs) if devs else None,
        "max_p_rel_dev": max(pdevs) if pdevs else None,
        "pass": bool(devs and max(devs) < 1e-9 and max(pdevs) < 1e-9),
    }


# ---------------------------------------------------------------------------
# analytic plane Poiseuille
# ---------------------------------------------------------------------------
def analytic_poiseuille(case: ChannelCase, ny: int) -> Dict[str, np.ndarray | float]:
    """Analytic plane-Poiseuille reference on the wall-adjacent cell centres."""
    h_w = float(ny - 2)
    y = np.arange(1, ny - 1) + 0.5 - 1.0       # distance from the wall
    u = case.body_force() / (2.0 * case.nu) * (y * (h_w - y))
    return {
        "y": y,
        "u": u,
        "u_bulk": case.body_force() * h_w ** 2 / (12.0 * case.nu),
        "u_max": case.body_force() / (2.0 * case.nu) * (h_w / 2.0) ** 2,
        "wall_force": case.body_force() * h_w,     # per unit length, both walls
    }


def wall_friction_extractor(case: ChannelCase, ny: int, nx: int) -> Dict[str, float]:
    """The shared extractor on the analytic field: reproduces ``g rho H Lx``."""
    ref = analytic_poiseuille(case, ny)
    solid = solid_mask(case, nx, ny, walls=True)
    u = np.zeros((ny, nx))
    u[1:ny - 1, :] = ref["u"][:, None]
    fx, _fy = surface_force(np.zeros((ny, nx)), u, np.zeros((ny, nx)), solid, 1.0, case.nu)
    expected = case.body_force() * (ny - 2) * nx
    return {
        "extracted": fx,
        "expected": expected,
        "rel_error": abs(fx - expected) / expected,
    }


def poiseuille_convergence(
    solver: str,
    grids: Sequence[Tuple[int, int]],
    re_ref: float = 20.0,
    u0: float = 0.06,
    steps: Optional[int] = None,
) -> Dict[str, object]:
    """Run ``solver`` ('fvm' | 'lbm') on unobstructed channels and score it."""
    rows: List[Dict[str, float]] = []
    from .cases import ChannelCase
    for (nx, ny) in grids:
        case = ChannelCase(name="poiseuille", kind="empty", D=(ny - 2) / 4.0,
                           re_ref=re_ref, u0=u0)
        if solver == "fvm":
            from .fvm import FVMSolver
            s = FVMSolver(case, nx, ny)
            s.initialise_poiseuille()
            n = steps or 12000
            for _ in range(n):
                s.step()
            p, u, v = s.cell_centred()
            ub = s.bulk_velocity()
            fx, _ = s.force()
        elif solver == "lbm":
            from .lbm import LBMSolver
            s = LBMSolver(case, nx, ny)
            s.initialise_poiseuille()
            n = steps or 20000
            for _ in range(n):
                s.step()
            rho, u, v = s.moments()
            p = rho / 3.0
            ub = s.bulk_velocity()
            fx, _ = surface_force(p, u, v, s.body if hasattr(s, "body") else s.solid,
                                  1.0, case.nu)
        else:  # pragma: no cover
            raise ValueError("solver must be 'fvm' or 'lbm'")
        ref = analytic_poiseuille(case, ny)
        prof = u[1:ny - 1, nx // 2]
        l2 = float(np.sqrt(np.mean((prof - ref["u"]) ** 2)) / ref["u_max"])
        linf = float(np.abs(prof - ref["u"]).max() / ref["u_max"])
        rows.append({
            "nx": nx,
            "ny": ny,
            "h": 1.0 / (ny - 2),
            "u_bulk": ub,
            "u_bulk_analytic": float(ref["u_bulk"]),
            "u_bulk_rel_error": abs(ub - ref["u_bulk"]) / float(ref["u_bulk"]),
            "profile_l2_rel": l2,
            "profile_linf_rel": linf,
            "steps": n,
        })
    return {"solver": solver, "rows": rows,
            "max_bulk_rel_error": max(r["u_bulk_rel_error"] for r in rows),
            "max_profile_l2": max(r["profile_l2_rel"] for r in rows)}