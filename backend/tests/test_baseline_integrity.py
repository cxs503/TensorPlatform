"""基准库投影审计门（m1407 收编，适配正典 v3.0）。

本文件只测 ``tools/audit/baseline_integrity.py`` 这个**审计工具**本身：它必须

* 在正典数据集（backend/data/benchmark_reference.json v3.0）上给出可复核的
  PASS，并且每条算例的恒等式偏差都可读出具体数值；
* 在注入腐坏数据时**必然转红**（C1 Ct↔Rt、C2 Froude 各有一条 FAIL 用例）；
* 区分「未声明的单位混用」（FAIL）与「记录里已声明尺度基准的混用」（WARN）；
* 把「容差放大到不可能失败的旁路门」判出来，但对显式声明 gate_enabled=False
  的 honest-FAIL 算例记为 WARN（其判决在消费侧被 WITHHELD）。

范围说明：m1407 分支另附的 ship_proxy 参考表重构（把手抄 R_T 改为 Ct 恒等式
派生 + 在表内直接改判 gate_enabled）与正典冲突（正典把 300% 容差作为
honest-FAIL 历史记录保留；该分支的测试还会把端点默认值从 :8001 回退到
:8002），故**未整文件收编**；本文件因此不引用那个重构 API。fake-gate 的消费侧
修复已按正典口径落地为 ``ship_proxy.benchmark_validate`` 对 gate_enabled=False
输出 WITHHELD。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE / "tools" / "audit"))

import baseline_integrity as audit  # noqa: E402

DATASET = WORKTREE / "backend" / "data" / "benchmark_reference.json"


# --------------------------------------------------------------------------- #
# 1. 工具在正典数据集上自洽（可复核、可重跑）
# --------------------------------------------------------------------------- #
def test_repository_dataset_passes_audit():
    report = audit.run(DATASET)
    assert report["summary"]["verdict"] == "PASS", report["summary"]
    assert report["summary"]["failing_checks"] == []
    # 通过不等于"没问题"：全船算例缺实测湿表面积，只能给 PARTIAL
    assert report["summary"]["partial"] >= 8


def test_verified_rows_reproduce_ct_identity():
    """正典里带实测湿表面积的行，Ct↔Rt 必须复算到 <1%。"""
    report = audit.run(DATASET)
    verified = [c for c in report["case_results"]
                if c["checks"]["C1_ct_rt"]["status"] == "PASS"]
    assert {c["case_id"] for c in verified} == {
        "SUBOFF-01", "SUBOFF-02", "SUBOFF-03", "SUBOFF-04"}, verified
    for case in verified:
        assert case["checks"]["C1_ct_rt"]["deviation_pct"] < 1.0, case["case_id"]


def test_audit_gate_can_fail_on_corrupt_rt(tmp_path):
    """注入 Rt 腐坏（÷6）必须让 C1 转红。"""
    data = json.loads(DATASET.read_text())
    idx = next(i for i, c in enumerate(data["cases"]) if c["case_id"] == "SUBOFF-01")
    data["cases"][idx]["experimental_rt_n"] /= 6.0
    bad = tmp_path / "benchmark_reference.json"
    bad.write_text(json.dumps(data))
    report = audit.run(bad)
    assert report["summary"]["verdict"] == "FAIL"
    row = next(c for c in report["case_results"] if c["case_id"] == "SUBOFF-01")
    assert row["checks"]["C1_ct_rt"]["status"] == "FAIL"


def test_audit_gate_can_fail_on_wrong_froude(tmp_path):
    """声明 Fr 与声明 L 不自洽（暗示 L≈181 m）必须让 C2 转红。"""
    data = json.loads(DATASET.read_text())
    dtmb = next(c for c in data["cases"] if c["case_id"] == "DTMB5415-01")
    # 历史缺陷的复现条件：保留 Fr=0.061，但速度写回实船尺度 2.572 m/s。
    # 二者只有在 L≈181 m 时才能同时成立（模型实长 5.72 m），故必然转红。
    dtmb["speed_ms"] = 2.572
    bad = tmp_path / "benchmark_reference.json"
    bad.write_text(json.dumps(data))
    report = audit.run(bad)
    c2 = next(c for c in report["case_results"]
              if c["case_id"] == "DTMB5415-01")["checks"]["C2_froude"]
    assert c2["status"] == "FAIL"
    assert abs(c2["implied_length_m"] - 181.2) < 1.0


# --------------------------------------------------------------------------- #
# 2. 单位混用：未声明 FAIL / 声明过 WARN
# --------------------------------------------------------------------------- #
def test_undocumented_kn_to_ms_mix_fails(tmp_path):
    data = json.loads(DATASET.read_text())
    case = next(c for c in data["cases"] if c["case_id"] == "KVLCC2-02")
    case.pop("speed_basis", None)
    case.pop("speed_kn_basis", None)
    case.pop("legacy_values", None)
    case["speed_kn"] = 4.0  # 与 speed_ms=7.717 明显不自洽，且无任何说明
    bad = tmp_path / "benchmark_reference.json"
    bad.write_text(json.dumps(data))
    report = audit.run(bad)
    c3 = next(c for c in report["case_results"]
              if c["case_id"] == "KVLCC2-02")["checks"]["C3_units"]
    assert c3["status"] == "FAIL"


def test_documented_scale_mix_warns():
    """正典行声明了 speed_basis（模型尺度由 Fr 反算），偏差判 WARN 而非 FAIL。"""
    data = json.loads(DATASET.read_text())
    case = next(c for c in data["cases"] if c["case_id"] == "DTMB5415-01")
    assert case.get("speed_basis")
    report = audit.run(DATASET)
    c3 = next(c for c in report["case_results"]
              if c["case_id"] == "DTMB5415-01")["checks"]["C3_units"]
    assert c3["status"] == "WARN"
    assert "declared scale mix" in c3["reason"]


# --------------------------------------------------------------------------- #
# 3. 门禁策略：不会失败的门禁不是门禁
# --------------------------------------------------------------------------- #
def test_gate_policy_passes_on_15pct_cases():
    report = audit.run(DATASET)
    rows = {r["case"]: r for r in report["gate_policy"]}
    assert rows["resistance/kvlcc2_full"]["status"] == "PASS"
    assert rows["resistance/kvlcc2_full"]["tolerance_pct"] <= audit.POLICY_MAX_TOLERANCE_PCT


def test_honest_fail_case_is_not_a_gate():
    """SUBOFF 保留 300% 历史容差，但必须显式 gate_enabled=False（WARN，不给 PASS）。"""
    report = audit.run(DATASET)
    row = next(r for r in report["gate_policy"] if r["case"] == "resistance/suboff_model")
    assert row["gate_enabled"] is False
    assert row["status"] == "WARN"
    assert "withheld" in row["reason"]


@pytest.mark.parametrize("tol", [20.0, 300.0])
def test_enabled_gate_above_policy_fails(tol):
    rows = audit.audit_tolerances(
        {"resistance": {"over_tol": {"tolerance_pct": tol, "gate_enabled": True}}})
    assert rows[0]["status"] == "FAIL"