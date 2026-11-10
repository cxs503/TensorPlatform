"""Hash-chained append-only audit chain (p156w2-n07).

每一次等级变更/回滚/裁定都作为一条 event 追加到链上：
``event.sha256 = sha256(canonical(seq, event_type, capability_id, payload,
prev_sha256, timestamp))``，``prev_sha256`` 指向前一条 event 的 ``sha256``
（创世事件指向 ``GENESIS``）。因此：

* 任何历史事件被改写 → 该事件自身 sha256 不再匹配，``verify()`` 在该 seq 报错；
* 任何事件被删除 → seq 断号 / prev 链接断裂；
* 任何事件被插入 → 后继事件的 prev_sha256 不再匹配。

链只追加不重写；回滚同样是「追加一条 ROLLBACK 事件」，而不是抹掉历史。

零领域语义：链不关心能力叫什么，只关心 ``capability_id`` 这个不透明字符串。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

GENESIS: str = "0" * 64

# 事件类型（闭合集合：出现未定义类型即为契约违约）
EVENT_TYPES = (
    "INIT",        # 能力首次进入台账
    "UPGRADE",     # 证据支持更高等级
    "DOWNGRADE",   # 证据支持更低等级（非矛盾驱动）
    "ROLLBACK",    # 先前判定被后续证据推翻 → 回滚（附 rollback_of 变更链）
    "REBASIS",     # 等级不变但证据基座被新证据替换
    "MANUAL_ROLLBACK",  # 人工/外部触发的世代回滚
)


def now_iso() -> str:
    """UTC ISO8601 时间戳（秒精度，避免抖动影响指纹可复现性）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_bytes(obj: Any) -> bytes:
    """规范化 JSON 编码（键排序、紧凑分隔符、UTF-8），用于稳定哈希。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def event_digest(seq: int, event_type: str, capability_id: str,
                 payload: Dict[str, Any], prev_sha256: str,
                 timestamp: str) -> str:
    """事件指纹：覆盖全部语义字段 + 前驱指纹（防篡改的核心）。"""
    return sha256_of({
        "seq": seq,
        "event_type": event_type,
        "capability_id": capability_id,
        "payload": payload,
        "prev_sha256": prev_sha256,
        "timestamp": timestamp,
    })


@dataclass(frozen=True)
class ChainEvent:
    """审计链上的一条事件（不可变）。"""
    seq: int
    event_type: str
    capability_id: str
    payload: Dict[str, Any]
    prev_sha256: str
    sha256: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "capability_id": self.capability_id,
            "payload": self.payload,
            "prev_sha256": self.prev_sha256,
            "sha256": self.sha256,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ChainEvent":
        return ChainEvent(
            seq=int(d["seq"]),
            event_type=str(d["event_type"]),
            capability_id=str(d["capability_id"]),
            payload=dict(d.get("payload") or {}),
            prev_sha256=str(d["prev_sha256"]),
            sha256=str(d["sha256"]),
            timestamp=str(d["timestamp"]),
        )


@dataclass(frozen=True)
class ChainVerification:
    """链校验结果。"""
    ok: bool
    checked: int
    head_sha256: str
    broken_at: Optional[int] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "head_sha256": self.head_sha256,
            "broken_at": self.broken_at,
            "reason": self.reason,
        }


class AuditChain:
    """追加式哈希链。

    Parameters
    ----------
    events : sequence of dict or ChainEvent, optional
        载入既有链（来自台账 JSON）。不做真实性校验——调用方应随后 ``verify()``。
    clock : callable, optional
        时间源（测试注入固定时钟以获得可复现指纹）。
    """

    def __init__(self, events: Optional[Sequence[Any]] = None,
                 clock=now_iso):
        self._events: List[ChainEvent] = []
        self._clock = clock
        for ev in events or ():
            self._events.append(ev if isinstance(ev, ChainEvent)
                                else ChainEvent.from_dict(ev))

    # ── 基本属性 ────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterable[ChainEvent]:
        return iter(self._events)

    @property
    def head(self) -> str:
        return self._events[-1].sha256 if self._events else GENESIS

    def events(self) -> List[ChainEvent]:
        return list(self._events)

    def events_for(self, capability_id: str) -> List[ChainEvent]:
        return [e for e in self._events if e.capability_id == capability_id]

    def next_seq(self) -> int:
        return len(self._events)

    # ── 追加 ────────────────────────────────────────────────────────

    def append(self, event_type: str, capability_id: str,
               payload: Optional[Dict[str, Any]] = None,
               timestamp: Optional[str] = None) -> ChainEvent:
        """追加一条事件并返回它（链尾即新指纹）。"""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}; allowed={EVENT_TYPES}")
        ts = timestamp or self._clock()
        seq = len(self._events)
        prev = self.head
        body = dict(payload or {})
        digest = event_digest(seq, event_type, capability_id, body, prev, ts)
        ev = ChainEvent(seq=seq, event_type=event_type, capability_id=capability_id,
                        payload=body, prev_sha256=prev, sha256=digest, timestamp=ts)
        self._events.append(ev)
        return ev

    # ── 校验 ────────────────────────────────────────────────────────

    def verify(self, expected_head: Optional[str] = None,
               expected_length: Optional[int] = None) -> ChainVerification:
        """全链校验：seq 连续 + prev 链接 + 自身指纹重算（+ 可选锚点）。

        Parameters
        ----------
        expected_head : str, optional
            外部锚点记录的链头指纹（如台账旁挂的 ``*.head`` 锚文件）。
        expected_length : int, optional
            外部锚点记录的链长。**尾部截断只能靠锚点发现**：仅有 prev 链接时，
            删掉最后 k 条事件后的链本身仍然自洽（这是一个真实局限，必须显式
            用锚点补上）。

        Returns
        -------
        ChainVerification
            ``ok=True`` 表示未被篡改；否则 ``broken_at`` 给出首个断点 seq。
        """
        if expected_length is not None and len(self._events) < expected_length:
            return ChainVerification(False, len(self._events), self.head,
                                     len(self._events),
                                     f"tail truncated: {len(self._events)} "
                                     f"< anchored {expected_length}")
        if expected_head and expected_length is not None:
            if expected_length == 0:
                if self.head != expected_head:      # 空链锚：链头必须仍是创世值
                    return ChainVerification(False, 0, self.head, None,
                                             "non-empty chain vs empty anchor")
            else:
                idx = expected_length - 1
                if self._events[idx].sha256 != expected_head:
                    return ChainVerification(False, idx, self.head, idx,
                                             "history rewritten at anchored event")
        if expected_length is None and expected_head and self.head != expected_head:
            return ChainVerification(False, len(self._events), self.head, None,
                                     "chain head != anchored head")
        prev = GENESIS
        for idx, ev in enumerate(self._events):
            if ev.seq != idx:
                return ChainVerification(False, idx, self.head, idx,
                                         f"seq gap: expected {idx}, got {ev.seq}")
            if ev.prev_sha256 != prev:
                return ChainVerification(False, idx, self.head, idx,
                                         "prev_sha256 mismatch (event inserted/deleted)")
            expect = event_digest(ev.seq, ev.event_type, ev.capability_id,
                                 ev.payload, ev.prev_sha256, ev.timestamp)
            if expect != ev.sha256:
                return ChainVerification(False, idx, self.head, idx,
                                         "event digest mismatch (payload tampered)")
            prev = ev.sha256
        return ChainVerification(True, len(self._events), self.head, None, "ok")

    def to_list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._events]

    # ── 只读派生视图 ────────────────────────────────────────────────

    def transitions(self) -> List[Dict[str, Any]]:
        """等级变迁视图：仅保留 from→to 变化的字段，供报告/图使用。"""
        out = []
        for ev in self._events:
            p = ev.payload
            out.append({
                "seq": ev.seq,
                "event_type": ev.event_type,
                "capability_id": ev.capability_id,
                "from_level": p.get("from_level"),
                "to_level": p.get("to_level"),
                "rule_fired": p.get("rule_fired"),
                "rollback_of": p.get("rollback_of"),
                "evidence_sha256": p.get("evidence_sha256"),
                "timestamp": ev.timestamp,
                "sha256": ev.sha256,
            })
        return out