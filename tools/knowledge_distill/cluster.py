"""聚类：余弦距离 DBSCAN（确定性）+ 邻域半径自适应 + 簇质量画像。

DBSCAN 而不是 k-means
---------------------
条目条数不定、簇数未知、且存在大量"只此一条"的孤立局限，密度型方法不需要预先指定 k，
并把孤立点标为 ``-1``（未归属），天然对应"暂不提案"。

确定性纪律
----------
* 点的处理顺序 = 输入索引顺序（不做集合遍历，不吃 ``hash`` 随机化）；
* 簇编号按"首次成为核心点的索引"递增给出，绝不按集合 pop 顺序；
* ``auto_eps`` 若需抽样估半径，采用等距步长抽样（不用随机种子）。

可复现的邻域半径
----------------
``auto_eps`` 取每个点第 ``k`` 近邻距离（``k = min_samples - 1``）分布的上分位点。
分位点越高 → 半径越大 → 簇越松；默认 0.6 取自合成语料标定（见报告 §3 表）。
"""
from __future__ import annotations

from typing import Callable, Mapping, Sequence

Metric = Callable[[int, int], float]
UNASSIGNED = -1


def _auto_eps_sample_size(n: int, cap: int = 500) -> int:
    return n if n <= cap else cap


def _sample_indices(n: int, cap: int = 500) -> list[int]:
    size = _auto_eps_sample_size(n, cap)
    if size == n:
        return list(range(n))
    step = n / size
    return [int(i * step) for i in range(size)]


def kth_distances(n: int, sim: Metric, k: int, *, cap: int = 500) -> list[float]:
    """每个（抽样）点到第 k 近邻的余弦距离，升序返回。"""
    if n <= 1:
        return [0.0] * n
    k = max(1, min(k, n - 1))
    idx = _sample_indices(n, cap)
    out: list[float] = []
    for i in idx:
        sims = sorted((sim(i, j) for j in range(n) if j != i), reverse=True)
        out.append(1.0 - sims[k - 1])
    return sorted(out)


def auto_eps(n: int, sim: Metric, *, min_samples: int, quantile: float = 0.6,
             cap: int = 500) -> float:
    """用 k-距离分布分位点给出邻域半径（余弦距离口径，值域 [0, 2]）。"""
    dists = kth_distances(n, sim, max(1, min_samples - 1), cap=cap)
    # 同文条目距离为 0，会整段压住分位点让半径退化成 0（历史实测），估半径时剔除
    nonzero = [d for d in dists if d > 1e-12]
    if nonzero:
        dists = nonzero
    if not dists:
        return 0.0
    pos = min(len(dists) - 1, max(0, int(round(quantile * (len(dists) - 1)))))
    return round(dists[pos], 6)


def _region(n: int, sim: Metric, i: int, eps: float) -> list[int]:
    return [j for j in range(n) if j != i and (1.0 - sim(i, j)) <= eps]


def dbscan(n: int, sim: Metric, *, eps: float, min_samples: int
           ) -> tuple[list[int], dict[str, object]]:
    """余弦距离 DBSCAN。

    返回 ``(labels, info)``；``info`` 记参数与规模统计，便于报告直接引用。
    """
    if n < 0:
        raise ValueError("n 不能为负")
    if eps < 0:
        raise ValueError("eps 不能为负")
    if min_samples < 1:
        raise ValueError("min_samples 必须 ≥ 1")
    labels = [UNASSIGNED] * n
    visited = [False] * n
    cluster_id = -1
    core_points = 0
    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        neighbours = _region(n, sim, i, eps)
        if len(neighbours) + 1 < min_samples:
            continue  # 暂为未归属点，后续可能被别的核心点收拢
        cluster_id += 1
        core_points += 1
        labels[i] = cluster_id
        seeds = list(neighbours)
        in_seeds = set(seeds)
        pos = 0
        while pos < len(seeds):
            j = seeds[pos]
            pos += 1
            if not visited[j]:
                visited[j] = True
                sub = _region(n, sim, j, eps)
                if len(sub) + 1 >= min_samples:
                    core_points += 1
                    for q in sub:
                        if q not in in_seeds:
                            in_seeds.add(q)
                            seeds.append(q)
            if labels[j] == UNASSIGNED:
                labels[j] = cluster_id
    info = {
        "n_points": n,
        "eps": round(eps, 6),
        "min_samples": min_samples,
        "clusters": cluster_id + 1,
        "core_points": core_points,
        "assigned": sum(1 for lab in labels if lab != UNASSIGNED),
        "unassigned": sum(1 for lab in labels if lab == UNASSIGNED),
        "algorithm": "dbscan(cosine)",
    }
    return labels, info


def medoid(indices: Sequence[int], sim: Metric) -> int:
    """簇内平均相似度最高的点（并列取最小索引，保证确定性）。"""
    best = indices[0]
    best_score = -1.0
    for i in indices:
        if len(indices) == 1:
            return i
        score = sum(sim(i, j) for j in indices if j != i) / (len(indices) - 1)
        if score > best_score + 1e-12:
            best_score = score
            best = i
    return best


def intra_similarity(indices: Sequence[int], sim: Metric) -> float:
    if len(indices) < 2:
        return 1.0
    total, pairs = 0.0, 0
    for a in range(len(indices)):
        for b in range(a + 1, len(indices)):
            total += sim(indices[a], indices[b])
            pairs += 1
    return round(total / pairs, 6) if pairs else 0.0


def purity(true_labels: Sequence[str], predicted: Sequence[int]) -> float:
    """给定真值标签时的聚类纯度（未归属点不计入分子/分母）。"""
    clusters: dict[int, dict[str, int]] = {}
    for truth, pred in zip(true_labels, predicted):
        if pred == UNASSIGNED:
            continue
        bucket = clusters.setdefault(pred, {})
        bucket[truth] = bucket.get(truth, 0) + 1
    total = sum(sum(b.values()) for b in clusters.values())
    if not total:
        return 0.0
    return round(sum(max(b.values()) for b in clusters.values()) / total, 6)


def cluster_profiles(labels: Sequence[int], sim: Metric,
                     truth: Sequence[str] | None = None,
                     texts: Sequence[str] | None = None) -> list[dict[str, object]]:
    """逐簇画像：成员索引、平均簇内相似度、代表点、主导真值标签（若给了真值）。"""
    buckets: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        if lab == UNASSIGNED:
            continue
        buckets.setdefault(lab, []).append(idx)
    profiles: list[dict[str, object]] = []
    for lab in sorted(buckets):
        members = buckets[lab]
        prof: dict[str, object] = {
            "cluster_id": lab,
            "size": len(members),
            "members": members,
            "intra_similarity": intra_similarity(members, sim),
            "medoid": medoid(members, sim),
        }
        if texts is not None:
            prof["medoid_text"] = texts[prof["medoid"]]
        if truth is not None:
            counts: dict[str, int] = {}
            for m in members:
                counts[truth[m]] = counts.get(truth[m], 0) + 1
            dom = max(sorted(counts), key=lambda k: counts[k])
            prof["dominant_truth"] = dom
            prof["dominant_share"] = round(counts[dom] / len(members), 6)
            prof["truth_counts"] = counts
        profiles.append(prof)
    return profiles


def cluster_quality(profiles: Sequence[Mapping[str, object]], *, threshold: float = 0.8
                    ) -> dict[str, object]:
    """簇质量汇总（供判据 (b) 用）：簇内一致率 ≥ 阈值的簇数 / 簇数。"""
    judged = [p for p in profiles if "dominant_share" in p]
    consistent = [p for p in judged if float(p["dominant_share"]) >= threshold]
    return {
        "clusters_judged": len(judged),
        "clusters_consistent": len(consistent),
        "consistency_ratio": round(len(consistent) / len(judged), 6) if judged else 0.0,
        "threshold": threshold,
    }


__all__ = [
    "Metric",
    "UNASSIGNED",
    "auto_eps",
    "cluster_profiles",
    "cluster_quality",
    "dbscan",
    "intra_similarity",
    "kth_distances",
    "medoid",
    "purity",
]