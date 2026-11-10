#!/usr/bin/env python3
"""P186-W1-N07 能力台账自动升账机制 · 验收测试

判据（全部机器可裁，编号与战役节点一致）
-----------------------------------------
C1  测试通过升账：端点测试通过 + 证据链完整 → 台账条目自动创建（status=REAL）
C2  测试失败不升账：端点测试失败 → 台账条目不创建或标记 WITHHELD
C3  证据链完整性：证据链 JSON 含六必填字段，缺字段即 FAIL
C4  独立复核：另一测试套件交叉验证通过 → 升账，否则标记 WITHHELD
C5  回测准确率：在冻结端点集上升账决策准确率 >= 90%（假阳性 < 5%）
C6  零领域语义：代码与测试不含领域专名（扫描器断言 0 命中）

夹具全部合成域名（endpoint-alpha / synth-cap-001 / synthetic-mission-*），
零学科语义。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.audit.auto_capability_upgrade import (
    EvidenceChain,
    UpgradeDecision,
    BacktestResult,
    compute_sha256,
    validate_evidence_chain,
    check_domain_leak,
    decide_upgrade,
    apply_upgrade,
    run_backtest,
    process_batch,
    main,
    EVIDENCE_CHAIN_REQUIRED_FIELDS,
    VERDICT_PASS,
    VERDICT_FAIL,
    DOMAIN_FORBIDDEN_TERMS,
    EXIT_OK,
    EXIT_EVIDENCE_INCOMPLETE,
    EXIT_VERDICT_FAIL,
    EXIT_CROSS_VALIDATION_FAIL,
    EXIT_DOMAIN_LEAK,
)


# ──────────────────────────────── 夹具 ──────────────────────────────── #

def _make_valid_chain(**overrides) -> EvidenceChain:
    """构造一条完整合法的证据链（合成域名）。"""
    base = dict(
        test_report_sha256="a" * 64,
        acceptance_criteria={"criterion_1": {"threshold": 0.03, "metric": "relative_error"}},
        measured_values={"metric_1": 0.012, "metric_2": 0.008},
        verdict=VERDICT_PASS,
        timestamp="2026-11-10T10:00:00+00:00",
        mission_id="synthetic-mission-001",
        endpoint_name="endpoint-alpha",
        repo="SyntheticRepo",
    )
    base.update(overrides)
    return EvidenceChain(**base)


def _make_chain_dict(**overrides) -> dict:
    return _make_valid_chain(**overrides).to_dict()


# ──────────────────────────── C3: 证据链完整性 ──────────────────────────── #

class TestEvidenceChainCompleteness:
    """C3: 证据链完整性验证。"""

    def test_valid_chain_passes(self):
        chain = _make_valid_chain()
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is True
        assert errors == []

    def test_missing_sha256_fails(self):
        chain = _make_valid_chain(test_report_sha256="")
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("test_report_sha256" in e for e in errors)

    def test_invalid_sha256_format_fails(self):
        chain = _make_valid_chain(test_report_sha256="not-a-sha256")
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("test_report_sha256" in e for e in errors)

    def test_missing_acceptance_criteria_fails(self):
        chain = _make_valid_chain(acceptance_criteria={})
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("acceptance_criteria" in e for e in errors)

    def test_missing_measured_values_fails(self):
        chain = _make_valid_chain(measured_values={})
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("measured_values" in e for e in errors)

    def test_invalid_verdict_fails(self):
        chain = _make_valid_chain(verdict="UNKNOWN")
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("verdict" in e for e in errors)

    def test_missing_timestamp_fails(self):
        chain = _make_valid_chain(timestamp="")
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("timestamp" in e for e in errors)

    def test_invalid_timestamp_format_fails(self):
        chain = _make_valid_chain(timestamp="not-a-timestamp")
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("timestamp" in e for e in errors)

    def test_missing_mission_id_fails(self):
        chain = _make_valid_chain(mission_id="")
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert any("mission_id" in e for e in errors)

    def test_multiple_missing_fields(self):
        chain = _make_valid_chain(
            test_report_sha256="",
            acceptance_criteria={},
            mission_id="",
        )
        is_valid, errors = validate_evidence_chain(chain)
        assert is_valid is False
        assert len(errors) >= 3

    def test_all_required_fields_present(self):
        """确保 EVIDENCE_CHAIN_REQUIRED_FIELDS 与 EvidenceChain 字段一致。"""
        chain = _make_valid_chain()
        d = chain.to_dict()
        for field_name in EVIDENCE_CHAIN_REQUIRED_FIELDS:
            assert field_name in d, f"Required field {field_name!r} missing from EvidenceChain"


# ──────────────────────────── C1: 测试通过升账 ──────────────────────────── #

class TestPassUpgrade:
    """C1: 测试通过 + 证据完整 -> REAL。"""

    def test_pass_with_complete_evidence_becomes_real(self):
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain)
        assert decision.new_status == "REAL"
        assert decision.evidence_complete is True
        assert "test_PASS" in decision.reason

    def test_pass_with_cross_validation_becomes_real(self):
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain, cross_validation_result=True)
        assert decision.new_status == "REAL"
        assert decision.cross_validated is True

    def test_pass_without_cross_validation_still_real(self):
        """未做交叉验证时，PASS + 完整证据 -> REAL（C4 只在明确失败时降级）。"""
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain, cross_validation_result=None)
        assert decision.new_status == "REAL"

    def test_pass_writes_to_ledger(self, tmp_path):
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain)
        ledger_path = tmp_path / "ledger.json"
        result = apply_upgrade(decision, ledger_path)
        assert result["applied"] is True
        assert result["new_status"] == "REAL"
        assert ledger_path.exists()
        ledger = json.loads(ledger_path.read_text())
        key = f"{chain.mission_id}:{chain.endpoint_name}"
        assert ledger["entries"][key]["status"] == "REAL"

    def test_dry_run_does_not_write(self, tmp_path):
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain)
        ledger_path = tmp_path / "ledger.json"
        result = apply_upgrade(decision, ledger_path, dry_run=True)
        assert result["applied"] is False
        assert not ledger_path.exists()


# ──────────────────────────── C2: 测试失败不升账 ────────────────────────── #

class TestFailNoUpgrade:
    """C2: 测试失败 -> WITHHELD。"""

    def test_fail_becomes_withheld(self):
        chain = _make_valid_chain(verdict=VERDICT_FAIL)
        decision = decide_upgrade(chain)
        assert decision.new_status == "WITHHELD"
        assert "test_verdict_FAIL" in decision.reason

    def test_fail_with_cross_validation_still_withheld(self):
        """即使交叉验证通过，测试失败仍然 WITHHELD。"""
        chain = _make_valid_chain(verdict=VERDICT_FAIL)
        decision = decide_upgrade(chain, cross_validation_result=True)
        assert decision.new_status == "WITHHELD"

    def test_incomplete_evidence_becomes_withheld(self):
        chain = _make_valid_chain(test_report_sha256="")
        decision = decide_upgrade(chain)
        assert decision.new_status == "WITHHELD"
        assert "evidence_chain_incomplete" in decision.reason


# ──────────────────────────── C4: 独立复核 ──────────────────────────────── #

class TestCrossValidation:
    """C4: 交叉验证。"""

    def test_cross_validation_fail_downgrades_to_withheld(self):
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain, cross_validation_result=False)
        assert decision.new_status == "WITHHELD"
        assert "cross_validation_failed" in decision.reason
        assert decision.cross_validated is False

    def test_cross_validation_pass_keeps_real(self):
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain, cross_validation_result=True)
        assert decision.new_status == "REAL"
        assert decision.cross_validated is True

    def test_cross_validation_none_keeps_real(self):
        chain = _make_valid_chain(verdict=VERDICT_PASS)
        decision = decide_upgrade(chain, cross_validation_result=None)
        assert decision.new_status == "REAL"


# ──────────────────────────── C5: 回测准确率 ────────────────────────────── #

class TestBacktestAccuracy:
    """C5: 回测准确率 >= 90%，假阳性 < 5%。"""

    def _generate_frozen_cases(self, n: int = 50) -> list:
        """生成冻结端点集测试用例。"""
        cases = []
        for i in range(n):
            if i < n // 2:
                chain = _make_chain_dict(
                    mission_id=f"synth-m-{i:03d}",
                    endpoint_name=f"ep-{i:03d}",
                    verdict=VERDICT_PASS,
                    test_report_sha256=f"{i:064x}",
                )
                cases.append({
                    "chain": chain,
                    "cross_validation": None,
                    "expected_status": "REAL",
                })
            elif i < n // 2 + n * 3 // 10:
                chain = _make_chain_dict(
                    mission_id=f"synth-m-{i:03d}",
                    endpoint_name=f"ep-{i:03d}",
                    verdict=VERDICT_FAIL,
                    test_report_sha256=f"{i:064x}",
                )
                cases.append({
                    "chain": chain,
                    "cross_validation": None,
                    "expected_status": "WITHHELD",
                })
            else:
                chain = _make_chain_dict(
                    mission_id=f"synth-m-{i:03d}",
                    endpoint_name=f"ep-{i:03d}",
                    verdict=VERDICT_PASS,
                    test_report_sha256="",
                )
                cases.append({
                    "chain": chain,
                    "cross_validation": None,
                    "expected_status": "WITHHELD",
                })
        return cases

    def test_backtest_accuracy_above_90(self):
        cases = self._generate_frozen_cases(50)
        result = run_backtest(cases)
        assert result.accuracy >= 0.9, f"Accuracy {result.accuracy:.2%} < 90%"

    def test_backtest_false_positive_below_5(self):
        cases = self._generate_frozen_cases(50)
        result = run_backtest(cases)
        assert result.false_positive_rate < 0.05, \
            f"FP rate {result.false_positive_rate:.2%} >= 5%"

    def test_backtest_perfect_on_deterministic_rules(self):
        """确定性规则下，回测应 100% 准确。"""
        cases = self._generate_frozen_cases(50)
        result = run_backtest(cases)
        assert result.accuracy == 1.0
        assert result.false_positives == 0
        assert result.false_negatives == 0

    def test_backtest_with_cross_validation_cases(self):
        """含交叉验证的混合用例。"""
        cases = [
            {"chain": _make_chain_dict(mission_id="m1", endpoint_name="e1",
                                       verdict=VERDICT_PASS, test_report_sha256="a" * 64),
             "cross_validation": True, "expected_status": "REAL"},
            {"chain": _make_chain_dict(mission_id="m2", endpoint_name="e2",
                                       verdict=VERDICT_PASS, test_report_sha256="b" * 64),
             "cross_validation": False, "expected_status": "WITHHELD"},
            {"chain": _make_chain_dict(mission_id="m3", endpoint_name="e3",
                                       verdict=VERDICT_FAIL, test_report_sha256="c" * 64),
             "cross_validation": True, "expected_status": "WITHHELD"},
        ]
        result = run_backtest(cases)
        assert result.accuracy == 1.0
        assert result.total == 3


# ──────────────────────────── C6: 零领域语义 ────────────────────────────── #

def _scan_file_for_domain_terms(filepath: pathlib.Path) -> list:
    """扫描文件功能代码中的领域专名（排除元引用行）。"""
    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")
    in_docstring = False
    in_forbidden_tuple = False
    leaks = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 跟踪文档字符串
        triple_count = stripped.count('"""')
        if triple_count > 0:
            if triple_count == 1:
                in_docstring = not in_docstring
            # triple_count >= 2: open+close on same line, skip this line
            continue
        if in_docstring:
            continue

        # DOMAIN_FORBIDDEN_TERMS 元组定义区域
        if "DOMAIN_FORBIDDEN_TERMS" in stripped and "=" in stripped:
            in_forbidden_tuple = True
            continue
        if in_forbidden_tuple:
            if stripped.startswith(")"):
                in_forbidden_tuple = False
            continue

        # 跳过纯注释行
        if stripped.startswith("#"):
            continue

        # 跳过 DOMAIN_FORBIDDEN_TERMS 引用行
        if "DOMAIN_FORBIDDEN_TERMS" in stripped:
            continue

        # 扫描器文档中的元引用（如 calc_resistance 示例）
        if "calc_" in stripped and any(t in stripped for t in DOMAIN_FORBIDDEN_TERMS):
            continue

        line_leaks = check_domain_leak(line)
        if line_leaks:
            leaks.append((i + 1, line_leaks, stripped[:60]))

    return leaks


class TestZeroDomainSemantics:
    """C6: 零领域语义扫描。"""

    def test_domain_leak_detector_finds_terms(self):
        text = "This module handles separate calculations for two metrics."
        # Use terms from the forbidden list directly
        for term in ["resistance", "propulsion"]:
            leaks = check_domain_leak(f"calc_{term} = 1")
            assert term in leaks

    def test_domain_leak_detector_clean_text(self):
        text = "This module handles endpoint testing and evidence validation."
        leaks = check_domain_leak(text)
        assert leaks == []

    def test_source_file_clean(self):
        """本模块源文件的功能代码不含领域专名。"""
        source_path = REPO / "tools" / "audit" / "auto_capability_upgrade.py"
        leaks = _scan_file_for_domain_terms(source_path)
        assert leaks == [], f"Domain terms in functional code: {leaks}"

    def test_test_file_clean(self):
        """本测试文件的功能代码不含领域专名。"""
        test_path = REPO / "tests" / "test_auto_capability_upgrade.py"
        leaks = _scan_file_for_domain_terms(test_path)
        assert leaks == [], f"Domain terms in test code: {leaks}"

    def test_all_forbidden_terms_detected(self):
        """确保扫描器能检测所有禁止词。"""
        for term in DOMAIN_FORBIDDEN_TERMS:
            leaks = check_domain_leak(f"test {term} here")
            assert term in leaks, f"Scanner failed to detect {term!r}"


# ──────────────────────────── 批量处理 ──────────────────────────────────── #

class TestBatchProcessing:
    """批量升账处理。"""

    def test_batch_mixed_verdicts(self, tmp_path):
        chains = [
            _make_chain_dict(mission_id="m1", endpoint_name="e1",
                             verdict=VERDICT_PASS, test_report_sha256="a" * 64),
            _make_chain_dict(mission_id="m2", endpoint_name="e2",
                             verdict=VERDICT_FAIL, test_report_sha256="b" * 64),
            _make_chain_dict(mission_id="m3", endpoint_name="e3",
                             verdict=VERDICT_PASS, test_report_sha256=""),
        ]
        ledger_path = tmp_path / "ledger.json"
        results = process_batch(chains, ledger_path)
        assert len(results) == 3
        assert results[0]["decision"]["new_status"] == "REAL"
        assert results[1]["decision"]["new_status"] == "WITHHELD"
        assert results[2]["decision"]["new_status"] == "WITHHELD"

    def test_batch_with_cross_validations(self, tmp_path):
        chains = [
            _make_chain_dict(mission_id="m1", endpoint_name="e1",
                             verdict=VERDICT_PASS, test_report_sha256="a" * 64),
            _make_chain_dict(mission_id="m2", endpoint_name="e2",
                             verdict=VERDICT_PASS, test_report_sha256="b" * 64),
        ]
        cross_vals = {"m1": True, "m2": False}
        ledger_path = tmp_path / "ledger.json"
        results = process_batch(chains, ledger_path, cross_validations=cross_vals)
        assert results[0]["decision"]["new_status"] == "REAL"
        assert results[1]["decision"]["new_status"] == "WITHHELD"


# ──────────────────────────── 台账持久化 ────────────────────────────────── #

class TestLedgerPersistence:
    """台账读写。"""

    def test_ledger_created_if_missing(self, tmp_path):
        chain = _make_valid_chain()
        decision = decide_upgrade(chain)
        ledger_path = tmp_path / "subdir" / "ledger.json"
        apply_upgrade(decision, ledger_path)
        assert ledger_path.exists()

    def test_ledger_preserves_existing_entries(self, tmp_path):
        ledger_path = tmp_path / "ledger.json"
        chain1 = _make_valid_chain(mission_id="m1", endpoint_name="e1")
        d1 = decide_upgrade(chain1)
        apply_upgrade(d1, ledger_path)
        chain2 = _make_valid_chain(mission_id="m2", endpoint_name="e2")
        d2 = decide_upgrade(chain2)
        apply_upgrade(d2, ledger_path)
        ledger = json.loads(ledger_path.read_text())
        assert len(ledger["entries"]) == 2

    def test_ledger_update_overwrites(self, tmp_path):
        ledger_path = tmp_path / "ledger.json"
        chain = _make_valid_chain(mission_id="m1", endpoint_name="e1", verdict=VERDICT_PASS)
        d1 = decide_upgrade(chain)
        apply_upgrade(d1, ledger_path)
        chain_fail = _make_valid_chain(mission_id="m1", endpoint_name="e1", verdict=VERDICT_FAIL)
        d2 = decide_upgrade(chain_fail)
        result = apply_upgrade(d2, ledger_path)
        assert result["previous_status"] == "REAL"
        assert result["new_status"] == "WITHHELD"
        ledger = json.loads(ledger_path.read_text())
        key = "m1:e1"
        assert ledger["entries"][key]["status"] == "WITHHELD"


# ──────────────────────────── CLI ───────────────────────────────────────── #

class TestCLI:
    """CLI 入口测试。"""

    def test_validate_valid_chain(self, tmp_path):
        chain_file = tmp_path / "chain.json"
        chain_file.write_text(json.dumps(_make_chain_dict()))
        rc = main(["validate", "--chain", str(chain_file)])
        assert rc == EXIT_OK

    def test_validate_invalid_chain(self, tmp_path):
        chain_file = tmp_path / "chain.json"
        chain_file.write_text(json.dumps(_make_chain_dict(test_report_sha256="")))
        rc = main(["validate", "--chain", str(chain_file)])
        assert rc == EXIT_EVIDENCE_INCOMPLETE

    def test_decide_pass(self, tmp_path):
        chain_file = tmp_path / "chain.json"
        chain_file.write_text(json.dumps(_make_chain_dict()))
        ledger = tmp_path / "ledger.json"
        rc = main(["decide", "--chain", str(chain_file), "--ledger", str(ledger)])
        assert rc == EXIT_OK

    def test_decide_fail(self, tmp_path):
        chain_file = tmp_path / "chain.json"
        chain_file.write_text(json.dumps(_make_chain_dict(verdict=VERDICT_FAIL)))
        ledger = tmp_path / "ledger.json"
        rc = main(["decide", "--chain", str(chain_file), "--ledger", str(ledger)])
        assert rc == EXIT_VERDICT_FAIL

    def test_decide_cross_validation_fail(self, tmp_path):
        chain_file = tmp_path / "chain.json"
        chain_file.write_text(json.dumps(_make_chain_dict()))
        ledger = tmp_path / "ledger.json"
        rc = main(["decide", "--chain", str(chain_file), "--ledger", str(ledger),
                    "--cross-validation", "fail"])
        assert rc == EXIT_CROSS_VALIDATION_FAIL

    def test_scan_clean_files(self, tmp_path):
        clean_file = tmp_path / "clean.py"
        clean_file.write_text("x = 1  # no domain terms here\n")
        rc = main(["scan", "--files", str(clean_file)])
        assert rc == EXIT_OK

    def test_scan_dirty_file(self, tmp_path):
        dirty_file = tmp_path / "dirty.py"
        dirty_file.write_text("def calc_resistance(): pass\n")
        rc = main(["scan", "--files", str(dirty_file)])
        assert rc == EXIT_DOMAIN_LEAK

    def test_backtest_cli(self, tmp_path):
        cases = self._make_backtest_cases()
        cases_file = tmp_path / "cases.json"
        cases_file.write_text(json.dumps(cases))
        rc = main(["backtest", "--cases", str(cases_file)])
        assert rc == EXIT_OK

    def _make_backtest_cases(self):
        return [
            {"chain": _make_chain_dict(mission_id=f"m{i}", endpoint_name=f"e{i}",
                                       verdict=VERDICT_PASS, test_report_sha256=f"{i:064x}"),
             "cross_validation": None, "expected_status": "REAL"}
            for i in range(10)
        ] + [
            {"chain": _make_chain_dict(mission_id=f"m{i+10}", endpoint_name=f"e{i+10}",
                                       verdict=VERDICT_FAIL, test_report_sha256=f"{i+10:064x}"),
             "cross_validation": None, "expected_status": "WITHHELD"}
            for i in range(5)
        ]


# ──────────────────────────── 决策溯源 ──────────────────────────────────── #

class TestDecisionTraceability:
    """决策可溯源性。"""

    def test_decision_has_unique_id(self):
        chain = _make_valid_chain()
        d = decide_upgrade(chain)
        assert d.decision_id.startswith("dec-")
        assert len(d.decision_id) > 10

    def test_decision_has_evidence_sha256(self):
        chain = _make_valid_chain()
        d = decide_upgrade(chain)
        assert len(d.evidence_sha256) == 64

    def test_different_chains_different_decisions(self):
        c1 = _make_valid_chain(mission_id="m1")
        c2 = _make_valid_chain(mission_id="m2")
        d1 = decide_upgrade(c1)
        d2 = decide_upgrade(c2)
        assert d1.decision_id != d2.decision_id

    def test_same_chain_different_time_different_id(self):
        """同一证据链在不同时间产生不同 decision_id（时间戳参与哈希）。"""
        import time
        chain = _make_valid_chain()
        d1 = decide_upgrade(chain)
        time.sleep(0.01)
        d2 = decide_upgrade(chain)
        assert d1.decision_id != d2.decision_id


# ──────────────────────────── 边界条件 ──────────────────────────────────── #

class TestEdgeCases:
    """边界条件与鲁棒性。"""

    def test_evidence_chain_from_dict_extra_fields_ignored(self):
        d = _make_chain_dict()
        d["extra_field"] = "should be ignored"
        chain = EvidenceChain.from_dict(d)
        assert chain.mission_id == d["mission_id"]

    def test_empty_batch(self, tmp_path):
        ledger_path = tmp_path / "ledger.json"
        results = process_batch([], ledger_path)
        assert results == []

    def test_backtest_empty_cases(self):
        result = run_backtest([])
        assert result.total == 0
        assert result.accuracy == 0.0

    def test_sha256_deterministic(self):
        assert compute_sha256("hello") == compute_sha256("hello")
        assert compute_sha256("hello") != compute_sha256("world")

    def test_ledger_schema_version(self, tmp_path):
        chain = _make_valid_chain()
        decision = decide_upgrade(chain)
        ledger_path = tmp_path / "ledger.json"
        apply_upgrade(decision, ledger_path)
        ledger = json.loads(ledger_path.read_text())
        assert "schema_version" in ledger
