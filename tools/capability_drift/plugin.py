"""pytest 插件：把验证案例测出的关键数字写成 JSON，供 drift runner 读取。

案例侧用法（一行，不许 print 解析）::

    from tools.capability_drift.plugin import record
    record("synth_metric", 1.2345)

runner 侧用法::

    CAPDRIFT_METRICS_OUT=/tmp/x.json python -m pytest verif/test_x.py::test_y \
        -p tools.capability_drift.plugin -q

指标必须来自真实计算：``record`` 只接受有限实数，NaN/inf 直接拒绝。
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, Optional

OUT_ENV = "CAPDRIFT_METRICS_OUT"
_METRICS: Dict[str, float] = {}
_META: Dict[str, Dict[str, object]] = {}


def record(name: str, value: float, **meta: object) -> float:
    """记录一个关键数字。返回原值方便断言写法 ``assert record('x', v) > 0``。"""
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"metric {name!r} is not finite: {value!r}")
    _METRICS[str(name)] = v
    if meta:
        _META[str(name)] = dict(meta)
    return v


def recorded() -> Dict[str, float]:
    return dict(_METRICS)


def pytest_sessionstart(session) -> None:  # noqa: ANN001 - pytest hook signature
    _METRICS.clear()
    _META.clear()


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001 - pytest hook signature
    out: Optional[str] = os.environ.get(OUT_ENV)
    if not out:
        return
    payload = {
        "metrics": dict(_METRICS),
        "meta": dict(_META),
        "exitstatus": int(exitstatus),
    }
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
    os.replace(tmp, out)