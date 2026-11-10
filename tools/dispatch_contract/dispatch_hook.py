"""dispatch_hook —— 派单链路挂钩：把就绪门包成「永不抛异常」的判定函数。

与 ``tools/tenancy_gate/dispatch_hook.py`` 同构：派单链路不能被门拖死，所以这里
把所有异常收敛为 ``mode=degraded`` 的放行结果。运行模式由 ``DISPATCH_CONTRACT_MODE``
控制：

* ``off``     —— 直接放行（不计算）
* ``report``  —— 计算并返回判定，但不拦（默认；先观察判定质量再收紧）
* ``enforce`` —— 判定 ``BLOCK`` 时 ``blocked=True``，派单侧据此拒派/排队

用法::

    from tools.dispatch_contract.dispatch_hook import pre_dispatch
    res = pre_dispatch(node_id="N1", contract=contract_doc, upstream=registry_snapshot)
    if res["blocked"]:
        ...  # 拒派并记录 res["reason_codes"]
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from .contract_schema import load_config
from .readiness import VERDICT_BLOCK, assess

SCHEMA = "dispatch-contract/dispatch_hook/v1"
MODES = ("off", "report", "enforce")


def pre_dispatch(node_id: str | None = None, contract: Mapping[str, Any] | None = None,
                 upstream: Mapping[str, Any] | None = None, dispatched_at: str | None = None,
                 mode: str | None = None, config: Mapping[str, Any] | None = None) -> dict:
    """派单前就绪判定（永不抛异常）。"""
    mode = (mode or os.environ.get("DISPATCH_CONTRACT_MODE", "report")).lower()
    if mode not in MODES:
        mode = "report"
    out: dict[str, Any] = {"schema": SCHEMA, "mode": mode, "enforced": mode == "enforce",
                          "blocked": False, "verdict": None, "degraded": None,
                          "reason_codes": [], "detail": None, "error": None}
    if mode == "off":
        out["verdict"] = "READY"
        out["degraded"] = True
        out["detail"] = "GATE_OFF"
        return out
    try:
        cfg = config or load_config()
        res = assess({"node_id": node_id, "contract": contract, "upstream": dict(upstream or {}),
                      "dispatched_at": dispatched_at}, cfg)
        out.update(verdict=res["verdict"], degraded=res["degraded"],
                   reason_codes=sorted({u["code"] for u in res["unmet"]}), detail=res)
        out["blocked"] = bool(mode == "enforce" and res["verdict"] == VERDICT_BLOCK)
        return out
    except Exception as exc:  # noqa: BLE001 —— 门不得拖死派单链路
        out.update(verdict="READY", degraded=True, error=f"{type(exc).__name__}:{exc}"[:300],
                   detail="GATE_ERROR_IDENTITY_PASS")
        return out