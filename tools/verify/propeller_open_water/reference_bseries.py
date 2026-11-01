"""Clean-room reference implementation of the Wageningen B-series open-water
polynomials (Oosterveld & van Oossanen, 1975).

Motivation
----------
The ship-performance platform exposes several "Wageningen B-series" open-water
implementations.  Before any of them can be certified as REAL, an independent
reference is needed.  This module is that reference: the published 39-term
``KT`` and 47-term ``KQ`` regression tables, transcribed once, and evaluated in
the standard polynomial form

    KT = sum_n  C_T,n * J^s_n * (P/D)^t_n * (Ae/A0)^u_n * Z^v_n
    KQ = sum_n  C_Q,n * J^s_n * (P/D)^t_n * (Ae/A0)^u_n * Z^v_n

Provenance of the coefficient table
-----------------------------------
The table below is the Oosterveld & van Oossanen (1975) set as reproduced in
Bernitsas, Ray & Kinley (1981), *KT, KQ and Efficiency Curves for the Wageningen
B-Series Propellers*, Report No. 237, University of Michigan (the same table is
shipped by the reference open-source implementation ``mkergoat/bseries``).
Every coefficient is cross-checked, term by term, against the platform's own
``backend/data/official/Wageningen_Bseries_coeffs.dat`` by
``tests/verify/test_propeller_open_water_audit.py`` so that transcription
errors cannot hide behind the audit.

Validity box reported in the source:  Z = 2..7, P/D = 0.5..1.4,
Ae/A0 = 0.2..1.05, J = 0..1.5 (out-of-box values are extrapolation).

The dimensionless convention is the B-series standard:
    KT = T / (rho n^2 D^4),  KQ = Q / (rho n^2 D^5),  eta_0 = J KT / (2 pi KQ).
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Tuple

# (coefficient, J exponent, P/D exponent, Ae/A0 exponent, Z exponent)
KT_COEFFICIENTS: List[Tuple[float, int, int, int, int]] = [
    (0.00880496, 0, 0, 0, 0),
    (-0.204554, 1, 0, 0, 0),
    (0.166351, 0, 1, 0, 0),
    (0.158114, 0, 2, 0, 0),
    (-0.147581, 2, 0, 1, 0),
    (-0.481497, 1, 1, 1, 0),
    (0.415437, 0, 2, 1, 0),
    (0.0144043, 0, 0, 0, 1),
    (-0.0530054, 2, 0, 0, 1),
    (0.0143481, 0, 1, 0, 1),
    (0.0606826, 1, 1, 0, 1),
    (-0.0125894, 0, 0, 1, 1),
    (0.0109689, 1, 0, 1, 1),
    (-0.133698, 0, 3, 0, 0),
    (0.00638407, 0, 6, 0, 0),
    (-0.00132718, 2, 6, 0, 0),
    (0.168496, 3, 0, 1, 0),
    (-0.0507214, 0, 0, 2, 0),
    (0.0854559, 2, 0, 2, 0),
    (-0.0504475, 3, 0, 2, 0),
    (0.010465, 1, 6, 2, 0),
    (-0.00648272, 2, 6, 2, 0),
    (-0.00841728, 0, 3, 0, 1),
    (0.0168424, 1, 3, 0, 1),
    (-0.00102296, 3, 3, 0, 1),
    (-0.0317791, 0, 3, 1, 1),
    (0.018604, 1, 0, 2, 1),
    (-0.00410798, 0, 2, 2, 1),
    (-0.000606848, 0, 0, 0, 2),
    (-0.0049819, 1, 0, 0, 2),
    (0.0025983, 2, 0, 0, 2),
    (-0.000560528, 3, 0, 0, 2),
    (-0.00163652, 1, 2, 0, 2),
    (-0.000328787, 1, 6, 0, 2),
    (0.000116502, 2, 6, 0, 2),
    (0.000690904, 0, 0, 1, 2),
    (0.00421749, 0, 3, 1, 2),
    (5.65229e-05, 3, 6, 1, 2),
    (-0.00146564, 0, 3, 2, 2),
]

KQ_COEFFICIENTS: List[Tuple[float, int, int, int, int]] = [
    (0.00379368, 0, 0, 0, 0),
    (0.00886523, 2, 0, 0, 0),
    (-0.032241, 1, 1, 0, 0),
    (0.00344778, 0, 2, 0, 0),
    (-0.0408811, 0, 1, 1, 0),
    (-0.108009, 1, 1, 1, 0),
    (-0.0885381, 2, 1, 1, 0),
    (0.188561, 0, 2, 1, 0),
    (-0.00370871, 1, 0, 0, 1),
    (0.00513696, 0, 1, 0, 1),
    (0.0209449, 1, 1, 0, 1),
    (0.00474319, 2, 1, 0, 1),
    (-0.00723408, 2, 0, 1, 1),
    (0.00438388, 1, 1, 1, 1),
    (-0.0269403, 0, 2, 1, 1),
    (0.0558082, 3, 0, 1, 0),
    (0.0161886, 0, 3, 1, 0),
    (0.00318086, 1, 3, 1, 0),
    (0.015896, 0, 0, 2, 0),
    (0.0471729, 1, 0, 2, 0),
    (0.0196283, 3, 0, 2, 0),
    (-0.0502782, 0, 1, 2, 0),
    (-0.030055, 3, 1, 2, 0),
    (0.0417122, 2, 2, 2, 0),
    (-0.0397722, 0, 3, 2, 0),
    (-0.00350024, 0, 6, 2, 0),
    (-0.0106854, 3, 0, 0, 1),
    (0.00110903, 3, 3, 0, 1),
    (-0.000313912, 0, 6, 0, 1),
    (0.0035985, 3, 0, 1, 1),
    (-0.00142121, 0, 6, 1, 1),
    (-0.00383637, 1, 0, 2, 1),
    (0.0126803, 0, 2, 2, 1),
    (-0.00318278, 2, 3, 2, 1),
    (0.00334268, 0, 6, 2, 1),
    (-0.00183491, 1, 1, 0, 2),
    (0.000112451, 3, 2, 0, 2),
    (-2.97228e-05, 3, 6, 0, 2),
    (0.000269551, 1, 0, 1, 2),
    (0.00083265, 2, 0, 1, 2),
    (0.00155334, 0, 2, 1, 2),
    (0.000302683, 0, 6, 1, 2),
    (-0.0001843, 0, 0, 2, 2),
    (-0.000425399, 0, 3, 2, 2),
    (8.69243e-05, 3, 3, 2, 2),
    (-0.0004689, 0, 6, 2, 2),
    (5.54194e-05, 1, 6, 2, 2),
]

#: Reported validity box of the published polynomial fit.
VALIDITY = {
    "J": (0.0, 1.5),
    "P_D": (0.5, 1.4),
    "Ae_Ao": (0.2, 1.05),
    "Z": (2, 7),
}


def _eval(coeffs: Iterable[Tuple[float, int, int, int, int]],
          J: float, P_D: float, Ae_Ao: float, Z: int) -> float:
    total = 0.0
    for coef, s, t, u, v in coeffs:
        total += coef * (J ** s) * (P_D ** t) * (Ae_Ao ** u) * (Z ** v)
    return total


def open_water_coefficients(J: float, P_D: float, Ae_Ao: float, Z: int) -> Dict[str, float]:
    """Return the canonical ``KT``, ``KQ`` and ``eta_0`` for one operating point."""
    kt = _eval(KT_COEFFICIENTS, J, P_D, Ae_Ao, Z)
    kq = _eval(KQ_COEFFICIENTS, J, P_D, Ae_Ao, Z)
    eta = (J / (2.0 * math.pi)) * (kt / kq) if abs(kq) > 1e-12 else float("nan")
    return {"J": J, "P_D": P_D, "Ae_Ao": Ae_Ao, "Z": Z, "KT": kt, "KQ": kq, "eta_0": eta}


def open_water_curve(P_D: float, Ae_Ao: float, Z: int,
                     Js: Iterable[float] | None = None) -> List[Dict[str, float]]:
    """Return the canonical open-water curve over a list of advance coefficients."""
    if Js is None:
        Js = [round(0.05 * i, 2) for i in range(31)]  # J = 0.00 .. 1.50
    return [open_water_coefficients(J, P_D, Ae_Ao, Z) for J in Js]


def zero_thrust_J(P_D: float, Ae_Ao: float, Z: int,
                  lo: float = 0.0, hi: float = 1.5, tol: float = 1e-10) -> float:
    """Advance coefficient at which thrust vanishes (bisection on KT)."""
    f_lo = open_water_coefficients(lo, P_D, Ae_Ao, Z)["KT"]
    f_hi = open_water_coefficients(hi, P_D, Ae_Ao, Z)["KT"]
    if f_lo * f_hi > 0.0:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = open_water_coefficients(mid, P_D, Ae_Ao, Z)["KT"]
        if f_lo * f_mid <= 0.0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def peak_efficiency(P_D: float, Ae_Ao: float, Z: int,
                    lo: float = 0.0, hi: float = 1.4) -> Dict[str, float]:
    """Locate ``eta_0`` maximum on a fine grid (B-series curves are unimodal)."""
    best = {"J": float("nan"), "eta_0": -1.0, "KT": float("nan"), "KQ": float("nan")}
    n = 1400
    for i in range(n + 1):
        J = lo + (hi - lo) * i / n
        c = open_water_coefficients(J, P_D, Ae_Ao, Z)
        # Physical branch only: eta_0 diverges where KQ crosses zero.
        if c["KT"] <= 0.0 or c["KQ"] <= 0.0:
            continue
        if c["eta_0"] == c["eta_0"] and c["eta_0"] > best["eta_0"]:
            best = {"J": J, "eta_0": c["eta_0"], "KT": c["KT"], "KQ": c["KQ"]}
    return best


def blasius_itcc_kt(P_D: float, Ae_Ao: float, Z: int, J: float) -> Dict[str, float]:
    """Ideal (shaft) efficiency upper bound: eta_ideal = 1 - J*KQ' / KT ...

    Only used as a physical sanity rail: for any real propeller
    ``eta_0 = J KT / (2 pi KQ)`` must stay below the actuator-disc ideal
    efficiency ``eta_ideal = 2 / (1 + sqrt(1 + C_T))`` with
    ``C_T = 8 KT / (pi J^2)`` for J > 0.
    """
    c = open_water_coefficients(J, P_D, Ae_Ao, Z)
    if J <= 0.0 or c["KT"] <= 0.0:
        raise ValueError("ideal-efficiency rail needs J > 0 and KT > 0")
    c_t = 8.0 * c["KT"] / (math.pi * J * J)
    eta_ideal = 2.0 / (1.0 + math.sqrt(1.0 + c_t))
    return {"eta_0": c["eta_0"], "eta_ideal": eta_ideal, "ratio": c["eta_0"] / eta_ideal}


if __name__ == "__main__":
    for point in ((1.0, 0.70, 4), (0.8, 0.55, 4), (1.0, 0.40, 4)):
        P_D, Ae_Ao, Z = point
        c0 = open_water_coefficients(0.0, P_D, Ae_Ao, Z)
        peak = peak_efficiency(P_D, Ae_Ao, Z)
        print(f"B{Z}-{int(round(Ae_Ao * 100))} P/D={P_D}: "
              f"KT0={c0['KT']:.4f} KQ0={c0['KQ']:.5f} "
              f"eta_max={peak['eta_0']:.3f} at J={peak['J']:.2f} "
              f"J(KT=0)={zero_thrust_J(P_D, Ae_Ao, Z):.3f}")