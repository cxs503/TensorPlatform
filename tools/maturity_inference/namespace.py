"""命名空间门：能力键（``capability_key``）的语法与保留前缀管制。

规则（机器可判）::

    1. 语法：``^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$``（至少两段，小写点分命名）
    2. 保留前缀：``node.`` / ``campaign.`` / ``tmp.`` / ``test.`` / ``delivery.``
       ——这些是**记账行**（节点交付状态、临时对象）而非能力，一律拒收并进迁移清单。

拒收不是丢弃：每条拒收行带 ``reason`` + ``migration_kind``，由
``tools.maturity_inference.migrate`` 生成迁移方案（记账行回它该去的表）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

#: 能力键语法（至少两段：``domain.thing``）
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")

#: 保留前缀 → 迁移去向（记账行不是能力）
RESERVED_PREFIXES: Dict[str, str] = {
    "node.": "campaign_node_registry",
    "campaign.": "campaign_node_registry",
    "delivery.": "delivery_hygiene_ledger",
    "tmp.": "ephemeral_scratch",
    "test.": "ephemeral_scratch",
}

_MAX_KEY_LEN = 120


def check_key(key: Any) -> Tuple[bool, str]:
    """校验一个能力键，返回 ``(是否接受, 原因)``。

    ``reason`` 在拒收时必非空，接受时为 ``"ok"``。判据顺序：类型 → 长度 →
    保留前缀 → 语法。保留前缀先于语法判，因为 ``node.*`` 语法上合法但语义上是记账行。
    """
    if not isinstance(key, str) or not key.strip():
        return False, "empty_or_non_string"
    raw = key.strip()
    if raw != key:
        return False, "whitespace_padding"
    if len(raw) > _MAX_KEY_LEN:
        return False, f"too_long:{len(raw)}>{_MAX_KEY_LEN}"
    if raw != raw.lower():
        return False, "not_lowercase"
    for prefix, target in RESERVED_PREFIXES.items():
        if raw.startswith(prefix):
            return False, f"reserved_prefix:{prefix}->{target}"
    if not KEY_RE.match(raw):
        return False, "syntax:mismatch"
    return True, "ok"


def migration_target(key: str) -> str:
    """保留前缀行 → 迁移目标表名；非保留前缀返回空串。"""
    for prefix, target in RESERVED_PREFIXES.items():
        if key.startswith(prefix):
            return target
    return ""


def screen_rows(rows: Iterable[Mapping[str, Any]], key_field: str = "id") -> Dict[str, List[Dict[str, Any]]]:
    """把一批台账行分成 ``accepted`` / ``rejected``（拒收行附迁移去向）。

    参数 ``key_field`` 允许调用方指认键字段（``id`` / ``capability_key`` / ``capability_id``）。
    """
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for row in rows:
        key = row.get(key_field)
        ok, reason = check_key(key)
        if ok:
            accepted.append(dict(row))
        else:
            entry = dict(row)
            entry["rejected_key"] = key
            entry["reason"] = reason
            entry["migration_target"] = migration_target(str(key or ""))
            rejected.append(entry)
    accepted.sort(key=lambda r: str(r.get(key_field, "")))
    rejected.sort(key=lambda r: str(r.get("rejected_key", "")))
    return {"accepted": accepted, "rejected": rejected}


def duplicates(keys: Sequence[str]) -> List[str]:
    """返回重复键（不重复即空表）——台账唯一性检查。"""
    seen: set = set()
    dup: List[str] = []
    for k in keys:
        if k in seen and k not in dup:
            dup.append(k)
        seen.add(k)
    return sorted(dup)