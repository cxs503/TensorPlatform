"""计算放置实验用的可校验工作负载（不是玩具：CPU 秒可控 + 结果可交叉验证）。

用法：
  python3 -m tools.compute_meter.workload --burn-seconds 30 [--shard 0/2]

输出一行 JSON：{burn_seconds, cpu_seconds, wall_seconds, checksum, shard}
- ``cpu_seconds`` 由 ``time.process_time()`` 实测（不是墙钟冒充）；
- ``checksum`` 是确定性整数运算的累积值，分片/shards 之间可拼出同一结果 → 防「跑了个空壳」。
"""
from __future__ import annotations

import argparse
import json
import time


def burn(seconds: float, shard: str = "0/1") -> dict:
    i, n = (int(x) for x in shard.split("/"))
    if n < 1 or not (0 <= i < n):
        raise ValueError(f"shard 非法: {shard!r}")
    acc, k = 0, 0
    t0, p0 = time.monotonic(), time.process_time()
    while time.process_time() - p0 < float(seconds):
        k += 1
        acc = (acc * 1103515245 + 12345 + k) & 0x7FFFFFFF
        if acc % 100003 == 0:
            acc ^= k
    return {
        "burn_seconds": float(seconds),
        "cpu_seconds": round(time.process_time() - p0, 3),
        "wall_seconds": round(time.monotonic() - t0, 3),
        "iterations": k,
        "checksum": acc,
        "shard": shard,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="可校验 CPU 工作负载（计算调度实验用）")
    ap.add_argument("--burn-seconds", type=float, default=30.0)
    ap.add_argument("--shard", default="0/1", help="i/n，例如 0/4")
    a = ap.parse_args(argv)
    print(json.dumps(burn(a.burn_seconds, a.shard), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())