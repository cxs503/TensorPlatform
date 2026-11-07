#!/usr/bin/env python3
"""验收门（IN19-G20）的回归 + 负控测试。

两类用例：
* **真实判定**：跑真 bundle，锁定五项判据的实测数字（本波覆盖 8/8、两路复算自洽、
  快照过期但被状态演进解释、账本 548 条链 ok、四中心 44/44、S-N 单合法）。数字变了
  就是上游/平台变了，测试会挡住。
* **负控**：把 bundle 逐项篡改（改账本一行、改一条 S×E、删根因、S4 配 E1、
  目标全达成、拆掉派发矩阵的拒绝格、给未实现中心填 0 值、删掉某单的评价记录），
  断言对应检查必须翻 FAIL。验收门只会"变严"不会"变松"，是这份测试要守的东西。

跑法：``python3 -m pytest -q tools/ops/test_spec_impl_acceptance_gate.py``
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec_impl_acceptance_gate as gate  # noqa: E402

EVIDENCE = REPO_ROOT / "artifacts/in19_g20/evidence"
SN_DIR = REPO_ROOT / "artifacts/in19_g20/sn"
ACCEPTANCE = REPO_ROOT / "artifacts/spec_impl_acceptance.json"


def _clone(tmp_path: Path) -> Path:
    dest = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, dest)
    return dest


def _sn_clone(tmp_path: Path) -> Path:
    dest = tmp_path / "sn"
    shutil.copytree(SN_DIR, dest)
    return dest


def _run(ev_root: Path, sn_dir: Path = SN_DIR, out_path: Path | None = None):
    """跑门并把产物写到临时路径（绝不污染仓内的 artifacts 目录）。"""
    target = out_path or Path(tempfile.mkdtemp(prefix="gate_out_")) / "acceptance.json"
    doc = gate.run_gate(ev_root, sn_dir, target)
    return doc, {c["id"]: c for c in doc["checks"]}


# ─────────────────────────── 真实判定 ───────────────────────────
def test_real_bundle_all_five_criteria_pass():
    doc, checks = _run(EVIDENCE)
    for cid in ("E1", "E2", "E3", "E4", "E5"):
        assert checks[cid]["status"] == "PASS", (cid, checks[cid]["root_cause"])
    assert doc["verdict"] == "PASSED"
    assert doc["summary"]["failed"] == [] and doc["summary"]["passed"] == ["E1", "E2", "E3", "E4", "E5"]


def test_e1_coverage_is_complete_and_backfill_is_disclosed():
    _, checks = _run(EVIDENCE)
    m = checks["E1"]["measured"]
    assert m["succeeded_wave_missions"] == [5018, 5019, 5020, 5021, 5022, 5023, 5024, 5025]
    assert m["evaluated_wave_missions"] == m["succeeded_wave_missions"]
    assert m["coverage_ratio"] == 1.0 and m["missing_wave_missions"] == []
    assert m["pending_records"] == m["pending_with_reason_source"]
    # 补记必须被量化披露：created_at 落后 finished_at 的单数与最大滞后
    assert m["backfilled_missions"], "补记单必须被列出而不是被掩盖"
    assert m["max_record_created_lag_minutes"] > 5.0
    assert any("波后补记" in f for f in checks["E1"]["findings"])


def test_e1_recompute_two_way_consistent_and_snapshot_staleness_explained():
    _, checks = _run(EVIDENCE)
    rec = checks["E1"]["measured"]["recompute"]
    assert rec["two_way_consistent"] is True
    # 上游快照已被重评：本门必须"记下偏差"而不是把偏差抹平
    assert rec["snapshot_match"] is False
    assert rec["verdict"] == "stale_explained"
    assert "candidate" in rec["statuses_not_in_snapshot"]
    assert rec["max_abs_diff"] > 0
    assert any("快照已过期" in f for f in checks["E1"]["findings"])


def test_e2_real_case_has_both_sides():
    _, checks = _run(EVIDENCE)
    m = checks["E2"]["measured"]
    case = m["real_case"]
    assert case["real_contributors_evaluated"] >= 10
    assert (case["required_tier_policy"], case["required_tier_gradient"]) == ("L1", "L3")
    assert gate.rank_of_tier(case["required_tier_applied"]) == 3
    assert case["allowed"] and case["denied"], "真实案例必须两侧俱全才叫联动"
    assert len(case["allowed"]) + len(case["denied"]) == case["real_contributors_evaluated"]
    assert case["coverage_side"] == "both_sides"
    assert m["tier_dependent_matrix"] is True
    assert m["dispatch_matrix"]["S4"]["L3"] is False
    assert m["dispatch_matrix"]["S4"]["L4"] is True


def test_e3_ledger_chain_and_weekly_numbers():
    _, checks = _run(EVIDENCE)
    m = checks["E3"]["measured"]
    assert m["chain"]["ok"] is True
    assert m["ledger_entries"] == 548
    assert m["kind_counts"] == {"review": 372, "production": 176}
    w45 = [r for r in m["weekly_reports"] if r["iso_week"] == "2026-W45"][0]
    assert w45["declared"]["entries"] == w45["recomputed"]["entries"] == 548
    assert w45["declared"]["review_events"] == w45["recomputed"]["review_events"] == 372
    assert w45["declared"]["production_events"] == w45["recomputed"]["production_events"] == 176
    assert w45["mismatch"] == {}


def test_e4_sn_record_legal_and_honest():
    _, checks = _run(EVIDENCE)
    m = checks["E4"]["measured"]
    assert m["valid_records"] == 1
    rec = m["records"][0]
    assert (rec["grade"], rec["e_level"]) == ("S4", "E2")
    assert rec["honest_fail"] is True
    assert rec["result_status"] == "failed_honestly"
    assert rec["measured"]["targets_achieved"] == 0
    assert rec["measured"]["targets_attempted"] > 0
    assert "EP001_measurements.json" in m["skipped_non_records"]


def test_e5_distribution_unweighted_legality_and_centers():
    """分布不锁死绝对条数（平台是活系统），锁**不变量**：两路一致、Σ恒等、越梯为 0、
    候选/否决按 §7.1-9 剔除，且矩阵与从 bundle 独立重算的结果逐格相等。"""
    _, checks = _run(EVIDENCE)
    m = checks["E5"]["measured"]
    rows = gate.Bundle(EVIDENCE).fact("outcome_evaluations")
    confirmed = [r for r in rows if r.get("status") == "confirmed"]
    independent = [[sum(1 for r in confirmed
                        if (r.get("grade") or "?") == g and (r.get("e_level") or "?") == e)
                    for e in gate.E_LEVELS] for g in gate.GRADES]
    assert m["matrix_confirmed_counts"] == independent
    assert m["n_confirmed"] == len(confirmed)
    assert m["off_ladder_confirmed"] == 0
    assert m["sum_identity_holds"] is True
    assert m["sum_of_cells"] == m["n_confirmed"]
    assert m["two_way_recompute_consistent"] is True
    # 分布必须真的有量（否则"统计"是空转）：S1 与 S2 行都非零
    assert sum(m["matrix_confirmed_counts"][0]) > 0
    assert sum(m["matrix_confirmed_counts"][1]) > 0
    excluded = m["excluded_by_legality"]
    assert excluded["candidate_n"] > 0 and "S-N" in excluded["candidate_grades"]
    assert excluded["rejected_n"] > 0
    assert m["center_crosscheck"]["items_matched"] == m["center_crosscheck"]["items_total"] == 44
    innov = m["centers"]["innovation"]
    assert innov["implemented"] is False and innov["not_implemented_declared"] >= 1
    assert innov["fabricated_placeholder_metrics"] == []


def test_artifact_matches_fresh_run():
    """落库产物必须与当前 bundle 的复跑一致（防手改 JSON 粉饰结论）。"""
    stored = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
    fresh, _ = _run(EVIDENCE)
    assert stored["verdict"] == fresh["verdict"]
    assert {c["id"]: c["status"] for c in stored["checks"]} == {
        c["id"]: c["status"] for c in fresh["checks"]
    }
    assert stored["summary"]["failed"] == fresh["summary"]["failed"]


def test_gate_never_issues_writes():
    """验收门只判定：源码里不得出现任何写库 SQL（审计护栏）。"""
    src = Path(gate.__file__).read_text(encoding="utf-8")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER TABLE"):
        assert forbidden not in src, f"验收门不应包含写库语句: {forbidden}"


# ─────────────────────────── 负控（篡改必须翻 FAIL） ───────────────────────────
def test_negative_tampered_ledger_breaks_chain(tmp_path):
    ev = _clone(tmp_path)
    ledger = ev / "upstream/m5019/review_budget_ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[10]["duration_s"] = 999.0  # 改写历史行 = 篡改
    ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    doc, checks = _run(ev)
    assert checks["E3"]["status"] == "FAIL"
    assert checks["E3"]["measured"]["chain"]["ok"] is False
    assert checks["E3"]["measured"]["chain"]["broken_at"] == 10
    assert doc["verdict"] == "NOT_PASSED"


def test_negative_dropped_evaluation_record_cannot_be_patched(tmp_path):
    """删掉 5018 的评价记录 → 覆盖率跌破 100%，且门的判定不含"自动补齐"逻辑。"""
    ev = _clone(tmp_path)
    facts = json.loads((ev / "db_facts.json").read_text(encoding="utf-8"))
    rows = facts["facts"]["outcome_evaluations"]["rows"]
    facts["facts"]["outcome_evaluations"]["rows"] = [r for r in rows if str(r["mission_id"]) != "5018"]
    (ev / "db_facts.json").write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(ev)
    assert checks["E1"]["status"] == "FAIL"
    assert checks["E1"]["measured"]["coverage_ratio"] == pytest.approx(7 / 8)
    assert checks["E1"]["measured"]["missing_wave_missions"] == [5018]


def test_negative_untraceable_pending_fails_auditability(tmp_path):
    ev = _clone(tmp_path)
    facts = json.loads((ev / "db_facts.json").read_text(encoding="utf-8"))
    rows = facts["facts"]["outcome_evaluations"]["rows"]
    for row in rows:
        if row["status"] == "pending":
            row["reason_source"] = ""
            break
    (ev / "db_facts.json").write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(ev)
    assert checks["E1"]["status"] == "FAIL"
    assert checks["E1"]["measured"]["pending_records"] > checks["E1"]["measured"]["pending_with_reason_source"]


def test_negative_missing_root_cause_voids_sn_record(tmp_path):
    sn = _sn_clone(tmp_path)
    path = sn / "sn_ep001.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["root_cause"] = "没跑通"  # 过短 = 没留根因
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(EVIDENCE, sn)
    assert checks["E4"]["status"] == "FAIL"
    assert any("root_cause" in p for p in checks["E4"]["measured"]["records"][0]["problems"])


def test_negative_illegal_s4_e1_combo(tmp_path):
    sn = _sn_clone(tmp_path)
    path = sn / "sn_ep001.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["e_level"] = "E1"  # SPEC §7.1-9：S4 须 E>=2
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(EVIDENCE, sn)
    assert checks["E4"]["status"] == "FAIL"
    assert any("S4 须 E>=2" in p for p in checks["E4"]["measured"]["records"][0]["problems"])


def test_negative_all_targets_achieved_is_not_a_failure(tmp_path):
    sn = _sn_clone(tmp_path)
    path = sn / "sn_ep001.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["attempt"]["measured"]["targets_achieved"] = doc["attempt"]["measured"]["targets_attempted"]
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(EVIDENCE, sn)
    assert checks["E4"]["status"] == "FAIL"
    assert any("不是失败单" in p for p in checks["E4"]["measured"]["records"][0]["problems"])


def test_negative_flat_dispatch_matrix_kills_tier_dependence(tmp_path):
    ev = _clone(tmp_path)
    path = ev / "upstream/m5026/dispatch_matrix.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["allowed_cells"], doc["denied_cells"] = 25, 0  # 全放行 = 联动不存在
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(ev)
    assert checks["E2"]["status"] == "FAIL"
    assert checks["E2"]["measured"]["tier_dependent_matrix"] is False


def test_negative_mock_zero_in_unimplemented_center(tmp_path):
    ev = _clone(tmp_path)
    path = ev / "upstream/m5023/center_payloads_snapshot.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    metrics = doc["payloads"]["innovation"]["metrics"]
    metrics[0]["status"] = "not_implemented"
    metrics[0]["value"] = 0  # 缺失被填成 0 = Mock 冒充
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(ev)
    assert checks["E5"]["status"] == "FAIL"
    assert checks["E5"]["measured"]["centers"]["innovation"]["fabricated_placeholder_metrics"]


def test_negative_gate_detects_missing_center(tmp_path):
    ev = _clone(tmp_path)
    path = ev / "upstream/m5023/center_payloads_snapshot.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["payloads"]["quality"]  # 少一个中心 = 四中心不齐
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, checks = _run(ev)
    assert checks["E5"]["status"] == "FAIL"
    assert checks["E5"]["measured"]["centers"]["quality"]["present"] is False