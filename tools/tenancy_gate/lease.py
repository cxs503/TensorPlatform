#!/usr/bin/env python3
"""派单前的路径预留（lease）门：冲突则拒派或降级只读。

语义
----
* 一条租约 = ``(repo, path_glob, mission_id, ts, mode)``。``mode=write`` 的租约之间
  若在两仓相同、时间窗内、且两个路径模式**可能相交**，即视为冲突。
* ``mode=read`` 的租约永不阻塞别人（只读者不改文件）。
* 过期租约（``now - ts > ttl_seconds``）不参与判定，但会记进 ``expired_dropped``
  以便审计"为什么当时放行了"。
* 处置策略由配置决定：``refuse``（拒派，``admitted=false``）或
  ``downgrade_readonly``（放行为只读，``mode=read``）。

判定顺序（先到先得，全部可复算）
------------------------------
``invalid_glob`` → ``protected_path`` → ``outside_writable_roots`` →
``lease_conflict`` → 放行。
"""
from __future__ import annotations

from .model import LEASE_SCHEMA, READ, WRITE, PathLease, parse_ts, with_digest
from .pattern import GlobSyntaxError, glob_covers_glob, globs_intersect

__all__ = ["check_lease", "active_leases"]


def active_leases(leases, now_ts: str, ttl_seconds: int):
    """过滤过期租约；返回 ``(active, expired_count)``。"""
    now = parse_ts(now_ts)
    alive, expired = [], 0
    for lease in leases:
        try:
            age = (now - parse_ts(lease.ts)).total_seconds()
        except Exception:
            expired += 1                      # 时间戳坏 → 不当作有效租约（fail-closed 在别处）
            continue
        if age > ttl_seconds or age < -ttl_seconds:
            expired += 1
            continue
        alive.append(lease)
    return alive, expired


def check_lease(candidate: PathLease, leases, config: dict, now_ts: str | None = None) -> dict:
    """判定一条待派发的路径预留是否可放行。

    返回可直接入库/上报的 dict（含 ``digest``，同输入逐字节一致）。
    """
    lease_cfg = config["lease"]
    now_ts = now_ts or candidate.ts
    expired = 0
    conflicts: list[dict] = []
    reason = "admitted"
    mode = candidate.mode
    admitted = True

    if candidate.mode == READ and not lease_cfg["allow_read_leases"]:
        return with_digest(
            {
                "schema": LEASE_SCHEMA,
                "candidate": candidate.as_dict(),
                "admitted": False,
                "granted_mode": "none",
                "reason": "read_leases_disabled",
                "conflicts": [],
                "expired_dropped": 0,
                "policy": lease_cfg["on_conflict"],
            }
        )

    alive, expired = active_leases(leases, now_ts, int(lease_cfg["ttl_seconds"]))

    protected_hit = [
        g for g in lease_cfg["protected_globs"] if _may_touch(candidate.path_glob, g)
    ]
    in_roots = any(glob_covers_glob(root, candidate.path_glob) for root in lease_cfg["writable_roots"])

    if protected_hit:
        admitted, mode, reason = False, "none", "protected_path"
    elif not in_roots:
        admitted, mode, reason = False, "none", "outside_writable_roots"
    elif candidate.mode == WRITE:
        for lease in sorted(alive, key=lambda x: (x.mission_id, x.path_glob, x.repo)):
            if lease.mode != WRITE or lease.repo != candidate.repo:
                continue
            if globs_intersect(lease.path_glob, candidate.path_glob):
                conflicts.append(
                    {
                        "mission_id": lease.mission_id,
                        "repo": lease.repo,
                        "path_glob": lease.path_glob,
                        "ts": lease.ts,
                    }
                )
        if conflicts:
            if lease_cfg["on_conflict"] == "downgrade_readonly":
                admitted, mode, reason = True, READ, "lease_conflict_downgraded_readonly"
            else:
                admitted, mode, reason = False, "none", "lease_conflict"

    return with_digest(
        {
            "schema": LEASE_SCHEMA,
            "candidate": candidate.as_dict(),
            "admitted": admitted,
            "granted_mode": mode,
            "reason": reason,
            "conflicts": conflicts,
            "expired_dropped": expired,
            "policy": lease_cfg["on_conflict"],
        }
    )


def _may_touch(pattern: str, guard: str) -> bool:
    """候选人模式是否可能触及受保护模式（相交或包含任一方向）。"""
    try:
        return globs_intersect(pattern, guard) or glob_covers_glob(guard, pattern)
    except GlobSyntaxError:      # pragma: no cover - 候选模式已被规范化
        return True