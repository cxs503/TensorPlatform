"""owner 审批签名的验证（基线更新 A4 / 退化豁免 A6-白名单共用）。

设计：审批记录是 JSON，签名 = HMAC-SHA256(规范化 JSON(去掉 signature 字段), key)。
key 只从环境变量 ``CAPDRIFT_APPROVAL_KEY`` 读取——**缺 key 一律 fail-closed**（拒绝），
避免仓库内出现任何密钥。审批记录本身（含 approver/approved_at/签名）随基线/白名单
工件一起入库，构成可复核的审计链。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

KEY_ENV = "CAPDRIFT_APPROVAL_KEY"


class ApprovalError(PermissionError):
    """审批缺失/签名不符/内容不符。"""


def canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def approval_key() -> Optional[str]:
    key = os.environ.get(KEY_ENV, "")
    return key or None


def sign(record: Mapping[str, Any], key: str) -> str:
    unsigned = {k: v for k, v in record.items() if k != "signature"}
    return hmac.new(key.encode("utf-8"), canonical(unsigned), hashlib.sha256).hexdigest()


def make_approval(kind: str, approver: str, key: str, **fields: Any) -> Dict[str, Any]:
    """构造一条已签名审批记录（供 owner 侧使用；agent 侧无 key 无法伪造）。"""
    record: Dict[str, Any] = {
        "kind": kind,
        "approver": approver,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    record.update(fields)
    record["signature"] = sign(record, key)
    return record


def verify_approval(
    record: Mapping[str, Any],
    *,
    kind: str,
    key: Optional[str] = None,
    require_fields: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """校验签名与必需字段；不合法抛 ApprovalError，合法返回记录本身。"""
    key = key or approval_key()
    if not key:
        raise ApprovalError(f"no approval key configured ({KEY_ENV}); refusing to honour approval")
    if not isinstance(record, Mapping):
        raise ApprovalError("approval record must be an object")
    if str(record.get("kind") or "") != kind:
        raise ApprovalError(f"approval kind mismatch: expected {kind!r}, got {record.get('kind')!r}")
    if not str(record.get("approver") or "").strip():
        raise ApprovalError("approval without approver")
    if not str(record.get("approved_at") or "").strip():
        raise ApprovalError("approval without approved_at")
    given = str(record.get("signature") or "")
    if not given:
        raise ApprovalError("approval without signature")
    expected = sign(record, key)
    if not hmac.compare_digest(given, expected):
        raise ApprovalError("approval signature mismatch")
    for field_name, want in (require_fields or {}).items():
        if record.get(field_name) != want:
            raise ApprovalError(
                f"approval field {field_name!r} mismatch: expected {want!r}, got {record.get(field_name)!r}"
            )
    return dict(record)


def approval_is_expired(record: Mapping[str, Any], now: Optional[datetime] = None) -> bool:
    expires = record.get("expires_at")
    if not expires:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        return True  # 无法解析 → 视为过期（fail-closed）
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= now