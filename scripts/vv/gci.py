"""Grid-convergence index (GCI) kernel with explicit degradation + U_V adjudication.

Normative content
-----------------
* Roache (1994, 1998) / ITTC 7.5-03-02-01 (2017) three-grid procedure:
  ``eps21 = (f2-f1)/f1``, ``eps32 = (f3-f2)/f2``, convergence ratio
  ``R = eps21/eps32``, observed order ``p`` from the refinement ratios,
  Richardson extrapolation ``phi_ext``, ``GCI_fine = Fs*|eps21|/(r21**p - 1)``.
* ASME V&V 20 / ITTC 7.5-03-01-01 uncertainty synthesis::

      U_SN = sqrt(U_GCI**2 + U_input**2)
      U_V  = sqrt(U_SN**2 + U_D**2)

* Validation acceptance: ``|D| <= U_V`` with ``D = (S - E)/E`` (ITTC
  7.5-03-01-01).  The platform's 15 % engineering gate is kept as a *second,
  independent* criterion, so "passes 15 %" can never be reported when the
  uncertainty band is itself as wide as the gate.

What this kernel adds over the two legacy kernels
------------------------------------------------
1. **General refinement ratios.**  ``tools/gci/core.py`` and
   ``src/verification/gci.py`` both solve ``p`` with the uniform-ratio closed
   form ``p = ln(eps32/eps21)/ln(r)``; with ``r21 != r32`` that formula is
   simply wrong.  Here ``p`` solves the general three-grid relation

        eps32/eps21 = r21**p * (r32**p - 1) / (r21**p - 1)

   by bisection, degenerating to the closed form for ``r21 == r32``.
2. **Falsifiable asymptotic-range test.**  The m2033 audit showed that the
   legacy indicator ``asym = eps32/(r**p*eps21)`` is identically 1 whenever
   ``p`` is estimated from the same triplet (tautology).  Here the test is
   against an *externally supplied* ``p_expected`` (the formal order of the
   scheme) and it can fail: ``|p_obs - p_expected| <= tol*p_expected``.
3. **Explicit degradation.**  Non-monotonic (``R < 0``) or divergent
   (``|R| > 1``) triplets are labelled, and the study is marked
   ``certifiable = False``; the GCI is still reported for traceability but may
   never be used as a validated uncertainty band, and :func:`adjudicate` then
   returns ``WITHHELD``.
4. **Adjudicator.**  :func:`adjudicate` returns a machine-checkable verdict
   plus the *narrowest accuracy band the evidence can certify*
   (``certified_band = U_V``), so a claim such as "error < 10 %" is
   mechanically refutable when ``U_V > 10 %``.

All functions are pure, deterministic and stdlib-only, so the chain is
bit-reproducible.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "FS_THREE_GRID",
    "FS_TWO_GRID",
    "GATE_DEFAULT",
    "P_LIMIT_DEFAULT",
    "P_MAX_DEFAULT",
    "R_MIN",
    "MONOTONIC",
    "OSCILLATORY",
    "DIVERGENT",
    "STATIONARY",
    "VALIDATION_ACHIEVED",
    "VALIDATION_NOT_ACHIEVED",
    "WITHHELD",
    "ConvergenceStudy",
    "Adjudication",
    "refinement_ratios",
    "observed_order",
    "observed_order_uniform",
    "richardson_extrapolate",
    "convergence_regime",
    "gci_study",
    "synthesize_u_sn",
    "synthesize_u_v",
    "adjudicate",
    "re_adjudicate_claim",
]

FS_THREE_GRID = 1.25   # p estimated from three grids (ITTC 7.5-03-02-01 §3.3)
FS_TWO_GRID = 3.0      # p assumed / two-grid evaluation
P_LIMIT_DEFAULT = 0.5  # limiting-order fallback when p_obs is unusable
P_MAX_DEFAULT = 4.0    # above this p_obs is not credible
R_MIN = 1.25           # minimum acceptable refinement ratio
GATE_DEFAULT = 0.15    # platform engineering acceptance gate

MONOTONIC = "monotonic"
OSCILLATORY = "oscillatory"
DIVERGENT = "divergent"
STATIONARY = "stationary"

VALIDATION_ACHIEVED = "VALIDATION_ACHIEVED"
VALIDATION_NOT_ACHIEVED = "VALIDATION_NOT_ACHIEVED"
WITHHELD = "WITHHELD"


# ---------------------------------------------------------------------------
# refinement ratios and observed order
# ---------------------------------------------------------------------------
def refinement_ratios(h: Sequence[float]) -> Tuple[float, float]:
    """``(r21, r32) = (h2/h1, h3/h2)`` for a grid triple ``(h1, h2, h3)``."""
    if len(h) != 3:
        raise ValueError("need exactly three grid spacings")
    h1, h2, h3 = (float(v) for v in h)
    if not (h1 > 0.0 and h2 > 0.0 and h3 > 0.0):
        raise ValueError("grid spacings must be positive")
    return h2 / h1, h3 / h2


def _ratio_model(p: float, r21: float, r32: float) -> float:
    """Model prediction of ``eps32/eps21`` for an ``h**p`` error family.

    With ``f(h) = f_exact + C*h**p``::

        eps21 = C*h1**p*(r21**p - 1)/f
        eps32 = eps21 * r21**p * (r32**p - 1)/(r21**p - 1)
    """
    a = r21 ** p
    b = r32 ** p
    return a * (b - 1.0) / (a - 1.0)


def observed_order_uniform(r: float, eps21: float, eps32: float) -> float:
    """Closed-form ``p = ln(eps32/eps21)/ln(r)`` (uniform refinement only)."""
    if r <= 1.0:
        raise ValueError("refinement ratio must be > 1")
    return math.log(eps32 / eps21) / math.log(r)


def observed_order(r21: float, r32: float, eps21: float, eps32: float,
                   p_lo: float = 1e-8, p_hi: float = 20.0,
                   tol: float = 1e-14, max_iter: int = 300) -> Optional[float]:
    """Solve the general three-grid order relation for ``p``.

    Returns ``None`` when no positive root exists — exactly the
    non-monotonic / divergent situation, where the measured ratio
    ``eps32/eps21`` falls below the ``p -> 0+`` limit ``ln(r32)/ln(r21)``.
    """
    if r21 <= 1.0 or r32 <= 1.0:
        raise ValueError("refinement ratios must be > 1")
    if eps21 == 0.0 or eps32 == 0.0:
        return None
    target = eps32 / eps21
    if not math.isfinite(target) or target <= 0.0:
        return None
    if abs(r21 - r32) <= 1e-12 * max(r21, r32):
        return observed_order_uniform(r21, eps21, eps32)
    lo_limit = math.log(r32) / math.log(r21)
    if target <= lo_limit:
        return None
    f_lo = math.log(_ratio_model(p_lo, r21, r32)) - math.log(target)
    f_hi = math.log(_ratio_model(p_hi, r21, r32)) - math.log(target)
    if f_lo >= 0.0 or f_hi <= 0.0:  # pragma: no cover - model is monotone in p
        return None
    lo, hi = p_lo, p_hi
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = math.log(_ratio_model(mid, r21, r32)) - math.log(target)
        if abs(f_mid) < tol or (hi - lo) < tol:
            return mid
        if f_mid > 0.0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def richardson_extrapolate(f1: float, f2: float, r21: float, p: float) -> float:
    """``phi_ext = f1 + (f1 - f2)/(r21**p - 1)`` (ITTC Eq. 8 form)."""
    if r21 <= 1.0:
        raise ValueError("r21 must be > 1")
    if not (p > 0.0):
        raise ValueError("p must be > 0")
    den = r21 ** p - 1.0
    if den == 0.0:  # pragma: no cover
        raise ZeroDivisionError("r21**p - 1 == 0")
    return f1 + (f1 - f2) / den


def convergence_regime(R: float, eps32: float) -> str:
    """Classify the triplet from the convergence ratio ``R = eps21/eps32``."""
    if eps32 == 0.0:
        return STATIONARY
    if not math.isfinite(R):
        return DIVERGENT
    if R < 0.0:
        return OSCILLATORY
    if R == 0.0:
        return MONOTONIC
    if R < 1.0:
        return MONOTONIC
    return DIVERGENT


# ---------------------------------------------------------------------------
# the study object
# ---------------------------------------------------------------------------
@dataclass
class ConvergenceStudy:
    """One three-grid GCI study carrying an explicit certification state."""

    name: str
    values: Tuple[float, float, float]          # (f1, f2, f3), fine -> coarse
    eps_convention: str
    h: Optional[Tuple[float, float, float]]
    r21: float
    r32: float
    eps21: float
    eps32: float
    R: float
    regime: str
    p_obs: Optional[float]
    p_used: float
    p_source: str
    p_expected: Optional[float]
    asymptotic: Optional[bool]
    e_a: float
    e_ext: float
    phi_ext: float
    fs: float
    gci_fine: float
    gci_medium: float
    u_gci: float
    certifiable: bool
    degradation: Optional[str]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def gci_fine_pct(self) -> float:
        return 100.0 * self.gci_fine

    @property
    def observed_order_dev(self) -> Optional[float]:
        """``|p_obs - p_expected|/p_expected`` (None without ``p_expected``)."""
        if self.p_obs is None or not self.p_expected:
            return None
        return abs(self.p_obs - self.p_expected) / self.p_expected


def gci_study(
    name: str,
    values: Sequence[float],
    h: Optional[Sequence[float]] = None,
    *,
    r21: float = 2.0,
    r32: Optional[float] = None,
    fs: float = FS_THREE_GRID,
    p_limit: float = P_LIMIT_DEFAULT,
    p_max: float = P_MAX_DEFAULT,
    p_expected: Optional[float] = None,
    order_tol: float = 0.10,
    r_min: float = R_MIN,
    eps_convention: str = "relative",
) -> ConvergenceStudy:
    """Complete a three-grid GCI study including the degradation verdict.

    ``values = (f1, f2, f3)`` with ``f1`` on the finest grid.  ``h`` (grid
    spacings) may be supplied instead of ``r21``/``r32``.  ``p_expected`` is
    the *formal* order of the scheme; when supplied, the asymptotic-range test
    ``|p_obs - p_expected| <= order_tol*p_expected`` is evaluated and can
    fail — the check the m2033 audit showed was tautological before.
    """
    f1, f2, f3 = (float(v) for v in values)
    for nm, v in (("f1", f1), ("f2", f2), ("f3", f3)):
        if not math.isfinite(v):
            raise ValueError(f"{nm} must be finite")
    if h is not None:
        r21, r32_h = refinement_ratios(h)
        r32 = r32_h if r32 is None else float(r32)
    r32 = float(r21) if r32 is None else float(r32)
    if r21 <= 1.0 or r32 <= 1.0:
        raise ValueError("refinement ratios must be > 1")
    if not math.isfinite(fs) or fs <= 0.0:
        raise ValueError("safety factor Fs must be > 0")

    notes: List[str] = []
    if f1 == 0.0 or f2 == 0.0:
        raise ValueError("relative differences undefined for a zero QoI")
    if (f1 > 0) != (f2 > 0) or (f2 > 0) != (f3 > 0):
        notes.append("QoI changes sign between grids: relative eps is ill-posed")

    if eps_convention not in ("relative", "absolute"):
        raise ValueError("eps_convention must be 'relative' or 'absolute'")
    if eps_convention == "relative":
        eps21 = (f2 - f1) / f1
        eps32 = (f3 - f2) / f2
    else:
        # Roache's original absolute-difference convention; the GCI and the
        # extrapolation stay dimensionless by normalising with |f1|
        eps21 = (f2 - f1) / abs(f1)
        eps32 = (f3 - f2) / abs(f1)
    R = (eps21 / eps32) if eps32 != 0.0 else math.inf
    regime = convergence_regime(R, eps32)

    # ---- observed order with ITTC limiting-order fallback --------------
    p_obs: Optional[float] = None
    p_source = "observed"
    degradation: Optional[str] = None
    if eps21 == 0.0:
        degradation = "eps21_zero"
    elif regime != MONOTONIC:
        degradation = f"non_{regime}_triplet"
    else:
        try:
            p_obs = observed_order(r21, r32, eps21, eps32)
        except (ValueError, ZeroDivisionError):  # pragma: no cover
            p_obs = None
        if p_obs is None or not math.isfinite(p_obs):
            degradation = "p_not_finite"
        elif p_obs <= 0.0:
            degradation = "p_obs_non_positive"
        elif p_obs > p_max:
            degradation = "p_obs_above_max"

    if degradation is None and p_obs is not None:
        p_used = float(p_obs)
    else:
        p_used = float(p_limit)
        p_source = f"fallback:{degradation}"
        notes.append(
            "observed order unreliable (%s): ITTC 7.5-03-02-01 §3.3 limiting "
            "order p_lim = %.4g used for traceability only" % (degradation, p_limit)
        )

    phi_ext = richardson_extrapolate(f1, f2, r21, p_used)
    e_a = abs(eps21)
    e_ext = abs(f1 - phi_ext) / abs(phi_ext) if phi_ext != 0.0 else math.inf
    gci_fine = fs * e_a / (r21 ** p_used - 1.0)
    gci_medium = fs * abs(eps32) / (r32 ** p_used - 1.0)

    # ---- falsifiable asymptotic-range test -----------------------------
    asymptotic: Optional[bool] = None
    if p_expected is not None:
        if p_obs is None:
            asymptotic = False
        else:
            asymptotic = abs(p_obs - p_expected) <= order_tol * p_expected
        if not asymptotic:
            notes.append(
                "asymptotic range not demonstrated: p_obs=%.4f vs p_expected=%.4f "
                "(tol %.1f%%)" % (p_obs if p_obs is not None else float("nan"),
                                  p_expected, 100.0 * order_tol)
            )
    if min(r21, r32) < r_min:
        notes.append(
            "refinement ratio %.4g < r_min %.4g: three-grid estimate is weak"
            % (min(r21, r32), r_min)
        )

    certifiable = (
        degradation is None
        and regime == MONOTONIC
        and min(r21, r32) >= r_min
        and (asymptotic is not False)
    )
    if regime != MONOTONIC:
        notes.append(
            "R = %.4g (%s): triplet is not monotonically convergent; GCI is "
            "traceability-only and must not be used as a validated band"
            % (R, regime)
        )

    return ConvergenceStudy(
        name=name,
        values=(f1, f2, f3),
        eps_convention=eps_convention,
        h=tuple(float(v) for v in h) if h is not None else None,
        r21=float(r21),
        r32=float(r32),
        eps21=eps21,
        eps32=eps32,
        R=R,
        regime=regime,
        p_obs=p_obs,
        p_used=p_used,
        p_source=p_source,
        p_expected=p_expected,
        asymptotic=asymptotic,
        e_a=e_a,
        e_ext=e_ext,
        phi_ext=phi_ext,
        fs=float(fs),
        gci_fine=gci_fine,
        gci_medium=gci_medium,
        u_gci=gci_fine,
        certifiable=certifiable,
        degradation=degradation,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# uncertainty synthesis (ASME V&V 20 / ITTC 7.5-03-01-01)
# ---------------------------------------------------------------------------
def synthesize_u_sn(u_gci: float, u_input: float) -> float:
    """``U_SN = sqrt(U_GCI**2 + U_input**2)`` (simulation numerical uncertainty)."""
    for nm, v in (("u_gci", u_gci), ("u_input", u_input)):
        if not math.isfinite(v) or v < 0.0:
            raise ValueError(f"{nm} must be finite and >= 0")
    return math.hypot(u_gci, u_input)


def synthesize_u_v(u_sn: float, u_d: float) -> float:
    """``U_V = sqrt(U_SN**2 + U_D**2)`` (validation uncertainty)."""
    for nm, v in (("u_sn", u_sn), ("u_d", u_d)):
        if not math.isfinite(v) or v < 0.0:
            raise ValueError(f"{nm} must be finite and >= 0")
    return math.hypot(u_sn, u_d)


# ---------------------------------------------------------------------------
# adjudicator
# ---------------------------------------------------------------------------
@dataclass
class Adjudication:
    """Machine-checkable validation verdict for one comparison ``S`` vs ``E``."""

    name: str
    simulation: Optional[float]
    experiment: Optional[float]
    deviation: Optional[float]          # D = (S - E)/E
    u_gci: float
    u_input: float
    u_sn: float
    u_d: float
    u_v: float
    gate: float
    verdict: str
    ittc_consistent: Optional[bool]     # |D| <= U_V
    platform_gate_pass: Optional[bool]  # |D| <= gate
    discriminating: bool                # U_V < gate -> the gate can be resolved
    certified_band: float               # narrowest certifiable accuracy band
    claimed_band: Optional[float]
    claim_certified: Optional[bool]
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def adjudicate(
    name: str,
    *,
    simulation: Optional[float] = None,
    experiment: Optional[float] = None,
    deviation: Optional[float] = None,
    study: Optional[ConvergenceStudy] = None,
    u_gci: Optional[float] = None,
    u_input: float = 0.0,
    u_d: float = 0.0,
    gate: float = GATE_DEFAULT,
    claimed_band: Optional[float] = None,
) -> Adjudication:
    """Adjudicate a validation comparison against ``U_V`` and the 15 % gate.

    Decision rules (all machine-checkable):

    * the discretisation uncertainty comes either from a
      :class:`ConvergenceStudy` (``study.u_gci``; a non-certifiable study
      forces ``WITHHELD``) or from an explicit ``u_gci``;
    * ``U_SN = sqrt(U_GCI**2 + U_input**2)``, ``U_V = sqrt(U_SN**2 + U_D**2)``;
    * ``WITHHELD``      if the GCI study is not certifiable, or ``U_V >= gate``,
      or no comparison value exists;
    * ``VALIDATION_ACHIEVED``     if ``|D| <= U_V`` (ITTC 7.5-03-01-01);
    * ``VALIDATION_NOT_ACHIEVED`` otherwise.

    ``certified_band = U_V`` is the narrowest accuracy statement the evidence
    supports: a claim of band ``b`` is certified only if ``U_V <= b``.
    """
    if gate <= 0.0 or not math.isfinite(gate):
        raise ValueError("gate must be finite and > 0")
    reasons: List[str] = []

    if study is not None:
        u_gci_eff = float(study.u_gci)
        if not study.certifiable:
            reasons.append(
                "GCI study %r not certifiable (%s)"
                % (study.name, study.degradation or study.regime)
            )
    elif u_gci is not None:
        u_gci_eff = float(u_gci)
    else:
        raise ValueError("either study or u_gci must be supplied")

    u_sn = synthesize_u_sn(u_gci_eff, u_input)
    u_v = synthesize_u_v(u_sn, u_d)

    if deviation is None:
        if simulation is not None and experiment is not None and experiment != 0.0:
            deviation = (simulation - experiment) / experiment
    if deviation is not None and not math.isfinite(deviation):
        deviation = None

    if deviation is not None:
        reasons.append("D = %+.4f (%.2f%%)" % (deviation, 100.0 * deviation))
    reasons.append("U_GCI = %.4g, U_input = %.4g -> U_SN = %.4g"
                   % (u_gci_eff, u_input, u_sn))
    reasons.append("U_D = %.4g -> U_V = %.4g (gate %.2f%%)"
                   % (u_d, u_v, 100.0 * gate))

    undecidable = (study is not None and not study.certifiable) or u_v >= gate or deviation is None
    if undecidable:
        verdict = WITHHELD
        if u_v >= gate and deviation is not None:
            reasons.append("U_V >= gate: the band cannot resolve the acceptance gate")
        if deviation is None:
            reasons.append("no experimental/comparison reference available")
    elif abs(deviation) <= u_v:
        verdict = VALIDATION_ACHIEVED
        reasons.append("|D| <= U_V: model not disproved at this uncertainty level")
    else:
        verdict = VALIDATION_NOT_ACHIEVED
        reasons.append("|D| > U_V: comparison error exceeds the noise budget")

    ittc_consistent = None if deviation is None else abs(deviation) <= u_v
    gate_pass = None if deviation is None else abs(deviation) <= gate
    discriminating = u_v < gate

    claim_certified: Optional[bool] = None
    if claimed_band is not None:
        claim_certified = u_v <= claimed_band
        reasons.append(
            "claimed band %.2f%% %s (U_V = %.2f%%)"
            % (100.0 * claimed_band,
               "certifiable" if claim_certified else "NOT certifiable", 100.0 * u_v)
        )

    return Adjudication(
        name=name,
        simulation=simulation,
        experiment=experiment,
        deviation=deviation,
        u_gci=u_gci_eff,
        u_input=u_input,
        u_sn=u_sn,
        u_d=u_d,
        u_v=u_v,
        gate=gate,
        verdict=verdict,
        ittc_consistent=ittc_consistent,
        platform_gate_pass=gate_pass,
        discriminating=discriminating,
        certified_band=u_v,
        claimed_band=claimed_band,
        claim_certified=claim_certified,
        reasons=reasons,
    )


def re_adjudicate_claim(
    claim: Dict[str, object],
    *,
    u_d: float,
    u_input: float = 0.0,
    u_gci: float = 0.0,
    gate: float = GATE_DEFAULT,
) -> Adjudication:
    """Re-adjudicate one historical accuracy claim with an explicit ``U_D``.

    ``claim`` needs ``name``, ``deviation`` (signed relative ``D``),
    ``claimed_band`` and optionally ``simulation``/``experiment``.  For a
    closed-form empirical formula ``u_gci = 0`` (there is no discretisation),
    so ``U_SN = U_input``; the decisive quantity is then ``U_D``.
    """
    return adjudicate(
        str(claim.get("name", "claim")),
        simulation=claim.get("simulation"),          # type: ignore[arg-type]
        experiment=claim.get("experiment"),          # type: ignore[arg-type]
        deviation=claim.get("deviation"),            # type: ignore[arg-type]
        u_gci=u_gci,
        u_input=u_input,
        u_d=u_d,
        gate=gate,
        claimed_band=claim.get("claimed_band"),      # type: ignore[arg-type]
    )