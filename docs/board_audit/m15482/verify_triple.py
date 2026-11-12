#!/usr/bin/env python3
"""m15482 board audit — 三件套核验（run_artifacts / benchmark ref-vs-got / repro_cmd）。

全部结论由程序采集，零手抄：读 m15444 工作树 + 查 PG tensor_benchmark_cases
+ 检查 repro_cmd 各段可执行性。输出 runs/m15482_audit/triple_check.json。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

M = Path("/root/tp-agent/work/m15444")
OUT = Path("/root/tp-agent/work/m15482/runs/m15482_audit")
ANCHOR = "GAP-PP-5-gpu-cluster"
PSQL = ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-tA", "-F", "\x01", "-c"]


def sh(cmd):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def q(sql):
    p = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    return [ln.split("\x01") for ln in p.stdout.strip().split("\n") if ln.strip()]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"audit_mission": "m15482", "audited_mission": "m15444", "anchor": ANCHOR}

    # ── 1 三件套之一：run_artifacts 真实存在且非空 ──
    arts = ["runs/gap_pp5_cpu_baseline/results.json", "runs/gap_pp5_cpu_baseline/wallclock.csv",
            "runs/gap_pp5_cpu_baseline/summary.md", "runs/gap_pp5_cpu_baseline/convergence_rep0.png",
            "runs/gap_pp5_cpu_baseline/rep_0/convergence_tau.csv",
            "artifacts/gap_pp5_cpu_baseline/bench_diff_lbm_cpu.py",
            "artifacts/gap_pp5_cpu_baseline/analyze.py", "artifacts/gap_pp5_cpu_baseline/entry_gap_pp5.py",
            "tests/test_gap_pp5_cpu_baseline.py", "docs/GAP-PP-5_CPU_BASELINE.md"]
    doc["run_artifacts"] = [{"path": str(M / a), "exists": (M / a).exists(),
                             "bytes": (M / a).stat().st_size if (M / a).exists() else 0} for a in arts]

    # ── 2 三件套之二：基准裁判（tensor_benchmark_cases）──
    rows = q(f"SELECT case_name, metric, tolerance FROM tensor_benchmark_cases "
             f"WHERE anchor_slug='{ANCHOR}'")
    doc["benchmark_referee"] = {
        "sql": f"SELECT case_name,metric,tolerance FROM tensor_benchmark_cases WHERE anchor_slug='{ANCHOR}'",
        "rows": rows, "registered": bool(rows),
        "total_cases_in_table": int(q("SELECT count(*) FROM tensor_benchmark_cases")[0][0]),
        "table_anchor_slugs": [r[0] for r in q("SELECT DISTINCT anchor_slug FROM tensor_benchmark_cases")],
    }

    # ── 3 ref vs got 数值核（从 m15444 自己的 results.json 采集，并复算误差）──
    res = json.loads((M / "runs/gap_pp5_cpu_baseline/results.json").read_text())
    doc["recorded_results"] = {
        "configs": res.get("configs"), "reps": res.get("reps"), "device": res.get("device"),
        "rows": [{k: r[k] for k in ("grid", "steps", "iters", "tau_star", "tau_got", "error_pct", "wall_s")}
                 for r in res["results"]],
    }
    recomputed = []
    for r in res["results"]:
        got, ref = r["tau_got"], r["tau_star"]
        e = abs(got - ref) / ref * 100 if got is not None else None
        recomputed.append({"grid": r["grid"], "steps": r["steps"], "ref": ref, "got": got,
                           "error_pct_recorded": r["error_pct"],
                           "error_pct_recomputed": round(e, 4) if e is not None else None,
                           "consistent": e is not None and abs(e - (r["error_pct"] or 0)) < 5e-3,
                           "within_15pct": e is not None and e < 15})
    doc["benchmark_ref_vs_got"] = {
        "ref_source": "tau* = 0.9 exact by construction (inverse problem, forward model with known tau)",
        "external_anchor": "BGK shear-wave decay nu=(tau-0.5)/3 (Krueger et al. 2017 ch.4/10)",
        "cases": recomputed,
    }

    # ── 4 三件套之三：repro_cmd 可执行性 ──
    repro = re.search(r"```bash\n(.*?)```", (M / "docs/GAP-PP-5_CPU_BASELINE.md").read_text(), re.S).group(1)
    entry = re.search(r"python3 (\S+).*?--grids (\S+) --steps (\S+).*?--reps (\d+).*?--out (\S+)", repro.replace("\\\n", " "))
    doc["repro_cmd"] = {
        "documented": " ".join(repro.split()),
        "entry_exists": (M / entry.group(1)).exists() if entry else False,
        "matrix_in_cmd": {"grids": entry.group(2), "steps": entry.group(3), "reps": int(entry.group(4))} if entry else None,
        "matrix_in_recorded_results": {"configs": res.get("configs"), "reps": res.get("reps")},
        "matrix_match": bool(entry) and (
            len(entry.group(2).split(",")) * len(entry.group(3).split(",")) == res.get("configs")
            and int(entry.group(4)) == res.get("reps")),
        "requires_uncommitted_external_patch": "M src/tensorlbm/input_contract.py" in sh(
            "git -C /root/tensor-ecosystem/TensorLBM status --porcelain").replace("\n", " "),
    }

    # ── 5 测试实跑（不是引用）──
    p = subprocess.run(["python3", "-m", "pytest", "tests/test_gap_pp5_cpu_baseline.py", "-q"],
                       cwd=M, capture_output=True, text=True, timeout=1800)
    (OUT / "pytest_m15444.txt").write_text(p.stdout + p.stderr)
    tail = [l for l in (p.stdout + p.stderr).splitlines() if "passed" in l or "failed" in l]
    doc["tests"] = {"cmd": "cd /root/tp-agent/work/m15444 && python3 -m pytest tests/test_gap_pp5_cpu_baseline.py -q",
                    "exit": p.returncode, "tail": tail[-1] if tail else ""}

    # ── 6 集成调用方 grep（L3 判据）──
    doc["integration_callers"] = {
        "grep": sh(f"grep -rn --include='*.py' -l -E 'gap_pp5|bench_diff_lbm|entry_gap_pp5' {M}/backend {M}/api 2>/dev/null"),
        "note": "命中为空即无平台侧调用方",
    }

    # ── 7 报告与六段（tensor_reports）──
    doc["report"] = {
        "missions_report_id": q("SELECT coalesce(report_id::text,'NULL') FROM tensor_dev_missions WHERE id=15444"),
        "reports_with_mission_15444": q("SELECT id, title FROM tensor_reports WHERE mission_id=15444"),
        "doc_sections": [l.strip() for l in (M / "docs/GAP-PP-5_CPU_BASELINE.md").read_text().splitlines()
                         if re.match(r"^## ", l.strip())],
    }

    # ── 8 §4.3 shear-wave 数字溯源 ──
    doc["handtyped_scan"] = {
        "numbers": ["0.03496", "0.13397", "0.29575"],
        "hits_in_run_artifacts_or_tests": sh(
            f"grep -rn -e 0.03496 -e 0.13397 -e 0.29575 {M}/runs {M}/artifacts/gap_pp5_cpu_baseline "
            f"{M}/tests /root/tensor-ecosystem/TensorLBM/tests 2>/dev/null | head -5"),
        "hits_in_doc": sh(f"grep -c -e 0.03496 -e 0.13397 -e 0.29575 {M}/docs/GAP-PP-5_CPU_BASELINE.md"),
    }

    (OUT / "triple_check.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(json.dumps({k: doc[k] for k in ("benchmark_referee", "benchmark_ref_vs_got", "repro_cmd",
                                          "tests", "integration_callers", "report", "handtyped_scan")},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())