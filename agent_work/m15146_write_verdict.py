#!/usr/bin/env python3
"""m15146：把 m15116 (KPI-QC-RECHECK) 交差核验判级写入 tensor_maturity_verdicts（verdict_by=board-audit）。"""
import json
import subprocess
from pathlib import Path

ROOT = Path("/root/tp-agent/work/m15146")

evidence = {
    "os": "board-audit m15146 of m15116 (KPI-QC-RECHECK), repo TensorPlatform, merged PR #1223 (deliver commit 7a4719e29, merge de31755b8)",
    "impl_files": [
        "artifacts/kpi_qc_recheck/measure.py:1-487 (只读计量脚本：SQL_CORE 口径绑定:53-92、git 交叉:180-210、阶段切分:312-327、时延:255-263、分仓:270-278)",
        "artifacts/kpi_qc_recheck/recompute.sql:1-28 (独立第二实现，手写单条 SQL)",
        "artifacts/kpi_qc_recheck/make_figures.py:1-166 (只读 kpi_result.json 出 6 图)",
        "artifacts/kpi_qc_recheck/post_report.py:1-82 (报告发布)",
        "docs/KPI_QC_RECHECK_20261111.md:1-212 (计量报告)",
    ],
    "entry": "python3 artifacts/kpi_qc_recheck/measure.py [--days 30] [--outdir artifacts/kpi_qc_recheck]",
    "run_artifacts": [
        "/root/tp-agent/work/m15116/artifacts/kpi_qc_recheck/kpi_result.json (17254B, current=5.16, N=5315, M=274, gap=94.84)",
        "/root/tp-agent/work/m15116/artifacts/kpi_qc_recheck/{per_repo,daily,latency,unreviewed_prs}.csv (unreviewed_prs.csv=5041 数据行)",
        "/root/tp-agent/work/m15116/artifacts/kpi_qc_recheck/fig1..fig6.png (6 图非空)",
        "runs/m15146_audit/repro_kpi_result.json (本单 19:57Z 独立复跑 measure.py 工件：N=5334, M=274, rate=5.14%)",
    ],
    "tests": "无 pytest 单测（计量单，未改平台代码）；L1 复算=recompute.sql 独立第二实现，本单复跑 5334|274|5.14（分子逐位一致）；本单另写 4 份独立 SQL（runs/m15146_audit/m15146_audit_recompute*.sql）交叉验证",
    "integration_callers": "none：grep -rn 'kpi_qc_recheck' --include=*.py 仅同目录 post_report.py 读产物发报告；无 api/backend/CI/战役门调用方 → 不达 L3",
    "benchmark": {
        "registered_case": "none：SELECT ... FROM tensor_benchmark_cases WHERE anchor_slug='KPI-QC-RECHECK' 返回 0 行（表内 13 个 anchor_slug 均非 KPI-QC-*）",
        "ref_source": "源表 mission_lifecycle_log(pr_gate merged) + outcome_evaluations，本单自写 SQL 复算",
        "N_denominator": {"ref": 5315, "got": "5324/5334", "error": "+0.17%/+0.36% live drift（报告⑤.6 预声明）", "within_tolerance": True},
        "M_numerator": {"ref": 274, "got": 274, "error": "0.00%", "within_tolerance": True},
        "rate": {"ref": "5.16%", "got": "5.14-5.15%", "error": "0.01-0.02pp（分母漂移）", "within_tolerance": True},
        "definition_ladder": {"d1": "690=690", "d2": "274=274", "d3": "3=3", "d0": "729（SQL=reviewed 行；标签『含 pending』失准，any_row=748）"},
        "phase_split": {"pre": "817/274/33.54% 逐位一致", "post": "4498/0→4510/0（漂移）"},
        "last_indep_conclusion": {"ref": "2026-11-07T06:43:24Z", "got": "2026-11-07T06:43:24Z", "error": "0.00%"},
        "pending_backlog": {"ref": 2871, "got": 2875, "error": "+4 live drift"},
        "git_cross_check": {"ref": "591/561 ratio 0.9492", "got": "逐日合计复核通过", "note": "5.08% 两源定义差，报告⑥如实声明 >3%"},
    },
    "repro_cmd": "cd /root/tp-agent/work/m15116 && python3 artifacts/kpi_qc_recheck/measure.py --outdir <out>  # 本单实测 RECOMPUTE: passed=274 merged=5334 rate=5.14%；psql -U postgres -d tensorplatform -f artifacts/kpi_qc_recheck/recompute.sql → 5334|274|5.14",
    "external_anchor": "none：无文献/解析解/外部基准；参考=平台自身源表复算 → 不达 L4",
    "evidence_mirror": [
        "runs/m15146_audit/evidence_verification.json (三件套逐项核验矩阵)",
        "runs/m15146_audit/m15146_audit_recompute{,2,3}.sql + m15146_audit_latency.sql (本单独立复算 SQL)",
        "runs/m15146_audit/repro_kpi_result.json (本单复跑工件)",
        "runs/m15146_audit/m15116_report_14247.md (原报告全文快照) / m15116_result_db.txt (mission result 快照)",
        "docs/reports/m15146_audit_m15116.md (本单审计报告，六要素)",
    ],
    "report": "tensor_reports id=14247 (m15116 原报告，六段齐全) / 本单审计报告见 docs/reports/m15146_audit_m15116.md",
    "level_rationale": "L2：真代码（输入=PG 源表→计算=SQL 聚合/口径阶梯/阶段切分→输出=JSON/CSV/6 图），可复跑、PR #1223 已合并；L3 缺集成调用方，L4 缺外部基准（且无注册 benchmark case）。",
    "findings": {
        "F1_no_benchmark_case": "tensor_benchmark_cases 无 KPI-QC-RECHECK 行 → 基准裁判无正式参考行，参考退化为源表复算；建议后续补注册（不阻塞消账）。",
        "F2_cluster_not_run": "cj984 仍 queued（19:27:42Z 递交，无 finished_at）→ 报告⑧如实记 not_run。",
        "F3_live_drift": "分母 5315→5334（29 分钟 +0.36%），分子恒 274；在报告预声明漂移带内。",
        "F4_git_crosscheck_shape": "git 交叉逐日形态偏（回写滞后/补记）；30 天 git=1045 vs 回写 561（覆盖 53.68%）如实声明。",
        "F5_d0_label": "D0 标签『含 pending 占位行』与 SQL（bool_or(review_status='reviewed')）不符（any_row=748 vs reviewed=729）；计数按 SQL 正确，标签轻微失准，不影响主口径 D2。",
    },
}

sql = (
    "INSERT INTO tensor_maturity_verdicts (repo, scope, mission_id, level, evidence, verdict_by) "
    "VALUES ('TensorPlatform', 'm15116 KPI-QC-RECHECK', 15146, 2, $json$%s$json$::jsonb, 'board-audit') "
    "ON CONFLICT (repo, scope, verdict_by) DO UPDATE SET level=EXCLUDED.level, evidence=EXCLUDED.evidence, mission_id=EXCLUDED.mission_id "
    "RETURNING id, gate_state;" % json.dumps(evidence, ensure_ascii=False)
)
sqlfile = ROOT / "runs" / "m15146_audit" / "insert_verdict.sql"
sqlfile.write_text(sql + "\n")
p = subprocess.run(["psql", "-U", "postgres", "-d", "tensorplatform", "-v", "ON_ERROR_STOP=1", "-f", str(sqlfile)],
                   capture_output=True, text=True)
print(p.stdout, p.stderr)
(ROOT / "runs" / "m15146_audit" / "verdict_payload.json").write_text(
    json.dumps({"repo": "TensorPlatform", "scope": "m15116 KPI-QC-RECHECK", "mission_id": 15146,
                "level": 2, "evidence": evidence, "verdict_by": "board-audit"},
               ensure_ascii=False, indent=1) + "\n")
