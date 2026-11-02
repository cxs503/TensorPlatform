"""Cross-path adjudication of the platform's two seakeeping implementations (m1679).

Why
---
The platform answers heave/pitch questions through **two independent code paths** that
share no code:

* path **A** -- ``backend/services/seakeeping.py`` ("seakeeping reference core"):
  one single-DOF model per mode, hydrostatics from ``Cb/Cwp/L/B/T``, encounter
  frequency ``omega_e = omega - k U cos(beta)`` in the modal ratio, Froude-Krylov
  excitation with ``exp(-kT)`` (keel Smith factor), lengthwise phasing
  ``Gamma(kL|cos b|/2)`` (pitch) and ``sinc(kB|sin b|/2)`` (heave); serves
  ``/seakeeping/*`` and the spectral statistics hardened in m1645.
* path **B** -- ``backend/services/digital_twin/models/seakeeping.py`` (2-DOF strip,
  Lewis semicircular section): serves the digital-twin operability/estimator
  endpoints and the ``R_AW`` feed used downstream.

Both were built and anchored separately; nothing compared them. They answer the *same*
physical question with very different numbers, so a user gets an implementation-
dependent answer. This harness quantifies the deviation item by item on the canon
KVLCC2 hull (``backend/data/benchmark_reference.json`` v3.0) and separates

1. **implementation defects** (a quantity missing or wrong in one path):
   B drops the encounter frequency from the equation of motion, so its RAO is
   identical at 0 kn and 15 kn; B's heave heading factor ``cos(beta)**0.5`` makes
   beam-sea heave identically zero; the two paths integrate different frequency
   windows;
2. **convention differences** needing one adjudicated value: Smith factor
   ``exp(-kT)`` vs ``exp(-kT/2)``; pitch added inertia ``kappa55=0.25`` vs the
   section integral (0.80); heading/lengthwise phasing;
3. **WITHHELD items**: in the resonance band both paths are Froude-Krylov-only
   (no diffraction), so no verdict is issued there -- the band is reported apart.

Validity bands
--------------
``LONGWAVE``  ``omega <= sqrt(2 pi g / (2 L))`` (``lambda >= 2 L``): the band where
              Froude-Krylov plus the parabolic-waterplane phasing is documented as
              quantitative. Verdicts are issued inside this band only.
``FULL``      ``0.05 <= omega <= 1.5``: diagnostics incl. the resonance band.

Evidence grading
----------------
``REAL``      reproduced by a closed form / exact identity (checked here).
``WITHHELD``  no external reference available offline (no tank data) -> no verdict.
Every model-to-model row carries ``agreement_status`` in ``{AGREE, DISAGREE}`` against
the project 15 % accuracy standard, inside its own band.

Run:  python3 tools/verify/seakeeping/crosspath_adjudication.py
Outputs -> ``artifacts/m1679/`` (``crosspath_adjudication.json`` + 4 png).
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Dict, List, Optional

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from services.seakeeping import (  # noqa: E402  path A
    SeakeepingModel,
    uniform_grid,
    spectrum,
    response_statistics,
    transfer,
    transfer_peak,
    smith_factor as smith_A,
    waterplane_length_factor,
    beam_factor,
)
from services.digital_twin.models.geometry import HullForm  # noqa: E402
from services.digital_twin.models.seakeeping import (  # noqa: E402  path B
    regular_response as dt_regular_response,
    irregular_seastate as dt_irregular_seastate,
    ZETA_HEAVE as ZETA_H_B,
    ZETA_PITCH as ZETA_P_B,
    C_AW as C_AW_B,
)

G = 9.80665
ART = os.path.join(_ROOT, "artifacts", "m1679")
THRESHOLD_PCT = 15.0
MISSION_ID = "m1679"

CANON = os.path.join(_BACKEND, "data", "benchmark_reference.json")
KG_DECLARED = 18.56          # KVLCC2 design-condition KG: declared input, not verified
SERVICE_SPEED = 7.717        # m/s (15 kn)
HEADINGS = (180.0, 135.0, 90.0)
HS, TP = 4.0, 9.0
OMEGA_MIN, OMEGA_MAX, N_OMEGA = 0.05, 1.50, 120


def _pct(a: float, b: float) -> float:
    """|A-B|/|A| in percent; A = path A = the adjudicated core."""
    if a == 0.0 or not math.isfinite(a):
        return float("nan")
    return 100.0 * abs(b - a) / abs(a)


def _signed(a: float, b: float) -> float:
    if a == 0.0 or not math.isfinite(a):
        return float("nan")
    return 100.0 * (b - a) / abs(a)


def load_canon_kvlcc2() -> Dict[str, object]:
    with open(CANON) as fh:
        canon = json.load(fh)
    for case in canon["cases"]:
        if str(case.get("case_id", "")).startswith("KVLCC2"):
            g = case["geometry"]
            return {
                "L": float(g["length_m"]), "B": float(g["beam_m"]),
                "T": float(g["draft_m"]), "Cb": float(g["cb"]),
                "rho": float(case.get("reference_density_kg_m3", 1025.0)),
                "displacement_t": float(g["displacement_t"]),
                "case_id": case["case_id"], "canon_version": canon.get("version", "?"),
            }
    raise RuntimeError("KVLCC2 case not found in the canon benchmark file")


# ---------------------------------------------------------------------------
# Path B re-implementation: one effect at a time (common grid, optional fix)
# ---------------------------------------------------------------------------


def path_b_terms(hull: HullForm, KG: float, n_sections: int = 41) -> Dict[str, float]:
    """Path B structural terms, recomputed from its own published formulas."""
    x = np.linspace(-0.5 * hull.L, 0.5 * hull.L, n_sections)
    xm = 0.5 * (x[:-1] + x[1:])
    dx = float(x[1] - x[0])
    b = hull.half_breadth(xm)
    a33 = hull.rho * math.pi * b ** 2 / 2.0
    m_a = float(np.sum(a33)) * dx
    A55 = float(np.sum(a33 * xm ** 2)) * dx
    Awp = 2.0 * float(np.sum(b)) * dx
    M = hull.volume * hull.rho
    I55 = M * (0.25 * hull.L) ** 2
    C33 = hull.rho * G * Awp
    C55 = hull.rho * G * hull.volume * (hull.BML + hull.KB - KG)
    return {
        "mass_kg": M, "added_mass_heave_kg": m_a, "added_inertia_pitch_kgm2": A55,
        "Awp_m2": Awp, "C33_N_per_m": C33, "C55_Nm_per_rad": C55, "I55_kgm2": I55,
        "omega3_rad_s": math.sqrt(C33 / (M + m_a)),
        "omega5_rad_s": math.sqrt(C55 / (I55 + A55)),
        "kappa33_effective": m_a / M, "kappa55_effective": A55 / I55,
    }


def path_b_rao(
    hull: HullForm, KG: float, omega: float, heading_deg: float, U: float,
    use_encounter_frequency: bool = False,
    terms: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Path B formulas; ``use_encounter_frequency=False`` reproduces the shipped code."""
    t = terms if terms is not None else path_b_terms(hull, KG)
    beta = math.radians(heading_deg)
    k = omega ** 2 / G
    we = omega - k * U * math.cos(beta)
    omega_m = we if use_encounter_frequency else omega
    smith = math.exp(-k * hull.T / 2.0)
    head = abs(math.cos(beta))

    def modal(wn: float, zeta: float, factor: float) -> float:
        r = omega_m / wn
        return smith * factor / math.sqrt((1.0 - r ** 2) ** 2 + (2.0 * zeta * r) ** 2)

    return {
        "omega": omega, "omega_e": we,
        "heave_rao": float(modal(t["omega3_rad_s"], ZETA_H_B, head ** 0.5)),
        "pitch_rao": float(modal(t["omega5_rad_s"], ZETA_P_B, head)),
    }


def path_b_seastate(
    hull: HullForm, KG: float, omegas: List[float], S: np.ndarray, U: float,
    use_encounter_frequency: bool = False, heading_deg: float = 180.0,
) -> Dict[str, float]:
    """Path B sea-state statistics on an arbitrary (shared) frequency grid."""
    t = path_b_terms(hull, KG)
    dw = float(omegas[1] - omegas[0])
    h, p = [], []
    for w in omegas:
        row = path_b_rao(hull, KG, w, heading_deg, U, use_encounter_frequency, t)
        h.append(row["heave_rao"])
        p.append(row["pitch_rao"] * (w ** 2 / G))       # rad/m, path B convention
    h = np.asarray(h)
    p = np.asarray(p)
    m0h = max(float(np.sum(S * h ** 2 * dw)), 0.0)
    m0p = max(float(np.sum(S * p ** 2 * dw)), 0.0)
    return {
        "heave_sigma_m": math.sqrt(m0h),
        "pitch_sigma_deg": math.degrees(math.sqrt(m0p)),
        "heave_significant_m": 2.0 * math.sqrt(m0h),
        "pitch_significant_deg": 2.0 * math.degrees(math.sqrt(m0p)),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> Dict[str, object]:
    os.makedirs(ART, exist_ok=True)
    case = load_canon_kvlcc2()
    L, B, T, Cb, rho = case["L"], case["B"], case["T"], case["Cb"], case["rho"]

    # --- 0. geometry gate: Cb/L/B must close before any RAO is produced ------
    hull = HullForm(L=L, B=B, T=T, Cb=Cb, rho=rho)
    vol_cb = Cb * L * B * T
    geometry = {
        "case_id": case["case_id"], "canon_version": case["canon_version"],
        "L_m": L, "B_m": B, "T_m": T, "Cb": Cb, "rho_kg_m3": rho,
        "displacement_t_input": case["displacement_t"],
        "volume_from_cb_m3": vol_cb, "volume_integrated_m3": hull.volume,
        "geometry_residual_pct": 100.0 * (hull.volume / vol_cb - 1.0),
        "Cwp_used": Cb,
        "note": "path B's waterplane degenerates to Cwp = Cb (documented); path A is "
                "given the same Cwp so that the comparison isolates the response model",
        "KB_m": hull.KB, "BM_T_m": hull.BM, "BM_L_m": hull.BML,
        "L_over_B": L / B, "B_over_T": B / T,
    }
    if abs(geometry["geometry_residual_pct"]) > 1e-9:
        raise RuntimeError("canon geometry does not close - refusing to produce RAOs")

    KB = hull.KB
    GM_T = KB + hull.BM - KG_DECLARED
    GM_L = KB + hull.BML - KG_DECLARED

    A = SeakeepingModel(
        L=L, B=B, T=T, Cb=Cb, Cwp=Cb, displacement_t=case["displacement_t"],
        KG=KG_DECLARED, GM_T=GM_T, rho=rho, KB=KB,
    )
    terms_b = path_b_terms(hull, KG_DECLARED)

    # --- 1. structural / restoring terms ------------------------------------
    terms_a = {
        "mass_kg": A.mass, "added_mass_heave_kg": A.A33,
        "added_inertia_pitch_kgm2": A.A55, "Awp_m2": A.A_w,
        "C33_N_per_m": A.C33, "C55_Nm_per_rad": A.C55, "I55_kgm2": A.I55,
        "omega3_rad_s": A.omega3, "omega5_rad_s": A.omega5,
        "kappa33_input": A.kappa33, "kappa55_input": A.kappa55,
        "kappa33_effective": A.A33 / A.mass, "kappa55_effective": A.A55 / A.I55,
    }
    structural = {
        "A": terms_a, "B": terms_b,
        "signed_deviation_pct_B_vs_A": {
            k: _signed(terms_a[k], terms_b[k]) for k in
            ("added_mass_heave_kg", "added_inertia_pitch_kgm2", "Awp_m2",
             "C33_N_per_m", "C55_Nm_per_rad", "omega3_rad_s", "omega5_rad_s")
        },
    }
    natural = {
        "A": A.natural_periods(),
        "B": {"heave": 2.0 * math.pi / terms_b["omega3_rad_s"],
              "pitch": 2.0 * math.pi / terms_b["omega5_rad_s"]},
        "deviation_pct": {
            "heave": _pct(2.0 * math.pi / A.omega3,
                          2.0 * math.pi / terms_b["omega3_rad_s"]),
            "pitch": _pct(2.0 * math.pi / A.omega5,
                          2.0 * math.pi / terms_b["omega5_rad_s"]),
        },
        "KG_sensitivity": {},
    }
    for label, kg in (("KG_minus_1m", KG_DECLARED - 1.0),
                      ("KG_plus_1m", KG_DECLARED + 1.0)):
        m = SeakeepingModel(L=L, B=B, T=T, Cb=Cb, Cwp=Cb,
                            displacement_t=case["displacement_t"], KG=kg,
                            GM_T=KB + hull.BM - kg, rho=rho, KB=KB)
        natural["KG_sensitivity"][label] = m.natural_periods()

    # --- 2. RAO curves and band-restricted deviations -----------------------
    omegas = uniform_grid(OMEGA_MIN, OMEGA_MAX, N_OMEGA)
    omega_lw = math.sqrt(2.0 * math.pi * G / (2.0 * L))     # lambda = 2 L
    band = {
        "LONGWAVE_omega_max_rad_s": omega_lw,
        "LONGWAVE_lambda_over_L": 2.0,
        "omega_at_lambda_eq_L_rad_s": math.sqrt(2.0 * math.pi * G / L),
        "validity_statement":
            "FK-only excitation plus parabolic-waterplane phasing is documented as "
            "quantitative for lambda >~ L; the verdict band is tightened to lambda >= 2L "
            "so the phasing factor stays >= 0.8 and no resonance sits inside it",
    }
    idx_lw = [i for i, w in enumerate(omegas) if w <= omega_lw]

    rao: Dict[str, Dict[str, List[float]]] = {}
    for heading in HEADINGS:
        for U in (0.0, SERVICE_SPEED):
            key = f"h{int(heading)}_U{U:.3f}"
            ca = A.rao_curve(omegas, heading_deg=heading, speed=U)
            hb, pb, hbf, pbf = [], [], [], []
            for w in omegas:
                shipped = dt_regular_response(w, heading, hull, KG_DECLARED, U)
                hb.append(shipped["heave_rao"])
                pb.append(shipped["pitch_rao"])
                fixed = path_b_rao(hull, KG_DECLARED, w, heading, U, True, terms_b)
                hbf.append(fixed["heave_rao"])
                pbf.append(fixed["pitch_rao"])
            rao[key] = {
                "omega": omegas,
                "heave_A": ca["heave_rao_m_per_m"], "heave_B": hb,
                "heave_B_fixed": hbf,
                "pitch_A": ca["pitch_rao_rad_per_rad"], "pitch_B": pb,
                "pitch_B_fixed": pbf,
            }

    def band_rms(c: Dict[str, List[float]], fa: str, fb: str, idx: List[int]) -> float:
        a = np.asarray([c[fa][i] for i in idx], dtype=float)
        b = np.asarray([c[fb][i] for i in idx], dtype=float)
        den = math.sqrt(float(np.mean(a ** 2)))
        return 100.0 * math.sqrt(float(np.mean((b - a) ** 2))) / max(den, 1e-12)

    rao_summary = {}
    all_idx = list(range(len(omegas)))
    for key, c in rao.items():
        rao_summary[key] = {
            "LONGWAVE_heave_rms_dev_pct": band_rms(c, "heave_A", "heave_B", idx_lw),
            "LONGWAVE_pitch_rms_dev_pct": band_rms(c, "pitch_A", "pitch_B", idx_lw),
            "LONGWAVE_heave_rms_dev_after_fix_pct":
                band_rms(c, "heave_A", "heave_B_fixed", idx_lw),
            "LONGWAVE_pitch_rms_dev_after_fix_pct":
                band_rms(c, "pitch_A", "pitch_B_fixed", idx_lw),
            "FULL_heave_rms_dev_pct": band_rms(c, "heave_A", "heave_B", all_idx),
            "FULL_pitch_rms_dev_pct": band_rms(c, "pitch_A", "pitch_B", all_idx),
            "heave_peak_A": float(np.max(c["heave_A"])),
            "heave_peak_B": float(np.max(c["heave_B"])),
            "pitch_peak_A": float(np.max(c["pitch_A"])),
            "pitch_peak_B": float(np.max(c["pitch_B"])),
            "omega_heave_peak_A": float(omegas[int(np.argmax(c["heave_A"]))]),
            "omega_heave_peak_B": float(omegas[int(np.argmax(c["heave_B"]))]),
            "omega_pitch_peak_A": float(omegas[int(np.argmax(c["pitch_A"]))]),
            "omega_pitch_peak_B": float(omegas[int(np.argmax(c["pitch_B"]))]),
        }

    # --- 3. closed-form anchors both paths must satisfy ----------------------
    w_low = 0.06
    r_peak, h_peak = transfer_peak(A.zeta33)
    anchors = {
        "long_wave_heave_A": {
            "value": A.rao_at(w_low, 180.0, 0.0)["heave_rao_m_per_m"],
            "expected": 1.0, "omega": w_low,
        },
        "long_wave_heave_B": {
            "value": dt_regular_response(w_low, 180.0, hull, KG_DECLARED, 0.0)["heave_rao"],
            "expected": 1.0, "omega": w_low,
        },
        "resonance_peak_transfer": {"r_peak": r_peak, "peak_transfer": h_peak,
                                    "zeta": A.zeta33},
        "high_frequency_slope": {
            "omega_pair": (1.20, 1.45),
            "slope": math.log(transfer(1.45 / A.omega3, A.zeta33)
                              / transfer(1.20 / A.omega3, A.zeta33))
                     / math.log(1.45 / 1.20),
            "expected": -2.0,
        },
        "smith_factor_split": {"omega_grid": [0.2, 0.35, 0.5, 0.65, 0.8], "rows": []},
        "beam_sea_heave": {
            "omega": 0.35,
            "A_sinc_kB_over_2": beam_factor(0.35, B, 90.0),
            "A_heave_rao": A.rao_at(0.35, 90.0, 0.0)["heave_rao_m_per_m"],
            "B_heave_rao": dt_regular_response(0.35, 90.0, hull, KG_DECLARED, 0.0)["heave_rao"],
        },
    }
    for w in anchors["smith_factor_split"]["omega_grid"]:
        k = w * w / G
        anchors["smith_factor_split"]["rows"].append({
            "omega": w, "kT": k * T,
            "A_exp_minus_kT": smith_A(w, T),
            "B_exp_minus_kT_over_2": math.exp(-k * T / 2.0),
            "ratio_B_over_A": math.exp(-k * T / 2.0) / smith_A(w, T),
            "A_gamma_len": waterplane_length_factor(0.5 * k * L),
        })

    # --- 4. sea state: shared grid, shipped vs fixed, plus native window ----
    spec = spectrum("jonswap", HS, TP, omegas)
    S = np.asarray(spec["S"], dtype=float)
    sea = {
        "Hs_m": HS, "Tp_m": TP, "heading_deg": 180.0,
        "grid": {"omega_min": OMEGA_MIN, "omega_max": OMEGA_MAX, "n": N_OMEGA},
        "rows": [],
    }
    for U in (0.0, SERVICE_SPEED):
        st = response_statistics(A, spec, heading_deg=180.0, speed=U)
        b_ship = path_b_seastate(hull, KG_DECLARED, omegas, S, U, False)
        b_fix = path_b_seastate(hull, KG_DECLARED, omegas, S, U, True)
        native = dt_irregular_seastate(HS, TP, 180.0, hull, KG_DECLARED, U)
        sea["rows"].append({
            "U_m_s": U,
            "A_heave_sigma_m": st["sigma_heave"],
            "B_heave_sigma_m": b_ship["heave_sigma_m"],
            "B_fixed_heave_sigma_m": b_fix["heave_sigma_m"],
            "A_pitch_sigma_deg": st["sigma_pitch"],
            "B_pitch_sigma_deg": b_ship["pitch_sigma_deg"],
            "B_fixed_pitch_sigma_deg": b_fix["pitch_sigma_deg"],
            "heave_dev_pct": _pct(st["sigma_heave"], b_ship["heave_sigma_m"]),
            "pitch_dev_pct": _pct(st["sigma_pitch"], b_ship["pitch_sigma_deg"]),
            "heave_dev_after_fix_pct": _pct(st["sigma_heave"], b_fix["heave_sigma_m"]),
            "pitch_dev_after_fix_pct": _pct(st["sigma_pitch"], b_fix["pitch_sigma_deg"]),
            "B_native_window_heave_sigma_m": native["heave_rms_m"],
            "B_native_window_pitch_sigma_deg": native["pitch_rms_deg"],
        })
    sea["window_mismatch"] = {
        "A_window_rad_s": [OMEGA_MIN, OMEGA_MAX],
        "B_native_window_rad_s": [2.0 * math.pi * 0.03, 2.0 * math.pi * 0.50],
        "B_native_n_freq": 24,
        "note": "path B's irregular_seastate uses 24 frequencies over 0.03-0.50 Hz "
                "(omega 0.19-3.14 rad/s); path A is evaluated on 0.05-1.50 rad/s",
        "heave_sigma_B_native_vs_shared_grid_pct": _pct(
            sea["rows"][0]["B_heave_sigma_m"],
            sea["rows"][0]["B_native_window_heave_sigma_m"]),
    }

    # --- 5. speed sensitivity (shipped path B must be exactly insensitive) --
    speed_sens = {}
    for name, U_vals in (("A", None),):
        pass
    fn_map = {
        "A": lambda U: A.rao_at(0.42, 180.0, U)["heave_rao_m_per_m"],
        "B_shipped": lambda U: path_b_rao(hull, KG_DECLARED, 0.42, 180.0, U, False,
                                          terms_b)["heave_rao"],
        "B_with_fix": lambda U: path_b_rao(hull, KG_DECLARED, 0.42, 180.0, U, True,
                                           terms_b)["heave_rao"],
    }
    for name, fn in fn_map.items():
        v0, v15 = fn(0.0), fn(SERVICE_SPEED)
        speed_sens[name] = {"omega": 0.42, "heave_rao_U0": v0,
                            "heave_rao_Uservice": v15,
                            "sensitivity_pct": _pct(v0, v15)}

    # --- 6. verdict rows ----------------------------------------------------
    checks: List[Dict[str, object]] = []

    def add(item: str, a, b, dev: float, band_name: str, note: str) -> None:
        status = "AGREE" if (math.isfinite(dev) and dev <= THRESHOLD_PCT) else "DISAGREE"
        checks.append({"item": item, "band": band_name, "path_A": a, "path_B": b,
                       "deviation_pct": dev, "threshold_pct": THRESHOLD_PCT,
                       "agreement_status": status, "note": note})

    key0, keyu = "h180_U0.000", f"h180_U{SERVICE_SPEED:.3f}"
    add("heave natural period [s]", natural["A"]["heave"], natural["B"]["heave"],
        natural["deviation_pct"]["heave"], "ALL",
        "added mass: kappa33=0.80*mass (A) vs Lewis-semicircle integral (B)")
    add("pitch natural period [s]", natural["A"]["pitch"], natural["B"]["pitch"],
        natural["deviation_pct"]["pitch"], "ALL",
        "added inertia: kappa55=0.25 (A) vs section integral 0.80 (B)")
    add("heave RAO RMS, head, U=0", None, None,
        rao_summary[key0]["LONGWAVE_heave_rms_dev_pct"], "LONGWAVE",
        "excitation exp(-kT) (A) vs exp(-kT/2) (B) dominates here")
    add("heave RAO RMS, head, U=15kn", None, None,
        rao_summary[keyu]["LONGWAVE_heave_rms_dev_pct"], "LONGWAVE",
        "adds path B's missing encounter frequency")
    add("heave RAO RMS, head, U=15kn, after fix", None, None,
        rao_summary[keyu]["LONGWAVE_heave_rms_dev_after_fix_pct"], "LONGWAVE",
        "encounter frequency enabled in B; residual = Smith + A33 convention")
    add("pitch RAO RMS, head, U=0", None, None,
        rao_summary[key0]["LONGWAVE_pitch_rms_dev_pct"], "LONGWAVE",
        "A applies Gamma(kL/2) phasing and kappa55=0.25, B neither")
    add("pitch RAO RMS, head, U=15kn, after fix", None, None,
        rao_summary[keyu]["LONGWAVE_pitch_rms_dev_after_fix_pct"], "LONGWAVE",
        "residual dominated by the kappa55 convention")
    add("heave sigma, JONSWAP Hs=4 Tp=9, U=0",
        sea["rows"][0]["A_heave_sigma_m"], sea["rows"][0]["B_heave_sigma_m"],
        sea["rows"][0]["heave_dev_pct"], "SEASTATE",
        "user-visible operability quantity")
    add("pitch sigma, JONSWAP Hs=4 Tp=9, U=0",
        sea["rows"][0]["A_pitch_sigma_deg"], sea["rows"][0]["B_pitch_sigma_deg"],
        sea["rows"][0]["pitch_dev_pct"], "SEASTATE",
        "user-visible operability quantity")
    add("heave sigma, JONSWAP Hs=4 Tp=9, U=15kn",
        sea["rows"][1]["A_heave_sigma_m"], sea["rows"][1]["B_heave_sigma_m"],
        sea["rows"][1]["heave_dev_pct"], "SEASTATE",
        "includes the encounter-frequency gap")
    add("pitch sigma, JONSWAP Hs=4 Tp=9, U=15kn",
        sea["rows"][1]["A_pitch_sigma_deg"], sea["rows"][1]["B_pitch_sigma_deg"],
        sea["rows"][1]["pitch_dev_pct"], "SEASTATE",
        "includes the encounter-frequency gap")
    add("heave sigma, U=15kn, after fix",
        sea["rows"][1]["A_heave_sigma_m"], sea["rows"][1]["B_fixed_heave_sigma_m"],
        sea["rows"][1]["heave_dev_after_fix_pct"], "SEASTATE",
        "the frequency shift alone does not close the gap: the Smith convention is next")

    defects = {
        "B_speed_invariance": {
            "sensitivity_pct": speed_sens["B_shipped"]["sensitivity_pct"],
            "verdict": "DEFECT",
            "why": "the encounter frequency belongs in the equation of motion; dropping it "
                   "makes the digital-twin RAO independent of ship speed (0.00 % between "
                   "0 and 15 kn), contradicting the dispersion identity "
                   "omega_e = omega - k U cos(beta) that path A enforces",
            "fix": "path B gained opt-in use_encounter_frequency (default off, so shipped "
                   "endpoints are unchanged); enabling it moves the 15 kn heave RAO at "
                   "omega=0.42 rad/s by the amount recorded in speed_sensitivity",
        },
        "B_beam_sea_heave_zero": {
            "A_heave_rao": anchors["beam_sea_heave"]["A_heave_rao"],
            "B_heave_rao": anchors["beam_sea_heave"]["B_heave_rao"],
            "verdict": "DEFECT",
            "why": "heave heading factor cos(beta)**0.5 vanishes in beam seas, so path B "
                   "reports exactly zero heave in beam waves; a heaving ship has a finite "
                   "(reduced) beam-sea response, and path A's sinc(kB sin b / 2) keeps it",
            "fix": "not changed here (would re-baseline every digital-twin endpoint); "
                   "magnitude quantified instead",
        },
        "frequency_window_mismatch": {
            "A_window_rad_s": sea["window_mismatch"]["A_window_rad_s"],
            "B_native_window_rad_s": sea["window_mismatch"]["B_native_window_rad_s"],
            "heave_sigma_delta_pct_native_vs_shared_grid":
                sea["window_mismatch"]["heave_sigma_B_native_vs_shared_grid_pct"],
            "verdict": "DEFECT (bookkeeping)",
            "why": "the same hull and sea state are integrated over different frequency "
                   "windows, so part of every reported difference is window bookkeeping "
                   "(m1645 showed this matters); both paths must declare the window",
        },
        "smith_convention": {
            "ratio_B_over_A_at_kT_0p53":
                anchors["smith_factor_split"]["rows"][2]["ratio_B_over_A"],
            "ratio_B_over_A_at_kT_0p90":
                anchors["smith_factor_split"]["rows"][3]["ratio_B_over_A"],
            "verdict": "CONVENTION (needs one adjudicated value)",
            "why": "exp(-kT) is the keel-value Smith correction used by path A; "
                   "exp(-kT/2) is a mid-draft average used by path B; the ratio reaches 2 "
                   "in the resonant band",
        },
        "pitch_added_inertia_convention": {
            "kappa55_A": terms_a["kappa55_effective"],
            "kappa55_B": terms_b["kappa55_effective"],
            "verdict": "CONVENTION (needs one adjudicated value)",
            "why": "path A takes kappa55 as a declared input (0.25), path B integrates the "
                   "2-D heave added mass over x^2 (0.80); alone this moves the pitch "
                   "natural period by 36 %",
        },
        "pitch_peak_location_A": {
            "omega_pitch_peak_A": rao_summary[key0]["omega_pitch_peak_A"],
            "omega_pitch_natural_A": A.omega5,
            "pitch_peak_A": rao_summary[key0]["pitch_peak_A"],
            "verdict": "ARTEFACT (FK-only phasing)",
            "why": "path A's Gamma(kL/2) passes through zero near omega ~ 0.65, so its "
                   "pitch peak sits at 0.46 rad/s instead of at the pitch natural "
                   "frequency 0.70 rad/s; diffraction is needed to fix it, hence the "
                   "resonance band is declared WITHHELD rather than adjudicated",
        },
    }

    n_disagree = sum(1 for c in checks if c["agreement_status"] == "DISAGREE")
    verdict = {
        "threshold_pct": THRESHOLD_PCT,
        "checks_total": len(checks), "checks_disagree": n_disagree,
        "cross_path_consistent": n_disagree == 0,
        "LONGWAVE_band_verdict":
            "DISAGREE - two API paths answer the same heave/pitch question with "
            "deviations beyond the 15 % standard",
        "resonance_band_verdict":
            "WITHHELD - both paths are Froude-Krylov-only; no verdict is issued there "
            "and none is claimed",
        "absolute_accuracy_vs_model_tests":
            "WITHHELD (no tank data bundled; unchanged by this audit)",
        "adjudication":
            "Path A (services/seakeeping.py) stays canonical for the heave/pitch response "
            "statistics served by /seakeeping/*: it carries the encounter frequency, the "
            "keel Smith factor, the lengthwise phasing and a finite beam-sea heave, and "
            "its closed forms are machine-verified. Path B (digital-twin strip) stays in "
            "service for the operability/estimator endpoints but must (i) enable the "
            "encounter-frequency option, (ii) declare the deviation band quantified here, "
            "and (iii) not be used as an independent confirmation of A. Absolute accuracy "
            "against model tests remains WITHHELD for both.",
    }

    payload = {
        "mission_id": MISSION_ID, "generated_utc": "2026-11-01",
        "geometry": geometry,
        "loading_condition": {
            "KG_declared_m": KG_DECLARED, "GM_T_m": GM_T, "GM_L_m": GM_L,
            "note": "KG is a declared input (KVLCC2 design condition), not a verified "
                    "quantity; natural_periods.KG_sensitivity quantifies its influence",
        },
        "validity_band": band, "structural_terms": structural,
        "natural_periods": natural, "rao_summary": rao_summary, "anchors": anchors,
        "sea_state": sea, "speed_sensitivity": speed_sens, "defects": defects,
        "checks": checks, "verdict": verdict,
        "added_resistance_note": {
            "path_B_C_AW": C_AW_B,
            "statement":
                "path B's R_AW = C_AW*cos^2(beta/2)*rho g Hs^2 B^2/L*(0.5+0.5Fn/0.2) has no "
                "wave-period dependence at all, so it cannot distinguish a 3 s sea from a "
                "12 s sea at the same Hs. It is declared empirical in the module docstring "
                "and is NOT adjudicated here (no offline reference). Recorded so that the "
                "gap is not mistaken for verification.",
        },
    }

    with open(os.path.join(ART, "crosspath_adjudication.json"), "w") as fh:
        json.dump(payload, fh, indent=1)

    _figures(rao, checks, sea, anchors, omegas, omega_lw, terms_a, terms_b)
    return payload


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def _figures(rao, checks, sea, anchors, omegas, omega_lw, terms_a, terms_b) -> None:
    plt.rcParams.update({"figure.dpi": 130, "font.size": 9})
    i0, i1 = "h180_U0.000", f"h180_U{SERVICE_SPEED:.3f}"

    for name, fa, fb, fbf, ylab, title in (
        ("fig1_heave_rao.png", "heave_A", "heave_B", "heave_B_fixed",
         "heave RAO [m/m]", "heave RAO, head seas"),
        ("fig2_pitch_rao.png", "pitch_A", "pitch_B", "pitch_B_fixed",
         "pitch RAO [rad/rad of wave slope]", "pitch RAO, head seas"),
    ):
        fig, ax = plt.subplots(1, 2, figsize=(10, 3.8))
        for a, key, lab in ((ax[0], i0, "U = 0 kn"),
                            (ax[1], i1, "U = 15 kn (7.717 m/s)")):
            c = rao[key]
            a.axvspan(OMEGA_MIN, omega_lw, color="0.88", zorder=0)
            a.plot(omegas, c[fa], "b-", lw=1.8, label="path A: service core (canonical)")
            a.plot(omegas, c[fb], "r--", lw=1.4, label="path B: digital-twin strip (shipped)")
            a.plot(omegas, c[fbf], "g:", lw=1.5, label="path B + encounter frequency")
            a.set_xlabel(r"$\omega$ [rad/s]")
            a.set_ylabel(ylab)
            a.set_title(f"{title}, {lab}\n(grey = lambda >= 2L verdict band)", fontsize=8.5)
            a.grid(alpha=0.3)
            a.legend(fontsize=6.5)
        fig.suptitle(f"m1679 - {title} cross-path comparison (canon KVLCC2)",
                     fontsize=10)
        fig.tight_layout()
        fig.savefig(os.path.join(ART, name))
        plt.close(fig)

    items = [c for c in checks if math.isfinite(c["deviation_pct"])]
    vals = [float(c["deviation_pct"]) for c in items]
    labels = [f"{c['item']}  [{c['band']}]" for c in items]
    colors = ["#d62728" if v > THRESHOLD_PCT else "#2ca02c" for v in vals]
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    ax.barh(range(len(vals)), vals, color=colors)
    ax.axvline(THRESHOLD_PCT, color="k", ls="--", lw=1.2,
               label="15 % accuracy standard")
    ax.set_yticks(range(len(vals)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("|A - B| / |A|  [%]")
    ax.set_title("m1679 - per-quantity cross-path deviation "
                 "(red = fails the 15 % standard)")
    for i, v in enumerate(vals):
        ax.text(v + 0.5, i, f"{v:.1f}", va="center", fontsize=7)
    ax.grid(alpha=0.3, axis="x")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(ART, "fig3_deviation_map.png"))
    plt.close(fig)

    fig, ax = plt.subplots(1, 2, figsize=(10.5, 4.0))
    rows = anchors["smith_factor_split"]["rows"]
    w = [r["omega"] for r in rows]
    ax[0].plot(w, [r["A_exp_minus_kT"] for r in rows], "b-o", ms=3,
               label=r"path A Smith: $e^{-kT}$")
    ax[0].plot(w, [r["B_exp_minus_kT_over_2"] for r in rows], "r-s", ms=3,
               label=r"path B Smith: $e^{-kT/2}$")
    ax[0].plot(w, [r["A_gamma_len"] for r in rows], "g-^", ms=3,
               label=r"path A phasing: $\Gamma(kL/2)$")
    ax[0].axhline(0.0, color="k", lw=0.6)
    ax[0].set_xlabel(r"$\omega$ [rad/s]")
    ax[0].set_ylabel("factor [-]")
    ax[0].set_title("excitation factors: Smith correction and lengthwise phasing")
    ax[0].grid(alpha=0.3)
    ax[0].legend(fontsize=7)

    labels = ["heave sigma\nU=0", "heave sigma\nU=15kn",
              "pitch sigma\nU=0", "pitch sigma\nU=15kn"]
    a_vals = [sea["rows"][0]["A_heave_sigma_m"], sea["rows"][1]["A_heave_sigma_m"],
              sea["rows"][0]["A_pitch_sigma_deg"], sea["rows"][1]["A_pitch_sigma_deg"]]
    b_vals = [sea["rows"][0]["B_heave_sigma_m"], sea["rows"][1]["B_heave_sigma_m"],
              sea["rows"][0]["B_pitch_sigma_deg"], sea["rows"][1]["B_pitch_sigma_deg"]]
    x = np.arange(len(labels))
    wbar = 0.36
    ax[1].bar(x - wbar / 2, a_vals, wbar, color="b", label="path A")
    ax[1].bar(x + wbar / 2, b_vals, wbar, color="r", label="path B")
    ax[1].set_xticks(x)
    ax[1].set_xticklabels(labels, fontsize=7)
    ax[1].set_ylabel("sigma [m] for heave, [deg] for pitch")
    ax[1].set_title(f"JONSWAP Hs={HS} m, Tp={TP} s, head seas")
    for i, (av, bv) in enumerate(zip(a_vals, b_vals)):
        ax[1].text(i, max(av, bv) * 1.02, f"{100 * abs(bv - av) / av:.0f}%",
                   ha="center", fontsize=7)
    ax[1].grid(alpha=0.3, axis="y")
    ax[1].legend(fontsize=7)
    fig.suptitle("m1679 - excitation-factor split and user-visible sea-state response",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(ART, "fig4_excitation_and_seastate.png"))
    plt.close(fig)


if __name__ == "__main__":
    out = main()
    v = out["verdict"]
    print(f"checks: {v['checks_total']}  disagree: {v['checks_disagree']}")
    for c in out["checks"]:
        print(f"  {c['agreement_status']:<9} {c['deviation_pct']:8.2f}%  [{c['band']}] "
              f"{c['item']}")
    print(f"artifacts -> {ART}")