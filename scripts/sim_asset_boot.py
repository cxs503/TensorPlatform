#!/usr/bin/env python3
"""仿真成果捕获守护的启动器（先 retro 全扫一次，再常驻 15min 轮转）。

为什么单独一个启动器：retro 全扫要遍历 succeeded 台账里上万单、并对上千棵
残存 worktree 做 git ls-files 清点（分钟级），常驻循环本身要 15min 一跳；两者
顺序执行后交给同一个后台进程，避免两个进程抢 capture 锁。

用法（父进程会退出，子进程脱离会话）：
  python3 scripts/sim_asset_boot.py --detach
  python3 scripts/sim_asset_boot.py            # 前台跑（调试）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(os.environ.get("TP_SIM_ASSET_ROOT", "/root/sim-assets"))
HARVESTER = pathlib.Path(__file__).resolve().parent / "sim_asset_harvest.py"
PY = sys.executable or "python3"


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] boot: {msg}"
    print(line, flush=True)
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "daemon.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    _log(f"retro 全扫开始（pid={os.getpid()}）")
    with (ROOT / "retro_scan.json").open("w", encoding="utf-8") as out, \
            (ROOT / "retro_scan.log").open("a", encoding="utf-8") as err:
        rc = subprocess.call([PY, str(HARVESTER), "--retro", "--json"],
                             env={**os.environ, "TP_SIM_ASSET_QUIET": "1"},
                             stdout=out, stderr=err)
    _log(f"retro 全扫结束 rc={rc}")
    _log("进入常驻轮转（interval=900s）")
    return subprocess.call([PY, str(HARVESTER), "--daemon", "--interval", "900",
                            "--window", "400"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detach", action="store_true", help="脱离会话后台运行")
    args = ap.parse_args(argv)
    if not args.detach:
        return run()
    pid = os.fork()
    if pid == 0:
        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            os._exit(0)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        out = os.open(ROOT / "daemon.out", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(out, 1)
        os.dup2(out, 2)
        rc = 0
        try:
            rc = run()
        finally:
            os._exit(rc)
    os.waitpid(pid, 0)
    time.sleep(0.5)
    print(json.dumps({"detached": True, "daemon_log": str(ROOT / "daemon.log")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())