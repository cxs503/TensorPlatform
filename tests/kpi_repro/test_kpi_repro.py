"""KPI-REPRO-95 计量模块测试。

测试覆盖：
  1. 合成数据生成与分布验证
  2. KPI 计算正确性
  3. 工件分类逻辑
  4. 根因分类逻辑
  5. 独立复核通过
  6. 端到端运行
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from kpi_repro.measure import (
    MissionRow, KPIResult,
    ARTIFACT_STRONG, ARTIFACT_MEDIUM, ARTIFACT_NONE,
    ROOT_CAUSE_NO_ARTIFACT, ROOT_CAUSE_DISK_FULL,
    ROOT_CAUSE_BUDGET_EXHAUSTED, ROOT_CAUSE_LLM_ERROR,
    KPI_TARGET,
    _clean_pointer, _parse_artifact_pointers,
    compute_kpi,
)
from kpi_repro.synthetic import generate_synthetic_data, measure_synthetic
from kpi_repro.verify import verify_kpi_repro


class TestSyntheticData:
    """合成数据生成测试。"""

    def test_generate_count(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        assert len(missions) == 82

    def test_generate_has_succeeded(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        succeeded = [m for m in missions if m.status == "succeeded"]
        assert len(succeeded) > 0

    def test_generate_has_varied_artifacts(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        succeeded = [m for m in missions if m.status == "succeeded"]
        levels = set(m.artifact_level for m in succeeded)
        assert ARTIFACT_STRONG in levels
        assert ARTIFACT_NONE in levels

    def test_generate_repro_task_keys(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        for m in missions:
            assert m.task_key is not None
            assert "repro" in m.task_key or "audit" in m.task_key


class TestKPIComputation:
    """KPI 计算测试。"""

    def test_basic_computation(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        kpi = measure_synthetic(missions)

        assert kpi.total == 82
        assert kpi.target == KPI_TARGET
        assert kpi.current >= 0
        assert kpi.current <= 100
        assert abs(kpi.gap - (KPI_TARGET - kpi.current)) < 0.01

    def test_passed_equals_strong_plus_medium(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        kpi = measure_synthetic(missions)

        succeeded = [m for m in missions if m.status == "succeeded"]
        strong = sum(1 for m in succeeded if m.artifact_level == ARTIFACT_STRONG)
        medium = sum(1 for m in succeeded if m.artifact_level == ARTIFACT_MEDIUM)
        assert kpi.passed == strong + medium

    def test_succeeded_rate(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        kpi = measure_synthetic(missions)

        succeeded = sum(1 for m in missions if m.status == "succeeded")
        expected_rate = round(succeeded / 82 * 100, 2)
        assert abs(kpi.succeeded_rate - expected_rate) < 0.01

    def test_upper_bound_gte_main(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        kpi = measure_synthetic(missions)

        # 上界应该 >= 主口径
        assert kpi.current_upper >= kpi.current

    def test_empty_missions(self):
        kpi = compute_kpi([], "t0", "t0_db")
        assert kpi.total == 0
        assert kpi.current == 0.0
        assert kpi.gap == KPI_TARGET


class TestArtifactClassification:
    """工件分类测试。"""

    def test_clean_pointer_removes_test_suffix(self):
        assert _clean_pointer("path.py::test_name") == "path.py"

    def test_clean_pointer_removes_line_number(self):
        assert _clean_pointer("path.py:42") == "path.py"

    def test_clean_pointer_removes_out_prefix(self):
        assert _clean_pointer("OUT=path.py") == "path.py"

    def test_parse_artifact_pointers_from_result(self):
        result = {
            "changed_files": ["docs/report.md", "src/code.py"],
            "head_commit": "abc123",
        }
        pointers = _parse_artifact_pointers(result)
        assert "docs/report.md" in pointers
        assert "src/code.py" in pointers

    def test_parse_artifact_pointers_empty_result(self):
        assert _parse_artifact_pointers(None) == []
        assert _parse_artifact_pointers({}) == []


class TestRootCauseClassification:
    """根因分类测试。"""

    def test_synthetic_has_root_causes(self):
        missions = generate_synthetic_data(n_total=82, seed=42)
        kpi = measure_synthetic(missions)
        # 应该有失败根因
        assert len(kpi.failure_root_causes) > 0


class TestVerification:
    """独立复核测试。"""

    def test_verify_passes_on_synthetic(self, tmp_path):
        missions = generate_synthetic_data(n_total=82, seed=42)
        kpi = measure_synthetic(missions)

        # 写入文件
        result_file = tmp_path / "result.json"
        rows_file = tmp_path / "rows.jsonl"

        result_dict = asdict(kpi)
        result_dict["missions"] = result_dict["missions"][:100]
        with open(result_file, "w") as f:
            json.dump(result_dict, f, default=str)

        with open(rows_file, "w") as f:
            for m in missions:
                f.write(json.dumps(asdict(m), default=str) + "\n")

        # 运行复核
        vr = verify_kpi_repro(
            result_path=str(result_file),
            rows_path=str(rows_file),
            output_dir=str(tmp_path),
        )

        assert vr.all_passed, f"Failed checks: {[c for c in vr.checks if not c['passed']]}"
        assert vr.passed_checks == vr.total_checks


class TestEndToEnd:
    """端到端测试。"""

    def test_full_pipeline(self, tmp_path):
        """完整流程：生成数据 → 计量 → 复核 → 验证输出。"""
        missions = generate_synthetic_data(n_total=82, seed=42)
        kpi = measure_synthetic(missions)

        out_dir = tmp_path / "runs" / "m15200_kpi_repro"
        out_dir.mkdir(parents=True)

        # 写入 result.json
        result_dict = asdict(kpi)
        result_dict["missions"] = result_dict["missions"][:100]
        result_file = out_dir / "result.json"
        with open(result_file, "w") as f:
            json.dump(result_dict, f, default=str)

        # 写入 rows.jsonl
        rows_file = out_dir / "rows.jsonl"
        with open(rows_file, "w") as f:
            for m in missions:
                f.write(json.dumps(asdict(m), default=str) + "\n")

        # 写入 measure.sql
        sql_file = out_dir / "measure.sql"
        with open(sql_file, "w") as f:
            f.write("-- KPI-REPRO-95 measurement SQL (synthetic)\n")

        # 验证文件存在
        assert result_file.exists()
        assert rows_file.exists()
        assert sql_file.exists()

        # 验证 result.json 可加载
        with open(result_file) as f:
            loaded = json.load(f)
        assert loaded["kpi_name"] == "KPI-REPRO-95"
        assert loaded["total"] == 82

        # 运行复核
        vr = verify_kpi_repro(
            result_path=str(result_file),
            rows_path=str(rows_file),
            output_dir=str(out_dir),
        )
        assert vr.all_passed

        # 验证 verify.json 存在
        verify_file = out_dir / "verify.json"
        assert verify_file.exists()
