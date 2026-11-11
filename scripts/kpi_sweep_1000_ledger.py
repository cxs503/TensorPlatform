#!/usr/bin/env python3
"""KPI-SWEEP-1000 最终计量汇总：把 samples/artifacts/cluster 三方计数合并成 result JSON + 图。

口径（三档，互斥归属，只计一次）：
  A 严格求解器批量口径 = 批量线逐工况工件（batch API 批量 + mission 级多工况工件），按 sha256 去重
  B 公式链批量口径     = A + ship_samples kb_*（无人值守程序化批量回填，公式链）
  C 宽口径             = B + geometry_validation 单点几何自洽行（无物理量，仅作容量上限参考）

headline（本单推荐 KPI 口径）= B，并附 L1 复算合格率作为质量门。
"""
from __future__ import annotations

import json
import sys

BASE = "artifacts/kpi_sweep_1000"
samples = json.load(open(f"{BASE}/samples_census.json"))
artifacts = json.load(open(f"{BASE}/artifact_census.json"))
cluster = json.load(open(f"{BASE}/stage/cluster_jobs_census.json"))

PLATFORM_BATCH = [a for a in artifacts["artifacts"]
                  if any(k in a["path"] for k in ("batch_sweep", "batch_report"))]
MISSION_BATCH = [a for a in artifacts["artifacts"] if a not in PLATFORM_BATCH]
n_platform = sum(a["cases"] for a in PLATFORM_BATCH)
n_mission = sum(a["cases"] for a in MISSION_BATCH)
n_kb = samples["tiers"]["T1b_kb_batch_cases"]
n_geo = samples["tiers"]["T2_geometry_validation_rows"]

A = n_platform + n_mission
B = A + n_kb
C = B + n_geo
TARGET = 1000

ident = samples["l1_identity_checks"]
def rate(ct, key):
    d = ident.get(ct, {}).get("details", {}).get(key)
    return None if not d else round(d["pass"] / max(d["n"], 1), 4)

quality = {
    "kb_stability.GM=KB+BM-KG": rate("kb_stability", "GM=KB+BM-KG"),
    "kb_stability.BM=B^2/12T": rate("kb_stability", "BM=B^2/12T"),
    "kb_resistance.Cf=ITTC57(Re)": rate("kb_resistance", "Cf=ITTC57(Re)"),
    "kb_resistance.Rt=sum(1e-3 tol)": rate("kb_resistance", "Rt=Rf+Rw+Ra"),
    "kb_propulsion.T=KT*rho*n^2*D^4": rate("kb_propulsion", "T=KT*rho*n^2*D^4"),
    "kb_propulsion.eta0=J*KT/(2*pi*KQ)": rate("kb_propulsion", "eta0=J*KT/(2pi*KQ)"),
    "kb_seakeeping.GM_T_m=GM_T": rate("kb_seakeeping", "GM_T_m=GM_T"),
    "kb_seakeeping.significant_heave=4*sigma(假设值)": rate("kb_seakeeping", "significant_heave=4*sigma_heave"),
    "kb_ice_navigation.R_ice=sum(components)": rate("kb_ice_navigation", "R_ice=sum(components)"),
    "kb_ice_navigation.rel_err_vs_reference": rate("kb_ice_navigation", "rel_err_vs_reference"),
    "kb_green_shipping.attained_ratio": rate("kb_green_shipping", "attained_ratio=attained/required"),
}

bsm = cluster["by_state_mode"]
shard = [r for r in bsm if r["mode"] == "shard"]
n_shard_ok = sum(r["tasks"] for r in shard if r["state"] == "succeeded")
n_shard_killed = sum(r["tasks"] for r in shard if r["state"] == "killed")
n_shard_run = sum(r["tasks"] for r in shard if r["state"] == "running")
n_shard_queued = sum(r["tasks"] for r in shard if r["state"] == "queued")

result = {
    "anchor": "KPI-SWEEP-1000",
    "measured_at": cluster["span"]["max"],
    "unit": "case(工况) = (求解链 identity, 参数向量) 唯一实例，被无人值守批量执行器算至终态并留可溯源记录",
    "target": TARGET,
    "current": B,
    "current_by_scope": {
        "A_strict_solver_batch": A,
        "B_formula_chain_batch(headline)": B,
        "C_wide_all_case_records": C,
    },
    "gap": max(TARGET - B, 0),
    "gap_by_scope": {k: max(TARGET - v, 0) for k, v in
                     {"A": A, "B": B, "C": C}.items()},
    "attainment_pct": {k: round(100.0 * v / TARGET, 1) for k, v in
                       {"A": A, "B": B, "C": C}.items()},
    "data_sources": [
        "PG/cluster_jobs (865 行, submitted_at %s ~ %s): state/mode/shards 统计" % (cluster["span"]["min"], cluster["span"]["max"]),
        "PG/ship_samples (23,176 行) + PG/kb_samples (11,861 行, 为 ship_samples kb_* 的镜像，只计一次): 逐工况记录",
        "/root/tp-agent/work/*/{artifacts,runs}/**/*.json: 157,831 文件 → sha256 去重 8,392 个不同工件 → 11 个含逐工况记录工件",
        "/root/tp-agent/work/m15118/artifacts/kpi_sweep_1000/artifact_census.json (逐工件 cases)",
        "/root/tp-agent/work/m15118/artifacts/kpi_sweep_1000/samples_census.json (L1 恒等式复算率)",
        "/root/tp-agent/work/m15118/artifacts/kpi_sweep_1000/stage/cluster_jobs_census.json (cluster SQL 结果快照)",
    ],
    "counting_rules": [
        "同一 sha256 工件 = 同一次执行（976 份 batch_sweep_demo.json 拷贝只计 51 工况，不去重将虚增 49,752）",
        "kb_samples 与 ship_samples kb_* 是同一批 11,861 行（case_id 一一对应），只计一次",
        "geometry_validation 11,315 行仅含 Cb 自洽性检查（无物理量/无工况参数），不计入 headline，只进宽口径",
        "cluster_jobs 的 job 数 ≠ 工况数（含 pytest/探针/单算例），故只作为执行侧旁证与瓶颈诊断，不直接折算工况",
    ],
    "evidence_breakdown": {
        "platform_batch_api_cases": n_platform,
        "platform_batch_artifacts": [{"path": a["path"], "cases": a["cases"], "copies": a["copies"]} for a in PLATFORM_BATCH],
        "mission_multi_case_artifacts": [{"path": a["path"], "cases": a["cases"]} for a in MISSION_BATCH],
        "kb_formula_chain_rows": n_kb,
        "kb_rows_by_domain": {k: v for k, v in samples["rows_by_type"].items() if k.startswith("kb_")},
        "geometry_validation_rows_excluded": n_geo,
    },
    "l1_identity_pass_rates": quality,
    "cluster_execution_diagnostics": {
        "jobs_total": cluster["total"],
        "jobs_terminal": cluster["term"],
        "shard_mode_tasks": {"succeeded": n_shard_ok, "killed": n_shard_killed,
                             "running": n_shard_run, "queued": n_shard_queued},
        "batch_like_jobs_by_state": cluster["batchlike"],
        "queued_jobs_total": sum(r["jobs"] for r in bsm if r["state"] == "queued"),
        "killed_jobs_total": sum(r["jobs"] for r in bsm if r["state"] == "killed"),
    },
    "top_gaps": [
        {"gap": "分片并行批量线 0 成功", "evidence": f"cluster_jobs mode=shard: succeeded 任务 {n_shard_ok}，killed {n_shard_killed}，running {n_shard_run}，queued {n_shard_queued}",
         "impact": "无人值守规模化目前只能串行/单机跑，分布式批量能力未验证"},
        {"gap": "求解器级批量仅 %d 工况（占 headline %.1f%%）" % (A, 100.0 * A / B),
         "evidence": f"批量线工件 {n_platform}+{n_mission}=A；其余 {n_kb} 全为公式链回填",
         "impact": "KPI 数字达标但能力密度偏低：CFD/FEM/DEM/BEM 级批量仍缺"},
        {"gap": "批量结果有效性未设门", "evidence": "batch_sweep_demo.json 51 例中 10 例 rt=0.0 仍被计 success",
         "impact": "计数可被退化值刷高，需有效性门（rt>0 + 几何 Cb/L/B 校验）"},
        {"gap": "冰区工况总阻力恒等式不成立", "evidence": "kb_ice_navigation R_ice=Σ分量 通过率 %s（R_ice 55.4 N vs 分量和 96.3 N）" % quality["kb_ice_navigation.R_ice=sum(components)"],
         "impact": "848 个冰区工况证据降级，KPI 计数不应含未解释公式变体的行"},
        {"gap": "KPI 无机器时序采样", "evidence": "PG/tensor_metric_samples 0 行；guide_tracker 仍用任务名代理口径 sweep_succ=65",
         "impact": "现值靠一次性脚本，无法做趋势/消账"},
        {"gap": "积压与超时", "evidence": f"queued {sum(r['jobs'] for r in bsm if r['state']=='queued')} jobs / killed {sum(r['jobs'] for r in bsm if r['state']=='killed')} jobs",
         "impact": "批量吞吐被排队与超时吞掉，是 distance-to-1000 的真实瓶颈"},
    ],
    "proposed_anchors": [
        {"anchor": "KPI-SWEEP-LEDGER-DAILY", "acceptance": "普查脚本日更，写 tensor_metric_samples(metric_key='kpi.sweep.cases.by_scope')，连续 3 天有样本且与重算差 <1%"},
        {"anchor": "KPI-SWEEP-SHARD-FIRST", "acceptance": "出现 1 个 state=succeeded 且 mode=shard 的作业（≥8 shard），每 shard 落逐工况工件，可点出 ≥80 工况"},
        {"anchor": "KPI-SWEEP-VALIDITY-GATE", "acceptance": "批量线工件中退化值（rt<=0/缺参）行数=0，门禁写进 batch API 响应 validity 字段"},
        {"anchor": "KPI-SWEEP-ICE-IDENTITY", "acceptance": "kb_ice_navigation 848 行中 ≥95% 通过 R_ice=Σ分量恒等式，或行内落 formula_version 说明差异"},
        {"anchor": "KPI-SWEEP-SOLVER-CASES-1000", "acceptance": "求解器级（LBM/FEM/DEM/BEM/FVM）无人值守批量 ≥1000 工况，逐 case 工件含 solver_version+收敛判据"},
    ],
}

json.dump(result, open(f"{BASE}/kpi_sweep_1000_result.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: result[k] for k in ("current", "unit", "target", "gap", "attainment_pct")},
                 ensure_ascii=False, indent=1))
print("A/B/C =", A, B, C)
json.dump({"A": A, "B": B, "C": C, "n_platform": n_platform, "n_mission": n_mission,
           "n_kb": n_kb, "n_geo": n_geo, "quality": quality,
           "shard": {"ok": n_shard_ok, "killed": n_shard_killed, "running": n_shard_run, "queued": n_shard_queued},
           "queued": sum(r["jobs"] for r in bsm if r["state"] == "queued"),
           "killed": sum(r["jobs"] for r in bsm if r["state"] == "killed"),
           "batchlike": cluster["batchlike"], "rows_by_type": samples["rows_by_type"]},
          open(f"{BASE}/stage/plot_data.json", "w"), ensure_ascii=False, indent=1)