#!/usr/bin/env python3
"""
自动化测试覆盖率监控脚本
每5分钟执行一次，记录覆盖率并汇报进展
"""
import subprocess
import sys
from datetime import datetime


def run_coverage():
    """运行覆盖率测试"""
    print(f"[{datetime.now().isoformat()}] 开始覆盖率测试...")

    # 运行 pytest with coverage
    result = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-v", "--cov=.", "--cov-report=term"],
        capture_output=True,
        text=True,
        cwd="/root/open_digital_foundation/open-digital-foundation/backend",
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    return 0


if __name__ == "__main__":
    run_coverage()
