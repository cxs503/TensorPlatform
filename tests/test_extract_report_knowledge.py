"""Tests for extract_report_knowledge.py"""
import json
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.extract_report_knowledge import (
    parse_markdown_tables,
    extract_geometry_from_md,
    extract_parameters_from_md,
    extract_results_from_md,
    parse_report,
    generate_synthetic_samples,
)


def test_parse_markdown_tables():
    """Test markdown table parsing."""
    md = """
| 面 | 几何 | 参数 |
|---|---|---|
| FEM→DEM | 立方实体单元 | 初速 v=(1, 0.5, 0) |
| LBM→FVM | 8³ 立方格子 | ρ=1+0.4 sin(2πy) |
"""
    tables = parse_markdown_tables(md)
    assert len(tables) == 1
    assert tables[0]["headers"] == ["面", "几何", "参数"]
    assert len(tables[0]["rows"]) == 2
    assert tables[0]["rows"][0]["面"] == "FEM→DEM"
    assert tables[0]["rows"][0]["几何"] == "立方实体单元"
    print("✓ test_parse_markdown_tables passed")


def test_extract_geometry():
    """Test geometry extraction."""
    md = """
| 面 | 几何 | 参数 |
|---|---|---|
| FEM→DEM | 立方实体单元，边长 1，密度 1 | 初速 v=(1, 0.5, 0) |
"""
    records = extract_geometry_from_md(md, "test.json")
    assert len(records) >= 1
    assert any(r.shape_type for r in records)
    print("✓ test_extract_geometry passed")


def test_extract_parameters():
    """Test parameter extraction."""
    md = """
| 参数 | 值 |
|---|---|
| 准入门三要素 | problem_definition / baseline / acceptance_criteria |
"""
    records = extract_parameters_from_md(md, "test.json")
    assert len(records) >= 1
    assert any(r.param_name for r in records)
    print("✓ test_extract_parameters passed")


def test_extract_results():
    """Test result extraction."""
    md = """
| 账本 | 最差相对残差 | 判据 | 结论 |
|---|---|---|---|
| mass | 2.72e-13 | 1.00e-12 | PASS |
| momentum | 2.91e-13 | 1.00e-10 | PASS |
"""
    records = extract_results_from_md(md, "test.json")
    assert len(records) >= 2
    # Check that metric_name is correctly extracted
    metric_names = [r.metric_name for r in records if r.metric_name]
    assert "mass" in metric_names or "momentum" in metric_names
    print("✓ test_extract_results passed")


def test_parse_report():
    """Test full report parsing."""
    report_path = "tensor_reports/p167w2_n10_report.json"
    if not os.path.exists(report_path):
        print("⊘ test_parse_report skipped (report not found)")
        return
    
    sample = parse_report(report_path)
    assert sample is not None
    assert sample.mission_id == 9705
    assert sample.title == "P167W2-N10 跨库场交换守恒性验证面：FEM→DEM 四账本 + LBM→FVM 插值守恒 + 时钟同步语义"
    assert len(sample.geometry) >= 1
    assert len(sample.parameters) >= 1
    assert len(sample.results) >= 1
    assert not sample.is_synthetic
    print("✓ test_parse_report passed")


def test_generate_synthetic_samples():
    """Test synthetic sample generation."""
    from dataclasses import dataclass, field
    from scripts.extract_report_knowledge import KnowledgeSample, ResultRecord
    
    base = KnowledgeSample()
    base.sample_id = "KS-base"
    base.mission_id = 9705
    base.title = "Test"
    base.tags = ["test"]
    base.domain = "general"
    base.results = [ResultRecord(metric_name="mass", value=2.72e-13, status="PASS")]
    
    synthetic = generate_synthetic_samples(base, 3)
    assert len(synthetic) == 3
    assert all(s.is_synthetic for s in synthetic)
    assert all(s.sample_id != base.sample_id for s in synthetic)
    # Check that synthetic tags are present
    assert all(any("synthetic_v" in t for t in s.tags) for s in synthetic)
    print("✓ test_generate_synthetic_samples passed")


def test_output_files():
    """Test that output files exist and are valid."""
    output_dir = Path("artifacts/knowledge_samples")
    if not output_dir.exists():
        print("⊘ test_output_files skipped (output not found)")
        return
    
    # Check JSONL
    jsonl_path = output_dir / "knowledge_samples.jsonl"
    assert jsonl_path.exists()
    with open(jsonl_path) as f:
        samples = [json.loads(line) for line in f]
    assert len(samples) >= 100
    print(f"✓ test_output_files passed ({len(samples)} samples)")


if __name__ == "__main__":
    test_parse_markdown_tables()
    test_extract_geometry()
    test_extract_parameters()
    test_extract_results()
    test_parse_report()
    test_generate_synthetic_samples()
    test_output_files()
    print("\n✅ All tests passed!")
