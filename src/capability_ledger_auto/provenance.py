"""等级变更 provenance 链（哈希链、append-only、可复算）。

每次等级变更写一条 :class:`ProvenanceEntry`：
- 记录 prev/new 等级、触发规则 ID、机器判据快照、证据摘要（锚/反例 sha256）；
- ``entry_hash = sha256(canonical(entry 除 hash 外全字段 + prev_hash))``，
  形成哈希链；:meth:`ProvenanceChain.verify` 可从创世条目逐条复算，
  任一字段被改写即定位到首个坏条目（防人工改账）。

零领域语义：本模块只处理 capability_id 与数值判据。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

GENESIS_HASH = "0" * 64

# 变更方向
INITIAL = "INITIAL"
UPGRADE = "UPGRADE"
DOWNGRADE = "DOWNGRADE"
HOLD = "HOLD"
UNDECIDABLE_LOCK = "UNDECIDABLE_LOCK"  # 进入/停留不可判（fail-closed）
REJECTED = "REJECTED"  # 被拒的人工覆盖尝试（只留痕，不改状态）

TRANSITIONS = (INITIAL, UPGRADE, DOWNGRADE, HOLD, UNDECIDABLE_LOCK, REJECTED)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProvenanceEntry:
    """单条等级变更记录。"""

    seq: int
    timestamp: str
    capability_id: str
    prev_state: str | None
    new_state: str | None  # 被拒的人工覆盖为 None（状态未变）
    rule_id: str
    transition: str
    reasons: tuple[str, ...] = ()
    criteria: dict[str, Any] = field(default_factory=dict)
    evidence_digests: tuple[str, ...] = ()
    actor: str = "machine"
    outcome: str = "ACCEPTED"
    prev_hash: str = GENESIS_HASH
    entry_hash: str = ""

    def __post_init__(self) -> None:
        if self.transition not in TRANSITIONS:
            raise ValueError(f"未知变更方向: {self.transition!r}（应取 {TRANSITIONS}）")

    def body(self) -> dict[str, Any]:
        """参与哈希的字段（不含 entry_hash）。"""
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "capability_id": self.capability_id,
            "prev_state": self.prev_state,
            "new_state": self.new_state,
            "rule_id": self.rule_id,
            "transition": self.transition,
            "reasons": list(self.reasons),
            "criteria": self.criteria,
            "evidence_digests": list(self.evidence_digests),
            "actor": self.actor,
            "outcome": self.outcome,
            "prev_hash": self.prev_hash,
        }

    def compute_hash(self) -> str:
        return sha256_text(canonical_json(self.body()))

    def to_dict(self) -> dict[str, Any]:
        d = self.body()
        d["entry_hash"] = self.entry_hash
        return d


class ProvenanceChain:
    """append-only 哈希链。"""

    def __init__(self, clock: Callable[[], str] | None = None) -> None:
        self._entries: list[ProvenanceEntry] = []
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # ── 读 ────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[ProvenanceEntry, ...]:
        return tuple(self._entries)

    @property
    def head_hash(self) -> str:
        return self._entries[-1].entry_hash if self._entries else GENESIS_HASH

    def history(self, capability_id: str) -> list[ProvenanceEntry]:
        return [e for e in self._entries if e.capability_id == capability_id]

    # ── 写 ────────────────────────────────────────────────────
    def append(
        self,
        *,
        capability_id: str,
        prev_state: str | None,
        new_state: str | None,
        rule_id: str,
        transition: str,
        reasons: Iterable[str] = (),
        criteria: dict[str, Any] | None = None,
        evidence_digests: Iterable[str] = (),
        actor: str = "machine",
        outcome: str = "ACCEPTED",
    ) -> ProvenanceEntry:
        entry = ProvenanceEntry(
            seq=len(self._entries),
            timestamp=self._clock(),
            capability_id=capability_id,
            prev_state=prev_state,
            new_state=new_state,
            rule_id=rule_id,
            transition=transition,
            reasons=tuple(reasons),
            criteria=dict(criteria or {}),
            evidence_digests=tuple(evidence_digests),
            actor=actor,
            outcome=outcome,
            prev_hash=self.head_hash,
        )
        entry = replace(entry, entry_hash=entry.compute_hash())
        self._entries.append(entry)
        return entry

    # ── 校验 ──────────────────────────────────────────────────
    def verify(self) -> tuple[bool, int | None]:
        """逐条复算哈希链；返回 (是否完好, 首个坏条目 seq)。"""
        prev = GENESIS_HASH
        for idx, entry in enumerate(self._entries):
            if entry.seq != idx:
                return False, idx
            if entry.prev_hash != prev:
                return False, idx
            if entry.entry_hash != entry.compute_hash():
                return False, idx
            prev = entry.entry_hash
        return True, None

    def verify_entries(self, entries: Iterable[ProvenanceEntry]) -> tuple[bool, int | None]:
        """对任意条目序列做同样的链校验（用于外部读回的防篡改复核）。"""
        prev = GENESIS_HASH
        for idx, entry in enumerate(entries):
            if entry.seq != idx or entry.prev_hash != prev:
                return False, idx
            if entry.entry_hash != entry.compute_hash():
                return False, idx
            prev = entry.entry_hash
        return True, None

    # ── 序列化 ────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        ok, bad = self.verify()
        return {
            "entry_count": len(self._entries),
            "head_hash": self.head_hash,
            "chain_valid": ok,
            "first_bad_seq": bad,
            "entries": [e.to_dict() for e in self._entries],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProvenanceChain":
        chain = cls()
        for e in raw.get("entries", []):
            chain._entries.append(
                ProvenanceEntry(
                    seq=e["seq"],
                    timestamp=e["timestamp"],
                    capability_id=e["capability_id"],
                    prev_state=e.get("prev_state"),
                    new_state=e.get("new_state"),
                    rule_id=e["rule_id"],
                    transition=e["transition"],
                    reasons=tuple(e.get("reasons", [])),
                    criteria=dict(e.get("criteria", {})),
                    evidence_digests=tuple(e.get("evidence_digests", [])),
                    actor=e.get("actor", "machine"),
                    outcome=e.get("outcome", "ACCEPTED"),
                    prev_hash=e.get("prev_hash", GENESIS_HASH),
                    entry_hash=e.get("entry_hash", ""),
                )
            )
        return chain


__all__ = [
    "GENESIS_HASH",
    "INITIAL",
    "UPGRADE",
    "DOWNGRADE",
    "HOLD",
    "UNDECIDABLE_LOCK",
    "REJECTED",
    "TRANSITIONS",
    "ProvenanceEntry",
    "ProvenanceChain",
    "canonical_json",
    "sha256_text",
]