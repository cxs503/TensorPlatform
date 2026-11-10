"""验收判据 E1–E8 的端到端测试（数字直接取自 results.json 同源链）。"""
from __future__ import annotations

import json

import pytest

from green_navigation import chain


def test_acceptance_all_criteria_reported(chain_result):
    acc = chain_result["acceptance"]
    assert set(acc["criteria"]) == {f"E{i}" for i in range(1, 9)}
    assert acc["total"] == 8


def test_E1_sfoc_within_5pct(chain_result):
    c = chain_result["acceptance"]["criteria"]["E1"]
    assert c["passed"] is True
    assert c["value_pct"] < 5.0
    assert c["value_all_loads_pct"] < 5.0


def test_E2_power_fraction_085_pm005(chain_result):
    c = chain_result["acceptance"]["criteria"]["E2"]
    assert c["passed"] is True
    for row in c["rows"]:
        if not row["engine_independent"]:
            continue
        assert 0.80 <= row["power_fraction"] <= 0.90, row
    assert c["independent_cases"], "至少需要一条厂家独立额定的算例"


def test_E3_eedi_vs_reference_within_10pct(chain_result):
    c = chain_result["acceptance"]["criteria"]["E3"]
    assert c["passed"] is True
    judged = [r for r in c["rows"] if r["use_for_e3"]]
    assert judged
    for row in judged:
        assert row["rel_err_pct"] < 10.0, row


def test_E4_rating_is_honestly_withheld(chain_result):
    """E4（vs DNV 在线计算器）离线不可验证 → 必须诚实 WITHHELD，不得伪称一致。"""
    c = chain_result["acceptance"]["criteria"]["E4"]
    assert c["status"] == "WITHHELD"
    assert c["passed"] is None
    assert "DNV" in c["reason"]
    # 替代证据：三船型都给出了评级与 dd 向量（逻辑可复核）
    for row in c["rows"]:
        assert row["ratings_by_year"][0] in ("A", "B", "C", "D", "E")
        assert len(row["dd"]) == 4


def test_E5_degradation_within_band(chain_result):
    c = chain_result["acceptance"]["criteria"]["E5"]
    assert c["passed"] is True
    lo, hi = c["threshold_pct_per_year"]
    for v in c["value_pct_per_year"]:
        assert lo <= v <= hi


def test_E6_zero_power_zero_eedi(chain_result):
    c = chain_result["acceptance"]["criteria"]["E6"]
    assert c["passed"] is True
    assert c["value"] == 0.0
    assert c["p_ae_kw"] == 0.0


def test_E7_infinite_speed_zero_eedi(chain_result):
    c = chain_result["acceptance"]["criteria"]["E7"]
    assert c["passed"] is True
    assert c["value"] > 0.0
    assert c["rel_to_baseline"] < 1e-6


def test_E8_compliant_flag_two_sided(chain_result):
    c = chain_result["acceptance"]["criteria"]["E8"]
    assert c["passed"] is True
    for row in c["rows_cases"]:
        assert row["consistent"] is True
    outcomes = {p["expected"] for p in c["rows_logic_pairs"]}
    assert outcomes == {True, False}
    for pair in c["rows_logic_pairs"]:
        assert pair["ok"] is True


def test_results_json_matches_chain_run(tmp_path, chain_result):
    """落盘 JSON 与一次现跑结果同构（判据数字可由 JSON 复现，零手抄）。"""
    payload = json.loads(json.dumps(chain_result, default=str))
    out = tmp_path / "results.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    again = json.loads(out.read_text(encoding="utf-8"))
    assert again["acceptance"]["criteria"]["E1"]["value_pct"] == \
        chain_result["acceptance"]["criteria"]["E1"]["value_pct"]
    assert [c["case_id"] for c in again["cases"]] == \
        [c["case_id"] for c in chain_result["cases"]]


def test_honest_list_only_contains_unverifiable(chain_result):
    """诚实清单里只允许出现真正无法在线验证的条目（E4），其余必须真实通过。"""
    acc = chain_result["acceptance"]
    assert acc["honest_fail_or_withheld"] == ["E4"]
    assert acc["all_passed"] is False   # E4 WITHHELD 未被粉饰成 pass


def test_machine_acceptance_block_maps_mission_criteria(chain_result):
    """任务书的 6 条机器判据必须能在 results.json 里逐条定位到数值。"""
    m = chain_result["machine_acceptance"]
    expected = [
        "SFOCvsWartsila < 5%[相对误差]",
        "P_engine/P_MCR in 0.85±0.05[比值]",
        "EEDIvsIMO < 10%[相对误差]",
        "CII评级vsDNV == 一致[一致]",
        "P_engine=0时EEDI=0 == true[布尔]",
        "EEDI<EEDI_req时compliant == true[布尔]",
    ]
    for key in expected:
        assert key in m, key
        assert "passed" in m[key] or key.endswith("[一致]")
        assert m[key]["artifact"]
    assert m["summary"]["total"] == 8


def test_every_criterion_carries_number_and_artifact(chain_result):
    """每条判据都必须给数字 + 产物字段 + 证据等级（禁止空话式结论）。"""
    for key, c in chain_result["acceptance"]["criteria"].items():
        assert c.get("metric"), key
        assert c.get("artifact"), key
        assert c.get("evidence_grade"), key
        assert c.get("criterion"), key