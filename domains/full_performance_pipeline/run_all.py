#!/usr/bin/env python3
"""IN61-N11 一键复算：跑全部合成算例 → 契约审计 → 证据统计 → 图 → 报告。

用法：
    python3 domains/full_performance_pipeline/run_all.py            # 全量（含图/文档）
    python3 domains/full_performance_pipeline/run_all.py --no-doc    # 只出 JSON+图
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from domains.full_performance_pipeline.report import DOC_PATH, OUT_DIR, build_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--no-doc", action="store_true")
    args = ap.parse_args()
    rep = build_report(out_dir=args.out, write_doc=not args.no_doc)
    j = rep["judgement"]
    print("=" * 78)
    print(f"IN61-N11 集成验证  all_criteria_pass = {rep['all_criteria_pass']}")
    print(f"  J1 链路完整   : {j['J1_end_to_end_link']['edges']} 边 / "
          f"{j['J1_end_to_end_link']['modules']} 模块，残差 "
          f"{j['J1_end_to_end_link']['max_fixed_point_residual_m_s']:.2e} m/s → "
          f"{'PASS' if j['J1_end_to_end_link']['PASS'] else 'FAIL'}")
    print(f"  J2 时延       : 最坏 {j['J2_latency']['worst_e2e_ms']:.1f} ms < "
          f"{j['J2_latency']['limit_ms']:.0f} ms → {'PASS' if j['J2_latency']['PASS'] else 'FAIL'}")
    print(f"  J3 契约       : {j['J3_contract']['required_fields']} 字段, 缺失/错配 "
          f"{j['J3_contract']['missing_or_mismatch']} → {'PASS' if j['J3_contract']['PASS'] else 'FAIL'}")
    ev = j['J4_evidence_report']
    print(f"  J4 证据统计   : {ev['fields_total']} 字段 {ev['field_counts']}；"
          f"门自检 gate_ok={ev['gate_self_test']['gate_ok']} → {'PASS' if ev['PASS'] else 'FAIL'}")
    ver = rep["verification"]
    print(f"  验证表        : {ver['judged']} 项判定, 通过 {ver['passed_3pct']}, "
          f"未通过 {ver['failed_3pct']}, 最大相对误差 {ver['max_rel_error']:.2%}")
    print(f"  产物          : {os.path.relpath(os.path.join(args.out, 'integration_report.json'))}")
    print(f"                  {os.path.relpath(DOC_PATH) if not args.no_doc else '(doc skipped)'}")
    print("=" * 78)
    return 0 if rep["all_criteria_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
