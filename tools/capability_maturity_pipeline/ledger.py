"""成熟度台账：版本化状态 + 自动升级/降级/回滚（p156w2-n07）。

台账保存每个能力当前的证据等级以及**它是怎么来的**：

    level            当前等级
    decided_at       等级生效时间（UTC ISO8601）
    event_seq        确立该等级的事件在审计链上的 seq
    evidence_refs    该等级所依据的证据 sha256 集合（判定依据的来源）
    rule_fired       触发该判定的规则 ID（判定依据）
    thresholds_sha256 当时生效的裁定阈值指纹（不可事后改判）
    superseded_refs  已被后续证据推翻的基座证据指纹
    rollback_of      若本条由回滚产生，被回滚事件的 seq 列表（变更链）
    history          该能力的完整变更链（append-only）

判定动作由 ``apply_decision`` 机器决策：

================  ==========================================================
action            触发条件
================  ==========================================================
INIT              能力首次出现
UPGRADE           derived 等级 > 当前等级
DOWNGRADE         derived 等级 < 当前等级，且**非**矛盾驱动
ROLLBACK          derived 等级 < 当前等级，且台账基座证据被新证据推翻
REBASIS           等级不变，但基座证据集合变化（证据刷新）
（无事件 = HOLD）  等级与基座证据都不变 → 幂等，不写链
================  ==========================================================

回滚是「追加一条 ROLLBACK 事件」，不是抹历史；因此任何时刻都能重放出完整变更链。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..capability_ledger_maturity.adjudication_engine import (
    LEVEL_RANK, AdjudicationResult,
)
from .chain import AuditChain, ChainEvent, now_iso, sha256_of
from .contradiction import Supersession, supersession_payload

SCHEMA = "capability-maturity-ledger/1.0"
ANCHOR_SCHEMA = "capability-maturity-ledger-anchor/1.0"


def anchor_path(path: Path) -> Path:
    """锚文件路径：台账旁挂 ``<ledger>.head``。

    锚文件记录「台账上一次落盘时的链头 + 链长 + 台账内容 sha256」。
    它与台账分离存在（便于随提交签字/外部存证），因此**尾部截断**可被发现：
    只有链内 prev 链接时，删掉末尾事件后的链自身依然自洽。
    """
    path = Path(path)
    return path.with_suffix(path.suffix + ".head")


# ── 台账状态 ──────────────────────────────────────────────────────────────

@dataclass
class CapabilityState:
    """单个能力的台账状态。"""
    capability_id: str
    level: str
    decided_at: str
    event_seq: int
    evidence_refs: List[str] = field(default_factory=list)
    rule_fired: str = ""
    confidence: float = 0.0
    thresholds_sha256: str = ""
    adjudication_sha256: str = ""
    superseded_refs: List[str] = field(default_factory=list)
    rollback_of: List[int] = field(default_factory=list)
    evidence_basis: List[Dict[str, Any]] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level": self.level,
            "decided_at": self.decided_at,
            "event_seq": self.event_seq,
            "evidence_refs": list(self.evidence_refs),
            "rule_fired": self.rule_fired,
            "confidence": self.confidence,
            "thresholds_sha256": self.thresholds_sha256,
            "adjudication_sha256": self.adjudication_sha256,
            "superseded_refs": list(self.superseded_refs),
            "rollback_of": list(self.rollback_of),
            "evidence_basis": list(self.evidence_basis),
            "history": list(self.history),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CapabilityState":
        return CapabilityState(
            capability_id=str(d["capability_id"]),
            level=str(d["level"]),
            decided_at=str(d.get("decided_at", "")),
            event_seq=int(d.get("event_seq", -1)),
            evidence_refs=list(d.get("evidence_refs") or []),
            rule_fired=str(d.get("rule_fired", "")),
            confidence=float(d.get("confidence", 0.0)),
            thresholds_sha256=str(d.get("thresholds_sha256", "")),
            adjudication_sha256=str(d.get("adjudication_sha256", "")),
            superseded_refs=list(d.get("superseded_refs") or []),
            rollback_of=[int(x) for x in (d.get("rollback_of") or [])],
            evidence_basis=list(d.get("evidence_basis") or []),
            history=list(d.get("history") or []),
        )


@dataclass(frozen=True)
class Decision:
    """一次台账决策（对应审计链上一条事件）。"""
    capability_id: str
    action: str
    from_level: Optional[str]
    to_level: str
    rule_fired: str
    reason: str
    evidence_refs: List[str]
    superseded_refs: List[str]
    rollback_of: List[int]
    event_seq: int
    event_sha256: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "action": self.action,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "rule_fired": self.rule_fired,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "superseded_refs": list(self.superseded_refs),
            "rollback_of": list(self.rollback_of),
            "event_seq": self.event_seq,
            "event_sha256": self.event_sha256,
            "timestamp": self.timestamp,
        }


# ── 台账 ──────────────────────────────────────────────────────────────────

class MaturityLedger:
    """能力成熟度台账（内存状态 + JSON 持久化 + 哈希审计链）。

    Parameters
    ----------
    chain : AuditChain, optional
        既有审计链（从台账 JSON 载入）。
    states : dict, optional
        既有 {capability_id: CapabilityState}。
    clock : callable, optional
        时间源；测试注入固定时钟以获得确定性指纹。
    """

    def __init__(self, chain: Optional[AuditChain] = None,
                 states: Optional[Dict[str, CapabilityState]] = None,
                 clock: Callable[[], str] = now_iso,
                 thresholds_sha256: str = "",
                 contradiction_policy_sha256: str = ""):
        self._clock = clock
        self.chain = chain or AuditChain(clock=clock)
        self.states: Dict[str, CapabilityState] = dict(states or {})
        self.thresholds_sha256 = thresholds_sha256
        self.contradiction_policy_sha256 = contradiction_policy_sha256
        self._generation = self._max_generation()
        self._anchor: Optional[Dict[str, Any]] = None

    # ── 基本属性 ────────────────────────────────────────────────────

    @property
    def generation(self) -> int:
        """世代号 = 已发生的状态变更次数（HOLD 不计数）。"""
        return self._generation

    def _max_generation(self) -> int:
        g = 0
        for st in self.states.values():
            for h in st.history:
                g = max(g, int(h.get("generation", 0)))
        return g

    def level_of(self, capability_id: str) -> Optional[str]:
        st = self.states.get(capability_id)
        return st.level if st else None

    def levels(self) -> Dict[str, str]:
        return {k: v.level for k, v in sorted(self.states.items())}

    # ── 决策 ────────────────────────────────────────────────────────

    def apply_decision(self, result: AdjudicationResult, *,
                       incoming_refs: Sequence[str],
                       supersessions: Sequence[Supersession] = (),
                       basis: Sequence[Dict[str, Any]] = (),
                       timestamp: Optional[str] = None,
                       thresholds_sha256: Optional[str] = None) -> Optional[Decision]:
        """把一次裁定结果落到台账上（机器决策 + 追加审计事件）。

        Returns
        -------
        Decision or None
            ``None`` 表示 HOLD（无变化，不写链 → 幂等）。
        """
        ts = timestamp or self._clock()
        cap = result.capability_id
        cur = self.states.get(cap)
        derived = result.derived_level
        refs = sorted(set(incoming_refs))
        thr = thresholds_sha256 if thresholds_sha256 is not None else (
            cur.thresholds_sha256 if cur else "")
        thr = thr or result.thresholds_sha256

        if cur is None:
            action, reason = "INIT", "能力首次进入台账"
            rollback_of: List[int] = []
            superseded: List[str] = []
        else:
            cur_rank = LEVEL_RANK.get(cur.level, 0)
            new_rank = LEVEL_RANK.get(derived, 0)
            refuted = [s for s in supersessions
                       if s.refuted_sha256 in set(cur.evidence_refs)]
            if new_rank > cur_rank:
                action = "UPGRADE"
                reason = f"证据支持更高等级：{cur.level} → {derived}（{result.rule_fired}）"
                rollback_of, superseded = [], []
            elif new_rank < cur_rank:
                if refuted:
                    action = "ROLLBACK"
                    chain_seqs = self._trust_chain(cap)
                    rollback_of = chain_seqs
                    superseded = sorted({s.refuted_sha256 for s in refuted})
                    reason = (
                        f"先前判定 {cur.level} 的基座证据被后续证据推翻"
                        f"（{len(refuted)} 条，最大偏离 "
                        f"{max(s.deviation_pct for s in refuted):.3f}%），"
                        f"自动回滚至 {derived}"
                    )
                else:
                    action = "DOWNGRADE"
                    reason = f"新证据支持更低等级：{cur.level} → {derived}（{result.rule_fired}）"
                    rollback_of, superseded = [], []
            else:
                if refs and refs != sorted(set(cur.evidence_refs)):
                    action = "REBASIS"
                    reason = f"等级不变（{derived}），基座证据刷新为 {len(refs)} 条"
                    rollback_of = []
                else:
                    return None  # HOLD：等级与基座都不变
                superseded = sorted({s.refuted_sha256 for s in supersessions
                                     if s.refuted_sha256 in set(cur.evidence_refs)})

        self._generation += 1
        payload: Dict[str, Any] = {
            "action": action,
            "from_level": None if cur is None else cur.level,
            "to_level": derived,
            "rule_fired": result.rule_fired,
            "reason": reason,
            "evidence_refs": refs,
            "evidence_sha256": result.evidence_sha256,
            "evidence_count": result.evidence_count,
            "max_error_pct": result.max_error_pct,
            "thresholds_sha256": thr,
            "adjudication_sha256": _adjudication_sha256(result),
            "confidence": result.confidence,
            "rollback_of": rollback_of,
            "generation": self._generation,
        }
        payload.update(supersession_payload(supersessions)
                       if supersessions else {"superseded_refs": [],
                                              "superseded_by": [],
                                              "supersession_count": 0,
                                              "max_deviation_pct": 0.0})
        if superseded:
            payload["superseded_refs"] = superseded
        # 冲突概览（同批证据不自洽时留痕）
        ev = self.chain.append(action, cap, payload, timestamp=ts)

        history_entry = {
            "generation": self._generation,
            "action": action,
            "from_level": payload["from_level"],
            "to_level": derived,
            "timestamp": ts,
            "event_seq": ev.seq,
            "event_sha256": ev.sha256,
            "evidence_refs": refs,
            "rule_fired": result.rule_fired,
            "superseded_refs": superseded,
            "rollback_of": rollback_of,
            # 历史只留基座指纹（完整基座只存当前状态，避免台账体积随历史线性膨胀）
            "basis_sha256": sha256_of(list(basis)),
        }
        if cur is None:
            self.states[cap] = CapabilityState(
                capability_id=cap, level=derived, decided_at=ts, event_seq=ev.seq,
                evidence_refs=refs, rule_fired=result.rule_fired,
                confidence=result.confidence, thresholds_sha256=thr,
                adjudication_sha256=payload["adjudication_sha256"],
                superseded_refs=superseded, rollback_of=rollback_of,
                evidence_basis=list(basis), history=[history_entry],
            )
        else:
            cur.level = derived
            cur.decided_at = ts
            cur.event_seq = ev.seq
            cur.evidence_refs = refs
            cur.rule_fired = result.rule_fired
            cur.confidence = result.confidence
            cur.thresholds_sha256 = thr
            cur.adjudication_sha256 = payload["adjudication_sha256"]
            cur.superseded_refs = sorted(set(cur.superseded_refs) | set(superseded))
            cur.rollback_of = rollback_of
            cur.evidence_basis = list(basis)
            cur.history.append(history_entry)

        return Decision(
            capability_id=cap, action=action, from_level=payload["from_level"],
            to_level=derived, rule_fired=result.rule_fired, reason=reason,
            evidence_refs=refs, superseded_refs=superseded,
            rollback_of=rollback_of, event_seq=ev.seq, event_sha256=ev.sha256,
            timestamp=ts,
        )

    def _trust_chain(self, capability_id: str) -> List[int]:
        """回到「当前等级首次被确立」的那条事件的 seq（回滚锚点）。"""
        st = self.states.get(capability_id)
        if st is None:
            return []
        target = st.level
        seqs: List[int] = []
        for h in st.history:
            if h.get("to_level") == target and h.get("action") in (
                    "INIT", "UPGRADE", "ROLLBACK", "DOWNGRADE"):
                seqs.append(int(h["event_seq"]))
        return seqs or [st.event_seq]

    # ── 人工/外部世代回滚 ───────────────────────────────────────────

    def rollback_to_generation(self, target_generation: int,
                               timestamp: Optional[str] = None) -> List[Decision]:
        """把台账回放到某一世代（追加 MANUAL_ROLLBACK 事件，不删历史）。"""
        if target_generation < 0 or target_generation > self._generation:
            raise ValueError(f"target generation {target_generation} out of range "
                             f"[0, {self._generation}]")
        decisions: List[Decision] = []
        for cap in sorted(self.states):
            st = self.states[cap]
            target_hist = None
            for h in st.history:
                if int(h.get("generation", 0)) <= target_generation:
                    target_hist = h
            if target_hist is None:
                continue
            if target_hist.get("to_level") == st.level and \
                    st.event_seq == int(target_hist["event_seq"]):
                continue
            ts = timestamp or self._clock()
            self._generation += 1
            payload = {
                "action": "MANUAL_ROLLBACK",
                "from_level": st.level,
                "to_level": target_hist["to_level"],
                "rule_fired": "MANUAL_ROLLBACK_TO_GENERATION",
                "reason": f"回放到世代 {target_generation}",
                "evidence_refs": list(target_hist.get("evidence_refs") or []),
                "evidence_sha256": target_hist.get("event_sha256", ""),
                "evidence_count": len(target_hist.get("evidence_refs") or []),
                "max_error_pct": None,
                "thresholds_sha256": st.thresholds_sha256,
                "adjudication_sha256": target_hist.get("event_sha256", ""),
                "confidence": 0.0,
                "rollback_of": [int(target_hist["event_seq"])],
                "generation": self._generation,
                "superseded_refs": [],
                "superseded_by": [],
                "supersession_count": 0,
                "max_deviation_pct": 0.0,
            }
            ev = self.chain.append("MANUAL_ROLLBACK", cap, payload, timestamp=ts)
            st.level = target_hist["to_level"]
            st.decided_at = ts
            st.event_seq = ev.seq
            st.evidence_refs = list(target_hist.get("evidence_refs") or [])
            st.rollback_of = [int(target_hist["event_seq"])]
            st.history.append({
                "generation": self._generation, "action": "MANUAL_ROLLBACK",
                "from_level": payload["from_level"], "to_level": payload["to_level"],
                "timestamp": ts, "event_seq": ev.seq, "event_sha256": ev.sha256,
                "evidence_refs": st.evidence_refs,
                "rule_fired": "MANUAL_ROLLBACK_TO_GENERATION",
                "superseded_refs": [], "rollback_of": st.rollback_of,
                "basis_sha256": sha256_of(list(st.evidence_basis)),
            })
            decisions.append(Decision(
                capability_id=cap, action="MANUAL_ROLLBACK",
                from_level=payload["from_level"], to_level=payload["to_level"],
                rule_fired="MANUAL_ROLLBACK_TO_GENERATION",
                reason=payload["reason"], evidence_refs=st.evidence_refs,
                superseded_refs=[], rollback_of=st.rollback_of,
                event_seq=ev.seq, event_sha256=ev.sha256, timestamp=ts))
        return decisions

    # ── 校验 ────────────────────────────────────────────────────────

    def anchor(self) -> Dict[str, Any]:
        """当前台账锚（链头 + 链长 + 世代）。"""
        return {"schema": ANCHOR_SCHEMA, "chain_head_sha256": self.chain.head,
                "chain_length": len(self.chain), "generation": self._generation}

    def verify(self, anchor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """链完整性（含锚点）+ 台账↔链一致性校验。"""
        anc = anchor if anchor is not None else self._anchor
        chain_v = self.chain.verify(
            expected_head=(anc or {}).get("chain_head_sha256"),
            expected_length=(anc or {}).get("chain_length"))
        issues: List[str] = []
        by_seq = {e.seq: e for e in self.chain}
        for cap, st in sorted(self.states.items()):
            ev = by_seq.get(st.event_seq)
            if ev is None:
                issues.append(f"{cap}: event_seq {st.event_seq} missing from chain")
                continue
            if ev.capability_id != cap:
                issues.append(f"{cap}: event_seq {st.event_seq} belongs to "
                              f"{ev.capability_id}")
            if ev.payload.get("to_level") != st.level:
                issues.append(f"{cap}: ledger level {st.level} != chain to_level "
                              f"{ev.payload.get('to_level')}")
            if ev.payload.get("generation") != st.history[-1]["generation"]:
                issues.append(f"{cap}: generation mismatch")
        return {
            "ok": chain_v.ok and not issues,
            "chain": chain_v.to_dict(),
            "anchor": dict(anc) if anc else None,
            "capabilities": len(self.states),
            "generation": self._generation,
            "issues": issues,
        }

    # ── 持久化 ──────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "generation": self._generation,
            "thresholds_sha256": self.thresholds_sha256,
            "contradiction_policy_sha256": self.contradiction_policy_sha256,
            "chain_head_sha256": self.chain.head,
            "states": {k: v.to_dict() for k, v in sorted(self.states.items())},
            "chain": self.chain.to_list(),
        }

    def save(self, path: Path) -> str:
        """原子写盘（tmp + rename），返回台账内容 sha256。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(blob)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        import hashlib
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        anchor = self.anchor()
        anchor["ledger_sha256"] = digest
        path.with_suffix(path.suffix + ".head").write_text(
            json.dumps(anchor, indent=2, sort_keys=True), encoding="utf-8")
        self._anchor = anchor
        return digest

    @classmethod
    def load(cls, path: Path, clock: Callable[[], str] = now_iso) -> "MaturityLedger":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != SCHEMA:
            raise ValueError(f"unsupported ledger schema {data.get('schema')!r}")
        chain = AuditChain(events=data.get("chain") or [], clock=clock)
        states = {k: CapabilityState.from_dict(v)
                  for k, v in (data.get("states") or {}).items()}
        ledger = cls(chain=chain, states=states, clock=clock,
                     thresholds_sha256=data.get("thresholds_sha256", ""),
                     contradiction_policy_sha256=data.get(
                         "contradiction_policy_sha256", ""))
        ledger._generation = int(data.get("generation", ledger._generation))
        anc_file = anchor_path(Path(path))
        if anc_file.exists():
            try:
                anc = json.loads(anc_file.read_text(encoding="utf-8"))
                if anc.get("schema") == ANCHOR_SCHEMA:
                    ledger._anchor = anc
            except json.JSONDecodeError:
                ledger._anchor = None
        return ledger


def _adjudication_sha256(result: AdjudicationResult) -> str:
    """裁定结果指纹（判定依据可追溯到具体一次裁定）。"""
    import hashlib
    from .chain import canonical_bytes
    return hashlib.sha256(canonical_bytes({
        "capability_id": result.capability_id,
        "derived_level": result.derived_level,
        "rule_fired": result.rule_fired,
        "evidence_sha256": result.evidence_sha256,
        "thresholds_sha256": result.thresholds_sha256,
        "max_error_pct": result.max_error_pct,
        "evidence_count": result.evidence_count,
        "passed_count": result.passed_count,
    })).hexdigest()