"""Deliberately broken hydrostatics variants (mutation gate).

These exist only to prove that the geometry gate is not vacuous: if a mutant
survives all criteria, the criteria are too weak.  Report 1133 used the same
"physically plausible synthetic control" device when it adjudicated the
propeller open-water endpoint.

Each mutant takes a :class:`HullForm` and returns the same dict layout as
``ref_hydrostatics.compute_hydrostatics`` with one planted defect.
"""
from __future__ import annotations

import numpy as np

from .ref_hydrostatics import HullForm, compute_hydrostatics


def _base(form: HullForm, **kw):
    return compute_hydrostatics(form, **kw)


def mutant_depth_in_cb(form: HullForm) -> dict:
    """Cb computed with depth D instead of draft T (classic dimension slip)."""
    r = _base(form)
    D = form.meta.get("D", form.T)
    r["Cb"] = r["VOL"] / (form.L * form.B * D)
    return r


def mutant_trapezoid(form: HullForm) -> dict:
    """Second-order trapezoid instead of Simpson (order criterion must catch it)."""
    return _base(form, integrate="trapezoid")


def mutant_cw_equals_cb(form: HullForm) -> dict:
    """Copy-paste bug: waterplane coefficient set to the block coefficient."""
    r = _base(form)
    r["Cw"] = r["Cb"]
    r["A_W"] = r["Cw"] * form.L * form.B
    return r


def mutant_bm_longitudinal(form: HullForm) -> dict:
    """BM built from the longitudinal moment of area instead of the transverse one."""
    r = _base(form)
    n_x = int(r["n_x"])
    xs = np.linspace(-0.5 * form.L, 0.5 * form.L, n_x + 1)
    y_w = form.y(xs, np.full_like(xs, form.T))
    w = np.ones(n_x + 1)
    w[1:-1:2] = 4.0
    w[2:-1:2] = 2.0
    i_l = float(((2.0 * y_w) * xs ** 2 * w).sum() * (xs[1] - xs[0]) / 3.0)
    r["BM"] = i_l / r["VOL"]
    return r


def mutant_kb_half_draft(form: HullForm) -> dict:
    """Folk assumption KB = T/2 for every hull form."""
    r = _base(form)
    r["KB"] = 0.5 * form.T
    return r


def mutant_cp_from_cw(form: HullForm) -> dict:
    """Prismatic coefficient taken as Cb/Cw (wrong identity)."""
    r = _base(form)
    if r["Cw"] > 1e-12:
        r["Cp"] = r["Cb"] / r["Cw"]
    return r


#: name -> (function, expected failing metric family)
MUTANTS = {
    "depth_in_cb": (mutant_depth_in_cb, "Cb"),
    "trapezoid": (mutant_trapezoid, "order"),
    "cw_equals_cb": (mutant_cw_equals_cb, "Cw"),
    "bm_longitudinal": (mutant_bm_longitudinal, "BM"),
    "kb_half_draft": (mutant_kb_half_draft, "KB"),
    "cp_from_cw": (mutant_cp_from_cw, "Cp"),
}