"""共享原语：证据等级格、规范化序列化、sha256 锚。

所有比较与哈希都走这里的单一实现，保证「同一输入 → 同一 anchor」可复现。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable

REPORT_VERSION = "capability-reconcile/1.0"

#: 证据等级格（弱 → 强）。判定比较一律用 :data:`RANK`。
LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
RANK: Dict[str, int] = {lv: i for i, lv in enumerate(LEVELS)}

#: 判定为「已定位到实现代码」的等级（其余表示代码缺失或空壳）。
HAS_CODE = frozenset({"STUB", "MOCK", "WITHHELD", "REAL"})

#: 报告里不影响语义的易变字段——anchor 计算前必须剔除，否则不可复现。
VOLATILE_KEYS = ("generated_at", "runtime", "elapsed_s", "host", "duration_ms")

#: 判定词表（对账结论）。
VERDICTS = ("CONSISTENT", "OVERCLAIM", "UNDERCLAIM", "UNVERIFIED")


def canonical_json(obj: Any) -> str:
    """确定性 JSON 序列化：键排序、紧凑分隔、保留非 ASCII（不转义）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(payload: Any) -> str:
    """对字符串/字节/对象取 sha256；对象先走 :func:`canonical_json`。"""
    if isinstance(payload, bytes):
        data = payload
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def strip_volatile(obj: Any, drop: Iterable[str] = ()) -> Any:
    """递归删除易变字段 + 自述区块，得到一个可复算的语义载荷。"""
    dropped = set(VOLATILE_KEYS) | set(drop)
    if isinstance(obj, dict):
        return {k: strip_volatile(v, drop) for k, v in obj.items() if k not in dropped}
    if isinstance(obj, list):
        return [strip_volatile(v, drop) for v in obj]
    return obj


#: anchor 计算时永远剔除的自述区块（校验结果与复现自述不参与自身哈希）。
ANCHOR_DROP = ("validation", "reproducibility", "anchor")


def anchor_of(report: Any, drop: Iterable[str] = ANCHOR_DROP) -> str:
    """报告的审计锚：语义载荷（去易变字段与自述区块）的 sha256。"""
    return sha256_hex(strip_volatile(report, drop))


def rank(level: str) -> int:
    return RANK.get(str(level).strip().upper(), -1)


def weaker(a: str, b: str) -> bool:
    """a 是否严格弱于 b。"""
    return rank(a) < rank(b)


def chain_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐能力记录串成 sha256 链，返回链明细 + 链头。

    记录顺序 = 能力 id 字典序（调用方保证有序），因此同一台账同一工作树
    重跑得到同一链头。
    """
    chain = []
    prev = "0" * 64
    for i, rec in enumerate(records, 1):
        payload = sha256_hex(rec)
        rec_sha = sha256_hex({"prev": prev, "payload": payload, "seq": i})
        chain.append(
            {
                "seq": i,
                "capability_id": rec.get("id"),
                "payload_sha256": payload,
                "prev_sha256": prev,
                "record_sha256": rec_sha,
            }
        )
        prev = rec_sha
    return {"len": len(chain), "head_sha256": prev, "chain": chain}