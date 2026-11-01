#!/usr/bin/env python3
"""
audit_question_bank — verify the verifier's ground truth ("who verifies the verifier").

Motivation
----------
Every candidate implementation on this platform is judged against the reference
values stored in ``tools/verifier/question_bank/questions.db``.  A wrong
reference value silently converts the whole verification pipeline into a rubber
stamp.  The platform KPI explicitly excludes self-consistency
("自洽测试不计入"), so a reference value that merely re-evaluates the candidate's
own formula is *not* evidence.

This auditor re-derives each committed reference value from an independent,
literature-faithful implementation (:mod:`tools.verifier.reference_impl`) and
classifies every question:

    CONFIRMED              independent recompute agrees within ``CIRCULAR_TOL``
    DEVIATION_EXCEEDS      independent recompute disagrees by more than the
                           question's own tolerance (reported with the % gap)
    OUTSIDE_SENSITIVITY    stored value cannot be reproduced by *any* admissible
                           choice of the inputs the question omits
    CIRCULAR_REFERENCE     stored value equals the candidate/generator formula
                           exactly while being misattributed to the cited source
    NO_INDEPENDENT_REF     no independent implementation available for this spec
    UNSUPPORTED_SPEC       spec does not carry enough information to re-derive

Bank-level checks (independent of individual questions):

    permission_isolation   the module promises chmod 0600 (``question_bank.py``
                           docstring); group/other-readable banks leak held-out
                           questions to the workers they are meant to test
    commit_hash_integrity  recompute the SHA-256 commit hash exactly as
                           ``QuestionBank.commit`` does and compare
    tolerance_semantics    confirm the deviation actually applied by
                           ``verifier.py`` is relative (|a-e|/|e|), so the
                           tolerance numbers mean what the schema docstring says
    coverage               domains / difficulty / formula families represented
    physical_trend         monotonicity sanity of a parameter family, e.g. the
                           form factor must grow with fullness Cb

Usage
-----
    python -m tools.verifier.audit_question_bank            # human summary
    python -m tools.verifier.audit_question_bank --json out.json
    python -m tools.verifier.audit_question_bank --db path/to/questions.db

Exit status: 0 if every question is CONFIRMED and no bank-level check fails,
1 otherwise.  The target database is opened read-only (``mode=ro``) and never
modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
from typing import Any, Dict, List, Optional

try:  # package import
    from tools.verifier import reference_impl as ref
except ImportError:  # direct script execution
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from tools.verifier import reference_impl as ref

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "question_bank", "questions.db")

# Agreement threshold for "the stored value is the same number as my independent
# recompute".  Formulas are closed-form, so agreement is exact to round-off;
# anything beyond 1e-9 is a real disagreement, not numerics.
CIRCULAR_TOL = 1e-9
# Platform-wide acceptance standard used when a question's own tolerance is
# looser than the reporting threshold for the audit narrative.
PLATFORM_PRECISION = 0.03

VERDICT_ORDER = [
    "CONFIRMED",
    "DEVIATION_EXCEEDS",
    "OUTSIDE_SENSITIVITY",
    "CIRCULAR_REFERENCE",
    "UNSUPPORTED_SPEC",
    "NO_INDEPENDENT_REF",
]


# --------------------------------------------------------------------------- #
# Bank-level checks
# --------------------------------------------------------------------------- #
def check_permission_isolation(db_path: str) -> Dict[str, Any]:
    """The schema promises 0600; verify it, because group/other bits leak the bank."""
    st = os.stat(db_path)
    mode = stat.S_IMODE(st.st_mode)
    leaked = bool(mode & 0o077)
    return {
        "check": "permission_isolation",
        "ok": not leaked and mode == 0o600,
        "observed_mode": oct(mode),
        "expected_mode": "0o600",
        "detail": (
            "question bank is group/other accessible -> held-out questions are readable "
            "by the candidates they are meant to test"
            if leaked
            else "question bank is owner-only"
        ),
    }


def check_commit_hash(row: sqlite3.Row) -> Dict[str, Any]:
    """Recompute the tamper-evidence hash exactly as QuestionBank.commit does."""
    payload = json.dumps(
        {
            "domain": row["domain"],
            "title": row["title"],
            "specification": row["specification"],
            "expected_result": row["expected_result"],
            "tolerances": row["tolerances"],
            "version": row["version"],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    recomputed = hashlib.sha256(payload.encode()).hexdigest()
    stored = row["commit_hash"] or ""
    return {
        "ok": bool(stored) and stored == recomputed,
        "stored": stored,
        "recomputed": recomputed,
        "committed": bool(row["committed"]),
    }


def check_tolerance_semantics(verifier_path: Optional[str] = None) -> Dict[str, Any]:
    """Confirm verifier.py applies a *relative* tolerance (schema docstring says so)."""
    if verifier_path is None:
        verifier_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verifier.py")
    try:
        with open(verifier_path, "r", encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        return {"ok": False, "detail": f"cannot read verifier.py: {exc}"}
    relative = "abs(actual_val - expected_val) / abs(expected_val)" in src
    return {
        "ok": relative,
        "detail": (
            "deviation is computed as |actual-expected|/|expected| -> tolerance values "
            "are RELATIVE, matching the schema docstring"
            if relative
            else "deviation formula not recognised -> tolerance semantics unclear"
        ),
    }


def check_physical_trend(question_rows: List[sqlite3.Row]) -> Dict[str, Any]:
    """Form factor must increase with fullness: d(1+k)/dCb > 0.

    This is a qualitative, source-independent check: any hull-form family from
    the Holtrop database shows a larger form factor for fuller hulls.  A
    parameter sweep with the opposite slope is a structural error, not a
    rounding issue.
    """
    fam = []
    for row in question_rows:
        spec = json.loads(row["specification"])
        exp = json.loads(row["expected_result"])
        if spec.get("method") == "holtrop_mennen_form_factor" and "1+k" in exp:
            fam.append((spec["Cb"], exp["1+k"], spec["L"], spec["B"], spec["T"]))
    if len(fam) < 2:
        return {"ok": True, "detail": "fewer than 2 form-factor questions — trend not testable", "series": []}
    fam.sort()
    slopes = [fam[i + 1][1] - fam[i][1] for i in range(len(fam) - 1)]
    bank_ok = all(s > 0 for s in slopes)

    # Independent reference for the same scenarios
    indep = [
        (Cb, ref.holtrop_mennen_form_factor(Cb, L, B, T)["1+k"]) for Cb, _, L, B, T in fam
    ]
    indep_ok = all(indep[i + 1][1] - indep[i][1] > 0 for i in range(len(indep) - 1))
    return {
        "ok": bank_ok,
        "detail": (
            "stored 1+k decreases with Cb (physically inverted)"
            if not bank_ok
            else "stored 1+k increases with Cb"
        )
        + "; independent Holtrop "
        + ("increases with Cb (correct)" if indep_ok else "also decreases — check implementation"),
        "series": [
            {"Cb": cb, "bank_1pk": v, "independent_1pk": iv} for (cb, v, _, _, _), (_, iv) in zip(fam, indep)
        ],
    }


# --------------------------------------------------------------------------- #
# Per-question reference re-derivation
# --------------------------------------------------------------------------- #
def _generator_formula_value(spec: Dict[str, Any]) -> Optional[float]:
    """Reproduce the value the *generator* of the bank computed, if recognisable.

    ``generate_questions._holtrop_form_factor`` is a fabricated "simplified"
    form factor; if a stored reference equals it exactly, the reference is a
    re-evaluation of the candidate's own formula, i.e. circular evidence.
    """
    if spec.get("method") != "holtrop_mennen_form_factor":
        return None
    try:
        Cb, L, B, T = spec["Cb"], spec["L"], spec["B"], spec["T"]
        return 0.93 + 0.487118 * (Cb ** (-0.9249)) * (L / B) ** 0.15 * (B / T) ** 0.2 * 0.1
    except Exception:  # pragma: no cover - defensive
        return None


def audit_question(row: sqlite3.Row) -> Dict[str, Any]:
    """Independently re-derive one question's expected result and classify it."""
    spec = json.loads(row["specification"])
    expected = json.loads(row["expected_result"])
    tolerances = json.loads(row["tolerances"]) if row["tolerances"] else {}
    provenance = json.loads(row["provenance"]) if row["provenance"] else {}

    formula = spec.get("formula") or spec.get("method")
    out: Dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
        "domain": row["domain"],
        "difficulty": row["difficulty"],
        "formula": formula,
        "stored": expected,
        "specification": spec,
        "provenance_source": provenance.get("source", ""),
        "independent": {},
        "deviations": {},
        "tolerance": tolerances,
    }

    # ---- flat-plate family -------------------------------------------------
    re_key = "Re_L" if "Re_L" in spec else ("Re" if "Re" in spec else None)

    if formula == "blasius_laminar":
        re_l = float(spec[re_key] if re_key else spec["Re_L"])
        value = ref.blasius_laminar_cf(re_l)
        out["independent"] = {"Cf": value}
    elif formula == "prandtl_schlichting":
        re_l = float(spec[re_key] if re_key else spec["Re_L"])
        value = ref.prandtl_schlichting_cf(re_l)
        out["independent"] = {"Cf": value}
    elif formula in ("ittc_1957", "ittc57"):
        re_l = float(spec[re_key] if re_key else spec["Re"])
        value = ref.ittc_1957_cf(re_l)
        out["independent"] = {"Cf": value}

    # ---- Holtrop & Mennen (1982) form factor --------------------------------
    elif formula == "holtrop_mennen_form_factor":
        try:
            Cb, L, B, T = float(spec["Cb"]), float(spec["L"]), float(spec["B"]), float(spec["T"])
        except KeyError as exc:
            out["verdict"] = "UNSUPPORTED_SPEC"
            out["note"] = f"missing input {exc} — cannot re-derive"
            return out
        full = ref.holtrop_mennen_form_factor(Cb, L, B, T)
        band = ref.holtrop_mennen_form_factor_band(Cb, L, B, T)
        out["independent"] = {"1+k": full["1+k"]}
        out["independent_detail"] = {k: v for k, v in full.items()}
        out["sensitivity_band"] = band
        out["applicability_violations"] = ref.applicability_violations(Cb, L, B, T)
        gen_value = _generator_formula_value(spec)
        out["generator_formula_value"] = gen_value
    else:
        out["verdict"] = "NO_INDEPENDENT_REF"
        out["note"] = f"no independent implementation registered for formula {formula!r}"
        return out

    # ---- deviation + verdict ----------------------------------------------
    for key, indep_val in out["independent"].items():
        if key not in expected:
            continue
        exp_val = expected[key]
        rel = abs(indep_val - exp_val) / abs(exp_val) if exp_val else float("inf")
        out["deviations"][key] = rel

    max_dev = max(out["deviations"].values()) if out["deviations"] else float("inf")
    tol = max(tolerances.values()) if tolerances else PLATFORM_PRECISION
    out["max_deviation"] = max_dev
    out["question_tolerance"] = tol

    circular = (
        out.get("generator_formula_value") is not None
        and abs(out["generator_formula_value"] - expected.get("1+k", float("nan"))) < CIRCULAR_TOL
        and max_dev > CIRCULAR_TOL
    )
    band = out.get("sensitivity_band") or {}
    outside_band = bool(band) and not (band["min"] <= expected.get("1+k", 0.0) <= band["max"])

    if max_dev <= CIRCULAR_TOL:
        out["verdict"] = "CONFIRMED"
    elif circular:
        out["verdict"] = "CIRCULAR_REFERENCE"
        out["note"] = (
            "stored reference equals the candidate/generator formula exactly, so the "
            "pass verdicts recorded against it are self-consistency, not verification"
        )
    elif outside_band:
        out["verdict"] = "OUTSIDE_SENSITIVITY"
        out["note"] = "stored value unreachable for any admissible Cp/LCB/Cstern"
    else:
        out["verdict"] = "DEVIATION_EXCEEDS"
    return out


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run_audit(db_path: str = DEFAULT_DB) -> Dict[str, Any]:
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = list(conn.execute("SELECT * FROM questions ORDER BY domain, difficulty, title"))
    finally:
        conn.close()

    questions = [audit_question(r) for r in rows]
    hashes = [check_commit_hash(r) for r in rows]

    counts: Dict[str, int] = {}
    for q in questions:
        counts[q.get("verdict", "NO_INDEPENDENT_REF")] = counts.get(q.get("verdict", "NO_INDEPENDENT_REF"), 0) + 1

    checks = {
        "permission_isolation": check_permission_isolation(db_path),
        "commit_hash_integrity": {
            "ok": all(h["ok"] for h in hashes),
            "detail": f"{sum(1 for h in hashes if h['ok'])}/{len(hashes)} commit hashes reproduce exactly",
            "failures": [h for h in hashes if not h["ok"]],
        },
        "tolerance_semantics": check_tolerance_semantics(),
        "physical_trend": check_physical_trend(rows),
    }

    domains = sorted({q["domain"] for q in questions})
    formulae = sorted({str(q["formula"]) for q in questions})
    coverage = {
        "n_questions": len(questions),
        "domains": domains,
        "formulas": formulae,
        "difficulty": sorted({q["difficulty"] for q in questions}),
        "note": (
            "single-domain bank: no stability / propulsion / seakeeping ground truth, "
            "so those capabilities cannot be verified through this mechanism"
            if len(domains) == 1
            else ""
        ),
    }

    failures = [q for q in questions if q.get("verdict") != "CONFIRMED"]
    failed_checks = [name for name, c in checks.items() if isinstance(c, dict) and c.get("ok") is False]
    return {
        "db_path": os.path.abspath(db_path),
        "generated_by": "tools/verifier/audit_question_bank.py",
        "thresholds": {"circular_tol": CIRCULAR_TOL, "platform_precision": PLATFORM_PRECISION},
        "verdict_counts": counts,
        "verdict_order": VERDICT_ORDER,
        "questions": questions,
        "bank_checks": checks,
        "coverage": coverage,
        "failures": failures,
        "failed_bank_checks": failed_checks,
        "ok": not failures and not failed_checks,
    }


CORRECTION_FORMULA = (
    "1+k = 0.93 + 0.487118*c14*(B/L)^1.06806*(T/L)^0.46106*(L/LR)^0.121563"
    "*(L^3/nabla)^0.36486*(1-Cp)^(-0.604247); LR = L*(1-Cp+0.06*Cp*LCB/(4*Cp-1)); "
    "c14 = 1+0.011*Cstern"
)


def build_corrections(result: Dict[str, Any]) -> Dict[str, Any]:
    """Emit corrected reference values for every question the audit rejected.

    The bank is append-only / tamper-evident by design (``commit_hash``), so a
    bad reference must never be edited in place: it is superseded by a new
    version.  This function produces the version-2 question set, with the
    literature formula written into the provenance so the next audit can
    reproduce it without consulting the candidate implementation.
    """
    corrections = []
    for q in result["failures"]:
        spec = dict(q.get("original_spec", {})) or dict(q.get("spec", {}))
        corrections.append(
            {
                "supersedes": q["id"],
                "title": q["title"],
                "domain": q["domain"],
                "difficulty": q["difficulty"],
                "specification": None,  # filled below
                "verdict_of_old": q["verdict"],
                "old_expected": q.get("stored"),
                "old_deviation_vs_literature": q.get("max_deviation"),
                "new_expected": q.get("independent"),
                "tolerances": q.get("tolerance"),
                "provenance": {
                    "source": "Holtrop & Mennen (1982), Eq. (12) — form factor",
                    "reference": "Holtrop, J. & Mennen, G.G.J., 'An Approximate Power Prediction "
                    "Method', International Shipbuilding Progress 29 (1982) 166-170",
                    "formula": CORRECTION_FORMULA,
                    "type": "empirical",
                    "omitted_inputs": "Cp = Cb/Cm with Cm=0.98; LCB=0%L; Cstern=0",
                    "sensitivity_band": q.get("sensitivity_band"),
                },
            }
        )
    # attach the original specification verbatim so re-seeding needs no join
    by_id = {q["id"]: q for q in result["questions"]}
    for c in corrections:
        c["specification"] = by_id[c["supersedes"]].get("specification")
    return {
        "generated_by": "tools/verifier/audit_question_bank.py",
        "reason": "reference values disagree with the cited primary source; supersede, never edit in place",
        "n_corrections": len(corrections),
        "corrections": corrections,
    }


def render_markdown(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Question-bank audit — {os.path.basename(result['db_path'])}")
    lines.append("")
    lines.append(f"questions: {result['coverage']['n_questions']}  |  verdicts: {result['verdict_counts']}")
    lines.append("")
    lines.append("| # | question | formula | stored | independent | deviation | verdict |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, q in enumerate(result["questions"], 1):
        stored = ", ".join(f"{k}={v:.6g}" for k, v in q.get("stored", {}).items())
        indep = ", ".join(f"{k}={v:.6g}" for k, v in q.get("independent", {}).items())
        dev = q.get("max_deviation")
        dev_s = "n/a" if dev is None else f"{dev * 100:.3g}%"
        lines.append(f"| {i} | {q['title']} | `{q['formula']}` | {stored} | {indep} | {dev_s} | **{q.get('verdict')}** |")
    lines.append("")
    lines.append("## Bank-level checks")
    lines.append("")
    for name, chk in result["bank_checks"].items():
        if name == "commit_hash_integrity":
            lines.append(f"- **{name}**: {'OK' if chk['ok'] else 'FAIL'} — {chk['detail']}")
        else:
            lines.append(f"- **{name}**: {'OK' if chk.get('ok') else 'FAIL'} — {chk.get('detail', '')}")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Independently audit the verifier question bank's reference values")
    ap.add_argument("--db", default=DEFAULT_DB, help="path to questions.db (opened read-only)")
    ap.add_argument("--json", dest="json_out", default=None, help="write full result as JSON")
    ap.add_argument("--quiet", action="store_true", help="suppress markdown summary")
    ap.add_argument(
        "--emit-corrections",
        default=None,
        help="write a version-2 corrected question set (JSON) for every rejected reference",
    )
    args = ap.parse_args(argv)

    result = run_audit(args.db)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False, default=str)
    if args.emit_corrections:
        corr = build_corrections(result)
        with open(args.emit_corrections, "w", encoding="utf-8") as fh:
            json.dump(corr, fh, indent=2, ensure_ascii=False, default=str)
        if not args.quiet:
            print(f"corrections written: {args.emit_corrections} ({corr['n_corrections']} questions)")
    if not args.quiet:
        print(render_markdown(result))
        print(f"verdict counts: {result['verdict_counts']}")
        print(f"failed bank checks: {result['failed_bank_checks'] or 'none'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())