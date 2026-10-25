#!/usr/bin/env python3
"""回归：PASS_ALL 模式——开关在→全 pending 默认批准；开关不在→原裁决流。"""
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
            return 200, {"ok": True}
        raise AssertionError(path)

ok = 0
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    switch = tmp / "PASS_ALL"
    park = tmp / "park.json"
    park.write_text('{"99":"x","140":"y"}')
    switch.write_text("")
    aa.PASS_ALL_SWITCH = str(switch)
    decided = []
    aa.decide = lambda a, i, d, n: decided.append((i, d))
    api = FakeAPI([{"id": 140}, {"id": 141}, {"id": 142}])
    saved = aa.API
    aa.API = lambda: api
    try:
        aa.main()
    finally:
        aa.API = saved
    assert decided == [(140, "approve"), (141, "approve"), (142, "approve")], decided
    assert not any(c[1].endswith("/decide") for c in api.calls), "应经 decide() 而非裸 API"
    print("T1 PASS：开关在→全 pending 默认批准（含停车场语义）"); ok += 1

    # T2: 空 pending → 零动作不崩
    decided.clear()
    api2 = FakeAPI([])
    aa.API = lambda: api2
    try:
        aa.main()
    finally:
        aa.API = saved
    assert decided == [], decided
    print("T2 PASS：空 pending 零动作"); ok += 1

    # T3: 开关不在 → 不进直通（默认批准不被触发）
    aa.PASS_ALL_SWITCH = str(tmp / "nonexistent")
    aa.decide = lambda a, i, d, n: (_ for _ in ()).throw(
        AssertionError("无开关不应默认批"))
    api3 = FakeAPI([])
    aa.API = lambda: api3
    try:
        aa.main()
    finally:
        aa.API = saved
    print("T3 PASS：无开关→不默认批准"); ok += 1

print(f"ALL {ok} PASS")
sys.exit(0 if ok == 3 else 1)
