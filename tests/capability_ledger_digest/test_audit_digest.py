"""End-to-end audit tests: criteria (c) bit identity, (e) idempotency, tamper controls."""
from __future__ import annotations

import json
import sqlite3

from capability_ledger_digest.audit import (
    load_report,
    report_markdown,
    run_audit,
    verify_audit,
    write_artifacts,
)


def test_ledger_and_report_digests_are_identical(tmp_path, known_spec):
    report = run_audit(known_spec, tmp_path / "ledger.sqlite")
    verdict = verify_audit(report, known_spec, tmp_path / "ledger.sqlite")
    assert verdict["ok"] is True, verdict["checks"]
    assert report["ledger"]["sha256"] == verdict["digests"]["ledger_sha256_db"]
    assert report["ledger"]["sha256"] == verdict["digests"]["ledger_sha256_report"]


def test_report_rows_equal_sql_capability_table(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    report = run_audit(known_spec, db)
    conn = sqlite3.connect(str(db))
    try:
        sql_rows = {
            cid: (lvl, rule, bool(chain)) for cid, lvl, rule, chain in conn.execute(
                "SELECT capability_id, level, rule_id, chain_complete FROM ledger_capability"
            )
        }
    finally:
        conn.close()
    report_rows = {
        r["capability_id"]: (r["level"], r["rule_id"], r["chain"]["chain_complete"])
        for r in report["results"]
    }
    assert sql_rows == report_rows


def test_rerun_is_bit_identical(tmp_path, known_spec):
    reports = []
    for i in range(3):
        db = tmp_path / f"ledger_{i}.sqlite"
        report = run_audit(known_spec, db)
        write_artifacts(report, tmp_path / f"out_{i}")
        reports.append((tmp_path / f"out_{i}" / "audit_report.json").read_bytes())
    assert len(set(reports)) == 1
    assert len({run_audit(known_spec, tmp_path / f"re_{i}.sqlite")["ledger"]["sha256"]
                for i in range(3)}) == 1


def test_in_place_rerun_keeps_digest(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    first = run_audit(known_spec, db)
    second = run_audit(known_spec, db)
    assert first["ledger"]["sha256"] == second["ledger"]["sha256"]
    assert first["report_sha256"] == second["report_sha256"]


def test_tampered_report_is_detected(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    report = run_audit(known_spec, db)
    assert verify_audit(report, known_spec, db)["ok"] is True

    tampered = json.loads(json.dumps(report))
    for row in tampered["results"]:
        if row["capability_id"] == "cap.zeta":
            row["level"] = "REAL"  # claim an upgrade that the evidence denies
    verdict = verify_audit(tampered, known_spec, db)
    assert verdict["ok"] is False
    assert verdict["checks"]["results_rederived_identical"] is False


def test_tampered_ledger_is_detected(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    report = run_audit(known_spec, db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE ledger_capability SET level='REAL' WHERE capability_id='cap.theta'"
        )
        conn.commit()
    finally:
        conn.close()
    verdict = verify_audit(report, known_spec, db)
    assert verdict["ok"] is False
    assert verdict["checks"]["ledger_canonical_identical"] is False


def test_tampered_report_digest_is_detected(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    report = run_audit(known_spec, db)
    edited = json.loads(json.dumps(report))
    edited["capability_count"] = edited["capability_count"] + 1
    verdict = verify_audit(edited, known_spec, db)
    assert verdict["ok"] is False
    assert verdict["checks"]["report_sha256_identical"] is False


def test_threshold_change_invalidates_verification(tmp_path, known_spec):
    from capability_ledger_digest.spec import AuditSpec

    db = tmp_path / "ledger.sqlite"
    report = run_audit(known_spec, db)
    loose = AuditSpec.from_dict(
        {**known_spec.to_dict(), "thresholds": {**known_spec.thresholds.to_dict(),
                                                "benchmark_error_max_pct": 25.0}}
    )
    verdict = verify_audit(report, loose, db)
    assert verdict["ok"] is False
    assert verdict["checks"]["thresholds_sha256_identical"] is False


def test_artifacts_are_materialised(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    report = run_audit(known_spec, db)
    out = write_artifacts(report, tmp_path / "artifacts")
    for name in ("audit_report.json", "ledger_canonical.txt", "digest_summary.json"):
        assert (out / name).exists()
    summary = json.loads((out / "digest_summary.json").read_text())
    assert summary["ledger_sha256"] == report["ledger"]["sha256"]
    assert summary["known_label_accuracy"] == 1.0
    assert load_report(out / "audit_report.json")["report_sha256"] == report["report_sha256"]


def test_report_markdown_matches_results(tmp_path, known_spec):
    report = run_audit(known_spec, tmp_path / "ledger.sqlite")
    md = report_markdown(report)
    assert md.count("\n") >= len(report["results"]) + 4
    for row in report["results"]:
        assert row["capability_id"] in md
    assert report["ledger"]["sha256"] in md


def test_stale_ledger_file_is_replaced(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    run_audit(known_spec, db)
    # a second run must not accumulate: row counts stay exactly on the spec size
    report = run_audit(known_spec, db)
    assert report["ledger"]["capability_rows"] == len(known_spec.capabilities)
    assert report["ledger"]["gate_rows"] == 3 * len(known_spec.capabilities)


def test_results_digest_binds_spec_not_labels(tmp_path, known_spec):
    """A wrong expected label changes the accuracy score, never the derived level."""
    from capability_ledger_digest.spec import AuditSpec

    raw = known_spec.to_dict()
    for cap in raw["capabilities"]:
        if cap["capability_id"] == "cap.alpha":
            cap["expected_level"] = "MOCK"
    perturbed = AuditSpec.from_dict(raw)
    base = run_audit(known_spec, tmp_path / "base.sqlite")
    other = run_audit(perturbed, tmp_path / "other.sqlite")
    levels_base = {r["capability_id"]: r["level"] for r in base["results"]}
    levels_other = {r["capability_id"]: r["level"] for r in other["results"]}
    assert levels_base == levels_other
    assert base["known_label_score"]["accuracy"] == 1.0
    assert other["known_label_score"]["accuracy"] < 1.0
    # the label is part of the report (so the digests differ) but not of the derived level
    assert base["results_sha256"] != other["results_sha256"]