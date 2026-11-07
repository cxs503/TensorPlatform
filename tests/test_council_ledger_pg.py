"""IN28-N07 账本接库：换存储不改协议（红/绿判据）。

覆盖：
- 哈希口径一致：库里重算的 entry_hash 与文件账本逐字节相等（协议不变的直接证据）；
- append-only：篡改内容/断链/协议裁剪一律拒写；
- 幂等导入 + 字段级对账（parity）能抓出库侧被改的行；
- PG 与 SQLite 双后端同构（PG 不可达则只在 SQLite 上跑并在报告里如实说明）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.council_constitution import Ledger, sha256_obj  # noqa: E402
from services.council_constitution.ledger_store import (  # noqa: E402
    DEFAULT_TABLE,
    GENESIS_HASH,
    LedgerStoreError,
    SqlLedgerStore,
)

RECORDS = [
    {"case_id": "X-1", "verdict": "HONEST_FAIL", "route": "criteria"},
    {"case_id": "X-2", "verdict": "ESCALATE_TO_OWNER", "route": "redline_escalation"},
    {"case_id": "X-3", "verdict": "ADOPT", "route": "committee"},
]


def _pg_dsn() -> str | None:
    import os
    for key in ("COUNCIL_LEDGER_DSN", "TP_PG_DSN", "DATABASE_URL"):
        if os.getenv(key):
            return os.environ[key]
    try:
        store = SqlLedgerStore("postgresql+psycopg://postgres@/tensorplatform?host=/tmp")
        store.count()
        store.close()
        return "postgresql+psycopg://postgres@/tensorplatform?host=/tmp"
    except Exception:  # noqa: BLE001 - 无 PG 时退化为 SQLite
        return None


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path: Path):
    if request.param == "postgres":
        dsn = _pg_dsn()
        if not dsn:
            pytest.skip("PostgreSQL 不可达（本机无 socket/DSN），PG 分支跳过")
        table = DEFAULT_TABLE + "_test"
        st = SqlLedgerStore(dsn, table=table)
        st.ensure_schema()
        from sqlalchemy import delete
        with st.engine.begin() as conn:
            conn.execute(delete(st.table))
        yield st
        with st.engine.begin() as conn:
            conn.execute(delete(st.table))
        st.close()
    else:
        st = SqlLedgerStore(f"sqlite:///{tmp_path/'ledger.db'}")
        st.ensure_schema()
        yield st
        st.close()


# --------------------------------------------------------------------------- #
# 一、协议不变：库重算哈希 == 文件账本哈希
# --------------------------------------------------------------------------- #
def test_store_hash_matches_file_ledger(store: SqlLedgerStore, tmp_path: Path) -> None:
    """同一批记录，文件账本与库账本的 entry_hash 必须逐字节相等（换存储不改协议）。"""
    path = tmp_path / "file_ledger.jsonl"
    ledger = Ledger(path)
    file_entries = [ledger.append(rec, recorded_at=f"2026-11-07T12:00:0{i}Z")
                    for i, rec in enumerate(RECORDS)]
    for entry in file_entries:
        store.append_entry(entry)

    db_entries = store.entries()
    assert [e["entry_hash"] for e in db_entries] == [e["entry_hash"] for e in file_entries]
    assert [e["seq"] for e in db_entries] == [1, 2, 3]
    assert db_entries[0]["prev_hash"] == GENESIS_HASH
    # 第三方复算：用协议口径重算一遍，库里的哈希也必须对得上
    for e in db_entries:
        assert sha256_obj({k: v for k, v in e.items() if k != "entry_hash"}) == e["entry_hash"]


def test_store_append_record_is_protocol_identical(store: SqlLedgerStore, tmp_path: Path) -> None:
    path = tmp_path / "file_ledger2.jsonl"
    ledger = Ledger(path)
    e_file = ledger.append({"case_id": "Y-1", "verdict": "REJECT", "route": "criteria"},
                           recorded_at="2026-11-07T12:00:00Z")
    e_db = store.append_record({"case_id": "Y-1", "verdict": "REJECT", "route": "criteria"},
                               recorded_at="2026-11-07T12:00:00Z")
    assert e_db["entry_hash"] == e_file["entry_hash"]
    assert store.verify()["ok"] is True


# --------------------------------------------------------------------------- #
# 二、append-only 与拒写（负例）
# --------------------------------------------------------------------------- #
def test_store_rejects_tampered_entry(store: SqlLedgerStore) -> None:
    path_rec = {"case_id": "Z-1", "verdict": "ADOPT", "route": "committee"}
    entry = store.append_record(path_rec, recorded_at="2026-11-07T12:00:00Z")
    bad = dict(entry)
    bad["record"] = {**entry["record"], "verdict": "HONEST_FAIL"}
    with pytest.raises(LedgerStoreError, match="entry_hash"):
        store.append_entry(bad)


def test_store_rejects_broken_chain(store: SqlLedgerStore) -> None:
    first = store.append_record({"case_id": "B-1", "verdict": "ADOPT", "route": "committee"},
                                recorded_at="2026-11-07T12:00:00Z")
    orphan = {
        "schema": first["schema"],
        "seq": 5,
        "recorded_at": "2026-11-07T12:00:01Z",
        "prev_hash": "f" * 64,  # 与库内链头不符
        "record": {"case_id": "B-2"},
    }
    orphan["entry_hash"] = sha256_obj({k: v for k, v in orphan.items() if k != "entry_hash"})
    with pytest.raises(LedgerStoreError, match="prev_hash"):
        store.append_entry(orphan)


def test_store_rejects_field_pruning(store: SqlLedgerStore) -> None:
    with pytest.raises(LedgerStoreError, match="缺字段"):
        store.append_entry({"schema": "tp.council.ledger_entry/v1", "seq": 1})


# --------------------------------------------------------------------------- #
# 三、导入幂等 + 字段级对账
# --------------------------------------------------------------------------- #
def test_import_file_idempotent_and_parity(store: SqlLedgerStore, tmp_path: Path) -> None:
    path = tmp_path / "file_ledger3.jsonl"
    ledger = Ledger(path)
    for i, rec in enumerate(RECORDS):
        ledger.append(rec, recorded_at=f"2026-11-07T12:0{i}:00Z")

    first = store.import_file(path)
    second = store.import_file(path)
    assert first["inserted"] == len(RECORDS) and first["skipped"] == 0
    assert second["inserted"] == 0 and second["skipped"] == len(RECORDS)

    report = store.parity(path)
    assert report["ok"] is True
    assert report["rows_db"] == report["rows_file"] == len(RECORDS)
    assert report["field_diff_count"] == 0
    assert report["db_head_hash"] == report["file_head_hash"]


def test_parity_detects_db_side_tampering(store: SqlLedgerStore, tmp_path: Path) -> None:
    path = tmp_path / "file_ledger4.jsonl"
    ledger = Ledger(path)
    for i, rec in enumerate(RECORDS):
        ledger.append(rec, recorded_at=f"2026-11-07T13:0{i}:00Z")
    store.import_file(path)
    assert store.parity(path)["ok"] is True

    # 直接改库里的 record（绕过 append 门），对账必须逐字段抓出来
    from sqlalchemy import update
    with store.engine.begin() as conn:
        conn.execute(update(store.table).where(store.table.c.seq == 2)
                     .values(record={"case_id": "X-2", "verdict": "ADOPT", "route": "committee"}))
    report = store.parity(path)
    assert report["ok"] is False
    assert report["field_diff_count"] >= 1
    fields = {d["field"] for d in report["field_diffs"]}
    assert "record" in fields or "entry_hash" in fields
    assert store.verify()["ok"] is False


def test_parity_detects_missing_rows(store: SqlLedgerStore, tmp_path: Path) -> None:
    path = tmp_path / "file_ledger5.jsonl"
    ledger = Ledger(path)
    ledger.append(RECORDS[0], recorded_at="2026-11-07T14:00:00Z")
    ledger.append(RECORDS[1], recorded_at="2026-11-07T14:00:01Z")
    store.import_file(path)
    from sqlalchemy import delete
    with store.engine.begin() as conn:
        conn.execute(delete(store.table).where(store.table.c.seq == 2))
    report = store.parity(path)
    assert report["ok"] is False
    assert report["rows_db"] == 1 and report["rows_file"] == 2
    assert any(d["field"] == "<row>" for d in report["field_diffs"])


# --------------------------------------------------------------------------- #
# 四、真实工件（回放验收产物）——存在即校验
# --------------------------------------------------------------------------- #
ARTIFACT = ROOT / "artifacts/in28_n07/acceptance.json"


@pytest.mark.skipif(not ARTIFACT.exists(), reason="回放工件未生成（先跑 scripts/in28_n07_ledger_pg.py）")
def test_acceptance_artifact_all_criteria_pass() -> None:
    acc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    criteria = acc["criteria"]
    assert criteria["a_db_file_parity"]["passed"] is True
    assert criteria["a_db_file_parity"]["field_diff_count"] == 0
    assert criteria["b_corpus"]["passed"] is True
    assert criteria["c_owner_agreement"]["passed"] is True
    assert criteria["d_protocol_unchanged"]["passed"] is True
    # e 由外部 pytest 结果注入；注入了就必须全绿且总判定 PASS
    if criteria["e_pytest"].get("status") != "not_injected":
        assert criteria["e_pytest"]["passed"] is True
        assert acc["all_passed"] is True