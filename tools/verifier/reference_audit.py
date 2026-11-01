#!/usr/bin/env python3
"""
Reference-Value Audit — independent re-check of every verifier question bank entry.

Why this exists
---------------
The AlphaEvolve split only works if the *references* the verifier holds are
themselves trustworthy.  If a reference value is produced by the same code path
as the candidate implementation, the verifier becomes a mirror: a systematically
wrong formula passes 100% of the questions (self-referential, "circular"
verification).  If a reference value violates a physical invariant, the verifier
*rejects* correct implementations (false negative) — which is strictly worse than
a wrong algorithm, because it silently deletes good work.

This auditor therefore never imports the question generator and never trusts the
`expected_result` field.  For each question it:

  1. re-derives the value through an INDEPENDENT implementation path
     (Decimal-based / separately transcribed formulas, see `independent_value`);
  2. applies family INVARIANTS that any physically admissible reference must
     satisfy (positivity, monotonicity, 1+k >= 1, plausibility band);
  3. measures the *effective acceptance window* of the stored tolerance, and
     flags tolerances so wide the check degenerates into a tautology;
  4. assigns an evidence grade per question (REAL / EMPIRICAL / UNVERIFIED)
     derived from provenance, never from the value itself.

Verdicts: PASS / FAIL (reference falsified) / WARN (usable but unverified).

Usage
-----
  python -m verifier.reference_audit --bank <questions.db> [--json out.json]
                                     [--markdown out.md] [--mutation-test]
Exit code 0 only if no FAIL is reported.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from decimal import Decimal, getcontext
from typing import Any, Callable, Optional

getcontext().prec = 40

# ---------------------------------------------------------------------------
# Independent reference implementations
#
# Deliberately NOT shared with tools/verifier/generate_questions.py: different
# code, different numeric type where practical, different algebra ordering.
# ---------------------------------------------------------------------------


def ittc57_cf(Re: float) -> float:
    """ITTC-1957 model-ship correlation line Cf = 0.075 / (log10(Re) - 2)^2."""
    if Re <= 0:
        raise ValueError("Re must be positive")
    lg = Decimal(str(Re)).log10()
    return float(Decimal("0.075") / (lg - Decimal(2)) ** 2)


def blasius_cf(Re_L: float) -> float:
    """Blasius laminar flat-plate Cf = 1.328 / sqrt(Re_L)."""
    if Re_L <= 0:
        raise ValueError("Re_L must be positive")
    return float(Decimal("1.328") / Decimal(str(Re_L)).sqrt())


def prandtl_schlichting_cf(Re_L: float) -> float:
    """Prandtl-Schlichting smooth turbulent flat plate Cf = 0.455/(log10 Re)^2.58."""
    if Re_L <= 0:
        raise ValueError("Re_L must be positive")
    lg = Decimal(str(Re_L)).log10()
    return float(Decimal("0.455") / (lg ** Decimal("2.58")))


def holtrop_1982_form_factor(
    Cb: float,
    L: float,
    B: float,
    T: float,
    lcb: float = 0.0,
    Cstern: float = 0.0,
    Cm: Optional[float] = None,
) -> float:
    """Holtrop & Mennen (1982) form factor 1+k1.

        1+k1 = c13 * [ 0.93 + c12 * (B/Lr)^0.92497 * (0.95 - Cp)^(-0.521448)
                                 * (1 - Cp + 0.0225*lcb)^0.6906 ]
        c12  = (T/L)^0.2228446                        (T/L > 0.05)
             = 48.20*(T/L - 0.02)^2.078 + 0.479948    (0.02 < T/L <= 0.05)
        c13  = 1 + 0.93 * Cstern
        Lr   = L * (1 - Cp + 0.0225*lcb)              (length of run)

    Parameters `lcb` (percent of L, positive forward of 0.5L) and `Cstern`
    (cruiser stern -25..+10) are first-class inputs of the 1982 formula; dropping
    them (as a "simplified" variant is tempted to do) removes the two terms that
    make the form factor grow with hull fullness, i.e. it inverts the physics.

    PROVENANCE / GRADE: EMPIRICAL, constants transcribed from the literature and
    NOT re-verified against the original paper table in this offline environment
    -> every question built on this family is graded UNVERIFIED, not REAL.
    """
    if not (0.0 < Cb < 1.0):
        raise ValueError(f"Cb out of range: {Cb}")
    if min(L, B, T) <= 0:
        raise ValueError("L, B, T must be positive")
    if Cm is None:
        # documented assumption; Cm only enters through Cp = Cb/Cm
        Cm = 1.0 - 0.05 * (1.0 - Cb)
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
        raise ValueError(f"length of run Lr={Lr:.2f} <= 0 (Cp/lcb inconsistent)")

    inner = (
        0.93
        + c12
        * (B / Lr) ** 0.92497
        * (0.95 - Cp) ** (-0.521448)
        * (1.0 - Cp + 0.0225 * lcb) ** 0.6906
    )
    return c13 * inner


# ---------------------------------------------------------------------------
# Families, invariants and bounds
# ---------------------------------------------------------------------------

CF_RESISTANCE_BAND = (1e-5, 5e-2)   # any admissible friction coefficient
FORM_FACTOR_BAND = (1.02, 1.60)     # 1+k1 for displacement hulls, Holtrop-1982 range


@dataclass
class QuestionAudit:
    question_id: str
    title: str
    family: str
    grade: str
    recomputed: dict[str, Any] = field(default_factory=dict)
    rel_deviation: dict[str, float] = field(default_factory=dict)
    invariant_failures: list[str] = field(default_factory=list)
    max_effective_window: float = 0.0
    verdict: str = "PASS"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "family": self.family,
            "grade": self.grade,
            "recomputed": self.recomputed,
            "rel_deviation_pct": {k: round(100.0 * v, 6) for k, v in self.rel_deviation.items()},
            "invariant_failures": self.invariant_failures,
            "max_effective_acceptance_window_pct": round(100.0 * self.max_effective_window, 4),
            "verdict": self.verdict,
            "notes": self.notes,
        }


def _rel_dev(expected: float, reference: float) -> float:
    if reference == 0:
        return float("inf")
    return abs(expected - reference) / abs(reference)


def _cf_family_invariants(name: str, fn: Callable[[float], float]) -> list[str]:
    """Invariants every admissible friction line must satisfy."""
    fails = []
    sweep = [1e5, 1e6, 1e7, 1e8, 1e9]
    vals = [fn(r) for r in sweep[:4]] if name == "blasius" else [fn(r) for r in sweep]
    lo, hi = CF_RESISTANCE_BAND
    for r, v in zip(sweep, vals):
        if not (lo < v < hi):
            fails.append(f"{name}: Cf({r:.0e})={v:.3e} outside physical band {CF_RESISTANCE_BAND}")
    for i in range(1, len(vals)):
        if not vals[i] < vals[i - 1]:
            fails.append(f"{name}: Cf not strictly decreasing in Re at Re={sweep[i]:.0e}")
    return fails


def form_factor_invariants(
    fn: Callable[..., float],
    cases: list[dict[str, float]],
) -> list[str]:
    """Invariants of any admissible Holtrop-style form factor.

    Checked on the *family function*, so the verdict does not depend on which
    particular hull the bank happens to store.
    """
    fails = []
    base = dict(L=150.0, B=22.0, T=8.5, lcb=2.0, Cstern=0.0)
    cbs = [0.55, 0.60, 0.70, 0.80]
    vals = []
    for cb in cbs:
        v = fn(Cb=cb, **base)
        vals.append(v)
        lo, hi = FORM_FACTOR_BAND
        if not (lo <= v <= hi):
            fails.append(f"1+k(Cb={cb})={v:.4f} outside Holtrop-1982 band {FORM_FACTOR_BAND}")
        if not v >= 1.0:
            fails.append(f"1+k(Cb={cb})={v:.4f} < 1.0 — form factor below 1 is impossible")
    for i in range(1, len(vals)):
        if not vals[i] > vals[i - 1]:
            fails.append(
                f"1+k not increasing with Cb ({cbs[i-1]}->{cbs[i]}: "
                f"{vals[i-1]:.4f}->{vals[i]:.4f}); fuller hulls must show MORE form drag"
            )
    # c13 / Cstern: a cruiser stern reduces the form factor relative to a
    # pram-with-gondola stern; monotone in Cstern with slope 0.93*inner.
    f_m25 = fn(Cb=0.70, **{**base, "Cstern": -25.0})
    f_0 = fn(Cb=0.70, **base)
    f_p10 = fn(Cb=0.70, **{**base, "Cstern": 10.0})
    if not (f_m25 < f_0 < f_p10):
        fails.append(
            f"c13/Cstern term not monotone ({f_m25:.4f} < {f_0:.4f} < {f_p10:.4f} violated)"
        )
    # slot validity of the per-question cases (raises -> recorded as failure)
    for c in cases:
        try:
            fn(**c)
        except Exception as exc:  # noqa: BLE001 - surfaced to the report
            fails.append(f"case {c} undefined in model validity range: {exc}")
    return fails


def stored_reference_invariants(audits: list[dict]) -> list[str]:
    """Invariants applied to the values the bank ACTUALLY stores.

    `form_factor_invariants` validates a formula family; this one validates the
    shipped reference numbers themselves, so a bank built from a broken variant
    cannot slip through just because a correct implementation exists elsewhere.
    """
    fails = []
    for row in audits:
        fam = row["family"]
        exp = row["expected_result"]
        spec = row["specification"]
        if fam == "holtrop_mennen_form_factor":
            val = exp.get("1+k")
            if not isinstance(val, (int, float)):
                continue
            lo, hi = FORM_FACTOR_BAND
            if val < 1.0:
                fails.append(
                    f"[{row['title']}] stored 1+k={val:.4f} < 1.0 — physically impossible "
                    f"form factor"
                )
            elif not (lo <= val <= hi):
                fails.append(
                    f"[{row['title']}] stored 1+k={val:.4f} outside Holtrop-1982 band {(lo, hi)}"
                )
        elif fam in ("ittc_1957", "blasius_laminar", "prandtl_schlichting"):
            val = exp.get("Cf")
            if isinstance(val, (int, float)):
                lo, hi = CF_RESISTANCE_BAND
                if not (lo < val < hi):
                    fails.append(f"[{row['title']}] stored Cf={val:.3e} outside band {CF_RESISTANCE_BAND}")
    return fails


def _family_of(spec: dict, method_key: str) -> str:
    return spec.get("formula") or spec.get(method_key) or "unknown"


def audit_question(row: dict) -> QuestionAudit:
    spec = row["specification"]
    expected = row["expected_result"]
    tolerances = row["tolerances"]
    prov = row.get("provenance") or {}

    family = _family_of(spec, "method")
    qa = QuestionAudit(
        question_id=row["id"],
        title=row["title"],
        family=family,
        grade=str(prov.get("grade") or ("REAL" if prov.get("type") == "analytical" else "UNVERIFIED")),
    )

    # --- independent recomputation -----------------------------------------
    if family == "ittc_1957":
        qa.recomputed = {"Cf": ittc57_cf(float(spec["Re"]))}
    elif family == "blasius_laminar":
        qa.recomputed = {"Cf": blasius_cf(float(spec["Re_L"]))}
    elif family == "prandtl_schlichting":
        qa.recomputed = {"Cf": prandtl_schlichting_cf(float(spec["Re_L"]))}
    elif family == "holtrop_mennen_form_factor":
        case = {k: float(spec[k]) for k in ("Cb", "L", "B", "T") if k in spec}
        for extra in ("lcb", "Cstern", "Cm"):
            if spec.get(extra) is not None:
                case[extra] = float(spec[extra])
        qa.recomputed = {"1+k": holtrop_1982_form_factor(**case)}
        qa.notes.append(
            "form factor reference is EMPIRICAL: reconstructible from the published "
            "formula, but the constants were not re-checked against the original table"
        )
    else:
        qa.grade = "UNKNOWN"
        qa.verdict = "WARN"
        qa.notes.append(f"no independent implementation registered for family '{family}'")
        return qa

    # --- deviation vs stored reference --------------------------------------
    for key, ref_val in qa.recomputed.items():
        if key not in expected:
            qa.invariant_failures.append(f"stored expected_result lacks key '{key}'")
            continue
        qa.rel_deviation[key] = _rel_dev(float(expected[key]), float(ref_val))
        tol = float(tolerances.get(key, 0.05))
        qa.max_effective_window = max(qa.max_effective_window, tol)

    # --- family invariants ---------------------------------------------------
    if family == "ittc_1957":
        qa.invariant_failures += _cf_family_invariants("ittc57", ittc57_cf)
    elif family == "blasius_laminar":
        qa.invariant_failures += _cf_family_invariants("blasius", blasius_cf)
    elif family == "prandtl_schlichting":
        qa.invariant_failures += _cf_family_invariants("prandtl_schlichting", prandtl_schlichting_cf)
    elif family == "holtrop_mennen_form_factor":
        qa.invariant_failures += form_factor_invariants(holtrop_1982_form_factor, [])

    # --- verdict -------------------------------------------------------------
    if qa.invariant_failures:
        qa.verdict = "FAIL"
    elif qa.rel_deviation and max(qa.rel_deviation.values()) > float(
        max(tolerances.values()) if tolerances else 0.05
    ):
        qa.verdict = "FAIL"
        qa.invariant_failures.append("stored reference deviates from independent recomputation")
    elif qa.max_effective_window >= 0.10:
        qa.verdict = "WARN"
        qa.notes.append(
            f"acceptance window {qa.max_effective_window:.1%} is wide enough to admit "
            f"order-of-magnitude errors"
        )
    if qa.grade in ("UNVERIFIED", "EMPIRICAL") and qa.verdict == "PASS":
        qa.verdict = "WARN"
    return qa


# ---------------------------------------------------------------------------
# Bank loading / reporting
# ---------------------------------------------------------------------------


def load_bank(db_path: str) -> list[dict]:
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, domain, difficulty, title, specification, expected_result,"
            " tolerances, provenance FROM questions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("specification", "expected_result", "tolerances", "provenance"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else {}
            except (TypeError, ValueError):
                d[k] = {}
        if not isinstance(d["provenance"], dict):
            d["provenance"] = {}
        out.append(d)
    return out


def audit_bank(db_path: str) -> dict[str, Any]:
    questions = load_bank(db_path)
    audits = [audit_question(q) for q in questions]
    raw = [{"title": q["title"], "family": _family_of(q["specification"], "method"),
            "expected_result": q["expected_result"], "specification": q["specification"]}
           for q in questions]
    stored_fails = stored_reference_invariants(raw)
    if stored_fails:
        by_title = {}
        for msg in stored_fails:
            by_title.setdefault(msg.split("]")[0].lstrip("["), []).append(msg)
        for a in audits:
            for msg in by_title.get(a.title, []):
                a.invariant_failures.append(msg)
                a.verdict = "FAIL"
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for a in audits:
        counts[a.verdict] = counts.get(a.verdict, 0) + 1
    grades: dict[str, int] = {}
    for a in audits:
        grades[a.grade] = grades.get(a.grade, 0) + 1
    return {
        "bank": db_path,
        "n_questions": len(questions),
        "verdicts": counts,
        "grades": grades,
        "families": sorted({a.family for a in audits}),
        "questions": [a.as_dict() for a in audits],
        "ok": counts["FAIL"] == 0,
    }


def mutation_test(db_path: str, factor: float = 1.2) -> dict[str, Any]:
    """Inject a 20% corruption into every reference and require the auditor to catch it.

    Without this, "all PASS" could simply mean the audit is a tautology.
    """
    questions = load_bank(db_path)
    missed = []
    for q in questions:
        base = audit_question(q)
        if base.verdict == "FAIL":
            continue  # already falsified; not a mutation-detection question
        corrupted = dict(q)
        corrupted["expected_result"] = {
            k: (v * factor if isinstance(v, (int, float)) else v)
            for k, v in q["expected_result"].items()
        }
        if audit_question(corrupted).verdict != "FAIL":
            missed.append(q["id"])
    return {
        "factor": factor,
        "n_missed": len(missed),
        "missed_ids": missed,
        "ok": not missed,
    }


def to_markdown(report: dict, mutation: Optional[dict] = None) -> str:
    lines = [
        "# Verifier question-bank reference audit",
        "",
        f"- bank: `{report['bank']}`",
        f"- questions: **{report['n_questions']}**",
        f"- verdicts: PASS={report['verdicts'].get('PASS',0)} "
        f"WARN={report['verdicts'].get('WARN',0)} FAIL={report['verdicts'].get('FAIL',0)}",
        f"- grades: {report['grades']}",
        "",
        "| question | family | grade | stored vs independent | worst invariant failure | verdict |",
        "|---|---|---|---|---|---|",
    ]
    for q in report["questions"]:
        dev = ", ".join(f"{k}: {v:.4f}%" for k, v in q["rel_deviation_pct"].items()) or "—"
        fail = q["invariant_failures"][0] if q["invariant_failures"] else "—"
        fail = fail.replace("|", "/")[:88]
        lines.append(
            f"| {q['title'][:46]} | {q['family']} | {q['grade']} | {dev} | {fail} | **{q['verdict']}** |"
        )
    if mutation:
        caught = "all caught" if mutation["ok"] else f"{mutation['n_missed']} missed"
        lines += [
            "",
            f"Mutation test (x{mutation['factor']} reference corruption): {caught}",
        ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Audit verifier question-bank reference values")
    ap.add_argument("--bank", default=os.path.join(os.path.dirname(__file__), "question_bank", "questions.db"))
    ap.add_argument("--json", default=None)
    ap.add_argument("--markdown", default=None)
    ap.add_argument("--mutation-test", action="store_true")
    args = ap.parse_args(argv)

    report = audit_bank(args.bank)
    mutation = mutation_test(args.bank) if args.mutation_test else None
    if mutation is not None:
        report["mutation_test"] = mutation
        report["ok"] = report["ok"] and mutation["ok"]
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    md = to_markdown(report, mutation)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(md)
    else:
        print(md)
    print(f"[reference_audit] ok={report['ok']} verdicts={report['verdicts']}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())