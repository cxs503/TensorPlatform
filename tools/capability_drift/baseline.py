"""基线管理：新基线必须 owner 签名审批后才生效，且每次更新留审计指纹（sha256）。

基线文件结构::

    {"schema_version": 1, "updated_at": "...", "approval": {...},
     "values": {"<capability>": {"<metric>": 1.234}}}

审批记录由 ``approvals.make_approval('baseline_update', ...)`` 生成，须带
``capability`` 与 ``values_sha256`` 两个字段——即审批针对**具体数字**，不是空白授权。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from . import approvals
from .model import sha256_bytes

SCHEMA_VERSION = 1


class BaselineError(RuntimeError):
    """基线文件损坏或更新被拒。"""


def baseline_sha256(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return sha256_bytes(p.read_bytes())


def load_baseline(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "values": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"invalid baseline JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("values", {}), dict):
        raise BaselineError("baseline.json: 'values' must be an object")
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("values", {})
    return data


def update_baseline(
    path: str | Path,
    capability: str,
    values: Mapping[str, float],
    approval: Mapping[str, Any],
    *,
    key: Optional[str] = None,
    audit_path: Optional[str | Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """校验 owner 审批后写入新基线；返回 {old_sha256,new_sha256,changed,values_sha256}。"""
    clean = {str(k): float(v) for k, v in values.items()}
    values_sha = approvals.sha256_payload(clean)
    approvals.verify_approval(
        approval,
        kind="baseline_update",
        key=key,
        require_fields={"capability": capability, "values_sha256": values_sha},
    )
    p = Path(path)
    old_sha = baseline_sha256(p)
    doc = load_baseline(p)
    doc["values"][capability] = clean
    doc["schema_version"] = SCHEMA_VERSION
    doc["updated_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    doc["approval"] = {k: v for k, v in approval.items()}
    doc["approval_capability"] = capability
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    new_sha = baseline_sha256(p)
    result = {
        "capability": capability,
        "values": clean,
        "values_sha256": values_sha,
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "changed": old_sha != new_sha,
        "approver": str(approval.get("approver") or ""),
        "approved_at": str(approval.get("approved_at") or ""),
        "updated_at": doc["updated_at"],
    }
    if audit_path is not None:
        ap = Path(audit_path)
        ap.parent.mkdir(parents=True, exist_ok=True)
        with open(ap, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, sort_keys=True, ensure_ascii=False) + "\n")
    return result


def baseline_values_for(doc: Mapping[str, Any], capability: str) -> Dict[str, float]:
    values = (doc.get("values") or {}).get(capability) or {}
    return {str(k): float(v) for k, v in values.items()}