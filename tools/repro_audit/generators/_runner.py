#!/usr/bin/env python3
"""repro_audit.generators._runner —— 生成器重放子进程（运行时读审计）。

由 ``contract.run_producer`` 以子进程方式拉起：父进程把一份 payload(JSON) 传进来，
本进程安装 ``sys.addaudithook`` 记录**所有** ``open`` 事件（读/写分开），再执行
目标生成器（脚本 or 可调用入口），最后把执行结果 + 读/写清单 + 产出文件 sha256
写成 result(JSON)。

**为什么用审计钩子而不是 AST**：AST 只能看到字面量路径。生成器若用
``glob``/``os.path.join``/间接 helper 读回自己被审计的工件，AST 看不出来；
``open`` 事件在解释器层触发，任何路径拼装方式都逃不掉。这是"生成器不得自读工件"
这条契约的运行时证据。

payload 结构::

    {"module": "scripts/foo.py",            # 仓库相对路径（脚本或模块）
     "entry": "build",                       # kind=callable 时的函数名
     "kind": "script" | "callable",
     "argv": ["foo.py", "--out", "x"],       # kind=script 时 sys.argv
     "kwargs": {},                           # kind=callable 时入参
     "setup": ["pkg.mod:func"],              # 执行前调用的注册函数
     "sys_path": ["backend"],                # 追加到 sys.path 的仓库相对目录
     "result_path": "/tmp/xxx/result.json",  # 本进程写结果的位置
     "watch": "artifacts/foo.json"}          # 期望被产出的工件（仓库相对）

result 结构::

    {"ok": bool, "error": str|None, "reads": [...], "writes": [...],
     "artifact_sha256": str|None, "duration_sec": float}
"""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import sys
import time
import traceback

_READS: list[str] = []
_WRITES: list[str] = []
_ACTIVE = True


def _hook(event: str, args: tuple) -> None:
    if not _ACTIVE or event != "open":
        return
    try:
        path = args[0]
        mode = args[1]
    except IndexError:  # pragma: no cover - 审计事件签名变化时保守降级
        return
    if isinstance(path, bytes):
        try:
            path = path.decode(errors="ignore")
        except Exception:  # pragma: no cover
            return
    if not isinstance(path, (str, os.PathLike)):
        return
    try:
        absp = os.path.abspath(os.fspath(path))
    except Exception:  # pragma: no cover
        return
    writing = isinstance(mode, str) and any(ch in mode for ch in "wax+")
    (_WRITES if writing else _READS).append(absp)


def _sha256(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def main(argv: list[str]) -> int:
    payload = json.loads(open(argv[1], "r", encoding="utf-8").read())
    repo_root = os.path.abspath(payload.get("repo_root") or os.getcwd())
    for rel in payload.get("sys_path") or []:
        p = os.path.join(repo_root, rel)
        if p not in sys.path:
            sys.path.insert(0, p)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    watch = payload.get("watch")
    result: dict = {"ok": False, "error": None, "reads": [], "writes": [],
                    "artifact_sha256": None, "duration_sec": 0.0}

    sys.addaudithook(_hook)
    started = time.time()
    try:
        for spec in payload.get("setup") or []:
            mod_name, _, fn_name = spec.partition(":")
            mod = __import__(mod_name, fromlist=["__name__"])
            getattr(mod, fn_name)()
        kind = payload.get("kind", "script")
        if kind == "callable":
            mod_name, _, fn_name = payload["module"].replace("/", ".").removesuffix(".py").partition(":")
            mod = __import__(mod_name, fromlist=["__name__"])
            fn = getattr(mod, fn_name or payload["entry"])
            ret = fn(**(payload.get("kwargs") or {}))
            if ret is not None and payload.get("watch"):
                # 可调用入口允许"返回 dict"作为产出（历史 gen_*.py 的形态）
                out_path = os.path.join(repo_root, payload["watch"])
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(ret, fh, sort_keys=True)
        else:
            sys.argv = list(payload.get("argv") or [payload["module"]])
            runpy.run_path(os.path.join(repo_root, payload["module"]), run_name="__main__")
        result["ok"] = True
    except SystemExit as exc:  # 脚本正常退出
        code = exc.code
        result["ok"] = code in (0, None)
        if not result["ok"]:
            result["error"] = f"SystemExit({code})"
    except BaseException as exc:  # noqa: BLE001 - 生成器崩了要如实记录
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()[-2000:]
    result["duration_sec"] = round(time.time() - started, 3)

    global _ACTIVE
    _ACTIVE = False
    if watch:
        result["artifact_sha256"] = _sha256(os.path.join(repo_root, watch))
    result["reads"] = sorted(set(_READS))
    result["writes"] = sorted(set(_WRITES))
    with open(payload["result_path"], "w", encoding="utf-8") as fh:
        json.dump(result, fh, sort_keys=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))