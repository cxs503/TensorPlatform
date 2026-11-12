#!/usr/bin/env python3
"""m15482：把 m15444 (GAP-PP-5-gpu-cluster) 交差核验判级写入 tensor_maturity_verdicts。

判级=功能实质：L2（真代码 input→compute→output，本单独立复跑逐位复现 τ=0.89971）。
非 L3：平台侧无调用方（grep 空）；非 L4：tensor_benchmark_cases 无该 anchor 行（基准裁判缺位），
且 repro_cmd 矩阵与 run_artifacts 不符 + 依赖 TensorLBM 未提交补丁。
evidence 全部指针由 docs/board_audit/m15482/{verify_triple,repro_check}.py 程序化产出。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WT = Path("/root/tp-agent/work/m15482")
RUN = WT / "runs/m15482_audit"
M = Path("/root/tp-agent/work/m15444")

triple = json.loads((RUN / "triple_check.json").read_text())
repro = json.loads((RUN / "repro_check.json").read_text())
delivered = next(r for r in repro["runs"] if r["tag"] == "delivered_worktree")
head = next(r for r in repro["runs"] if r["tag"] == "clean_head")

LEVEL = 2

evidence = {
    "os": ("board-audit m15482 of m15444 (GAP-PP-5-gpu-cluster), repo TensorPlatform, "
           f"PR #1266 merged (commit f97aed771); TensorLBM HEAD={repro['tensorlbm_head'][:12]}"),
    "impl_files": [
        f"{M}/artifacts/gap_pp5_cpu_baseline/bench_diff_lbm_cpu.py",
        f"{M}/artifacts/gap_pp5_cpu_baseline/analyze.py",
        f"{M}/artifacts/gap_pp5_cpu_baseline/entry_gap_pp5.py",
        f"{M}/tests/test_gap_pp5_cpu_baseline.py",
    ],
    "impl_notes": [
        "bench_diff_lbm_cpu.py:1-237 — subprocess 驱动 TensorLBM examples/differentiable_lbm.py 的配置矩阵基准（wall/RSS/stdout 解析）",
        "analyze.py:1-172 — results.json→summary.md+收敛图，judge_error 15% 判据",
        "entry_gap_pp5.py:1-78 — bench→analyze 串联入口",
        "tests/test_gap_pp5_cpu_baseline.py:1-176 — _as_float 梯度保护/harness 结构/端到端 τ 恢复，11 用例",
    ],
    "entry": f"{M}/artifacts/gap_pp5_cpu_baseline/entry_gap_pp5.py",
    "entry_cmd": "python3 artifacts/gap_pp5_cpu_baseline/entry_gap_pp5.py --grids 64 --steps 50 --iters 60 --reps 1 --out runs/gap_pp5_cpu_baseline",
    "run_artifacts": [
        f"{M}/runs/gap_pp5_cpu_baseline/results.json",
        f"{M}/runs/gap_pp5_cpu_baseline/wallclock.csv",
        f"{M}/runs/gap_pp5_cpu_baseline/summary.md",
        f"{M}/runs/gap_pp5_cpu_baseline/rep_0/convergence_tau.csv",
        f"{M}/runs/gap_pp5_cpu_baseline/convergence_rep0.png",
        f"{RUN}/repro_check.json",
        f"{RUN}/triple_check.json",
        f"{RUN}/pytest_m15444.txt",
        f"{RUN}/fig_tau_recovery.png",
    ],
    "run_artifacts_notes": [
        "results.json — 1 config × 1 rep, device=CPU, tau_got=0.89971, error_pct=0.03",
        "wallclock.csv — 1 行, median_wall_s=33.5047（单样本，字段名 median 但 reps=1）",
        "rep_0/convergence_tau.csv — 60 行, loss 3.608e-06→2.971e-12",
        "repro_check.json — 本单独立复跑: delivered τ=0.89971/0.03% PASS, clean TensorLBM HEAD τ=0.55/38.89% FAIL",
    ],
    "tests": [
        "tests/test_gap_pp5_cpu_baseline.py::TestAsFloatGradientPreservation::test_as_float_returns_none_for_tensor",
        "tests/test_gap_pp5_cpu_baseline.py::TestAsFloatGradientPreservation::test_check_relaxation_time_preserves_tensor",
        "tests/test_gap_pp5_cpu_baseline.py::TestAsFloatGradientPreservation::test_as_float_still_works_for_scalars",
        "tests/test_gap_pp5_cpu_baseline.py::TestEndToEnd (slow, 真跑 differentiable_lbm.py)",
        "本单实跑: cd work/m15444 && python3 -m pytest tests/test_gap_pp5_cpu_baseline.py -q → 11 passed, 2 warnings, 15.81s",
    ],
    "integration_callers": ("none: grep -rn --include='*.py' -E 'gap_pp5|bench_diff_lbm|entry_gap_pp5' "
                            "work/m15444/backend work/m15444/api → 空；harness 为独立诊断脚本，未接入平台/主求解器 → 不达 L3"),
    "benchmark": {
        "registered_case": ("none: SELECT case_name,metric,tolerance FROM tensor_benchmark_cases "
                            "WHERE anchor_slug='GAP-PP-5-gpu-cluster' → 0 行（表内 16 个 anchor_slug，无 GAP-PP-5-*）"),
        "ref_source": "tau*=0.9 exact by construction（已知 τ 的前向模型造目标场，再反解 τ）；解析锚=BGK 剪切波衰减 ν=(τ-0.5)/3",
        "tau_recovery_recorded": {"ref": 0.9, "got": 0.89971, "error_pct": 0.03,
                                  "error_pct_recomputed": 0.0322, "within_15pct": True},
        "tau_recovery_audit_rerun": {"delivered_worktree": {"got": delivered["tau_got"], "error_pct": delivered["error_pct"],
                                                            "wall_s": delivered["wall_s"], "within_15pct": True},
                                     "clean_tensorlbm_head": {"got": head["tau_got"], "error_pct": head["error_pct"],
                                                              "wall_s": head["wall_s"], "within_15pct": False}},
        "wallclock_reproducibility": {"recorded_wall_s": 33.5047, "audit_wall_s": delivered["wall_s"],
                                      "ratio": repro["conclusion"]["wallclock_reproducible_vs_recorded"]["ratio"],
                                      "note": "单 rep 无方差；两次独立测量差 1.25× → 吞吐表只到量级可信"},
        "formal_benchmark_judged": False,
    },
    "repro_cmd": ("python3 docs/board_audit/m15482/repro_check.py ; "
                  "本单实测: delivered=0.89971/0.03% PASS, clean HEAD=0.55/38.89% FAIL"),
    "external_anchor": ("BGK shear-wave decay analytic u=U0 sin(ky) exp(-νk²t), ν=(τ-0.5)/3 "
                        "(Krueger et al., The Lattice Boltzmann Method, Springer 2017, ch.4/10)；"
                        "与 TensorLBM benchmarks/verified/shear_wave_decay 同载体（物理锚，非实验/文献误差对照）"),
    "report": ("tensor_reports mission_id=15444 → 0 行；tensor_dev_missions.report_id=NULL（m15444 未发报告）。"
               "同 anchor 上一轮 m15359 报告 id=14445。m15444 的文档证据=docs/GAP-PP-5_CPU_BASELINE.md（7 段：问题/方法/参数/ref-vs-got/墙钟/失败面/判级）"),
    "level_rationale": ("L2: 真代码（输入=CLI 配置→计算=驱动 TensorLBM 可微 LBM 反解 τ→输出=results.json/csv/png），"
                        "本单独立复跑逐位复现 tau_got=0.89971（0.03%<15%），pytest 11 passed。"
                        "非 L3（无平台/求解器调用方）；非 L4（tensor_benchmark_cases 无该 anchor 行=基准裁判缺位，"
                        "repro_cmd 矩阵 6 配置×2 reps 与 run_artifacts 1×1 不符，且复现依赖 TensorLBM 工作树未提交补丁）；"
                        "非 L5（无实验/文献误差对照）。"),
    "findings": {
        "F1_no_benchmark_case": "tensor_benchmark_cases 无 GAP-PP-5-gpu-cluster 行 → 基准裁判缺位，L4 无法机器判；建议注册 case（tau_recovery, tol=15%）。不阻塞 L2 消账。",
        "F2_repro_cmd_matrix_mismatch": ("docs §6.4 repro_cmd 用 --grids 64,128 --steps 10,50,200 --reps 2（6 配置×2 reps），"
                                         "而 run_artifacts 的 results.json 是 configs=1, reps=1 → 照抄文档命令复现不出所报工件（triple_check.json repro_cmd.matrix_match=false）。"),
        "F3_fix_not_versioned": ("核心修复（input_contract._as_float 的 torch.Tensor 守卫）只存在于 TensorLBM 工作树的未提交改动"
                                 "（git status: M src/tensorlbm/input_contract.py）；本单以 HEAD 版 input_contract 复跑得 τ=0.55 / 38.89% FAIL，"
                                 "即'修复'TensorLBM bug'的表述在该仓未落库，干净检出不可复现。"),
        "F4_report_missing": "m15444 report_id=NULL 且 tensor_reports 无 mission_id=15444 行 → 报告纪律欠账（本轮由本审计单报告补位，不能替代原单账）。",
        "F5_handtyped_numbers": ("docs §4.3 剪切波三行 ν 值（0.03496/0.13397/0.29575）全仓扫描只出现在该文档（tests/run/artifacts 均无产出源），"
                                 "与文档抬头'所有数字由 results.json 程序化产出'不符 → 手抄数字（未影响主判据 tau 恢复）。"),
        "F6_loss_parse_bug": ("bench_diff_lbm_cpu.py 解析 loss 的正则 [\\deE+-]+ 不含小数点，"
                              "对 'loss 3.608e-06 -> 2.971e-12' 无匹配 → results.json loss_final 恒 null（复现: python3 -c re.search 验证 None）。低级缺陷，不影响 τ 判据。"),
        "F7_timing_single_sample": "wallclock.csv 字段名 median_wall_s 但 reps=1（无 median 可言）；本单复跑 41.99s vs 记录 33.50s（1.25×），吞吐绝对值仅供量级参考。",
        "F8_gpu_cluster_unmet": "无 GPU 节点、集群作业未完成（m15359 cj1081 queued）；anchor 的 gpu-cluster 半边由'documented CPU baseline'替代口径满足，属 owner 允许的第二选项，如实记录不粉饰。",
    },
}

sql_ev = json.dumps(evidence, ensure_ascii=False)
sql = (
    "INSERT INTO tensor_maturity_verdicts (repo, scope, mission_id, level, evidence, verdict_by) VALUES "
    "('TensorPlatform', 'm15444 GAP-PP-5-gpu-cluster', %d, %d, $json$%s$json$::jsonb, 'board-audit')"
    % (15482, LEVEL, sql_ev)
)
r = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform",
                    "-v", "ON_ERROR_STOP=1", "-tAc", sql], capture_output=True, text=True)
print("rc=", r.returncode, r.stdout.strip(), r.stderr.strip()[:400])
if r.returncode != 0:
    sys.exit(1)

chk = subprocess.run(["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-tAc",
                      "SELECT id,repo,scope,mission_id,level,verdict_by FROM tensor_maturity_verdicts "
                      "ORDER BY id DESC LIMIT 2"], capture_output=True, text=True)
print(chk.stdout.strip())