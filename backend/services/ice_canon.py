"""Canonical (reference) level-ice resistance decomposition — anchor implementation.

Purpose
-------
This module is a **reference model**, not a production path.  It exists so that
the platform's operating ice-resistance implementations can be checked against
derivable physics instead of against each other:

* ``backend/services/ice_resistance_uq.py``  (Lindqvist-form, per-ship calibrated)
* ``optimizer/ice_class/objectives.py``      (Lindqvist-named heuristic)
* ``backend/services/polar/resistance.py``   (first-principles decomposition,
  the polar voyage optimiser's production path)

Every coefficient below is either *derived* (and the derivation is written out)
or explicitly flagged as a convention whose value is literature-ambiguous, so
that a comparison against this module never silently absorbs a coefficient
choice into a "validated" claim.

Model TP-ICE-ANCHOR
-------------------
Notation: ``h`` level-ice thickness, ``sigma_c`` uniaxial compressive strength,
``sigma_f`` flexural strength, ``mu`` hull-ice friction, ``B`` beam, ``T``
draft, ``Cb`` block coefficient, ``alpha`` waterline entrance angle measured
from the horizontal, ``beta`` buttock-rise angle.

1. Crushing (process zone).  Ice is crushed over a process-zone area of order
   ``h^2`` at the waterline; the horizontal projection of the plate-normal
   pressure is ``sin(alpha)`` and the tangential friction contribution is
   ``mu*cos(alpha)``::

       R_cr = C_cr * sigma_c * h^2 * (sin(alpha) + mu*cos(alpha))

   ``C_cr`` is a geometry/process-zone factor of order 1 (convention, 0.5-1.0
   in the literature); the *scaling* ``R_cr ~ sigma_c * h^2`` is not
   convention-dependent and is the anchor used by the audit.

2. Breaking (bending).  The ice sheet is a semi-infinite beam on an elastic
   foundation with ``k = rho_w*g`` per unit width, ``EI = E*h^3/(12*(1-nu^2))``::

       l_c = (4*EI/k)^(1/4) = (E*h^3/(12*rho_w*g*(1-nu^2)))^(1/4)  ~ h^(3/4)

   The failure moment per unit width of a rectangular ice section is
   ``M_f = sigma_f*h^2/6``; for a point load on an infinite beam
   ``M_max = P*l_c/4``, hence the failure force per unit width and the
   resistance over the broken channel width ``B`` are::

       P_f = 4*M_f/l_c = (2/3) * sigma_f * h^2 / l_c
       R_br = C_br * P_f * B

   ``C_br`` is the convention factor (the often-quoted Lindqvist form uses
   ~1/2 of this, i.e. ``sigma_f*h^2/(3*l_c)*B``, which corresponds to a
   different beam end condition).  The anchor for the audit is the pair of
   exponents ``R_br ~ sigma_f * h^(1.25) * B`` — independent of ``C_br``.

3. Submersion.  Broken ice is pushed down and along the hull; the buoyancy
   imbalance sets the normal force over the submerged bow area ``A_sub``::

       A_sub    = Cb * B * T                      (projected submerged area)
       R_sub    = (rho_w - rho_i) * g * h * A_sub * (mu + sin(beta))
       beta     = atan(2*T/L)                     (buttock-rise proxy)

   Anchor: ``R_sub ~ h^1 * B^1 * T^1``, increasing in draft.

4. Dynamic term.  Only the submersion (added-mass) component is speed
   dependent in the classical decomposition::

       R_sub(V) = R_sub(0) * (1 + C_v * V / sqrt(g*h))

   Anchor: monotone increasing in ``V``, coefficient ``C_v`` a convention.

All forces are returned in newton.

References (form-alike, coefficient conventions differ between them; this
module states its own convention explicitly above)
    Lindqvist, G. (1989), A straightforward method for the calculation of ice
        resistance of ships, POAC '89.
    Riska, K. et al. (1997), Full scale measurements of ice loads on MV Arctic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict

#: Convention factors (documented, not fitted).
C_CRUSH = 1.0   #: process-zone geometry factor [-]
C_BREAK = 2.0 / 3.0  #: beam-on-elastic-foundation point-load factor [-]
C_FLEX = 0.5    #: sigma_f / sigma_c for first-year ice [-]
C_VEL = 0.5     #: added-mass coefficient on submersion [-]

RHO_W = 1025.0  # kg/m3
RHO_I = 917.0   # kg/m3
G = 9.81        # m/s2
E_ICE = 2.5e9   # Pa
NU_ICE = 0.33   # -


def characteristic_length(h_m: float, e_ice: float = E_ICE, nu: float = NU_ICE,
                         rho_w: float = RHO_W) -> float:
    """Beam-on-elastic-foundation characteristic length ``l_c`` [m].

    ``l_c = (E*h^3/(12*rho_w*g*(1-nu^2)))^(1/4)``, i.e. ``l_c ~ h^(3/4)``.
    """
    if h_m <= 0.0:
        raise ValueError("h_m must be positive")
    d = e_ice * h_m ** 3 / (12.0 * (1.0 - nu ** 2))
    return (d / (rho_w * G)) ** 0.25


@dataclass(frozen=True)
class IceComponents:
    """Decomposition of level-ice resistance [N]."""

    crushing_n: float
    breaking_n: float
    submersion_n: float
    speed_term_n: float

    @property
    def total_n(self) -> float:
        return self.crushing_n + self.breaking_n + self.submersion_n + self.speed_term_n

    def as_dict(self) -> Dict[str, float]:
        return {
            "crushing_n": self.crushing_n,
            "breaking_n": self.breaking_n,
            "submersion_n": self.submersion_n,
            "speed_term_n": self.speed_term_n,
            "total_n": self.total_n,
        }


def ice_components(
    *,
    length_m: float,
    beam_m: float,
    draft_m: float,
    cb: float,
    bow_angle_deg: float,
    h_m: float,
    sigma_c_pa: float,
    mu: float,
    v_ms: float,
    c_crush: float = C_CRUSH,
    c_break: float = C_BREAK,
    c_flex: float = C_FLEX,
    c_vel: float = C_VEL,
) -> IceComponents:
    """Reference level-ice resistance decomposition (see module docstring).

    ``bow_angle_deg`` is the waterline entrance angle from the horizontal,
    matching the convention used by the other in-repo implementations.
    """
    if h_m <= 0.0:
        raise ValueError("h_m must be positive")
    if sigma_c_pa <= 0.0:
        raise ValueError("sigma_c_pa must be positive")
    if min(length_m, beam_m, draft_m) <= 0.0:
        raise ValueError("principal dimensions must be positive")

    alpha = math.radians(bow_angle_deg)
    # sin(alpha) is never zero in a real bow; guard the degenerate input only.
    s_a, c_a = math.sin(alpha), math.cos(alpha)

    crushing = c_crush * sigma_c_pa * h_m ** 2 * (s_a + mu * c_a)

    l_c = characteristic_length(h_m)
    sigma_f = c_flex * sigma_c_pa
    breaking = c_break * sigma_f * h_m ** 2 / l_c * beam_m

    beta = math.atan(2.0 * draft_m / length_m)
    area_sub = cb * beam_m * draft_m
    submersion = (RHO_W - RHO_I) * G * h_m * area_sub * (mu + math.sin(beta))

    speed_term = submersion * (c_vel * v_ms / math.sqrt(G * h_m))

    return IceComponents(
        crushing_n=crushing,
        breaking_n=breaking,
        submersion_n=submersion,
        speed_term_n=speed_term,
    )


__all__ = [
    "C_BREAK",
    "C_CRUSH",
    "C_FLEX",
    "C_VEL",
    "IceComponents",
    "characteristic_length",
    "ice_components",
]