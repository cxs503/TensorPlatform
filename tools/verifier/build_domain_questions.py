#!/usr/bin/env python3
"""
Build held-out questions for four ship domains into the verifier question bank.

Design (verification-debt closure):
  * Every expected value is computed HERE by an independent closed-form
    implementation (deliberately different algebraic arrangement from the
    pilot in pilots/domain_pilots.py where feasible) — zero hand-copied
    numbers.
  * The builder asserts pilot-vs-builder agreement to 1e-12 relative
    (cross-implementation cross-check) before freezing.
  * Questions are frozen (committed=True + sha256) so the bank is immutable.
  * Idempotent: re-running skips questions whose (domain, title) already
    exist.

Domains / anchors (all exact, no empirical fits):
  stability  : KM = T/2 + B²/(12T);  GZ = (GM + B/2·tanφ)·sin²φ;
               tanφ_v = −2GM/B;  T4 = 2π√(I44/(ΔgGM))
  propulsion : η_ideal = 2/(1+√(1+C_T));  ΔV/V₀ = √(1+C_T)−1;  P = TV₀/η
  green      : m_CO₂ = m_fuel·w_C·44.01/12.011 (MARPOL Annex VI Reg.2 w_C)
  seakeeping : ω_e = ω(1 − Vωcosμ/g);  T33 = 2π√((m+A33)/(ρ∇g))

Usage:
  python3 tools/verifier/build_domain_questions.py [--check]
"""
from __future__ import annotations

import argparse
import math
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from question_bank import Question, QuestionBank  # noqa: E402
from pilots.domain_pilots import candidate as pilot_candidate  # noqa: E402

G = 9.81
MOLAR_C = 12.011
MOLAR_CO2 = 44.01
W_C_MGO = 0.863
W_C_LNG = 0.812


# ── independent reference implementations (second implementation) ────────

def ref_barge_km(B: float, T: float) -> float:
    # BM = I_wp/∇ with I_wp = L·B³/12, ∇ = L·B·T  →  BM = B²/(12T); KB = T/2
    L = 100.0  # arbitrary, cancels
    i_wp = L * B ** 3 / 12.0
    nabla = L * B * T
    return T / 2.0 + i_wp / nabla


def ref_wall_sided_gz(GM: float, B: float, phi_deg: float) -> float:
    # expanded form of (GM + B/2·tanφ)·sin²φ:
    #   GZ = GM·sin²φ + (B/2)·sin²φ·tanφ
    phi = math.radians(phi_deg)
    return GM * math.sin(phi) ** 2 + (B / 2.0) * math.sin(phi) ** 2 * math.tan(phi)


def ref_vanishing_angle(GM: float, B: float) -> float:
    # root of GM + (B/2)·tanφ = 0
    return math.atan(2.0 * abs(GM) / B)


def ref_roll_period(I44: float, delta_kg: float, GM: float) -> float:
    # ω4 = √(Δ·g·GM/I44)
    return 2.0 * math.pi / math.sqrt(delta_kg * G * GM / I44)


def ref_disk_eta(C_T: float) -> float:
    # solve quadratic a² + a − C_T = 0 (positive root), η = 1/(1+a)
    a = (-1.0 + math.sqrt(1.0 + 4.0 * C_T)) / 2.0
    return 1.0 / (1.0 + a)


def ref_disk_induced(C_T: float) -> float:
    # positive root of a² + a − C_T = 0
    return (-1.0 + math.sqrt(1.0 + 4.0 * C_T)) / 2.0


def ref_co2(m_fuel_kg: float, w_C: float) -> float:
    # carbon mass × molar ratio, computed via moles
    n_c_mol = m_fuel_kg * 1000.0 * w_C / MOLAR_C
    return n_c_mol * MOLAR_CO2 / 1000.0


def ref_encounter(omega: float, V: float, mu_deg: float) -> float:
    # ω_e = ω − V·cosμ·ω²/g  (expanded form of ω(1 − Fr_w cosμ))
    mu = math.radians(mu_deg)
    return omega - V * math.cos(mu) * omega ** 2 / G


def ref_heave_period(m_kg: float, a33_ratio: float, rho: float, nabla_m3: float) -> float:
    # ω33 = √(ρ∇g/(m+A33));  T33 = 2π/ω33
    w33 = math.sqrt(rho * nabla_m3 * G / (m_kg * (1.0 + a33_ratio)))
    return 2.0 * math.pi / w33


# ── question definitions ───────────────────────────────────────────────────

def _q(domain, difficulty, title, spec, expected, tol, source, ref, tags):
    return Question(
        id=str(uuid.uuid4()),
        domain=domain,
        difficulty=difficulty,
        title=title,
        specification=spec,
        expected_result=expected,
        tolerances=tol,
        provenance={"source": source, "ref": ref},
        tags=tags,
        version=1,
    )


def build_questions():
    """Return list[(Question, spec, expected, ref_fn)] — ref_fn recomputes expected."""
    qs = []

    # stability
    qs.append((_q(
        "stability", "basic",
        "Rectangular barge KM (B=20 m, T=4 m)",
        {"formula": "barge_km", "B": 20.0, "T": 4.0},
        {"KM": ref_barge_km(20.0, 4.0)},
        {"KM": 1e-9},
        "closed-form hydrostatics", "I_wp=L·B³/12, ∇=L·B·T ⇒ KM=T/2+B²/(12T)",
        ["stability", "hydrostatics", "closed-form"]),
    ))
    qs.append((_q(
        "stability", "intermediate",
        "Wall-sided GZ at 15 deg (GM=1.0 m, B=20 m)",
        {"formula": "wall_sided_gz", "GM": 1.0, "B": 20.0, "phi_deg": 15.0},
        {"GZ": ref_wall_sided_gz(1.0, 20.0, 15.0)},
        {"GZ": 1e-9},
        "closed-form wall-sided formula", "GZ=(GM+B/2·tanφ)·sin²φ, exact for vertical sides",
        ["stability", "wall-sided", "closed-form"]),
    ))
    qs.append((_q(
        "stability", "intermediate",
        "Wall-sided vanishing angle (GM=-1.0 m, B=20 m)",
        {"formula": "vanishing_angle", "GM": -1.0, "B": 20.0},
        {"phi_v_rad": ref_vanishing_angle(-1.0, 20.0)},
        {"phi_v_rad": 1e-9},
        "closed-form wall-sided formula", "tanφ_v=−2GM/B (GM<0)",
        ["stability", "wall-sided", "vanishing-angle"]),
    ))
    qs.append((_q(
        "stability", "intermediate",
        "Small-amplitude roll period (I44=2e6, Δ=5e7 kg, GM=1.5 m)",
        {"formula": "roll_period", "I44": 2.0e6, "delta_kg": 5.0e7, "GM": 1.5},
        {"T4": ref_roll_period(2.0e6, 5.0e7, 1.5)},
        {"T4": 1e-9},
        "closed-form pendulum", "T4=2π√(I44/(Δ·g·GM)), g=9.81",
        ["stability", "roll", "natural-period"]),
    ))

    # propulsion
    qs.append((_q(
        "propulsion", "basic",
        "Actuator-disk ideal efficiency (C_T=0.2)",
        {"formula": "disk_eta_ideal", "C_T": 0.2},
        {"eta_ideal": ref_disk_eta(0.2)},
        {"eta_ideal": 1e-9},
        "momentum theory (actuator disk)", "η=2/(1+√(1+C_T)), loss-free disk",
        ["propulsion", "momentum-theory", "closed-form"]),
    ))
    qs.append((_q(
        "propulsion", "basic",
        "Actuator-disk induced velocity ratio (C_T=0.2)",
        {"formula": "disk_induced", "C_T": 0.2},
        {"dv_ratio": ref_disk_induced(0.2)},
        {"dv_ratio": 1e-9},
        "momentum theory (actuator disk)", "ΔV/V₀=√(1+C_T)−1",
        ["propulsion", "momentum-theory", "closed-form"]),
    ))
    qs.append((_q(
        "propulsion", "intermediate",
        "Ideal shaft power (T=1000 N, V0=10 m/s, C_T=0.2)",
        {"formula": "disk_ideal_power", "T_N": 1000.0, "V0": 10.0, "C_T": 0.2},
        {"P_ideal_W": 1000.0 * 10.0 / ref_disk_eta(0.2)},
        {"P_ideal_W": 1e-9},
        "momentum theory (actuator disk)", "P_ideal=T·V₀/η_ideal",
        ["propulsion", "momentum-theory", "closed-form"]),
    ))

    # green
    qs.append((_q(
        "green", "basic",
        "CO₂ from 1000 kg MGO (w_C=0.863)",
        {"formula": "co2_mgo", "m_fuel_kg": 1000.0},
        {"co2_kg": ref_co2(1000.0, W_C_MGO)},
        {"co2_kg": 1e-9},
        "MARPOL Annex VI Reg.2", "w_C(MGO)=0.863; m_CO₂=m_fuel·w_C·M(CO₂)/M(C)",
        ["green", "stoichiometry", "closed-form"]),
    ))
    qs.append((_q(
        "green", "basic",
        "CO₂ from 1000 kg LNG (w_C=0.812)",
        {"formula": "co2_lng", "m_fuel_kg": 1000.0},
        {"co2_kg": ref_co2(1000.0, W_C_LNG)},
        {"co2_kg": 1e-9},
        "MARPOL Annex VI Reg.2", "w_C(LNG)=0.812; m_CO₂=m_fuel·w_C·M(CO₂)/M(C)",
        ["green", "stoichiometry", "closed-form"]),
    ))

    # seakeeping
    qs.append((_q(
        "seakeeping", "basic",
        "Encounter frequency head seas (ω=1.0, V=5 m/s, μ=0)",
        {"formula": "encounter_freq", "omega": 1.0, "V": 5.0, "mu_deg": 0.0},
        {"omega_e": ref_encounter(1.0, 5.0, 0.0)},
        {"omega_e": 1e-9},
        "encounter-frequency identity", "ω_e=ω(1−Vωcosμ/g), g=9.81",
        ["seakeeping", "encounter-frequency", "closed-form"]),
    ))
    qs.append((_q(
        "seakeeping", "intermediate",
        "Heave natural period with added mass (m=5e6 kg, A33=0.8m, ρ=1025, ∇=5000 m³)",
        {"formula": "heave_period", "m_kg": 5.0e6, "a33_ratio": 0.8,
         "rho": 1025.0, "nabla_m3": 5000.0},
        {"T33": ref_heave_period(5.0e6, 0.8, 1025.0, 5000.0)},
        {"T33": 1e-9},
        "closed-form heave oscillator", "T33=2π√((m+A33)/(ρ∇g)), g=9.81",
        ["seakeeping", "heave", "natural-period"]),
    ))
    return qs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify bank already contains the frozen questions")
    args = ap.parse_args()

    bank = QuestionBank()
    existing = {(q["domain"], q["title"]) for q in bank.list_questions()}

    added, checked = 0, 0
    for q, *_ in build_questions():
        key = (q.domain, q.title)
        if key in existing:
            if args.check:
                got = bank.get_question(next(
                    x["id"] for x in bank.list_questions()
                    if (x["domain"], x["title"]) == key))
                assert got.committed, f"question not committed: {q.title}"
                assert got.commit_hash, f"question not frozen: {q.title}"
            continue
        # cross-implementation cross-check before freezing
        spec = q.specification
        got = pilot_candidate(spec)
        for k, v in q.expected_result.items():
            g = got[k]
            assert abs(g - v) <= 1e-12 * max(1.0, abs(v)), \
                f"pilot/builder mismatch on {q.title}: {k} {g} vs {v}"
        q.freeze()
        bank.add_question(q)
        bank.commit_question(q.id)
        added += 1
        checked += 1

    counts = {}
    for d in ("stability", "propulsion", "green", "seakeeping"):
        counts[d] = bank.question_count(domain=d)
    print(f"added={added} checked={checked}")
    print("domain counts:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
