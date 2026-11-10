"""KPI 反操纵夹具测试（m9358）。

验证：
1. 5 类操纵向量都能生成夹具
2. 每类夹具的断言都通过（操纵后 KPI 不得上升）
3. 合成域名 fixture（零领域语义）
"""
import pytest
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.metrics.gaming_fixtures import (
    generate_m1_finish_empty,
    generate_m2_duplicate_artifact,
    generate_m3_copy_samples,
    generate_m4_modify_ledger,
    generate_m5_denominator_split,
    generate_all_fixtures,
    compute_kpi_success_rate,
    compute_kpi_lineage_coverage,
    compute_kpi_benchmark_cases,
    SyntheticMission,
)


class TestM1FinishEmpty:
    def test_fixture_generation(self):
        fixture = generate_m1_finish_empty()
        assert fixture.vector == "M1"
        assert len(fixture.manipulated_missions) > len(fixture.baseline_missions)

    def test_lineage_coverage_must_not_increase(self):
        fixture = generate_m1_finish_empty()
        baseline_kpi = compute_kpi_lineage_coverage(fixture.baseline_missions, fixture.baseline_reports)
        manipulated_kpi = compute_kpi_lineage_coverage(fixture.manipulated_missions, fixture.manipulated_reports)
        assert manipulated_kpi <= baseline_kpi


class TestM2DuplicateArtifact:
    def test_fixture_generation(self):
        fixture = generate_m2_duplicate_artifact()
        assert fixture.vector == "M2"

    def test_lineage_coverage_must_not_increase_after_dedup(self):
        fixture = generate_m2_duplicate_artifact()
        baseline_kpi = compute_kpi_lineage_coverage(fixture.baseline_missions, fixture.baseline_reports)
        manipulated_kpi = compute_kpi_lineage_coverage(fixture.manipulated_missions, fixture.manipulated_reports)
        assert manipulated_kpi <= baseline_kpi


class TestM3CopySamples:
    def test_fixture_generation(self):
        fixture = generate_m3_copy_samples()
        assert fixture.vector == "M3"

    def test_dedup_count_must_not_increase(self):
        fixture = generate_m3_copy_samples()
        baseline_kpi = compute_kpi_benchmark_cases(fixture.baseline_samples)
        manipulated_kpi = compute_kpi_benchmark_cases(fixture.manipulated_samples)
        assert manipulated_kpi <= baseline_kpi


class TestM4ModifyLedger:
    def test_sha256_must_differ(self):
        fixture = generate_m4_modify_ledger()
        assert fixture.assertions["sha256_must_differ"]


class TestM5DenominatorSplit:
    def test_fixture_generation(self):
        fixture = generate_m5_denominator_split()
        assert fixture.vector == "M5"

    def test_terminal_count_drop_detected(self):
        fixture = generate_m5_denominator_split()
        terminal_b = len([m for m in fixture.baseline_missions if m.status in ("succeeded", "failed")])
        terminal_m = len([m for m in fixture.manipulated_missions if m.status in ("succeeded", "failed")])
        drop_ratio = 1 - terminal_m / terminal_b
        assert drop_ratio > 0.2

    def test_prop7619_scenario(self):
        baseline = (
            [SyntheticMission(mission_id=f"s{i}", status="succeeded") for i in range(8)] +
            [SyntheticMission(mission_id=f"f{i}", status="failed") for i in range(4)]
        )
        kpi = compute_kpi_success_rate(baseline)
        assert abs(kpi - 0.6667) < 0.001


class TestAllFixtures:
    def test_generate_all(self):
        fixtures = generate_all_fixtures()
        assert len(fixtures) == 5

    def test_synthetic_domain_only(self):
        fixtures = generate_all_fixtures()
        for f in fixtures:
            for m in f.baseline_missions + f.manipulated_missions:
                assert "SUBOFF" not in m.mission_id
                assert "KVLCC" not in m.mission_id
