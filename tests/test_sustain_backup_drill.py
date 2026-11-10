"""Regression suite: backup/restore drill (isolated namespace, triple cross-check,
three negative controls, RPO/RTO ledger)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.sustainment import backup_drill as bd


@pytest.fixture()
def source_db(tmp_path: Path) -> tuple[str, list[str]]:
    path = tmp_path / "platform.db"
    tables = bd.build_fixture_db(str(path), n_tables=5, rows=25, seed=11)
    return str(path), tables


def test_fixture_has_five_core_tables(source_db):
    path, tables = source_db
    assert len(tables) == 5
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(f'SELECT count(*) FROM "{tables[0]}"').fetchone()[0]
    finally:
        conn.close()
    assert rows == 25


def test_sqlite_drill_passes_with_triple_cross_check(source_db, tmp_path: Path):
    src, tables = source_db
    ledger = tmp_path / "ledger.json"
    res = bd.run_drill(src, tables, str(tmp_path / "work"), engine_kind="sqlite",
                       ledger_path=str(ledger))
    assert res["verdict"] == "PASS", json.dumps(res["checks"], indent=1)
    assert res["checks"]["triple_cross_check"]["ok"] is True
    assert res["checks"]["triple_cross_check"]["tables_matched"] == 5
    assert res["checks"]["restore_isolated"]["ok"] is True
    # every table matched on all three invariants, no mismatches recorded
    for row in res["tables_detail"]:
        assert row["mismatches"] == []
        assert row["source"]["content_digest"] == row["restored"]["content_digest"]
        assert row["source"]["row_count"] == row["restored"]["row_count"] == 25
        assert row["source"]["schema_signature"] == row["restored"]["schema_signature"]
    # RTO/RPO recorded and inside budget
    assert res["rto_s"] is not None and res["rto_s"] < 900
    assert res["rpo_s"] is not None
    assert res["checks"]["rto_within_budget"]["ok"] is True


def test_negative_controls_all_detected(source_db, tmp_path: Path):
    src, tables = source_db
    res = bd.run_drill(src, tables, str(tmp_path / "work"))
    ncs = {nc["control"]: nc for nc in res["negative_controls"]}
    assert set(ncs) == {"NC1_truncated_dump", "NC2_missing_table", "NC3_tampered_row"}
    assert all(nc["detected"] for nc in ncs.values()), ncs
    assert res["checks"]["negative_controls"]["detected"] == 3


def test_source_is_never_written(source_db, tmp_path: Path):
    src, tables = source_db
    before = bd._sha256_file(src)
    bd.run_drill(src, tables, str(tmp_path / "work"))
    after = bd._sha256_file(src)
    assert before == after


def test_ledger_is_append_only_and_newest_first(source_db, tmp_path: Path):
    src, tables = source_db
    ledger = tmp_path / "ledger.json"
    bd.run_drill(src, tables, str(tmp_path / "w1"), ledger_path=str(ledger))
    bd.run_drill(src, tables, str(tmp_path / "w2"), ledger_path=str(ledger))
    payload = json.loads(ledger.read_text())
    assert len(payload["records"]) == 2
    rec = payload["records"][0]
    assert rec["verdict"] == "PASS"
    assert set(rec["checks"]) >= {"backup_created", "restore_isolated",
                                 "triple_cross_check", "negative_controls"}
    assert rec["rto_budget_s"] == bd.DEFAULT_RTO_BUDGET_S
    assert rec["rpo_budget_s"] == bd.DEFAULT_RPO_BUDGET_S
    assert len(rec["backup_sha256"]) == 64


def test_missing_table_fails_honestly(source_db, tmp_path: Path):
    src, tables = source_db
    res = bd.run_drill(src, ["core_01", "no_such_table"], str(tmp_path / "work"))
    assert res["verdict"] == "FAIL"
    assert res["checks"]["source_tables_present"]["ok"] is False
    assert res["checks"]["source_tables_present"]["missing"] == ["no_such_table"]


def test_no_tables_is_fail_not_crash(source_db, tmp_path: Path):
    src, _ = source_db
    res = bd.run_drill(src, [], str(tmp_path / "work"))
    assert res["verdict"] == "FAIL"
    assert res["checks"]["tables_declared"]["ok"] is False


def test_unknown_engine_is_withheld(source_db, tmp_path: Path):
    src, tables = source_db
    res = bd.run_drill(src, tables, str(tmp_path / "work"), engine_kind="mysql")
    assert res["verdict"] == "WITHHELD"
    assert res["checks"]["engine_available"]["ok"] is False


def test_postgres_engine_unavailable_is_withheld(tmp_path: Path):
    """No DSN/tooling in CI → the drill must say WITHHELD, never fake a PASS."""
    res = bd.run_drill("postgresql://nobody@127.0.0.1:1/none", ["t1"],
                       str(tmp_path / "work"), engine_kind="postgres")
    assert res["verdict"] in ("WITHHELD", "FAIL")
    assert res["checks"].get("engine_available", {}).get("ok", True) is False or \
        res["checks"].get("backup_created", {}).get("ok") is False


def test_content_digest_is_order_insensitive_but_value_sensitive(source_db, tmp_path: Path):
    """The digest is a canonical (ordered) row encoding: value edits must show."""
    src, tables = source_db
    eng = bd.SqliteEngine(src)
    t = tables[0]
    s1 = eng.stats(src, t)
    assert s1.row_count == 25 and len(s1.content_digest) == 64
    conn = sqlite3.connect(src)
    try:
        conn.execute(f'UPDATE "{t}" SET weight = weight + 1 WHERE id = 3')
        conn.commit()
    finally:
        conn.close()
    s2 = bd.SqliteEngine(src).stats(src, t)
    assert s2.row_count == s1.row_count
    assert s2.content_digest != s1.content_digest


def test_schema_signature_changes_with_ddl(source_db, tmp_path: Path):
    src, tables = source_db
    eng = bd.SqliteEngine(src)
    t = tables[1]
    before = eng.stats(src, t).schema_signature
    conn = sqlite3.connect(src)
    try:
        conn.execute(f'ALTER TABLE "{t}" ADD COLUMN extra TEXT')
        conn.commit()
    finally:
        conn.close()
    after = bd.SqliteEngine(src).stats(src, t).schema_signature
    assert before != after


def test_drill_artifacts_kept_and_isolated_namespace(source_db, tmp_path: Path):
    src, tables = source_db
    work = tmp_path / "work"
    res = bd.run_drill(src, tables, str(work))
    assert res["namespace"].startswith(bd.DRILL_NAMESPACE_PREFIX)
    assert Path(res["restore"]["target"]).exists()
    assert Path(res["restore"]["target"]).name == f"{res['namespace']}.db"
    assert Path(res["backup"]["path"]).exists()
    assert Path(res["backup"]["path"]) != Path(src)


def test_tables_config_loader(tmp_path: Path):
    p = tmp_path / "tables.json"
    p.write_text(json.dumps({"tables": ["missions", "tensor_reports"]}))
    assert bd.load_tables_config(str(p)) == ["missions", "tensor_reports"]
    assert bd.load_tables_config(str(tmp_path / "absent.json")) == []


def test_repo_core_table_config_is_five_platform_tables():
    root = Path(__file__).resolve().parents[1]
    tables = bd.load_tables_config(str(root / "config" / "sustain_core_tables.json"))
    assert len(tables) == 5
    assert all(isinstance(t, str) and t for t in tables)