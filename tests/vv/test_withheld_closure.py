"""WITHHELD 欠账闭环成熟化测试（IN51-W5-N09）。

三条机器判据 + 机制反例，全部用 ``SYN-*`` 合成域名（平台仓零领域语义铁律）：

A. 登记库↔门禁**双向绑定**：覆盖率 = 100%，三类漂移（UNBOUND/GHOST/STALE）可检测、可修复；
B. 数字化**不确定度预算**：自动路径 vs 手工台账相对误差 < 1%，相关分量不得双计，
   分辨力不足（k·u_c/D > tol）只能 WITHHELD，**不得**被不确定度洗成 PASS；
C. **缺口自动派单**：上游优先决策表 + 幂等工单 + 标注语料准确率 > 95%、零误派。

运行：python3 -m pytest tests/vv/test_withheld_closure.py -q
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.vv import withheld_closure as C  # noqa: E402
from tools.vv import withheld_gates as W  # noqa: E402

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)
MIGRATION = (Path(__file__).resolve().parents[2]
             / "backend" / "migrations" / "114_withheld_closure_binding.sql")


def _pg_columns(sql: str, table: str) -> list:
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.S)
    assert match, f"{table} 未在迁移中找到"
    cols = []
    for line in match.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith(("CONSTRAINT", "UNIQUE", "PRIMARY", "--")):
            continue
        cols.append(line.split()[0])
    return cols


@pytest.fixture()
def conn():
    c = W.connect(":memory:")
    C.ensure_closure_schema(c)
    yield c
    c.close()


def syn_dataset(ds_id: str = "SYN-DS-001", template: str = "complete") -> dict:
    return C.synthetic_dataset(ds_id, template)


def declare(conn, gate_id: str = "SYN-G001", ds_id: str = "SYN-DS-001",
            *, at: float = 0.5, value=1.575, tol: float = 0.15, k: float = 2.0,
            u_exp: float = 0.01, repo: str = "SYN-Repo-A", when: datetime = T0,
            u_num: float = 0.01) -> dict:
    meas = {"at": at, "u_exp": u_exp}
    if value is not None:
        meas["value"] = value
    art = C.gate_artifact(gate_id, ds_id, repo=repo, measurement=meas, tolerance=tol,
                          k_factor=k, declared_at=when)
    art["requires_reference"]["budget"] = {
        "components": [{"name": "u_num", "u": u_num, "kind": "relative"}],
        "correlations": [],
    }
    return W.declare_gate(conn, art)


# --------------------------------------------------------------------------- #
# A. 双向绑定：建边 / 状态推进 / 三类漂移 / 修复
# --------------------------------------------------------------------------- #
def test_binding_created_on_declare_and_state_pending(conn):
    declare(conn)
    rev = C.reverse_edges(conn)
    assert len(rev) == 1 and rev[0]["gate_id"] == "SYN-G001"
    assert rev[0]["state"] == C.B_PENDING  # 数据集还没登记 ⇒ 债在数据集侧
    cov = C.binding_coverage(conn)
    # 边两侧一致（100%），但登记完成度为 0：两个指标不混算
    assert cov["binding_coverage"] == 1.0 and cov["bidirectional_ok"] is True
    assert cov["registration_coverage"] == 0.0


def test_registration_flips_binding_to_bound(conn):
    declare(conn)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    rev = C.reverse_edges(conn)
    assert rev[0]["state"] == C.B_BOUND  # 触发器在登记时自动推进状态
    cov = C.binding_coverage(conn)
    assert cov["binding_coverage"] == 1.0 and cov["registration_coverage"] == 1.0


def test_unbound_drift_detected_and_repaired(conn):
    declare(conn)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    conn.execute("DELETE FROM tensor_reference_bindings WHERE gate_id = 'SYN-G001'")
    conn.commit()
    cov = C.binding_coverage(conn)
    assert cov["binding_coverage"] < 1.0
    assert cov["drift_counts"][C.DRIFT_UNBOUND] == 1
    assert cov["bidirectional_ok"] is False
    repair = C.reconcile_bindings(conn, now=T0 + 2 * DAY)
    assert [a["action"] for a in repair["actions"]] == ["BIND"]
    assert C.binding_coverage(conn)["bidirectional_ok"] is True


def test_ghost_and_stale_drift_detected(conn):
    declare(conn)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    conn.execute("UPDATE tensor_reference_bindings SET artifact_sha256 = 'deadbeef'"
                 " WHERE gate_id = 'SYN-G001'")
    conn.execute(
        """INSERT INTO tensor_reference_bindings
               (gate_id, dataset_id, artifact_sha256, state, bound_at, updated_at)
           VALUES ('SYN-G-GHOST', 'SYN-DS-GHOST', 'deadbeef', 'PENDING', ?, ?)""",
        (W.iso(T0), W.iso(T0)))
    conn.commit()
    cov = C.binding_coverage(conn)
    assert cov["drift_counts"][C.DRIFT_STALE] == 1
    assert cov["drift_counts"][C.DRIFT_GHOST] == 1
    C.reconcile_bindings(conn, now=T0 + 2 * DAY)
    cov2 = C.binding_coverage(conn)
    assert cov2["drift_counts"] == {C.DRIFT_UNBOUND: 0, C.DRIFT_GHOST: 0, C.DRIFT_STALE: 0}
    assert cov2["binding_coverage"] == 1.0


def test_gate_delete_cascades_binding(conn):
    declare(conn)
    conn.execute("DELETE FROM tensor_withheld_gates WHERE gate_id = 'SYN-G001'")
    conn.commit()
    assert C.reverse_edges(conn) == []


def test_reconcile_is_idempotent(conn):
    declare(conn)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    C.reconcile_bindings(conn, now=T0 + 2 * DAY)
    again = C.reconcile_bindings(conn, now=T0 + 3 * DAY)
    assert again["n_actions"] == 0


def test_dataset_reverse_lookup_reads_index_not_gate_table(conn):
    declare(conn, "SYN-G001", "SYN-DS-001")
    declare(conn, "SYN-G002", "SYN-DS-001")
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    assert C.dataset_reverse_gates(conn, "SYN-DS-001") == ["SYN-G001", "SYN-G002"]
    # 索引被破坏时反查必须跟着断（证明登记册侧不是靠扫门表伪造反向视图）
    conn.execute("DELETE FROM tensor_reference_bindings WHERE gate_id = 'SYN-G002'")
    conn.commit()
    assert C.dataset_reverse_gates(conn, "SYN-DS-001") == ["SYN-G001"]


# --------------------------------------------------------------------------- #
# B. 不确定度预算：公式 / 相关性 / 双路复算 / 分辨力
# --------------------------------------------------------------------------- #
def _comps(*pairs):
    return [{"name": n, "u_declared": u, "kind": "absolute", "u_abs": u, "source": "SYN"}
            for n, u in pairs]


def test_auto_matches_exact_manual():
    comps = _comps(("SYN-U1", 0.0123), ("SYN-U2", 0.0456), ("SYN-U3", 0.0078))
    auto = C.auto_u_combined(comps)
    exact = C.manual_u_combined(comps, digits=15)
    assert abs(auto - exact) / exact < 1e-12


def test_hand_ledger_parity_under_one_percent():
    parity = C.evaluate_budget_parity(C.budget_parity_corpus(n=24), mc_n=20_000)
    assert parity["max_parity_rel_error_hand_ledger"] < C.BUDGET_PARITY_TARGET
    assert parity["pass"] is True


def test_correlated_components_are_not_double_counted():
    comps = _comps(("SYN-U1", 1.0), ("SYN-U2", 1.0))
    # ρ=+1 完全正相关 ⇒ u_c = u1 + u2 = 2（若把 (a,b)/(b,a) 双计会得 sqrt(6)）
    assert C.auto_u_combined(comps, [["SYN-U1", "SYN-U2", 1.0]]) == pytest.approx(2.0)
    assert C.manual_u_combined(comps, [["SYN-U1", "SYN-U2", 1.0]]) == pytest.approx(2.0)


def test_negative_correlation_reduces_uncertainty():
    comps = _comps(("SYN-U1", 1.0), ("SYN-U2", 1.0))
    assert C.auto_u_combined(comps, [["SYN-U1", "SYN-U2", -1.0]]) == pytest.approx(0.0)


def test_invalid_correlation_rejected():
    with pytest.raises(ValueError):
        C.auto_u_combined(_comps(("SYN-U1", 1.0), ("SYN-U2", 1.0)),
                          [["SYN-U1", "SYN-U2", 1.5]])


def test_budget_band_uses_expanded_uncertainty_and_floor():
    comps = _comps(("SYN-U1", 0.03))
    budget = C.compute_budget(comps, [], k=2.0, denominator=1.5, tolerance=0.15)
    assert budget["u_expanded"] == pytest.approx(0.06)
    assert budget["band_uncertainty"] == pytest.approx(0.06 / 1.5)
    assert budget["band"] == pytest.approx(0.15)
    # 分母取 max(|ref|, floor)：参考值很小时不得让相对误差爆掉
    small = C.compute_budget(comps, [], k=2.0, denominator=0.0, tolerance=0.15)
    assert small["denominator"] > 0.0


def test_monte_carlo_agrees_within_three_sigma():
    cases = C.budget_parity_corpus(n=12)
    parity = C.evaluate_budget_parity(cases, mc_n=40_000)
    assert parity["mc_within_3stderr"] is True
    assert parity["max_mc_rel_error"] < 0.02


def test_resolution_limited_gate_is_withheld_not_pass():
    """核心纪律：不确定度带大于容差时，不得把超差结果洗成 PASS。"""
    verdict = C.judge_with_budget(0.20, tolerance=0.15, band_uncertainty=0.60,
                                  resolution_ok=False)
    assert verdict == {"verdict": W.WITHHELD, "reason_code": C.R_INSUFFICIENT_RESOLUTION}
    # 对照：同一误差在分辨力充足时就是 FAIL
    assert C.judge_with_budget(0.20, tolerance=0.15, band_uncertainty=0.02,
                               resolution_ok=True)["verdict"] == W.FAIL
    # 容差带内仍是 PASS
    assert C.judge_with_budget(0.10, tolerance=0.15, band_uncertainty=0.60,
                               resolution_ok=False)["verdict"] == W.PASS


def test_collect_components_from_registry_and_missing(conn):
    declare(conn, value=1.575)
    ds = syn_dataset()
    ds["digitisation"]["uncertainty"] = {"u_dig": 0.02, "type": "relative"}
    W.register_reference(conn, ds, now=T0 + DAY)
    row = W.connect(conn.raw) if False else conn.one(
        "SELECT * FROM tensor_withheld_gates WHERE gate_id = 'SYN-G001'")
    gathered = C.collect_budget_components(conn, row, ref=1.5)
    names = {c["name"]: c for c in gathered["components"]}
    assert names["u_dig"]["u_abs"] == pytest.approx(0.03)   # 2% × 1.5
    assert names["u_exp"]["u_abs"] == pytest.approx(0.01)   # 绝对口径原样
    assert names["u_num"]["u_abs"] == pytest.approx(0.015)  # 1% × 1.5
    assert names["u_interp"]["u_abs"] == pytest.approx(0.003)


def test_missing_components_are_reported_not_silently_zero(conn):
    declare(conn, value=1.575)
    W.register_reference(conn, syn_dataset("SYN-DS-001", "no_udig"), now=T0 + DAY)
    row = conn.one("SELECT * FROM tensor_withheld_gates WHERE gate_id = 'SYN-G001'")
    gathered = C.collect_budget_components(conn, row, ref=1.5)
    assert "u_dig" in gathered["missing"]


def test_budget_ledger_is_append_only(conn):
    declare(conn, value=1.575)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    out = C.rejudge_with_budget(conn, "SYN-G001", now=T0 + 2 * DAY)
    assert out["verdict"] == W.PASS
    assert C.latest_budget(conn, "SYN-G001") is not None
    with pytest.raises(Exception):
        conn.execute("UPDATE tensor_uncertainty_budgets SET u_combined = 0.0")
        conn.commit()


def test_resolution_gap_gate_stays_withheld(conn):
    declare(conn, value=1.8)          # E = 0.2 > tol
    W.register_reference(conn, syn_dataset("SYN-DS-001", "weak"), now=T0 + DAY)
    out = C.rejudge_with_budget(conn, "SYN-G001", now=T0 + 2 * DAY)
    assert out["verdict"] == W.WITHHELD
    assert out["reason_code"] == C.R_INSUFFICIENT_RESOLUTION
    assert out["budget"]["resolution_ok"] is False


# --------------------------------------------------------------------------- #
# C. 缺口分类 / 派单 / 准确率
# --------------------------------------------------------------------------- #
def test_healthy_gate_has_no_gap_and_honest_fail_is_not_a_debt(conn):
    declare(conn, "SYN-G001", "SYN-DS-001", value=1.575)   # E=+0.05 → PASS
    declare(conn, "SYN-G002", "SYN-DS-002", value=2.25)    # E=+0.50 → FAIL（诚实 FAIL）
    for ds in ("SYN-DS-001", "SYN-DS-002"):
        W.register_reference(conn, syn_dataset(ds), now=T0 + DAY)
    C.rejudge_all_with_budget(conn, now=T0 + 2 * DAY)
    gaps = C.classify_gaps(conn, now=T0 + 3 * DAY, include_binding=False)
    assert gaps == []


def test_gap_precedence_upstream_first(conn):
    # 数据集未登记 + 缺实测值：先还数据集侧的债
    declare(conn, "SYN-G001", "SYN-DS-001", value=None)
    gaps = C.classify_gaps(conn, now=T0 + DAY, include_binding=False)
    assert [g["gap_class"] for g in gaps] == [C.G_NO_DATASET]


def test_provenance_gap_lists_missing_fields(conn):
    declare(conn, value=1.575)
    W.register_reference(conn, syn_dataset("SYN-DS-001", "no_doi"), now=T0 + DAY)
    gaps = C.classify_gaps(conn, now=T0 + DAY + timedelta(hours=1), include_binding=False)
    assert gaps[0]["gap_class"] == C.G_PROVENANCE
    assert "doi|url" in (gaps[0]["evidence"]["registration_missing"] or [])


def test_measurement_range_and_orphan_gaps(conn):
    declare(conn, "SYN-G001", "SYN-DS-001", value=None)          # 缺实测
    declare(conn, "SYN-G002", "SYN-DS-002", at=0.9, value=1.5)   # 量程覆盖不到
    W.register_reference(conn, syn_dataset("SYN-DS-001"), now=T0 + DAY)
    W.register_reference(conn, syn_dataset("SYN-DS-002", "narrow"), now=T0 + DAY)
    W.register_reference(conn, syn_dataset("SYN-DS-ORPHAN"), now=T0 + DAY)
    gaps = C.classify_gaps(conn, now=T0 + DAY + timedelta(hours=1), include_binding=False)
    got = {g.get("gate_id") or g.get("dataset_id"): g["gap_class"] for g in gaps}
    assert got["SYN-G001"] == C.G_MEASUREMENT
    assert got["SYN-G002"] == C.G_RANGE
    assert got["SYN-DS-ORPHAN"] == C.G_ORPHAN


def test_tamper_detected_from_audit_chain_not_verdict_column(conn):
    W.register_reference(conn, syn_dataset(), now=T0)  # 数据集先就位
    declare(conn, value=1.575, tol=0.10)
    declare(conn, value=1.575, tol=0.30)               # 事后放宽 tol = 篡改
    row = conn.one("SELECT * FROM tensor_withheld_gates WHERE gate_id = 'SYN-G001'")
    # IN50 的 declare_gate 只写审计、不覆盖判决列 ⇒ 判决列可能仍是 PASS
    assert row["verdict"] == W.PASS
    assert C.tampered_gates(conn) == {"SYN-G001"}
    gaps = C.classify_gaps(conn, now=T0 + DAY, include_binding=False)
    assert gaps[0]["gap_class"] == C.G_TAMPER
    # 自动重判不得把篡改洗白
    skipped = C.rejudge_with_budget(conn, "SYN-G001", now=T0 + 2 * DAY)
    assert skipped["skipped"] is True
    # 人工裁定撤销后，标记消失
    C.clear_tamper(conn, "SYN-G001", actor="SYN", reason="SYN-adjudication")
    assert C.tampered_gates(conn) == set()


def test_stale_gate_detected_after_late_registration(conn):
    declare(conn, value=1.575)
    W.register_reference(conn, syn_dataset(), now=T0 + DAY)
    C.rejudge_all_with_budget(conn, now=T0 + 2 * DAY)
    W.register_reference(conn, syn_dataset(), now=T0 + 3 * DAY, rejudge=False)
    gaps = C.classify_gaps(conn, now=T0 + 4 * DAY, include_binding=False)
    assert [g["gap_class"] for g in gaps] == [C.G_STALE]


def test_dispatch_orders_are_idempotent_and_prioritised(conn):
    declare(conn, "SYN-G001", "SYN-DS-001")
    declare(conn, "SYN-G002", "SYN-DS-002")
    gaps = C.classify_gaps(conn, now=T0 + 5 * DAY, include_binding=False)
    orders_a = C.dispatch_gap_orders(gaps, now=T0 + 5 * DAY)
    orders_b = C.dispatch_gap_orders(gaps, now=T0 + 5 * DAY)
    assert [o["order_id"] for o in orders_a] == [o["order_id"] for o in orders_b]
    assert all(o["order_id"].startswith("WO-") for o in orders_a)
    assert all(o["action"] == C.GAP_ACTIONS[o["gap_class"]] for o in orders_a)
    assert orders_a == sorted(orders_a, key=lambda o: (o["priority"], o["repo"], o["target"]))


def test_dispatch_min_age_filter(conn):
    declare(conn, "SYN-G001", "SYN-DS-001")
    gaps = C.classify_gaps(conn, now=T0 + timedelta(hours=1), include_binding=False)
    assert C.dispatch_gap_orders(gaps, now=T0 + timedelta(hours=1), min_age_days=7) == []


def test_corpus_dispatch_accuracy_above_target(conn):
    specs = C.build_corpus_specs()
    C.materialize_corpus(conn, specs, t0=T0)
    gaps = C.classify_gaps(conn, now=T0 + 5 * DAY)
    gold = {str(s["gate_id"] or s["dataset_id"]): s["expected"] for s in specs}
    report = C.evaluate_dispatch_accuracy(gold, gaps)
    assert report["accuracy"] > C.DISPATCH_ACCURACY_TARGET
    assert report["false_orders"] == []
    assert report["pass"] is True


def test_in50_baseline_is_strictly_worse(conn):
    """改进前/后对照：同一批场景，IN50 规则必须明显劣于闭环口径。"""
    specs = C.build_corpus_specs()
    C.materialize_corpus(conn, specs, t0=T0)
    gaps = C.classify_gaps(conn, now=T0 + 5 * DAY)
    gold = {str(s["gate_id"] or s["dataset_id"]): s["expected"] for s in specs}
    after = C.evaluate_dispatch_accuracy(gold, gaps)["accuracy"]
    baseline = C.in50_baseline(specs, t0=T0)
    assert baseline["reverse_lookup_supported"] is False      # IN50 无反向索引
    assert baseline["uncertainty_washed_pass"] == 6           # 6 门被不确定度洗成 PASS
    assert baseline["dispatch_accuracy"] < after
    assert baseline["gaps_detected"] == 27 < len(gaps) == 43


# --------------------------------------------------------------------------- #
# 结构 / 迁移 / CLI
# --------------------------------------------------------------------------- #
def test_migration_columns_match_module_ddl(conn):
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in ("tensor_reference_bindings", "tensor_uncertainty_budgets"):
        live = [r["name"] for r in conn.query(f"PRAGMA table_info({table})")]
        assert _pg_columns(sql, table) == live, table
    for token in ("trg_withheld_gate_bind_ins", "trg_withheld_gate_bind_del",
                  "trg_uncertainty_budgets_no_update", "trg_uncertainty_budgets_no_delete",
                  "v_reference_binding_drift", "INSUFFICIENT_RESOLUTION"):
        assert token in sql, token


def test_cli_writes_artifacts_and_passes(tmp_path):
    rc = C.main(["--out", str(tmp_path), "--no-figures", "--mc-n", "20000"])
    assert rc == 0
    summary = json.loads((tmp_path / "closure_summary.json").read_text(encoding="utf-8"))
    assert summary["overall"] == "PASS"
    for name in ("binding_coverage.json", "budget_parity.json", "dispatch_accuracy.json",
                 "dispatch_orders.json", "gaps.json", "corpus.json", "budget_rows.json",
                 "drift_selftest.json"):
        assert (tmp_path / name).exists(), name
    assert summary["criteria"]["A_binding_coverage"]["pass"] is True
    assert summary["criteria"]["B_uncertainty_budget_parity"]["pass"] is True
    assert summary["criteria"]["C_dispatch_accuracy"]["pass"] is True
    assert summary["orders_idempotent"] is True
    assert summary["audit_chain_ok"] is True
    assert summary["drift_selftest"]["detected_all_three"] is True


def test_budget_parity_target_is_one_percent():
    assert C.BUDGET_PARITY_TARGET == 0.01
    assert C.DISPATCH_ACCURACY_TARGET == 0.95
    assert C.COVERAGE_TARGET == 1.0
    assert C.TOLERANCE_STANDARD == 0.15
    assert math.isclose(C.GAP_PRECEDENCE[C.G_NO_DATASET],
                        C.GAP_PRECEDENCE[C.G_NO_DATASET])