#!/usr/bin/env python3
"""push 前本地套件门（P2b：摆脱 GitHub CI runner 失联依赖）。

两级：
  1. 全库收集（CPU，~1min）：pytest --co -q —— 拦 #371 型模块级合并伤损
     （import 崩/语法崩在收集层就红）；
  2. 四 Triton 模块聚焦测试（GPU5 空闲时才跑；忙则披露跳过——GPU5 是约定
     唯一可用卡，他人任务在跑时不抢）。

用法：suite_check.py <worktree>
退出码：0 过 / 1 不过 / 2 环境坏（venv 缺失等，按不过处理）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PY = "/srv/tensor/shared/venvs/tensorlbm/bin/python"
GPU_BUSY = Path("/srv/tensor/shared/runs/bm_close/GPU5_BUSY")
FOCUSED = ["tests/test_triton_fused.py",
           "tests/test_triton_fused_distributed.py",
           "tests/test_triton_fused_obstacle.py",
           "tests/test_triton_suboff_step_distributed.py"]


def gpu5_actually_busy() -> bool | None:
    """GPU5 真实占用检测（P0b，与 benchmark_dispatch 同逻辑）：True=真占 /
    False=标记失活 / None=查询失败（保守尊重标记）。"""
    try:
        g = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,gpu_bus_id", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20)
        a = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_bus_id",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20)
        if g.returncode != 0 or a.returncode != 0:
            return None
        bus5 = ""
        for ln in g.stdout.splitlines():
            f = [x.strip() for x in ln.split(",")]
            if len(f) >= 2 and f[0] == "5":
                bus5 = f[1].upper()
        if not bus5:
            return None
        apps = {x.strip().upper() for x in a.stdout.splitlines() if x.strip()}
        return bus5 in apps
    except Exception:            # noqa: BLE001
        return None


def focused_for(wt: Path) -> list[str]:
    """聚焦清单按 worktree 存在性过滤：基上不存在的测试文件≠套件红
    （m116 教训：基先于 #371 时 4 文件只 1 在，pytest no-tests-ran 假红）。"""
    return [f for f in FOCUSED if (wt / f).exists()]


def main() -> int:
    wt = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    if not Path(PY).exists():
        print(json.dumps({"rc": 2, "error": f"venv python 缺失: {PY}"}))
        return 2
    out = {"worktree": str(wt), "stages": [], "rc": 0}

    t0 = time.time()
    r = subprocess.run([PY, "-m", "pytest", "--co", "-q", "tests/"],
                       cwd=wt, capture_output=True, text=True, timeout=900)
    tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
    n_err = (r.stdout or "").count(" error") + (r.stderr or "").count("ERROR")
    stage = {"stage": "collect", "rc": r.returncode,
             "summary": tail[0][:120], "seconds": round(time.time() - t0, 1)}
    out["stages"].append(stage)
    if r.returncode != 0:
        out["rc"] = 1
        out["error"] = f"全库收集红：{tail[0][:200]}"
        print(json.dumps(out, ensure_ascii=False))
        return 1

    busy = GPU_BUSY.exists() and gpu5_actually_busy() is not False
    focused = focused_for(wt)
    if busy or not focused:
        why = ("GPU5_BUSY 存在且非失活" if busy else
               f"聚焦清单 {len(FOCUSED) - len(focused)}/{len(FOCUSED)}"
               " 文件在本基不存在（基先于相应测试合入）")
        out["stages"].append({"stage": "focused_gpu", "rc": 0, "skipped":
                              f"{why}，聚焦 GPU 测试本轮跳过（已披露）"})
    else:
        t1 = time.time()
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="5")
        r2 = subprocess.run([PY, "-m", "pytest", "-x", "-q"] + focused,
                            cwd=wt, capture_output=True, text=True,
                            timeout=1800, env=env)
        tail2 = (r2.stdout or "").strip().splitlines()[-1:] or [""]
        out["stages"].append({"stage": "focused_gpu", "rc": r2.returncode,
                              "summary": tail2[0][:120],
                              "seconds": round(time.time() - t1, 1)})
        if r2.returncode != 0:
            out["rc"] = 1
            out["error"] = f"聚焦 GPU 测试红：{tail2[0][:200]}"

    print(json.dumps(out, ensure_ascii=False))
    return out["rc"]


if __name__ == "__main__":
    sys.exit(main())
