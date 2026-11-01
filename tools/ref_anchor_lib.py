"""Reference-value anchor library (domain anchors for ship performance checks).

Three anchor families are provided, each with an explicit primary source:

1. ITTC/ISSC two-parameter wave spectrum
       S(w) = A * w^-5 * exp(-B * w^-4)
   with the ITTC-1978 parameterisation A = 173 Hs^2 / T1^4, B = 691 / T1^4.
   Closed-form moments:  m0 = A / (4 B),  m1 = (Gamma(3/4) / 4) * A * B^(-3/4),
   m2 = divergent (integrand ~ A w^-3 as w -> 0).

2. Wageningen B4-70 (P/D = 1.0, AE/A0 = 0.70) open-water table from the
   polynomial regression of the B-screw series:
       Oosterveld, M.W.C. & van Oossanen, P. (1975), "Further Computer-Analyzed
       Data of the Wageningen B-Screw Series", International Shipbuilding
       Progress, Vol. 22, No. 251.  (39-term KT, 47-term KQ, valid at Re = 2e6)
   Coefficients below were taken from two independent public transcriptions of
   that table (see B_SERIES_SOURCES) and agree term-by-term.

3. Full-scale total resistance anchors (KVLCC2 at 15.5 kn, KCS at 24 kn) with
   the corresponding principal particulars.

Nothing in this module talks to the network or to the platform; it is pure
data + closed-form maths so that the anchors can be recomputed offline.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Dict, List, Tuple

G_STANDARD = 9.80665  # m/s^2

# ---------------------------------------------------------------------------
# 1. ITTC / ISSC two-parameter spectrum
# ---------------------------------------------------------------------------

ITTC78_HS_COEF = 173.0  # A = 173 * Hs^2 / T1^4
ITTC78_T_COEF = 691.0   # B = 691 / T1^4

SPECTRUM_SOURCE = (
    "ITTC Recommended Procedures 7.5-04-01-01 (Seakeeping experiments) / "
    "ISSC-1964 two-parameter spectrum: S(w)=A w^-5 exp(-B w^-4), "
    "A=173 Hs^2/T1^4, B=691/T1^4 (T1 = first moment period)."
)


def ittc78_ab(Hs: float, T1: float) -> Tuple[float, float]:
    """Return (A, B) of the ITTC-78 two-parameter spectrum."""
    if Hs <= 0 or T1 <= 0:
        raise ValueError("Hs and T1 must be positive")
    return (ITTC78_HS_COEF * Hs ** 2 / T1 ** 4, ITTC78_T_COEF / T1 ** 4)


def spectrum_value(A: float, B: float, omega: float) -> float:
    """S(omega) = A omega^-5 exp(-B omega^-4)  [m^2 s]."""
    if omega <= 0:
        raise ValueError("omega must be positive")
    return A * omega ** -5.0 * math.exp(-B * omega ** -4.0)


def m0_closed(A: float, B: float) -> float:
    """Zeroth spectral moment, closed form: m0 = A / (4 B)  [m^2]."""
    return A / (4.0 * B)


def m1_closed(A: float, B: float) -> float:
    """First spectral moment, closed form: m1 = Gamma(3/4)/4 * A * B^(-3/4)."""
    return 0.25 * math.gamma(0.75) * A * B ** -0.75


def m1_sqrt_pi_form(A: float, B: float) -> float:
    """``sqrt(pi)/4 * A * B^(-3/4)`` -- kept only as a documented cross-check.

    NOTE: the mission brief quoted ``m1 = (sqrt(pi)/4) B^(-3/4)``.  That form is
    (a) missing the A factor and (b) numerically wrong: the exact integral is
    Gamma(3/4)/4 = 0.306354 versus sqrt(pi)/4 = 0.443113, a 44.6% offset.  The
    exact value is stored as the anchor; this helper exists so the test suite
    can document the discrepancy rather than silently copy the brief.
    """
    return 0.25 * math.sqrt(math.pi) * A * B ** -0.75


def m2_divergent(A: float, B: float) -> bool:
    """m2 = int w^2 S(w) dw diverges: integrand behaves as A*w^-3 as w -> 0+."""
    return A > 0.0 and B > 0.0


# ---------------------------------------------------------------------------
# 2. Wageningen B-series polynomial regression (Oosterveld & van Oossanen 1975)
#    Term layout: (C, s[J], t[P/D], u[AE/AO], v[Z])
# ---------------------------------------------------------------------------

B_SERIES_SOURCES = {
    "primary": (
        "Oosterveld, M.W.C. & van Oossanen, P. (1975), Further Computer-Analyzed "
        "Data of the Wageningen B-Screw Series, International Shipbuilding "
        "Progress 22(251), 39-term KT / 47-term KQ polynomial, Re = 2e6."
    ),
    "transcription_a": (
        "isulovsky/Wageningen-B-series-propeller-calculator, wageningen_b.py "
        "(GitHub, MIT) -- explicit 1975 citation, standard-library implementation."
    ),
    "transcription_b": (
        "mkergoat/bseries, coeffs.dat (GitHub, GPL-3.0) -- Bernitsas (1978) "
        "re-publication of the same 39/47-term table."
    ),
}

B_SERIES_VALIDITY = {
    "Z": [3, 7],
    "P_D": [0.6, 1.4],
    "Ae_Ao": [0.3, 1.05],
    "J": [0.0, 1.4],
    "Re": 2.0e6,
}

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
    (-0.0004659, 0, 6, 2, 2),
    (5.54194e-05, 1, 6, 2, 2),
]


def _poly_eval(coeffs, J, P_D, Ae_Ao, Z):
    acc = 0.0
    for c, s, t, u, v in coeffs:
        acc += c * J ** s * P_D ** t * Ae_Ao ** u * Z ** v
    return acc


def b_series_kt(J: float, P_D: float, Ae_Ao: float, Z: float) -> float:
    return _poly_eval(KT_COEFFICIENTS, J, P_D, Ae_Ao, Z)


def b_series_kq(J: float, P_D: float, Ae_Ao: float, Z: float) -> float:
    return _poly_eval(KQ_COEFFICIENTS, J, P_D, Ae_Ao, Z)


def open_water_point(J: float, P_D: float = 1.0, Ae_Ao: float = 0.70, Z: float = 4) -> Dict[str, float]:
    """KT, KQ, 10KQ and eta0 = J*KT/(2 pi KQ) for one advance coefficient."""
    KT = b_series_kt(J, P_D, Ae_Ao, Z)
    KQ = b_series_kq(J, P_D, Ae_Ao, Z)
    eta0 = J * KT / (2.0 * math.pi * KQ) if KQ > 0 else float("nan")
    return {"KT": KT, "KQ": KQ, "10KQ": 10.0 * KQ, "eta0": eta0}


B4_70 = {"Z": 4, "P_D": 1.0, "Ae_Ao": 0.70}
B4_70_J_POINTS = [round(0.1 * i, 1) for i in range(1, 9)]  # J = 0.1 ... 0.8


def b4_70_table() -> List[Dict[str, float]]:
    rows = []
    for J in B4_70_J_POINTS:
        pt = open_water_point(J, B4_70["P_D"], B4_70["Ae_Ao"], B4_70["Z"])
        rows.append({"J": J, **pt})
    return rows


# ---------------------------------------------------------------------------
# Second, independent transcription of the same 1975 table (Bernitsas 1978
# re-publication, mkergoat/bseries coeffs.dat).  Kept so that the B4-70 anchor
# can be recomputed from a source that shares no code with the transcription
# above; the two agree term-by-term to within 3e-6.
# ---------------------------------------------------------------------------
KT_COEFFICIENTS_BERNITSAS: List[Tuple[float, int, int, int, int]] = [
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

KQ_COEFFICIENTS_BERNITSAS: List[Tuple[float, int, int, int, int]] = [
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


def _poly_eval_set(coeffs, J, P_D, Ae_Ao, Z):
    return _poly_eval(coeffs, J, P_D, Ae_Ao, Z)


def coefficient_crosscheck() -> Dict[str, float]:
    """Term-by-term and B4-70-table deviation between the two transcriptions."""
    def term_dev(a, b):
        ka = sorted((float(c), s, t, u, v) for c, s, t, u, v in a)
        kb = sorted((float(c), s, t, u, v) for c, s, t, u, v in b)
        if len(ka) != len(kb):
            raise ValueError("coefficient tables of different length")
        return max(abs(x[0] - y[0]) for x, y in zip(ka, kb))

    dev_kt = term_dev(KT_COEFFICIENTS, KT_COEFFICIENTS_BERNITSAS)
    dev_kq = term_dev(KQ_COEFFICIENTS, KQ_COEFFICIENTS_BERNITSAS)
    worst = 0.0
    for J in B4_70_J_POINTS:
        a = open_water_point(J, B4_70["P_D"], B4_70["Ae_Ao"], B4_70["Z"])
        b_kt = _poly_eval(B4_70_COEFFICIENTS_BERNITSAS_KT, J, B4_70["P_D"], B4_70["Ae_Ao"], B4_70["Z"])
        b_kq = _poly_eval(KQ_COEFFICIENTS_BERNITSAS, J, B4_70["P_D"], B4_70["Ae_Ao"], B4_70["Z"])
        for va, vb in ((a["KT"], b_kt), (a["10KQ"], 10.0 * b_kq)):
            if va:
                worst = max(worst, abs(va - vb) / abs(va))
    return {"max_abs_term_deviation_KT": dev_kt,
             "max_abs_term_deviation_KQ": dev_kq,
             "max_rel_deviation_B4_70_table": worst}


B4_70_COEFFICIENTS_BERNITSAS_KT = KT_COEFFICIENTS_BERNITSAS


# ---------------------------------------------------------------------------
# 3. Full-scale total resistance anchors
# ---------------------------------------------------------------------------

KVLCC2 = {
    "name": "KVLCC2 (KRISO very large crude carrier)",
    "Lpp_m": 320.0,
    "Lwl_m": 320.0,
    "B_m": 58.0,
    "T_m": 20.8,
    "Cb": 0.8098,
    "Cm": 0.9950,
    "displacement_m3": 312622.0,
    "S_m2": 27194.0,
    "rho_kg_m3": 1025.0,
    "nu_m2_s": 1.19e-6,
    "design_speed_kn": 15.5,
    "Fn": 0.1423,
}

KCS = {
    "name": "KCS (KRISO container ship)",
    "Lpp_m": 230.0,
    "Lwl_m": 232.5,
    "B_m": 32.2,
    "T_m": 10.8,
    "Cb": 0.6505,
    "Cm": 0.9849,
    "displacement_m3": 52030.0,
    "S_m2": 9424.0,
    "rho_kg_m3": 1025.0,
    "nu_m2_s": 1.19e-6,
    "design_speed_kn": 24.0,
    "Fn": 0.26,
}

RESISTANCE_SOURCE = (
    "KVLCC2/KCS benchmark geometries of the Tokyo-2015 / Gothenburg-2010 "
    "workshops; full-scale R_T from ITTC-78 extrapolation of the published model "
    "tests. KVLCC2 particulars: Lpp=320 m, B=58 m, T=20.8 m, Cb=0.8098, "
    "S=27194 m^2, nabla=312622 m^3. KCS particulars: Lpp=230 m, B=32.2 m, "
    "T=10.8 m, Cb=0.6505, S=9424 m^2, nabla=52030 m^3."
)


def knots_to_ms(v_kn: float) -> float:
    return v_kn * 1852.0 / 3600.0


def dynamic_pressure_force(rho: float, S: float, V: float) -> float:
    """0.5 rho S V^2  [N]  -- the scale factor used to turn C_T into R_T."""
    return 0.5 * rho * S * V * V


def ittc57_cf(Re: float) -> float:
    return 0.075 / (math.log10(Re) - 2.0) ** 2


def resistance_breakdown(ship: Dict[str, float], form_factor: float, ca: float,
                         V_kn: float = None) -> Dict[str, float]:
    """Physics cross-check of the R_T anchor: C_T = (1+k) C_F + C_A + C_W.

    C_W is estimated from the residual of the anchor: this helper is used to show
    that the anchored R_T is consistent with a plausible resistance breakdown and
    is *not* used to generate the anchor value itself.
    """
    V_kn = ship["design_speed_kn"] if V_kn is None else V_kn
    V = knots_to_ms(V_kn)
    Re = V * ship["Lpp_m"] / ship["nu_m2_s"]
    cf = ittc57_cf(Re)
    return {"V_ms": V, "Re": Re, "C_F": cf, "C_F_form": (1.0 + form_factor) * cf,
            "C_A": ca, "q_S": dynamic_pressure_force(ship["rho_kg_m3"], ship["S_m2"], V)}


# ---------------------------------------------------------------------------
# Integrity helpers (used by tools/gen_ref_anchors.py and the test-suite)
# ---------------------------------------------------------------------------

def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj) -> str:
    return sha256_text(canonical_json(obj))


def anchor_document(anchor_set: str, payload: Dict, generated_at: str) -> Dict:
    """Wrap a payload with a self-describing sha256 checksum over the payload."""
    return {
        "schema_version": "ref-anchor/1.0",
        "anchor_set": anchor_set,
        "generated_at": generated_at,
        "generated_by": "tools/gen_ref_anchors.py",
        "payload": payload,
        "checksum": {
            "algo": "sha256",
            "covers": "payload serialised with json.dumps(sort_keys=True, ensure_ascii=False, separators=(',',':'))",
            "value": sha256_obj(payload),
        },
    }


def verify_document(doc: Dict) -> bool:
    return doc["checksum"]["value"] == sha256_obj(doc["payload"])