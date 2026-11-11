"""Rational (ℚ) re-evaluation of the polynomial conservation ledgers.

The float path closes the ledgers to ~1e-16 relative.  That number could be
round-off *or* a small model error that happens to be small; the two are not
distinguishable from one float run.  So the same moment sums are recomputed in
exact rational arithmetic from the very same vertex coordinates:

* every ledger quantity here (volume, mass, linear momentum, inertia tensor,
  angular momentum) is a **polynomial** in the coordinates, hence exactly
  representable in ℚ; the strain-energy ledger is not (it involves a square
  root) and is excluded on purpose;
* if the ℚ residual is zero (or bounded by the coordinate rounding of the
  fracture vertices) the float residual is pure round-off — the construction
  conserves, the arithmetic merely rounds.

The moment formula is the tetrahedron identity used by the float kernel
(``∫_T xxᵀ dV = V/20 (Σ v_k v_kᵀ + s sᵀ)``, ``s = Σ v_k``), so the comparison
isolates arithmetic, not geometry.

No domain semantics.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np


def _frac(x: Any) -> Fraction:
    return Fraction(float(x))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _exact_box_moments(poly) -> Dict[str, Any]:
    """Analytic ℚ moments of an axis-aligned box (the kernel's box fast path).

    The float kernel short-circuits boxes without a hull triangulation
    (``simplicies`` is empty), so the rational path needs the same closed form:
    ``V = Π h``, ``∫x dV = V c``, ``∫xxᵀ dV = V(ccᵀ + diag(h²)/3)``.
    """
    lo = [_frac(v) for v in poly.bbox_lo]
    hi = [_frac(v) for v in poly.bbox_hi]
    c = [(hi[i] + lo[i]) / 2 for i in range(3)]
    half = [(hi[i] - lo[i]) / 2 for i in range(3)]
    vol = 8 * half[0] * half[1] * half[2]
    first = [vol * c[i] for i in range(3)]
    second = [[vol * (c[i] * c[j] + ((half[i] * half[i] / 3) if i == j else Fraction(0)))
               for j in range(3)] for i in range(3)]
    return {"volume": vol, "first": first, "second": second}


def exact_moments(poly) -> Dict[str, Any]:
    """Volume / first moment / second moment of a convex polytope in ℚ."""
    if len(np.asarray(poly.simplicies)) == 0:
        if getattr(poly, "is_axis_box", False) and poly.bbox_lo is not None:
            return _exact_box_moments(poly)
        raise ValueError("polytope has no hull triangles; only boxes are analytic")
    verts: List[List[Fraction]] = [[_frac(c) for c in row] for row in poly.vertices]
    n = len(verts)
    interior = [sum(v[i] for v in verts) / n for i in range(3)]
    vol = Fraction(0)
    first = [Fraction(0)] * 3
    second = [[Fraction(0)] * 3 for _ in range(3)]
    for tri in np.asarray(poly.simplicies, dtype=int):
        a, b, c = verts[tri[0]], verts[tri[1]], verts[tri[2]]
        v6 = _dot([a[i] - interior[i] for i in range(3)],
                  _cross([b[i] - interior[i] for i in range(3)],
                         [c[i] - interior[i] for i in range(3)]))
        tet = v6 / 6
        if tet < 0:
            tet = -tet
        if tet == 0:
            continue
        corners = [interior, a, b, c]
        s = [sum(corner[i] for corner in corners) for i in range(3)]
        vol += tet
        for i in range(3):
            first[i] += tet * s[i] / 4
        for i in range(3):
            for j in range(3):
                second[i][j] += (tet / 20) * (
                    sum(corner[i] * corner[j] for corner in corners) + s[i] * s[j])
    return {"volume": vol, "first": first, "second": second}


def exact_inertia(poly, density: float) -> Tuple[Fraction, List[Fraction], List[List[Fraction]]]:
    """Mass, centroid and inertia tensor about the centroid in ℚ."""
    mom = exact_moments(poly)
    vol = mom["volume"]
    rho = _frac(density)
    mass = rho * vol
    c = [mom["first"][i] / vol for i in range(3)]
    second = [[mom["second"][i][j] - vol * c[i] * c[j] for j in range(3)] for i in range(3)]
    tr = second[0][0] + second[1][1] + second[2][2]
    inertia = [[rho * ((tr if i == j else Fraction(0)) - second[i][j]) for j in range(3)]
               for i in range(3)]
    return mass, c, inertia


def _matvec(m, v):
    return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]


def _rel(a, b):
    num = abs(a - b)
    den = (abs(a) + abs(b)) / 2
    if den == 0:
        return Fraction(0) if num == 0 else Fraction(10) ** 30
    return num / den


def _rel_vec(a, b):
    # exact 2-norm is irrational in general; use the max norm (rational) and say so
    num = max(abs(x - y) for x, y in zip(a, b))
    den = max(max(abs(x) for x in a), max(abs(y) for y in b))
    if den == 0:
        return Fraction(0) if num == 0 else Fraction(10) ** 30
    return num / den


def _rel_mat(a, b):
    num = max(abs(a[i][j] - b[i][j]) for i in range(3) for j in range(3))
    den = max(max(abs(x) for row in a for x in row), max(abs(x) for row in b for x in row))
    if den == 0:
        return Fraction(0) if num == 0 else Fraction(10) ** 30
    return num / den


def rational_ledger_residuals(element, fragments: Sequence[Any]) -> Dict[str, Any]:
    """Recompute the polynomial ledgers in ℚ and return exact residuals.

    Residual definition matches the float kernel up to the norm: ℚ cannot take
    an exact 2-norm, so the *max* norm is used (``note: "max-norm"``).  A zero
    here therefore means "identically zero in ℚ", the strongest statement
    available.
    """
    m_e, c_e, i_e = exact_inertia(element.polyhedron, element.density)
    v_e = [ _frac(x) for x in element.velocity]
    w_e = [_frac(x) for x in element.angular_velocity]

    m_t = Fraction(0)
    mom_t = [Fraction(0)] * 3
    ang_t = [Fraction(0)] * 3
    i_t = [[Fraction(0)] * 3 for _ in range(3)]
    v_t = Fraction(0)

    for f in fragments:
        m_i, c_i, i_i = exact_inertia(f.polyhedron, element.density)
        m_t += m_i
        v_t += m_i / _frac(element.density)
        d = [c_i[k] - c_e[k] for k in range(3)]
        vel = [v_e[k] + _cross(w_e, d)[k] for k in range(3)]
        p_i = [m_i * x for x in vel]
        mom_t = [mom_t[k] + p_i[k] for k in range(3)]
        iw = _matvec(i_i, w_e)
        dpp = _cross(d, p_i)
        ang_t = [ang_t[k] + iw[k] + dpp[k] for k in range(3)]
        d2 = _dot(d, d)
        for a in range(3):
            for b in range(3):
                pa = m_i * ((d2 if a == b else Fraction(0)) - d[a] * d[b])
                i_t[a][b] += i_i[a][b] + pa

    iw_e = _matvec(i_e, w_e)
    return {
        "note": "exact rational arithmetic, max-norm residuals",
        "mass": _rel(m_e, m_t),
        "momentum": _rel_vec([m_e * x for x in v_e], mom_t),
        "angular_momentum": _rel_vec(iw_e, ang_t),
        "inertia_tensor": _rel_mat(i_e, i_t),
        "volume": _rel(_frac(element.polyhedron.volume), v_t),
        "exact_zero": {
            "mass": m_e == m_t,
            "momentum": [m_e * x for x in v_e] == mom_t,
            "angular_momentum": iw_e == ang_t,
            "inertia_tensor": i_e == i_t,
            "volume": _frac(element.polyhedron.volume) == v_t,
        },
        "floats": {
            "mass": float(_rel(m_e, m_t)),
            "momentum": float(_rel_vec([m_e * x for x in v_e], mom_t)),
            "angular_momentum": float(_rel_vec(iw_e, ang_t)),
            "inertia_tensor": float(_rel_mat(i_e, i_t)),
            "volume": float(_rel(_frac(element.polyhedron.volume), v_t)),
        },
    }


__all__ = ["exact_inertia", "exact_moments", "rational_ledger_residuals"]