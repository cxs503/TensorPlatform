"""证据工件安全加载器（m2059）——测试不得在 import 期硬 import ``artifacts/`` 下的模块。

背景：``artifacts/`` 里的参照实现（ref_vibration / lewis_reference 等）由具体任务单生成，
提交门禁会把未登记工件挡在提交之外，工作树里因此可能不存在。硬 ``import`` 会抛
ModuleNotFoundError —— 在 pytest 里这不是「某条用例失败」，而是**整轮采集中止**
（``errors during collection``，退出码 2），同批 2590 例一条都跑不到。

本模块把这种依赖降级成显式 WITHHELD skip（带可复现的恢复指令），工件存在时照常加载。
用法::

    import _evidence
    ref = _evidence.load_ref("ref_vibration", "artifacts/verif_vibration")
    path = _evidence.require("artifacts/verif_vibration/results.json")

``TP_EVIDENCE_ROOT``（``os.pathsep`` 分隔）可把搜索面扩到别的 worktree，
用于跨单复算而不必把工件复制进本仓。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path(__file__).resolve().parent / "evidence_registry.json"


def registry() -> dict:
    """工件 → {provenance, recover, consumers} 的登记表（缺失时返回空表）。"""
    if not REGISTRY_PATH.exists():
        return {}
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {e["artifact"]: e for e in data.get("artifacts", [])}


def search_roots() -> list:
    roots = [ROOT]
    for extra in filter(None, os.environ.get("TP_EVIDENCE_ROOT", "").split(os.pathsep)):
        roots.append(Path(extra))
    return roots


def locate(relpath: str):
    """在仓库根与 TP_EVIDENCE_ROOT 下定位相对路径工件；找不到返回 None。"""
    for root in search_roots():
        cand = root / relpath
        if cand.exists():
            return cand
    return None


def skip_reason(relpath: str) -> str:
    entry = registry().get(relpath, {})
    searched = ", ".join(str(r / relpath) for r in search_roots())
    return (
        "WITHHELD: 证据工件缺失 %s（已搜: %s）。来源任务: %s；恢复: %s"
        % (relpath, searched, entry.get("provenance", "未登记"),
           entry.get("recover", "见 tests/evidence_registry.json"))
    )


def require(relpath: str) -> Path:
    """返回工件路径；缺失则 pytest.skip（模块级 skip，采集因此不会中止）。"""
    found = locate(relpath)
    if found is None:
        pytest.skip(skip_reason(relpath), allow_module_level=True)
    return found


def _load(modname: str, path: Path):
    spec = importlib.util.spec_from_file_location("ev_" + modname, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_ref(modname: str, reldir: str):
    """从证据目录加载参考实现模块 ``<reldir>/<modname>.py``。"""
    builtin = Path(__file__).resolve().parent / "reference" / (modname + ".py")
    if builtin.exists():
        return _load(modname, builtin)
    d = require(reldir)
    py = Path(d) / (modname + ".py")
    if not py.exists():
        pytest.skip(skip_reason(reldir + "/" + modname + ".py"), allow_module_level=True)
    return _load(modname, py)


def load_file(modname: str, relpath: str):
    """加载任意单文件工件（非包），返回模块对象。"""
    return _load(modname, require(relpath))
