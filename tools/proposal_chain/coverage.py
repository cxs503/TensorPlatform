"""播种覆盖率账本：提案主题 → 战役节点落点 → 缺口账本逐项比对。

三段
----
1. **主题提取**（:func:`topic_vocabulary`）：在提案语料上算 TF-IDF 全局权重
   （``term_weight = mean_tfidf * log(1+df)``），取前 ``max_topics`` 且 ``df ≥ min_topic_df`` 的词项。
   纯统计，无域词表；``physics_domain`` 字段若存在则额外作为**结构化域标签**入账。
2. **覆盖矩阵**（:func:`coverage_matrix`）：
   * ``domain_matrix``：域标签（提案侧与节点侧 ``physics_domain`` 的并集）×
     {proposal_count, node_count, covered}；
   * ``topic_matrix``：主题词项 × 各仓节点命中数（节点文本 cosine ≥ ``match_threshold``）。
3. **账本比对**（:func:`ledger_coverage`）：每个缺口账本条目当查询词，找最佳节点/提案匹配，
   三态分类 ``covered_by_node`` / ``proposal_pending`` / ``uncovered``，
   再与条目的 ``declared_status`` 逐项比对得 ``consistent`` / ``disagreement``。

漂移语义（重要）
----------------
``declared_status == "open"`` 的条目若被判 ``covered_by_node``，**不自动改账本**：
只记成 ``doc_stale_candidate``（文档可能滞后，也可能只是词面撞车）。反向
（``planned`` 条目却 ``uncovered``）记 ``plan_not_seeded``。两者都进 disagreement 清单，
由人工/后续节点裁决——机器只负责把差异摆到台面上。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .dedup import Doc
from .ledger import LedgerItem
from .text import TfidfSpace, build_space, tokens


@dataclass(frozen=True)
class TopicHit:
    topic: str
    df: int
    weight: float

    def as_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "df": self.df, "weight": round(self.weight, 6)}


def topic_vocabulary(
    docs: Sequence[Doc], cfg: Mapping[str, Any]
) -> list[TopicHit]:
    """提案语料上的统计主题词表（df 下限 + 权重上限截断，确定性排序）。"""
    cov = dict(cfg.get("coverage", {}) or {})
    min_df = int(cov.get("min_topic_df", 3))
    max_topics = int(cov.get("max_topics", 40))
    space = build_space([d.text for d in docs], cfg)
    if not space.vocab:
        return []
    df = {term: 0 for term in space.vocab}
    for vec in space.vectors:
        for idx in vec:
            df[space.inverse_vocab[idx]] += 1
    weights: dict[str, float] = {t: 0.0 for t in space.vocab}
    for vec in space.vectors:
        for idx, w in vec.items():
            weights[space.inverse_vocab[idx]] += w
    n = max(1, len(docs))
    hits = [
        TopicHit(topic=t, df=df[t], weight=(weights[t] / n) * math.log(1.0 + df[t]))
        for t in space.vocab
        if df[t] >= min_df and len(t) >= 2 and not (len(t) <= 2 and t.isascii())
    ]
    hits.sort(key=lambda h: (-h.weight, h.topic))
    return hits[:max_topics]


def normalize_domain(label: str, cfg: Mapping[str, Any]) -> str:
    """域标签归一：按配置的分隔符族取首段（数据驱动，无域词）。"""
    cov = dict(cfg.get("coverage", {}) or {})
    seps = str(cov.get("domain_split_chars", "/（(；;·,，"))
    text = str(label or "").strip()
    cut = len(text)
    for ch in seps:
        idx = text.find(ch)
        if 0 <= idx < cut:
            cut = idx
    head = text[:cut].strip()
    return head or text


def _containment(query: Mapping[int, float], doc: Mapping[int, float]) -> float:
    """查询覆盖率：查询词项权重被文档覆盖的比例（短 query vs 长 doc 时不因长度被稀释）。"""
    total = sum(query.values())
    if total <= 0:
        return 0.0
    covered = sum(min(w, doc.get(idx, 0.0)) for idx, w in query.items())
    return max(0.0, min(1.0, covered / total))


def _max_containment(
    query: str, space: TfidfSpace, docs: Sequence[Doc]
) -> tuple[float, str]:
    """查询覆盖率最大者（短 query 不被长文档稀释；并列取 id 字典序小者）。"""
    qv = space.vectorize(query)
    if not qv:
        return 0.0, ""
    best_s, best_id = 0.0, ""
    for k, doc in enumerate(docs):
        s = _containment(qv, space.vectors[k])
        if s > best_s + 1e-12 or (abs(s - best_s) <= 1e-12 and s > 0 and doc.id < best_id):
            best_s, best_id = s, doc.id
    return best_s, best_id


def _gap_id_matches(gap_ids: Sequence[str], docs: Sequence[Doc]) -> tuple[str, str]:
    """账本里的 GAP-* 标识符若被某节点文本显式引用，则构成最硬的“计划→落点”证据。"""
    for gap_id in gap_ids:
        needle = gap_id.casefold()
        for doc in docs:
            if needle in doc.text.casefold():
                return doc.id, gap_id
    return "", ""


def coverage_matrix(
    proposals: Sequence[Doc],
    nodes: Sequence[Doc],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    """域标签矩阵 + 主题×仓矩阵。空输入 → 空矩阵（不崩）。"""
    cov = dict(cfg.get("coverage", {}) or {})
    match_threshold = float(cov.get("match_threshold", 0.35))
    raw_domains = {
        d.physics_domain for d in proposals if d.physics_domain
    } | {d.physics_domain for d in nodes if d.physics_domain}
    norm = {raw: normalize_domain(raw, cfg) for raw in raw_domains}
    domains = sorted(set(norm.values()))
    node_space = build_space([d.text for d in nodes], cfg)
    proposal_space = build_space([d.text for d in proposals], cfg)

    rows: list[dict[str, Any]] = []
    for dom in domains:
        p_docs = [d for d in proposals if d.physics_domain and norm[d.physics_domain] == dom]
        n_docs = [d for d in nodes if d.physics_domain and norm[d.physics_domain] == dom]
        rows.append(
            {
                "domain": dom,
                "proposal_count": len(p_docs),
                "node_count": len(n_docs),
                "covered": bool(p_docs and n_docs),
                "state": "covered_by_node"
                if n_docs
                else ("proposal_pending" if p_docs else "uncovered"),
            }
        )

    topics = topic_vocabulary(proposals, cfg)
    repos = sorted({d.repo for d in nodes if d.repo})
    node_tokens = [set(tokens(d.text, markup_tokens=())) for d in nodes]
    topic_rows: list[dict[str, Any]] = []
    for hit in topics:
        per_repo: dict[str, int] = {}
        total = 0
        for k, node in enumerate(nodes):
            if hit.topic in node_tokens[k]:  # 词项在位（term presence），不受文档长度稀释
                total += 1
                if node.repo:
                    per_repo[node.repo] = per_repo.get(node.repo, 0) + 1
        topic_rows.append(
            {
                "topic": hit.topic,
                "df": hit.df,
                "weight": round(hit.weight, 6),
                "node_hits": total,
                "repo_hits": len(per_repo),
                "per_repo": dict(sorted(per_repo.items())),
            }
        )

    # 粗粒度域族矩阵：归一首段再取首词，缓解「一条提案一个自由文本标签」的碎片化
    def family(label: str) -> str:
        head = normalize_domain(label, cfg)
        parts = head.split()
        return parts[0] if parts else head

    fam_rows: dict[str, dict[str, Any]] = {}
    for pool, key in ((proposals, "proposal_count"), (nodes, "node_count")):
        for doc in pool:
            if not doc.physics_domain:
                continue
            fam = family(doc.physics_domain)
            row = fam_rows.setdefault(fam, {"domain_family": fam, "proposal_count": 0, "node_count": 0})
            row[key] += 1
    family_matrix = []
    for fam in sorted(fam_rows):
        row = fam_rows[fam]
        row["state"] = (
            "covered_by_node" if row["node_count"]
            else ("proposal_pending" if row["proposal_count"] else "uncovered")
        )
        family_matrix.append(row)

    return {
        "domain_matrix": rows,
        "domain_family_matrix": family_matrix,
        "topic_matrix": topic_rows,
        "repos": repos,
        "totals": {
            "domains": len(domains),
            "raw_domain_labels": len(raw_domains),
            "domains_covered": sum(1 for r in rows if r["covered"]),
            "domain_families": len(family_matrix),
            "domain_families_covered": sum(1 for r in family_matrix if r["node_count"]),
            "topics": len(topic_rows),
            "topics_with_node_hits": sum(1 for r in topic_rows if r["node_hits"] > 0),
            "proposals": len(proposals),
            "nodes": len(nodes),
            "proposals_with_domain": sum(1 for d in proposals if d.physics_domain),
            "nodes_with_domain": sum(1 for d in nodes if d.physics_domain),
            "node_vocab": len(node_space.vocab),
            "proposal_vocab": len(proposal_space.vocab),
        },
    }


@dataclass
class LedgerRow:
    item_id: str
    section: str
    line_no: int
    declared_status: str
    state: str
    node_sim: float
    node_id: str
    proposal_sim: float
    proposal_id: str
    consistent: bool
    note: str
    text: str
    gap_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "section": self.section,
            "line_no": self.line_no,
            "declared_status": self.declared_status,
            "state": self.state,
            "node_similarity": round(self.node_sim, 6),
            "node_id": self.node_id,
            "proposal_similarity": round(self.proposal_sim, 6),
            "proposal_id": self.proposal_id,
            "consistent": self.consistent,
            "note": self.note,
            "gap_ids": list(self.gap_ids),
            "text": self.text,
        }


@dataclass
class LedgerCoverage:
    rows: list[LedgerRow] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"stats": self.stats, "rows": [r.as_dict() for r in self.rows]}

    @property
    def disagreements(self) -> list[LedgerRow]:
        return [r for r in self.rows if not r.consistent]


def ledger_coverage(
    items: Sequence[LedgerItem],
    proposals: Sequence[Doc],
    nodes: Sequence[Doc],
    cfg: Mapping[str, Any],
) -> LedgerCoverage:
    """逐条比对：账本条目 → 最佳节点/提案匹配 → 与声明状态一致性。"""
    cov = dict(cfg.get("coverage", {}) or {})
    threshold = float(cov.get("ledger_match_threshold", 0.30))
    states = dict(cov.get("states", {}) or {})
    s_covered = str(states.get("covered_by_node", "covered_by_node"))
    s_pending = str(states.get("proposal_pending", "proposal_pending"))
    s_uncovered = str(states.get("uncovered", "uncovered"))

    node_space = build_space([d.text for d in nodes], cfg)
    proposal_space = build_space([d.text for d in proposals], cfg)

    rows: list[LedgerRow] = []
    for item in items:
        node_sim, node_id = _max_containment(item.text, node_space, nodes)
        prop_sim, prop_id = _max_containment(item.text, proposal_space, proposals)
        gap_node, gap_id = _gap_id_matches(item.gap_ids, nodes)
        if gap_node:  # 显式 GAP-* 引用 → 最硬的落点证据
            state = s_covered
            node_id = gap_node
        elif node_sim >= threshold:
            state = s_covered
        elif prop_sim >= threshold:
            state = s_pending
        else:
            state = s_uncovered

        note = ""
        consistent = True
        declared = item.declared_status
        if declared == "open":
            if state == s_covered:
                consistent = False
                note = "doc_stale_candidate"
        elif declared == "planned":
            if state != s_covered:
                consistent = False
                note = "plan_not_seeded"
            elif gap_node:
                note = f"gap_id_explicit:{gap_id}"
        else:  # unmapped：不参与一致性判定
            consistent = True
            note = "unmapped_declared_status"
        rows.append(
            LedgerRow(
                item_id=item.item_id,
                section=item.section,
                line_no=item.line_no,
                declared_status=declared,
                state=state,
                node_sim=node_sim,
                node_id=node_id,
                proposal_sim=prop_sim,
                proposal_id=prop_id,
                consistent=consistent,
                note=note,
                text=item.text,
                gap_ids=item.gap_ids,
            )
        )

    judged = [r for r in rows if r.declared_status in ("open", "planned")]
    stats = {
        "items": len(rows),
        "judged_items": len(judged),
        "consistent_items": sum(1 for r in judged if r.consistent),
        "agreement_rate": round(
            sum(1 for r in judged if r.consistent) / len(judged), 6
        ) if judged else 1.0,
        "state_counts": _counts(r.state for r in rows),
        "declared_status_counts": _counts(r.declared_status for r in rows),
        "note_counts": _counts(r.note for r in rows if r.note),
        "disagreement_items": [r.item_id for r in judged if not r.consistent],
        "match_threshold": threshold,
    }
    return LedgerCoverage(rows=rows, stats=stats)


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))