#!/usr/bin/env python3
"""崩溃注入用工：获得写租约后常驻，等外部 SIGKILL（D4 崩溃回收用例）。

用法：``python3 -m tools.write_lease._worker <root> <rel-path> <ttl>``
启动后第一行打印 ``{"lease_id": ..., "key": ...}``（供父进程读取），随后阻塞。
被 SIGKILL 后：内核释放 flock、心跳线程消失 → 记录板仍是 active，回收靠 reaper。
"""
from __future__ import annotations

import json
import sys
import time

from .registry import LeaseRegistry


def main(argv) -> int:
    root, rel, ttl = argv[1], argv[2], float(argv[3])
    reg = LeaseRegistry(root, default_ttl=ttl, wait_timeout=3.0)
    lease = reg.acquire([rel], holder="crash-worker", ttl=ttl, repo="repo-x", heartbeat=True)
    print(json.dumps({"lease_id": lease.lease_id, "key": lease.keys[0]}), flush=True)
    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))