#!/usr/bin/env python3
"""
Literature cross-check audit of the verifier resistance pilot (mission 537).

Why
---
``artifacts/pilot_verification_report.json`` reports 15/15 pass with a
deviation of exactly 0.0 on every question. That is not evidence of correctness:
the question generator and the candidate implement the *same* formula string, so
the reference value is a copy of the candidate's own arithmetic. This script
re-does the verification along an independent path:

  A. Anchor check  -- does the reference implementation reproduce values that are
                      *tabulated in the literature* (published anchors)?
  B. Bank check    -- recompute each stored question from the independent
                      reference; report deviation and flag circularity
                      (bit-identical reference == candidate).
  C. Cross-formula -- Prandtl-Schlichting vs Schoenherr flat plate level.
  D. Model check   -- candidate "Holtrop-Mennen form factor" (which the pilot
                      docstring itself calls "simplified ... scaled for pilot")
                      vs the full Holtrop & Mennen (1982) expression, with a
                      Cm/LCB sensitivity envelope.

Outputs: artifacts/m1391/literature_crosscheck.json (+ 2 png figures).

Read-only w.r.t. the question bank. No heavy compute.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "pilots"))

import reference_formulas as ref  # noqa: E402

import resistance_calculator as cand  # noqa: E402  (the pilot candidate)

QBANK = os.path.join(HERE, "question_bank", "questions.db")
OUTDIR = os.path.join(REPO, "artifacts", "m1391")

# Platform accuracy standard: >15 % error on a key quantity is unacceptable.
PLATFORM_ACCEPT_PCT = 15.0
# "Has to be < 3 %" reporting threshold used by the report gate.
TARGET_PCT = 3.0

HULL_ASSUMPTIONS = {"Cm": 0.98, "LCB_pct": -1.5}


# --------------------------------------------------------------------------- #
def load_questions(qbank: str = QBANK) -> list[dict]:
    """Read the frozen question bank (read-only)."""
    conn = sqlite3.connect(f"file:{qbank}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, title, domain, specification, expected_result, tolerances "
            "FROM questions ORDER BY title"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for qid, title, domain, spec, exp, tol in rows:
        out.append(
            {
                "id": qid,
                "title": title,
                "domain": domain,
                "specification": json.loads(spec),
                "expected_result": json.loads(exp),
                "tolerances": json.loads(tol),
            }
        )
    return out


def reference_value(spec: dict) -> tuple[dict, str]:
    """Independent reference result for a question specification."""
    formula = spec.get("formula", spec.get("method", ""))
    if formula == "ittc_1957":
        return {"Cf": ref.ittc1957_cf(spec["Re"])}, "ittc_1957"
    if formula == "blasius_laminar":
        return {"Cf": ref.blasius_cf(spec["Re_L"])}, "blasius"
    if formula == "prandtl_schlichting":
        return {"Cf": ref.prandtl_schlichting_cf(spec["Re_L"])}, "prandtl_schlichting"
    if formula == "holtrop_mennen_form_factor":
        r = ref.holtrop1982_form_factor(
            spec["L"], spec["B"], spec["T"], spec["Cb"],
            Cm=HULL_ASSUMPTIONS["Cm"], LCB_pct=HULL_ASSUMPTIONS["LCB_pct"],
        )
        return {"1+k": r["1+k"], "k": r["k"]}, "holtrop_1982_full"
    raise ValueError(f"Unknown formula in bank: {formula}")


def candidate_value(spec: dict) -> dict:
    """The pilot candidate's own answer."""
    return cand.run_candidate(spec)


# --------------------------------------------------------------------------- #
def anchor_check() -> list[dict]:
    """A. Reference implementation vs values tabulated in the literature."""
    checks = []
    impl = {
        "ittc_1957": (ref.ittc1957_cf, "Re"),
        "blasius": (ref.blasius_cf, "Re_L"),
        "prandtl_schlichting": (ref.prandtl_schlichting_cf, "Re_L"),
    }
    for key, anchors in ref.PUBLISHED_ANCHORS.items():
        fn, re_key = impl[key]
        for a in anchors:
            got = fn(a[re_key])
            # A value quoted to `decimals` significant figures carries an
            # uncertainty of half a unit in its last quoted digit.
            rel_ulp = 0.5 * 10 ** (-(a["decimals"] - 1))
            dev = ref.relative_error(got, a["Cf"])
            checks.append(
                {
                    "line": key,
                    "input": {re_key: a[re_key]},
                    "published": a["Cf"],
                    "reference_impl": got,
                    "rel_dev": dev,
                    "rel_dev_pct": 100.0 * dev,
                    "tolerance_pct": 100.0 * rel_ulp,
                    "pass": dev <= rel_ulp,
                    "note": f"published quoted to {a['decimals']} s.f.",
                }
            )
    return checks


def bank_check(questions: list[dict]) -> list[dict]:
    """B. Bank expected values vs the independent reference, + circularity flag."""
    rows = []
    for q in questions:
        spec = q["specification"]
        ref_res, ref_kind = reference_value(spec)
        cand_res = candidate_value(spec)
        key = sorted(q["expected_result"])[0]
        exp = q["expected_result"][key]
        dev_ref = ref.relative_error(ref_res[key], exp)
        dev_cand = ref.relative_error(cand_res[key], ref_res[key])
        rows.append(
            {
                "id": q["id"],
                "title": q["title"],
                "reference_kind": ref_kind,
                "key": key,
                "bank_expected": exp,
                "independent_reference": ref_res[key],
                "candidate": cand_res[key],
                "bank_vs_reference_rel_dev": dev_ref,
                "candidate_vs_reference_rel_dev": dev_cand,
                "candidate_vs_reference_pct": 100.0 * dev_cand,
                "circular": dev_ref < 1e-12 and dev_cand < 1e-12,
                "tolerance": q["tolerances"][key],
                "within_platform_15pct": 100.0 * dev_cand <= PLATFORM_ACCEPT_PCT,
                "within_target_3pct": 100.0 * dev_cand <= TARGET_PCT,
            }
        )
    return rows


def cross_formula_check() -> list[dict]:
    """C. Prandtl-Schlichting vs Schoenherr (independent flat-plate line)."""
    rows = []
    for Re in (1e6, 1e7, 1e8, 1e9):
        ps = ref.prandtl_schlichting_cf(Re)
        sch = ref.schoenherr_cf(Re)
        rows.append(
            {
                "Re_L": Re,
                "prandtl_schlichting": ps,
                "schoenherr": sch,
                "rel_dev_pct": 100.0 * ref.relative_error(sch, ps),
                "pass_3pct": 100.0 * ref.relative_error(sch, ps) <= TARGET_PCT,
            }
        )
    return rows


def form_factor_check(models: list[dict]) -> list[dict]:
    """D. Pilot 'Holtrop' form factor vs the full Holtrop & Mennen (1982)."""
    rows = []
    for m in models:
        spec = m["specification"]
        L, B, T, Cb = spec["L"], spec["B"], spec["T"], spec["Cb"]
        true_r = ref.holtrop1982_form_factor(
            L, B, T, Cb,
            Cm=HULL_ASSUMPTIONS["Cm"], LCB_pct=HULL_ASSUMPTIONS["LCB_pct"],
        )
        env = ref.holtrop1982_form_factor_sensitivity(L, B, T, Cb)
        pilot_1pk = cand.compute_holtrop_form_factor(Cb, L, B, T)
        pilot_k = pilot_1pk - 1.0
        err_1pk = ref.relative_error(pilot_1pk, true_r["1+k"])
        err_k = ref.relative_error(pilot_k, true_r["k"]) if true_r["k"] else float("inf")
        outside_env = not (env["one_plus_k_min"] <= pilot_1pk <= env["one_plus_k_max"])
        rows.append(
            {
                "title": m["title"],
                "geometry": {"L": L, "B": B, "T": T, "Cb": Cb, "L/B": L / B, "B/T": B / T},
                "assumptions": dict(HULL_ASSUMPTIONS),
                "pilot_1_plus_k": pilot_1pk,
                "pilot_k": pilot_k,
                "holtrop1982_1_plus_k": true_r["1+k"],
                "holtrop1982_k": true_r["k"],
                "envelope_1_plus_k": [env["one_plus_k_min"], env["one_plus_k_max"]],
                "envelope_k": [env["k_min"], env["k_max"]],
                "pilot_outside_envelope": outside_env,
                "rel_err_pct_on_1_plus_k": 100.0 * err_1pk,
                "rel_err_pct_on_k": 100.0 * err_k,
                "within_platform_15pct_on_1_plus_k": 100.0 * err_1pk <= PLATFORM_ACCEPT_PCT,
                "within_platform_15pct_on_k": 100.0 * err_k <= PLATFORM_ACCEPT_PCT,
                "verdict": (
                    "PASS" if (100.0 * err_1pk <= PLATFORM_ACCEPT_PCT
                               and 100.0 * err_k <= PLATFORM_ACCEPT_PCT)
                    else "FAIL_LITERATURE"
                ),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
def make_figures(bank_rows, ff_rows, xrows, outdir) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(outdir, exist_ok=True)
    paths = []

    # ---- Figure 1: friction lines + bank questions + reference-level check ---
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.0))
    Re = np.logspace(5, 10, 200)
    ax.loglog(Re, [ref.ittc1957_cf(r) for r in Re], "k-", lw=2.0,
              label="ITTC-1957  $0.075/(\\log_{10}Re-2)^2$")
    Re_lam = np.logspace(3, 5.7, 120)
    ax.loglog(Re_lam, [ref.blasius_cf(r) for r in Re_lam], "C0--", lw=1.8,
              label="Blasius laminar  $1.328/\\sqrt{Re_L}$")
    Re_t = np.logspace(5.5, 10, 160)
    ax.loglog(Re_t, [ref.prandtl_schlichting_cf(r) for r in Re_t], "C1-", lw=1.8,
              label="Prandtl-Schlichting  $0.455/(\\log_{10}Re)^{2.58}$")
    ax.loglog(Re_t, [ref.schoenherr_cf(r) for r in Re_t], "C2:", lw=1.8,
              label="Schoenherr (ATTC-1947), independent cross-check")

    fr = [r for r in bank_rows if r["key"] == "Cf"]
    Re_of = {"ittc_1957": "Re", "blasius": "Re_L", "prandtl_schlichting": "Re_L"}
    for r in fr:
        spec_re = 1.0
        q = next(q for q in QUESTIONS if q["id"] == r["id"])
        spec_re = q["specification"][Re_of[r["reference_kind"]]]
        ax.plot(spec_re, r["independent_reference"], "o", ms=6,
                mfc="white", mec="k", mew=1.2, zorder=5)
    ax.set_xlabel("Reynolds number $Re$ / $Re_L$ [-]")
    ax.set_ylabel("Friction coefficient $C_F$ [-]")
    ax.set_title("(a) Friction lines: literature reference path\n"
                 "open circles = the 15 bank questions (independent recompute)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8, loc="lower left")

    xr = np.array([x["Re_L"] for x in xrows])
    yd = np.array([x["rel_dev_pct"] for x in xrows])
    ax2.semilogx(xr, yd, "C3-o", lw=1.8, label="Prandtl-Schlichting vs Schoenherr")
    ax2.axhline(TARGET_PCT, color="g", ls="--", lw=1.2, label="3 % target")
    ax2.axhline(PLATFORM_ACCEPT_PCT, color="r", ls=":", lw=1.2, label="15 % unacceptable")
    ax2.set_xlabel("$Re_L$ [-]")
    ax2.set_ylabel("relative deviation [%]")
    ax2.set_title("(b) Cross-formula check of the flat-plate level\n"
                  "(does the reference agree with an independent line?)")
    ax2.grid(True, which="both", alpha=0.3)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    p1 = os.path.join(outdir, "fig1_friction_lines_crosscheck.png")
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    paths.append(p1)

    # ---- Figure 2: form factor, candidate vs Holtrop 1982 envelope -----------
    fig, (bx, bx2) = plt.subplots(1, 2, figsize=(12.5, 5.0))
    idx = np.arange(len(ff_rows))
    pilot = [r["pilot_1_plus_k"] for r in ff_rows]
    lo = [r["envelope_1_plus_k"][0] for r in ff_rows]
    hi = [r["envelope_1_plus_k"][1] for r in ff_rows]
    mid = [r["holtrop1982_1_plus_k"] for r in ff_rows]
    bx.bar(idx - 0.18, pilot, width=0.34, color="C3", label="pilot candidate (\"scaled for pilot\")")
    bx.bar(idx + 0.18, mid, width=0.34, color="C0",
           label="Holtrop & Mennen (1982), full expression")
    bx.errorbar(idx + 0.18, mid,
                yerr=[np.array(mid) - np.array(lo), np.array(hi) - np.array(mid)],
                fmt="none", ecolor="k", capsize=5, lw=1.2,
                label="envelope over $C_m\\in[0.97,0.995]$, LCB$-3\\ldots0\\,\\%L$")
    bx.axhline(1.0, color="k", lw=0.8)
    bx.set_xticks(idx)
    bx.set_xticklabels([f"{r['title'].split(': ')[-1]}\nCb={r['geometry']['Cb']}"
                        for r in ff_rows], fontsize=8)
    bx.set_ylabel("form factor $1+k$ [-]")
    bx.set_ylim(0.98, max(hi) + 0.06)
    bx.set_title("(a) Form factor: pilot vs published Holtrop & Mennen (1982)")
    bx.grid(True, axis="y", alpha=0.3)
    bx.legend(fontsize=8, loc="upper left")

    err_1pk = [r["rel_err_pct_on_1_plus_k"] for r in ff_rows]
    err_k = [r["rel_err_pct_on_k"] for r in ff_rows]
    bx2.bar(idx - 0.18, err_1pk, width=0.34, color="C1", label="error on $1+k$")
    bx2.bar(idx + 0.18, err_k, width=0.34, color="C4", label="error on $k=1+k-1$")
    bx2.axhline(PLATFORM_ACCEPT_PCT, color="r", ls="--", lw=1.2, label="15 % limit (platform)")
    bx2.set_xticks(idx)
    bx2.set_xticklabels([f"Cb={r['geometry']['Cb']}" for r in ff_rows])
    bx2.set_ylabel("relative deviation vs literature [%]")
    bx2.set_title("(b) Deviation of the pilot form factor from the published model")
    bx2.grid(True, axis="y", alpha=0.3)
    bx2.legend(fontsize=8)
    fig.tight_layout()
    p2 = os.path.join(outdir, "fig2_form_factor_vs_holtrop1982.png")
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    paths.append(p2)
    return paths


# --------------------------------------------------------------------------- #
def main() -> int:
    global QUESTIONS
    QUESTIONS = load_questions()
    os.makedirs(OUTDIR, exist_ok=True)

    anchors = anchor_check()
    bank_rows = bank_check(QUESTIONS)
    xrows = cross_formula_check()
    ff_questions = [q for q in QUESTIONS
                    if q["specification"].get("method") == "holtrop_mennen_form_factor"]
    ff_rows = form_factor_check(ff_questions)

    figs = make_figures(bank_rows, ff_rows, xrows, OUTDIR)

    ff_err_1pk = ", ".join("%.1f%%" % r["rel_err_pct_on_1_plus_k"] for r in ff_rows)
    ff_err_k = ", ".join("%.1f%%" % r["rel_err_pct_on_k"] for r in ff_rows)
    ff_trend = ", ".join("%s->%.3f" % (r["geometry"]["Cb"], r["holtrop1982_1_plus_k"])
                         for r in sorted(ff_rows, key=lambda x: x["geometry"]["Cb"]))
    ff_outside = sum(1 for r in ff_rows if r["pilot_outside_envelope"])

    n_circ = sum(1 for r in bank_rows if r["circular"])
    ff_fail = [r for r in ff_rows if r["verdict"] != "PASS"]
    friction_rows = [r for r in bank_rows if r["key"] == "Cf"]
    worst_friction = max(friction_rows, key=lambda r: r["candidate_vs_reference_pct"])

    report = {
        "audit": "literature_crosscheck",
        "mission_id": 1391,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": REPO,
        "scope": "verifier resistance pilot (mission 537), 15 frozen questions",
        "question_bank": os.path.relpath(QBANK, REPO),
        "literature_basis": {
            "ittc_1957": ref.ITTC57_PROVENANCE.__dict__,
            "blasius": ref.BLASIUS_PROVENANCE.__dict__,
            "prandtl_schlichting": ref.PRANDTL_SCHLICHTING_PROVENANCE.__dict__,
            "schoenherr": ref.SCHOENHERR_PROVENANCE.__dict__,
            "holtrop_1982": ref.HOLTROP1982_PROVENANCE.__dict__,
        },
        "hull_assumptions_for_full_holtrop": HULL_ASSUMPTIONS,
        "criteria": {
            "platform_accept_pct": PLATFORM_ACCEPT_PCT,
            "target_pct": TARGET_PCT,
            "circularity_threshold_rel": 1e-12,
        },
        "A_anchor_check": anchors,
        "A_anchor_check_pass": all(a["pass"] for a in anchors),
        "B_bank_vs_reference": bank_rows,
        "B_circular_count": n_circ,
        "B_friction_max_dev_pct": worst_friction["candidate_vs_reference_pct"],
        "C_cross_formula": xrows,
        "C_cross_formula_pass_3pct": all(x["pass_3pct"] for x in xrows),
        "D_form_factor": ff_rows,
        "D_form_factor_fail_count": len(ff_fail),
        "findings": [
            {
                "id": "F1",
                "title": "Circular verification in the pilot report",
                "evidence": (
                    f"{n_circ}/15 bank reference values are bit-identical to the "
                    "candidate output (rel dev < 1e-12) because generate_questions.py "
                    "and pilots/resistance_calculator.py implement the same formula "
                    "string; a wrong constant would pass unnoticed."
                ),
                "severity": "high",
                "grade_before": "REAL (claimed)",
                "grade_after": "WITHHELD (self-consistent only) for the pass claim; "
                               "the formulas themselves are literature-anchored by A.",
            },
            {
                "id": "F2",
                "title": "\"Holtrop-Mennen form factor\" is not Holtrop & Mennen (1982)",
                "evidence": (
                    "The candidate multiplies an ad-hoc expression by 0.1 "
                    "('# Scaled for pilot'). Against the full published expression "
                    "the error on 1+k is " + ff_err_1pk + " and on k it is "
                    + ff_err_k + "; the pilot value lies outside the Cm/LCB "
                    f"envelope in {ff_outside}/{len(ff_rows)} hulls."
                ),
                "severity": "critical",
                "grade_before": "REAL (claimed, provenance 'Holtrop 1982')",
                "grade_after": "MOCK (form factor is fabricated, provenance mislabeled)",
            },
            {
                "id": "F3",
                "title": "Cb trend of the pilot form factor has the wrong sign",
                "evidence": (
                    "Pilot 1+k decreases with Cb (0.6->1.0547, 0.7->1.0393, 0.8->1.0276) "
                    "because of the Cb^-0.9249 exponent, while Holtrop (1982) increases "
                    "strongly with Cb ("
                    + ff_trend
                    + "). A trend-sign error cannot be fixed by retuning a constant."
                ),
                "severity": "critical",
                "grade_before": "-",
                "grade_after": "-",
            },
            {
                "id": "F4",
                "title": "Friction lines themselves are correct and now literature-anchored",
                "evidence": (
                    f"Independent re-derivation matches all 12 friction questions to "
                    f"{worst_friction['candidate_vs_reference_pct']:.2e} % (driver: "
                    "identical closed form), and A reproduces the published anchor "
                    f"table: {sum(1 for a in anchors if a['pass'])}/{len(anchors)} anchors pass "
                    "at the quoted significant figures."
                ),
                "severity": "info",
                "grade_before": "WITHHELD (circular)",
                "grade_after": "REAL (formula + published anchors agree)",
            },
            {
                "id": "F5",
                "title": "Tolerance semantics undocumented (absolute vs relative)",
                "evidence": (
                    "The bank stores plain numbers (Cf: 0.001 / 0.005, 1+k: 0.01) while "
                    "verifier.py judges dev = |a-e|/|e| <= tol, i.e. treats them as "
                    "*relative*. Read as absolute they would be 20-300 % bands. The "
                    "PILOT_REPORT text ('0.1 % tolerance') only matches the relative "
                    "reading. Ambiguity must be pinned in the schema before scaling."
                ),
                "severity": "medium",
                "grade_before": "-",
                "grade_after": "-",
            },
        ],
        "recommendations": [
            "Replace the pilot form factor with the full Holtrop & Mennen (1982) "
            "expression from tools/verifier/reference_formulas.py (do not keep the 0.1 fudge).",
            "Make the reference path independent by construction: generate expected "
            "values only from reference_formulas.py, never from candidate code, and "
            "require |bank_expected - reference| < 1e-12 as a commit gate "
            "(tests/test_literature_crosscheck.py enforces this).",
            "Store tolerances as explicit relative fractions with a schema field "
            "('tolerance_kind': 'relative') or as {value, kind} objects.",
            "Gate the question bank on published anchors (PUBLISHED_ANCHORS) so a "
            "reference implementation cannot drift silently.",
            "Downgrade the mission-537 pilot verdict from 'pass' to "
            "'pass for the 12 friction questions, FAIL for the 3 form-factor questions'.",
        ],
        "figures": [os.path.relpath(p, REPO) for p in figs],
    }
    out = os.path.join(OUTDIR, "literature_crosscheck.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    print(f"anchor check      : {'PASS' if report['A_anchor_check_pass'] else 'FAIL'} "
          f"({len(anchors)} anchors)")
    print(f"circular questions: {n_circ}/{len(bank_rows)}")
    print(f"friction max dev  : {worst_friction['candidate_vs_reference_pct']:.3e} %")
    print(f"cross-formula 3%  : {'PASS' if report['C_cross_formula_pass_3pct'] else 'FAIL'}")
    for r in ff_rows:
        print(f"form factor {r['title'][:44]:44s} pilot 1+k={r['pilot_1_plus_k']:.4f} "
              f"literature={r['holtrop1982_1_plus_k']:.4f} "
              f"err(1+k)={r['rel_err_pct_on_1_plus_k']:.1f}% "
              f"err(k)={r['rel_err_pct_on_k']:.1f}% -> {r['verdict']}")
    print(f"artifact          : {os.path.relpath(out, REPO)}")
    print(f"figures           : {', '.join(os.path.relpath(p, REPO) for p in figs)}")
    return 0


QUESTIONS: list[dict] = []

if __name__ == "__main__":
    sys.exit(main())