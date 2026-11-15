"""
能力台账适配层 —— 平台 API ↔ tools/capability_ledger 引擎
=========================================================
职责：让平台侧查询接口在「台账产物缺失」时 fail-soft，
返回 21 条能力 + warning，而不是抛 FileNotFoundError / 503。

等级来源：tools/capability_ledger/engine.py 的扫描复算（配置路径见
config/capability_ledger.v1.json），本层不做任何等级判断。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = _REPO_ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from capability_ledger import engine as _engine  # noqa: E402


def repo_root() -> Path:
    return _REPO_ROOT


def load_config() -> dict:
    return _engine.load_config(root=_REPO_ROOT)


def query_ledger(path: str | Path | None = None) -> dict[str, Any]:
    """查询台账（永不抛异常；缺产物时降级为内存复算 + warning）。"""
    return _engine.query(path=path, cfg=load_config(), root=_REPO_ROOT)


def audit_payload(path: str | Path | None = None) -> dict[str, Any]:
    """返回带 21 条能力 + 等级分布 + warnings 的审计载荷。"""
    q = query_ledger(path)
    dist = q.get("level_distribution") or {}
    return {
        "schema_version": q.get("schema_version"),
        "as_of": q.get("as_of"),
        "sha256": q.get("sha256"),
        "total_capabilities": q.get("capability_count", 0),
        "level_distribution": dist,
        "capabilities": q.get("capabilities", []),
        "warnings": q.get("warnings", []),
        "status": q.get("status", "ok"),
    }