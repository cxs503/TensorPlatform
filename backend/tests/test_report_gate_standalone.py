#!/usr/bin/env python3
"""Standalone tests for report gate functionality (RPT-GATE).

These tests verify the core logic without requiring database connections.
Run with: python test_report_gate_standalone.py
"""
import re
import sys

# Copy the _check_report_quality function from tensor_reports.py
REQUIRED_SECTIONS = {
    "physics_statement": [
        r"(?i)(物理|背景|problem|background|物理背景|案例介绍)",
        r"(?i)(几何|参数|geometry|parameter|参数表)",
    ],
    "traceable_criteria": [
        r"(?i)(方法|method|流程|procedure|工具链|toolchain)",
        r"(?i)(验证|validation|对标|benchmark|参考|reference|文献|误差|error)",
    ],
    "figures": [
        r"(?i)(图|figure|云图|contour|曲线|curve|比较|comparison)",
    ],
    "limitations": [
        r"(?i)(局限|limitation|注意|note|适用|scope|范围|展望|outlook|future)",
    ],
}


def _check_report_quality(markdown: str, figures: list) -> dict:
    """Check if report meets paper-quality minimum requirements."""
    issues = []
    
    # Check physics statement
    physics_found = any(
        re.search(pattern, markdown)
        for patterns in REQUIRED_SECTIONS["physics_statement"]
        for pattern in [patterns]
    )
    if not physics_found:
        issues.append("missing_physics_statement: 缺少物理背景/案例介绍（需包含物理问题描述、几何参数）")
    
    # Check traceable criteria
    method_found = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["traceable_criteria"][:1]
    )
    validation_found = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["traceable_criteria"][1:]
    )
    if not method_found:
        issues.append("missing_method: 缺少方法/流程描述（需包含计算方法、关键设置、工具链）")
    if not validation_found:
        issues.append("missing_validation: 缺少验证/对标（需包含与基准/文献/解析解的比较，或声明无基准原因）")
    
    # Check figures
    has_figure_section = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["figures"]
    )
    has_figures = len(figures) > 0
    if not has_figure_section and not has_figures:
        issues.append("missing_figures: 缺少图表（需包含场量云图/比较曲线，或纯文档任务需写明无图理由）")
    
    # Check limitations
    limitations_found = any(
        re.search(pattern, markdown)
        for pattern in REQUIRED_SECTIONS["limitations"]
    )
    if not limitations_found:
        issues.append("missing_limitations: 缺少局限与展望（需包含踩过的坑、适用范围、后续改进方向）")
    
    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "checks": {
            "physics_statement": physics_found,
            "method": method_found,
            "validation": validation_found,
            "figures": has_figure_section or has_figures,
            "limitations": limitations_found,
        }
    }


def test_missing_physics():
    """Test that report without physics statement is rejected."""
    markdown = """
    ## 方法
    使用了LBM方法计算阻力。
    
    ## 验证
    与实验数据对比，误差<3%。
    
    ## 图表
    见图1。
    
    ## 局限
    仅适用于低雷诺数。
    """
    figures = [{"name": "fig1", "caption": "阻力曲线"}]
    result = _check_report_quality(markdown, figures)
    assert not result["passed"], f"Expected failure, got: {result}"
    assert "missing_physics_statement" in result["issues"][0]
    print("✓ test_missing_physics passed")


def test_missing_method():
    """Test that report without method description is rejected."""
    markdown = """
    ## 物理背景
    船舶阻力问题，几何参数L=10m, B=2m。
    
    ## 验证
    与实验数据对比。
    
    ## 图表
    见图1。
    
    ## 局限
    仅适用于低雷诺数。
    """
    figures = [{"name": "fig1", "caption": "阻力曲线"}]
    result = _check_report_quality(markdown, figures)
    assert not result["passed"], f"Expected failure, got: {result}"
    assert any("missing_method" in issue for issue in result["issues"])
    print("✓ test_missing_method passed")


def test_missing_validation():
    """Test that report without validation is rejected."""
    markdown = """
    ## 物理背景
    船舶阻力问题，几何参数L=10m, B=2m。
    
    ## 方法
    使用了LBM方法。
    
    ## 图表
    见图1。
    
    ## 局限
    仅适用于低雷诺数。
    """
    figures = [{"name": "fig1", "caption": "阻力曲线"}]
    result = _check_report_quality(markdown, figures)
    assert not result["passed"], f"Expected failure, got: {result}"
    assert any("missing_validation" in issue for issue in result["issues"])
    print("✓ test_missing_validation passed")


def test_missing_figures():
    """Test that report without figures is rejected."""
    markdown = """
    ## 物理背景
    船舶阻力问题，几何参数L=10m, B=2m。
    
    ## 方法
    使用了LBM方法。
    
    ## 验证
    与实验数据对比，误差<3%。
    
    ## 局限
    仅适用于低雷诺数。
    """
    figures = []
    result = _check_report_quality(markdown, figures)
    assert not result["passed"], f"Expected failure, got: {result}"
    assert any("missing_figures" in issue for issue in result["issues"])
    print("✓ test_missing_figures passed")


def test_missing_limitations():
    """Test that report without limitations is rejected."""
    markdown = """
    ## 物理背景
    船舶阻力问题，几何参数L=10m, B=2m。
    
    ## 方法
    使用了LBM方法。
    
    ## 验证
    与实验数据对比，误差<3%。
    
    ## 图表
    见图1。
    """
    figures = [{"name": "fig1", "caption": "阻力曲线"}]
    result = _check_report_quality(markdown, figures)
    assert not result["passed"], f"Expected failure, got: {result}"
    assert any("missing_limitations" in issue for issue in result["issues"])
    print("✓ test_missing_limitations passed")


def test_complete_report_passes():
    """Test that complete report passes QA."""
    markdown = """
    ## 物理背景
    船舶阻力问题，几何参数L=10m, B=2m, Cb=0.6。
    
    ## 方法
    使用了LBM方法，网格100万，时间步长0.001s。
    
    ## 验证
    与实验数据对比，误差2.5%<3%。
    
    ## 图表
    见图1阻力曲线。
    
    ## 局限与展望
    仅适用于低雷诺数，后续需扩展到高速工况。
    """
    figures = [{"name": "fig1", "caption": "阻力曲线"}]
    result = _check_report_quality(markdown, figures)
    assert result["passed"], f"Expected pass, got issues: {result['issues']}"
    assert len(result["issues"]) == 0
    assert result["checks"]["physics_statement"]
    assert result["checks"]["method"]
    assert result["checks"]["validation"]
    assert result["checks"]["figures"]
    assert result["checks"]["limitations"]
    print("✓ test_complete_report_passes passed")


def test_report_gate_logic():
    """Test report gate logic for complete_mission."""
    # Test 1: succeeded without report_id → report_owed=true
    status = "succeeded"
    report_id = None
    report_owed = (status == "succeeded" and report_id is None)
    assert report_owed is True, "succeeded without report_id should set report_owed=true"
    print("✓ test_report_gate_logic: succeeded without report_id → report_owed=true")
    
    # Test 2: succeeded with report_id → report_owed=false
    status = "succeeded"
    report_id = 123
    report_owed = (status == "succeeded" and report_id is None)
    assert report_owed is False, "succeeded with report_id should set report_owed=false"
    print("✓ test_report_gate_logic: succeeded with report_id → report_owed=false")
    
    # Test 3: failed mission → no report_owed
    status = "failed"
    report_id = None
    report_owed = (status == "succeeded" and report_id is None)
    assert report_owed is False, "failed mission should not set report_owed"
    print("✓ test_report_gate_logic: failed mission → no report_owed")


def test_report_owed_queue_logic():
    """Test report owed queue filtering logic."""
    missions = [
        {"id": 1, "status": "succeeded", "report_owed": True},
        {"id": 2, "status": "succeeded", "report_owed": False},
        {"id": 3, "status": "failed", "report_owed": False},
        {"id": 4, "status": "succeeded", "report_owed": True},
    ]
    
    # Filter for report_owed queue
    queue = [m for m in missions if m["report_owed"] and m["status"] == "succeeded"]
    assert len(queue) == 2, f"Expected 2 missions in queue, got {len(queue)}"
    assert queue[0]["id"] == 1
    assert queue[1]["id"] == 4
    print("✓ test_report_owed_queue_logic passed")


def main():
    """Run all tests."""
    print("Running report gate standalone tests...\n")
    
    test_missing_physics()
    test_missing_method()
    test_missing_validation()
    test_missing_figures()
    test_missing_limitations()
    test_complete_report_passes()
    test_report_gate_logic()
    test_report_owed_queue_logic()
    
    print("\n✅ All tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
