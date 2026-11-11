"""规范化表示：稳定 JSON 序列化 + sha256 指纹 + 易变字段剥离。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

#: 幂等比对时必须剥离的易变字段（时间戳/耗时/主机名等）
VOLATILE_KEYS: frozenset[str] = frozenset(
    {"generated_at", "wall_seconds", "duration_s", "host", "hostname", "pid", "run_id"}
)


def canonical(obj: Any) -> str:
    """稳定序列化：键序固定、无空格、UTF-8 原样（同输入必同输出）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical(obj))


def strip_volatile(obj: Any) -> Any:
    """递归剥离 VOLATILE_KEYS，得到可与上次运行比对的主体。"""
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def stable_digest(obj: Any) -> str:
    """幂等指纹：剥离易变字段后的 sha256（C6 判据的度量）。"""
    return sha256_obj(strip_volatile(obj))
