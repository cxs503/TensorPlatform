"""重放启动器：先装导入面替身，再按 ``__main__`` 语义执行目标脚本。

用法（在干净检出根目录）::

    python3 _repro/launch.py artifacts/<node>/make_artifacts.py [args...]

替身清单来自同目录 ``spec.json`` 的 ``import_stubs`` 字段（默认 matplotlib）。
脚本的工作目录继承调用者（=检出根），``sys.path[0]`` 指向脚本自身目录，与直接
``python3 <script>`` 一致。
"""

from __future__ import annotations

import json
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _stub_names() -> list:
    spec = os.path.join(HERE, "spec.json")
    try:
        with open(spec, "r", encoding="utf-8") as f:
            return list(json.load(f).get("import_stubs") or ["matplotlib"])
    except Exception:
        return ["matplotlib"]


def _sys_path_roots(cwd: str) -> None:
    """历史脚本普遍假定「仓库根 / backend / src」在 sys.path 上（它们原来在仓库根手跑）。"""
    cands = [cwd, os.path.join(cwd, "backend"), os.path.join(cwd, "src"),
             os.path.join(cwd, "backend", "api"), os.path.join(cwd, "backend", "modules")]
    for c in reversed(cands):
        if os.path.isdir(c) and c not in sys.path:
            sys.path.insert(0, c)


def _module_name(script: str) -> str | None:
    """若脚本位于包内（自底向上逐级有 __init__.py），返回可 run_module 的点分名。"""
    parts = script.replace("\\", "/").split("/")
    if not parts[-1].endswith(".py"):
        return None
    pkg_parts = parts[:-1]
    if not pkg_parts:
        return None
    for i in range(1, len(pkg_parts) + 1):
        if not os.path.exists(os.path.join(*pkg_parts[:i], "__init__.py")):
            return None
    return ".".join(pkg_parts + [parts[-1][:-3]])


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: launch.py <script.py> [args...]", file=sys.stderr)
        return 2
    import shims
    shims.install(_stub_names())
    script = sys.argv[1]
    _sys_path_roots(os.getcwd())
    sys.argv = [script] + sys.argv[2:]
    try:
        runpy.run_path(script, run_name="__main__")
    except ImportError as exc:
        mod = _module_name(script)
        if mod and "relative import" in str(exc).lower():
            runpy.run_module(mod, run_name="__main__")
        else:
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())