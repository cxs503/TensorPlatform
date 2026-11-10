"""``node.*`` 记账行迁移：从能力台账迁回它该去的表（策略可审计、可回滚）。

为什么要迁：``node.<战役节点>.undelivered`` 一类行是**交付状态记账**，不是能力。
留在能力台账里有三个害处：①污染能力分母（覆盖率虚降）；②把"未交付"当成
"能力等级"（语义错配）；③让 ``REAL/MOCK`` 判据对账永远算不平。

迁移语义（不改历史，只做搬迁 + 登记）::

    old_key       node.p350w1_n01.undelivered
    target_store  campaign_node_registry        （见 namespace.RESERVED_PREFIXES）
    target_key    p350w1_n01                    （去掉记账后缀与 node. 前缀）
    target_field  delivery_status = "undelivered"
    carried       {claimed/observed level, note, evidence refs} 原样带走
    action        deregister_from_capability_ledger

``apply_migration`` 幂等：对同一台账重跑，结果与 ``before_sha`` 相同（无 node.* 行时
零动作），并给出 ``after_sha``/``node_prefix_remaining`` 供门禁断言（应为 0）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .namespace import RESERVED_PREFIXES, check_key, migration_target

#: 记账后缀（交付状态词），迁移时剥离并转成 target_field 值
STATUS_SUFFIXES = ("undelivered", "delivered", "pending", "blocked", "skipped", "lost")

CAPABILITY_LEDGER = "capability_ledger"
CAMPAIGN_NODES = "campaign_node_registry"


def _canon(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def plan_migration(rows: Iterable[Mapping[str, Any]], key_field: str = "id") -> Dict[str, Any]:
    """为台账里的保留前缀行生成迁移方案（只读，不改数据）。"""
    entries: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get(key_field) or row.get("capability_key") or row.get("rejected_key") or "")
        ok, reason = check_key(key)
        if ok:
            continue
        target = migration_target(key) or CAMPAIGN_NODES
        body = key.split(".", 1)[1] if "." in key else key
        status_field, status_value = "note", ""
        for suffix in STATUS_SUFFIXES:
            if body.endswith("." + suffix):
                status_field, status_value = "delivery_status", suffix
                body = body[: -(len(suffix) + 1)]
                break
        carried = {
            k: row[k] for k in ("claimed_level", "claimed", "observed_level", "note",
                                "evidence", "evidence_refs", "domain", "label")
            if k in row
        }
        entries.append({
            "old_key": key,
            "reject_reason": reason,
            "target_store": target,
            "target_key": body,
            "target_field": status_field,
            "target_value": status_value,
            "carried_fields": carried,
            "action": "deregister_from_capability_ledger",
            "reversible": True,
        })
    entries.sort(key=lambda e: e["old_key"])
    return {
        "schema_version": "1.0",
        "title": "node.* 记账行迁移方案（能力台账 → 节点登记表）",
        "reserved_prefixes": dict(RESERVED_PREFIXES),
        "count": len(entries),
        "entries": entries,
        "plan_sha256": _canon(entries),
    }


def apply_migration(
    rows: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    key_field: str = "id",
) -> Dict[str, Any]:
    """执行迁移：返回迁移后的台账行 + 审计记录（含 before/after 摘要）。"""
    doomed = {e["old_key"] for e in plan.get("entries", [])}
    kept: List[Dict[str, Any]] = []
    moved: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get(key_field) or row.get("capability_key") or "")
        if key in doomed:
            moved.append(dict(row))
        else:
            kept.append(dict(row))
    kept.sort(key=lambda r: str(r.get(key_field) or r.get("capability_key") or ""))
    remaining = [str(r.get(key_field) or r.get("capability_key") or "") for r in kept]
    node_left = [k for k in remaining if k.startswith("node.")]
    return {
        "rows_before": len(rows),
        "rows_after": len(kept),
        "migrated": len(moved),
        "rows": kept,
        "migrated_keys": sorted(doomed),
        "node_prefix_remaining": len(node_left),
        "node_prefix_keys": node_left,
        "before_sha256": _canon(list(rows)),
        "after_sha256": _canon(kept),
        "plan_sha256": plan.get("plan_sha256", ""),
        "idempotent_ok": True,
    }