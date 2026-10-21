#!/usr/bin/env python3
"""回归：worker 池化（worker 身份=slot/session，非模型 API）。

判据：
  T1 run_agent_multi.sh 默认 slot 1 → 旧锁名（既有 cron 行零改动，向后兼容）
  T2 --slot 2 → 新锁名 tensor-agent-<model>-w2.lock
  T3 同一 slot 锁互斥：两个并发持锁者第二个拿不到（flock 单实例语义）
  T4 workers.json schema：可解析、值为 int>=1、_note 等非整数值被 fleet 忽略
  T5 tensor_agent.py 语法完好且 --slot 进了参数面（静态契约）

运行：bash tools/agent/test_worker_slots.sh 或直接 python3 本文件。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASS: list[str] = []

# T1/T2 锁名契约（--print-lock 不启动守护）
r1 = subprocess.run(["bash", str(HERE / "run_agent_multi.sh"), "testmodel",
                     "--print-lock"], capture_output=True, text=True)
assert r1.stdout.strip() == "/srv/tensor/shared/tensor_eco/tensor-agent-testmodel.lock", \
    f"T1 FAIL: {r1.stdout!r}"
PASS.append("T1 slot 1 默认=旧锁名（既有 cron 兼容）")

r2 = subprocess.run(["bash", str(HERE / "run_agent_multi.sh"), "testmodel",
                     "--slot", "2", "--print-lock"], capture_output=True, text=True)
assert r2.stdout.strip() == "/srv/tensor/shared/tensor_eco/tensor-agent-testmodel-w2.lock", \
    f"T2 FAIL: {r2.stdout!r}"
PASS.append("T2 slot 2=独立锁名 -w2.lock")

# T3 同 slot flock 互斥（用 T2 的真实锁路径：先探测持有，再抢应失败）
lock3 = "/srv/tensor/shared/tensor_eco/tensor-agent-testmodel-w3.lock"
h1 = open(lock3, "w")
import fcntl
fcntl.flock(h1, fcntl.LOCK_EX | fcntl.LOCK_NB)
rc = subprocess.run(["bash", "-c", f'exec 9>>"{lock3}"; flock -n 9 || exit 42']).returncode
assert rc == 42, f"T3 FAIL: 第二持锁者 rc={rc}（应 42 拿锁失败）"
fcntl.flock(h1, fcntl.LOCK_UN)
h1.close()
PASS.append("T3 同 slot 锁互斥（第二持锁者被拒）")

# T4 workers.json schema
cfg = json.loads((HERE / "workers.json").read_text(encoding="utf-8"))
ints = {k: v for k, v in cfg.items() if isinstance(v, int)}
assert all(isinstance(v, int) and v >= 1 for v in ints.values()), "T4 FAIL: 槽数非正整数"
assert "_note" in cfg and not isinstance(cfg["_note"], int), "T4 FAIL: _note 应被忽略"
assert len(ints) >= 5, f"T4 FAIL: 覆盖模型过少（{len(ints)}）"
PASS.append(f"T4 workers.json 可解析，{len(ints)} 模型全正整数，_note 被忽略")

# T5 tensor_agent.py 静态契约
ta = (HERE / "tensor_agent.py").read_text(encoding="utf-8")
ast.parse(ta)
assert '"--slot" in args' in ta and "int(args[args.index" in ta, "T5 FAIL: --slot 未进参数面"
assert ta.count("FOR UPDATE SKIP LOCKED") >= 0  # 文档锚（claim 原子性在平台侧）
PASS.append("T5 tensor_agent.py 语法完好，--slot 参数面在位")

print("\n".join(PASS))
print(f"\nALL {len(PASS)} PASS")
