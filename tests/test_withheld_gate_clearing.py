"""WITHHELD 三态清偿闭环测试（IN50-W4-N09）。

覆盖：门声明→登记→自动重判（三态保留）→不确定度预算→反作弊（tol 放宽/tol 列触发器/
审计 append-only/哈希链）→KPI 落库→派单幂等→与 reference_registry 口径兼容。

全部域名为 ``SYN-*`` 合成域名（平台仓零领域语义铁律）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.vv import reference_registry as RR  # noqa: E402
from tools.vv import withheld_gates as W  # noqa: E402

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)

# 合成数字化参考表：y 在 x∈[0,5] 上单调，逐点 σ 为数字化读数不确定度
SYN_POINTS = [
    [0.0, 1.00, 0.010],
    [1.0, 1.20, 0.012],
    [2.0, 1.40, 0.014],
    [3.0, 1.60, 0.016],
    [4.0, 1.80, 0.018],
    [5.0, 2.00, 0.020],
]


def syn_dataset(ds_id: str = "SYN-DS-001", *, u_dig: float = 0.01,
                kind: str = "relative", doi: str = "10.9999/syn.0001",
                method: str = "SYN-DIGITISATION-METHOD-A",
                points=None) -> dict:
    pts = SYN_POINTS if points is None else points
    return W.make_reference_dataset(
        ds_id, pts,
        citation="SYN-CITATION-ET-AL (2099) synthetic reference table",
        doi=doi,
        digitised_by="SYN-ROBOT-DIGITISER",
        retrieved_from="SYN-Repo-A/reference/syn_table.json#1",
        retrieved_sha256="a" * 64,
        digitisation_method=method,
        u_dig=u_dig,
        u_dig_kind=kind,
        tolerance=0.15,
        quantity="SYN-QUANTITY-ALPHA",
        unit="SYN-U",
    )


def syn_gate(gate_id: str = "SYN-GATE-001", ds_id: str = "SYN-DS-001", *,
             at: float = 2.0, value: float = 1.41, tol: float = 0.15,
             k: float = 2.0, u_exp: float = 0.0,
             repo: str = "SYN-Repo-A", node: str = "SYN-NODE-1") -> dict:
    return {
        "schema": W.SCHEMA_ID,
        "gate_id": gate_id,
        "mission_id": 10377,
        "node": node,
        "repo": repo,
        "quantity": "SYN-QUANTITY-ALPHA",
        "unit": "SYN-U",
        "reason_code": W.R_NO_REFERENCE,
        "requires_reference": {
            "dataset_id": ds_id,
            "tolerance": tol,
            "k_factor": k,
            "measurement": {"at": at, "value": value, "u_exp": u_exp},
        },
    }


@pytest.fixture()
def conn():
    c = W.connect(":memory:")
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# 1. 门声明：没有数据集时必须是 WITHHELD（不得假装可判）
# --------------------------------------------------------------------------- #
def test_declare_waits_withheld(conn):
    res = W.declare_gate(conn, syn_gate(), now=T0)
    assert res["verdict"] == W.WITHHELD
    assert res["reason_code"] == W.R_NO_REFERENCE
    row = conn.one("SELECT * FROM tensor_withheld_gates WHERE gate_id='SYN-GATE-001'")
    assert row["verdict"] == W.WITHHELD
    assert row["cleared_at"] is None
    assert row["tolerance"] == pytest.approx(0.15)
    assert len(row["artifact_sha256"]) == 64


def test_declare_requires_dataset_and_sane_tolerance(conn):
    bad = syn_gate()
    del bad["requires_reference"]["dataset_id"]
    with pytest.raises(ValueError):
        W.declare_gate(conn, bad, now=T0)
    with pytest.raises(ValueError):
        W.declare_gate(conn, syn_gate(tol=0.9), now=T0)  # 容差不能自证宽松


# --------------------------------------------------------------------------- #
# 2. 登记 → 自动重判：清偿成 PASS，审计链自洽
# --------------------------------------------------------------------------- #
def test_registration_clears_gate_to_pass(conn):
    W.declare_gate(conn, syn_gate(), now=T0)
    reg = W.register_reference(conn, syn_dataset(), now=T0 + 2 * DAY, actor="agent-test")
    assert reg["usable_as_gate"] is True
    assert reg["bound_gates"] == ["SYN-GATE-001"]
    rj = reg["rejudged"][0]
    assert rj["verdict"] == W.PASS
    assert rj["reference"] == pytest.approx(1.40)
    assert rj["E"] == pytest.approx((1.41 - 1.40) / 1.40)

    row = conn.one("SELECT * FROM tensor_withheld_gates WHERE gate_id='SYN-GATE-001'")
    assert row["cleared_at"] is not None
    chain = W.verify_audit_chain(conn, "SYN-GATE-001")
    assert chain["ok"] is True
    events = [r["event"] for r in conn.query(
        "SELECT event FROM tensor_withheld_audit WHERE gate_id='SYN-GATE-001' ORDER BY seq")]
    assert events == ["GATE_DECLARED", "REJUDGED", "DATASET_REGISTERED", "REJUDGED"]


def test_fail_is_honest_and_keeps_withheld_open(conn):
    W.declare_gate(conn, syn_gate(value=1.70), now=T0)  # E≈21%
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    row = conn.one("SELECT * FROM tensor_withheld_gates WHERE gate_id='SYN-GATE-001'")
    assert row["verdict"] == W.FAIL
    assert row["cleared_at"] is None
    kpi = W.compute_kpis(conn, now=T0 + DAY)
    assert kpi["gates_fail"] == 1 and kpi[W.KPI_SHARE] == 0.0


# --------------------------------------------------------------------------- #
# 3. 数字化不确定度预算：|E|≤max(tol, k·sqrt(u_exp²+u_dig²)) 才有 PASS
# --------------------------------------------------------------------------- #
def test_uncertainty_budget_is_what_clears_the_gate(conn):
    # tol 收紧到 5%：单独看容差必然 FAIL
    W.declare_gate(conn, syn_gate(value=1.50, tol=0.05), now=T0)
    no_unc = W.register_reference(conn, syn_dataset("SYN-DS-001", u_dig=0.0), now=T0 + DAY)
    assert no_unc["rejudged"][0]["verdict"] == W.FAIL  # 无 u_dig 时不确定度带为 0

    # 同一门换成声明 5% 数字化不确定度的数据集重新登记 → 在 k·u 预算内 ⇒ PASS
    with_unc = W.register_reference(conn, syn_dataset("SYN-DS-001", u_dig=0.05), now=T0 + 2 * DAY)
    rj = with_unc["rejudged"][0]
    assert rj["verdict"] == W.PASS
    assert rj["reason_code"] == W.R_WITHIN_UNCERTAINTY
    assert rj["band"] == pytest.approx(0.10)
    assert abs(rj["E"]) > 0.05  # 确实超了容差，靠不确定度预算通过


def test_uncertainty_budget_cannot_swallow_arbitrary_error(conn):
    W.declare_gate(conn, syn_gate(value=1.95, tol=0.05), now=T0)  # E≈39%
    res = W.register_reference(conn, syn_dataset(u_dig=0.05), now=T0 + DAY)
    assert res["rejudged"][0]["verdict"] == W.FAIL


def test_future_created_at_does_not_yield_negative_age(conn):
    # 时钟回拨/未来声明：账龄下限 0，不得把均值拉低成"假清偿"
    W.declare_gate(conn, syn_gate(), now=T0 + 5 * DAY)
    kpi = W.compute_kpis(conn, now=T0)
    assert kpi[W.KPI_AGE] == 0.0
    assert kpi["open_withheld_max_age_days"] == 0.0


def test_absolute_u_dig_supported(conn):
    W.declare_gate(conn, syn_gate(value=1.55, tol=0.05), now=T0)  # E≈10.7%
    ds = syn_dataset(u_dig=0.10, kind="absolute")  # 绝对不确定度 0.10 ⇒ band=2·0.10/1.4≈14.3%
    rj = W.register_reference(conn, ds, now=T0 + DAY)["rejudged"][0]
    assert rj["u_dig_kind"] == "absolute"
    assert rj["verdict"] == W.PASS and rj["reason_code"] == W.R_WITHIN_UNCERTAINTY


# --------------------------------------------------------------------------- #
# 4. 反作弊：tol 放宽 = 篡改；库层不可改；审计不可改
# --------------------------------------------------------------------------- #
def test_post_hoc_tolerance_widening_is_blocked(conn):
    W.declare_gate(conn, syn_gate(value=1.70), now=T0)  # E≈21% > 15%
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    assert conn.one("SELECT verdict FROM tensor_withheld_gates")["verdict"] == W.FAIL

    widened = syn_gate(value=1.70, tol=0.30)  # 想把 15% 放成 30% 洗成 PASS
    res = W.rejudge_gate(conn, "SYN-GATE-001", artifact=widened, now=T0 + 2 * DAY)
    assert res["verdict"] == W.FAIL and res["tampered"] is True
    assert res["reason_code"] == W.R_TOLERANCE_TAMPERED
    assert res["cleared"] is False
    row = conn.one("SELECT * FROM tensor_withheld_gates")
    assert row["tolerance"] == pytest.approx(0.15)   # 基线容差未变
    assert row["verdict"] == W.FAIL
    events = [r["event"] for r in conn.query(
        "SELECT event FROM tensor_withheld_audit ORDER BY seq")]
    assert "TAMPER_BLOCKED" in events
    assert W.verify_audit_chain(conn)["ok"] is True


def test_redeclare_cannot_rebaseline(conn):
    W.declare_gate(conn, syn_gate(), now=T0)
    res = W.declare_gate(conn, syn_gate(tol=0.30), now=T0 + DAY)
    assert res["tampered"] is True and res["verdict"] == W.FAIL
    assert conn.one("SELECT tolerance FROM tensor_withheld_gates")["tolerance"] == pytest.approx(0.15)


def test_db_trigger_blocks_direct_tolerance_update(conn):
    W.declare_gate(conn, syn_gate(), now=T0)
    with pytest.raises(sqlite3.Error):
        conn.raw.execute("UPDATE tensor_withheld_gates SET tolerance=0.30")  # noqa: S608
    with pytest.raises(sqlite3.Error):
        conn.raw.execute("UPDATE tensor_withheld_gates SET artifact_json='{}'")


def test_audit_table_is_append_only(conn):
    W.declare_gate(conn, syn_gate(), now=T0)
    with pytest.raises(sqlite3.Error):
        conn.raw.execute("UPDATE tensor_withheld_audit SET verdict='PASS'")
    with pytest.raises(sqlite3.Error):
        conn.raw.execute("DELETE FROM tensor_withheld_audit")


def test_audit_chain_detects_forged_row(conn):
    W.declare_gate(conn, syn_gate(), now=T0)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    assert W.verify_audit_chain(conn)["ok"] is True
    # 伪造一行\"PASS\"审计（INSERT 允许，但 prev_hash/row_hash 无法自洽）
    conn.raw.execute(
        "INSERT INTO tensor_withheld_audit "
        "(gate_id, seq, event, verdict, payload, prev_hash, row_hash, created_at) "
        "VALUES ('SYN-GATE-001', 99, 'REJUDGED', 'PASS', '{}', 'deadbeef', 'cafebabe', "
        "'2026-11-05T00:00:00Z')")
    chain = W.verify_audit_chain(conn, "SYN-GATE-001")
    assert chain["ok"] is False
    assert chain["broken"][0]["seq"] == 99


# --------------------------------------------------------------------------- #
# 5. 三态不被压成二态：证据不全 / 超范围 / 缺实测值仍是 WITHHELD
# --------------------------------------------------------------------------- #
def test_incomplete_provenance_stays_withheld(conn):
    W.declare_gate(conn, syn_gate(), now=T0)
    ds = syn_dataset(doi="")           # 无出处定位符
    ds["digitisation"]["method"] = ""   # 无数字化方法
    reg = W.register_reference(conn, ds, now=T0 + DAY)
    assert reg["usable_as_gate"] is False
    assert reg["validation_verdict"] == RR.WITHHELD
    row = conn.one("SELECT * FROM tensor_withheld_gates")
    assert row["verdict"] == W.WITHHELD
    assert row["verdict_reason"].startswith(W.R_REFERENCE_INCOMPLETE)
    assert row["cleared_at"] is None


def test_out_of_range_is_withheld_not_fail(conn):
    W.declare_gate(conn, syn_gate(at=9.0, value=2.5), now=T0)  # 表只覆盖到 x=5
    res = W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    assert res["rejudged"][0]["verdict"] == W.WITHHELD
    assert res["rejudged"][0]["reason_code"] == W.R_OUT_OF_RANGE


def test_missing_measurement_is_withheld(conn):
    art = syn_gate()
    art["requires_reference"]["measurement"] = {"at": 2.0}
    W.declare_gate(conn, art, now=T0)
    res = W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    assert res["rejudged"][0]["verdict"] == W.WITHHELD
    assert res["rejudged"][0]["reason_code"] == W.R_MEASUREMENT_MISSING


def test_regression_reopens_cleared_gate(conn):
    W.declare_gate(conn, syn_gate(value=1.41), now=T0)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    assert conn.one("SELECT verdict FROM tensor_withheld_gates")["verdict"] == W.PASS
    # 数据集修正（数字化复核后参考值上调到 1.75）→ 原 PASS 结论必须被推翻
    shifted = [[x, y + 0.35, s] for x, y, s in SYN_POINTS]
    W.register_reference(conn, syn_dataset(points=shifted), now=T0 + 2 * DAY)
    row = conn.one("SELECT * FROM tensor_withheld_gates")
    assert row["verdict"] == W.FAIL and row["cleared_at"] is None
    assert W.verify_audit_chain(conn)["ok"] is True


# --------------------------------------------------------------------------- #
# 6. KPI：份额 + 平均账龄，落 tensor_guide_tracker（小时桶幂等）
# --------------------------------------------------------------------------- #
def test_kpi_share_and_mean_age(conn):
    # 4 门：G1 在 t0 声明并在 t0+2d 清偿；G2 声明在 t0+2d 仍挂账；G3 声明在 t0+6d 仍挂账；G4 FAIL
    W.declare_gate(conn, syn_gate("SYN-G1", value=1.41), now=T0)
    W.declare_gate(conn, syn_gate("SYN-G2", ds_id="SYN-DS-002", value=1.41), now=T0 + 2 * DAY)
    W.declare_gate(conn, syn_gate("SYN-G3", ds_id="SYN-DS-003", value=1.41), now=T0 + 6 * DAY)
    W.declare_gate(conn, syn_gate("SYN-G4", ds_id="SYN-DS-004", value=1.70), now=T0)
    W.register_reference(conn, syn_dataset("SYN-DS-001"), now=T0 + 2 * DAY)
    W.register_reference(conn, syn_dataset("SYN-DS-004"), now=T0 + 2 * DAY)

    now = T0 + 10 * DAY
    kpi = W.compute_kpis(conn, now=now)
    assert kpi["gates_total"] == 4
    assert kpi["gates_open_withheld"] == 2          # G2 (8d) + G3 (4d)
    assert kpi[W.KPI_SHARE] == pytest.approx(0.5)
    assert kpi[W.KPI_AGE] == pytest.approx((8 + 4) / 2)
    assert kpi["mean_time_to_clear_days"] == pytest.approx(2.0)

    snap = W.snapshot_kpis(conn, organization_id=7, now=now)
    keys = {r["metric_key"]: r for r in snap["written"]}
    assert keys[W.KPI_SHARE]["metric_value"] == pytest.approx(0.5)
    assert keys[W.KPI_AGE]["metric_value"] == pytest.approx(6.0)
    assert keys[W.KPI_AGE]["target_value"] == pytest.approx(7.0)
    rows = conn.query("SELECT * FROM tensor_guide_tracker WHERE organization_id=7")
    assert len(rows) == 2

    # 同小时桶重跑：幂等 upsert（不新增行），数值刷新
    W.register_reference(conn, syn_dataset("SYN-DS-002"), now=now)
    W.snapshot_kpis(conn, organization_id=7, now=now + timedelta(minutes=5))
    rows2 = conn.query("SELECT * FROM tensor_guide_tracker WHERE organization_id=7")
    assert len(rows2) == 2
    share = [r for r in rows2 if r["metric_key"] == W.KPI_SHARE][0]
    assert share["metric_value"] == pytest.approx(1 / 4)


# --------------------------------------------------------------------------- #
# 7. 派单：(repo, dataset_id, age)，同数据集合并，工单号幂等
# --------------------------------------------------------------------------- #
def test_dispatch_groups_by_repo_dataset_and_orders_by_age(conn):
    W.declare_gate(conn, syn_gate("SYN-G1", "SYN-DS-001", repo="SYN-Repo-B"), now=T0)
    W.declare_gate(conn, syn_gate("SYN-G2", "SYN-DS-001", repo="SYN-Repo-B"), now=T0 + DAY)
    W.declare_gate(conn, syn_gate("SYN-G3", "SYN-DS-002", repo="SYN-Repo-A"), now=T0 + 3 * DAY)
    W.declare_gate(conn, syn_gate("SYN-G4", "SYN-DS-003", repo="SYN-Repo-A"), now=T0)

    orders = W.dispatch_unresolved(conn, now=T0 + 20 * DAY)
    assert [(o["repo"], o["dataset_id"]) for o in orders] == [
        ("SYN-Repo-A", "SYN-DS-002"), ("SYN-Repo-A", "SYN-DS-003"), ("SYN-Repo-B", "SYN-DS-001")]
    merged = orders[-1]
    assert merged["n_gates"] == 2 and merged["gate_ids"] == ["SYN-G1", "SYN-G2"]
    assert merged["age_days"] == pytest.approx(20.0)   # 取最老门年龄
    assert merged["priority"] == 1
    assert merged["action"] == W.ACTIONS[W.R_NO_REFERENCE]   # 动作口径随原因码走
    assert orders[0]["age_days"] == pytest.approx(17.0)

    again = W.dispatch_unresolved(conn, now=T0 + 20 * DAY)
    assert [o["order_id"] for o in again] == [o["order_id"] for o in orders]  # 幂等

    # 清偿后工单自动消失
    W.register_reference(conn, syn_dataset("SYN-DS-001"), now=T0 + 21 * DAY)
    left = W.dispatch_unresolved(conn, now=T0 + 21 * DAY)
    assert all(o["dataset_id"] != "SYN-DS-001" for o in left)
    assert len(left) == 2


def test_dispatch_action_follows_reason_code(conn):
    W.declare_gate(conn, syn_gate("SYN-G1", "SYN-DS-001", at=9.0), now=T0)
    W.register_reference(conn, syn_dataset("SYN-DS-001"), now=T0 + DAY)
    orders = W.dispatch_unresolved(conn, now=T0 + 2 * DAY)
    assert orders[0]["reason_code"] == W.R_OUT_OF_RANGE
    assert orders[0]["action"] == W.ACTIONS[W.R_OUT_OF_RANGE]


def test_dispatch_min_age_filter(conn):
    W.declare_gate(conn, syn_gate("SYN-G1", "SYN-DS-001"), now=T0)
    assert W.dispatch_unresolved(conn, now=T0 + DAY, min_age_days=7) == []
    assert len(W.dispatch_unresolved(conn, now=T0 + 8 * DAY, min_age_days=7)) == 1


# --------------------------------------------------------------------------- #
# 8. 与既有参考登记册口径兼容（同一张表喂 reference_registry.gate）
# --------------------------------------------------------------------------- #
def test_registered_dataset_is_accepted_by_reference_registry(conn):
    W.declare_gate(conn, syn_gate(), now=T0)
    ds = syn_dataset()
    W.register_reference(conn, ds, now=T0)

    validation = RR.validate_dataset(ds)
    assert validation["usable_as_gate"] is True
    assert validation["verdict"] == RR.PASS

    # 合成仿真曲线：正落在参考表上 ⇒ 参考登记册自身也判 PASS
    sim = [(x, y) for x, y, _ in SYN_POINTS]
    g = RR.gate(ds, sim)
    assert g["verdict"] == RR.PASS and g["max_rel_err"] == pytest.approx(0.0, abs=1e-12)

    stored = conn.one("SELECT * FROM tensor_reference_datasets WHERE dataset_id='SYN-DS-001'")
    payload = json.loads(stored["payload_json"])
    assert payload["digitisation"]["original_file_sha256"] == "a" * 64
    assert "10.9999/syn.0001" in stored["doi"]


def test_artifact_fingerprint_ignores_non_contract_noise():
    base = syn_gate()
    noisy = syn_gate()
    noisy["free_text_note"] = "不影响判决契约的注释"
    noisy["schema"] = W.SCHEMA_ID
    assert W.artifact_fingerprint(base) == W.artifact_fingerprint(noisy)
    drifted = syn_gate(tol=0.1500001)
    assert W.artifact_fingerprint(base) != W.artifact_fingerprint(drifted)


# --------------------------------------------------------------------------- #
# 9. 迁移与模块 DDL 的列级 parity（防止 PG 迁移与 sqlite 测试口径漂移）
# --------------------------------------------------------------------------- #
import re  # noqa: E402

MIGRATION = Path(__file__).resolve().parents[1] / "backend/migrations/113_withheld_gates.sql"


def _pg_columns(sql: str, table: str) -> list:
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.S)
    assert match, f"{table} 未在迁移中找到"
    cols = []
    for line in match.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith(("CONSTRAINT", "UNIQUE", "--")):
            continue
        cols.append(line.split()[0])
    return cols


def test_migration_columns_match_module_ddl(conn):
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in ("tensor_withheld_gates", "tensor_reference_datasets",
                  "tensor_withheld_audit"):
        live = [r["name"] for r in conn.query(f"PRAGMA table_info({table})")]
        assert _pg_columns(sql, table) == live, table
    # 反作弊结构必须在迁移里落地
    for token in ("trg_withheld_gates_contract_immutable", "trg_withheld_audit_no_update",
                  "trg_withheld_audit_no_delete", "withheld_gate_share",
                  "withheld_mean_age_days", "v_withheld_gates_open"):
        assert token in sql, token


# --------------------------------------------------------------------------- #
# 10. CLI（战役/派单的实际调用面）：声明→登记→KPI→派单→链校验 一次跑通
# --------------------------------------------------------------------------- #
def test_cli_end_to_end(tmp_path):
    db = tmp_path / "gates.db"
    art = tmp_path / "gate.json"
    art.write_text(json.dumps(syn_gate(), ensure_ascii=False), encoding="utf-8")
    dsf = tmp_path / "dataset.json"
    dsf.write_text(json.dumps(syn_dataset(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "cli.json"

    rc = W.main(["--db", str(db), "--declare", str(art), "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text())["declare"]["verdict"] == W.WITHHELD

    rc = W.main(["--db", str(db), "--register", str(dsf), "--kpi",
                 "--dispatch", "--verify", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["register"]["rejudged"][0]["verdict"] == W.PASS
    assert payload["kpi"]["written"][0]["metric_key"] == W.KPI_SHARE
    assert payload["dispatch"] == []          # 已清偿 → 无工单
    assert payload["audit"]["ok"] is True


# --------------------------------------------------------------------------- #
# 11. 演示场景自检：三态必须同时出现（防止"全 WITHHELD 的假 KPI 轨迹"）
# --------------------------------------------------------------------------- #
def test_demo_scenario_covers_all_three_states(tmp_path):
    pytest.importorskip("matplotlib")
    from tools.vv import withheld_gates_demo as DEMO

    out = DEMO.run(tmp_path / "demo.db", tmp_path)
    states = {g["verdict"] for g in out["gates"]}
    assert states == {W.PASS, W.FAIL, W.WITHHELD}
    assert out["audit_chain"]["ok"] is True
    assert len(out["kpi_series"]) == 13
    assert 0.0 < out["kpis_final"][W.KPI_SHARE] < 1.0
    assert out["kpis_final"]["mean_time_to_clear_days"] > 0.0
    assert len(out["dispatch"]) >= 1
    assert all(f["sha256"] for f in out["figures"])
