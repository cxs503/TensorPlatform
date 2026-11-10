"""推断引擎：证据图 → 成熟度标签（阶梯取最小值 + fail-closed 截断 + 矛盾封顶）。

算法（无隐式放行）::

    1. 只认 capability_key 精确匹配的证据节点，按 node_id 全序排序（可复算）。
    2. 逐环 r = 1..5：该环"支撑"当且仅当存在一个节点
       —— ``node.rank == r``、结构校验通过、``node.supports >= r``（夹具作用域会再封顶）。
       首个不支撑的环即**断点**，阶梯停在断点-1（fail-closed）。
    3. 矛盾证据封顶：台账中 stance=contradicts 且断言历史档位 ∈ {STUB, MOCK, WITHHELD,
       NOT_SUPPORTED} 的，按 ``ladder.LEGACY_CAP`` 封顶（MOCK → implemented）。
       封顶只在**低于**当前推断时才生效（取 min）。
    4. 自述等级（claimed_level）不参与抬级：只用来判"高报/一致/低报"。

输出 ``Inference`` 保留：逐环支撑表、断点原因、封顶来源、证据链（可执行命令）、
以及"高报"判定，供报告与台账落地直接引用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .evidence import EvidenceNode, evidence_chain
from .ladder import LADDER, REAL_MIN_RANK, legacy_cap, rank_of

#: 断点原因分类
BREAK_NO_NODE = "no_node_for_rung"
BREAK_INVALID = "node_present_but_fail_closed"
BREAK_SCOPE = "node_scope_capped"
BREAK_NONE = "ladder_complete"

CLAIM_UNBACKED = "unbacked_overclaim"
CLAIM_CONSISTENT = "consistent"
CLAIM_UNDERSTATED = "understated"
CLAIM_NONE = "no_claim"


@dataclass
class Inference:
    """一条能力的推断结果。"""

    capability_key: str
    rank: int
    level: str
    break_rung: Optional[str]
    break_reason: str
    rung_support: List[Dict[str, Any]] = field(default_factory=list)
    caps: List[Dict[str, Any]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    evidence_chain: List[Dict[str, Any]] = field(default_factory=list)
    claimed_level: Optional[str] = None
    claim_status: str = CLAIM_NONE
    node_count: int = 0
    executable_links: int = 0
    rejects: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def keeps_real(self) -> bool:
        return self.rank >= REAL_MIN_RANK

    @property
    def unlinked_real(self) -> bool:
        """被判为 REAL 却没有可执行证据链 —— 判据 (c) 的计数对象（应为 0）。

        只看**推断结果**（keeps_real）而不看自述：台账标签以推断为准，
        自述 REAL 但推断不足的行属于"下调"，不属"unlinked REAL"。
        """
        return self.keeps_real and self.executable_links == 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_key": self.capability_key,
            "inferred_level": self.level,
            "inferred_rank": self.rank,
            "claimed_level": self.claimed_level,
            "claim_status": self.claim_status,
            "break_rung": self.break_rung,
            "break_reason": self.break_reason,
            "rung_support": list(self.rung_support),
            "caps": list(self.caps),
            "reasons": list(self.reasons),
            "node_count": self.node_count,
            "executable_links": self.executable_links,
            "evidence_chain": list(self.evidence_chain),
            "keeps_real": self.keeps_real,
            "unlinked_real": self.unlinked_real,
        }


def _support_of(nodes: Sequence[EvidenceNode], rung_rank: int) -> Dict[str, Any]:
    """某环的支撑判定：返回 ``{supported, node_id, reason, candidates}``。"""
    level = LADDER[rung_rank]
    candidates = [n for n in nodes if n.rank == rung_rank]
    good = [n for n in candidates if n.valid and n.supports >= rung_rank]
    if good:
        pick = good[0]
        return {"rung": level, "rank": rung_rank, "supported": True,
                "node_id": pick.node_id, "reason": "ok", "candidates": len(candidates)}
    if not candidates:
        return {"rung": level, "rank": rung_rank, "supported": False,
                "node_id": None, "reason": BREAK_NO_NODE, "candidates": 0}
    # 有节点但无效 / 被作用域封顶
    capped = [n for n in candidates if n.valid and n.supports < rung_rank]
    if capped:
        return {"rung": level, "rank": rung_rank, "supported": False,
                "node_id": capped[0].node_id, "reason": BREAK_SCOPE,
                "detail": f"scope={capped[0].scope} 封顶 rank {capped[0].supports}",
                "candidates": len(candidates)}
    bad = candidates[0]
    return {"rung": level, "rank": rung_rank, "supported": False,
            "node_id": bad.node_id, "reason": BREAK_INVALID,
            "detail": "missing=" + ",".join(bad.missing[:4]) + ("|problems=" + ";".join(bad.problems[:2]) if bad.problems else ""),
            "candidates": len(candidates)}


def infer(
    capability_key: str,
    nodes: Iterable[Mapping[str, Any] | EvidenceNode],
    *,
    claimed_level: Optional[str] = None,
    contradictions: Optional[Sequence[str]] = None,
    rejects: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Inference:
    """对一条能力做推断。

    参数
    ----
    capability_key : 能力键（已由命名空间门校验；此处不做二次校验以免掩盖上游问题）
    nodes : 证据节点（mapping 或 ``EvidenceNode``）
    claimed_level : 台账自述等级（只用于判高报，**不抬级**）
    contradictions : 断言"更低档位"的矛盾证据（历史档位名列表，如 ``["MOCK"]``）
    rejects : 被命名空间门拒收的行（透传到结果里，便于审计）
    """
    parsed: List[EvidenceNode] = []
    for item in nodes:
        node = item if isinstance(item, EvidenceNode) else EvidenceNode.from_mapping(item)
        if node.capability_key == capability_key:
            parsed.append(node)
    parsed.sort(key=lambda n: n.node_id)

    rung_support: List[Dict[str, Any]] = []
    rank = 0
    break_rung: Optional[str] = None
    break_reason = BREAK_NONE
    for r in range(1, len(LADDER)):
        support = _support_of(parsed, r)
        rung_support.append(support)
        if not support["supported"]:
            break_rung = LADDER[r]
            break_reason = support["reason"]
            break
        rank = r

    reasons: List[str] = []
    caps: List[Dict[str, Any]] = []
    for legacy in contradictions or []:
        cap = legacy_cap(legacy)
        if cap is None:
            reasons.append(f"未登记的矛盾档位 {legacy!r} 忽略（不臆造惩罚）")
            continue
        caps.append({"kind": "contradiction", "asserted_legacy": legacy, "cap_rank": cap})
        if cap < rank:
            reasons.append(f"矛盾证据 {legacy} 封顶 rank {cap}（原 rank {rank}）")
            rank = cap
        else:
            reasons.append(f"矛盾证据 {legacy} 封面 rank {cap} 未低于当前 rank {rank}（不生效）")

    if break_rung:
        reasons.insert(0, f"阶梯在 {break_rung} 断（{break_reason}）→ 停在 {LADDER[rank]}")

    chain = evidence_chain(parsed, rank) if rank else []
    executable = [c for c in chain if c.get("rerun_cmd")]
    inference = Inference(
        capability_key=capability_key,
        rank=rank,
        level=LADDER[rank],
        break_rung=break_rung,
        break_reason=break_reason,
        rung_support=rung_support,
        caps=caps,
        reasons=reasons,
        evidence_chain=chain,
        claimed_level=(claimed_level.upper() if isinstance(claimed_level, str) else claimed_level),
        node_count=len(parsed),
        executable_links=len(executable),
        rejects=[dict(r) for r in (rejects or [])],
    )

    claim_rank = None
    if isinstance(claimed_level, str) and claimed_level.strip():
        from .ladder import LEGACY_EQUIVALENT
        claim_rank = LEGACY_EQUIVALENT.get(claimed_level.strip().upper())
    if claim_rank is None:
        inference.claim_status = CLAIM_NONE
    elif claim_rank > rank:
        inference.claim_status = CLAIM_UNBACKED
    elif claim_rank == rank:
        inference.claim_status = CLAIM_CONSISTENT
    else:
        inference.claim_status = CLAIM_UNDERSTATED
    return inference


def infer_batch(
    rows: Iterable[Mapping[str, Any]],
    nodes: Iterable[Mapping[str, Any] | EvidenceNode],
    *,
    key_field: str = "id",
) -> List[Inference]:
    """对一批台账行做推断（行内可带 ``contradictions`` 与 ``rejects``）。"""
    materialised = list(nodes)
    out: List[Inference] = []
    for row in rows:
        key = str(row.get(key_field, ""))
        out.append(
            infer(
                key,
                materialised,
                claimed_level=row.get("claimed_level") or row.get("claimed"),
                contradictions=[str(c) for c in (row.get("contradictions") or [])],
                rejects=row.get("rejects") or [],
            )
        )
    return out