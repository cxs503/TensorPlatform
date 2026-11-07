"""实验：psql 无界等待 → daemon 挂死；补丁加了单次调用硬上限后的量化对比。

单变量设计：baseline = 补丁后镜像仅**去掉 timeout=PSQL_TIMEOUT_S**（其余字节相同），
两者都在同一个 shim 沙箱里跑（psql shim 每次调用 sleep S 秒、退出码 1 = DB 不可达）。
观测：整进程墙钟时间（psql 调用是串行的 3 次）。
预期：baseline ≈ 3S；patched ≈ min(TP_PG_TIMEOUT_S, S)。

用法：python3 -m harness_gateway.experiments.timeout_bound --artifact-dir artifacts/harness_gateway/timeout_bound
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from harness_gateway.gates import canary_gate
from harness_gateway.spec import HarnessUnit

UNIT = HarnessUnit(
    name="saturation_regulator",
    role="daemon",
    relpath="saturation_regulator_loop_once.py",
    canary="exec_dry_run",
    expect_symbols=("q",),
    expect_exit=(0, 1),
    timeout_s=60,
)


def _run(path: Path, sandbox: Path, sleep_s: float, extra_env: dict) -> dict:
    sandbox.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    res = canary_gate.run_canary_gate(
        path.parent, (UNIT,), sandbox_root=sandbox, sleep_s=sleep_s, extra_env=extra_env
    )
    wall = time.monotonic() - t0
    unit = res.evidence["units"][0]
    return {
        "wall_s": round(wall, 3),
        "status": res.status.value,
        "returncode": unit["returncode"],
        "stdout_tail": unit["stdout_tail"][-120:],
        "shim_calls": unit.get("shim_calls"),
        "note": unit.get("mode_note", "")[:80],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", default="harness_gateway/mirror")
    ap.add_argument("--artifact-dir", default="artifacts/harness_gateway/timeout_bound")
    ap.add_argument("--shim-sleep", type=float, default=3.0)
    ap.add_argument("--timeout-cap", type=str, default="1")
    args = ap.parse_args()

    art = Path(args.artifact_dir)
    art.mkdir(parents=True, exist_ok=True)
    work = art / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    patched_dir = work / "patched"
    baseline_dir = work / "baseline_no_timeout"
    for d in (patched_dir, baseline_dir):
        d.mkdir(parents=True)
    src = Path(args.mirror) / UNIT.relpath
    patched_text = src.read_text(encoding="utf-8")
    assert "timeout=PSQL_TIMEOUT_S" in patched_text, "patched mirror must carry the psql timeout"
    (patched_dir / UNIT.relpath).write_text(patched_text, encoding="utf-8")
    baseline_text = patched_text.replace("env={**os.environ, **PGENV},timeout=PSQL_TIMEOUT_S)",
                                         "env={**os.environ, **PGENV})")
    assert baseline_text != patched_text
    (baseline_dir / UNIT.relpath).write_text(baseline_text, encoding="utf-8")

    env = {"TP_PG_TIMEOUT_S": args.timeout_cap}
    baseline = _run(baseline_dir / UNIT.relpath, art / "sb_baseline", args.shim_sleep, env)
    patched = _run(patched_dir / UNIT.relpath, art / "sb_patched", args.shim_sleep, env)

    speedup = baseline["wall_s"] / patched["wall_s"] if patched["wall_s"] else float("nan")
    measurement = {
        "schema": "harness-gateway/experiment/timeout-bound/1",
        "shim_sleep_s": args.shim_sleep,
        "timeout_cap_s": args.timeout_cap,
        "psql_calls_per_run": 3,
        "baseline_no_timeout": baseline,
        "patched_with_timeout": patched,
        "wall_speedup": round(speedup, 2),
        "per_call_bound_s": float(args.timeout_cap),
        "worst_case_wall_s_patched": round(3 * float(args.timeout_cap), 2),
        "bound_respected": abs(patched["wall_s"] - 3 * float(args.timeout_cap)) < float(args.timeout_cap),
        "timeout_message_seen": "psql 超时" in patched["stdout_tail"],
        "baseline_unbounded": baseline["wall_s"] > 2 * args.shim_sleep,
        "criterion": "patched wall time ≈ 3 × per-call cap (bound enforced, timeout path exercised); "
                     "baseline wall time ≈ 3 × shim sleep (no bound: waits accumulate serially)",
    }
    (art / "measurement.json").write_text(json.dumps(measurement, indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    print(json.dumps(measurement, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())