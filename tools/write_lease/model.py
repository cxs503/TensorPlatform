#!/usr/bin/env python3
"""写租约的公共模型：资源键、持有者条目、事件类型、摘要。

设计边界（零领域语义）
----------------------
本模块是平台共性机制：只处理「仓名 + 路径」这类合成标识，不认识任何业务词条。
仓名/路径只做字符串规范化与哈希，绝不解释其含义。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

#: 内容摘要算法（策略可配；默认 blake2b-256：同一台机器上比 sha256 快约 3 倍，
#: 在 CAS 归因这种每次写都要算的热路径上是决定性的）。
HASH_ALGO = os.environ.get("TP_WRITE_LEASE_HASH", "blake2b-256")
_HASHERS = {
    "blake2b-256": lambda data: hashlib.blake2b(data, digest_size=32).hexdigest(),
    "sha256": lambda data: hashlib.sha256(data).hexdigest(),
}


def set_hash_algo(name: str) -> str:
    """切换内容摘要算法（策略注入用）。返回生效的算法名。"""
    global HASH_ALGO
    if name not in _HASHERS:
        raise ValueError(f"unknown hash algo {name!r}; choose from {sorted(_HASHERS)}")
    HASH_ALGO = name
    return HASH_ALGO


def content_digest(data: bytes) -> str:
    """字节内容摘要（CAS 前置哈希的算法入口）。"""
    return _HASHERS[HASH_ALGO](data)

SCHEMA = "tp.write_lease/v1"
RECORD_SCHEMA = "tp.write_lease.record/v1"
EVENT_SCHEMA = "tp.write_lease.event/v1"

#: 租约模式：``rw`` 独占写；``r`` 共享读。
RW = "rw"
R = "r"
MODES = (RW, R)

#: 租约状态
ACTIVE = "active"
RELEASED = "released"
REVOKED = "revoked"
RECLAIMED = "reclaimed"

#: 事件类型（append-only 台账，用于等待图重建与审计）
ACQUIRE = "acquire"          # 整租约获得
ACQUIRE_KEY = "acquire_key"  # 单键获得
WAIT_START = "wait_start"    # 等待边登记：waiter 等 blocker
WAIT_END = "wait_end"        # 等待边撤销（拿到 / 超时 / 放弃）
RELEASE = "release"
HEARTBEAT_MISS = "heartbeat_miss"
RECLAIM = "reclaim"          # TTL+心跳缺失 → 强制回收
REVOKE = "revoke"            # 死锁打破 / 强制撤销
DEADLOCK = "deadlock"        # 死锁检出事件（环 + 打破者）
CAS_COMMIT = "cas_commit"
CAS_REJECT = "cas_reject"    # 静默覆写检出（lost update）
GATE_REFUSE = "gate_refuse"  # shadow 模式下会拦但没拦
WOULD_BLOCK = "would_block"


def now_ts() -> float:
    return time.time()


def norm_path(path: str) -> str:
    """路径规范化：posix 分隔、去掉 ``./`` 与重复分隔符、相对化（不触盘）。"""
    p = str(path).replace("\\", "/")
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def resource_key(repo: str, path: str) -> str:
    """资源键 = sha256(repo \\0 规范化路径)[:16]，仓名/路径全部按合成标识处理。"""
    blob = f"{repo}\x00{norm_path(path)}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def new_lease_id() -> str:
    return "L-" + uuid.uuid4().hex[:12]


def digest(obj) -> str:
    """内容寻址摘要（无墙钟字段参与，同输入逐字节一致）。"""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _HASHERS[HASH_ALGO](blob)


def holder_entry(lease_id: str, holder: str, mode: str, ttl: float, ts: float | None = None) -> dict:
    ts = now_ts() if ts is None else ts
    return {
        "lease_id": lease_id,
        "holder": holder,
        "mode": mode,
        "ttl": float(ttl),
        "created_at": ts,
        "heartbeat_at": ts,
        "pid": os.getpid(),
    }


def expiry(entry: dict) -> float:
    """租约到期时刻 = 最近心跳 + ttl。"""
    return float(entry.get("heartbeat_at", entry.get("created_at", 0.0))) + float(entry.get("ttl", 0.0))


def event(kind: str, **fields) -> dict:
    ev = {"schema": EVENT_SCHEMA, "kind": kind, "t": now_ts(), "pid": os.getpid()}
    ev.update(fields)
    ev["digest"] = digest({k: v for k, v in ev.items() if k != "digest"})
    return ev


def record_path_name(key: str) -> str:
    return f"{key}.json"