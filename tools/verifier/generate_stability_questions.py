"""
Stability-domain questions for the verifier question bank (held-out exam).

Closes the documented gap in ``docs/verifier/DESIGN.md`` §7 ("更多学科域的题库"):
the bank previously covered only ``resistance``, so a worker could ship a
stability feature with no held-out exam to pass.

Every expected value here is derived from a *closed-form analytical* result, not
from any platform formula:

  * GZ of a wall-sided ship:      GZ = sin φ (GM + ½ BM tan²φ)
  * area under that curve:        exact antiderivative (see stability_reference)
  * IMO IS Code 2008 §2.2.1      thresholds transcribed from the Code

Run ``python -m tools.verifier.generate_stability_questions`` to append them to
the bank (idempotent: questions already present by title are skipped).
"""
from __future__ import annotations

import json
import os

from .question_bank import DEFAULT_QBANK_PATH, Question, QuestionBank
from .stability_reference import (
    IMO_IS_CODE_2008,
    wall_sided_area,
    wall_sided_gz,
)

#: Wall-sided reference ships (GM, BM) — the pair fully defines the curve.
SHIPS = [
    {"id": "ws-gm1.5-bm4.0", "GM": 1.5, "BM": 4.0, "note": "typical cargo ship"},
    {"id": "ws-gm0.4-bm6.0", "GM": 0.4, "BM": 6.0, "note": "tender, large BM"},
    {"id": "ws-gm3.0-bm2.5", "GM": 3.0, "BM": 2.5, "note": "stiff, small BM"},
]


def generate_stability_questions() -> list[Question]:
    """Analytical stability questions (wall-sided closed form + IS Code)."""
    out: list[Question] = []

    for ship in SHIPS:
        gm, bm = ship["GM"], ship["BM"]

        # 1) single righting-arm points (basic)
        for phi in (10.0, 20.0, 30.0):
            out.append(Question(
                id="",
                domain="stability",
                difficulty="basic",
                title=f"Wall-sided GZ at {phi:.0f} deg ({ship['id']})",
                specification={
                    "method": "wall_sided_gz",
                    "GM": gm, "BM": bm, "heel_deg": phi,
                    "description": "Compute the righting arm GZ [m] of a wall-sided "
                                   "ship using GZ = sin(phi)(GM + 0.5*BM*tan^2(phi))",
                },
                expected_result={"gz": wall_sided_gz(phi, gm, bm)},
                tolerances={"gz": 0.005},
                provenance={
                    "source": "Wall-sided ship, closed form",
                    "reference": "Biran, Ship Hydrostatics and Stability, 2nd ed., §5.4",
                    "formula": "GZ = sin(phi)*(GM + 0.5*BM*tan^2(phi))",
                    "type": "analytical",
                },
                tags=["stability", "gz", "wall-sided", "analytical"],
            ))

        # 2) curve areas (intermediate) — exact antiderivative, no quadrature
        for (a0, a1, key) in ((0.0, 30.0, "area_0_30"),
                              (30.0, 40.0, "area_30_40"),
                              (0.0, 40.0, "area_0_40")):
            out.append(Question(
                id="",
                domain="stability",
                difficulty="intermediate",
                title=f"Wall-sided GZ area {a0:.0f}-{a1:.0f} deg ({ship['id']})",
                specification={
                    "method": "wall_sided_area",
                    "GM": gm, "BM": bm, "phi1_deg": a0, "phi2_deg": a1,
                    "description": f"Area under the wall-sided GZ curve between "
                                   f"{a0:.0f} and {a1:.0f} deg [m.rad]",
                },
                expected_result={"area": wall_sided_area(a0, a1, gm, bm)},
                tolerances={"area": 0.005},
                provenance={
                    "source": "Exact integral of the wall-sided GZ curve",
                    "reference": "integral of sin(phi)(GM+0.5*BM*tan^2 phi) dphi "
                                 "= -GM cos(phi) + 0.5 BM (sec(phi)+cos(phi))",
                    "type": "analytical",
                },
                tags=["stability", "area", "wall-sided", "analytical",
                      IMO_IS_CODE_2008[key]["ref"]],
            ))

    # 3) one criterion-level question: the Code's own 0-40 deg threshold
    out.append(Question(
        id="",
        domain="stability",
        difficulty="advanced",
        title="IS Code 2008 §2.2.1: area 0-40 deg threshold is 0.090 m·rad",
        specification={
            "method": "imo_threshold_lookup",
            "criterion": "area_0_40",
            "description": "State the IMO IS Code 2008 Part A Ch.2 para 2.2.1.2 "
                           "minimum area under the GZ curve up to 40 deg",
        },
        expected_result={"threshold": IMO_IS_CODE_2008["area_0_40"]["threshold"]},
        tolerances={"threshold": 0.0},
        provenance={
            "source": "IMO Res. MSC.267(85), IS Code 2008",
            "reference": "Part A, Chapter 2, §2.2.1.2",
            "type": "regulatory",
        },
        tags=["stability", "imo", "is-code-2008", "criterion"],
    ))
    return out


def populate(qbank_path: str | None = None) -> dict:
    """Append the stability questions to the bank, skipping duplicates by title."""
    path = qbank_path or DEFAULT_QBANK_PATH
    # git does not carry file modes: restore the isolation permission after checkout
    if os.path.exists(path):
        os.chmod(path, 0o600)
    qbank = QuestionBank(path)
    existing = {q["title"] for q in qbank.list_questions(domain="stability")}
    added = 0
    for q in generate_stability_questions():
        if q.title in existing:
            continue
        qid = qbank.add_question(q)
        qbank.commit_question(qid)
        added += 1
    return {"added": added, "domain_total": qbank.question_count("stability")}


if __name__ == "__main__":
    print(json.dumps(populate(), ensure_ascii=False))