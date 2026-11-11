#!/usr/bin/env python3
"""Trace runner: fatigue-life curve (log10 N) from ONE consumer's implementation.

生产方/消费方两侧的实现各有一个 runner（``sn_curve_producer.py`` /
``sn_curve_consumer.py``），只有 ``WORKTREE``/``MODULE`` 不同——同一网格、同一
坐标变换，保证「比的是两侧的律，不是比两个不同的问题」。曲线类别、应力单位与
参数全部由被测模块自己声明，runner 不写任何标准数值。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

SIBLING_ROOT = Path(os.environ.get("TP_SIBLING_ROOT", "/root/tp-agent/work"))
WORKTREE = SIBLING_ROOT / os.environ["SN_WORKTREE"]
MODULE = WORKTREE / os.environ["SN_MODULE"]
FUNC = os.environ.get("SN_FUNC", "sn_curve_cycles")
GRID = [15.0, 20.0, 34.0, 40.0, 53.4, 60.0, 80.0, 100.0, 150.0]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    """按文件路径加载被测实现：先注册进 sys.modules（dataclass 需要它）。"""
    sys.path.insert(0, str(path.parent))
    name = f"sn_under_test_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def main() -> int:
    if not MODULE.is_file():
        print(json.dumps({"error": f"module absent: {MODULE}"}))
        return 1
    mod = load(MODULE)
    fn = getattr(mod, FUNC)
    rows = []
    for ds in GRID:
        n = float(fn(ds))
        rows.append({"param": ds, "value": float(__import__("math").log10(n))})
    meta = {"module_sha256": sha256(MODULE), "module_path": str(MODULE), "func": FUNC}
    # 把两侧各自的曲线参数抽成**标量指标**（键名两侧不同，这里做机械归一），
    # 使闸能直接比对同一物理参数，而不是比字典。
    curve = None
    sn = getattr(mod, "DNV_SN_CURVES", None)
    if isinstance(sn, dict):
        curve = sn.get(os.environ.get("SN_CURVE_ID", "D"))
    if curve is None:
        curve = getattr(mod, "DNV_D_CURVE", None)
    if isinstance(curve, dict):
        alias = {"log_a1": ("log_a1", "log_A1"), "log_a2": ("log_a2", "log_A2"),
                 "knee": ("delta_sigma_c", "Sc", "knee"), "m1": ("m1",), "m2": ("m2",)}
        for out_key, cands in alias.items():
            for c in cands:
                if isinstance(curve.get(c), (int, float)):
                    meta[out_key] = float(curve[c])
                    break
    print(json.dumps({"rows": rows, "meta": meta}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())