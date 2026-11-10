"""特征空间：TF-IDF（字符 n-gram）+ 子词哈希嵌入（确定性，无训练、无外部模型）。

为什么不用分词器
----------------
条目跨中英混排、跨作者、跨工具生成，没有统一分词器；字符 n-gram 不需要词典，
天然满足「零领域词表」纪律。字符 n-gram 上做 TF-IDF，等价于把"哪些子串在语料里
稀有"当作词汇权重——重复模板句（"本节点未完成…"）会自动降权。

嵌入是什么、不是什么
--------------------
:class:`HashedEmbedding` 是 **fastText 风格子词哈希**：每个字符 n-gram 用
``blake2b`` 映射到固定维度、带符号累加，再做 L2 归一化。它给出的是**子词级**
近似语义（共享子串 → 高余弦），**不是**预训练语义模型：没有同义改写的召回能力。
接口留了外部注入口（:meth:`HashedEmbedding.from_vectors`），真实语义向量可按
``{item_id: [float, ...]}`` 注入替换，报告里必须注明本次用的是哪一档（诚实披露）。

组合相似度
----------
三路互补通道取最大（与平台既有 ``tools/proposal_seeding/match.py`` 的"多通道取最大"同一
口径）：

* ``char_cos  = w * cos_tfidf + (1 - w) * cos_emb``（字面子串近似）；
* ``jaccard`` 字符 3-gram 集合 Jaccard（对模板句敏感）；
* ``token_similarity`` —— 词元（ASCII 词 + CJK 二元字）通道：IDF 加权 Jaccard 与
  「长度比达标时的加权包含度」取最大（``Σidf(A∩B)/min(...)`` 式包含度在长度悬殊时
  会假性给满分，故设长度比门）。为什么需要它：同义改写的**字符级**相似度实测
  只有 0.2~0.4（见报告 §3），而分词元后同一缺口的包含度可达 0.2~0.4、跨议题 ≈0，
  单通道会把"同一缺口的不同写法"判成不相关。

三路值与最终 ``pair_metric`` 都进输出，便于复核哪一路在起作用。
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

from .params import DistillParams

_MARKUP_TOKENS: tuple[str, ...] = ("**", "__", "`", "~~", "##", "#")


def normalize_text(text: str) -> str:
    """NFKC → 去排版记号 → 标点/符号/控制符折空格 → 折叠空白 → casefold。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    for token in _MARKUP_TOKENS:
        s = s.replace(token, " ")
    out: list[str] = []
    for ch in s:
        if ch.isspace():
            out.append(" ")
            continue
        if unicodedata.category(ch)[0] in ("P", "S", "C", "Z"):
            out.append(" ")
        else:
            out.append(ch.casefold())
    return " ".join("".join(out).split())


def compact(text: str) -> str:
    return normalize_text(text).replace(" ", "")


def char_ngrams(text: str, n_min: int, n_max: int) -> list[str]:
    """字符 n-gram 列表（保留重复计数供 TF 使用；n 超出文本长度时退化为整串）。"""
    body = compact(text)
    if not body:
        return []
    grams: list[str] = []
    for n in range(n_min, n_max + 1):
        if n >= len(body):
            grams.append(body)
            break
        grams.extend(body[i:i + n] for i in range(len(body) - n + 1))
    return grams


def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """稀疏向量余弦（只遍历较短的一侧）。"""
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


_TOKEN_RX = re.compile(r"[a-z0-9]+")


def token_units(text: str) -> list[str]:
    """词元：ASCII 词 + CJK 二元字（中文无词边界，二元字是无需词典的最小语义单元）。"""
    low = normalize_text(text)
    toks = _TOKEN_RX.findall(low)
    cjk = "".join(ch for ch in low if "\u4e00" <= ch <= "\u9fff")
    if len(cjk) == 1:
        toks.append(cjk)
    else:
        toks.extend(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return toks


class TokenIndex:
    """词元 IDF 表 + IDF 加权包含度（同义改写的召回通道）。"""

    def __init__(self) -> None:
        self.idf: dict[str, float] = {}
        self.default_idf = 1.0

    def fit(self, docs: Sequence[str]) -> "TokenIndex":
        df: dict[str, int] = {}
        for doc in docs:
            for tok in set(token_units(doc)):
                df[tok] = df.get(tok, 0) + 1
        n = len(docs)
        self.idf = {tok: math.log((1 + n) / (1 + c)) + 1.0 for tok, c in df.items()}
        self.default_idf = math.log((1 + n) / 1) + 1.0
        return self

    def weights(self, text: str) -> dict[str, float]:
        return {tok: self.idf.get(tok, self.default_idf)
                for tok in sorted(set(token_units(text)))}

    @staticmethod
    def containment(a: Mapping[str, float], b: Mapping[str, float]) -> float:
        """``Σidf(A∩B) / min(Σidf(A), Σidf(B))``：短文本被长文本覆盖的比例。"""
        if not a or not b:
            return 0.0
        inter = sum(w for tok, w in a.items() if tok in b)
        denom = min(sum(a.values()), sum(b.values()))
        return inter / denom if denom else 0.0

    @staticmethod
    def weighted_jaccard(a: Mapping[str, float], b: Mapping[str, float]) -> float:
        """``Σidf(A∩B) / Σidf(A∪B)``：对称，短片段不会被长句"完全包含"式拉满。"""
        if not a or not b:
            return 0.0
        inter = sum(w for tok, w in a.items() if tok in b)
        union = sum(a.values()) + sum(b.values()) - inter
        return inter / union if union else 0.0

    def similarity(self, a: Mapping[str, float], b: Mapping[str, float],
                   min_length_ratio: float = 0.5) -> float:
        """词元通道：加权 Jaccard 与「长度比达标时的包含度」取最大。

        长度比门控是必需的：短片段（如小节小标题残片）与长句共享一个词元时
        包含度会直接给 1.0，会把完全无关的条目串成长链（实测发生过，见报告 §5）。
        """
        wj = self.weighted_jaccard(a, b)
        if not a or not b:
            return wj
        ratio = min(len(a), len(b)) / max(len(a), len(b))
        if ratio >= min_length_ratio:
            return max(wj, self.containment(a, b))
        return wj


def ngram_jaccard(a: str, b: str, n_min: int = 3, n_max: int = 3) -> float:
    """字符 n-gram **集合** Jaccard（与 TF-IDF 的加权余弦互补：对模板句更敏感）。"""
    sa = set(char_ngrams(a, n_min, n_max))
    sb = set(char_ngrams(b, n_min, n_max))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class Tfidf:
    """字符 n-gram TF-IDF。

    权重：``tf = 1 + log(count)``；``idf = log((1 + N) / (1 + df)) + 1``；
    行向量 L2 归一化（余弦可直接点积）。语料为空时 transform 返回空向量。
    """

    def __init__(self, params: DistillParams | None = None) -> None:
        self.params = (params or DistillParams()).validate()
        self.vocab: dict[str, int] = {}
        self.idf: list[float] = []
        self.doc_count = 0

    def fit(self, docs: Sequence[str]) -> "Tfidf":
        df: dict[str, int] = {}
        for doc in docs:
            for gram in set(char_ngrams(doc, self.params.ngram_min, self.params.ngram_max)):
                df[gram] = df.get(gram, 0) + 1
        self.doc_count = len(docs)
        self.vocab = {gram: i for i, gram in enumerate(sorted(df))}
        self.idf = [math.log((1 + self.doc_count) / (1 + df[g])) + 1.0
                    for g in sorted(df)]
        return self

    def transform(self, text: str) -> dict[str, float]:
        counts: dict[str, int] = {}
        for gram in char_ngrams(text, self.params.ngram_min, self.params.ngram_max):
            if gram in self.vocab:
                counts[gram] = counts.get(gram, 0) + 1
        vec: dict[str, float] = {}
        for gram, count in counts.items():
            vec[gram] = (1.0 + math.log(count)) * self.idf[self.vocab[gram]]
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm:
            vec = {k: v / norm for k, v in vec.items()}
        return vec


def _hash32(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(),
                          "big")


class HashedEmbedding:
    """子词哈希嵌入（确定性）：字符 n-gram → 桶号 + 符号 → 累加 → L2 归一化。"""

    def __init__(self, dim: int = 256, n_min: int = 2, n_max: int = 3) -> None:
        if dim < 8:
            raise ValueError("dim 过小")
        if n_min < 1 or n_max < n_min:
            raise ValueError("n-gram 区间非法")
        self.dim = dim
        self.n_min = n_min
        self.n_max = n_max

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        grams = char_ngrams(text, self.n_min, self.n_max)
        if not grams:
            return vec
        for gram in grams:
            h = _hash32(gram)
            idx = h % self.dim
            vec[idx] += 1.0 if (h >> 31) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    @staticmethod
    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    @classmethod
    def from_vectors(cls, vectors: Mapping[str, Sequence[float]]) -> "HashedEmbedding":
        """外部注入语义向量时的占位校验器（维度一致性检查）。"""
        dims = {len(v) for v in vectors.values()}
        if len(dims) > 1:
            raise ValueError(f"注入向量维度不一致: {sorted(dims)}")
        return cls(dim=dims.pop() if dims else 256)


class FeatureSpace:
    """把条目文本映射到 (TF-IDF, 哈希嵌入) 双表示，并提供组合相似度。"""

    def __init__(self, params: DistillParams | None = None) -> None:
        self.params = (params or DistillParams()).validate()
        self.tfidf = Tfidf(self.params)
        self.embedding = HashedEmbedding(self.params.emb_dim,
                                         self.params.ngram_min, self.params.ngram_max)
        self.token_index = TokenIndex()
        self.texts: list[str] = []
        self._tfidf_vecs: list[dict[str, float]] = []
        self._emb_vecs: list[list[float]] = []
        self._tok_vecs: list[dict[str, float]] = []
        self._gram_sets: list[frozenset[str]] = []

    def fit_transform(self, texts: Sequence[str]) -> "FeatureSpace":
        self.texts = list(texts)
        self.tfidf.fit(self.texts)
        self.token_index.fit(self.texts)
        self._tfidf_vecs = [self.tfidf.transform(t) for t in self.texts]
        self._emb_vecs = [self.embedding.embed(t) for t in self.texts]
        self._tok_vecs = [self.token_index.weights(t) for t in self.texts]
        self._gram_sets = [frozenset(char_ngrams(t, self.params.ngram_min,
                                                 self.params.ngram_max))
                           for t in self.texts]
        return self

    def __len__(self) -> int:
        return len(self.texts)

    def tfidf_vector(self, index: int) -> Mapping[str, float]:
        return self._tfidf_vecs[index]

    def emb_vector(self, index: int) -> Sequence[float]:
        return self._emb_vecs[index]

    def similarity(self, i: int, j: int) -> float:
        w = self.params.tfidf_weight
        cos_t = max(0.0, cosine(self._tfidf_vecs[i], self._tfidf_vecs[j]))
        cos_e = max(0.0, HashedEmbedding.cosine(self._emb_vecs[i], self._emb_vecs[j]))
        return w * cos_t + (1.0 - w) * cos_e

    def components(self, i: int, j: int) -> dict[str, float]:
        cos_t = max(0.0, cosine(self._tfidf_vecs[i], self._tfidf_vecs[j]))
        cos_e = max(0.0, HashedEmbedding.cosine(self._emb_vecs[i], self._emb_vecs[j]))
        return {"tfidf_cos": round(cos_t, 6), "emb_cos": round(cos_e, 6),
                "combined": round(self.similarity(i, j), 6)}

    def jaccard(self, i: int, j: int) -> float:
        a, b = self._gram_sets[i], self._gram_sets[j]
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def token_similarity(self, i: int, j: int) -> float:
        return round(self.token_index.similarity(
            self._tok_vecs[i], self._tok_vecs[j], self.params.token_min_length_ratio), 6)

    def token_containment(self, i: int, j: int) -> float:
        return round(self.token_index.containment(self._tok_vecs[i], self._tok_vecs[j]), 6)

    def pair_metric(self, i: int, j: int) -> float:
        """综合相似度（聚类/门控共用）：三路互补通道取最大。"""
        return max(self.similarity(i, j), self.jaccard(i, j), self.token_similarity(i, j))

    def full_components(self, i: int, j: int) -> dict[str, float]:
        comp = self.components(i, j)
        comp["jaccard"] = round(self.jaccard(i, j), 6)
        comp["token_similarity"] = self.token_similarity(i, j)
        comp["pair_metric"] = round(self.pair_metric(i, j), 6)
        return comp


__all__ = [
    "FeatureSpace",
    "HashedEmbedding",
    "Tfidf",
    "TokenIndex",
    "token_units",
    "char_ngrams",
    "compact",
    "cosine",
    "ngram_jaccard",
    "normalize_text",
]