#!/usr/bin/env python3
"""
propulsion_control — synthetic control candidate for the propulsion verifier battery.

NOT a reference implementation of the Wageningen B-series and NOT usable for design
work.  Its only job is to serve as a *negative control for the verifier itself*: a
model built to satisfy every invariant in propulsion_openwater.py (efficiency
definition, actuator-disk bound, P/D sensitivity, bollard efficiency, strictly
decreasing KT, non-constant KQ/KT, distinct blade counts).  If the invariant battery
fails this candidate, the battery is too strict and must be fixed before it is
allowed to condemn the platform.

Construction:
  1. KT(J, P/D, Z, Ae/A0) is a smooth, strictly decreasing polynomial in J whose
     zero-thrust advance ratio grows with P/D, with weak Ae/A0 and Z dependence.
  2. eta0 is prescribed as a fixed fraction (0.80) of the momentum-theory ideal
     efficiency at the same (KT, J) — this guarantees the physical upper bound.
  3. KQ is then *back-solved* from the efficiency definition,
         KQ = J * KT / (2*pi*eta0),
     so the definitional identity holds to round-off by construction.
"""
from __future__ import annotations

import math

_AE_REF = 0.65


def _eta_ideal(KT: float, J: float) -> float:
    ct = 8.0 * KT / (math.pi * J * J)
    return 2.0 / (1.0 + math.sqrt(1.0 + ct))


def control_sampler(J: float, P_D: float, Z: int, Ae_A0: float) -> dict[str, float]:
    """Return {"KT","KQ","eta0"} from the synthetic admissible control model."""
    # blade-count weight (distinct, monotone, no wrap-around)
    z_w = {2: 0.95, 3: 0.975, 4: 1.0, 5: 1.02, 6: 1.035, 7: 1.045}.get(Z, 1.0)
    # weak blade-area effect (real B-series dependence on Ae/A0 is weak)
    ae_w = 1.0 + 0.12 * (Ae_A0 - _AE_REF)

    # zero-thrust advance ratio scales with pitch
    j0 = 0.85 * P_D + 0.10
    # bollard thrust coefficient grows with pitch
    kt0 = 0.11 * P_D + 0.09

    x = J / j0
    kt = kt0 * (1.0 - x) * (1.0 + 0.25 * x) * z_w * ae_w

    if J <= 0.0:
        # bollard condition: eta0 = 0; KQ finite and positive
        eta = 0.0
        kq = 0.02 * P_D + 0.008
        return {"KT": kt, "KQ": kq, "eta0": eta}

    if kt > 0.0:
        eta = 0.80 * _eta_ideal(kt, J)
        kq = J * kt / (2.0 * math.pi * eta)
    else:
        # past zero thrust: propelling/windmilling branch, keep KQ positive
        kq = 0.004 + 0.002 * abs(kt)
        eta = J * kt / (2.0 * math.pi * kq)   # negative: windmilling branch
    return {"KT": kt, "KQ": kq, "eta0": eta}