"""Wageningen B-screw series open-water regression (Oosterveld & van Oossanen, 1975).

Reference implementation used as the *literature benchmark* for the platform's
open-water capability (``propeller.open_water``).  Until this module existed the
in-repo candidate kernels could only be audited structurally (physics invariants);
the numerical layer was WITHHELD because the published regression coefficient
table was not available offline (see ``docs/verification/propeller_open_water_audit.md``
and ``docs/reports/m1403-propeller-open-water-verification.md``).

Provenance of the coefficient table
-----------------------------------
Oosterveld, M.W.C. & van Oossanen, P. (1975), *Further computer-analyzed data of
the Wageningen B-screw series*, International Shipbuilding Progress 22(251).
The two tables (39 terms for ``KT``, 47 terms for ``KQ``) below are the widely
reproduced published regression.  The transcription in this file was **not**
typed by hand and **not** taken from a single source: it was extracted
programmatically from two independent public implementations
(``AlfredMylne/PropCalc`` and ``isulovsky/Wageningen-B-series-propeller-calculator``,
both fetched 2026-11-01) which agree term-for-term and to all printed digits.
Physical acceptance anchors of the resulting regression (checked in
``tests/test_wageningen_b_series.py``):

* ``KT(J=0)`` for Z=4, Ae/Ao=0.70 rises from 0.250 (P/D=0.60) to 0.624 (P/D=1.40);
* zero-thrust advance ratio ``J0/(P/D)`` = 1.06-1.08 for all P/D (geometric pitch);
* peak efficiency ``eta0`` = 0.54 (P/D=0.60) .. 0.76 (P/D=1.40), monotone in P/D.

Model
-----
    KT = sum_i  C_i * J^{s_i} * (P/D)^{t_i} * (Ae/Ao)^{u_i} * Z^{v_i}
    KQ = sum_i  D_i * J^{s_i} * (P/D)^{t_i} * (Ae/Ao)^{u_i} * Z^{v_i}
    eta0 = J * KT / (2 * pi * KQ)

Fit/validity domain (as published): Z = 2..7, Ae/Ao = 0.30..1.05,
P/D = 0.5..1.4, Reynolds number 2e6 (referenced to chord at 0.75R), smooth
blades, hub ratio 0.18-0.20.  Values are non-dimensional.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

__all__ = [
    "KT_COEFFICIENTS",
    "KQ_COEFFICIENTS",
    "DOMAIN",
    "DomainError",
    "kt",
    "kq",
    "eta0",
    "check_domain",
    "zero_thrust_advance_ratio",
    "best_efficiency_point",
    "open_water_curve",
]

#: Reference Reynolds number of the regression (chord at 0.75R).
RE_REFERENCE = 2.0e6

# (C, s[J], t[P/D], u[Ae/Ao], v[Z]) -- 39 terms, Oosterveld & van Oossanen (1975)
KT_COEFFICIENTS: Tuple[Tuple[float, int, int, int, int], ...] = (
    (+0.00880496, 0, 0, 0, 0),
    (-0.20455400, 1, 0, 0, 0),
    (+0.16635100, 0, 1, 0, 0),
    (+0.15811400, 0, 2, 0, 0),
    (-0.14758100, 2, 0, 1, 0),
    (-0.48149700, 1, 1, 1, 0),
    (+0.41543700, 0, 2, 1, 0),
    (+0.01440430, 0, 0, 0, 1),
    (-0.05300540, 2, 0, 0, 1),
    (+0.01434810, 0, 1, 0, 1),
    (+0.06068260, 1, 1, 0, 1),
    (-0.01258940, 0, 0, 1, 1),
    (+0.01096890, 1, 0, 1, 1),
    (-0.13369800, 0, 3, 0, 0),
    (+0.00638407, 0, 6, 0, 0),
    (-0.00132718, 2, 6, 0, 0),
    (+0.16849600, 3, 0, 1, 0),
    (-0.05072140, 0, 0, 2, 0),
    (+0.08545590, 2, 0, 2, 0),
    (-0.05044750, 3, 0, 2, 0),
    (+0.01046500, 1, 6, 2, 0),
    (-0.00648272, 2, 6, 2, 0),
    (-0.00841728, 0, 3, 0, 1),
    (+0.01684240, 1, 3, 0, 1),
    (-0.00102296, 3, 3, 0, 1),
    (-0.03177910, 0, 3, 1, 1),
    (+0.01860400, 1, 0, 2, 1),
    (-0.00410798, 0, 2, 2, 1),
    (-0.00060685, 0, 0, 0, 2),
    (-0.00498190, 1, 0, 0, 2),
    (+0.00259830, 2, 0, 0, 2),
    (-0.00056053, 3, 0, 0, 2),
    (-0.00163652, 1, 2, 0, 2),
    (-0.00032879, 1, 6, 0, 2),
    (+0.00011650, 2, 6, 0, 2),
    (+0.00069090, 0, 0, 1, 2),
    (+0.00421749, 0, 3, 1, 2),
    (+0.00005652, 3, 6, 1, 2),
    (-0.00146564, 0, 3, 2, 2),
)

# (C, s[J], t[P/D], u[Ae/Ao], v[Z]) -- 47 terms, Oosterveld & van Oossanen (1975)
KQ_COEFFICIENTS: Tuple[Tuple[float, int, int, int, int], ...] = (
    (+0.00379368, 0, 0, 0, 0),
    (+0.00886523, 2, 0, 0, 0),
    (-0.03224100, 1, 1, 0, 0),
    (+0.00344778, 0, 2, 0, 0),
    (-0.04088110, 0, 1, 1, 0),
    (-0.10800900, 1, 1, 1, 0),
    (-0.08853810, 2, 1, 1, 0),
    (+0.18856100, 0, 2, 1, 0),
    (-0.00370871, 1, 0, 0, 1),
    (+0.00513696, 0, 1, 0, 1),
    (+0.02094490, 1, 1, 0, 1),
    (+0.00474319, 2, 1, 0, 1),
    (-0.00723408, 2, 0, 1, 1),
    (+0.00438388, 1, 1, 1, 1),
    (-0.02694030, 0, 2, 1, 1),
    (+0.05580820, 3, 0, 1, 0),
    (+0.01618860, 0, 3, 1, 0),
    (+0.00318086, 1, 3, 1, 0),
    (+0.01589600, 0, 0, 2, 0),
    (+0.04717290, 1, 0, 2, 0),
    (+0.01962830, 3, 0, 2, 0),
    (-0.05027820, 0, 1, 2, 0),
    (-0.03005500, 3, 1, 2, 0),
    (+0.04171220, 2, 2, 2, 0),
    (-0.03977220, 0, 3, 2, 0),
    (-0.00350024, 0, 6, 2, 0),
    (-0.01068540, 3, 0, 0, 1),
    (+0.00110903, 3, 3, 0, 1),
    (-0.00031391, 0, 6, 0, 1),
    (+0.00359850, 3, 0, 1, 1),
    (-0.00142121, 0, 6, 1, 1),
    (-0.00383637, 1, 0, 2, 1),
    (+0.01268030, 0, 2, 2, 1),
    (-0.00318278, 2, 3, 2, 1),
    (+0.00334268, 0, 6, 2, 1),
    (-0.00183491, 1, 1, 0, 2),
    (+0.00011245, 3, 2, 0, 2),
    (-0.00002972, 3, 6, 0, 2),
    (+0.00026955, 1, 0, 1, 2),
    (+0.00083265, 2, 0, 1, 2),
    (+0.00155334, 0, 2, 1, 2),
    (+0.00030268, 0, 6, 1, 2),
    (-0.00018430, 0, 0, 2, 2),
    (-0.00042540, 0, 3, 2, 2),
    (+0.00008692, 3, 3, 2, 2),
    (-0.00046590, 0, 6, 2, 2),
    (+0.00005542, 1, 6, 2, 2),
)

#: Published validity domain.  Ranges are inclusive; ``j`` is open-ended upward
#: (the regression is a polynomial, useful slightly past the zero-thrust point).
DOMAIN = {
    "z": (2, 7),
    "p_d": (0.5, 1.4),
    "ae_ao": (0.30, 1.05),
    "j_min": -0.2,
}


class DomainError(ValueError):
    """Raised when a query leaves the published validity domain."""


def check_domain(j: float, p_d: float, ae_ao: float, z: int,
                 strict: bool = True) -> List[str]:
    """Return human-readable domain violations (raise ``DomainError`` if strict)."""
    problems: List[str] = []
    if not (DOMAIN["z"][0] <= z <= DOMAIN["z"][1]):
        problems.append(f"Z={z} outside [{DOMAIN['z'][0]}, {DOMAIN['z'][1]}]")
    if not (DOMAIN["p_d"][0] <= p_d <= DOMAIN["p_d"][1]):
        problems.append(f"P/D={p_d} outside [{DOMAIN['p_d'][0]}, {DOMAIN['p_d'][1]}]")
    if not (DOMAIN["ae_ao"][0] <= ae_ao <= DOMAIN["ae_ao"][1]):
        problems.append(f"Ae/Ao={ae_ao} outside [{DOMAIN['ae_ao'][0]}, "
                        f"{DOMAIN['ae_ao'][1]}]")
    if j < DOMAIN["j_min"]:
        problems.append(f"J={j} below {DOMAIN['j_min']}")
    if problems and strict:
        raise DomainError("; ".join(problems))
    return problems


def _poly(coeffs: Sequence[Sequence[float]], j: float, p_d: float,
          ae_ao: float, z: int) -> float:
    total = 0.0
    for c, s, t, u, v in coeffs:
        total += c * (j ** s) * (p_d ** t) * (ae_ao ** u) * (z ** v)
    return total


def kt(j: float, p_d: float, ae_ao: float, z: int, strict: bool = True) -> float:
    """Thrust coefficient ``KT = T / (rho n^2 D^4)`` of the B-series regression."""
    check_domain(j, p_d, ae_ao, z, strict=strict)
    return _poly(KT_COEFFICIENTS, j, p_d, ae_ao, z)


def kq(j: float, p_d: float, ae_ao: float, z: int, strict: bool = True) -> float:
    """Torque coefficient ``KQ = Q / (rho n^2 D^5)`` of the B-series regression."""
    check_domain(j, p_d, ae_ao, z, strict=strict)
    return _poly(KQ_COEFFICIENTS, j, p_d, ae_ao, z)


def eta0(j: float, p_d: float, ae_ao: float, z: int, strict: bool = True) -> float:
    """Open-water efficiency ``eta0 = J KT / (2 pi KQ)``; 0 where ``KQ <= 0``."""
    kt_v = kt(j, p_d, ae_ao, z, strict=strict)
    kq_v = kq(j, p_d, ae_ao, z, strict=strict)
    if kq_v <= 0.0:
        return 0.0
    return j * kt_v / (2.0 * math.pi * kq_v)


def zero_thrust_advance_ratio(p_d: float, ae_ao: float, z: int,
                              j_hi: float = 3.0) -> float:
    """First ``J`` with ``KT(J) = 0`` by bisection (``nan`` if none in ``[0, j_hi]``)."""
    if kt(0.0, p_d, ae_ao, z, strict=False) <= 0.0:
        return 0.0
    lo, hi = 0.0, j_hi
    if kt(hi, p_d, ae_ao, z, strict=False) > 0.0:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if kt(mid, p_d, ae_ao, z, strict=False) > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi)


@dataclass(frozen=True)
class OpenWaterPoint:
    """One open-water operating point of the B-series reference."""

    J: float
    P_D: float
    Ae_Ao: float
    Z: int
    KT: float
    KQ: float
    eta0: float

    def as_dict(self) -> Dict[str, float]:
        return {"J": self.J, "P_D": self.P_D, "Ae_Ao": self.Ae_Ao, "Z": self.Z,
                "KT": self.KT, "KQ": self.KQ, "eta0": self.eta0}


def best_efficiency_point(p_d: float, ae_ao: float, z: int,
                          samples: int = 400) -> OpenWaterPoint:
    """Peak-efficiency operating point (coarse scan + golden-section refinement)."""
    j0 = zero_thrust_advance_ratio(p_d, ae_ao, z)
    if not math.isfinite(j0) or j0 <= 0.0:
        raise DomainError("no zero-thrust advance ratio inside the scan window")
    best_j, best_eta = 0.0, -1.0
    for i in range(1, samples + 1):
        j = j0 * i / samples
        e = eta0(j, p_d, ae_ao, z, strict=False)
        if e > best_eta:
            best_j, best_eta = j, e
    # golden-section refinement inside the bracketing interval
    lo, hi = max(0.0, best_j - j0 / samples), min(j0, best_j + j0 / samples)
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    for _ in range(120):
        a = hi - phi * (hi - lo)
        b = lo + phi * (hi - lo)
        if eta0(a, p_d, ae_ao, z, strict=False) < eta0(b, p_d, ae_ao, z, strict=False):
            lo = a
        else:
            hi = b
    j_opt = 0.5 * (lo + hi)
    return OpenWaterPoint(j_opt, p_d, ae_ao, z,
                          kt(j_opt, p_d, ae_ao, z, strict=False),
                          kq(j_opt, p_d, ae_ao, z, strict=False),
                          eta0(j_opt, p_d, ae_ao, z, strict=False))


def open_water_curve(p_d: float, ae_ao: float, z: int,
                     j_values: Iterable[float]) -> List[OpenWaterPoint]:
    """Sample the B-series reference at the given advance ratios."""
    return [OpenWaterPoint(j, p_d, ae_ao, z,
                           kt(j, p_d, ae_ao, z, strict=False),
                           kq(j, p_d, ae_ao, z, strict=False),
                           eta0(j, p_d, ae_ao, z, strict=False))
            for j in j_values]
