"""Materialise the full acceptance evidence bundle for one audit run.

    python tools/capability_ledger_digest/materialize.py \
        --spec tests/capability_ledger_digest/fixtures/spec_known.json \
        --out artifacts/capability_ledger_digest [--new-tests N] [--gate-matrix F]

Writes into ``--out``:

    ledger.sqlite          the SQL ledger produced by the audit
    audit_report.json      the digest-bound audit report
    ledger_canonical.txt   the canonical ledger dump (sha256 source)
    audit_report.md        human-readable table
    digest_summary.json    the five digests of the run
    verification.json      independent re-derivation of every digest
    acceptance.json        criterion-by-criterion machine verdict (a)-(e)
    fig1_gate_matrix.png   capability x evidence-item gate matrix
    fig2_audit_profile.png level / rule / transition profile
    fig3_idempotency.png   ledger digest stability over three runs

Nothing here is domain specific: the spec document supplies every capability id,
number and threshold.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from capability_ledger_digest.audit import (  # type: ignore
        report_markdown,
        run_audit,
        verify_audit,
        write_artifacts,
    )
    from capability_ledger_digest.classifier import classify  # type: ignore
    from capability_ledger_digest.spec import AuditSpec, EvidenceRecord, Thresholds  # type: ignore
    from capability_ledger_digest import figures  # type: ignore
else:
    from .audit import report_markdown, run_audit, verify_audit, write_artifacts
    from .classifier import classify
    from .spec import AuditSpec, EvidenceRecord, Thresholds
    from . import figures

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_TARGETS = ["tools/capability_ledger_digest", "tests/capability_ledger_digest"]


def _gate_matrix_consistency(matrix_path: Path) -> dict:
    """Re-run the declared gate matrix (expected outcomes read from the file)."""
    data = json.loads(matrix_path.read_text(encoding="utf-8"))
    thr = Thresholds(**data["thresholds"])
    rows = []
    for combo in data["combinations"]:
        res = classify(
            EvidenceRecord(
                capability_id=f"cap.{combo['combo']}",
                test_pass_rate=combo["test_pass_rate"],
                benchmark_error_pct=combo["benchmark_error_pct"],
                review_verdict=combo["review_verdict"],
            ),
            thr,
        )
        agree = (
            res.chain.chain_complete == combo["expected_chain_complete"]
            and res.level == combo["expected_level"]
        )
        rows.append(
            {
                "combo": combo["combo"],
                "chain_complete": res.chain.chain_complete,
                "expected_chain_complete": combo["expected_chain_complete"],
                "level": res.level,
                "expected_level": combo["expected_level"],
                "agree": agree,
            }
        )
    return {
        "combinations": len(rows),
        "agree": sum(1 for r in rows if r["agree"]),
        "consistency": (sum(1 for r in rows if r["agree"]) / len(rows)) if rows else None,
        "rows": rows,
    }


def _domain_scan() -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    import evidence_domain_scan as scan  # type: ignore

    result = scan.scan_paths(DOMAIN_TARGETS, root=ROOT)
    return {
        "targets": DOMAIN_TARGETS,
        "scanned_files": result["scanned_files"],
        "terms": result["terms"],
        "hits": len(result["hits"]),
        "hit_detail": result["hits"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--new-tests", type=int, default=None)
    ap.add_argument(
        "--gate-matrix",
        default=str(ROOT / "tests" / "capability_ledger_digest" / "fixtures" / "gate_matrix.json"),
    )
    ap.add_argument("--idempotency-runs", type=int, default=3)
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    spec = AuditSpec.load(args.spec)

    # the spec travels with the bundle so the artefacts stay self-describing
    (out / "spec_used.json").write_text(
        json.dumps(spec.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    db = out / "ledger.sqlite"
    report = run_audit(spec, db)
    write_artifacts(report, out)
    (out / "audit_report.md").write_text(report_markdown(report), encoding="utf-8")

    verdict = verify_audit(report, spec, db)
    (out / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    runs = []
    for i in range(1, args.idempotency_runs + 1):
        rep = run_audit(spec, out / f"_idem_{i}.sqlite")
        runs.append(
            {
                "run": i,
                "ledger_sha256": rep["ledger"]["sha256"],
                "report_sha256": rep["report_sha256"],
            }
        )
        (out / f"_idem_{i}.sqlite").unlink(missing_ok=True)
    idem_reports = {r["report_sha256"] for r in runs}
    idem_ledgers = {r["ledger_sha256"] for r in runs}

    figures.fig_gate_matrix(report, out / "fig1_gate_matrix.png")
    figures.fig_audit_profile(report, out / "fig2_audit_profile.png")
    figures.fig_idempotency(runs, out / "fig3_idempotency.png")

    matrix = _gate_matrix_consistency(Path(args.gate_matrix))
    domain = _domain_scan()

    acceptance = {
        "spec": str(args.spec),
        "spec_sha256": spec.sha256(),
        "thresholds": spec.thresholds.to_dict(),
        "thresholds_sha256": spec.thresholds.sha256(),
        "criteria": {
            "a_classification_accuracy": {
                "value": report["known_label_score"]["accuracy"],
                "judged": report["known_label_score"]["judged"],
                "matched": report["known_label_score"]["matched"],
                "per_level": report["known_label_score"]["per_level"],
                "mismatches": report["known_label_score"]["mismatches"],
                "pass": report["known_label_score"]["accuracy"] == 1.0
                and report["known_label_score"]["judged"] >= 10,
            },
            "b_chain_adjudication_consistency": {
                "value": matrix["consistency"],
                "combinations": matrix["combinations"],
                "agree": matrix["agree"],
                "rows": matrix["rows"],
                "pass": matrix["consistency"] == 1.0,
            },
            "c_ledger_report_bit_identity": {
                "ledger_sha256": report["ledger"]["sha256"],
                "ledger_sha256_from_db": verdict["digests"]["ledger_sha256_db"],
                "report_sha256": report["report_sha256"],
                "checks": verdict["checks"],
                "pass": verdict["checks"]["ledger_canonical_identical"]
                and verdict["checks"]["ledger_sha256_identical"],
            },
            "d_zero_domain_semantics": {
                "hits": domain["hits"],
                "scanned_files": domain["scanned_files"],
                "terms": domain["terms"],
                "hit_detail": domain["hit_detail"],
                "pass": domain["hits"] == 0 and domain["scanned_files"] > 0,
            },
            "e_idempotency": {
                "runs": runs,
                "distinct_ledger_digests": len(idem_ledgers),
                "distinct_report_digests": len(idem_reports),
                "pass": len(idem_ledgers) == 1 and len(idem_reports) == 1,
            },
            "f_new_test_cases": {
                "collected": args.new_tests,
                "target_min": 15,
                "pass": (args.new_tests is not None and args.new_tests >= 15),
            },
        },
    }
    acceptance["all_pass"] = all(c["pass"] for c in acceptance["criteria"].values())
    (out / "acceptance.json").write_text(
        json.dumps(acceptance, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "out": str(out),
                "all_pass": acceptance["all_pass"],
                "criteria": {
                    k: {"pass": v["pass"], "value": v.get("value", v.get("hits", ""))}
                    for k, v in acceptance["criteria"].items()
                },
                "ledger_sha256": report["ledger"]["sha256"],
                "report_sha256": report["report_sha256"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if acceptance["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())