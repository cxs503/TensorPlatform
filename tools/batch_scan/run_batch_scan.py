#!/usr/bin/env python3
"""PC-1 千工况批量执行器 — 主入口

完整流程：
  1. 生成 YAML 工况清单（120+ cases）
  2. 并发执行全部工况
  3. 生成端到端报告（含 4 张可视化图）
  4. 验证机器判据
"""
import sys
import subprocess
from pathlib import Path

def main():
    print("=" * 60)
    print("PC-1 千工况批量执行器 — 完整流程")
    print("=" * 60)
    
    # Step 1: 生成工况清单
    print("\n[Step 1/3] Generating case list...")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "case_generator.py")],
        cwd=Path(__file__).parent.parent.parent,
    )
    if result.returncode != 0:
        print("ERROR: Case generation failed")
        return 1
    
    # Step 2: 执行批量扫描
    print("\n[Step 2/3] Running batch scan...")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "batch_executor.py")],
        cwd=Path(__file__).parent.parent.parent,
    )
    if result.returncode != 0:
        print("ERROR: Batch execution failed")
        return 1
    
    # Step 3: 生成报告
    print("\n[Step 3/3] Generating report...")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "report_generator.py")],
        cwd=Path(__file__).parent.parent.parent,
    )
    if result.returncode != 0:
        print("ERROR: Report generation failed")
        return 1
    
    print("\n" + "=" * 60)
    print("✓ PC-1 千工况批量执行器 完成")
    print("=" * 60)
    print("\n产物:")
    print("  - tools/batch_scan/case_list.yaml (工况清单)")
    print("  - artifacts/m642/batch_report.json (JSON 报告)")
    print("  - artifacts/m642/*.png (可视化图)")
    print("  - docs/m642/batch_scan_report.md (Markdown 报告)")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
