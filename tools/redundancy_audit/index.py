"""节点文本索引 + 近似最近邻（倒排 + 上界剪枝 + 长度过滤）。

为什么需要近似
--------------
全库 n≈2450 个节点时全对枚举是 300 万对，每对都要做集合交并，单机 Python 不可行；
而冗余审计每波都要重跑，必须把候选生成与精确打分都压到近线性。

算法（四层，全部可对账）
------------------------
1. **解析预算**：每条文本只切一次字符 n-gram（复用 ``textsim.shingles``）。
2. **倒排 + df 截断**：只对 df ≤ ``ann.posting_cap`` 的 shingle 建倒排（高频 shingle 在语料里
   没有判别力——数据驱动的频次截断，不是词表）。
3. **长度过滤**：集合规模比 < ``similarity.min_length_ratio`` 的对直接剪掉（与相似度里
   containment 项的启用条件同口径）。
4. **上界剪枝**：候选对从倒排里天然带出「共享 shingle 数 s」。因
   ``jaccard = s/union ≤ s/max(|A|,|B|)`` 且 ``containment = s/min(|A|,|B|)``，
   故 ``score ≤ max(s/maxsize, w_c·s/minsize)``。上界低于审计下限的对**不做集合交并**即淘汰。

只有通过上界的对才做精确计算（``textsim.sim_from_sets``，保证相似度口径唯一）。

候选召回率（对账口径）
----------------------
``ann_recall`` = 暴力枚举中 score≥floor 的对里，被候选集覆盖的比例。测试夹具与抽样桶上
可直接暴力对账；全库尺度用稳定哈希抽桶（诚实披露抽样，而非全量）。

零领域语义
----------
本模块只搬运 shingle 字符串与整数分数，不认识任何域词。
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from tools.proposal_gate.textsim import SimResult, shingles, sim_from_sets

from .config import RedundancyConfig


@dataclass(frozen=True)
class NodeVector:
    """一条节点文本的索引表示（预解析，避免重复切分）。"""

    key: str            # 节点标识（快照里的 node_id 字符串）
    size: int           # 集合规模（长度过滤/上界剪枝用）
    grams: frozenset[str]


def build_vectors(nodes: Sequence[dict], cfg: RedundancyConfig) -> list[NodeVector]:
    """``[{node_id, text}]`` → NodeVector 列表（保持输入顺序，确定性）。"""
    st = int(cfg.get("similarity.shingle_size"))
    min_chars = int(cfg.get("similarity.min_chars"))
    out: list[NodeVector] = []
    for n in nodes:
        text = str(n.get("text") or "")
        grams = shingles(text, st)
        if len(text) < min_chars or not grams:
            # 过短文本（标题级）不参与成对判重：字符 n-gram 在短文本上假阳性高，
            # 且这类节点占成本极低（不是本审计要治理的对象）。
            continue
        out.append(NodeVector(key=str(n["node_id"]), size=len(grams), grams=grams))
    return out


def build_postings(vectors: Sequence[NodeVector], cfg: RedundancyConfig) -> dict[str, tuple[int, ...]]:
    """shingle → 出现它的向量下标元组（df 超过 posting_cap 的 shingle 丢弃）。"""
    cap = int(cfg.get("ann.posting_cap"))
    raw: dict[str, list[int]] = {}
    for idx, vec in enumerate(vectors):
        for gram in vec.grams:
            raw.setdefault(gram, []).append(idx)
    return {g: tuple(sorted(ix)) for g, ix in raw.items() if len(ix) <= cap}


def candidate_pairs(
    vectors: Sequence[NodeVector],
    postings: dict[str, tuple[int, ...]],
    cfg: RedundancyConfig,
) -> list[tuple[int, int, int]]:
    """倒排候选对 ``(i, j, 共享 shingle 数)``，i<j，确定性排序。"""
    min_shared = int(cfg.get("ann.min_shared_shingles"))
    max_per_node = int(cfg.get("ann.max_candidates_per_node"))
    min_ratio = float(cfg.get("similarity.min_length_ratio"))
    shared: dict[int, dict[int, int]] = {i: {} for i in range(len(vectors))}
    for vec_idx, vec in enumerate(vectors):
        bucket = shared[vec_idx]
        for gram in vec.grams:
            plist = postings.get(gram)
            if not plist:
                continue
            for other in plist:
                if other <= vec_idx:
                    continue
                ot = vectors[other]
                small, big = sorted((vec.size, ot.size))
                if big and small / big < min_ratio:
                    continue
                bucket[other] = bucket.get(other, 0) + 1
        if len(bucket) > max_per_node:
            # 截断到共享数最高的 max_per_node 个邻居（按 -shared, index 定序，确定性）
            kept = sorted(bucket.items(), key=lambda kv: (-kv[1], kv[0]))[:max_per_node]
            shared[vec_idx] = dict(kept)
    out: dict[tuple[int, int], int] = {}
    for i, bucket in shared.items():
        for j, cnt in bucket.items():
            if cnt < min_shared:
                continue
            key = (i, j) if i < j else (j, i)
            prev = out.get(key)
            # 两侧都给出时取较大共享数（截断只会少算，不会多算）
            if prev is None or cnt > prev:
                out[key] = cnt
    return sorted((i, j, c) for (i, j), c in out.items())


def upper_bound(vec_i: NodeVector, vec_j: NodeVector, shared: int, cfg: RedundancyConfig) -> float:
    """由共享 shingle 数给出的相似度上界（无需集合交并）。"""
    w_c = float(cfg.get("similarity.containment_weight"))
    min_ratio = float(cfg.get("similarity.min_length_ratio"))
    small, big = sorted((vec_i.size, vec_j.size))
    if not big or small / big < min_ratio:
        return 0.0
    return max(shared / big, w_c * shared / small)


def score_pairs(
    vectors: Sequence[NodeVector],
    pairs: Iterable[tuple[int, int, int]],
    cfg: RedundancyConfig,
    *,
    floor: float | None = None,
) -> list[tuple[int, int, SimResult]]:
    """对上界达标的候选对做精确打分；低于 ``floor`` 的直接丢弃（默认取扫描网格最小值）。"""
    if floor is None:
        floor = min(float(x) for x in cfg.get("thresholds.sweep_grid"))
    out: list[tuple[int, int, SimResult]] = []
    for i, j, shared in pairs:
        if upper_bound(vectors[i], vectors[j], shared, cfg) < floor:
            continue
        res = sim_from_sets(vectors[i].grams, vectors[j].grams, cfg.gate_config())
        if res.score >= floor:
            out.append((i, j, res))
    out.sort(key=lambda t: (-t[2].score, t[0], t[1]))
    return out


def brute_force_pairs(
    vectors: Sequence[NodeVector], cfg: RedundancyConfig, *, floor: float | None = None
) -> list[tuple[int, int, SimResult]]:
    """全对枚举（对账用；O(n²)，仅用于测试夹具/抽样桶）。"""
    gate = cfg.gate_config()
    thr = float(cfg.get("thresholds.weak")) if floor is None else float(floor)
    out: list[tuple[int, int, SimResult]] = []
    for i, j in itertools.combinations(range(len(vectors)), 2):
        res = sim_from_sets(vectors[i].grams, vectors[j].grams, gate)
        if res.score >= thr:
            out.append((i, j, res))
    out.sort(key=lambda t: (-t[2].score, t[0], t[1]))
    return out


def recall_of(
    candidates: Iterable[tuple[int, int]],
    reference: Iterable[tuple[int, int]],
) -> dict[str, float | int]:
    """候选集对参考集（暴力/抽样真值）的召回。"""
    cand = {(i, j) if i < j else (j, i) for i, j in candidates}
    ref = {(i, j) if i < j else (j, i) for i, j in reference}
    hit = len(cand & ref)
    return {
        "reference_pairs": len(ref),
        "candidate_pairs": len(cand),
        "hit_pairs": hit,
        "recall": (hit / len(ref)) if ref else 1.0,
    }


def sample_recall_check(
    nodes: Sequence[dict], cfg: RedundancyConfig, *, buckets: int = 12, seed: int = 0,
    floor: float | None = None,
) -> dict[str, float | int]:
    """全库尺度的召回抽检：按稳定哈希分桶，逐桶暴力枚举求真值，与候选集对账。"""
    min_chars = int(cfg.get("similarity.min_chars"))
    usable = [n for n in nodes if len(str(n.get("text") or "")) >= min_chars]
    if not usable:
        return {"buckets": 0, "recall": 1.0, "reference_pairs": 0, "hit_pairs": 0}
    keyed = sorted(usable, key=lambda n: str(n["node_id"]))
    sample = [n for n in keyed if _bucket(str(n["node_id"]), buckets) == seed % buckets]
    vectors = build_vectors(sample, cfg)
    postings = build_postings(vectors, cfg)
    cand = candidate_pairs(vectors, postings, cfg)
    ref = brute_force_pairs(vectors, cfg, floor=floor)
    stat = recall_of([(i, j) for i, j, _ in cand], [(i, j) for i, j, _ in ref])
    stat["buckets"] = buckets
    stat["sample_nodes"] = len(vectors)
    stat["bucket"] = seed % buckets
    return stat


def plan_recall_check(nodes: Sequence[dict], cfg: RedundancyConfig, *,
                      floor: float | None = None) -> dict[str, float | int]:
    """定向召回对账：**同计划内的全对**（这一区域最可能有重复，也是漏检代价最高的区域）。

    同计划对数约 1e4 量级，可暴力枚举；比随机抽样更能证明候选集在「真重复高发区」不漏。
    """
    min_chars = int(cfg.get("similarity.min_chars"))
    by_plan: dict[Any, list[dict]] = {}
    for n in nodes:
        if len(str(n.get("text") or "")) < min_chars:
            continue
        by_plan.setdefault(n.get("plan_id"), []).append(n)
    ref: list[tuple[int, int]] = []
    cand: list[tuple[int, int]] = []
    total_pairs = 0
    for _, members in sorted(by_plan.items(), key=lambda kv: str(kv[0])):
        if len(members) < 2:
            continue
        vectors = build_vectors(members, cfg)
        postings = build_postings(vectors, cfg)
        cf = candidate_pairs(vectors, postings, cfg)
        bf = brute_force_pairs(vectors, cfg, floor=floor)
        total_pairs += len(vectors) * (len(vectors) - 1) // 2
        cand.extend((i, j) for i, j, _ in cf)
        ref.extend((i, j) for i, j, _ in bf)
    hit = len(set(cand) & set(ref))
    return {"same_plan_pairs": total_pairs, "reference_pairs": len(ref),
            "candidate_pairs": len(set(cand)), "hit_pairs": hit,
            "recall": (hit / len(ref)) if ref else 1.0}


def _bucket(text: str, buckets: int) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16) % buckets