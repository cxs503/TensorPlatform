#!/usr/bin/env python3
"""KPI-REPRO-95 计量入口。

用法:
  python -m kpi_repro [--db-url URL] [--output-dir DIR] [--no-verify] [--no-figures]

环境变量:
  DATABASE_URL: 数据库连接字符串
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kpi_repro.measure import measure_kpi_repro, KPIResult
from kpi_repro.verify import verify_kpi_repro
from kpi_repro.figures import generate_figures

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("kpi_repro.cli")


def main():
    parser = argparse.ArgumentParser(description="KPI-REPRO-95 复现官记录通过率计量")
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"),
                        help="数据库连接字符串")
    parser.add_argument("--output-dir", default="runs/m15200_kpi_repro",
                        help="输出目录")
    parser.add_argument("--no-verify", action="store_true",
                        help="跳过独立复核")
    parser.add_argument("--no-figures", action="store_true",
                        help="跳过图生成")
    parser.add_argument("--synthetic", action="store_true",
                        help="使用合成数据（测试模式）")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.synthetic:
        logger.info("Running in synthetic mode (test data)")
        from kpi_repro.synthetic import generate_synthetic_data, measure_synthetic
        missions = generate_synthetic_data()
        kpi = measure_synthetic(missions)

        # 输出 result.json
        from dataclasses import asdict
        result_dict = asdict(kpi)
        result_file = out_dir / "result.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2, default=str)

        # 输出 rows.jsonl
        rows_file = out_dir / "rows.jsonl"
        with open(rows_file, "w", encoding="utf-8") as f:
            for m in missions:
                f.write(json.dumps(asdict(m), ensure_ascii=False, default=str) + "\n")

        # 输出 measure.sql
        sql_file = out_dir / "measure.sql"
        with open(sql_file, "w", encoding="utf-8") as f:
            f.write("-- KPI-REPRO-95 measurement SQL (synthetic mode)\n")
            f.write("-- This is a placeholder for synthetic data mode\n")
    else:
        logger.info("Running against database")
        kpi = measure_kpi_repro(db_url=args.db_url, output_dir=str(out_dir))

    # 打印摘要
    print("\n" + "=" * 60)
    print(f"KPI-REPRO-95 计量结果")
    print("=" * 60)
    print(f"  总任务数: {kpi.total}")
    print(f"  succeeded: {kpi.succeeded}")
    print(f"  通过 (strong+medium): {kpi.passed}")
    print(f"  主口径通过率: {kpi.current}%")
    print(f"  目标: {kpi.target}%")
    print(f"  缺口: {kpi.gap} pp")
    print(f"  上界通过率: {kpi.current_upper}%")
    print(f"  失败根因: {kpi.failure_root_causes}")
    print("=" * 60)

    # 独立复核
    if not args.no_verify:
        logger.info("Running verification...")
        vr = verify_kpi_repro(
            result_path=str(out_dir / "result.json"),
            rows_path=str(out_dir / "rows.jsonl"),
            output_dir=str(out_dir),
        )
        print(f"\n复核结果: {vr.passed_checks}/{vr.total_checks} 通过")
        if not vr.all_passed:
            failed = [c for c in vr.checks if not c["passed"]]
            for f in failed:
                print(f"  FAIL: {f['name']} - {f['detail']}")

    # 图生成
    if not args.no_figures:
        logger.info("Generating figures...")
        figures = generate_figures(
            result_path=str(out_dir / "result.json"),
            rows_path=str(out_dir / "rows.jsonl"),
            output_dir=str(out_dir / "figures"),
        )
        print(f"\n生成 {len(figures)} 张图")

    logger.info("Done. Output in %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
