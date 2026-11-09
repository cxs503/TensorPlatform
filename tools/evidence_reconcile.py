#!/usr/bin/env python3
"""Reconcile author self-reported evidence grades against machine-derived grades.

Two reconciliations are produced:

1. ``reconciliation_vs_self_report.json`` — the platform's existing self-reported
   capability snapshot (``tests/fixtures/capability_ledger/capabilities_snapshot.json``)
   against the evidence contracts available for those capability ids. Producing
   this file is itself the finding: a grade that no contract backs is reported as
   ``UNVERIFIED`` instead of being taken at face value.
2. ``reconciliation_synthetic.json`` — the 30 synthetic contracts against a
   self-report built from the human labels with four deliberate deviations
   (3 overclaims, 1 underclaim) so the verdict logic is exercised in both
   directions.

Verdict vocabulary
------------------
``CONSISTENT``    declared == derived, or declared NOT_SUPPORTED with no contract
``OVERCLAIM``     declared stronger than derived (rank(derived) < rank(declared))
``UNDERCLAIM``    declared weaker than derived
``UNVERIFIED``    a grade is declared but no evidence contract exists for it

Ranking is :data:`evidence_grade.GRADE_RANK`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import evidence_grade as EG  # sibling module (tools/)

RECON_VERSION = "evidence-reconciliation/1.0"
METRICS_VERSION = "evidence-grade-metrics/1.0"
ROOT = Path(__file__).resolve().parents[1]


# ── self-report normalisation ───────────────────────────────────────────────
def normalize_self_report(payload) -> dict[str, str]:
    """Accept the shapes seen in the wild; return ``{capability_id: grade}``."""
    caps = payload.get("capabilities", payload) if isinstance(payload, dict) else payload
    if isinstance(caps, dict):
        out = {}
        for key, val in caps.items():
            if isinstance(val, dict):
                grade = val.get("claimed_level") or val.get("grade") or val.get("evidence")
            else:
                grade = val
            if isinstance(grade, str):
                out[str(key)] = grade.strip().upper()
        return out
    if isinstance(caps, list):
        out = {}
        for row in caps:
            if not isinstance(row, dict):
                continue
            cid = row.get("id") or row.get("capability_id") or row.get("name")
            grade = row.get("claimed_level") or row.get("grade") or row.get("evidence")
            if cid and isinstance(grade, str):
                out[str(cid)] = grade.strip().upper()
        return out
    raise ValueError("unrecognised self-report shape (need capabilities dict/list)")


def _verdict(declared: str, derived: str) -> str:
    if declared == derived:
        return "CONSISTENT"
    if declared in ("NOT_SUPPORTED", "UNKNOWN"):
        return "CONSISTENT" if derived in ("UNKNOWN", "STUB", "NOT_SUPPORTED") else "UNDERCLAIM"
    rd, rr = EG.GRADE_RANK.get(derived, 0), EG.GRADE_RANK.get(declared, 0)
    if rd < rr:
        return "OVERCLAIM"
    if rd > rr:
        return "UNDERCLAIM"
    return "CONSISTENT"


# ── reconciliation core ─────────────────────────────────────────────────────
def reconcile(self_report: dict[str, str], derived: dict[str, dict],
              source: dict | None = None, include_undeclared: bool = True) -> dict:
    """Compare declared vs derived grades; return the inconsistency ledger.

    ``include_undeclared=False`` restricts the ledger to capability ids that the
    self-report actually claims, which is what a "reconcile against this snapshot"
    run wants (otherwise contracts for other ids show up as UNDECLARED noise).
    """
    rows = []
    tally: dict[str, int] = {}
    ids = set(self_report) | set(derived) if include_undeclared else set(self_report)
    for cid in sorted(ids):
        declared = self_report.get(cid, "NOT_DECLARED")
        entry = derived.get(cid)
        if entry is None:
            derived_grade = "UNKNOWN"
            contract = None
            reason = "no evidence contract for this capability id"
        else:
            derived_grade = entry["grade"]
            contract = entry.get("contract_path")
            reason = entry.get("reason_code", "")
        if declared == "NOT_DECLARED":
            verdict = "UNDECLARED" if derived_grade != "UNKNOWN" else "CONSISTENT"
        elif entry is None and declared not in ("NOT_SUPPORTED", "UNKNOWN"):
            verdict = "UNVERIFIED"
        else:
            verdict = _verdict(declared, derived_grade)
        tally[verdict] = tally.get(verdict, 0) + 1
        rows.append({
            "capability_id": cid,
            "self_reported": declared,
            "derived": derived_grade,
            "derived_reason": reason,
            "contract": contract,
            "verdict": verdict,
            "note": reason if verdict != "CONSISTENT" else "",
        })
    inconsistencies = [r for r in rows if r["verdict"] not in ("CONSISTENT",)]
    out = {
        "schema_version": RECON_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source or {},
        "summary": {
            "capabilities": len(rows),
            "consistent": tally.get("CONSISTENT", 0),
            "inconsistent": len(inconsistencies),
            "tally": tally,
        },
        "inconsistencies": inconsistencies,
        "rows": rows,
    }
    return out


def _derived_from_report(report: dict) -> dict[str, dict]:
    out = {}
    for res in report.get("results", []):
        cid = res.get("capability_id")
        if cid:
            out[cid] = res
    return out


def reconcile_file(self_report_path, fixtures_dir, include_undeclared: bool = False) -> dict:
    """Reconcile a self-report file against the contracts under *fixtures_dir*."""
    self_report_path = Path(self_report_path)
    report = EG.grade_dir(Path(fixtures_dir))
    declared = normalize_self_report(json.loads(self_report_path.read_text(encoding="utf-8")))
    return reconcile(declared, _derived_from_report(report),
                     include_undeclared=include_undeclared, source={
        "kind": "file",
        "path": str(self_report_path.relative_to(ROOT)) if self_report_path.is_relative_to(ROOT)
        else str(self_report_path),
        "sha256": EG.sha256_file(self_report_path),
        "contracts_dir": str(Path(fixtures_dir)),
    })


def reconcile_synthetic(report: dict, manifest: dict) -> dict:
    """Self-report from labels + 4 deliberate deviations; exercise both directions."""
    case_cap = manifest["case_capability"]          # capability_id -> case_id
    labels = {cap: manifest["cases"][case]["expected_grade"]
              for cap, case in case_cap.items()}
    ids = sorted(labels)
    declared = dict(labels)
    # 3 overclaims: a WITHHELD or STUB case declared REAL
    overclaim_ids = [cid for cid in ids if labels[cid] == "WITHHELD"][:2] + \
                    [cid for cid in ids if labels[cid] == "STUB"][:1]
    for cid in overclaim_ids:
        declared[cid] = "REAL"
    # 1 underclaim: a REAL case declared MOCK
    underclaim_ids = [cid for cid in ids if labels[cid] == "REAL"][:1]
    for cid in underclaim_ids:
        declared[cid] = "MOCK"

    out = reconcile(declared, _derived_from_report(report), source={
        "kind": "synthetic",
        "note": "declared = constructed labels + 3 overclaims + 1 underclaim",
        "overclaim_ids": overclaim_ids,
        "underclaim_ids": underclaim_ids,
        "manifest": "artifacts/evidence_grade/fixtures/manifest.json",
    })
    out["expectation"] = {"OVERCLAIM": len(overclaim_ids), "UNDERCLAIM": len(underclaim_ids)}
    out["expectation_met"] = (
        out["summary"]["tally"].get("OVERCLAIM", 0) == len(overclaim_ids)
        and out["summary"]["tally"].get("UNDERCLAIM", 0) == len(underclaim_ids)
    )
    return out


# ── metrics / judge criteria ────────────────────────────────────────────────
def cohen_kappa(pairs: list[tuple[str, str]]) -> dict:
    """Cohen's kappa for two raters over the same nominal categories."""
    n = len(pairs)
    if n == 0:
        return {"value": None, "n": 0}
    cats = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    row: dict[str, int] = {c: 0 for c in cats}
    col: dict[str, int] = {c: 0 for c in cats}
    agree = 0
    for a, b in pairs:
        row[a] += 1
        col[b] += 1
        if a == b:
            agree += 1
    po = agree / n
    pe = sum(row[c] * col[c] for c in cats) / (n * n)
    kappa = 1.0 if pe == 1.0 else (po - pe) / (1.0 - pe)
    return {"value": kappa, "po": po, "pe": pe, "n": n, "categories": cats}


def confusion_matrix(pairs: list[tuple[str, str]]) -> dict:
    mat: dict[str, dict[str, int]] = {}
    for expected, derived in pairs:
        mat.setdefault(expected, {})
        mat[expected][derived] = mat[expected].get(derived, 0) + 1
    return mat


def build_metrics(report: dict, manifest: dict, fixtures_dir,
                  reconciliation_path=None) -> dict:
    labels = {cid: meta["expected_grade"] for cid, meta in manifest["cases"].items()}
    reasons = {cid: meta["expected_reason_code"] for cid, meta in manifest["cases"].items()}
    by_case = {res["case_id"]: res for res in report.get("results", [])}

    pairs = []
    for case_id, expected in labels.items():
        derived = by_case.get(case_id, {}).get("grade", "MISSING")
        pairs.append((expected, derived))
    reproduced = sum(1 for a, b in pairs if a == b)

    trap_ids = [cid for cid in labels if reasons[cid] == "SAME_SOURCE_COPY"]
    traps_hit = [cid for cid in trap_ids
                 if by_case.get(cid, {}).get("grade") == "WITHHELD"
                 and by_case.get(cid, {}).get("reason_code") == "SAME_SOURCE_COPY"]

    kappa = cohen_kappa(pairs)

    # (e) domain vocabulary scan over the engine + schema + fixtures
    scan = {"hits": None, "scanned_files": None, "pass": False, "detail": ""}
    try:
        import evidence_domain_scan as SC  # noqa: PLC0415
        res = SC.scan_default_targets()
        scan = {"hits": len(res["hits"]), "scanned_files": res["scanned_files"],
                "pass": len(res["hits"]) == 0,
                "detail": "zero domain-vocabulary hits in engine/schema/fixtures"}
    except Exception as exc:  # pragma: no cover - scan must not break grading
        scan["detail"] = f"scan error: {type(exc).__name__}: {exc}"

    # (f) audit degrade path: no ledger source -> UNKNOWN, no exception
    degrade = {"value": None, "pass": False, "detail": ""}
    try:
        import sys  # noqa: PLC0415
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.audit.ledger_consumer_audit_tool import render  # noqa: PLC0415
        text = render({"capabilities": None, "reasons": ["NO_EVIDENCE_SOURCE"]})
        degrade = {"value": "UNKNOWN" if "UNKNOWN" in text else "OTHER",
                   "pass": "UNKNOWN" in text, "detail": text.splitlines()[0][:160]}
    except Exception as exc:
        degrade["detail"] = f"{type(exc).__name__}: {exc}"

    criteria = {
        "a_fixture_reproduction_rate": {
            "value": reproduced / len(pairs) if pairs else None,
            "numerator": reproduced, "denominator": len(pairs),
            "threshold": 1.0,
            "pass": bool(pairs) and reproduced == len(pairs),
            "detail": "derived grade equals the construction-time label",
        },
        "b_same_source_trap_detection": {
            "value": f"{len(traps_hit)}/{len(trap_ids)}",
            "numerator": len(traps_hit), "denominator": len(trap_ids),
            "threshold": f"{len(trap_ids)}/{len(trap_ids)}",
            "pass": len(traps_hit) == len(trap_ids) and len(trap_ids) > 0,
            "detail": "same-source copy graded WITHHELD with reason SAME_SOURCE_COPY",
        },
        "c_cohen_kappa_vs_labels": {
            "value": kappa["value"], "n": kappa["n"],
            "threshold": 0.9,
            "pass": kappa["value"] is not None and kappa["value"] >= 0.9,
            "detail": "derived grade vs construction-time human label",
        },
        "d_reconciliation_json": {
            "value": "artifacts/evidence_grade/reconciliation_vs_self_report.json",
            "pass": bool(reconciliation_path) and Path(reconciliation_path).is_file(),
            "detail": "declared-vs-derived inconsistency ledger emitted as JSON",
        },
        "e_domain_word_hits": {
            "value": scan["hits"], "scanned_files": scan["scanned_files"],
            "threshold": 0, "pass": scan["pass"], "detail": scan["detail"],
        },
        "f_audit_unknown_on_missing_source": {
            "value": degrade["value"], "pass": degrade["pass"],
            "detail": degrade["detail"],
        },
    }
    return {
        "schema_version": METRICS_VERSION,
        "fixtures": {"counts": manifest["counts"], "cases": len(labels),
                     "trap_cases": trap_ids},
        "tally": report.get("tally", {}),
        "criteria": criteria,
        "confusion": confusion_matrix(pairs),
        "kappa": kappa,
        "per_case": [{"case_id": cid,
                      "expected": labels[cid],
                      "derived": by_case.get(cid, {}).get("grade"),
                      "reason": by_case.get(cid, {}).get("reason_code"),
                      "expected_reason": reasons[cid]}
                     for cid in sorted(labels)],
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="reconcile self-reported vs derived grades")
    ap.add_argument("--self-report", default=str(
        ROOT / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"))
    ap.add_argument("--contracts", default=str(ROOT / "artifacts" / "evidence_grade" / "fixtures"))
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "evidence_grade"
                                         / "reconciliation_vs_self_report.json"))
    ap.add_argument("--include-undeclared", action="store_true",
                    help="also list derived-but-undeclared capability ids")
    args = ap.parse_args()
    result = reconcile_file(args.self_report, args.contracts,
                            include_undeclared=args.include_undeclared)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))