"""审计哈希链 + S-N 负知识轨。

两条独立账本，都做 **append-only + sha256 前向链**（篡改任一环 → ``verify_*`` 必报错）：

1. ``AuditLog``：全流程事件（派发/取工件/打分/排名/淘汰/晋级/杂交/门禁/裁决）。
   每条 = ``{seq, type, payload, utc, prev, hash}``，``hash = sha256(prev + type + payload + utc)``。
2. ``NegativeLedger``：**S-N 负知识轨**——每个实播席位的产物都入轨（含冠军）。记录的是
   「这个假设被什么物理判据证伪/证实、后续不要再单独投放什么」，记录号 ``S-N-0001``
   起递增，同样带前向哈希，防止事后美化历史。

为什么连冠军也进负知识轨：锦标赛的价值不只是选出赢家，更是把「哪些组合在这道门限下
不可行」变成可检索的资产。只记赢家的锦标赛在下一次会长出同一批失败席位。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .model import canonical, sha256_text

GENESIS = "0" * 64


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditLog:
    """append-only 审计链。时钟可注入（测试传定值 → 逐字节可复现）。"""

    def __init__(self, clock: Callable[[], str] | None = None) -> None:
        self._clock = clock or _utc_now
        self.events: list[dict] = []
        self._head = GENESIS

    # -- 写 ------------------------------------------------------------------------
    def append(self, type_: str, **payload: Any) -> dict:
        body = {
            "seq": len(self.events) + 1,
            "type": type_,
            "payload": payload,
            "utc": self._clock(),
            "prev": self._head,
        }
        event = dict(body)
        event["hash"] = sha256_text(canonical(body))
        self.events.append(event)
        self._head = event["hash"]
        return event

    # -- 读 ------------------------------------------------------------------------
    @property
    def head(self) -> str:
        return self._head

    def of_type(self, type_: str) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]

    def count(self, type_: str) -> int:
        return sum(1 for e in self.events if e["type"] == type_)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def as_dict(self) -> dict:
        return {
            "events": self.events,
            "count": len(self.events),
            "head": self.head,
            "chain_valid": verify_chain(self.events),
        }


def verify_chain(events: Iterable[dict]) -> bool:
    """重算整条链；任一处 hash/prev 不匹配即 False。"""
    prev = GENESIS
    for i, event in enumerate(events, start=1):
        if event.get("seq") != i or event.get("prev") != prev:
            return False
        body = {k: event[k] for k in ("seq", "type", "payload", "utc", "prev")}
        if sha256_text(canonical(body)) != event.get("hash"):
            return False
        prev = event["hash"]
    return True


class NegativeLedger:
    """S-N 负知识轨：全席产物 → 可检索的负知识记录（带前向哈希）。"""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._head = GENESIS

    def record(
        self,
        *,
        seat_id: str,
        generation: int,
        clan_id: str,
        origin: str,
        gene: dict,
        hypothesis: str,
        parents: Iterable[str],
        verdict: str,
        failure_mode: str,
        score: float | None,
        metrics: dict,
        artifact_ref: str,
        do_not_repeat: str,
    ) -> dict:
        rid = f"S-N-{len(self.records) + 1:04d}"
        body = {
            "record_id": rid,
            "seat_id": seat_id,
            "generation": generation,
            "clan_id": clan_id,
            "origin": origin,
            "gene": dict(gene),
            "hypothesis": hypothesis,
            "parents": list(parents),
            "verdict": verdict,
            "failure_mode": failure_mode,
            "score": score,
            "metrics": dict(metrics),
            "artifact_ref": artifact_ref,
            "do_not_repeat": do_not_repeat,
            "prev": self._head,
        }
        record = dict(body)
        record["hash"] = sha256_text(canonical(body))
        self.records.append(record)
        self._head = record["hash"]
        return record

    @property
    def head(self) -> str:
        return self._head

    def by_failure_mode(self) -> dict:
        out: dict[str, int] = {}
        for rec in self.records:
            out[rec["failure_mode"]] = out.get(rec["failure_mode"], 0) + 1
        return dict(sorted(out.items()))

    def verify(self) -> bool:
        prev = GENESIS
        for rec in self.records:
            if rec.get("prev") != prev:
                return False
            body = {k: v for k, v in rec.items() if k != "hash"}
            if sha256_text(canonical(body)) != rec.get("hash"):
                return False
            prev = rec["hash"]
        return True

    def as_dict(self) -> dict:
        return {
            "count": len(self.records),
            "record_ids": [r["record_id"] for r in self.records],
            "by_failure_mode": self.by_failure_mode(),
            "head": self.head,
            "chain_valid": self.verify(),
            "records": self.records,
        }