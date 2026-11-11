"""SQL ledger tests: canonical dump, ordering independence, idempotent upsert."""
from __future__ import annotations

import sqlite3

from capability_ledger_digest.classifier import classify_all
from capability_ledger_digest.sql_ledger import SqlLedger


def _results(spec):
    return classify_all(spec.capabilities, spec.thresholds)


def test_dump_is_identical_after_reopen(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    results = _results(known_spec)
    with SqlLedger(db) as ledger:
        ledger.write(results, {"spec_label": "unit"})
        first = ledger.canonical_dump()
    with SqlLedger(db) as ledger:
        assert ledger.canonical_dump() == first


def test_dump_is_independent_of_input_order(tmp_path, known_spec):
    results = list(_results(known_spec))
    db_a, db_b = tmp_path / "a.sqlite", tmp_path / "b.sqlite"
    with SqlLedger(db_a) as ledger:
        ledger.write(results, {"spec_label": "unit"})
        dump_a = ledger.canonical_dump()
    with SqlLedger(db_b) as ledger:
        ledger.write(list(reversed(results)), {"spec_label": "unit"})
        dump_b = ledger.canonical_dump()
    assert dump_a == dump_b


def test_rewrite_removes_stale_rows(tmp_path, known_spec):
    results = _results(known_spec)
    db = tmp_path / "ledger.sqlite"
    with SqlLedger(db) as ledger:
        ledger.write(results, {"spec_label": "full"})
        full_rows = len(ledger.rows("ledger_capability", ("capability_id",)))
        ledger.write(results[:3], {"spec_label": "trimmed"})
        trimmed_rows = len(ledger.rows("ledger_capability", ("capability_id",)))
        gate_rows = len(ledger.rows("ledger_gate", ("capability_id", "gate")))
    assert full_rows == len(results)
    assert trimmed_rows == 3
    assert gate_rows == 3 * 3  # three evidence items per capability, none stale


def test_dump_has_no_clock_values(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    with SqlLedger(db) as ledger:
        ledger.write(_results(known_spec), {"spec_label": "unit", "spec_sha256": known_spec.sha256()})
        dump = ledger.canonical_dump()
        meta = {row["key"]: row["value"] for row in ledger.rows("ledger_meta", ("key",))}
    assert "2026-" not in dump and "T00:00" not in dump
    assert meta["capability_count"] == str(len(known_spec.capabilities))
    assert meta["spec_sha256"] == known_spec.sha256()


def test_capability_table_matches_dump_cells(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    with SqlLedger(db) as ledger:
        ledger.write(_results(known_spec), {"spec_label": "unit"})
        rows = ledger.rows("ledger_capability", ("capability_id",))
        dump = ledger.canonical_dump()
    section = dump.split("## ledger_capability", 1)[1]
    for row in rows:
        assert (
            f"{row['capability_id']}|{row['level']}|{row['rule_id']}|{row['reason']}"
            in section
        )


def test_sql_query_reproduces_levels(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    results = _results(known_spec)
    with SqlLedger(db) as ledger:
        ledger.write(results, {"spec_label": "unit"})
    conn = sqlite3.connect(str(db))
    try:
        queried = dict(conn.execute("SELECT capability_id, level FROM ledger_capability"))
    finally:
        conn.close()
    assert queried == {r.capability_id: r.level for r in results}


def test_markdown_table_lists_all_capabilities(tmp_path, known_spec):
    db = tmp_path / "ledger.sqlite"
    with SqlLedger(db) as ledger:
        ledger.write(_results(known_spec), {"spec_label": "unit"})
        md = ledger.to_markdown()
    assert md.count("\n") >= len(known_spec.capabilities) + 1
    assert "cap.alpha" in md and "REAL" in md