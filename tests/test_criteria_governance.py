"""P171W1-N08 判据治理成熟化集成：机器可裁验收 C1–C6 的 pytest 证明。

覆盖：
* C1 注册表覆盖（14/14 入册、source_type 缺失 0、无剔除）
* C2 派生一致性（引擎自行重算观测阶；并证明它**不橡皮图章**：伪造声明阶会被判 FAIL）
* C3 漂移门（20/20 篡改检出，0/20 假阳性，含阈值定向变体）
* C4 成熟度四维 ≥ 0.7
* C5 负控（heuristic 判据混入 analytic 集合被剔除 + 告警，队列计数不上升）
* C6 治理前后 KPI 对比存在且数字一致
* 数值核自检（斜槽/体积四极子观测阶、群公理、不可约重数）
* 运行器确定性（同输入两跑逐字段相同）
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.criteria_governance import drift_gate, kernels  # noqa: E402
from tools.criteria_governance.derivation import DerivationEngine  # noqa: E402
from tools.criteria_governance.registry import ThresholdRegistry, compare_op  # noqa: E402
from tools.criteria_governance.run_integration import SPEC_FILES, run  # noqa: E402

DATA_DIR = REPO_ROOT / "data" / "criteria_governance"


@pytest.fixture(scope="module")
def report():
    """跑一次完整集成（不写盘，避免测试污染工件）。"""
    return run(write=False)


@pytest.fixture(scope="module")
def registry():
    return ThresholdRegistry.from_specs(SPEC_FILES.values())


# --------------------------------------------------------------------------- #
# C1 阈值注册表覆盖
# --------------------------------------------------------------------------- #


def test_c1_all_declared_criteria_registered(report):
    reg = report["registry"]
    assert reg["expected_total"] == 14, "N04+N05 应共 14 条判据"
    assert reg["registered_total"] == 14
    assert reg["coverage"] == pytest.approx(1.0)
    assert reg["quarantined_total"] == 0
    assert reg["source_type_missing"] == 0
    assert reg["source_type_completeness"] == pytest.approx(1.0)


def test_c1_source_type_partition_and_traceability(report):
    reg = report["registry"]
    assert reg["analytic_total"] + reg["empirical_total"] + reg["heuristic_total"] == 14
    assert reg["analytic_total"] == 11
    assert reg["empirical_total"] == 3
    assert reg["traceability_rate"] == pytest.approx(1.0)
    assert report["registry"]["validation"]["untraceable"] == []
    # 派生链确有层级（不是全部根节点）
    depths = report["registry"]["validation"]["chain_depth"]
    assert max(depths.values()) >= 1


def test_c1_registry_rejects_incomplete_entry():
    """缺 probe / 缺 threshold 的条目应被剔除而非静默登记。"""
    reg = ThresholdRegistry()
    reg.add_spec({
        "bundle_id": "synthetic",
        "criteria": [
            {"id": "S-1", "title": "t", "statement": "s", "cohort": "analytic",
             "source_type": "analytic", "metric": "m",
             "threshold": {"op": "<=", "value": 0.1},
             "derivation": {"method": "analytic_identity", "reference": "r", "parents": []},
             "probe": {"name": "lattice_moment_isotropy", "params": {"lattice": "D3Q27"}}},
            {"id": "S-2", "title": "missing probe", "statement": "s", "cohort": "analytic",
             "source_type": "analytic", "metric": "m",
             "threshold": {"op": "<=", "value": 0.1},
             "derivation": {"method": "analytic_identity", "reference": "r", "parents": []}},
        ],
    })
    findings = reg.validate()
    assert "S-1" in findings.registered
    quarantined = {row["id"]: row["reasons"] for row in findings.quarantined}
    assert "S-2" in quarantined
    assert any("probe_missing" in reason for reason in quarantined["S-2"])


# --------------------------------------------------------------------------- #
# C2 派生一致性
# --------------------------------------------------------------------------- #


def test_c2_refinement_exponent_deviation(report):
    der = report["derivation"]
    assert der["refinement_total"] == 2
    assert der["refinement_consistent"] == 2
    assert der["max_exponent_deviation"] <= 0.3

    for cid, row in report["criteria"].items():
        ref = row.get("refinement")
        if ref is None:
            continue
        assert abs(ref["measured_exponent"] - ref["declared_exponent"]) <= ref["tolerance"]
        assert row["verdict"] == "PASS"


def test_c2_engine_recomputes_exponent_independently():
    """引擎不得采信探针自报的指数：给一组人为放慢的误差序列，指数必须变化。"""
    hs = [1 / 16, 1 / 32, 1 / 64, 1 / 128]
    p2 = kernels.fit_decay_exponent(hs, [h ** 2 for h in hs])
    p1 = kernels.fit_decay_exponent(hs, [h for h in hs])
    assert p2 == pytest.approx(2.0, abs=1e-9)
    assert p1 == pytest.approx(1.0, abs=1e-9)


def test_c2_engine_fails_on_forged_declared_exponent(registry):
    """负控：把 N04-C4 的声明阶改成 3.0，引擎必须判 FAIL（证明判据不是橡皮图章）。"""
    forged = copy.deepcopy(registry.get("N04-C4"))
    forged["derivation"]["declared_exponent"] = 3.0
    reg = ThresholdRegistry()
    reg._entries["N04-C4"] = forged
    reg._spec_ids = ["N04-C4"]
    reg._spec_count = 1
    engine = DerivationEngine(reg)
    result = engine.evaluate("N04-C4")
    assert result.kind == "refinement"
    assert result.exponent_consistent is False
    assert result.verdict == "FAIL"


# --------------------------------------------------------------------------- #
# C3 CI 漂移门
# --------------------------------------------------------------------------- #


def test_c3_drift_gate_detection_and_false_positive(report):
    gate = report["drift_gate"]
    assert gate["tamper_total"] == 20
    assert gate["tamper_detected"] == 20
    assert gate["detection_rate"] == pytest.approx(1.0)
    assert gate["clean_total"] == 20
    assert gate["false_positive"] == 0
    assert gate["false_positive_rate"] == pytest.approx(0.0)
    assert gate["threshold_targeted_trials"] > 0, "篡改变体必须覆盖阈值字段"


def test_c3_threshold_flip_detected_by_hash_and_value():
    payload = json.loads(SPEC_FILES["N04"].read_text(encoding="utf-8"))
    pins = drift_gate.pin({"spec": payload})
    mutated = copy.deepcopy(payload)
    mutated["criteria"][0]["threshold"]["value"] = 0.5  # 阈值被偷偷放宽
    findings = drift_gate.check({"spec": mutated}, pins)
    assert findings[0].drifted
    assert findings[0].hash_match is False
    assert any(row["reason"] == "value_changed" for row in findings[0].value_mismatches)


def test_c3_unmodified_payload_is_quiet():
    payload = json.loads(SPEC_FILES["N05"].read_text(encoding="utf-8"))
    pins = drift_gate.pin({"spec": payload})
    findings = drift_gate.check({"spec": json.loads(drift_gate.canonical_json(payload))}, pins)
    assert not findings[0].drifted


# --------------------------------------------------------------------------- #
# C4 成熟度四维
# --------------------------------------------------------------------------- #


def test_c4_maturity_dimensions_above_gate(report):
    mat = report["maturity"]
    assert set(mat["dimensions"]) == {"coverage", "derivability", "drift_detection",
                                      "false_positive_safety"}
    for name, row in mat["dimensions"].items():
        assert row["score"] >= 0.7, f"维度 {name} 得分 {row['score']} 低于 0.7"
        assert row["denominator"] > 0
    assert mat["verdict"] == "PASS"


# --------------------------------------------------------------------------- #
# C5 负控
# --------------------------------------------------------------------------- #


def test_c5_heuristic_impostor_quarantined(report):
    neg = report["negative_control"]
    assert neg["impostor_source_type"] == "heuristic"
    assert neg["quarantined"] is True
    assert neg["accepted_into_analytic_set"] is False
    assert neg["analytic_count_unchanged"] is True
    assert neg["warnings"], "剔除必须附带告警"
    assert neg["verdict"] == "PASS"


def test_c5_poisoned_analytic_method_also_rejected():
    """第二类负控：source_type 谎报 analytic，但派生方法是经验判断 → 同样剔除。"""
    reg = ThresholdRegistry()
    reg.add_spec({
        "bundle_id": "synthetic",
        "criteria": [{
            "id": "S-3", "title": "forged analytic", "statement": "s",
            "cohort": "analytic", "source_type": "analytic", "metric": "m",
            "threshold": {"op": "<=", "value": 1.0},
            "derivation": {"method": "expert_judgement", "reference": "老师傅经验",
                           "parents": [], "tolerance": 0.0},
            "probe": {"name": "lattice_moment_isotropy", "params": {"lattice": "D3Q27"}},
        }],
    })
    findings = reg.validate()
    assert findings.registered == []
    assert findings.quarantined[0]["id"] == "S-3"
    assert any("analytic_set_poisoned" in reason for reason in findings.quarantined[0]["reasons"])
    assert any("负控" in warning for warning in findings.warnings)


# --------------------------------------------------------------------------- #
# C6 治理前后 KPI
# --------------------------------------------------------------------------- #


def test_c6_before_after_kpi(report):
    kpi = report["before_after_kpi"]
    assert kpi["before"]["criteria_registered"] == 0
    assert kpi["before"]["coverage"] == pytest.approx(0.0)
    assert kpi["after"]["criteria_registered"] == 14
    assert kpi["after"]["coverage"] == pytest.approx(1.0)
    assert kpi["after"]["adjudicable_rate"] == pytest.approx(1.0)
    assert kpi["after"]["drift_gate"] is True
    assert kpi["delta"]["criteria_registered"] == 14
    assert report["acceptance"]["checks"]["C6_before_after_kpi"]["pass"] is True


# --------------------------------------------------------------------------- #
# 数值核自检
# --------------------------------------------------------------------------- #


def test_kernel_skew_slot_observed_order_is_two():
    scan = kernels.refinement_scan(lambda n: kernels.skew_slot_laplacian_error(n, 0.5),
                                   [16, 32, 64, 128], 1.0)
    assert scan["observed_exponent"] == pytest.approx(2.0, abs=0.3)
    # 剪切必须真的改变误差常数（否则"斜槽"名不副实）
    base = kernels.refinement_scan(lambda n: kernels.skew_slot_laplacian_error(n, 0.0),
                                   [16, 32, 64, 128], 1.0)
    ratio = (kernels.error_constant(scan["hs"], scan["errors"], 2.0)
             / kernels.error_constant(base["hs"], base["errors"], 2.0))
    assert ratio > 1.5


def test_kernel_quadrupole_observed_order_and_exact_value():
    scan = kernels.refinement_scan(
        lambda n: kernels.rel_max_error(kernels.quadrupole_midpoint(n)[0],
                                        kernels.quadrupole_midpoint_exact()),
        [16, 32, 64, 128], 1.0)
    assert scan["observed_exponent"] == pytest.approx(2.0, abs=0.3)
    quad, mass = kernels.quadrupole_midpoint(128)
    exact = kernels.quadrupole_midpoint_exact()
    assert mass == pytest.approx(1.0, abs=1e-3)
    assert kernels.rel_max_error(quad, exact) < 1e-3


def test_kernel_group_axioms_and_irreducible_multiplicity():
    ops = kernels.signed_permutation_group(3)
    assert len(ops) == 48
    assert kernels.group_closure_residual(ops) == 0.0
    assert kernels.invariant_subspace_dimension(ops, 3) == 1
    assert kernels.invariance_residual(ops, kernels.isotropic_rank4(3), 4) < 1e-12


def test_compare_op_semantics():
    assert compare_op(1.0, "==", 1.0, tol=1e-9)
    assert not compare_op(1.001, "<=", 1.0)
    assert compare_op(1.0000000001, "==", 1.0, tol=1e-9)


# --------------------------------------------------------------------------- #
# 报告整体与确定性
# --------------------------------------------------------------------------- #


def test_acceptance_all_pass(report):
    acc = report["acceptance"]
    assert acc["failing"] == []
    assert acc["verdict"] == "PASS"


def test_runner_is_deterministic():
    first = run(write=False)
    second = run(write=False)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_artifacts_declared(report):
    arts = report["artifacts"]
    for key in ("report", "pins", "maturity_radar", "refinement_scan", "markdown"):
        assert key in arts