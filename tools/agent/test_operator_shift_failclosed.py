#!/usr/bin/env python3
"""operator_shift fail-closed 回归（GPT 2026-10-19 #23 复审修复的验收测试）。

失败注入矩阵——每例断言「main() 退出码」与「dispatch_helper 被调次数」：
  T1  200 正常列表（含在飞班次） → 0（SKIP 不派），派发 0 次（对照组）
  T2  401                       → 2，派发 0 次
  T3  500                       → 2，派发 0 次
  T4  传输错误（URLError/拒连）  → 2，派发 0 次
  T5  响应非 JSON               → 2，派发 0 次
  T6  响应体非对象（列表）       → 2，派发 0 次
  T7  items 缺失                → 2，派发 0 次
  T8  items 非列表              → 2，派发 0 次
  T9  items 元素非对象          → 2，派发 0 次

修复前行为（fail-open）：T2-T9 全部落入 `items=[] if code==200 else []`
语义（或直接空表），派发器按「无在飞班次」继续走门→可能开班。

运行：python3 test_operator_shift_failclosed.py；退出码 0=9/9 全过。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "operator_shift.py"

# (用例名, 注入模式, 期望退出码, 期望派发次数)
CASES = [
    ("T1 200 正常列表/班次在飞→SKIP（对照）", "ok_inflight", 0, 0),
    ("T2 401 未授权", "http401", 2, 0),
    ("T3 500 服务端错误", "http500", 2, 0),
    ("T4 传输错误（拒连/超时）", "conn", 2, 0),
    ("T5 响应非 JSON（代理错误页）", "badjson", 2, 0),
    ("T6 响应体非对象（列表）", "bodylist", 2, 0),
    ("T7 items 缺失", "noitems", 2, 0),
    ("T8 items 非列表（字典）", "itemsdict", 2, 0),
    ("T9 items 元素非对象（裸串）", "elembad", 2, 0),
]


def load_module():
    spec = importlib.util.spec_from_file_location("operator_shift_under_test", TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_api(mode):
    def fake(method, path, body=None, tok=None):
        if path.startswith("/auth/login"):
            return 200, {"access_token": "test-tok"}
        if mode == "ok_inflight":
            return 200, {"items": [
                {"id": 51, "goal": "platform-operator 值班班次 2026-10-19",
                 "status": "running", "created_at": "2026-10-19T07:00:00Z"},
                {"id": 50, "goal": "wedge_icebreaking S3 修正腿",
                 "status": "running", "created_at": "2026-10-19T06:00:00Z"},
            ]}
        if mode == "http401":
            return 401, {"detail": "unauthorized"}
        if mode == "http500":
            return 500, {}
        if mode == "conn":
            raise RuntimeError(
                "GET /tensor/missions?limit=100 传输错误: URLError(refused)")
        if mode == "badjson":
            raise json.JSONDecodeError("Expecting value", "<html>Bad Gateway", 0)
        if mode == "bodylist":
            return 200, [{"id": 1}]
        if mode == "noitems":
            return 200, {"total": 0}
        if mode == "itemsdict":
            return 200, {"items": {"a": 1}}
        if mode == "elembad":
            return 200, {"items": [
                {"id": 1, "goal": "x", "status": "running"}, "oops"]}
        raise AssertionError("unknown mode " + mode)
    return fake


class _FakeCompleted:
    returncode = 0
    stdout = "{}"
    stderr = ""


def run_case(mode):
    mod = load_module()
    mod.OFF_SWITCH = Path("/nonexistent-operator_shift.OFF")  # 测试不依赖现场开关
    mod.api = make_api(mode)
    counter = {"dispatch": 0}

    def fake_run(argv, *a, **k):
        if "dispatch_helper.py" in str(argv):
            counter["dispatch"] += 1
        return _FakeCompleted()

    real_run, real_argv = subprocess.run, sys.argv
    subprocess.run = fake_run          # mod.subprocess 即全局模块，须还原
    os.environ["OPERATOR_USER"] = "test-op"
    os.environ["OPERATOR_PASS"] = "test-pw"
    sys.argv = ["operator_shift.py"]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = mod.main()
    finally:
        subprocess.run = real_run
        sys.argv = real_argv
    return rc, counter["dispatch"], buf.getvalue()


def main() -> int:
    npass = 0
    for name, mode, want_rc, want_dispatch in CASES:
        rc, dispatch, out = run_case(mode)
        ok = rc == want_rc and dispatch == want_dispatch
        # 失败注入例必须留下 fail-closed 诊断行（对照组无此要求）
        if ok and want_rc != 0:
            ok = "fail closed" in out
        print(f"{'PASS' if ok else 'FAIL'}  {name}  rc={rc} dispatch={dispatch}")
        if not ok:
            print("  stdout:", out.strip()[:200])
        npass += ok
    print(f"{npass}/{len(CASES)}")
    return 0 if npass == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
