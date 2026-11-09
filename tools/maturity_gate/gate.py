"""战役成熟化门（maturity gate）：节点通过率 → 战役级裁定。

裁定语义（三值，禁止二值化）

* ``PASS``     —— 每个节点都**已交付**证据，且 ``pass_ratio >= criteria_pass_threshold``。
* ``FAIL``     —— 至少一个已交付节点的 ``pass_ratio`` 低于绿线，或出现假通过嫌疑
                  （``claim_mismatch``：上游自述 pass 与本引擎复算相反）。
                  失败不可被「别的节点很绿」抵消，也不可被覆盖。
* ``WITHHELD`` —— 无低绿线节点，但仍有节点零证据（在飞/未交付）。**不知道 ≠ 通过**：
                  缺证据只让整战役停在 WITHHELD，绝不会抬成 PASS。

两条实现路径共享同一套判定语义：

* :func:`evaluate_campaign` —— 磁盘证据（CLI / 战役交付审计）；
* :func:`evaluate_campaign_inline` —— 内存证据（API 无状态裁判，零磁盘依赖）。

确定性：输出 ``sort_keys=True`` + 固定缩进；``input_digest`` = 规格规范化 +
全部被读证据文档 sha256 的 sha256。同一输入连跑逐字节相同，不掺 wall clock。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .evidence import (FAIL, PASS, WITHHELD, EvidenceResolver, evaluate_node,
                       payload_digest, summarize_node)
from .spec import CampaignSpec, NodeSpec, SpecError, parse_spec

GATE_SCHEMA_VERSION = "1.0"

#: 节点证据成熟度标签（台账用）
LEVEL_REAL = "REAL"
LEVEL_PARTIAL = "PARTIAL"
LEVEL_WITHHELD = "WITHHELD"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class InlineResolver(EvidenceResolver):
    """内存证据解析器：``docs`` 是 ``{doc: payload}``，供 API 无状态裁判使用。

    sha256 按 ``canonical_json(payload)`` 计算——调用方与裁判用同一定义即可互验钉桩。
    """

    def __init__(self, docs: Mapping[str, Any]) -> None:
        super().__init__()
        self.docs: Dict[str, Any] = dict(docs)
        self._digests = {k: payload_digest(v) for k, v in self.docs.items()}

    def locate(self, node: NodeSpec, doc: str) -> Optional[Path]:  # type: ignore[override]
        return Path(doc) if doc in self.docs else None

    def digest(self, node: NodeSpec, doc: str) -> Optional[str]:  # type: ignore[override]
        return self._digests.get(doc)

    def payload(self, node: NodeSpec, doc: str) -> Any:  # type: ignore[override]
        return self.docs[doc]

    def resolve_existing_root(self, node: NodeSpec) -> Optional[Path]:  # type: ignore[override]
        return None


def _spec_digest_view(spec: CampaignSpec) -> Dict[str, Any]:
    return {
        "spec_id": spec.spec_id,
        "schema_version": spec.schema_version,
        "criteria_pass_threshold": spec.criteria_pass_threshold,
        "nodes": [
            {"node_key": n.node_key, "repo": n.repo, "capability": n.capability,
             "criteria": [
                 {"criterion_id": c.criterion_id, "statement": c.statement,
                  "checks": [{"doc": k.doc, "pointer": k.pointer,
                              "comparator": k.comparator, "threshold": k.threshold,
                              "sha256": k.sha256} for k in c.checks]}
                 for c in n.criteria]}
            for n in spec.nodes
        ],
    }


def _digest_inputs(spec: CampaignSpec, evidence_digests: Sequence[Tuple[str, str]]) -> str:
    payload = {"spec": _spec_digest_view(spec), "evidence": sorted(evidence_digests)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def campaign_verdict(summaries: Sequence[Mapping[str, Any]], threshold: float,
                     claim_mismatch_total: int) -> Dict[str, Any]:
    """战役级三值裁定（纯函数，可单测）。"""
    total = len(summaries)
    delivered = [s for s in summaries if s["evaluable_ratio"] > 0.0]
    absent = [s["node_key"] for s in summaries if s["evaluable_ratio"] == 0.0]
    green = [s["node_key"] for s in delivered if s["pass_ratio"] >= threshold]
    red = [s for s in delivered if s["pass_ratio"] < threshold]
    ratios = [s["pass_ratio"] for s in delivered]
    criterion_a = bool(total) and not absent and len(green) == total
    if red or claim_mismatch_total:
        verdict = FAIL
    elif criterion_a:
        verdict = PASS
    else:
        verdict = WITHHELD
    return {
        "nodes_total": total,
        "nodes_delivered": len(delivered),
        "nodes_absent": absent,
        "nodes_green": green,
        "nodes_red": [s["node_key"] for s in red],
        "min_pass_ratio": min(ratios) if ratios else None,
        "mean_pass_ratio": (sum(ratios) / len(ratios)) if ratios else None,
        "criteria_pass_threshold": threshold,
        "criterion_a_all_nodes_ge_threshold": criterion_a,
        "claim_mismatch_total": claim_mismatch_total,
        "verdict": verdict,
        "blocking": ([{"node_key": k, "reason": "no_evaluable_evidence"} for k in absent]
                     + [{"node_key": s["node_key"], "reason": "pass_ratio_below_threshold",
                         "pass_ratio": s["pass_ratio"]} for s in red]
                     + [{"node_key": s["node_key"], "reason": "claim_mismatch",
                         "criteria": s["claim_mismatches"]}
                        for s in summaries if s["claim_mismatches"]]),
    }


def _assemble(spec: CampaignSpec, nodes_out: List[Dict[str, Any]],
              summaries: List[Dict[str, Any]], digests: List[Tuple[str, str]]) -> Dict[str, Any]:
    mismatch_total = sum(len(s["claim_mismatches"]) for s in summaries)
    return {
        "gate_schema_version": GATE_SCHEMA_VERSION,
        "spec_id": spec.spec_id,
        "spec_title": spec.title,
        "input_digest": _digest_inputs(spec, digests),
        "nodes": nodes_out,
        "node_summaries": summaries,
        "campaign": campaign_verdict(summaries, spec.criteria_pass_threshold, mismatch_total),
    }


def _node_payload(node: NodeSpec, results, root: Optional[Path]) -> Dict[str, Any]:
    return {
        "node_key": node.node_key,
        "repo": node.repo,
        "mission_id": node.mission_id,
        "layer": node.layer,
        "risk": node.risk,
        "worktree": str(root) if root else None,
        "criteria": [r.to_dict() for r in results],
    }


def evaluate_campaign(spec: CampaignSpec, resolver: EvidenceResolver) -> Dict[str, Any]:
    """磁盘证据路径：逐节点判定，返回机器可裁判定体（未含台账/契约/报告三项）。"""
    nodes_out: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    digests: List[Tuple[str, str]] = []
    for node in spec.nodes:
        results, summary = evaluate_node(node, resolver)
        nodes_out.append(_node_payload(node, results, resolver.resolve_existing_root(node)))
        summaries.append(summary)
        for crit in results:
            for chk in crit.checks:
                if chk.evidence_sha256:
                    digests.append((chk.evidence_doc or "", chk.evidence_sha256))
    return _assemble(spec, nodes_out, summaries, digests)


def evaluate_campaign_inline(spec_raw: Any, docs: Mapping[str, Any]) -> Dict[str, Any]:
    """无状态裁判：规格 + 内存证据文档 → 判定体（API 端点用，零磁盘依赖）。"""
    spec = parse_spec(spec_raw)
    resolver = InlineResolver(docs)
    nodes_out: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    digests: List[Tuple[str, str]] = []
    for node in spec.nodes:
        results, summary = evaluate_node(node, resolver)
        nodes_out.append(_node_payload(node, results, None))
        summaries.append(summary)
        for crit in results:
            for chk in crit.checks:
                if chk.evidence_sha256:
                    digests.append((chk.evidence_doc or "", chk.evidence_sha256))
    return _assemble(spec, nodes_out, summaries, digests)


def dump_json(payload: Mapping[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


__all__ = ["evaluate_campaign", "evaluate_campaign_inline", "campaign_verdict", "dump_json",
           "canonical_json", "InlineResolver", "LEVEL_REAL", "LEVEL_PARTIAL",
           "LEVEL_WITHHELD", "SpecError", "PASS", "FAIL", "WITHHELD"]