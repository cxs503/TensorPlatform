#!/usr/bin/env python3
"""船舶总体性能平台（:8001）健康守护 / 探针。

用法：
    python3 tools/ops/ship_api_watchdog.py                 # 探测一次，JSON 到 stdout
    python3 tools/ops/ship_api_watchdog.py --timeout 5
    python3 tools/ops/ship_api_watchdog.py --json-pretty
    python3 tools/ops/ship_api_watchdog.py --restart-cmd "cd /srv/tensor/ship-workspace/ship-performance-platform/backend && nohup /root/ship-venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1 >> /var/log/ship8001.log 2>&1 &"

退出码：0 = 健康；1 = 不可达（外加 --restart-cmd 时已尝试重启并复探）。

为什么需要它：2026-11-01 复盘发现 :8001 宕机期间无任何守护进程负责拉起
（tp-agent 的 autoheal_state.json 名单里没有 ship-api），而 TensorPlatform
的代理层又写死了 :8002 —— 于是"服务死"与"端口错"叠加成双重静默失效，
全平台 21 项能力审计只能落到 WITHHELD。本脚本把"服务活着吗"变成一次
机器可读的判决，可直接被 cron / fleet_governor / 看板消费。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

# 允许从仓库根或任意 cwd 调用
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from services.ship_api_endpoint import DEFAULT_ORIGIN, probe  # noqa: E402


def _apply_restart(cmd: str, timeout: float, wait: float) -> dict:
    """执行重启命令并在 wait 秒后复探。"""
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        launch = {"returncode": proc.returncode, "stderr": proc.stderr[-300:]}
    except Exception as exc:  # noqa: BLE001
        launch = {"returncode": -1, "stderr": f"{type(exc).__name__}: {exc}"}
    time.sleep(wait)
    after = probe(timeout=timeout)
    after["restart_launch"] = launch
    after["restart_elapsed_s"] = round(time.time() - started, 2)
    return after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="船舶平台 :8001 健康探针 / 守护")
    parser.add_argument("--timeout", type=float, default=3.0, help="单次探测超时（秒）")
    parser.add_argument("--json-pretty", action="store_true", help="缩进输出")
    parser.add_argument(
        "--restart-cmd",
        default=os.environ.get("SHIP_API_RESTART_CMD", ""),
        help="不可达时执行的拉起命令（也可用环境变量 SHIP_API_RESTART_CMD）",
    )
    parser.add_argument("--restart-timeout", type=float, default=60.0)
    parser.add_argument("--restart-wait", type=float, default=25.0, help="拉起后等待秒数")
    args = parser.parse_args(argv)

    result = probe(timeout=args.timeout)
    result["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    result["default_origin"] = DEFAULT_ORIGIN

    if not result["ok"] and args.restart_cmd:
        restarted = _apply_restart(args.restart_cmd, args.restart_timeout, args.restart_wait)
        restarted.update(
            {"ts": result["ts"], "default_origin": DEFAULT_ORIGIN, "attempted_restart": True}
        )
        result = restarted

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json_pretty else None))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())