#!/usr/bin/env python3
"""m15424: board-audit verdict for m15359 (GAP-PP-5-gpu-cluster)."""
import json
import subprocess
from pathlib import Path

ROOT = Path("/root/tp-agent/work/m15424")

evidence = {
    "os": "board-audit m15424 of m15359 (GAP-PP-5-gpu-cluster), repo TensorPlatform, PR #1263 merged",
    "impl_files": [
        "artifacts/gap_pp5_cpu_baseline/bench_diff_lbm_cpu.py:1-200+ (CPU baseline harness: end-to-end CLI subprocess, cost matrix 3x3, gradient check, shear-wave decay anchor, A/B control)",
        "artifacts/gap_pp5_cpu_baseline/analyze.py:1-120+ (results→tables+figures pipeline, zero hand-typed numbers)",
        "docs/GAP-PP-5_CPU_BASELINE.md:1-120+ (six-element report, all numbers from results.json)",
    ],
    "entry": "cd <TensorLBM_checkout> && PYTHONPATH=src python3 bench_diff_lbm_cpu.py --grids 64,128,256 --steps 10,50,200 --threads 8 --reps 2 --out runs/gap_pp5_cpu_baseline",
    "run_artifacts": [
        "artifacts/gap_pp5_cpu_baseline/run_x86_controlplane/results.json (full JSON: device info, e2e tau recovery, pytest A/B, 9-config matrix, shear-wave anchor)",
        "artifacts/gap_pp5_cpu_baseline/run_x86_controlplane/wallclock.csv (9 rows, all numeric fields present)",
        "artifacts/gap_pp5_cpu_baseline/run_x86_controlplane/summary.md (markdown tables, all numbers from JSON)",
        "artifacts/gap_pp5_cpu_baseline/run_x86_controlplane/figures/fig_wallclock_mlups.png (52KB)",
        "artifacts/gap_pp5_cpu_baseline/run_x86_controlplane/figures/fig_peak_rss.png (58KB)",
        "artifacts/gap_pp5_cpu_baseline/run_x86_controlplane/figures/fig_decay_anchor.png (52KB)",
    ],
    "tests": "No pytest in TensorPlatform (harness is a standalone script); L1 verification via: (a) gradient check autograd vs central FD (B: rel_err=1.013e-09), (b) shear-wave decay analytic anchor (3 tau points, max err 4.88%), (c) upstream pytest tests/test_autograd.py -k collide_step_gradients A/B control (A: 2 failed → B: 5 passed, red→green)",
    "integration_callers": "none: grep for 'gap_pp5' / 'bench_diff_lbm' in api/backend/tests yields zero hits; harness is standalone diagnostic, not integrated into platform pipeline → not L3",
    "benchmark": {
        "registered_case": "none: SELECT ... FROM tensor_benchmark_cases WHERE anchor_slug='GAP-PP-5-gpu-cluster' returns 0 rows (table has 15 anchor_slugs, none match GAP-PP-5)",
        "ref_source": "Analytic: BGK shear-wave decay nu=(tau-0.5)/3 (Krueger et al. 2017, ch.4/10); tau recovery ground truth tau*=0.9 exact by construction",
        "tau_recovery_B": {"ref": 0.9, "got": 0.89971, "error_pct": 0.0322, "within_15pct": True},
        "tau_recovery_A": {"ref": 0.9, "got": 0.55, "error_pct": 38.8889, "within_15pct": False, "note": "expected FAIL — pins live regression in input_contract.check_relaxation_time"},
        "gradient_check_B": {"autograd": -2.136e-05, "fd": -2.136e-05, "rel_err": 1.013e-09, "within_tolerance": True},
        "shear_wave_tau06": {"nu_analytic": 0.03333, "nu_eff": 0.03496, "rel_err_pct": 4.88, "within_15pct": True},
        "shear_wave_tau09": {"nu_analytic": 0.13333, "nu_eff": 0.13397, "rel_err_pct": 0.48, "within_15pct": True},
        "shear_wave_tau14": {"nu_analytic": 0.3, "nu_eff": 0.29575, "rel_err_pct": 1.42, "within_15pct": True},
        "throughput_range_fwd": "1.18–2.31 MLUPS (9 configs, variant B)",
        "throughput_range_fb": "0.027–0.997 MLUPS (9 configs, variant B)",
    },
    "repro_cmd": "cd /tmp/gp5_stage && PYTHONPATH=src python3 bench_diff_lbm_cpu.py --grids 64,128,256 --steps 10,50,200 --threads 8 --reps 2 --example-cases 64:50 --example-iters 60 --gradcheck-grid 64 --gradcheck-steps 50 --out runs/gap_pp5_cpu_baseline && python3 analyze.py --run runs/gap_pp5_cpu_baseline",
    "external_anchor": "BGK shear-wave decay analytic solution: u=U0*sin(ky)*exp(-nu*k^2*t), nu=(tau-0.5)/3; Krueger et al., The Lattice Boltzmann Method: Principles and Practice, Springer 2017, ch.4 & 10. Same carrier as TensorLBM/benchmarks/verified/shear_wave_decay.",
    "report": "tensor_reports id=14445 (m15359 original report, six elements); docs/GAP-PP-5_CPU_BASELINE.md (full markdown report)",
    "level_rationale": "L2: Real code (bench_diff_lbm_cpu.py + analyze.py) with input→compute→output pipeline, reproducible, PR #1263 merged. L1 satisfied (gradient check + analytic anchor). NOT L3: no integration callers in platform/solver. NOT L4: no registered benchmark case in tensor_benchmark_cases for GAP-PP-5-gpu-cluster; analytic anchor is physics validation not a formal benchmark case. Cluster leg (cj1081) never completed; GPU node not added to pool.",
    "findings": {
        "F1_no_benchmark_case": "tensor_benchmark_cases has no GAP-PP-5-gpu-cluster row → L4 cannot be formally judged; recommend registering a benchmark case for future audits.",
        "F2_cluster_not_run": "Cluster job cj1081 was still queued (289 jobs ahead) when round closed; no cluster/GPU numbers claimed — honest omission.",
        "F3_no_gpu_node": "No GPU node was added to the compute pool; only CPU baseline achieved. The 'gpu-cluster' half of the anchor is NOT met.",
        "F4_live_regression_not_fixed": "The root cause (input_contract._as_float coercing tensor tau to float, destroying autograd graph) was identified but NOT fixed in TensorLBM — only a /tmp overlay was used. The defect remains live on main (pytest still red without overlay).",
        "F5_tau_curve_missing": "tau convergence curve figure not produced (convergence_tau.csv loss column parsed empty); honestly reported, not fabricated.",
        "F6_control_plane_not_pool": "Numbers come from x86_64/loongarch64 control plane host, NOT a pool node — documented but means this is a baseline, not a cluster measurement.",
    },
}

sql = (
    "INSERT INTO tensor_maturity_verdicts (repo, scope, mission_id, level, evidence, verdict_by) "
    "VALUES ('TensorPlatform', 'm15359 GAP-PP-5-gpu-cluster', 15424, 2, $json$%s$json$::jsonb, 'board-audit') "
    "ON CONFLICT (repo, scope, verdict_by) DO UPDATE SET level=EXCLUDED.level, evidence=EXCLUDED.evidence, mission_id=EXCLUDED.mission_id "
    "RETURNING id, gate_state;" % json.dumps(evidence, ensure_ascii=False)
)

sqlfile = ROOT / "runs" / "m15424_audit" / "insert_verdict.sql"
sqlfile.parent.mkdir(parents=True, exist_ok=True)
sqlfile.write_text(sql + "\n")

p = subprocess.run(
    ["psql", "-U", "postgres", "-d", "tensorplatform", "-v", "ON_ERROR_STOP=1", "-f", str(sqlfile)],
    capture_output=True, text=True
)
print("STDOUT:", p.stdout)
print("STDERR:", p.stderr)
print("RC:", p.returncode)

(ROOT / "runs" / "m15424_audit" / "verdict_payload.json").write_text(
    json.dumps({
        "repo": "TensorPlatform",
        "scope": "m15359 GAP-PP-5-gpu-cluster",
        "mission_id": 15424,
        "level": 2,
        "evidence": evidence,
        "verdict_by": "board-audit"
    }, ensure_ascii=False, indent=2) + "\n"
)
print("Verdict written to:", sqlfile)
print("Payload written to:", ROOT / "runs" / "m15424_audit" / "verdict_payload.json")
