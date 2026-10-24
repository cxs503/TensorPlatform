#!/usr/bin/env python3
"""回归 v2：PASS_ALL——不桩 decide()，走真实 decide 路径验证动词 approved。"""
from __future__ import annotations
import importlib.util, sys, tempfile
from pathlib import Path

HERE = Path("/srv/tensor/shared/tensor_eco/TensorPlatform/tools/agent")
spec = importlib.util.spec_from_file_location("aa", HERE / "auto_approver.py")
aa = importlib.util.module_from_spec(spec); spec.loader.exec_module(aa)

class FakeAPI:
    def __init__(self, items):
        self.items = items; self.calls = []
    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.startswith("/tensor/approvals?"):
            return 200, {"items": self.items}
        if path.endswith("/decide"):
            assert payload["decision"] == "approved", payload   # 动词真断言
            return 200, {"id": 1, "status": "approved"}
        raise AssertionError(path)

ok = 0
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    sw = tmp / "PASS_ALL"; sw.write_text("")
    aa.PASS_ALL_SWITCH = str(sw)
    api = FakeAPI([{"id": 7}, {"id": 8}])
    saved = aa.API; aa.API = lambda: api
    try:
        aa.main()
    finally:
        aa.API = saved
    posts = [c for c in api.calls if c[1].endswith("/decide")]
    assert len(posts) == 2, posts
    print("T1 PASS：真实 decide 路径动词=approved ×2"); ok += 1

    # T2 复跑零重批（无 pending）
    api2 = FakeAPI([])
    aa.API = lambda: api2
    try:
        aa.main()
    finally:
        aa.API = saved
    assert not [c for c in api2.calls if c[1].endswith("/decide")]
    print("T2 PASS：空队列不动作"); ok += 1
print(f"ALL {ok} PASS")
sys.exit(0 if ok == 2 else 1)
