"""Discretization-error verification: Richardson extrapolation + Grid Convergence Index.

Purpose
-------
Every Tensor* solver reports numbers (resistance, C_D, GZ, thrust...) whose
*discretization* uncertainty is, by default, unquantified.  The proposal pool
records "numerical V&V / GCI" as a domain with **zero** coverage across the 102
campaign nodes, so any platform number currently carries an implicit
"grid-independent" claim that was never tested.

This module supplies the missing estimator so that a solver result can be
published together with a defensible uncertainty band.

Method (all cited, no calibration constants invented here)
----------------------------------------------------------
Roache (1994) *Perspective: a method for uniform reporting of grid refinement
studies*, J. Fluids Eng. 116:405-413; Celik, Ghia, Roache et al. (2008)
*Procedure for estimation and reporting of uncertainty due to discretization in
CFD applications*, J. Fluids Eng. 130:078001; ASME V&V 20-2009.

With three grids of representative cell size ``h1 < h2 < h3`` (``h1`` finest)
and the corresponding scalar solutions ``phi1, phi2, phi3``:

1. refinement ratios ``r21 = h2/h1``, ``r32 = h3/h2``;
2. solution changes ``eps21 = phi2 - phi1``, ``eps32 = phi3 - phi2``;
3. convergence ratio ``R = eps21 / eps32`` (Roache convention: ``0 < R < 1``
   is monotone convergence, ``R < 0`` oscillatory, ``R > 1`` divergent);
4. observed order of accuracy ``p``;
5. Richardson-extrapolated value ``phi_ext`` and the uncertainty band
   ``GCI = Fs * |(phi1 - phi2)/phi1| / (r21**p - 1)`` with safety factor
   ``Fs = 1.25`` (three grids, ASME V&V 20).

Why the general-``p`` solve below is stated the way it is
---------------------------------------------------------
The usual one-line formula ``p = ln|eps32/eps21| / ln(r)`` is *only* valid for
a constant refinement ratio.  Platform grids are usually refined
anisotropically (``r21 != r32``), where that formula silently returns a wrong
order.  Writing the asymptotic model as ``phi_i = phi_ext + a * h_i**p`` gives

    R = eps21/eps32 = (r21**p - 1) / (r21**p * (r32**p - 1))           (Eq. A)

which reduces to ``R = r**-p`` (i.e. ``p = ln(1/R)/ln(r)``) when
``r21 == r32 == r``.  Eq. A is monotone in ``p``, so :func:`observed_order`
solves it by bisection -- no iteration that can
diverge, and it degrades to the textbook formula exactly in the constant-ratio
limit (pinned by ``tests/test_gci_verification.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional, Sequence

__all__ = [
    "GCIRegime",
    "GCIResult",
    "observed_order",
    "richardson_extrapolate",
    "gci",
    "refinement_ratio_ok",
    "R_MIN_RECOMMENDED",
    "FS_THREE_GRIDS",
    "FS_TWO_GRIDS",
]

#: ASME V&V 20 safety factor when *three* grids are used and ``p`` is estimated.
FS_THREE_GRIDS = 1.25
#: Safety factor when only two grids are available (``p`` must be assumed).
FS_TWO_GRIDS = 3.0
#: Celik et al. (2008) recommend r >= 1.3 so that round-off does not swamp eps.
R_MIN_RECOMMENDED = 1.3
#: Search interval for the observed order of accuracy.
_P_LO = 1.0e-3
_P_HI = 30.0


class GCIRegime:
    """Convergence regime labels (kept as plain strings for JSON friendliness)."""

    MONOTONE = "monotone"          # 0 < R < 1
    OSCILLATORY = "oscillatory"    # R < 0 (solution changes alternate in sign)
    DIVERGENT = "divergent"        # R > 1 (changes grow under refinement)


@dataclass
class GCIResult:
    """Outcome of a three-grid verification study (all fields JSON-serialisable)."""

    regime: str
    h: tuple
    phi: tuple
    r21: float
    r32: float
    eps21: float
    eps32: float
    R: float
    p: Optional[float]
    p_constant_ratio: Optional[float]
    phi_ext: Optional[float]
    e_a21: Optional[float]
    e_ext21: Optional[float]
    gci_fine: float
    gci_fine_pct: float
    fs: float
    asymptotic: bool
    notes: list

    def as_dict(self) -> dict:
        d = asdict(self)
        d["h"] = list(self.h)
        d["phi"] = list(self.phi)
        return d


def refinement_ratio_ok(r: float, r_min: float = R_MIN_RECOMMENDED) -> bool:
    """True when ``r`` is large enough for a trustworthy order estimate."""
    return r >= r_min


def _bisect_p(r21: float, r32: float, R: float) -> Optional[float]:
    """Solve Eq. A for ``p`` on ``(_P_LO, _P_HI)``; ``None`` if not bracketed."""

    a21, a32 = math.log(r21), math.log(r32)

    def f(p: float) -> float:
        """(r21**p - 1) / (r21**p * (r32**p - 1)) - R, overflow-guarded."""
        if p * a32 > 700.0:
            return -R                      # denominator explodes -> f -> -R
        den = math.exp(p * a21) * math.expm1(p * a32)
        if den == 0.0:
            return math.inf
        return math.expm1(p * a21) / den - R

    lo, hi = _P_LO, _P_HI
    f_lo, f_hi = f(lo), f(hi)
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)):
        return None
    if f_lo < 0.0 or f_hi > 0.0:      # no sign change -> not in asymptotic range
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = f(mid)
        if not math.isfinite(f_mid):
            lo = mid                   # push toward the well-behaved side
            continue
        if f_mid > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1.0e-14:
            break
    return 0.5 * (lo + hi)


def observed_order(
    eps21: float,
    eps32: float,
    r21: float,
    r32: float,
) -> tuple:
    """Observed order of accuracy ``p`` from two solution changes.

    Returns ``(p, regime, note)``.  ``p`` is ``None`` when the grids are not in
    the asymptotic range (no valid Richardson estimate exists).
    """
    if eps21 == 0.0 or eps32 == 0.0:
        return None, GCIRegime.MONOTONE, "zero solution change between grids; p undefined"
    R = eps21 / eps32
    if R < 0.0:
        return None, GCIRegime.OSCILLATORY, "R < 0: solution changes alternate in sign"
    if R > 1.0:
        return None, GCIRegime.DIVERGENT, "R > 1: changes grow under refinement"

    p = _bisect_p(r21, r32, R)
    if p is None:
        return None, GCIRegime.OSCILLATORY, (
            "R outside the range implied by the refinement ratios "
            "((r21**p-1)/(r21**p*(r32**p-1))): pre-asymptotic, order not recoverable"
        )
    return p, GCIRegime.MONOTONE, ""


def richardson_extrapolate(phi1: float, phi2: float, r21: float, p: float) -> float:
    """``phi_ext = phi1 + (phi1 - phi2)/(r21**p - 1)`` (two finest grids)."""
    den = r21 ** p - 1.0
    if den == 0.0:
        raise ValueError("r21**p == 1: extrapolation undefined (r21 <= 1 or p == 0)")
    return phi1 + (phi1 - phi2) / den


def gci(
    h: Sequence[float],
    phi: Sequence[float],
    fs: float = FS_THREE_GRIDS,
    p_assumed: Optional[float] = None,
    r_min: float = R_MIN_RECOMMENDED,
) -> GCIResult:
    """Three-grid GCI study.

    Parameters
    ----------
    h : sequence of three cell sizes, **finest first** (``h[0] < h[1] < h[2]``).
    phi : the corresponding scalar quantity of interest.
    fs : safety factor (default 1.25, ASME V&V 20 for three grids).
    p_assumed : if given, skip the order estimate and use this ``p`` (the
        classical two-grid/post-hoc usage; pass ``fs=FS_TWO_GRIDS`` in that case).
    r_min : minimum acceptable refinement ratio (warning only, never fatal).
    """
    if len(h) != 3 or len(phi) != 3:
        raise ValueError("gci() needs exactly three grid levels (h, phi each length 3)")
    h1, h2, h3 = (float(v) for v in h)
    phi1, phi2, phi3 = (float(v) for v in phi)
    if not (h1 < h2 < h3):
        raise ValueError("h must be strictly increasing (finest first): h[0] < h[1] < h[2]")
    if any(not math.isfinite(v) for v in (h1, h2, h3, phi1, phi2, phi3)):
        raise ValueError("h and phi must be finite")

    r21 = h2 / h1
    r32 = h3 / h2
    eps21 = phi2 - phi1
    eps32 = phi3 - phi2
    R = (eps21 / eps32) if eps32 != 0.0 else math.inf
    notes: list = []

    if not refinement_ratio_ok(r21, r_min) or not refinement_ratio_ok(r32, r_min):
        notes.append(
            "refinement ratio below %.2f: round-off can dominate eps (Celik 2008)"
            % r_min
        )

    # Constant-ratio textbook estimate, kept as a cross-check / audit trail.
    p_cr: Optional[float] = None
    if eps21 != 0.0 and eps32 != 0.0:
        ratio = abs(eps21 / eps32)
        if ratio > 0.0 and not math.isinf(ratio):
            p_cr = abs(math.log(ratio)) / math.log(r21)

    if p_assumed is not None:
        p = float(p_assumed)
        regime = GCIRegime.MONOTONE
        notes.append("p supplied by caller (p_assumed=%.4g); not estimated from data" % p)
    else:
        p, regime, note = observed_order(eps21, eps32, r21, r32)
        if note:
            notes.append(note)

    if regime != GCIRegime.MONOTONE or p is None:
        # No valid Richardson estimate: report a conservative range-based bound.
        # Celik (2008) / Roache (1994): U = Fs * (max(phi) - min(phi)) is the
        # accepted fallback outside the asymptotic range.
        spread = max(phi1, phi2, phi3) - min(phi1, phi2, phi3)
        u = fs * spread
        scale = max(abs(phi1), abs(phi2), abs(phi3))
        pct = 100.0 * u / scale if scale > 0 else math.inf
        notes.append(
            "not in asymptotic range: GCI replaced by conservative range bound "
            "Fs*(max-min); treat the platform number as UNVERIFIED"
        )
        return GCIResult(
            regime=regime, h=(h1, h2, h3), phi=(phi1, phi2, phi3),
            r21=r21, r32=r32, eps21=eps21, eps32=eps32, R=R,
            p=None, p_constant_ratio=p_cr, phi_ext=None,
            e_a21=None, e_ext21=None,
            gci_fine=u, gci_fine_pct=pct, fs=fs,
            asymptotic=False, notes=notes,
        )

    phi_ext = richardson_extrapolate(phi1, phi2, r21, p)
    e_a21 = abs((phi1 - phi2) / phi1) if phi1 != 0.0 else math.inf
    e_ext21 = abs((phi_ext - phi1) / phi_ext) if phi_ext != 0.0 else math.inf
    denom = r21 ** p - 1.0
    gci_fine = fs * e_a21 / denom if denom != 0.0 else math.inf

    asymptotic = refinement_ratio_ok(r21, r_min) and refinement_ratio_ok(r32, r_min)
    if p_cr is not None and p > 0:
        rel = abs(p_cr - p) / p
        if rel > 0.10:
            notes.append(
                "constant-ratio estimate p_cr=%.4g differs from general p=%.4g by "
                "%.1f%%: refinement ratios are non-uniform, use p" % (p_cr, p, 100 * rel)
            )

    return GCIResult(
        regime=regime, h=(h1, h2, h3), phi=(phi1, phi2, phi3),
        r21=r21, r32=r32, eps21=eps21, eps32=eps32, R=R,
        p=p, p_constant_ratio=p_cr, phi_ext=phi_ext,
        e_a21=e_a21, e_ext21=e_ext21,
        gci_fine=gci_fine, gci_fine_pct=100.0 * gci_fine, fs=fs,
        asymptotic=asymptotic, notes=notes,
    )