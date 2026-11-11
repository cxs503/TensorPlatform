"""One audit run: spec in, SQL ledger + digest-bound report out.

Flow::

    spec.json ──load──► AuditSpec ──classify──► [Classification]
                                                   │
                                                   ├─► SqlLedger.write  (SQL)
                                                   │        │
                                                   │        └─ canonical_dump() ──► sha256
                                                   └─► audit report (JSON)  ── carries the dump

The report embeds the canonical ledger dump verbatim *and* its digest, so the
verification step (:func:`verify_audit`) can prove, from files alone, that

  (i)   the database still projects to exactly the reported dump (bit identity),
  (ii)  the report body was not edited after the fact (report digest), and
  (iii) the levels follow from the injected spec and thresholds (re-derivation).

Because no clock value enters either artefact, running the audit twice on the
same spec produces byte-identical files (idempotency).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .classifier import (
    RULE_IDS,
    Classification,
    classify_all,
    known_label_score,
    level_counts,
)
from .spec import (
    LEVELS,
    SCHEMA_VERSION,
    AuditSpec,
    canonical_json,
    sha256_json,
    sha256_text,
)
from .sql_ledger import LEDGER_SCHEMA_VERSION, SqlLedger

REPORT_SCHEMA_VERSION = "1.0"


def _transition_counts(results: Sequence[Classification]) -> Dict[str, int]:
    counts = {"INIT": 0, "UPGRADE": 0, "HOLD": 0, "DOWNGRADE": 0}
    for res in results:
        counts[res.transition] = counts.get(res.transition, 0) + 1
    return counts


def _report_body(report: Mapping[str, Any]) -> Dict[str, Any]:
    body = dict(report)
    body.pop("report_sha256", None)
    return body


def run_audit(
    spec: AuditSpec,
    db_path: str | Path,
    *,
    materialize_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Audit ``spec``, write the SQL ledger, return the digest-bound report."""
    spec = spec.validate()
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()  # a run owns its ledger file; stale rows must not survive

    results = classify_all(spec.capabilities, spec.thresholds)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "spec_sha256": spec.sha256(),
        "thresholds_sha256": spec.thresholds.sha256(),
        "rule_set_sha256": sha256_json(list(RULE_IDS)),
        "spec_label": spec.label,
    }

    ledger = SqlLedger(db_path).open()
    try:
        ledger.write(results, meta)
        canonical = ledger.canonical_dump()
        ledger_sha = sha256_text(canonical)
        ledger_rows = len(ledger.rows("ledger_capability", ("capability_id",)))
        gate_rows = len(ledger.rows("ledger_gate", ("capability_id", "gate")))
    finally:
        ledger.close()

    report: Dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "spec_label": spec.label,
        "spec_sha256": spec.sha256(),
        "thresholds": spec.thresholds.to_dict(),
        "thresholds_sha256": spec.thresholds.sha256(),
        "rule_set": list(RULE_IDS),
        "capability_count": len(results),
        "level_counts": level_counts(results),
        "level_vocabulary": list(LEVELS),
        "transitions": _transition_counts(results),
        "known_label_score": known_label_score(results),
        "results": [r.to_dict() for r in results],
        "results_sha256": sha256_json([r.to_dict() for r in results]),
        "ledger": {
            # deliberately no file name/path: the report must be a pure function
            # of the spec, otherwise re-running into a different file would break
            # bit-identical idempotency.
            "capability_rows": ledger_rows,
            "gate_rows": gate_rows,
            "sha256": ledger_sha,
            "canonical": canonical,
        },
    }
    report["report_sha256"] = sha256_json(_report_body(report))

    if materialize_dir is not None:
        write_artifacts(report, materialize_dir)
    return report


def write_artifacts(report: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "audit_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "ledger_canonical.txt").write_text(report["ledger"]["canonical"], encoding="utf-8")
    (out / "digest_summary.json").write_text(
        json.dumps(
            {
                "spec_sha256": report["spec_sha256"],
                "thresholds_sha256": report["thresholds_sha256"],
                "results_sha256": report["results_sha256"],
                "ledger_sha256": report["ledger"]["sha256"],
                "report_sha256": report["report_sha256"],
                "capability_count": report["capability_count"],
                "level_counts": report["level_counts"],
                "known_label_accuracy": report["known_label_score"]["accuracy"],
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


def verify_audit(
    report: Mapping[str, Any],
    spec: AuditSpec,
    db_path: str | Path,
) -> Dict[str, Any]:
    """Independent re-derivation of every digest in ``report``."""
    spec = spec.validate()
    ledger = SqlLedger(db_path).open()
    try:
        canonical = ledger.canonical_dump()
    finally:
        ledger.close()

    recomputed = classify_all(spec.capabilities, spec.thresholds)
    recomputed_results = [r.to_dict() for r in recomputed]
    reported_results = report.get("results", [])

    checks = {
        # (c) SQL ledger -> report dump, bit for bit
        "ledger_canonical_identical": canonical == report["ledger"]["canonical"],
        "ledger_sha256_identical": sha256_text(canonical) == report["ledger"]["sha256"],
        # (b) verdicts follow from the spec, not from the report
        "results_rederived_identical": recomputed_results == reported_results,
        "results_sha256_identical": sha256_json(recomputed_results)
        == report.get("results_sha256"),
        "report_sha256_identical": sha256_json(_report_body(report))
        == report.get("report_sha256"),
        "spec_sha256_identical": spec.sha256() == report.get("spec_sha256"),
        "thresholds_sha256_identical": spec.thresholds.sha256()
        == report.get("thresholds_sha256"),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "digests": {
            "ledger_sha256_db": sha256_text(canonical),
            "ledger_sha256_report": report["ledger"]["sha256"],
            "results_sha256_rederived": sha256_json(recomputed_results),
            "results_sha256_report": report.get("results_sha256"),
            "report_sha256_rederived": sha256_json(_report_body(report)),
            "report_sha256_report": report.get("report_sha256"),
            "spec_sha256": spec.sha256(),
            "thresholds_sha256": spec.thresholds.sha256(),
        },
    }


def load_report(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def report_markdown(report: Mapping[str, Any]) -> str:
    """Render the audit report as a diffable markdown table."""
    lines = [
        f"# capability ledger audit — {report.get('spec_label') or '(unlabelled spec)'}",
        "",
        f"- spec sha256: `{report['spec_sha256']}`",
        f"- thresholds sha256: `{report['thresholds_sha256']}`",
        f"- ledger sha256: `{report['ledger']['sha256']}`",
        f"- report sha256: `{report['report_sha256']}`",
        f"- capabilities: {report['capability_count']}  "
        f"levels: {canonical_json(report['level_counts'])}",
        "",
        "| capability_id | level | rule | chain | transition | expected | match |",
        "|---|---|---|---|---|---|---|",
    ]
    for res in report["results"]:
        match = "n/a" if res["label_match"] is None else ("yes" if res["label_match"] else "no")
        lines.append(
            "| {cid} | {lvl} | {rule} | {chain} | {tr} | {exp} | {m} |".format(
                cid=res["capability_id"],
                lvl=res["level"],
                rule=res["rule_id"],
                chain="complete" if res["chain"]["chain_complete"] else "incomplete",
                tr=res["transition"],
                exp=res["expected_level"] or "n/a",
                m=match,
            )
        )
    return "\n".join(lines) + "\n"