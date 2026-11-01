"""
Question Generator — Creates verification questions from specifications and literature.

This tool is used by the VERIFIER role to populate the question bank.
Questions are generated from:
  - Published empirical formulas (e.g. ITTC-1957, Holtrop-Mennen)
  - Analytical solutions (e.g. flat plate friction, wave resistance)
  - Benchmark datasets (e.g. Series 60, DTMB models)
  - Literature reference values

Each question includes:
  - Provenance: where the reference value comes from
  - Tolerances: acceptable deviation (based on formula uncertainty)
  - Specification: input parameters for the candidate
"""
from __future__ import annotations

import math
import json
from typing import Any

from .question_bank import Question, QuestionBank


def generate_ittc57_questions() -> list[Question]:
    """
    Generate questions based on ITTC-1957 friction line.

    The ITTC-1957 model-ship correlation line:
      Cf = 0.075 / (log10(Re) - 2)^2

    This is an analytical formula, so we can compute exact reference values.
    Tolerance: 0.1% (formula is deterministic)
    """
    questions = []

    test_cases = [
        {"Re": 1e6, "label": "Re=1e6 (low speed model)"},
        {"Re": 1e7, "label": "Re=1e7 (typical model)"},
        {"Re": 1e8, "label": "Re=1e8 (full ship)"},
        {"Re": 1e9, "label": "Re=1e9 (large ship)"},
        {"Re": 5e6, "label": "Re=5e6 (medium model)"},
    ]

    for tc in test_cases:
        Re = tc["Re"]
        log_Re = math.log10(Re)
        Cf = 0.075 / (log_Re - 2) ** 2

        q = Question(
            id="",
            domain="resistance",
            difficulty="basic",
            title=f"ITTC-1957 Cf at {tc['label']}",
            specification={
                "formula": "ittc_1957",
                "Re": Re,
                "description": f"Compute friction coefficient using ITTC-1957 line at Re={Re:.1e}",
            },
            expected_result={"Cf": Cf},
            tolerances={"Cf": 0.001},
            provenance={
                "source": "ITTC-1957 Model-Ship Correlation Line",
                "reference": "Proceedings of the 7th ITTC, 1957",
                "formula": "Cf = 0.075 / (log10(Re) - 2)^2",
                "type": "analytical",
                "grade": "REAL",
            },
            tags=["ittc-1957", "friction", "analytical", "basic"],
        )
        questions.append(q)

    return questions


def generate_flat_plate_questions() -> list[Question]:
    """
    Generate questions based on flat plate boundary layer theory.

    Blasius solution for laminar flow:
      Cf_lam = 1.328 / sqrt(Re_L)

    Prandtl-Schlichting for turbulent flow (smooth plate):
      Cf_turb = 0.455 / (log10(Re_L))^2.58
    """
    questions = []

    # Laminar flat plate
    for Re_L in [1e5, 5e5, 1e6]:
        Cf_lam = 1.328 / math.sqrt(Re_L)
        q = Question(
            id="",
            domain="resistance",
            difficulty="basic",
            title=f"Laminar flat plate Cf at Re_L={Re_L:.0e}",
            specification={
                "formula": "blasius_laminar",
                "Re_L": Re_L,
                "description": f"Compute laminar flat plate friction coefficient at Re_L={Re_L:.0e}",
            },
            expected_result={"Cf": Cf_lam},
            tolerances={"Cf": 0.001},
            provenance={
                "source": "Blasius (1908) exact solution",
                "reference": "Schlichting, Boundary-Layer Theory",
                "formula": "Cf = 1.328 / sqrt(Re_L)",
                "type": "analytical",
                "grade": "REAL",
            },
            tags=["blasius", "laminar", "flat-plate", "analytical"],
        )
        questions.append(q)

    # Turbulent flat plate (Prandtl-Schlichting)
    for Re_L in [1e6, 1e7, 1e8, 1e9]:
        log_Re = math.log10(Re_L)
        Cf_turb = 0.455 / (log_Re ** 2.58)
        q = Question(
            id="",
            domain="resistance",
            difficulty="intermediate",
            title=f"Turbulent flat plate Cf at Re_L={Re_L:.0e}",
            specification={
                "formula": "prandtl_schlichting",
                "Re_L": Re_L,
                "description": f"Compute turbulent flat plate friction coefficient at Re_L={Re_L:.0e}",
            },
            expected_result={"Cf": Cf_turb},
            tolerances={"Cf": 0.005},
            provenance={
                "source": "Prandtl-Schlichting skin friction formula",
                "reference": "Schlichting, Boundary-Layer Theory, 8th Ed., Eq. (20.18)",
                "formula": "Cf = 0.455 / (log10(Re_L))^2.58",
                "type": "empirical",
                "grade": "EMPIRICAL",
                "grade_note": "reproducible from the cited formula; formula error is not "
                              "covered by this reference",
            },
            tags=["prandtl-schlichting", "turbulent", "flat-plate", "empirical"],
        )
        questions.append(q)

    return questions


def holtrop_1982_form_factor(
    Cb: float,
    L: float,
    B: float,
    T: float,
    lcb: float = 0.0,
    Cstern: float = 0.0,
    Cm: float | None = None,
) -> float:
    """Holtrop & Mennen (1982) form factor, full structure.

        1+k1 = c13 * [0.93 + c12 * (B/Lr)^0.92497 * (0.95 - Cp)^(-0.521448)
                                * (1 - Cp + 0.0225*lcb)^0.6906]
        c12  = (T/L)^0.2228446                         for T/L > 0.05
        c13  = 1 + 0.93 * Cstern
        Lr   = L * (1 - Cp + 0.0225*lcb)

    The previous implementation of this file used
    ``0.93 + 0.487118*Cb^-0.9249*(L/B)^0.15*(B/T)^0.2*0.1`` which is NOT the 1982
    formula: it drops c13/Cstern, Cp, lcb and the length of run, and the trailing
    factor 0.1 was added to match a pilot implementation ("scaled for pilot").
    Consequences (falsified by tools/verifier/tests/test_reference_audit.py):
      1+k*(Cb=0.6) = 1.0548  >  1+k*(Cb=0.8) = 0.9947  < 1.0
    i.e. the form factor DECREASED for a fuller hull and fell below unity, which
    is physically impossible; the bank would have failed a correct candidate.
    """
    if not (0.0 < Cb < 1.0):
        raise ValueError(f"Block coefficient out of range: {Cb}")
    if min(L, B, T) <= 0:
        raise ValueError("Dimensions must be positive")
    if Cm is None:
        Cm = 1.0 - 0.05 * (1.0 - Cb)   # documented assumption, only enters via Cp
    Cp = Cb / Cm
    t_over_l = T / L
    if t_over_l > 0.05:
        c12 = t_over_l ** 0.2228446
    elif t_over_l > 0.02:
        c12 = 48.20 * (t_over_l - 0.02) ** 2.078 + 0.479948
    else:
        raise ValueError(f"T/L={t_over_l:.4f} outside Holtrop-1982 validity range")
    c13 = 1.0 + 0.93 * Cstern
    Lr = L * (1.0 - Cp + 0.0225 * lcb)
    if Lr <= 0:
        raise ValueError(f"length of run Lr={Lr:.3f} <= 0")
    form_factor = c13 * (
        0.93
        + c12 * (B / Lr) ** 0.92497 * (0.95 - Cp) ** (-0.521448)
        * (1.0 - Cp + 0.0225 * lcb) ** 0.6906
    )
    if form_factor < 1.0:
        raise ValueError(f"form factor {form_factor:.4f} < 1.0 — reject, not emit")
    return form_factor


def generate_holtrop_questions() -> list[Question]:
    """
    Generate questions based on the Holtrop-Mennen (1982) form factor.

    Reference values are computed from the published 1982 formula.  Because the
    formula constants could not be re-checked against the original paper table in
    this environment, the family is graded EMPIRICAL (not REAL): reproducible,
    invariant-checked, but not an experimental anchor.
    """
    questions = []

    ship_cases = [
        {"Cb": 0.60, "L": 100.0, "B": 15.0, "T": 6.0, "lcb": 0.0, "Cstern": 0.0,
         "label": "Series 60 Cb=0.6"},
        {"Cb": 0.70, "L": 150.0, "B": 22.0, "T": 8.5, "lcb": 2.0, "Cstern": 0.0,
         "label": "Bulk carrier Cb=0.7"},
        {"Cb": 0.80, "L": 200.0, "B": 32.0, "T": 11.0, "lcb": 2.0, "Cstern": 0.0,
         "label": "Tanker Cb=0.8"},
    ]

    for sc in ship_cases:
        expected_1pk = holtrop_1982_form_factor(
            sc["Cb"], sc["L"], sc["B"], sc["T"], lcb=sc["lcb"], Cstern=sc["Cstern"]
        )
        q = Question(
            id="",
            domain="resistance",
            difficulty="intermediate",
            title=f"Holtrop-1982 form factor: {sc['label']}",
            specification={
                "method": "holtrop_mennen_form_factor",
                "Cb": sc["Cb"],
                "L": sc["L"],
                "B": sc["B"],
                "T": sc["T"],
                "lcb": sc["lcb"],
                "Cstern": sc["Cstern"],
                "description": (
                    f"Compute form factor (1+k1) for {sc['label']} using Holtrop-Mennen 1982; "
                    f"lcb is percent of L positive forward of 0.5L, Cstern in [-25, +10]"
                ),
            },
            expected_result={"1+k": expected_1pk},
            tolerances={"1+k": 0.01},  # 1% relative — formula is deterministic
            provenance={
                "source": "Holtrop & Mennen (1982) form factor, full 1982 structure",
                "reference": "Holtrop, J. & Mennen, G.G.J., 'An Approximate Power Prediction "
                             "Method', International Shipbuilding Progress, 1982",
                "formula": "1+k1 = c13*(0.93 + c12*(B/Lr)^0.92497*(0.95-Cp)^-0.521448"
                           "*(1-Cp+0.0225*lcb)^0.6906)",
                "type": "empirical",
                "grade": "EMPIRICAL",
                "grade_note": "constants transcribed from literature, not re-verified against "
                              "the original table offline; invariant-gated by reference_audit",
            },
            tags=["holtrop-mennen", "form-factor", "empirical"],
        )
        questions.append(q)

    return questions


def generate_all_questions() -> list[Question]:
    """Generate all available questions."""
    questions = []
    questions.extend(generate_ittc57_questions())
    questions.extend(generate_flat_plate_questions())
    questions.extend(generate_holtrop_questions())
    return questions


def populate_question_bank(qbank_path: str = None) -> dict[str, int]:
    """
    Populate the question bank with all generated questions.
    Returns count of questions added per domain.
    """
    qbank = QuestionBank(qbank_path) if qbank_path else QuestionBank()
    questions = generate_all_questions()

    counts = {}
    for q in questions:
        qid = qbank.add_question(q)
        qbank.commit_question(qid)
        counts[q.domain] = counts.get(q.domain, 0) + 1

    return counts


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    counts = populate_question_bank(path)
    print(f"Question bank populated: {json.dumps(counts)}")
    total = sum(counts.values())
    print(f"Total questions: {total}")
