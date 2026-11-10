"""跨战役提案语义去重器（TF-IDF → LSA → cosine；阈值判重叠并输出合并建议）。

语义
----
* 输入是**异构文档集合**（历史提案池条目、战役 DAG 节点），每条带 ``campaign``
  （提案 = 播种波次目录，节点 = 计划 id）。同波内去重解决「同一波内重复立项」，
  跨波次去重解决「同一件事换个说法再来一次」——后者是本模块主靶。
* **分档判定**（阈值全部来自配置，代码无字面量）：
  * ``score ≥ dup_threshold`` → ``duplicate``：给合并建议（自动档，要求高精确率）；
  * ``review_threshold ≤ score < dup_threshold`` → ``near_duplicate``：进复核带
    （不自动合并，但**计入机器门召回**——档案召回是治理链的兜底，见报告表 3）；
  * 其余不相邻。
* **合并建议** = duplicate 边上的并查集连通分量：代表元取分量内平均内部相似度最大的文档
  （并列取 id 字典序），附 ``shared_terms``（两向量权重乘积，可解释）与 ``rationale``；
  分量超过 ``max_merge_group_size`` 只给 ``cluster_review``，不自动合并。

纪律
----
* 空池 / 单文档 / 全空文本 → 输出空集，不抛异常（负控判据 e）。
* 结果定序 (score desc, a, b)，同一输入逐字节可复现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .semantic import SemanticSpace


@dataclass(frozen=True)
class Doc:
    """去重输入单元（提案或战役节点）。"""

    id: str
    text: str
    campaign: str = ""
    repo: str = ""
    kind: str = "proposal"
    physics_domain: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "campaign": self.campaign,
            "repo": self.repo,
            "kind": self.kind,
            "physics_domain": self.physics_domain,
        }


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    score: float
    cross_campaign: bool
    shared_terms: tuple[tuple[str, float], ...] = ()
    verdict: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "a": self.a,
            "b": self.b,
            "score": round(self.score, 6),
            "cross_campaign": self.cross_campaign,
            "verdict": self.verdict,
            "shared_terms": [[t, round(w, 6)] for t, w in self.shared_terms],
        }


@dataclass
class MergeSuggestion:
    group_id: str
    representative: str
    members: list[str]
    action: str
    mean_similarity: float
    max_similarity: float
    campaigns: list[str]
    shared_terms: list[tuple[str, float]] = field(default_factory=list)
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "representative": self.representative,
            "members": list(self.members),
            "member_count": len(self.members),
            "action": self.action,
            "mean_similarity": round(self.mean_similarity, 6),
            "max_similarity": round(self.max_similarity, 6),
            "campaigns": list(self.campaigns),
            "shared_terms": [[t, round(w, 6)] for t, w in self.shared_terms],
            "rationale": self.rationale,
        }


@dataclass
class DedupResult:
    docs: list[Doc]
    pairs: list[Pair]
    duplicate_pairs: list[Pair]
    review_pairs: list[Pair]
    suggestions: list[MergeSuggestion]
    stats: dict[str, Any]

    @property
    def duplicate_groups(self) -> list[MergeSuggestion]:
        return [s for s in self.suggestions if s.action == "merge"]

    def as_dict(self, *, emit_pairs: bool = True) -> dict[str, Any]:
        return {
            "stats": self.stats,
            "suggestions": [s.as_dict() for s in self.suggestions],
            "duplicate_pairs": [p.as_dict() for p in self.duplicate_pairs] if emit_pairs else [],
            "review_pairs": [p.as_dict() for p in self.review_pairs] if emit_pairs else [],
        }


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        root = self.parent.setdefault(x, x)
        while self.parent[root] != root:
            self.parent[root] = self.parent[self.parent[root]]
            root = self.parent[root]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:  # 定序连接：分量根与输入顺序无关
                ra, rb = rb, ra
            self.parent[rb] = ra


def scan_pairs(
    sem: SemanticSpace,
    docs: Sequence[Doc],
    *,
    dup_threshold: float,
    review_threshold: float,
    top_shared_terms: int = 6,
    emit_pair_cap: int = 4000,
) -> tuple[list[Pair], dict[str, Any]]:
    """全对语义扫描（LSA 时用分块矩阵乘，TF-IDF 退化时用稀疏点积）。"""
    n = len(docs)
    if n < 2:
        return [], {"candidate_pairs": 0, "scored_pairs": 0, "cap_hit": False}
    hits = sem.sim_matrix_pairs(review_threshold)
    cap = max(0, emit_pair_cap)
    out: list[Pair] = []
    for s, i, j in hits[:cap]:
        a_id, b_id = docs[i].id, docs[j].id
        if a_id > b_id:  # 对序归一：输出与文档输入顺序无关
            a_id, b_id = b_id, a_id
            i, j = j, i
        out.append(
            Pair(
                a=a_id,
                b=b_id,
                score=s,
                cross_campaign=docs[i].campaign != docs[j].campaign,
                shared_terms=tuple(
                    sem.space.shared_terms(sem.space.vectors[i], sem.space.vectors[j], top_shared_terms)
                ),
                verdict="duplicate" if s >= dup_threshold else "near_duplicate",
            )
        )
    return out, {
        "candidate_pairs": len(hits),
        "scored_pairs": min(len(hits), cap),
        "cap_hit": len(hits) > cap,
    }


def merge_suggestions(
    docs: Sequence[Doc],
    pairs: Sequence[Pair],
    sem: SemanticSpace,
    *,
    dup_threshold: float,
    max_merge_group_size: int = 12,
    top_shared_terms: int = 6,
) -> list[MergeSuggestion]:
    """duplicate 边上的连通分量 → 合并建议（代表元/跨战役标记/理由）。"""
    by_id = {d.id: k for k, d in enumerate(docs)}
    dup_edges = [p for p in pairs if p.score >= dup_threshold]
    if not dup_edges:
        return []
    uf = _UnionFind()
    for p in dup_edges:
        uf.add(p.a)
        uf.add(p.b)
        uf.union(p.a, p.b)
    groups: dict[str, list[str]] = {}
    for node in sorted(uf.parent):
        groups.setdefault(uf.find(node), []).append(node)

    out: list[MergeSuggestion] = []
    for gi, root in enumerate(sorted(groups)):
        members = sorted(groups[root])
        if len(members) < 2:
            continue
        idxs = [by_id[m] for m in members if m in by_id]
        best_id, best_mean = members[0], -1.0
        for m in members:
            others = [o for o in members if o != m and o in by_id]
            if not others:
                continue
            mean = sum(sem.sim(by_id[m], by_id[o]) for o in others) / len(others)
            if mean > best_mean + 1e-12 or (abs(mean - best_mean) <= 1e-12 and m < best_id):
                best_id, best_mean = m, mean
        member_pairs = [p.score for p in dup_edges if p.a in members and p.b in members]
        campaigns = sorted({docs[k].campaign for k in idxs})
        action = "merge" if len(members) <= max_merge_group_size else "cluster_review"
        second = members[1] if len(members) > 1 else members[0]
        shared = sem.space.shared_terms(
            sem.space.vectors[by_id[best_id]], sem.space.vectors[by_id[second]], top_shared_terms
        )
        rationale = (
            f"{len(members)} 条文档在语义空间两两相连（最高 {max(member_pairs):.4f}），"
            f"建议并入代表元 {best_id}（跨 {len(campaigns)} 个播种波次/计划）"
            if action == "merge"
            else f"分量 {len(members)} 条超过单次自动合并上限 {max_merge_group_size}，转人工 cluster_review"
        )
        out.append(
            MergeSuggestion(
                group_id=f"G{gi + 1:04d}",
                representative=best_id,
                members=members,
                action=action,
                mean_similarity=sum(member_pairs) / len(member_pairs) if member_pairs else 0.0,
                max_similarity=max(member_pairs) if member_pairs else 0.0,
                campaigns=campaigns,
                shared_terms=list(shared),
                rationale=rationale,
            )
        )
    out.sort(key=lambda s: (-len(s.members), -s.max_similarity, s.group_id))
    return out


def analyze(
    docs: Iterable[Doc],
    cfg: Mapping[str, Any],
    *,
    emit_pair_cap: int | None = None,
    use_lsa: bool | None = None,
    representation: str | None = None,
) -> DedupResult:
    """一站式：建语义空间 → 全对扫描 → 合并建议（空池安全）。"""
    from .labels import doc_text_for  # 局部导入：避免循环依赖

    doc_list = list(docs)
    dedup_cfg = dict(cfg.get("dedup", {}) or {})
    representation = representation or str(dedup_cfg.get("representation", "claim"))
    raw = dict(cfg)
    if use_lsa is False:
        raw = {**raw, "lsa": {**dict(raw.get("lsa", {}) or {}), "enabled": False}}
    texts = [doc_text_for(d, representation, cfg) for d in doc_list]
    sem = SemanticSpace.build(texts, raw)
    dup_threshold = float(dedup_cfg.get("dup_threshold", 0.85))
    review_threshold = float(dedup_cfg.get("review_threshold", 0.55))
    pairs, scan_stats = scan_pairs(
        sem,
        doc_list,
        dup_threshold=dup_threshold,
        review_threshold=review_threshold,
        top_shared_terms=int(dedup_cfg.get("top_shared_terms", 6)),
        emit_pair_cap=int(emit_pair_cap if emit_pair_cap is not None else dedup_cfg.get("emit_pair_cap", 4000)),
    )
    suggestions = merge_suggestions(
        doc_list,
        pairs,
        sem,
        dup_threshold=dup_threshold,
        max_merge_group_size=int(dedup_cfg.get("max_merge_group_size", 12)),
        top_shared_terms=int(dedup_cfg.get("top_shared_terms", 6)),
    )
    duplicate_pairs = [p for p in pairs if p.verdict == "duplicate"]
    review_pairs = [p for p in pairs if p.verdict == "near_duplicate"]
    stats = {
        "doc_count": len(doc_list),
        "campaign_count": len({d.campaign for d in doc_list}),
        "empty_text_docs": sum(1 for d in doc_list if not d.text.strip()),
        "semantic_backend": sem.backend,
        "semantic_dim": sem.dim,
        "vocab_size": len(sem.space.vocab),
        "dropped_generic_terms": len(sem.space.dropped_terms),
        "representation": representation,
        "dup_threshold": dup_threshold,
        "review_threshold": review_threshold,
        "duplicate_pair_count": len(duplicate_pairs),
        "near_duplicate_pair_count": len(review_pairs),
        "cross_campaign_duplicate_pairs": sum(1 for p in duplicate_pairs if p.cross_campaign),
        "cross_campaign_review_pairs": sum(1 for p in review_pairs if p.cross_campaign),
        "suggestion_count": len(suggestions),
        "merge_suggestion_count": sum(1 for s in suggestions if s.action == "merge"),
        "cluster_review_count": sum(1 for s in suggestions if s.action == "cluster_review"),
        "docs_in_merge_groups": sum(len(s.members) for s in suggestions if s.action == "merge"),
        **scan_stats,
    }
    return DedupResult(
        docs=doc_list,
        pairs=pairs,
        duplicate_pairs=duplicate_pairs,
        review_pairs=review_pairs,
        suggestions=suggestions,
        stats=stats,
    )