"""Solution verification: generalised Richardson extrapolation + Grid Convergence Index.

Why this module
---------------
Every campaign node of this platform reports numbers produced by a discrete
solver, yet the platform had **no** facility to answer the elementary
verification question *"how much of this number is discretisation error?"*.
A grep over the whole tree before this module existed found zero occurrences of
``Richardson`` / ``Grid Convergence Index`` / ``GCI`` (see
``docs/verifier/NUMERICAL_VV_GCI.md`` for the audit trail).  Campaign wave-9
coverage analysis listed ``numerical V&V / GCI`` as one of six *new domains with
zero nodes*.

This module implements the textbook procedure (Roache 1994, 1998; ASME V&V 20;
Celik et al. 2008, *J. Fluids Eng.* 130:078001), which is a strictly
deterministic arithmetic procedure with **no tunable physics** -- which is
exactly why it can be locked down by machine-judgeable tests against analytic
solutions.

Conventions
-----------
* Three grids are ordered *fine* -> *medium* -> *coarse* with spacings
  ``h1 < h2 < h3`` and refinement ratios ``r21 = h2/h1``, ``r32 = h3/h2``.
  The ratios need not be equal; the observed order is then obtained from the
  implicit relation solved by fixed-point iteration.
* ``s = sign((f3 - f2) / (f2 - f1))``: ``s = +1`` monotone convergence,
  ``s = -1`` oscillatory convergence.
* Differences ``d21 = f2 - f1``, ``d32 = f3 - f2``; convergence ratio
  ``CR = d21 / d32`` (Roache): ``0 < CR < 1`` monotone, ``-1 < CR < 0``
  oscillatory, ``|CR| > 1`` divergence.
* Observed order of accuracy ``p`` solves

      p = | ln|d32/d21| + ln[ (r21^p - s) / (r32^p - s) ] | / ln(r21)

  which reduces to the familiar ``p = ln|d32/d21| / ln(r)`` for uniform ratio.
* Richardson estimate of the exact value

      f_ext = (r21^p f1 - f2) / (r21^p - 1)

* Estimated discretisation error of the *fine* solution (factor of safety 1)

      e1 = |f2 - f1| / (r21^p - 1)

  and the Grid Convergence Index ``GCI_fine = Fs * e1 / |f1|`` with
  ``Fs = 1.25`` (three or more grids, asymptotic range) or ``Fs = 3.0``
  (two grids / order taken as nominal).
* Asymptotic-range indicator: with ``A1 = |d21| / (r21^p - 1)`` and
  ``A2 = |d32| / (r32^p - 1)`` (error magnitudes of grids 1 and 2), the ratio
  ``A2 / (r21^p A1)`` must be ~1 when the grids are in the asymptotic range.

Nothing here is vessel-physics specific: the same routine certifies resistance
solvers, seakeeping codes, DEM contact models or FEM modal analyses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

__all__ = [
    "Level",
    "ConvergenceReport",
    "observed_order",
    "richardson_value",
    "gci_pair",
    "assess_convergence",
    "MONOTONE",
    "OSCILLATORY",
    "DIVERGENCE",
    "INDETERMINATE",
]

MONOTONE = "monotone_convergence"
OSCILLATORY = "oscillatory_convergence"
DIVERGENCE = "divergence"
INDETERMINATE = "indeterminate"

_P_MIN = 1.0e-3
_P_MAX = 50.0


@dataclass(frozen=True)
class Level:
    """One grid/time-step level: a characteristic spacing and a scalar QoI."""

    h: float
    value: float
    label: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.h) or self.h <= 0.0:
            raise ValueError(f"grid spacing must be positive and finite, got {self.h!r}")
        if not math.isfinite(self.value):
            raise ValueError(f"QoI value must be finite, got {self.value!r}")


def observed_order(
    f1: float,
    f2: float,
    f3: float,
    r21: float,
    r32: float,
    *,
    max_iter: int = 500,
    tol: float = 1.0e-13,
) -> float:
    """Observed order of accuracy ``p`` from three solutions (fine/medium/coarse).

    Handles unequal refinement ratios via the standard fixed-point form of
    ASME V&V 20.  Raises :class:`ValueError` for degenerate differences
    (bit-identical solutions) and :class:`RuntimeError` when the iteration does
    not approach a stable order, which is itself diagnostic information
    (the grids are not an asymptotic sequence).
    """
    if r21 <= 0.0 or r32 <= 0.0:
        raise ValueError("refinement ratios must be positive")
    d21 = f2 - f1
    d32 = f3 - f2
    scale = max(abs(f1), abs(f2), abs(f3), 1.0e-300)
    degenerate = 1.0e-14 * scale
    if abs(d21) <= degenerate or abs(d32) <= degenerate:
        raise ValueError(
            "degenerate convergence differences: two solutions agree to "
            "round-off, the order of accuracy is not observable"
        )
    s = 1.0 if d32 * d21 > 0.0 else -1.0
    ln_r21 = math.log(r21)
    ratio = abs(d32 / d21)

    p = abs(math.log(ratio)) / ln_r21
    if not math.isfinite(p) or p <= 0.0:
        p = 1.0
    p = min(max(p, _P_MIN), _P_MAX)

    for _ in range(max_iter):
        num = r21**p - s
        den = r32**p - s
        if num <= 0.0 or den <= 0.0 or not (math.isfinite(num) and math.isfinite(den)):
            raise RuntimeError(f"observed-order iteration broke down at p={p:.6g}")
        p_new = abs(math.log(ratio) + math.log(num / den)) / ln_r21
        if not math.isfinite(p_new):
            raise RuntimeError(f"observed-order iteration produced non-finite p at p={p:.6g}")
        p_new = min(max(p_new, _P_MIN), _P_MAX)
        if abs(p_new - p) <= tol * max(1.0, abs(p)):
            return p_new
        # damped update: the map is contractive but can ring for r21 != r32
        p = 0.5 * (p + p_new)
    raise RuntimeError(f"observed-order iteration did not converge (last p={p:.6g})")


def richardson_value(f1: float, f2: float, r21: float, p: float) -> float:
    """Richardson-extrapolated (order ``p``) estimate of the exact value."""
    r21p = r21**p
    if r21p == 1.0:
        raise ValueError("r21**p == 1: Richardson extrapolation is singular")
    return (r21p * f1 - f2) / (r21p - 1.0)


def gci_pair(
    f_fine: float,
    f_coarse: float,
    r: float,
    p: float,
    *,
    fs: float = 1.25,
) -> Dict[str, float]:
    """GCI and estimated error for one grid pair (relative *and* absolute)."""
    if r <= 1.0:
        raise ValueError("refinement ratio must exceed 1 to estimate error")
    denom = r**p - 1.0
    if denom <= 0.0:
        raise ValueError("r**p - 1 <= 0: unphysical refinement/order combination")
    e_abs = abs(f_coarse - f_fine) / denom
    e_signed = (f_coarse - f_fine) / denom
    ref = abs(f_fine) if abs(f_fine) > 0.0 else 1.0
    return {
        "refinement_ratio": r,
        "observed_order": p,
        "error_abs": e_abs,
        "error_signed": e_signed,
        "error_relative": e_abs / ref,
        "gci_relative": fs * e_abs / ref,
        "gci_absolute": fs * e_abs,
        "factor_of_safety": fs,
    }


@dataclass(frozen=True)
class ConvergenceReport:
    """Auditable solution-verification verdict for one scalar quantity of interest."""

    quantity: str
    spacings: Tuple[float, ...]
    values: Tuple[float, ...]
    refinement_ratios: Tuple[float, ...]
    convergence_ratio: Optional[float]
    verdict: str
    observed_order: Optional[float]
    extrapolated_value: Optional[float]
    estimated_error_fine: Optional[float]
    gci_fine: Optional[float]
    gci_medium: Optional[float]
    asymptotic_ratio: Optional[float]
    asymptotic_ok: Optional[bool]
    exact_value: Optional[float]
    true_error_fine: Optional[float]
    true_error_ratio_to_gci: Optional[float]
    true_error_covered: Optional[bool]
    factor_of_safety: float
    warnings: Tuple[str, ...]
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in self.__dict__.items():
            out[key] = list(value) if isinstance(value, tuple) else value
        return out

    def summary(self) -> str:
        p = "n/a" if self.observed_order is None else f"{self.observed_order:.4f}"
        gci = "n/a" if self.gci_fine is None else f"{100.0 * self.gci_fine:.4e}%"
        head = f"[{self.verdict}] {self.quantity}: p_obs={p}, GCI_fine={gci}"
        tail = ""
        if self.true_error_fine is not None and self.true_error_ratio_to_gci is not None:
            tail = (
                f", |true error|={abs(self.true_error_fine):.3e}"
                f" (= {self.true_error_ratio_to_gci:.3f} x GCI)"
            )
        return f"{head}{tail}, passed={self.passed}"


def assess_convergence(
    levels: Sequence[Level],
    *,
    quantity: str = "Q",
    exact: Optional[float] = None,
    fs: Optional[float] = None,
    expected_order: Optional[float] = None,
    order_tolerance: float = 0.10,
    asymptotic_band: Tuple[float, float] = (0.90, 1.10),
) -> ConvergenceReport:
    """Full solution-verification assessment of one QoI from 2+ refinement levels.

    Parameters
    ----------
    levels : sequence of :class:`Level`
        Refinement levels; any input order, sorted internally by ``h``.
        Three levels are required to *observe* the order; with two levels the
        order is taken from ``expected_order`` (default 2) and the factor of
        safety defaults to 3.0.
    exact : float, optional
        Analytic/reference value.  When given, the report additionally states
        whether the GCI band actually covers the true discretisation error --
        the only genuinely falsifiable statement in solution verification.
    fs : float, optional
        Factor of safety; defaults to 1.25 for 3+ levels and 3.0 for 2 levels.
    expected_order : float, optional
        Nominal order used to warn when the observed order drifts, and to fall
        back to when only two levels are supplied.

    ``passed`` requires: monotone or oscillatory convergence with a finite
    observed order, an asymptotic-range indicator inside ``asymptotic_band``,
    the observed order within ``order_tolerance`` (relative) of
    ``expected_order`` when that is given, and -- if ``exact`` is supplied --
    the true error being covered by the GCI band.
    """
    if len(levels) < 2:
        raise ValueError("at least two refinement levels are required")
    ordered = sorted(levels, key=lambda lv: lv.h)
    h = [lv.h for lv in ordered]
    f = [lv.value for lv in ordered]
    for i in range(len(h) - 1):
        if h[i + 1] <= h[i] * (1.0 + 1.0e-12):
            raise ValueError("refinement levels must have strictly increasing h")

    warnings: list[str] = []
    ratios = tuple(h[i + 1] / h[i] for i in range(len(h) - 1))
    three = len(levels) >= 3
    fs_eff = fs if fs is not None else (1.25 if three else 3.0)

    p: Optional[float] = None
    verdict = INDETERMINATE
    extrapolated: Optional[float] = None
    e_fine: Optional[float] = None
    gci_fine: Optional[float] = None
    gci_medium: Optional[float] = None
    cr: Optional[float] = None
    asym_ratio: Optional[float] = None
    asym_ok: Optional[bool] = None
    true_err: Optional[float] = None
    true_ratio: Optional[float] = None
    covered: Optional[bool] = None

    if three:
        f1, f2, f3 = f[0], f[1], f[2]
        r21, r32 = ratios[0], ratios[1]
        d21, d32 = f2 - f1, f3 - f2
        scale = max(abs(f1), abs(f2), abs(f3), 1.0e-300)
        if abs(d21) > 1.0e-14 * scale:
            # Roache convergence ratio: change on the fine pair over the change
            # on the coarse pair -> |CR| < 1 for a converging sequence.
            cr = d21 / d32
            if 0.0 < cr < 1.0:
                verdict = MONOTONE
            elif -1.0 < cr <= 0.0:
                verdict = OSCILLATORY
            else:
                verdict = DIVERGENCE
        else:
            warnings.append(
                "the two finest solutions agree to round-off: refinement is not "
                "resolving the QoI (either fully converged or round-off limited)"
            )

        try:
            p = observed_order(f1, f2, f3, r21, r32)
        except (ValueError, RuntimeError) as exc:
            warnings.append(f"observed order unavailable: {exc}")
            p = None

        if p is not None:
            r21p = r21**p
            extrapolated = richardson_value(f1, f2, r21, p)
            pair = gci_pair(f1, f2, r21, p, fs=fs_eff)
            e_fine = pair["error_abs"]
            gci_fine = pair["gci_relative"]
            second = gci_pair(f2, f3, r32, p, fs=1.0)
            gci_medium = second["error_relative"]
            a1 = pair["error_abs"]
            a2 = second["error_abs"]
            if a1 > 0.0 and a2 > 0.0:
                asym_ratio = a2 / (r21p * a1)
                asym_ok = asymptotic_band[0] <= asym_ratio <= asymptotic_band[1]
                if not asym_ok:
                    warnings.append(
                        "asymptotic range not confirmed: error ratio "
                        f"{asym_ratio:.3f} outside "
                        f"[{asymptotic_band[0]}, {asymptotic_band[1]}]"
                    )
            if verdict == DIVERGENCE:
                warnings.append(
                    f"divergent sequence (CR={cr:.3f}): the GCI is meaningless here"
                )
            if verdict == OSCILLATORY:
                warnings.append(
                    "oscillatory convergence: GCI remains the error magnitude "
                    "bound but the errors alternate in sign"
                )
            if expected_order is not None and expected_order > 0.0:
                dev = abs(p - expected_order) / expected_order
                if dev > order_tolerance:
                    warnings.append(
                        f"observed order {p:.4f} deviates {100.0 * dev:.1f}% from "
                        f"theoretical {expected_order:g} "
                        f"(tolerance {100.0 * order_tolerance:.0f}%)"
                    )
            if p > 6.0:
                warnings.append(
                    f"observed order {p:.3f} implausibly high: check grid bookkeeping"
                )
    else:  # two levels: assume the nominal order
        f1, f2 = f[0], f[1]
        r21 = ratios[0]
        p = expected_order if expected_order is not None else 2.0
        if expected_order is None:
            warnings.append(
                "only two levels supplied: order taken as nominal 2 and Fs=3.0; "
                "supply three levels to observe the order"
            )
        else:
            warnings.append(
                f"only two levels supplied: order fixed at nominal {p:g}, Fs={fs_eff:g}"
            )
        d21 = f2 - f1
        scale = max(abs(f1), abs(f2), 1.0e-300)
        if abs(d21) > 1.0e-14 * scale:
            verdict = MONOTONE
            extrapolated = richardson_value(f1, f2, r21, p)
            pair = gci_pair(f1, f2, r21, p, fs=fs_eff)
            e_fine = pair["error_abs"]
            gci_fine = pair["gci_relative"]
        else:
            warnings.append("solutions agree to round-off: no error can be estimated")

    gci_abs: Optional[float] = None
    if exact is not None:
        true_err = f[0] - exact
        if e_fine is not None:
            gci_abs = fs_eff * e_fine
            if gci_abs > 0.0:
                true_ratio = abs(true_err) / gci_abs
                covered = abs(true_err) <= gci_abs
                if not covered:
                    warnings.append(
                        f"true error {abs(true_err):.4e} exceeds the GCI band "
                        f"{gci_abs:.4e} (ratio {true_ratio:.2f}): the estimate is "
                        "not a valid bound here"
                    )

    order_flagged = any(w.startswith("observed order") for w in warnings)
    passed = (
        verdict in (MONOTONE, OSCILLATORY)
        and p is not None
        and gci_fine is not None
        and asym_ok is not False
        and covered is not False
        and not order_flagged
    )
    if verdict == DIVERGENCE:
        passed = False

    return ConvergenceReport(
        quantity=quantity,
        spacings=tuple(h),
        values=tuple(f),
        refinement_ratios=ratios,
        convergence_ratio=cr,
        verdict=verdict,
        observed_order=p,
        extrapolated_value=extrapolated,
        estimated_error_fine=e_fine,
        gci_fine=gci_fine,
        gci_medium=gci_medium,
        asymptotic_ratio=asym_ratio,
        asymptotic_ok=asym_ok,
        exact_value=exact,
        true_error_fine=true_err,
        true_error_ratio_to_gci=true_ratio,
        true_error_covered=covered,
        factor_of_safety=fs_eff,
        warnings=tuple(warnings),
        passed=passed,
    )