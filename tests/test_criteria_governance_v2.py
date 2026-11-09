"""m8263 · 阈值注册表 v2 与派生一致性门的验收测试（C1–C7 + CI 钉桩）。

所有夹具量都是合成量（线性算子/求积的已知阶），零领域语义；领域出处只以
``source_ref`` 字符串形式出现在数据工件里。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.criteria_governance import drift_gate_v2 as drift  # noqa: E402
from tools.criteria_governance import engine_v2, live_harvest, run_v2, schema_v2  # noqa: E402

ARTIFACTS = ROOT / "artifacts" / "criteria_governance_v2"


@pytest.fixture(scope="module")
def registry():
    return run_v2.build_registry()


@pytest.fixture(scope="module")
def findings(registry):
    return registry.validate()


@pytest.fixture(scope="module")
def outcomes(registry, findings):
    return [engine_v2.evaluate_entry(registry.get(cid)) for cid in findings.registered]


@pytest.fixture(scope="module")
def report():
    return run_v2.main(write=False)


# --------------------------------------------------------------------------- #
# C1 schema 完备性：现网抽样 ≥30 条、source_type 零缺失、heuristic 显式不可裁
# --------------------------------------------------------------------------- #
def test_c1_live_sample_at_least_thirty_entries(registry, findings):
    live = [cid for cid in findings.registered if cid.startswith("LIVE-")]
    assert len(live) >= 30
    for cid in live:
        entry = registry.get(cid)
        assert entry["source_ref"]["locator"]
        assert entry["source_ref"]["file_sha256"]
        assert entry["review_date"]


def test_c1_source_type_missing_is_zero(registry, report):
    ledger = registry.coverage_ledger()
    assert ledger["source_type_missing"] == 0
    assert ledger["source_type_missing_fraction"] == 0.0
    assert report["c1_schema"]["live_sample_total"] >= 30


def test_c1_heuristic_entries_are_explicitly_non_adjudicable(registry, findings):
    heuristic = [cid for cid in findings.registered
                 if registry.get(cid)["source_type"] == "heuristic"]
    assert heuristic, "live sampling should surface heuristic thresholds"
    for cid in heuristic:
        verdict = schema_v2.adjudicability(registry.get(cid))
        assert verdict["adjudicable"] is False
        assert verdict["reason"] == "heuristic_expert_judgement"
        assert cid not in findings.adjudicable


def test_c1_registry_rejects_incomplete_entry():
    broken = {
        "criterion_id": "",
        "quantity": "",
        "comparator": "=<",
        "threshold": "0.1",
        "source_type": "",
        "source_ref": {"kind": "doi", "identifier": "not-a-doi"},
        "depends_on": [{"param": "", "exponent": "two"}],
        "derivation": {"method": "analytic_identity", "reference": "",
                       "expression": "C*h**2 + unknown_symbol"},
        "review_date": "09/11/2026",
    }
    reasons = schema_v2.validate_entry(broken)
    for expected in ("criterion_id_missing", "quantity_missing", "comparator_invalid",
                     "threshold_not_numeric", "source_type_missing", "review_date_invalid"):
        assert any(reason == expected or reason.startswith(expected + ":") for reason in reasons), \
            f"{expected} not reported: {reasons}"
    assert any(reason.startswith("source_ref_doi_malformed") for reason in reasons)
    assert any(reason.startswith("depends_on") for reason in reasons)
    assert any(reason.startswith("expression_symbols_undeclared") for reason in reasons)


# --------------------------------------------------------------------------- #
# C2 派生一致性：观测阶由引擎自己重算，偏差 ≤ 0.3
# --------------------------------------------------------------------------- #
def test_c2_observed_exponent_deviation_within_bound(outcomes):
    refinement = [o for o in outcomes if o.get("exponent")]
    assert len(refinement) >= 3
    for outcome in refinement:
        assert outcome["exponent"]["deviation"] <= engine_v2.EXPONENT_TOLERANCE_DEFAULT
        assert outcome["exponent"]["consistent"] is True


def test_c2_engine_recomputes_exponent_independently(registry):
    cid = "SYN-C1"
    entry = dict(registry.get(cid))
    honest = engine_v2.evaluate_entry(entry)
    forged = dict(entry)
    forged["derivation"] = dict(entry["derivation"])
    forged["derivation"]["declared_exponent"] = 3.0
    fake = engine_v2.evaluate_entry(forged)
    assert honest["exponent"]["consistent"] is True
    assert fake["exponent"]["measured"] == pytest.approx(honest["exponent"]["measured"], rel=1e-12)
    assert fake["exponent"]["consistent"] is False
    assert fake["verdict"] == "FAIL"


# --------------------------------------------------------------------------- #
# C3 漂移检出：阈值被改而出处不变 = 红（20/20），且钉桩哈希随之变化
# --------------------------------------------------------------------------- #
def test_c3_tamper_campaign_detects_all(registry):
    result = drift.run_tamper_campaign(registry.entries, n=20, factor=1.1)
    assert result["n"] == 20
    assert result["detected_total"] == 20
    assert result["detection_rate"] == 1.0
    assert all("threshold_changed_without_source_change" in item["reasons"]
               for item in result["injections"])


def test_c3_threshold_change_without_source_ref_is_red():
    entry = {
        "criterion_id": "SYN-PIN",
        "quantity": "synthetic_quantity",
        "comparator": "<=",
        "threshold": 1e-3,
        "unit": "1",
        "source_type": "analytic",
        "source_ref": {"kind": "analytic_identity", "identifier": "synthetic:order-2"},
        "depends_on": [{"param": "h", "exponent": 2.0}],
        "derivation": {"method": "refinement_scan", "expression": "C*h**2",
                       "declared_exponent": 2.0, "reference": "synthetic"},
        "probe": {"name": "linear_operator_refinement", "params": {"stencil": 2}},
        "review_date": "2026-11-09",
    }
    reference = drift.artifact_view([entry])
    mutated = dict(entry)
    mutated["threshold"] = entry["threshold"] * 1.1
    candidate = drift.artifact_view([mutated])
    verdict = drift.check(reference, candidate)
    assert verdict[0].red is True
    assert "threshold_changed_without_source_change" in verdict[0].reasons
    # 钉桩随之变化：CI 只比语义哈希
    assert drift.pin_view(reference) != drift.pin_view(candidate)


def test_c3_committed_pins_match_registry(registry):
    """CI 门：注册表语义哈希必须与已提交钉桩一致（阈值被改而未更新出处 → 本测试先红）。"""
    pins_path = ARTIFACTS / "drift_pins.json"
    assert pins_path.is_file(), "drift pins must be committed for CI"
    committed = json.loads(pins_path.read_text(encoding="utf-8"))
    current = drift.pin_view(drift.artifact_view(registry.entries))
    assert committed["schema"] == current["schema"]
    assert committed["criteria"] == current["criteria"]


# --------------------------------------------------------------------------- #
# C4 假阳性：只改注释/排版不得判红（0/20）；出处与阈值同改必须放行
# --------------------------------------------------------------------------- #
def test_c4_cosmetic_campaign_has_no_false_positive(registry):
    result = drift.run_cosmetic_campaign(registry.entries, n=20)
    assert result["n"] == 20
    assert result["false_positive_total"] == 0
    assert result["false_positive_rate"] == 0.0


def test_c4_source_tracked_revision_is_allowed(registry):
    probe = drift.source_tracked_revision_probe(registry.entries)
    assert probe["passed"] is True
    assert "source_tracked_revision" in probe["reasons"]


def test_c4_removed_criterion_is_red(registry):
    view = drift.artifact_view(registry.entries)
    trimmed = {"schema": view["schema"],
               "criteria": dict(list(view["criteria"].items())[1:])}
    verdicts = drift.check(view, trimmed)
    removed = [v for v in verdicts if "criterion_removed" in v.reasons]
    assert len(removed) == 1 and removed[0].red


# --------------------------------------------------------------------------- #
# C5 重推导闭环：threshold(h) 显式表达式在 h/2、h/4 上比值 ∈ [0.5, 2]
# --------------------------------------------------------------------------- #
def test_c5_closure_holds_for_three_criteria(outcomes):
    closed = [o for o in outcomes if o.get("closure")]
    assert len(closed) >= 3
    for outcome in closed:
        assert outcome["closure"]["closure_ok"] is True
        levels = {lvl["level"]: lvl for lvl in outcome["closure"]["levels"]}
        for label in ("h/2", "h/4"):
            assert 0.5 <= levels[label]["ratio"] <= 2.0


def test_c5_law_scaling_matches_declared_order(registry):
    entry = registry.get("SYN-C2")
    expression = entry["derivation"]["expression"]
    constant = 1.0  # 只验"表达式对 h 的标度律"，与 C 的具体取值无关
    base = 0.1
    high = schema_v2.evaluate_threshold_law(expression, {"h": base, "C": constant,
                                                        "SF": entry["derivation"]["safety_factor"]})
    half = schema_v2.evaluate_threshold_law(expression, {"h": base / 2, "C": constant,
                                                        "SF": entry["derivation"]["safety_factor"]})
    quarter = schema_v2.evaluate_threshold_law(expression, {"h": base / 4, "C": constant,
                                                           "SF": entry["derivation"]["safety_factor"]})
    assert half / high == pytest.approx(2 ** -4, rel=1e-12)
    assert quarter / high == pytest.approx(2 ** -8, rel=1e-12)


def test_c5_expression_whitelist_blocks_arbitrary_code():
    with pytest.raises(schema_v2.ThresholdLawError):
        schema_v2.evaluate_threshold_law("__import__('os').system('true')", {})
    with pytest.raises(schema_v2.ThresholdLawError):
        schema_v2.evaluate_threshold_law("C*h**2", {"C": 1.0})


# --------------------------------------------------------------------------- #
# C6 覆盖率账本
# --------------------------------------------------------------------------- #
def test_c6_coverage_ledger_numbers(registry, report):
    ledger = registry.coverage_ledger()
    assert ledger["sourced_fraction"] == 1.0
    assert 0.0 < ledger["derivable_fraction"] < 1.0
    assert ledger["adjudicable_total"] <= ledger["registered_total"]
    assert report["c6_coverage"]["passed"] is True
    assert report["ledger"]["by_source_type"]["heuristic"] == ledger["heuristic_total"]


def test_c6_derivable_requires_probe_and_chain(registry):
    live = [e for e in registry.entries if str(e.get("criterion_id")).startswith("LIVE-")]
    assert live and all(not engine_v2.RegistryV2.is_derivable(e) for e in live)
    assert engine_v2.RegistryV2.is_derivable(registry.get("SYN-C1"))


# --------------------------------------------------------------------------- #
# C7 负控：阈值 = 实测误差 → 必须判 empirical 且禁止进入可裁集合
# --------------------------------------------------------------------------- #
def test_c7_fitted_threshold_is_demoted_and_excluded():
    control = run_v2.negative_control_fit_to_measurement()
    assert control["passed"] is True
    assert control["fit_ratio"] <= engine_v2.FIT_TO_MEASUREMENT_TOL
    assert control["source_type_before"] == "analytic"
    assert control["source_type_after"] == "empirical"
    assert control["adjudicable_after"] is False
    assert control["in_adjudicable_set_after"] is False
    assert control["adjudicable_reason_after"] == "threshold_fitted_to_measurement"


def test_c7_identity_criteria_are_not_false_demoted(registry):
    """恒等式判据（== ，阈值是精确目标）实测恰好等于阈值，不能误判为拟合。"""
    identity = [e for e in registry.entries if e.get("comparator") == "=="]
    assert identity, "registry should keep exact-identity criteria"
    for entry in identity:
        outcome = engine_v2.evaluate_entry(entry)
        assert not outcome.get("demoted_to_empirical")


# --------------------------------------------------------------------------- #
# 机制自检：来源绑定探针 / 抽样计划覆盖 / 零领域语义 / 工件完整性
# --------------------------------------------------------------------------- #
def test_source_constant_probe_reads_live_value(registry, findings):
    live = [registry.get(cid) for cid in findings.registered if cid.startswith("LIVE-")][:5]
    for entry in live:
        observation = engine_v2.ALL_PROBES["source_constant"](dict(entry["probe"]["params"]))
        assert observation["value"] == pytest.approx(float(entry["threshold"]), rel=1e-12)


def test_sampling_plan_coverage_is_reported():
    coverage = live_harvest.plan_coverage()
    assert coverage["declared"] >= 30
    assert coverage["harvested"] >= 30
    # 无法解析的源文件必须如实记账（不静默丢）
    assert all("reason" in item for item in coverage["unparseable"])


def test_zero_domain_semantics(report):
    scan = report["domain_scan"]
    assert scan["clean"] is True, scan["hits"]


def test_artifacts_and_gates(report):
    assert report["all_passed"] is True
    for key in ("registry", "report", "coverage_ledger", "drift_pins",
                "drift_controls", "markdown_report"):
        assert (ROOT / report["artifacts"][key]).exists(), key
    figures = sorted((ARTIFACTS / "figures").glob("*.png"))
    assert len(figures) >= 4


def test_runner_is_deterministic(registry):
    again = run_v2.build_registry()
    first = drift.pin_view(drift.artifact_view(registry.entries))
    second = drift.pin_view(drift.artifact_view(again.entries))
    assert first == second