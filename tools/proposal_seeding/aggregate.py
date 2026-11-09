"""多提案者聚合：读入 → 3-gram Jaccard 语义去重 → 合并吸收细节。

判定规则（代码即规则，全部参数来自 :class:`SeedingConfig`）
-----------------------------------------------------------
1. **同 digest** → ``exact``（同一提案被两位提案者各自写下，零成本合并）。
2. **3-gram Jaccard ≥ merge_threshold**（任务书口径 0.55）→ ``merge``：并查集聚成一类，
   合并时吸收各家可取细节（分节行并集 + 判据并集 + 依赖提示并集 + 仓冲突留证）。
3. 其余 → ``distinct``：**互补保留**（不因为「同一域」就合并——那是人工仲裁最容易
   犯的错误，也会静默丢掉能力）。

为什么只用 Jaccard 而不用 max(jaccard, w·containment)
----------------------------------------------------
包含度会让「短提案被长提案完全覆盖」的情况被判重复（例如一句标题 vs 一段目标），
实测容易把互补项并掉。包含度仍然计算并写进证据表，但不参与合并决策
（``similarity.merge_rule = jaccard_only``，可经配置改口径并重跑）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.proposal_gate.textsim import normalize, shingles

from .config import SeedingConfig
from .model import (AggregatedItem, Proposal, ProposalError, Quote, sections_text,
                    digest, proposal_from_raw)

SCHEMA = "proposal-seeding-aggregate/1.0"


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------
def source_tag(path: str | Path) -> str:
    """``prop-6260.json`` → ``6260``；``wave2_proposals.json`` → ``wave2_proposals``。"""
    stem = Path(path).stem
    for sep in ("-", "_"):
        if sep in stem:
            head, tail = stem.rsplit(sep, 1)
            if tail.isdigit():
                return tail
    return stem


def _iter_raw(doc: Any, path: str | Path) -> list[Mapping[str, Any]]:
    if isinstance(doc, Mapping):
        for key in ("proposals", "items", "data"):
            if isinstance(doc.get(key), list):
                return [x for x in doc[key] if isinstance(x, Mapping)]
        return [doc]
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, Mapping)]
    raise ProposalError(f"{path}: 顶层既不是对象也不是数组")


def load_proposals(paths: Sequence[str | Path], cfg: SeedingConfig
                   ) -> tuple[list[Proposal], list[dict[str, Any]], list[dict[str, Any]]]:
    """读入多份提案工件。

    返回 ``(proposals, rejected, files)``：``rejected`` 逐条给出原因（fail-closed 但不静默
    丢弃）；``files`` 记录每份输入的路径 / 摘要 / 条数，供审计链引用。
    """
    proposals: list[Proposal] = []
    rejected: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for path in paths:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"提案文件不存在: {p}")
        text = p.read_text(encoding="utf-8")
        doc = json.loads(text)
        tag = source_tag(p)
        raws = _iter_raw(doc, p)
        accepted = 0
        for idx, raw in enumerate(raws):
            try:
                prop = proposal_from_raw(raw, source=tag, index=idx,
                                         required=cfg.required_fields,
                                         criteria_markers=cfg.criteria_section_markers)
            except ProposalError as exc:
                rejected.append({"file": str(p), "source": tag, "index": idx,
                                 "reason": str(exc)})
                continue
            proposals.append(prop)
            accepted += 1
        files.append({
            "path": str(p),
            "source": tag,
            "raw_count": len(raws),
            "accepted": accepted,
            "sha256": digest(text),
        })
    proposals.sort(key=lambda pr: (pr.source, pr.index))
    return proposals, rejected, files


# ---------------------------------------------------------------------------
# similarity
# ---------------------------------------------------------------------------
def _ratio(a: int, b: int) -> float:
    if not a or not b:
        return 0.0
    return min(a, b) / max(a, b)


def similarity_measure(text_a: str, text_b: str, cfg: SeedingConfig) -> dict[str, float]:
    """``jaccard`` 为判据口径；``containment``/``score`` 仅作证据列。"""
    size = cfg.similarity.shingle_size
    sa, sb = shingles(text_a, size), shingles(text_b, size)
    if not sa or not sb:
        return {"jaccard": 0.0, "containment": 0.0, "score": 0.0, "length_ratio": 0.0}
    inter = len(sa & sb)
    union = len(sa | sb)
    jaccard = inter / union if union else 0.0
    containment = inter / min(len(sa), len(sb))
    ratio = _ratio(len(sa), len(sb))
    score = jaccard
    if ratio >= cfg.similarity.min_length_ratio:
        score = max(jaccard, cfg.similarity.containment_weight * containment)
    return {
        "jaccard": round(jaccard, 6),
        "containment": round(containment, 6),
        "score": round(score, 6),
        "length_ratio": round(ratio, 6),
    }


def pairwise_similarity(proposals: Sequence[Proposal], cfg: SeedingConfig
                        ) -> list[dict[str, Any]]:
    """所有无序对的相似度（按 jaccard 降序，附双方来源与 id，可直接做证据表）。"""
    out: list[dict[str, Any]] = []
    for i in range(len(proposals)):
        for j in range(i + 1, len(proposals)):
            a, b = proposals[i], proposals[j]
            m = similarity_measure(a.text, b.text, cfg)
            out.append({
                "a": _pid(a), "b": _pid(b),
                "a_title": a.title, "b_title": b.title,
                "a_source": a.source, "b_source": b.source,
                "cross_source": a.source != b.source,
                "exact": a.digest == b.digest,
                **m,
            })
    out.sort(key=lambda r: (-r["jaccard"], r["a"], r["b"]))
    return out


def _pid(prop: Proposal) -> str:
    return f"{prop.source}#{prop.index}"


# ---------------------------------------------------------------------------
# clustering + merge
# ---------------------------------------------------------------------------
def cluster(proposals: Sequence[Proposal], sims: Sequence[Mapping[str, Any]],
            cfg: SeedingConfig) -> list[list[Proposal]]:
    """并查集：同 digest 或 jaccard ≥ 阈值即同簇。簇内按来源排序。"""
    parent = {_pid(p): _pid(p) for p in proposals}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    for rec in sims:
        if rec["exact"] or rec["jaccard"] >= cfg.similarity.merge_threshold:
            union(rec["a"], rec["b"])

    groups: dict[str, list[Proposal]] = {}
    for prop in proposals:
        groups.setdefault(find(_pid(prop)), []).append(prop)
    clusters = [sorted(g, key=lambda p: (p.source, p.index)) for g in groups.values()]
    clusters.sort(key=lambda g: (g[0].source, g[0].index))
    return clusters


def merge_cluster(members: Sequence[Proposal], cfg: SeedingConfig,
                  sims: Sequence[Mapping[str, Any]] = ()) -> AggregatedItem:
    """合并一簇：以「内容最完整」的成员为基底，逐行吸收其余成员的新增内容。

    标题取基底（最长文本，长度相同取 digest 小者 → 确定性）；分节按「基底先、贡献者后」
    顺序并集；判据 / 依赖提示并集保序；仓不一致 → ``repo_conflict`` 留证（不静默改写）。
    """
    ordered = sorted(members, key=lambda p: (-len(p.text), p.digest))
    base = ordered[0]
    sections: dict[str, list[str]] = {k: list(v) for k, v in base.sections.items()}
    criteria: list[str] = list(base.criteria)
    deps: list[str] = list(base.deps)
    quotes: list[Quote] = [Quote(base.source, base.index, base.raw_id,
                                 tuple(sorted(base.sections)))]
    absorbed: list[dict[str, Any]] = []
    repos = [base.repo]
    is_maturity = base.is_maturity

    for prop in ordered[1:]:
        added_sections: dict[str, int] = {}
        for key in sorted(prop.sections):
            target = sections.setdefault(key, [])
            added = 0
            for line in prop.sections[key]:
                if line not in target:
                    target.append(line)
                    added += 1
            if added:
                added_sections[key] = added
        added_criteria = [c for c in prop.criteria if c not in criteria]
        criteria.extend(added_criteria)
        added_deps = [d for d in prop.deps if d not in deps]
        deps.extend(added_deps)
        if prop.repo not in repos:
            repos.append(prop.repo)
        is_maturity = is_maturity or prop.is_maturity
        quotes.append(Quote(prop.source, prop.index, prop.raw_id,
                            tuple(sorted(prop.sections))))
        absorbed.append({
            "source": prop.source,
            "index": prop.index,
            "raw_id": prop.raw_id,
            "same_digest": prop.digest == base.digest,
            "sections_added": added_sections,
            "criteria_added": added_criteria,
            "deps_added": added_deps,
        })

    member_max = 0.0
    member_ids = {_pid(m) for m in members}
    for rec in sims:
        if rec["a"] in member_ids and rec["b"] in member_ids:
            member_max = max(member_max, float(rec["jaccard"]))

    payload = {
        "title": base.title,
        "sections": sections,
        "criteria": criteria,
        "deps": deps,
        "is_maturity": is_maturity,
        "repos": sorted(repos),
    }
    return AggregatedItem(
        item_id=digest(payload)[:16],
        title=base.title,
        repo=base.repo,
        repos=tuple(sorted(repos)),
        is_maturity=is_maturity,
        sections=sections,
        criteria=tuple(criteria),
        deps=tuple(deps),
        quotes=tuple(quotes),
        absorbed=tuple(absorbed),
        repo_conflict=len(repos) > 1,
        max_similarity=round(member_max, 6),
        members=tuple(sorted(member_ids)),
    )


def aggregate(paths: Sequence[str | Path], cfg: SeedingConfig) -> dict[str, Any]:
    """完整聚合：读入 → 相似度 → 分簇 → 合并 → 统计。"""
    proposals, rejected, files = load_proposals(paths, cfg)
    sims = pairwise_similarity(proposals, cfg)
    clusters = cluster(proposals, sims, cfg)
    items = [merge_cluster(c, cfg, sims) for c in clusters]
    # 聚合项排序：成熟化单优先（机器门要求≥min_maturity_nodes），再按仓名与内容摘要
    items.sort(key=lambda it: (not it.is_maturity, it.repo, it.item_id))

    merged_groups = [c for c in clusters if len(c) > 1]
    exact_groups = [c for c in merged_groups if len({p.digest for p in c}) == 1]
    max_j = max((r["jaccard"] for r in sims), default=0.0)
    top = sims[0] if sims else None
    return {
        "schema": SCHEMA,
        "config": cfg.as_dict(),
        "files": files,
        "sources": sorted({p.source for p in proposals}),
        "counts": {
            "proposals_in": len(proposals),
            "rejected": len(rejected),
            "clusters": len(clusters),
            "items_out": len(items),
            "merged_clusters": len(merged_groups),
            "merged_exact": len(exact_groups),
            "merged_semantic": len(merged_groups) - len(exact_groups),
            "absorbed_proposals": sum(len(c) - 1 for c in merged_groups),
            "cross_source_merges": sum(
                1 for c in merged_groups if len({p.source for p in c}) > 1),
            "distinct_kept": sum(1 for c in clusters if len(c) == 1),
        },
        "similarity": {
            "max_jaccard": round(max_j, 6),
            "max_pair": top,
            "threshold": cfg.similarity.merge_threshold,
            "pairs_above_threshold": sum(
                1 for r in sims if r["jaccard"] >= cfg.similarity.merge_threshold),
            "matrix": sims,
        },
        "proposals": [p.as_dict() for p in proposals],
        "rejected": rejected,
        "items": [it.as_dict() for it in items],
    }