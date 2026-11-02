"""ITTC 7.5-03-01 / 7.5-03-02 grid-convergence-index (GCI) machinery.

Absorbed normative content
--------------------------
* ITTC 7.5-03-02-01 (2017) "Numerical Uncertainty Analysis for CFD" --
  §3.1 systematic grid refinement (refinement ratio ``r``), §3.2 three
  solutions ``S1`` (fine), ``S2`` (medium), ``S3`` (coarse), Eqs. (1)-(15):
  relative differences ``eps21 = (S2-S1)/S1``, ``eps32 = (S3-S2)/S2``,
  convergence ratio ``R_G = eps21/eps32``, observed order of accuracy
  ``p_obs = ln(eps32/eps21)/ln(r)``, Richardson extrapolation, and the
  grid-convergence index ``GCI_fine = Fs*|eps21|/(r^p - 1)``.
* ITTC 7.5-03-01-01 (2017) "Verification and Validation of Numerical
  Simulations" -- the ``U_V = sqrt(U_SN^2 + U_input^2)`` validation
  uncertainty and the ``|D| <= U_V`` acceptance criterion.
* ITTC 7.5-03-02-01 §3.3 recommends ``Fs = 1.25`` when ``p`` is *estimated*
  from three grids, and ``Fs = 3.0`` when ``p`` is *assumed* (two-grid /
  theory-limited evaluation).  When ``p_obs`` is unreliable the procedure
  prescribes a limiting order fallback ``p = p_lim`` (default 0.5, i.e. an
  effective low order) instead of the extrapolated value.

Platform policy absorbed by this module
---------------------------------------
The platform's legacy rule is "relative error > 15% is unacceptable".  That
rule alone cannot distinguish *solver distortion* from *insufficient
verification*.  :func:`verdict` therefore upgrades it to a two-criterion gate:
a deviation above 15% is only allowed to condemn the solver when the
numerical uncertainty ``U_V`` is itself below the gate; otherwise the case is
``WITHHELD`` (validation insufficient) and may never be reported as PASS/FAIL.

Units: ``f1, f2, f3`` are any consistent positive QoI values (e.g. a
resistance coefficient); ``r``, ``p``, ``R_G``, ``GCI``, ``U_V`` and the
15% gate are dimensionless.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import List, Optional

__all__ = [
    "FS_THREE_GRID",
    "FS_TWO_GRID",
    "P_LIMIT_DEFAULT",
    "P_MAX_DEFAULT",
    "GATE_DEFAULT",
    "GCIResult",
    "gci",
    "richardson_extrapolate",
    "verdict",
    "ittc_validation_state",
]

FS_THREE_GRID = 1.25  # ITTC 7.5-03-02-01: p estimated from three grids
FS_TWO_GRID = 3.0     # ITTC 7.5-03-02-01: p assumed / two-grid evaluation
P_LIMIT_DEFAULT = 0.5  # limiting order fallback, ITTC 7.5-03-02-01 §3.3
P_MAX_DEFAULT = 4.0    # above this p_obs is treated as unreliable
GATE_DEFAULT = 0.15    # platform acceptance gate on the experimental deviation


@dataclass
class GCIResult:
    """Result of one ITTC grid-convergence study (three grids, one QoI)."""

    f1: float
    f2: float
    f3: float
    r21: float
    r32: float
    eps21: float
    eps32: float
    R_G: float
    p_obs: Optional[float]
    p_used: float
    p_source: str            # "observed" | "fallback:<reason>"
    p_fallback_reason: Optional[str]
    e_a: float               # |eps21| -- estimated relative discretisation error
    phi_ext: float           # Richardson extrapolated QoI
    gci21: float             # GCI_fine  (== U_SN when U_input = 0)
    gci32: float
    fs: float
    u_input: float
    u_v: float               # validation uncertainty U_V
    convergence: str         # monotonic | oscillatory | divergent | indeterminate
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    # -- convenience -------------------------------------------------------
    @property
    def p(self) -> float:
        return self.p_used

    @property
    def gci21_pct(self) -> float:
        return 100.0 * self.gci21

    @property
    def dev_phi_ext_vs_fine(self) -> float:
        """|f1 - phi_ext| / |phi_ext| : estimated fine-grid discretisation error."""
        if self.phi_ext == 0:
            return float("inf")
        return abs(self.f1 - self.phi_ext) / abs(self.phi_ext)


def richardson_extrapolate(f1: float, f2: float, r: float, p: float) -> float:
    """Classical Richardson extrapolation from the two finest grids.

    ``phi_ext = f1 + (f1 - f2) / (r**p - 1)``  (ITTC 7.5-03-02-01 Eq. 8
    form, written for a fine grid that converges *from above* or below).
    """
    if r <= 1.0:
        raise ValueError("refinement ratio r must be > 1")
    if p <= 0.0:
        raise ValueError("order of accuracy p must be > 0")
    denom = r ** p - 1.0
    if denom == 0.0:  # pragma: no cover - guarded by r > 1, p > 0
        raise ZeroDivisionError("r**p - 1 == 0")
    return f1 + (f1 - f2) / denom


def _convergence_class(R: float) -> str:
    if not math.isfinite(R):
        return "indeterminate"
    if R < 0.0:
        return "oscillatory"
    if R == 0.0:
        return "monotonic"  # infinitely fast convergence of eps21 -> 0
    if R < 1.0:
        return "monotonic"
    if R == 1.0:
        return "divergent"  # eps21 == eps32: no decay at all
    return "divergent"


def gci(
    f1: float,
    f2: float,
    f3: float,
    r21: float = 2.0,
    r32: Optional[float] = None,
    *,
    fs: float = FS_THREE_GRID,
    p_limit: float = P_LIMIT_DEFAULT,
    p_max: float = P_MAX_DEFAULT,
    p_assumed: Optional[float] = None,
    u_input: float = 0.0,
) -> GCIResult:
    """Three-grid GCI study for one quantity of interest.

    Parameters
    ----------
    f1, f2, f3 : QoI on the fine, medium and coarse grid (``f1`` finest).
    r21, r32 : refinement ratios ``h2/h1`` and ``h3/h2`` (``r32`` defaults
        to ``r21``).  Both must be > 1.
    fs : safety factor, ``1.25`` (three grids, p estimated, default) or
        ``3.0`` (p assumed).  If ``p_assumed`` is given the caller is
        responsible for the matching safety factor; :func:`gci` raises when
        ``p_assumed`` is used together with the three-grid ``Fs``.
    p_limit : limiting order used when ``p_obs`` is unreliable.
    p_max : upper bound of a credible observed order.
    p_assumed : bypass the observed-order computation (two-grid evaluation,
        ``Fs = 3.0``); the fallback machinery is skipped in that case.
    u_input : relative input uncertainty (``U_input``) folded into
        ``U_V = sqrt(GCI_fine**2 + U_input**2)``.
    """
    for name, val in (("f1", f1), ("f2", f2), ("f3", f3)):
        if not math.isfinite(val):
            raise ValueError(f"{name} must be finite")
    if r21 <= 1.0 or (r32 is not None and r32 <= 1.0):
        raise ValueError("refinement ratios must be > 1")
    if not math.isfinite(fs) or fs <= 0.0:
        raise ValueError("safety factor Fs must be > 0")
    r32 = r21 if r32 is None else r32

    notes: List[str] = []
    den21 = f1
    den32 = f2
    if den21 == 0.0 or den32 == 0.0:
        raise ValueError("relative differences undefined for a zero QoI")
    if (f1 > 0) != (f2 > 0) or (f2 > 0) != (f3 > 0):
        notes.append("QoI changes sign between grids: relative differences are ill-posed")

    eps21 = (f2 - f1) / f1
    eps32 = (f3 - f2) / f2

    # ---- convergence ratio R_G = eps21 / eps32 --------------------------
    if eps32 == 0.0:
        R_G = math.inf if eps21 != 0.0 else 0.0
        notes.append("eps32 == 0: coarse/medium solutions coincide; R_G = inf")
    else:
        R_G = eps21 / eps32
    convergence = _convergence_class(R_G)

    # ---- observed order of accuracy with limiting-order fallback -------
    p_obs: Optional[float] = None
    p_used: float
    p_source: str
    p_fallback_reason: Optional[str] = None

    if p_assumed is not None:
        if p_assumed <= 0 or not math.isfinite(p_assumed):
            raise ValueError("p_assumed must be finite and > 0")
        p_used = float(p_assumed)
        p_source = "assumed"
        if fs == FS_THREE_GRID:
            notes.append(
                "p_assumed with Fs=1.25: ITTC 7.5-03-02-01 prescribes Fs=3.0 "
                "when p is not estimated from the grid triplet"
            )
    else:
        ratio = (eps32 / eps21) if eps21 != 0.0 else math.nan
        if eps21 == 0.0:
            p_fallback_reason = "eps21_zero"
        elif not math.isfinite(ratio) or ratio <= 0.0:
            p_fallback_reason = "non_monotonic"
        else:
            try:
                p_obs = math.log(ratio) / math.log(r21)
            except (ValueError, ZeroDivisionError):  # pragma: no cover
                p_obs = None
            if p_obs is None or not math.isfinite(p_obs):
                p_fallback_reason = "p_not_finite"
            elif p_obs <= 0.0:
                p_fallback_reason = "p_obs_non_positive"
            elif p_obs > p_max:
                p_fallback_reason = "p_obs_above_max"

        if p_fallback_reason is None:
            p_used = float(p_obs)  # type: ignore[arg-type]
            p_source = "observed"
        else:
            p_used = float(p_limit)
            p_source = f"fallback:{p_fallback_reason}"
            notes.append(
                "observed order unreliable (%s): ITTC 7.5-03-02-01 §3.3 "
                "limiting order p_lim = %.4g used" % (p_fallback_reason, p_limit)
            )

    # ---- Richardson extrapolation and GCI ------------------------------
    phi_ext = richardson_extrapolate(f1, f2, r21, p_used)
    e_a = abs(eps21)
    gci21 = fs * e_a / (r21 ** p_used - 1.0)
    gci32 = fs * abs(eps32) / (r32 ** p_used - 1.0)
    u_v = math.hypot(gci21, u_input)

    if convergence != "monotonic":
        notes.append(
            "R_G = %.4g (%s): the triplet is not monotonically convergent, "
            "GCI is reported for traceability only and must not be used as a "
            "validated uncertainty band" % (R_G, convergence)
        )
    if u_input < 0.0:
        raise ValueError("u_input must be >= 0")

    return GCIResult(
        f1=f1,
        f2=f2,
        f3=f3,
        r21=r21,
        r32=r32,
        eps21=eps21,
        eps32=eps32,
        R_G=R_G,
        p_obs=p_obs,
        p_used=p_used,
        p_source=p_source,
        p_fallback_reason=p_fallback_reason,
        e_a=e_a,
        phi_ext=phi_ext,
        gci21=gci21,
        gci32=gci32,
        fs=fs,
        u_input=u_input,
        u_v=u_v,
        convergence=convergence,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Two-criterion adjudication: U_V band vs the 15% experimental gate
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"


def verdict(u_v: float, deviation: Optional[float], gate: float = GATE_DEFAULT) -> str:
    """Three-state verdict of the platform's upgraded acceptance gate.

    ``deviation`` is the relative deviation between the numerical result and
    the experimental / benchmark reference (``|S - S_ref| / |S_ref|``).

    Decision procedure (machine-checkable, no human in the loop):

    * ``U_V >= gate``  -> ``WITHHELD``: the numerical uncertainty is as large
      as the acceptance band, so nothing can be concluded about the solver.
      A ``WITHHELD`` case may never be reported as PASS or FAIL.
    * ``U_V < gate`` and ``|deviation| <= gate`` -> ``PASS``.
    * ``U_V < gate`` and ``|deviation| > gate``  -> ``FAIL``: the band is
      tight enough that the deviation must be attributed to the solver.
    """
    if not math.isfinite(u_v) or u_v < 0.0:
        raise ValueError("U_V must be finite and >= 0")
    if gate <= 0.0 or not math.isfinite(gate):
        raise ValueError("gate must be finite and > 0")
    if u_v >= gate:
        return WITHHELD
    if deviation is None or not math.isfinite(deviation):
        return WITHHELD
    return PASS if abs(deviation) <= gate else FAIL


def ittc_validation_state(u_v: float, comparison_error: float) -> str:
    """ITTC 7.5-03-01-01 ``|D| <= U_V`` validation acceptance.

    ``comparison_error`` is ``D = S - S_ref`` (relative form accepted, sign is
    ignored: the criterion is two-sided).
    """
    d = abs(comparison_error)
    if not math.isfinite(u_v) or u_v <= 0.0:
        return WITHHELD
    return "VALIDATED" if d <= u_v else "SIMULATION_ERROR"