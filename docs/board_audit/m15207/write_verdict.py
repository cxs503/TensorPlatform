#!/usr/bin/env python3
"""m15207（board-audit）落表脚本：把 m15177 的裁定写入 tensor_maturity_verdicts。

设计要点
  1. 证据指针**落在本审计工作树内**（docs/board_audit/m15207/refs/…）：被审工作树
     /root/tp-agent/work/m15177 是别人的目录，随时可能被回收；本脚本先把被审工件
     （result.json / 两份底账 CSV / 5 图 / 源码 5 模块 / 报告 md）原样复制进 refs/，
     再用相对路径写 evidence，保证 maturity_gate 的 files_exist 将来还能复核。
  2. 缺失的证据键**显式置 null**（integration_callers / benchmark / external_anchor），
     不拿文字凑数：L3/L4 缺就是缺，让机器门把级别压回到 L2。
  3. INSERT … ON CONFLICT (repo, scope, verdict_by) DO UPDATE，重复跑幂等。

用法：python3 docs/board_audit/m15207/write_verdict.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = "tensorplatform"
HERE = Path(__file__).resolve().parent
WT = HERE.parent.parent.parent            # /root/tp-agent/work/m15207
M15177 = Path("/root/tp-agent/work/m15177")
REFS = HERE / "refs"
AUDIT_MID = 15207
TARGET_MID = 15177
REPO = "TensorPlatform"
SCOPE = "m15177 KPI-TRACE-100"


def stage_refs() -> dict:
    """把被审工件复制进 refs/，返回相对路径映射。"""
    art_src = M15177 / "artifacts" / "kpi_trace_100"
    art_dst = REFS / "artifacts" / "kpi_trace_100"
    impl_dst = REFS / "impl"
    art_dst.mkdir(parents=True, exist_ok=True)
    impl_dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(art_src.glob("*")):
        if p.is_file():
            shutil.copy2(p, art_dst / p.name)
    for p in sorted((M15177 / "tools" / "kpi_trace").glob("*.py")):
        shutil.copy2(p, impl_dst / p.name)
    for p in [M15177 / "tensor_reports" / "m15177_report.json",
              M15177 / "docs" / "KPI_TRACE_100_MEASUREMENT.md",
              M15177 / "tests" / "test_kpi_trace_tool.py",
              M15177 / "tests" / "test_kpi_trace_100_measure.py"]:
        if p.exists():
            shutil.copy2(p, REFS / p.name)
    return {"art": art_dst.relative_to(WT).as_posix(),
            "impl": impl_dst.relative_to(WT).as_posix()}


def build_evidence(paths: dict) -> dict:
    art = paths["art"]
    impl = paths["impl"]
    verify = json.loads((HERE / "verify_output.json").read_text(encoding="utf-8"))
    real = verify["C3_real_value"]
    fp = verify["C4_synthetic_fingerprint"]
    return {
        # gate 的 files_exist 要求**纯路径**（含说明文字会被判缺失），细节另放 impl_files_detail
        "impl_files": [
            f"{impl}/measure.py", f"{impl}/entry.py", f"{impl}/synthetic.py",
            f"{impl}/__init__.py", f"{impl}/__main__.py",
        ],
        "impl_files_detail": {
            f"{impl}/measure.py": "run_measure():274-345 双数据源（synthetic/database）；compute_kpi():180-250 分子/分母/分状态/窗口",
            f"{impl}/entry.py": "CLI 入口 392-430（measure/figures/doc/run-all 四子命令）",
            f"{impl}/synthetic.py": "合成底账生成器 23-25 行 _DEFAULT_TERMINAL=14085 / _DEFAULT_TRACEABLE=7677（钉桩源）",
        },
        "entry": f"{impl}/entry.py",
        "run_artifacts": [
            f"{art}/result.json",
            f"{art}/terminal_missions.csv",
            f"{art}/reports_facts.csv",
            f"{art}/kpi_trace_100_queries.sql",
            f"{art}/fig_1_gap.png", f"{art}/fig_2_by_status.png",
            f"{art}/fig_3_weekly.png", f"{art}/fig_4_repo_7d.png",
            f"{art}/fig_5_debt.png",
        ],
        "tests": [
            "tests/test_kpi_trace_tool.py（m15207 独立复跑：15 passed, 23.85s）",
            "tests/test_kpi_trace_100_measure.py（同上）",
        ],
        # —— 以下三项缺失是实情，显式 null，不用文字凑数 ——
        "integration_callers": None,
        "benchmark": None,
        "repro_cmd": ("python3 -m tools.kpi_trace measure --synthetic --outdir artifacts/kpi_trace_100"
                      " → 复跑 9/9 数字字段逐位一致（verify_output.json C2）；"
                      f"database 模式 python3 -m tools.kpi_trace measure --outdir <tmp> → data_mode=database, "
                      f"{real['numerator']}/{real['denominator']}={real['pct']}%"),
        "external_anchor": None,
        "absent_reason": {
            "integration_callers": ("main 8c1db9f671335743ee08a25a3eabc37089647493 全仓 grep 'tools.kpi_trace' "
                                    "仅命中自带测试 tests/test_kpi_trace_tool.py（3 行），无 backend/api/CLI/CI 调用方"),
            "benchmark": ("tensor_benchmark_cases 全表 13 行锚点无 'KPI-TRACE-100'，"
                          "故本锚无 ref/tolerance 判据行，L4 不可判"),
            "external_anchor": "无文献/实验/前值对照登记；真值退化为本审计自写 SQL 复算（见 real_measurement）",
        },
        "real_measurement": {
            "source": "docs/board_audit/m15207/verify_m15177.py C3（SQL 独立书写，口径取自 kpi_registry trace_report_ratio.recompute_sql）",
            "at": real["at"],
            "numerator": real["numerator"], "denominator": real["denominator"], "pct": real["pct"],
            "by_status": real["by_status"],
            "report_id_channel_count": real["report_id_channel_count"],
            "tensor_reports_distinct_missions": real["tensor_reports_distinct_missions"],
        },
        "delivered_value": {
            "current": verify["C2_reproducibility"]["data_mode_reported"] and 54.5,
            "data_mode": "synthetic",
            "provenance": (f"synthetic.py 默认参数直接等于历史观测 terminal=14085/traceable=7677 → "
                           f"header 数字 7677/14085=54.5% 为钉桩复述，非实测"),
            "deviation_vs_real_pp": fp["abs_pp_deviation"],
        },
        "material_defects": [
            f"交付 headline 54.5% 为合成底账复述（synthetic 参数钉死在 m15120 观测值），真库实测 {real['pct']}%（差 {fp['abs_pp_deviation']} pp）",
            f"双通道指纹：交付称 missions_with_report_id={fp['committed_report_id_channel']}，真库 report_id 非空仅 {fp['real_report_id_channel']} 条（0.06%）",
            f"抽样 {fp['sample_n']} 行底账：id 在库命中 {fp['sample_ids_in_db']}，status 一致 {fp['sample_status_match']}，created_at 一致 {fp['sample_created_at_match']} → 底账非库导出",
            "artifacts/kpi_trace_100/kpi_trace_100_queries.sql 仅 114 字节占位（'Synthetic mode: no SQL executed'），报告 §4.1 算路 B 亦自记「未运行」",
            "报告 §6 称「计算侧误差=0（算路一致）」与 §4 算路 B/C 均未运行互相矛盾（无多算路可对齐）",
            "报告附录 repro_cmd 只给 --synthetic，任何人按它复算只复述合成数字，拿不到真值",
        ],
        "honest_disclosure": "报告头部与 §5.3 均标注 data_mode=synthetic，未隐瞒模式；缺陷在「把合成复述当锚点计量值交付」",
        "rework_recommendation": ("同一工具 database 模式已能跑出真值：以 python3 -m tools.kpi_trace measure "
                                  f"--outdir artifacts/kpi_trace_100 重出报告（{real['pct']}%），"
                                  "并补 tensor_benchmark_cases 一行 recompute SQL 锚（ref=trace_report_ratio 口径）才可谈 L4"),
    }


def insert(evidence: dict) -> tuple[int, str]:
    sql = f"""
INSERT INTO tensor_maturity_verdicts (repo, scope, mission_id, level, evidence, verdict_by)
VALUES ('{REPO}', '{SCOPE}', {AUDIT_MID}, 2, $$ {json.dumps(evidence, ensure_ascii=False)} $$::jsonb, 'board-audit')
ON CONFLICT (repo, scope, verdict_by) DO UPDATE
  SET level = EXCLUDED.level, evidence = EXCLUDED.evidence,
      mission_id = EXCLUDED.mission_id
RETURNING id, coalesce(gate_state,''), coalesce(gate_level,-1);
"""
    Path("/tmp/m15207_verdict.sql").write_text(sql, encoding="utf-8")
    r = subprocess.run(["su", "postgres", "-c",
                        f"psql -d {DB} -t -A -F '|' -f /tmp/m15207_verdict.sql"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"insert failed: {r.stderr[:600]}")
    line = [ln for ln in r.stdout.strip().splitlines() if ln.count("|") >= 2][-1]
    vid, state, glevel = line.split("|")[:3]
    return int(vid), f"gate_state={state or 'pending'} gate_level={glevel}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    paths = stage_refs()
    ev = build_evidence(paths)
    (HERE / "verdict_evidence.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evidence staged → {(HERE / 'verdict_evidence.json').relative_to(WT)}")
    if a.dry_run:
        return 0
    vid, state = insert(ev)
    print(f"verdict id={vid} {state}")
    (HERE / "verdict_inserted.json").write_text(json.dumps(
        {"verdict_id": vid, "state": state, "at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())