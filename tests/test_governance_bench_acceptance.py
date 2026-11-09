"""IN99-N10 端到端验收：C1..C5 基准 + CLI 冒烟（判据数字一律取自 metrics.json）。"""
from __future__ import annotations

import json

import pytest

from ops.taskplane.governance import __main__ as cli
from ops.taskplane.governance import bench as bench_mod


@pytest.fixture(scope="module")
def metrics(tmp_path_factory):
    out = tmp_path_factory.mktemp("in99_n10")
    return bench_mod.run(out, with_figures=False)


def test_c1_dedup_recall_and_fpr(metrics):
    c1 = metrics["acceptance"]["C1_dedup_recall_fpr"]
    assert c1["pass"], c1
    assert c1["recall"] >= 0.90 and c1["fpr"] <= 0.10
    hold = metrics["dedup"]["holdout_frozen_theta"]
    assert hold["tp"] + hold["fn"] == 12 and hold["fp"] + hold["tn"] == 8
    # 阈值确实冻结自 dev 划分，且模块默认值即该冻结值
    assert metrics["dedup"]["meta"]["dev_selected_theta"] == metrics["dedup"]["meta"]["default_theta"]


def test_c2_dag_cycle_detection_complete(metrics):
    totals = metrics["dag"]["totals"]
    assert metrics["acceptance"]["C2_dag_cycle_detection"]["pass"]
    assert totals["n_cyclic"] > 3000 and totals["n_cyclic_detected"] == totals["n_cyclic"]
    assert totals["n_false_positive"] == 0
    assert metrics["dag"]["cycle_path_example"]


def test_c3_gate_rejects_all_mutants_with_codes(metrics):
    gate = metrics["gate"]
    assert metrics["acceptance"]["C3_gate_reject_with_items"]["pass"]
    assert gate["n_mutants"] == 18 and gate["n_rejected_with_expected_code"] == 18
    assert gate["baseline_accepted"]
    for row in gate["mutants"]:
        assert row["rejected"] and row["all_expected_present"], row


def test_c4_ledger_query_and_audit_trail(metrics):
    ledger = metrics["ledger"]
    assert metrics["acceptance"]["C4_ledger_query_and_audit"]["pass"]
    assert ledger["chain"]["ok"] and ledger["tamper_detected"]["code"] == "CONTENT_TAMPERED"
    assert ledger["capability_index"]["gov.dedup_gate"]["highest_grade"] == "S3"
    assert [c["code"] for c in ledger["cases"]] == [
        "REGISTERED", "REGISTERED", "REGISTERED",
        "NOT_ACCEPTED", "CRITERIA_FAILED", "IDEMPOTENT"]


def test_c5_negative_control(metrics):
    nc = metrics["gate"]["negative_control"]
    assert metrics["acceptance"]["C5_negative_control"]["pass"]
    assert nc["registry_collision_rejected"] and nc["registry_collision_codes"] == ["EXT_ID_COLLISION"]
    assert nc["registry_does_not_block_compliant"]


def test_metrics_json_is_self_contained(tmp_path, metrics):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["all_pass"] and set(loaded["acceptance"]) == {
        "C1_dedup_recall_fpr", "C2_dag_cycle_detection", "C3_gate_reject_with_items",
        "C4_ledger_query_and_audit", "C5_negative_control"}


# ─────────────────────────── CLI ───────────────────────────

def test_cli_seed_accept_and_reject(tmp_path, capsys):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(bench_mod.compliant_seed(), ensure_ascii=False), encoding="utf-8")
    assert cli.main(["seed", "--seed", str(seed), "--out", str(tmp_path / "r.json")]) == 0
    bad = bench_mod.apply_mutant(bench_mod.compliant_seed(), "M13_dag_cycle")
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["seed", "--seed", str(bad_path)]) == 2
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["ok"] and report["dag"]["ok"]


def test_cli_dedup_and_ledger(tmp_path):
    props = tmp_path / "props.json"
    props.write_text(json.dumps({"proposals": [
        {"id": "A", "summary": "提案去重门：先对摘要做归一化与近义词归一，再用字符级 IDF 余弦、"
                              "长公共子序列占比与骨架集合相似度加权成综合分，越界即判近重复拒收；"
                              "同批提案再做兄弟聚类，每簇只保留信息量最大的代表，其余标近重复。",
         "deliverables": ["code"]},
        {"id": "B", "summary": "缓存失效按分区版本号做键前缀，版本递增即自然失效旧键，后台按存活时间"
                              "清理，命中率与过期时长做成度量上报；换分区只改版本号不动缓存内容。",
         "deliverables": ["code"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["dedup", "--proposals", str(props), "--out", str(tmp_path / "d.json")]) == 0
    dedup_report = json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))
    assert dedup_report["n_in"] == 2

    ledger = tmp_path / "L.sqlite"
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({
        "mission_id": 1, "capability_id": "gov.cli", "category": "defect_fix", "accepted": True,
        "acceptance": [{"id": "C1", "passed": True}, {"id": "C2", "passed": True},
                       {"id": "C3", "passed": True}],
        "baseline": {"metric": "err", "reference": 0.10, "measured": 0.02},
        "artifacts": ["x.py"],
    }, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["ledger", "register", "--ledger", str(ledger), "--record", str(rec)]) == 0
    assert cli.main(["ledger", "verify", "--ledger", str(ledger)]) == 0
    assert cli.main(["ledger", "query", "--ledger", str(ledger), "--capability", "gov.cli"]) == 0


def test_cli_input_error_exit_code(tmp_path):
    assert cli.main(["seed", "--seed", str(tmp_path / "missing.json")]) == 3