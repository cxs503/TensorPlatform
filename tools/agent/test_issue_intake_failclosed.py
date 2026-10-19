#!/usr/bin/env python3
"""issue_intake fail-closed + 幂等回归（GPT #23 同标准）。

  F1-F7 fetch 层：200 正常 / 401 / 500 / 传输错误 / 响应非 JSON /
     响应非数组 / 元素缺 number —— 除 200 外全抛 IssueFetchError
  M1 main 层 fail-closed：fetch 抛错 → rc=2 且 dispatch 0 次
  M2 幂等闭环：派发成功写 state → 同 Issue 二轮 SKIP；goal 标记二保险 SKIP；
     failed 记录默认跳过
运行：python3 test_issue_intake_failclosed.py；退出码 0=全过。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "issue_intake.py"

ISSUE_OK = [{"number": 42, "title": "t", "body": "b",
             "labels": [{"name": "agent-task"}]}]
PR_ITEM = {"number": 43, "title": "pr-as-issue", "pull_request": {"url": "x"}}


def load_module():
    spec = importlib.util.spec_from_file_location("issue_intake_under_test", TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_gh(mode):
    def fake(path, token):
        if mode == "ok":
            return 200, ISSUE_OK + [PR_ITEM]
        if mode == "ok_empty":
            return 200, []
        if mode == "http401":
            return 401, {"message": "Bad credentials"}
        if mode == "http500":
            return 500, None
        if mode == "conn":
            raise RuntimeError("GET /issues 传输错误: URLError(refused)")
        if mode == "badjson":
            raise RuntimeError("GET /issues 响应非 JSON: Expecting value")
        if mode == "bodydict":
            return 200, {"items": ISSUE_OK}
        if mode == "nonumber":
            return 200, [{"title": "no number"}]
        raise AssertionError(mode)
    return fake


def run_fetch(mode):
    mod = load_module()
    mod._gh_request = fake_gh(mode)
    try:
        issues = mod.fetch_open_issues("tok")
        return None, issues
    except mod.IssueFetchError as exc:
        return str(exc), None


def main() -> int:
    npass = 0
    total = 0

    # ── fetch 层矩阵 ──
    err, issues = run_fetch("ok")
    total += 1
    ok = err is None and len(issues) == 1 and issues[0]["number"] == 42
    print(("PASS" if ok else "FAIL") + "  F1 200 正常列表（PR 条目剔除）")
    npass += ok

    err, issues = run_fetch("ok_empty")
    total += 1
    ok = err is None and issues == []
    print(("PASS" if ok else "FAIL") + "  F1b 200 空列表")
    npass += ok

    for name, mode, frag in (("F2 401", "http401", "HTTP 401"),
                             ("F3 500", "http500", "HTTP 500"),
                             ("F4 传输错误", "conn", "传输错误"),
                             ("F5 响应非 JSON", "badjson", "非 JSON"),
                             ("F6 响应非数组", "bodydict", "非数组"),
                             ("F7 元素缺 number", "nonumber", "number")):
        err, _ = run_fetch(mode)
        total += 1
        ok = err is not None and frag in err
        print(("PASS" if ok else "FAIL") + f"  {name}  err={str(err)[:60]}")
        npass += ok

    # ── main 层：fetch 失败 → rc=2、dispatch 0 次 ──
    mod = load_module()
    mod.OFF_SWITCH = Path("/nonexistent-issue-intake.OFF")
    tmp = tempfile.mkdtemp(prefix="intake_test_")
    mod.STATE = Path(tmp) / "state.json"
    os_env = dict(__import__("os").environ)
    __import__("os").environ.update(GH_TOKEN="t", OPERATOR_USER="u",
                                    OPERATOR_PASS="p")

    def boom(token):
        raise mod.IssueFetchError("GET /issues HTTP 500")

    mod.fetch_open_issues = boom
    counter = {"n": 0}

    def no_dispatch(*a, **k):
        counter["n"] += 1
        raise AssertionError("不应派发")

    mod.dispatch_one = no_dispatch
    import io
    sys.argv = ["issue_intake.py"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main()
    total += 1
    ok = rc == 2 and counter["n"] == 0 and "fail closed" in buf.getvalue()
    print(("PASS" if ok else "FAIL") + f"  M1 fetch 失败 fail closed  rc={rc}")
    npass += ok

    # ── 幂等闭环：派发→写 state→二轮 SKIP ──
    mod2 = load_module()
    mod2.OFF_SWITCH = Path("/nonexistent-issue-intake.OFF")
    mod2.STATE = Path(tmp) / "state2.json"
    mod2._gh_request = fake_gh("ok")

    def fake_api(method, path, body=None, tok=None):
        if path.startswith("/auth/login"):
            return 200, {"access_token": "x"}
        return 200, {"items": []}

    mod2.api = fake_api
    mod2.mission_goals = lambda tok: []
    calls = {"n": 0}

    def fake_dispatch(issue, base_ref, env):
        calls["n"] += 1
        return {"id": 900 + calls["n"], "memory_injected": False}

    mod2.dispatch_one = fake_dispatch
    mod2.subprocess.run = (lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": "feat/x\n", "stderr": ""})())
    sys.argv = ["issue_intake.py"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod2.main()
    total += 1
    ok = rc == 0 and calls["n"] == 1 and "DISPATCHED: #42" in buf.getvalue()
    state = json.loads(mod2.STATE.read_text())
    ok = ok and state["42"]["mission"] == 901
    print(("PASS" if ok else "FAIL") + "  M2a 首轮派发并写 state")
    npass += ok

    sys.argv = ["issue_intake.py"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod2.main()
    total += 1
    ok = rc == 0 and calls["n"] == 1 and "已派发过" in buf.getvalue()
    print(("PASS" if ok else "FAIL") + "  M2b 二轮幂等 SKIP")
    npass += ok

    # goal 标记二保险（state 清空但平台已有标记）
    mod2.STATE.unlink()
    mod2.mission_goals = lambda tok: [{"goal": "TensorPlatform 代码任务（GitHub Issue #42）：x"}]
    sys.argv = ["issue_intake.py"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod2.main()
    total += 1
    ok = rc == 0 and calls["n"] == 1 and "goal 标记" in buf.getvalue()
    print(("PASS" if ok else "FAIL") + "  M2c goal 标记二保险 SKIP")
    npass += ok

    # failed 记录默认跳过
    mod2.STATE.write_text(json.dumps(
        {"42": {"failed": True, "error": "dispatch_helper 失败: boom"}}))
    mod2.mission_goals = lambda tok: []
    sys.argv = ["issue_intake.py"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod2.main()
    total += 1
    ok = rc == 0 and calls["n"] == 1 and "上次派发失败" in buf.getvalue()
    print(("PASS" if ok else "FAIL") + "  M2d failed 记录默认跳过")
    npass += ok

    __import__("os").environ.clear()
    __import__("os").environ.update(os_env)
    print(f"{npass}/{total}")
    return 0 if npass == total else 1


if __name__ == "__main__":
    sys.exit(main())
