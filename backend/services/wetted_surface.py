"""Wetted-surface estimation for the resistance chain (m1637 verification debt).

Why this module exists
----------------------
``backend/api/calculations.py`` and ``services/ship_resistance.py`` used a single
hard-wired estimate for the wetted surface area::

    S = pi * B * L * (0.5 + 0.25 * Cb)          # "legacy axisymmetric fit"

That expression was calibrated against an axisymmetric body whose ``B`` is the
*maximum diameter*, so it is only meaningful when ``T ~ B/2``.  Applied to the
two families the platform actually serves it misses in opposite directions:

* body of revolution (``T ~ B/2``): under-predicts, -20 % (spheroid) to -30 %
  (cylinder), i.e. below the 15 % acceptance bar in both cases;
* ship hull (``B/T ~ 3``): over-predicts grossly, because ``pi*B*L`` no longer
  describes the hull envelope.  KVLCC2 (L=320 m, B=58 m, T=20.8 m, Cb=0.8098)
  gets 40 965 m^2 from the legacy fit against a 27 100 m^2 class ship hull,
  i.e. +51 % and a +51 % biased frictional resistance.

Two estimators, each validated against closed-form references
-------------------------------------------------------------
``denny_mumford``  -- ship forms (``B/T >= 1.5``) ::

    S = 1.025 * L * (1.7 * T + Cb * B)

``capped_cylinder_equivalent`` -- bodies of revolution : keep the mid diameter
``B`` as well as the length, solve the cap semi-length from volume closure and
use the closed-form area of the resulting parallel-midbody body (exact for
spheroids, capsules and closed cylinders).  ``spheroid_equivalent`` is retained
as the one-parameter (volume only) variant

    a = L/2,  b = sqrt(3V / (4*pi*a)),  e = sqrt(1 - b^2/a^2)
    S = 2*pi*b^2 + 2*pi*a*b*asin(e)/e            (-> 4*pi*a^2 as e -> 0)

``estimate_wetted_surface`` picks the branch from the non-dimensional geometry
(``B/T``, ``L/B``, ``Cb``) and returns the model tag so the response can record
which model produced the number.  ``legacy`` is kept for bit-exact
reproducibility of pre-m1637 results.

Evidence
--------
``tools/verif/verify_wetted_surface.py`` re-derives every reference from closed
form (cylinder, spheroids, cone, capsule, sphere, barge) or from the platform's
independent hydrostatics integration (Wigley hull), and pins literature values
for KCS / KVLCC2 / DTMB-5415.  The measured error table is dumped to
``artifacts/m1637/wetted_surface_validation.json`` and summarised in
``docs/reports/m1637_wetted_surface_engine.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

#: Denominator factor of the classic Denny-Mumford / Holtrop-London shell
#: allowance (1.025 = 2.5 % appendage+shell roughness allowance).
DENNY_MUMFORD_SHELL_FACTOR = 1.025

#: Branch selection thresholds (dimensionless geometry only).
SHIP_MIN_BEAM_DRAFT_RATIO = 1.4
SHIP_MIN_LENGTH_BEAM_RATIO = 4.0
SHIP_MIN_CB = 0.35
SHIP_MAX_CB = 1.0

MODEL_DENNY_MUMFORD = "denny_mumford"
MODEL_CAPPED_CYLINDER = "capped_cylinder_equivalent"
MODEL_SPHEROID = "spheroid_equivalent"
MODEL_LEGACY = "empirical_formula"
MODEL_INPUT = "input_validated"


class WettedSurfaceError(ValueError):
    """Raised when the supplied geometry cannot support any estimate."""


def legacy_axisymmetric(length: float, beam: float, cb: float) -> float:
    """Pre-m1637 estimator, kept bit-exact for reproducibility.

    ``S = pi*B*L*(0.5 + 0.25*Cb)``.  Valid only for axisymmetric bodies with
    ``T ~ B/2``; documented biases are pinned in the test suite.
    """
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def denny_mumford(length: float, beam: float, draft: float, cb: float) -> float:
    """Ship-form wetted surface, ``S = 1.025*L*(1.7*T + Cb*B)``.

    Reference: Denny-Mumford statistical fit, reproduced as eq. (4) of the
    Holtrop-Mennen resistance method (Holtrop & Mennen, ISP 1982) and in
    ITTC 7.5-02-02-01 §3.  Valid for conventional displacement hulls
    (``0.35 <= Cb <= 0.90``).  It has no appendage term: rudder, bilge keels
    and shafting must be added by the caller.
    """
    for name, value in (("length", length), ("beam", beam), ("draft", draft)):
        if not (value > 0.0):
            raise WettedSurfaceError(f"{name} must be positive, got {value}")
    if not (0.0 < cb <= 1.0):
        raise WettedSurfaceError(f"cb must be in (0, 1], got {cb}")
    return DENNY_MUMFORD_SHELL_FACTOR * length * (1.7 * draft + cb * beam)


def spheroid_equivalent(length: float, beam: float, draft: float, cb: float) -> float:
    """Exact lateral area of the volume-equivalent prolate spheroid.

    Length is preserved, the displaced volume ``V = Cb*L*B*T`` is preserved and
    the mid diameter is whatever closes the volume.  Exact for spheroids and
    for the sphere (degenerate ``e -> 0``).
    """
    for name, value in (("length", length), ("beam", beam), ("draft", draft)):
        if not (value > 0.0):
            raise WettedSurfaceError(f"{name} must be positive, got {value}")
    volume = cb * length * beam * draft
    if not (volume > 0.0):
        raise WettedSurfaceError(f"non-positive displaced volume {volume}")

    a = 0.5 * length
    b_sq = 3.0 * volume / (4.0 * math.pi * a)
    if b_sq >= a * a:
        # Blunter than a sphere: fall back to the volume-equivalent sphere,
        # which is the bounded maximum of the family (honest overshoot guard).
        r = (3.0 * volume / (4.0 * math.pi)) ** (1.0 / 3.0)
        return 4.0 * math.pi * r * r

    b = math.sqrt(b_sq)
    e = math.sqrt(1.0 - b_sq / (a * a))
    if e < 1e-12:  # sphere
        return 4.0 * math.pi * a * a
    return 2.0 * math.pi * b * b + 2.0 * math.pi * a * b * math.asin(e) / e


def capped_cylinder_equivalent(length: float, beam: float, draft: float, cb: float) -> float:
    """Closed body of revolution: parallel midbody of diameter ``beam`` + two spheroidal caps.

    The mid diameter is taken from the request (``R = B/2``), so — unlike
    :func:`spheroid_equivalent` — the shape is described by *two* parameters
    (volume and mid diameter) instead of volume alone.  The cap semi-length
    ``c`` is the only unknown and follows from volume closure::

        V = pi*R^2*L - (2/3)*pi*R^2*c   ->   c = 1.5*(L - V/(pi*R^2)),  0 <= c <= L/2

    and the lateral area of the resulting body is::

        S = 2*pi*R*(L - 2c) + 2*(pi*R^2 + pi*c*R*asin(e)/e),
        e = sqrt(1 - (R/c)^2)  (e -> 0, asin(e)/e -> 1 for c <= R)

    Exact for a prolate spheroid (``c = L/2``), for a closed cylinder
    (``c -> 0``, area includes the two end discs) and for a capsule with
    hemispherical caps (``c = R``); the residual errors are carried by the
    capsule/spheroid family, i.e. the shapes the resistance endpoint actually
    sees (see ``tools/verif/verify_wetted_surface.py``).
    """
    for name, value in (("length", length), ("beam", beam), ("draft", draft)):
        if not (value > 0.0):
            raise WettedSurfaceError(f"{name} must be positive, got {value}")
    volume = cb * length * beam * draft
    if not (volume > 0.0):
        raise WettedSurfaceError(f"non-positive displaced volume {volume}")

    r = 0.5 * beam
    mid_length = volume / (math.pi * r * r)          # length of an equal-volume cylinder
    c = 1.5 * (length - mid_length)
    c = min(max(c, 0.0), 0.5 * length)               # clamp: no caps .. full spheroid

    cap_len = length - 2.0 * c
    if c <= 0.0:
        # degenerate: straight cylinder with flat ends
        return 2.0 * math.pi * r * length + 2.0 * math.pi * r * r

    if c <= r:
        g = 1.0     # asin(e)/e -> 1 as e -> 0
    else:
        e = math.sqrt(1.0 - (r * r) / (c * c))
        g = math.asin(e) / e
    return 2.0 * math.pi * r * cap_len + 2.0 * (math.pi * r * r + math.pi * c * r * g)


def is_ship_form(length: float, beam: float, draft: Optional[float], cb: float) -> bool:
    """True when the dimensionless geometry looks like a conventional hull.

    Body-of-revolution requests declared with ``T = B`` fail the ``B/T`` test;
    very fine or boxy sections fail the ``Cb`` band and stay on the
    body-of-revolution branch.
    """
    if draft is None or draft <= 0.0:
        return False
    if length <= 0.0 or beam <= 0.0:
        return False
    return (
        beam / draft >= SHIP_MIN_BEAM_DRAFT_RATIO
        and length / beam >= SHIP_MIN_LENGTH_BEAM_RATIO
        and SHIP_MIN_CB <= cb <= SHIP_MAX_CB
    )


@dataclass(frozen=True)
class WettedSurfaceEstimate:
    """Result of :func:`estimate_wetted_surface` with provenance."""

    area_m2: float
    model: str
    branch_reason: str
    length: float
    beam: float
    draft: float
    cb: float

    def as_dict(self) -> dict:
        return asdict(self)


def estimate_wetted_surface(
    length: float,
    beam: float,
    draft: Optional[float],
    cb: float,
    model: str = "auto",
) -> WettedSurfaceEstimate:
    """Estimate the wetted surface area, recording which model was used.

    ``model`` accepts ``"auto"`` (default), ``"denny_mumford"``,
    ``"capped_cylinder_equivalent"``, ``"spheroid_equivalent"`` and ``"legacy"``.
    ``"auto"`` selects the ship branch when ``B/T >= 1.4``, ``L/B >= 4`` and
    ``0.35 <= Cb <= 1.0``; otherwise the body-of-revolution branch (capped
    cylinder).  A body of revolution described with ``T = B`` (the SUBOFF
    default of this API) has ``B/T = 1``, while a fine ship hull stays above
    ``B/T = 1.4`` (Wigley 1.6, KCS 2.98, KVLCC2 2.79).  Because the draft
    convention is the only ambiguity left, callers that know the body is
    axisymmetric should force ``model="capped_cylinder_equivalent"``.
    """
    key = (model or "auto").strip().lower()
    if key in {"auto", ""}:
        if is_ship_form(length, beam, draft, cb):
            est = WettedSurfaceEstimate(
                area_m2=denny_mumford(length, beam, draft, cb),
                model=MODEL_DENNY_MUMFORD,
                branch_reason=(
                    f"ship form: B/T={beam / draft:.2f}>={SHIP_MIN_BEAM_DRAFT_RATIO}, "
                    f"L/B={length / beam:.2f}>={SHIP_MIN_LENGTH_BEAM_RATIO}, "
                    f"Cb={cb:.3f} in [{SHIP_MIN_CB}, {SHIP_MAX_CB}]"
                ),
                length=length,
                beam=beam,
                draft=draft,
                cb=cb,
            )
        elif draft is not None and draft > 0.0:
            est = WettedSurfaceEstimate(
                area_m2=capped_cylinder_equivalent(length, beam, draft, cb),
                model=MODEL_CAPPED_CYLINDER,
                branch_reason=(
                    f"body of revolution: B/T={beam / draft:.2f} < 2.4 or Cb={cb:.3f} "
                    "outside the ship band -> capped-cylinder equivalent (mid diameter kept)"
                ),
                length=length,
                beam=beam,
                draft=draft,
                cb=cb,
            )
        else:
            raise WettedSurfaceError(
                "auto branch needs a positive draft; pass draft or an explicit "
                "wetted_surface_m2 from validated geometry"
            )
        return est

    if key == "legacy":
        return WettedSurfaceEstimate(
            area_m2=legacy_axisymmetric(length, beam, cb),
            model=MODEL_LEGACY,
            branch_reason="legacy axisymmetric fit (pre-m1637, reproducible only)",
            length=length,
            beam=beam,
            draft=draft if draft is not None else float("nan"),
            cb=cb,
        )
    if key == "denny_mumford":
        if draft is None or draft <= 0.0:
            raise WettedSurfaceError("denny_mumford needs a positive draft")
        return WettedSurfaceEstimate(
            area_m2=denny_mumford(length, beam, draft, cb),
            model=MODEL_DENNY_MUMFORD,
            branch_reason="caller forced ship branch",
            length=length,
            beam=beam,
            draft=draft,
            cb=cb,
        )
    if key == "capped_cylinder_equivalent":
        if draft is None or draft <= 0.0:
            raise WettedSurfaceError("capped_cylinder_equivalent needs a positive draft")
        return WettedSurfaceEstimate(
            area_m2=capped_cylinder_equivalent(length, beam, draft, cb),
            model=MODEL_CAPPED_CYLINDER,
            branch_reason="caller forced body-of-revolution branch (capped cylinder)",
            length=length,
            beam=beam,
            draft=draft,
            cb=cb,
        )
    if key == "spheroid_equivalent":
        if draft is None or draft <= 0.0:
            raise WettedSurfaceError("spheroid_equivalent needs a positive draft")
        return WettedSurfaceEstimate(
            area_m2=spheroid_equivalent(length, beam, draft, cb),
            model=MODEL_SPHEROID,
            branch_reason="caller forced body-of-revolution branch",
            length=length,
            beam=beam,
            draft=draft,
            cb=cb,
        )
    raise WettedSurfaceError(
        f"unknown wetted-surface model {model!r}; "
        "expected auto / denny_mumford / capped_cylinder_equivalent / "
        "spheroid_equivalent / legacy"
    )