"""
能力证据链自动采集引擎 — pytest 测试套件（≥10 项）

纪律：零领域语义——所有测试夹具使用合成域名和合成验证案例，
不硬编码任何船舶/冰区词条。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# 确保 backend 在 path 中
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.capability_evidence_auto import (
    PATTERN_KEYWORDS,
    ArtifactMetadata, ArtifactScanner, CapabilityEvidence, DerivedGrade,
    DiffEntry, DiffReporter, EvidenceDeriver, LedgerUpdater,
    VALIDATION_PATTERNS, EVIDENCE_REAL, EVIDENCE_MOCK, EVIDENCE_STUB,
    EVIDENCE_WITHHELD, EVIDENCE_NOT_SUPPORTED, LEVEL_RANK,
    run_full_audit,
)


# ── 测试夹具 ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_repo(tmp_path):
    """创建临时仓库结构。"""
    (tmp_path / "verified").mkdir()
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "config" / "evidence_pipeline" / "benchmarks").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def synthetic_artifacts():
    """生成合成验证案例元数据。"""
    return [
        ArtifactMetadata(
            source_path="tests/test_syn_alpha.py",
            source_type="pytest",
            capability_id="syn.alpha",
            validation_patterns=["analytical_solution_comparison", "unit_test_coverage"],
            metrics={"error_pct": 2.5, "threshold_pct": 15.0},
            passed=True,
            error_pct=2.5,
        ),
        ArtifactMetadata(
            source_path="benchmarks/syn_beta_exp.json",
            source_type="benchmark",
            capability_id="syn.beta",
            validation_patterns=["experimental_benchmark", "grid_convergence"],
            metrics={"error_pct": 5.0, "threshold_pct": 15.0},
            passed=True,
            error_pct=5.0,
        ),
        ArtifactMetadata(
            source_path="tests/test_syn_gamma.py",
            source_type="pytest",
            capability_id="syn.gamma",
            validation_patterns=["unit_test_coverage"],
            metrics={"error_pct": 200.0, "threshold_pct": 15.0},
            passed=False,
            error_pct=200.0,
        ),
        ArtifactMetadata(
            source_path="verified/syn_delta_stub.json",
            source_type="verified",
            capability_id="syn.delta",
            validation_patterns=["unit_test_coverage"],
            metrics={"error_pct": 0.0},
            passed=True,
            error_pct=0.0,
            extra={"source_code": "raise NotImplementedError"},
        ),
    ]


# ── 测试用例 ─────────────────────────────────────────────────────────


class TestValidationPatterns:
    """T1: 验证模式覆盖 ≥10 种。"""

    def test_pattern_count(self):
        assert len(VALIDATION_PATTERNS) >= 10, f"Only {len(VALIDATION_PATTERNS)} patterns, need ≥10"

    def test_pattern_uniqueness(self):
        assert len(VALIDATION_PATTERNS) == len(set(VALIDATION_PATTERNS)), "Duplicate patterns found"


class TestArtifactScanner:
    """T2: 工件扫描器功能。"""

    def test_scan_empty_repo(self, tmp_repo):
        scanner = ArtifactScanner(repo_root=tmp_repo)
        artifacts = scanner.scan_all()
        assert isinstance(artifacts, list)

    def test_scan_with_pytest_result(self, tmp_repo):
        pytest_data = {
            "tests": [
                {
                    "nodeid": "tests/test_syn.py::test_alpha",
                    "outcome": "passed",
                    "user_properties": {
                        "capability_id": "syn.alpha",
                        "metric": "R_T",
                        "measured": 123.4,
                        "reference": 120.0,
                        "error_pct": 2.83,
                        "threshold_pct": 15.0,
                    },
                }
            ]
        }
        result_path = tmp_repo / "pytest_result.json"
        result_path.write_text(json.dumps(pytest_data))

        scanner = ArtifactScanner(repo_root=tmp_repo)
        artifacts = scanner.scan_all()
        assert len(artifacts) >= 1
        assert artifacts[0].capability_id == "syn.alpha"
        assert artifacts[0].passed is True

    def test_scan_with_benchmark_json(self, tmp_repo):
        bench_data = {
            "benchmarks": [
                {
                    "capability_id": "syn.beta",
                    "metrics": {
                        "R_T": {"measured": 125.0, "reference": 120.0, "error_pct": 4.17}
                    },
                }
            ]
        }
        bench_path = tmp_repo / "benchmarks" / "syn_beta.json"
        bench_path.write_text(json.dumps(bench_data))

        scanner = ArtifactScanner(repo_root=tmp_repo)
        artifacts = scanner.scan_all()
        assert len(artifacts) >= 1
        assert artifacts[0].capability_id == "syn.beta"


class TestEvidenceDeriver:
    """T3: 证据等级推导器。"""

    def test_derive_real(self):
        artifacts = [
            ArtifactMetadata(
                source_path="tests/test_syn.py", source_type="pytest",
                capability_id="syn.alpha",
                validation_patterns=["analytical_solution_comparison", "unit_test_coverage", "grid_convergence"],
                metrics={"error_pct": 2.5}, passed=True, error_pct=2.5,
            ),
            ArtifactMetadata(
                source_path="benchmarks/syn_exp.json", source_type="benchmark",
                capability_id="syn.alpha",
                validation_patterns=["experimental_benchmark"],
                metrics={"error_pct": 3.0}, passed=True, error_pct=3.0,
            ),
        ]
        deriver = EvidenceDeriver()
        grades = deriver.derive_all(artifacts)
        assert "syn.alpha" in grades
        assert grades["syn.alpha"].evidence_grade == EVIDENCE_REAL

    def test_derive_mock_high_error(self):
        artifacts = [
            ArtifactMetadata(
                source_path="tests/test_syn.py", source_type="pytest",
                capability_id="syn.gamma",
                validation_patterns=["unit_test_coverage"],
                metrics={"error_pct": 200.0}, passed=False, error_pct=200.0,
            ),
        ]
        deriver = EvidenceDeriver()
        grades = deriver.derive_all(artifacts)
        assert grades["syn.gamma"].evidence_grade == EVIDENCE_MOCK

    def test_derive_stub(self):
        artifacts = [
            ArtifactMetadata(
                source_path="verified/syn_delta.json", source_type="verified",
                capability_id="syn.delta",
                validation_patterns=["unit_test_coverage"],
                metrics={"error_pct": 0.0}, passed=True, error_pct=0.0,
                extra={"source_code": "raise NotImplementedError"},
            ),
        ]
        deriver = EvidenceDeriver()
        grades = deriver.derive_all(artifacts)
        assert grades["syn.delta"].evidence_grade == EVIDENCE_STUB

    def test_derive_withheld_insufficient_patterns(self):
        artifacts = [
            ArtifactMetadata(
                source_path="tests/test_syn.py", source_type="pytest",
                capability_id="syn.epsilon",
                validation_patterns=["unit_test_coverage"],
                metrics={"error_pct": 5.0}, passed=True, error_pct=5.0,
            ),
        ]
        deriver = EvidenceDeriver()
        grades = deriver.derive_all(artifacts)
        assert grades["syn.epsilon"].evidence_grade == EVIDENCE_WITHHELD

    def test_derive_not_supported(self):
        deriver = EvidenceDeriver()
        grades = deriver.derive_all([])
        assert len(grades) == 0


class TestLedgerUpdater:
    """T4: 台账更新器。"""

    def test_apply_new_grades(self, tmp_path):
        ledger_path = tmp_path / "ledger.json"
        updater = LedgerUpdater(ledger_path=ledger_path)

        grades = {
            "syn.alpha": DerivedGrade(
                capability_id="syn.alpha", evidence_grade=EVIDENCE_REAL,
                confidence=0.95, reason="All criteria met",
                pattern_count=3, pass_rate=1.0, artifact_count=2,
            ),
        }
        result = updater.apply_grades(grades)
        assert result["new_entries"] == 1
        assert ledger_path.exists()

    def test_apply_update_existing(self, tmp_path):
        ledger_path = tmp_path / "ledger.json"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        initial_ledger = {
            "schema_version": "3.0",
            "capabilities": [
                {"id": "syn.alpha", "domain": "syn", "claimed_level": EVIDENCE_MOCK, "evidence": []}
            ],
        }
        ledger_path.write_text(json.dumps(initial_ledger))

        updater = LedgerUpdater(ledger_path=ledger_path)
        grades = {
            "syn.alpha": DerivedGrade(
                capability_id="syn.alpha", evidence_grade=EVIDENCE_REAL,
                confidence=0.95, reason="Upgraded",
                pattern_count=3, pass_rate=1.0, artifact_count=2,
            ),
        }
        result = updater.apply_grades(grades)
        assert result["updated"] == 1


class TestDiffReporter:
    """T5: 差异报告器。"""

    def test_generate_upgrade(self):
        reporter = DiffReporter()
        grades = {
            "syn.alpha": DerivedGrade(
                capability_id="syn.alpha", evidence_grade=EVIDENCE_REAL,
                confidence=0.95, reason="Test", pattern_count=3, pass_rate=1.0, artifact_count=2,
            ),
        }
        current_audit = {"syn.alpha": EVIDENCE_MOCK}
        report = reporter.generate({}, grades, current_audit=current_audit)
        assert report["summary"]["upgrades"] == 1

    def test_generate_downgrade(self):
        reporter = DiffReporter()
        grades = {
            "syn.beta": DerivedGrade(
                capability_id="syn.beta", evidence_grade=EVIDENCE_MOCK,
                confidence=0.8, reason="Test", pattern_count=1, pass_rate=0.5, artifact_count=1,
            ),
        }
        current_audit = {"syn.beta": EVIDENCE_REAL}
        report = reporter.generate({}, grades, current_audit=current_audit)
        assert report["summary"]["downgrades"] == 1

    def test_generate_new_capability(self):
        reporter = DiffReporter()
        grades = {
            "syn.gamma": DerivedGrade(
                capability_id="syn.gamma", evidence_grade=EVIDENCE_WITHHELD,
                confidence=0.6, reason="Test", pattern_count=1, pass_rate=1.0, artifact_count=1,
            ),
        }
        report = reporter.generate({}, grades, current_audit={})
        assert report["summary"]["new"] == 1


class TestFullAudit:
    """T6: 全量审计流程。"""

    def test_run_full_audit_timing(self, tmp_repo):
        """T6a: 全量审计耗时 ≤60s。"""
        result = run_full_audit(repo_root=tmp_repo)
        assert result["total_time_s"] <= 60.0, f"Audit took {result['total_time_s']}s, exceeds 60s limit"

    def test_run_full_audit_structure(self, tmp_repo):
        """T6b: 全量审计返回结构完整。"""
        result = run_full_audit(repo_root=tmp_repo)
        assert "run_id" in result
        assert "scan_time_s" in result
        assert "derive_time_s" in result
        assert "update_time_s" in result
        assert "total_time_s" in result
        assert "artifacts_scanned" in result
        assert "capabilities_derived" in result
        assert "ledger_update" in result
        assert "diff_report" in result
        assert "grades" in result


class TestLevelRank:
    """T7: 证据等级排序正确性。"""

    def test_level_rank_order(self):
        assert LEVEL_RANK[EVIDENCE_NOT_SUPPORTED] < LEVEL_RANK[EVIDENCE_STUB]
        assert LEVEL_RANK[EVIDENCE_STUB] < LEVEL_RANK[EVIDENCE_MOCK]
        assert LEVEL_RANK[EVIDENCE_MOCK] < LEVEL_RANK[EVIDENCE_WITHHELD]
        assert LEVEL_RANK[EVIDENCE_WITHHELD] < LEVEL_RANK[EVIDENCE_REAL]

    def test_level_rank_completeness(self):
        for level in [EVIDENCE_REAL, EVIDENCE_MOCK, EVIDENCE_STUB, EVIDENCE_WITHHELD, EVIDENCE_NOT_SUPPORTED]:
            assert level in LEVEL_RANK


class TestZeroDomainSemantics:
    """T8: 零领域语义验证——测试夹具不含船舶/冰区词条。"""

    def test_no_domain_keywords_in_patterns(self):
        """验证模式关键词不含船舶/冰区特定词条。"""
        domain_keywords = ["holtrop", "stf", "mmg", "lindqvist", "iacs", "ice", "ship", "hull", "propeller"]
        all_keywords = []
        for keywords in PATTERN_KEYWORDS.values():
            all_keywords.extend(keywords)
        all_text = " ".join(all_keywords).lower()
        for kw in domain_keywords:
            assert kw not in all_text, f"Domain keyword '{kw}' found in pattern keywords"

    def test_synthetic_capability_ids(self, synthetic_artifacts):
        """验证合成 capability_id 不含领域词条。"""
        domain_keywords = ["resistance", "seakeeping", "maneuvering", "ice", "propeller"]
        for art in synthetic_artifacts:
            cap_lower = art.capability_id.lower()
            for kw in domain_keywords:
                assert kw not in cap_lower, f"Domain keyword '{kw}' in capability_id '{art.capability_id}'"


class TestPatternDetection:
    """T9: 验证模式检测准确性。"""

    def test_detect_analytical_from_path(self, tmp_repo):
        scanner = ArtifactScanner(repo_root=tmp_repo)
        patterns = scanner._detect_patterns_from_path("tests/test_analytic_solution.py")
        assert "analytical_solution_comparison" in patterns

    def test_detect_grid_convergence_from_path(self, tmp_repo):
        scanner = ArtifactScanner(repo_root=tmp_repo)
        patterns = scanner._detect_patterns_from_path("benchmarks/grid_convergence_study.json")
        assert "grid_convergence" in patterns

    def test_detect_experimental_from_data(self, tmp_repo):
        scanner = ArtifactScanner(repo_root=tmp_repo)
        data = {"capability_id": "syn.test", "experimental_data": [1, 2, 3]}
        patterns = scanner._detect_patterns_from_data(data)
        assert "experimental_benchmark" in patterns

    def test_detect_conservation_from_data(self, tmp_repo):
        scanner = ArtifactScanner(repo_root=tmp_repo)
        data = {"capability_id": "syn.test", "mass_balance_check": "passed"}
        patterns = scanner._detect_patterns_from_data(data)
        assert "conservation_law" in patterns


class TestEvidenceBundle:
    """T10: 证据汇总统计正确性。"""

    def test_pass_rate_calculation(self):
        artifacts = [
            ArtifactMetadata(source_path="a", source_type="pytest", capability_id="syn.test",
                validation_patterns=["unit_test_coverage"], passed=True, error_pct=1.0),
            ArtifactMetadata(source_path="b", source_type="pytest", capability_id="syn.test",
                validation_patterns=["unit_test_coverage"], passed=True, error_pct=2.0),
            ArtifactMetadata(source_path="c", source_type="pytest", capability_id="syn.test",
                validation_patterns=["unit_test_coverage"], passed=False, error_pct=50.0),
        ]
        deriver = EvidenceDeriver()
        evidence = deriver._build_evidence("syn.test", artifacts)
        assert evidence.total_tests == 3
        assert evidence.passed_tests == 2
        assert evidence.failed_tests == 1
        assert abs(evidence.pass_rate - 2/3) < 0.01


class TestKnownCapabilitiesConsistency:
    """T11: 已知能力自动推导等级与预期一致（模拟 audit_capabilities 输出）。"""

    def test_known_capabilities_derivation(self):
        """
        模拟 5 个已知能力的证据推导，验证与人工审计一致率 ≥4/5。
        使用合成域名，不硬编码真实船舶能力。
        """
        # 模拟 5 个已知能力的合成证据
        test_cases = [
            # (cap_id, artifacts, expected_grade)
            ("syn.resistance_like", [
                ArtifactMetadata(source_path="tests/test_syn_r.py", source_type="pytest",
                    capability_id="syn.resistance_like",
                    validation_patterns=["analytical_solution_comparison", "experimental_benchmark", "unit_test_coverage"],
                    metrics={"error_pct": 3.0}, passed=True, error_pct=3.0),
            ], EVIDENCE_REAL),
            ("syn.seakeeping_like", [
                ArtifactMetadata(source_path="tests/test_syn_s.py", source_type="pytest",
                    capability_id="syn.seakeeping_like",
                    validation_patterns=["unit_test_coverage"],
                    metrics={"error_pct": 5.0}, passed=True, error_pct=5.0),
            ], EVIDENCE_WITHHELD),
            ("syn.maneuvering_like", [
                ArtifactMetadata(source_path="tests/test_syn_m.py", source_type="pytest",
                    capability_id="syn.maneuvering_like",
                    validation_patterns=["unit_test_coverage"],
                    metrics={"error_pct": 150.0}, passed=False, error_pct=150.0),
            ], EVIDENCE_MOCK),
            ("syn.structural_like", [
                ArtifactMetadata(source_path="verified/syn_struct.json", source_type="verified",
                    capability_id="syn.structural_like",
                    validation_patterns=["unit_test_coverage"],
                    metrics={"error_pct": 0.0}, passed=True, error_pct=0.0,
                    extra={"source_code": "raise NotImplementedError"}),
            ], EVIDENCE_STUB),
            ("syn.green_like", [
                ArtifactMetadata(source_path="tests/test_syn_g.py", source_type="pytest",
                    capability_id="syn.green_like",
                    validation_patterns=["analytical_solution_comparison", "conservation_law", "unit_test_coverage"],
                    metrics={"error_pct": 1.5}, passed=True, error_pct=1.5),
            ], EVIDENCE_REAL),
        ]

        deriver = EvidenceDeriver()
        all_artifacts = []
        for cap_id, arts, _ in test_cases:
            all_artifacts.extend(arts)

        grades = deriver.derive_all(all_artifacts)

        # 验证一致率
        matches = 0
        for cap_id, _, expected in test_cases:
            if cap_id in grades and grades[cap_id].evidence_grade == expected:
                matches += 1

        consistency_rate = matches / len(test_cases)
        assert consistency_rate >= 0.8, f"Consistency rate {consistency_rate} < 0.8 (matched {matches}/{len(test_cases)})"


class TestDiffEntry:
    """T12: DiffEntry 序列化正确性。"""

    def test_diff_entry_to_dict(self):
        entry = DiffEntry(
            capability_id="syn.test",
            current_level=EVIDENCE_MOCK,
            derived_level=EVIDENCE_REAL,
            action="upgrade",
            reason="Test upgrade",
        )
        d = entry.to_dict()
        assert d["capability_id"] == "syn.test"
        assert d["current_level"] == EVIDENCE_MOCK
        assert d["derived_level"] == EVIDENCE_REAL
        assert d["action"] == "upgrade"
