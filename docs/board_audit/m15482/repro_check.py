#!/usr/bin/env python3
"""m15482 board audit — independent repro of m15444 (GAP-PP-5-gpu-cluster).

Re-runs TensorLBM examples/differentiable_lbm.py at the exact configuration
recorded in m15444 runs/gap_pp5_cpu_baseline/results.json, twice:

  A) "as-delivered"  : TensorLBM worktree as m15444 left it (local, UNCOMMITTED
                       _as_float guard present in src/tensorlbm/input_contract.py)
  B) "clean HEAD"    : same tree but input_contract.py restored from
                       `git show HEAD:` → the state a fresh checkout of the
                       TensorLBM repo has (guard absent)

Outputs runs/m15482_audit/repro_check.json — no number is hand-typed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

LBM = Path("/root/tensor-ecosystem/TensorLBM")
OUT = Path("/root/tp-agent/work/m15482/runs/m15482_audit")
HEAD_SRC = Path("/tmp/m15482_lbm_head/src")

CFG = ["--mode", "tau", "--grid", "64", "--steps", "50", "--iters", "60",
       "--tau-star", "0.9", "--tau-init", "0.55", "--lr", "2000",
       "--device", "cpu", "--dtype", "float64"]


def run(pypath: Path, tag: str) -> dict:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(pypath), "OMP_NUM_THREADS": "8",
                "MKL_NUM_THREADS": "8", "OPENBLAS_NUM_THREADS": "8"})
    cmd = [sys.executable, str(LBM / "examples" / "differentiable_lbm.py"), *CFG]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
    wall = time.perf_counter() - t0
    (OUT / f"stdout_{tag}.txt").write_text(p.stdout)
    (OUT / f"stderr_{tag}.txt").write_text(p.stderr)
    m = re.search(r"converged: tau=([\d.]+) \(target ([\d.]+), error ([\d.]+)%\)", p.stdout)
    tau, ref, err = (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (None, None, None)
    return {"tag": tag, "py_path": str(pypath), "cmd": " ".join(cmd),
            "rc": p.returncode, "wall_s": round(wall, 3),
            "tau_got": tau, "tau_ref": ref, "error_pct": err,
            "judged_15pct": ("PASS" if err is not None and err < 15 else "FAIL")}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # stage A: delivered worktree state (dirty: guard applied)
    a = run(LBM / "src", "delivered_worktree")

    # stage B: clean HEAD of TensorLBM
    HEAD_SRC.parent.mkdir(parents=True, exist_ok=True)
    if HEAD_SRC.exists():
        shutil.rmtree(HEAD_SRC)
    shutil.copytree(LBM / "src", HEAD_SRC)
    head_txt = subprocess.run(["git", "-C", str(LBM), "show", "HEAD:src/tensorlbm/input_contract.py"],
                              capture_output=True, text=True).stdout
    (HEAD_SRC / "tensorlbm" / "input_contract.py").write_text(head_txt)
    b = run(HEAD_SRC, "clean_head")

    dirty = subprocess.run(["git", "-C", str(LBM), "status", "--porcelain", "src/tensorlbm/input_contract.py"],
                           capture_output=True, text=True).stdout.strip()

    doc = {
        "audit_mission": "m15482",
        "audited_mission": "m15444",
        "anchor": "GAP-PP-5-gpu-cluster",
        "recorded_reference": "m15444 runs/gap_pp5_cpu_baseline/results.json (run_artifacts)",
        "tensorlbm_head": subprocess.run(["git", "-C", str(LBM), "rev-parse", "HEAD"],
                                         capture_output=True, text=True).stdout.strip(),
        "tensorlbm_input_contract_dirty": dirty or "(clean)",
        "runs": [a, b],
        "conclusion": {
            "A_delivered_state_reproduces": (a["error_pct"] is not None and a["error_pct"] < 1.0),
            "B_clean_head_reproduces": (b["error_pct"] is not None and b["error_pct"] < 15),
            "fix_versioned_in_tensorlbm": bool(dirty) is False,
            "wallclock_reproducible_vs_recorded": {
                "recorded_wall_s": 33.5047,
                "audit_wall_s": a["wall_s"],
                "ratio": round(a["wall_s"] / 33.5047, 2),
            },
        },
    }
    (OUT / "repro_check.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(json.dumps(doc, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())