#!/usr/bin/env python3
"""``/health`` 的能力台账消费者（消费者 #3）。

``/health`` 此前只报 api/database 两行，运维看不见「证据台账是否还是真值、
有没有漂移、有没有未获证实的能力」。本模块把台账体检结果拼成 ``/health`` 的
``capability_ledger`` 字段（键集冻结在 :data:`FIELD_KEYS`）。

纪律：**永不抛异常**——健康端点不能因为台账文件缺失/损坏而 500；任何缺陷都
降级为 ``status="DEGRADED"`` + 具名 ``reasons``。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from tools.audit import ledger_paths as LP
except Exception:  # 台账解析器本身缺失：本模块也必须可用（降级而不是崩）
    LP = None  # type: ignore[assignment]

FIELD_KEYS = ("path", "sha256", "schema_version", "capabilities", "unverified",
              "overclaim", "status", "reasons", "contract_version")


def ledger_ref() -> dict:
    """消费者契约访问器。"""
    if LP is None:
        return {"status": "DEGRADED", "reasons": ["RESOLVER_IMPORT_FAILED"],
                "path": None, "path_rel": None, "sha256": None, "capabilities": 0,
                "unverified": 0, "overclaim": 0, "schema_version": None}
    return LP.resolve_ledger()


def ledger_field() -> dict:
    """``/health`` 的 ``capability_ledger`` 字段（键集固定为 FIELD_KEYS）。"""
    try:
        view = LP.ledger_view(ledger_ref()) if LP is not None else {}
    except Exception as exc:  # 兜底：任何异常都变成降级字段，绝不 500
        view = {"status": "DEGRADED", "reasons": [f"RESOLVER_ERROR:{type(exc).__name__}"],
                "path": None, "sha256": None, "schema_version": None,
                "capabilities": 0, "unverified": 0, "overclaim": 0,
                "contract_version": "unknown"}
    return {k: view.get(k) for k in FIELD_KEYS}


if __name__ == "__main__":
    import json
    print(json.dumps(ledger_field(), ensure_ascii=False, indent=1))