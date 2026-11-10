"""knowledge_distill 特征与聚类测试（合成语料，零域词）。

被测：``features.py``（TF-IDF / 子词哈希嵌入 / 词元通道 / 三通道合成）与
``cluster.py``（余弦 DBSCAN / 自适应半径 / 簇画像 / 纯度）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.knowledge_distill.cluster import (  # noqa: E402
    UNASSIGNED,
    auto_eps,
    cluster_profiles,
    cluster_quality,
    dbscan,
    intra_similarity,
    kth_distances,
    medoid,
    purity,
)
from tools.knowledge_distill.features import (  # noqa: E402
    FeatureSpace,
    HashedEmbedding,
    Tfidf,
    TokenIndex,
    char_ngrams,
    cosine,
    ngram_jaccard,
    normalize_text,
    token_units,
)
from tools.knowledge_distill.params import DistillParams  # noqa: E402

GROUP_A = [
    "稀疏线性求解器只实现了对角预条件，缺 ILU 与 AMG 预条件子，大问题收敛慢",
    "线性求解器仅含对角缩放预条件，ILU/AMG 预条件尚未实现，大问题迭代次数偏高",
    "稀疏求解器缺 ILU/AMG 预条件子，只剩对角预条件，收敛速度不满足大规模算例",
]
GROUP_B = [
    "长任务队列没有断点续跑能力，节点重启后作业从零开始",
    "任务队列缺 checkpoint 断点续跑，进程重启后只能整段重跑",
    "队列不支持检查点续跑，节点重启导致长作业从头再跑一遍",
]
OUTLIERS = [
    "文档站点的搜索索引没有增量更新，新增页面需要全量重建",
    "前端表格分页在移动端样式错位，遮挡操作按钮",
]


# --------------------------------------------------------------------------- #
# 基础特征
# --------------------------------------------------------------------------- #
def test_normalize_and_token_units() -> None:
    assert normalize_text("**Window**：`L2`  误差 <3%") == "window l2 误差 3"
    toks = token_units("ILU/AMG 预条件子收敛慢")
    assert "ilu" in toks and "amg" in toks
    assert "预条" in toks and "收敛" in toks


def test_char_ngrams_are_bounded_and_dedup_safe() -> None:
    grams = char_ngrams("abcd", 2, 3)
    assert grams == ["ab", "bc", "cd", "abc", "bcd"]
    assert char_ngrams("x", 2, 3) == ["x"]
    assert char_ngrams("", 2, 3) == []


def test_tfidf_vectors_are_unit_norm_and_discriminative() -> None:
    docs = GROUP_A + GROUP_B + OUTLIERS
    model = Tfidf().fit(docs)
    v0 = model.transform(GROUP_A[0])
    assert sum(x * x for x in v0.values()) == pytest.approx(1.0, rel=1e-6)
    cross = cosine(v0, model.transform(GROUP_A[1]))
    far = cosine(v0, model.transform(OUTLIERS[0]))
    assert cross > far
    assert cosine(v0, model.transform("完全无关的一句短话")) == pytest.approx(0.0, abs=0.05)


def test_hashed_embedding_is_deterministic_and_normalized() -> None:
    emb = HashedEmbedding(dim=64)
    a1, a2 = emb.embed(GROUP_A[0]), emb.embed(GROUP_A[0])
    assert a1 == a2
    assert sum(x * x for x in a1) == pytest.approx(1.0, rel=1e-6)
    assert HashedEmbedding.cosine(a1, emb.embed(GROUP_A[1])) > \
        HashedEmbedding.cosine(a1, emb.embed(OUTLIERS[0]))
    with pytest.raises(ValueError):
        HashedEmbedding(dim=4)
    with pytest.raises(ValueError):
        HashedEmbedding.from_vectors({"a": [0.1, 0.2], "b": [0.1]})


def test_token_channel_length_ratio_gate() -> None:
    """短片段与长句共享一个词元时，包含度不得直接给满分（历史长链缺陷）。"""
    index = TokenIndex().fit(GROUP_A + ["收敛"])
    long_map = index.weights(GROUP_A[0])
    short_map = index.weights("收敛")
    assert index.containment(short_map, long_map) >= 0.9
    assert index.similarity(short_map, long_map, min_length_ratio=0.5) < 0.2
    assert index.weighted_jaccard(short_map, long_map) < 0.2


def test_feature_space_three_channels_and_components() -> None:
    docs = GROUP_A + GROUP_B + OUTLIERS
    space = FeatureSpace(DistillParams()).fit_transform(docs)
    assert len(space) == len(docs)
    comp = space.full_components(0, 1)
    assert set(comp) == {"tfidf_cos", "emb_cos", "combined", "jaccard",
                         "token_similarity", "pair_metric"}
    assert comp["pair_metric"] == pytest.approx(max(comp["combined"], comp["jaccard"],
                                                    comp["token_similarity"]), rel=1e-6)
    assert space.pair_metric(0, 1) > space.pair_metric(0, len(GROUP_A) + len(GROUP_B))
    # 组内比组间更像：这是三通道合成后的基本性质
    within = sum(space.pair_metric(i, j) for i in range(3) for j in range(i + 1, 3)) / 3
    between = sum(space.pair_metric(i, j) for i in range(3) for j in range(3, 6)) / 9
    assert within > between + 0.1


def test_ngram_jaccard_edge_cases() -> None:
    assert ngram_jaccard("", "abc") == 0.0
    assert ngram_jaccard("同样的字串", "同样的字串") == 1.0


# --------------------------------------------------------------------------- #
# 聚类
# --------------------------------------------------------------------------- #
def test_dbscan_separates_groups_and_flags_outliers() -> None:
    docs = GROUP_A + GROUP_B + OUTLIERS
    space = FeatureSpace(DistillParams()).fit_transform(docs)
    eps = auto_eps(len(docs), space.pair_metric, min_samples=2, quantile=0.8)
    labels, info = dbscan(len(docs), space.pair_metric, eps=eps, min_samples=2)
    assert info["algorithm"] == "dbscan(cosine)"
    assert info["assigned"] + info["unassigned"] == len(docs)
    assert labels[0] == labels[1] == labels[2] != UNASSIGNED
    assert labels[3] == labels[4] == labels[5] != UNASSIGNED
    assert labels[0] != labels[3]
    assert labels[6] == labels[7] == UNASSIGNED


def test_max_quantile_chains_distinct_groups() -> None:
    """分位点取满会把孤立项串进簇（单链串联），所以默认不用 1.0。"""
    docs = GROUP_A + GROUP_B + OUTLIERS
    space = FeatureSpace(DistillParams()).fit_transform(docs)
    eps = auto_eps(len(docs), space.pair_metric, min_samples=2, quantile=1.0)
    labels, _ = dbscan(len(docs), space.pair_metric, eps=eps, min_samples=2)
    assert UNASSIGNED not in labels


def test_dbscan_is_deterministic() -> None:
    docs = GROUP_A + GROUP_B
    space = FeatureSpace(DistillParams()).fit_transform(docs)
    first = dbscan(len(docs), space.pair_metric, eps=0.7, min_samples=2)[0]
    second = dbscan(len(docs), space.pair_metric, eps=0.7, min_samples=2)[0]
    assert first == second


def test_dbscan_rejects_bad_parameters() -> None:
    with pytest.raises(ValueError):
        dbscan(3, lambda i, j: 0.0, eps=-1.0, min_samples=2)
    with pytest.raises(ValueError):
        dbscan(3, lambda i, j: 0.0, eps=0.1, min_samples=0)


def test_auto_eps_ignores_duplicate_zero_distances() -> None:
    """同文条目距离为 0：不得把半径估成 0（历史实测缺陷）。"""
    docs = ["同一条文字条目"] * 6 + GROUP_A
    space = FeatureSpace(DistillParams()).fit_transform(docs)
    eps = auto_eps(len(docs), space.pair_metric, min_samples=2, quantile=0.5)
    assert eps > 0.0


def test_kth_distances_shape() -> None:
    docs = GROUP_A + OUTLIERS
    space = FeatureSpace(DistillParams()).fit_transform(docs)
    dists = kth_distances(len(docs), space.pair_metric, 1)
    assert len(dists) > 0
    assert dists == sorted(dists)


def test_profiles_medoid_intra_and_purity() -> None:
    docs = GROUP_A + GROUP_B + OUTLIERS
    space = FeatureSpace(DistillParams()).fit_transform(docs)
    labels, _ = dbscan(len(docs), space.pair_metric, eps=0.7, min_samples=2)
    truth = ["a"] * 3 + ["b"] * 3 + ["x"] * 2
    profiles = cluster_profiles(labels, space.pair_metric, truth=truth, texts=docs)
    assert profiles
    for prof in profiles:
        assert prof["size"] == len(prof["members"])
        assert prof["medoid"] in prof["members"]
        assert 0.0 <= float(prof["intra_similarity"]) <= 1.0
        assert float(prof["dominant_share"]) >= 0.5
    assert purity(truth, labels) > 0.9
    quality = cluster_quality(profiles, threshold=0.8)
    assert quality["clusters_judged"] == len(profiles)
    assert quality["consistency_ratio"] > 0.8


def test_medoid_and_intra_similarity_singleton() -> None:
    sim = lambda i, j: 0.5 if (i, j) != (0, 1) else 0.9  # noqa: E731
    assert medoid([0], sim) == 0
    assert intra_similarity([0], sim) == 1.0
    # 全并列时取最小索引（确定性并列规则）
    assert medoid([0, 1, 2], sim) == 0
    assert medoid([2, 1], lambda i, j: 1.0 if 2 in (i, j) else 0.0) == 2


def test_purity_ignores_unassigned() -> None:
    assert purity(["a", "b"], [UNASSIGNED, UNASSIGNED]) == 0.0
    assert purity(["a", "a"], [0, 0]) == 1.0