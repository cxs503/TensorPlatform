"""Grid Convergence Index (GCI) toolkit for code/discretisation verification.

Why this module exists
----------------------
Several verifier references in this repository integrate a continuous model with a
discretisation parameter (RK4 time step in ``tools/verifier/mmg_reference.py``,
Gauss-Legendre / quasi-Monte-Carlo quadrature in
``docs/verification_debt/wigley_michell/michell_wigley.py``) and then quote a single
number as if it were the exact value of the model.  A numerical value is only a
verified value once the *discretisation error* attached to it is quantified.

This module implements the standard systematic grid-refinement procedure of
Roache (1994, 1998) with the factor-of-safety convention of Celik et al. (2008,
*J. Fluids Eng.* 130:078001) and the ASME V&V-20 style reporting fields:

* triplet analysis  -> observed order of accuracy ``p`` (Richardson),
* Richardson extrapolation -> discretisation-error-free estimate ``f_exact``,
* ``GCI_fine`` [%]  -> interval that contains the exact model value at 95 %,
* ``asymptotic_ratio`` -> ``GCI_32 / (r21**p * GCI_21)``; unity inside the
  asymptotic range, which is the precondition for quoting ``p`` at all,
* robustness guards that would otherwise be reported as nonsense numbers:
  - ``roundoff_dominated`` : refinement increments at the double-precision floor,
  - ``oscillatory``        : refinement increments change sign,
  - ``diverging``          : increments grow under refinement,
  - ``no_order_estimate``  : no root of the triplet equation in the searched band.

Nothing here is tuned to a particular solver: the caller supplies the three (or
more) successive discretisation levels and the corresponding integral quantity,
so the same tool can audit TensorLBM/FVM/FEM/BEM field results as well as the
cheap analytic references used inside TensorPlatform.

References
----------
* Roache, P.J. (1994) "Perspective: a method for uniform reporting of grid
  refinement studies", *J. Fluids Eng.* 116:405-413.
* Roache, P.J. (1998) *Verification and Validation in Computational Science and
  Engineering*, Hermosa.
* Celik, I.B. et al. (2008) "Procedure for estimation and reporting of
  uncertainty due to discretization in CFD applications", *J. Fluids Eng.*
  130:078001.
* ASME V&V 20-2009, *Standard for Verification and Validation in Computational
  Fluid Dynamics and Heat Transfer*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence

__all__ = [
    "GCIResult",
    "gci_study",
    "observed_order",
    "richardson_extrapolate",
    "study_from_function",
]

#: Below this relative level a refinement increment is treated as round-off.
ROUNDOFF_TOL = 1.0e-12

#: Factor of safety, Celik et al. (2008): 1.25 for a well-behaved triplet,
#: 3.0 when only two levels (or an assumed order) are available.
FS_TRIPLET = 1.25
FS_TWO_LEVEL = 3.0


@dataclass
class GCIResult:
    """Result of a grid-convergence study, all fields JSON-serialisable."""

    steps: List[float]                 # h, ordered fine -> coarse
    values: List[float]                # f(h)
    refinement_ratios: List[float]     # r between successive levels
    n_levels: int
    observed_order: Optional[float] = None
    assumed_order: Optional[float] = None
    extrapolated: Optional[float] = None
    gci_fine_pct: Optional[float] = None
    gci_medium_pct: Optional[float] = None
    asymptotic_ratio: Optional[float] = None
    regime: str = "unknown"            # monotone | oscillatory | diverging | roundoff | no_order_estimate
    safety_factor: Optional[float] = None
    roundoff_dominated: bool = False
    oscillatory: bool = False
    asymptotic_range: bool = False
    converged: bool = False
    tolerance_pct: float = 5.0
    relative_increment_pct: Optional[float] = None
    messages: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "n_levels": self.n_levels,
            "steps": list(self.steps),
            "values": list(self.values),
            "refinement_ratios": list(self.refinement_ratios),
            "observed_order": self.observed_order,
            "assumed_order": self.assumed_order,
            "extrapolated": self.extrapolated,
            "gci_fine_pct": self.gci_fine_pct,
            "gci_medium_pct": self.gci_medium_pct,
            "asymptotic_ratio": self.asymptotic_ratio,
            "regime": self.regime,
            "safety_factor": self.safety_factor,
            "roundoff_dominated": self.roundoff_dominated,
            "oscillatory": self.oscillatory,
            "asymptotic_range": self.asymptotic_range,
            "converged": self.converged,
            "tolerance_pct": self.tolerance_pct,
            "relative_increment_pct": self.relative_increment_pct,
            "messages": list(self.messages),
        }
        return out


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------
def richardson_extrapolate(f_fine: float, f_coarse: float, r: float, p: float) -> float:
    """Richardson extrapolation ``f_exact = f_fine + (f_fine - f_coarse)/(r**p - 1)``."""
    denom = r ** p - 1.0
    if denom == 0.0:
        raise ValueError("r**p == 1 -> no extrapolation possible")
    return f_fine + (f_fine - f_coarse) / denom


def _triplet_residual(p: float, r21: float, r32: float, e21: float, e32: float, s: float) -> float:
    """g(p) from Celik et al. (2008) eq. (3)-(6); root gives the observed order."""
    ratio = abs(e32 / e21)
    q = math.log((r21 ** p - s) / (r32 ** p - s))
    return p * math.log(r21) - math.log(ratio) - q


def observed_order(
    f1: float,
    f2: float,
    f3: float,
    r21: float,
    r32: float,
    p_lo: float = 0.05,
    p_hi: float = 12.0,
) -> tuple[Optional[float], str, List[str]]:
    """Observed order of accuracy from three successive refinement levels.

    ``f1`` is the finest level.  Returns ``(p, regime, messages)``; ``p`` is
    ``None`` whenever the triplet carries no usable convergence information.
    """
    msgs: List[str] = []
    scale = max(abs(f1), abs(f2), abs(f3), 1e-300)
    e21 = f2 - f1
    e32 = f3 - f2
    if abs(e21) / scale < ROUNDOFF_TOL and abs(e32) / scale < ROUNDOFF_TOL:
        msgs.append(
            "refinement increments below %.0e of the quantity -> round-off floor"
            % ROUNDOFF_TOL
        )
        return None, "roundoff", msgs
    if e21 == 0.0:
        msgs.append("e21 == 0 exactly -> cannot form the triplet ratio")
        return None, "roundoff", msgs
    ratio = e32 / e21
    if ratio < 0.0:
        msgs.append(
            "refinement increments change sign (e32/e21 = %.3f) -> oscillatory "
            "convergence, no reliable order" % ratio
        )
        return None, "oscillatory", msgs
    # no sign change in g(p) over the search band -> try the one-term estimate
    lo, hi = p_lo, p_hi
    g_lo = _triplet_residual(lo, r21, r32, e21, e32, 1.0)
    g_hi = _triplet_residual(hi, r21, r32, e21, e32, 1.0)
    if g_lo * g_hi > 0.0:
        p = math.log(abs(ratio)) / math.log(r21)
        if p <= 0.0:
            msgs.append("increments grow under refinement -> diverging sequence "
                        "(or the levels were passed coarse -> fine; f1 must be the finest)")
            return None, "diverging", msgs
        msgs.append(
            "no root of the unequal-ratio triplet equation in [%.2f, %.2f]; "
            "fell back to the one-term estimate p = ln|e32/e21|/ln(r21)" % (p_lo, p_hi)
        )
        if p > p_hi:
            msgs.append("one-term p = %.2f exceeds the searched band -> reported as "
                        "no_order_estimate" % p)
            return None, "no_order_estimate", msgs
        return p, "monotone", msgs
    for _ in range(200):  # bisection is plenty for a 1-D monotone-in-sign band
        mid = 0.5 * (lo + hi)
        g_mid = _triplet_residual(mid, r21, r32, e21, e32, 1.0)
        if g_lo * g_mid <= 0.0:
            hi, g_hi = mid, g_mid
        else:
            lo, g_lo = mid, g_mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi), "monotone", msgs


# ---------------------------------------------------------------------------
# the study driver
# ---------------------------------------------------------------------------
def gci_study(
    steps: Sequence[float],
    values: Sequence[float],
    assumed_order: Optional[float] = None,
    tolerance_pct: float = 5.0,
    asymptotic_band: tuple[float, float] = (0.8, 1.25),
) -> GCIResult:
    """Run the Roache/Celik GCI procedure on a refinement sequence.

    Parameters
    ----------
    steps : discretisation sizes (``h``), any order on input; re-sorted ascending.
    values : integral quantity at each ``h`` (same length as ``steps``).
    assumed_order : use a fixed ``p`` instead of the triplet estimate (needed for
        two-level studies; forces ``Fs = 3.0``).
    tolerance_pct : maximum acceptable ``GCI_fine`` for ``converged = True``.
    asymptotic_band : acceptable range for the asymptotic ratio.
    """
    if len(steps) != len(values):
        raise ValueError("steps and values must have the same length")
    if len(steps) < 2:
        raise ValueError("need at least two refinement levels")
    order = sorted(range(len(steps)), key=lambda i: steps[i])
    h = [float(steps[i]) for i in order]
    f = [float(values[i]) for i in order]
    if any(h[i] >= h[i + 1] for i in range(len(h) - 1)):
        raise ValueError("duplicate discretisation sizes in steps")
    ratios = [h[i + 1] / h[i] for i in range(len(h) - 1)]

    res = GCIResult(
        steps=h,
        values=f,
        refinement_ratios=ratios,
        n_levels=len(h),
        assumed_order=assumed_order,
        tolerance_pct=tolerance_pct,
    )
    f1, f2 = f[0], f[1]
    scale = max(abs(v) for v in f) or 1.0
    res.relative_increment_pct = abs(f1 - f2) / scale * 100.0

    if assumed_order is None and len(f) >= 3:
        p, regime, msgs = observed_order(f[0], f[1], f[2], ratios[0], ratios[1])
        res.regime = regime
        res.messages.extend(msgs)
        res.oscillatory = regime == "oscillatory"
    elif assumed_order is not None:
        p, regime = float(assumed_order), "assumed_order"
        res.regime = regime
    else:
        p, regime = None, "two_level_no_order"
        res.regime = regime
        res.messages.append(
            "only two levels and no assumed order -> GCI cannot be formed "
            "(pass assumed_order, e.g. 4.0 for classical RK4)"
        )

    if regime == "roundoff":
        res.roundoff_dominated = True
        # the sequence is converged far below any engineering tolerance; the
        # exact model value is f_finest to within the round-off floor.
        res.extrapolated = f1
        res.gci_fine_pct = 0.0
        res.safety_factor = FS_TRIPLET if len(f) >= 3 else FS_TWO_LEVEL
        res.converged = True
        res.asymptotic_range = True
        res.messages.append(
            "sequence is converged to the double-precision floor; reported GCI = 0 %%"
        )
        return res

    if p is None:
        res.converged = False
        res.messages.append("no observed order -> discretisation error unquantified")
        return res

    res.observed_order = p if assumed_order is None else None
    r21 = ratios[0]
    res.safety_factor = FS_TRIPLET if (len(f) >= 3 and assumed_order is None) else FS_TWO_LEVEL
    if abs(r21 - 1.0) < 1e-12:
        res.messages.append("r21 == 1 -> no refinement, GCI undefined")
        res.converged = False
        return res
    res.extrapolated = richardson_extrapolate(f1, f2, r21, p)
    denom = r21 ** p - 1.0
    if denom == 0.0:
        res.messages.append("r21**p == 1 -> GCI undefined")
        res.converged = False
        return res
    e_rel = abs((f1 - f2) / f1) if f1 != 0.0 else abs(f1 - f2) / scale
    res.gci_fine_pct = res.safety_factor * e_rel / denom * 100.0

    if len(f) >= 3 and assumed_order is None and abs(f2) > 0.0:
        f3 = f[2]
        e_rel_m = abs((f2 - f3) / f2)
        res.gci_medium_pct = res.safety_factor * e_rel_m / (ratios[1] ** p - 1.0) * 100.0
        if res.gci_medium_pct and res.gci_fine_pct and res.gci_fine_pct > 0.0:
            res.asymptotic_ratio = res.gci_medium_pct / (r21 ** p * res.gci_fine_pct)
            lo, hi = asymptotic_band
            res.asymptotic_range = lo <= res.asymptotic_ratio <= hi
            if not res.asymptotic_range:
                res.messages.append(
                    "asymptotic ratio %.3f outside [%.2f, %.2f] -> the quoted order "
                    "is not supported by an asymptotic sequence" % (res.asymptotic_ratio, lo, hi)
                )
    else:
        res.messages.append(
            "asymptotic range not assessable without a 3-level triplet; "
            "GCI quoted with Fs = %.2f" % res.safety_factor
        )

    assessable = len(f) >= 3 and assumed_order is None
    res.converged = bool(
        res.gci_fine_pct is not None
        and res.gci_fine_pct <= tolerance_pct
        and (res.asymptotic_range if assessable else True)
        and not res.oscillatory
    )
    if res.gci_fine_pct is not None and res.gci_fine_pct > tolerance_pct:
        res.messages.append(
            "GCI_fine = %.3g %% > tolerance %.3g %% -> not converged"
            % (res.gci_fine_pct, tolerance_pct)
        )
    return res


def study_from_function(
    f: Callable[[float], float],
    steps: Iterable[float],
    **kwargs,
) -> GCIResult:
    """Convenience wrapper: evaluate ``f`` at each discretisation size and analyse."""
    hs = list(steps)
    return gci_study(hs, [f(h) for h in hs], **kwargs)