"""文本层：归一化 → 词项（拉丁词 + CJK 二元组）→ TF-IDF → cosine。

为什么自建而不用 sklearn
------------------------
* 本仓部署面（loong64 sandbox）无 sklearn；且平台铁律要求**零领域语义**——
  词典/停用词表一旦内置就有域词风险。这里用「df 上限过滤」代替停用词表：
  ``df > max_df_ratio * N`` 的词项被判为全库通用词，自动退出向量（纯统计，无词表）。
* 依赖只有 ``unicodedata`` / ``math`` / ``collections``，确定性可复现。

约定
----
* :func:`normalize` —— NFKC → 去排版记号 → 标点/符号/控制符折成空格 → 折叠空白 → casefold。
* :func:`tokens` —— 拉丁词（长度 ≥ ``min_latin_token_length``，大小写折叠）+ CJK 滑窗二元组
  （单字丢弃，避免「量/值/误差」这种单字主导相似度）。
* :class:`TfidfSpace` —— 语料级 TF-IDF（``tf = 1+log(count)`` 次线性、``idf = log((1+N)/(1+df))+1``
  平滑、可 L2 归一化），向量是 ``dict[int, float]`` 稀疏映射；cosine 用稀疏点积。
  空语料（``N=0``）合法：向量为空、cosine 恒 0，不抛异常（负控要求）。
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

_CJK_RANGE = (0x4E00, 0x9FFF)
_LATIN_KEEP = "._-+/"


def is_cjk(ch: str) -> bool:
    return _CJK_RANGE[0] <= ord(ch) <= _CJK_RANGE[1]


def normalize(text: str, markup_tokens: Sequence[str] = ()) -> str:
    """NFKC → 去排版记号 → 标点/符号/控制符折空格 → 折叠空白 → casefold。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    for token in markup_tokens:
        if token:
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


def _cjk_bigrams(run: str) -> list[str]:
    if len(run) < 2:
        return []
    return [run[i : i + 2] for i in range(len(run) - 1)]


def tokens(
    text: str,
    *,
    min_latin: int = 2,
    cjk_bigram: bool = True,
    cjk_unigram: bool = False,
    markup_tokens: Sequence[str] = (),
) -> list[str]:
    """拉丁词 + CJK 二元组（可选单字），保序可重复，供 TF 统计。"""
    s = normalize(text, markup_tokens)
    out: list[str] = []
    latin: list[str] = []
    cjk_run: list[str] = []

    def flush_latin() -> None:
        if latin:
            word = "".join(latin).strip("".join(_LATIN_KEEP))
            if len(word) >= min_latin:
                out.append(word)
            latin.clear()

    def flush_cjk() -> None:
        if cjk_run:
            run = "".join(cjk_run)
            if cjk_bigram:
                out.extend(_cjk_bigrams(run))
            if cjk_unigram:
                out.extend(run)
            cjk_run.clear()

    for ch in s:
        if ch.isspace():
            flush_latin()
            flush_cjk()
        elif ch.isascii() and (ch.isalnum() or ch in _LATIN_KEEP):
            flush_cjk()
            latin.append(ch)
        elif is_cjk(ch):
            flush_latin()
            cjk_run.append(ch)
        else:  # 归一化后不应出现，防御性处理
            flush_latin()
            flush_cjk()
    flush_latin()
    flush_cjk()
    return out


@dataclass
class TfidfSpace:
    """语料级 TF-IDF 向量空间（稀疏、确定性）。"""

    docs: list[str] = field(default_factory=list)
    sublinear_tf: bool = True
    idf_smoothing: float = 1.0
    min_df: int = 1
    max_df_ratio: float = 1.0
    l2_normalize: bool = True
    min_latin: int = 2
    cjk_unigram: bool = False
    markup_tokens: Sequence[str] = ()
    vocab: dict[str, int] = field(default_factory=dict)
    idf: list[float] = field(default_factory=list)
    vectors: list[dict[int, float]] = field(default_factory=list)
    dropped_terms: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # ---------------- 构建 ----------------
    def build(self, docs: Sequence[str]) -> "TfidfSpace":
        self.docs = list(docs)
        n_docs = len(self.docs)
        counts = [
            Counter(
                tokens(
                    d,
                    min_latin=self.min_latin,
                    cjk_unigram=self.cjk_unigram,
                    markup_tokens=self.markup_tokens,
                )
            )
            for d in self.docs
        ]
        df: Counter[str] = Counter()
        for c in counts:
            df.update(c.keys())
        max_df = self.max_df_ratio * n_docs if n_docs else 0.0
        keep = {
            term
            for term, d in df.items()
            if d >= self.min_df and (max_df <= 0 or d <= max_df)
        }
        if not keep and df:
            # 退化保护：语料内所有词项都超过 df 上限（如多条完全相同的文本）时，
            # 若继续过滤会得到全零向量、把所有对判成「不相似」。此时保留全部词项并留痕。
            keep = set(df)
            self.notes.append("max_df_filter_disabled_all_terms_generic")
        self.dropped_terms = sorted(t for t in df if t not in keep)
        self.vocab = {term: i for i, term in enumerate(sorted(keep))}
        self.idf = [
            math.log((1.0 + n_docs) / (1.0 + df[t])) + self.idf_smoothing
            for t in self.vocab
        ]
        self.vectors = [self._vectorize_counts(c) for c in counts]
        return self

    def _vectorize_counts(self, counts: Mapping[str, int]) -> dict[int, float]:
        vec: dict[int, float] = {}
        for term, cnt in counts.items():
            idx = self.vocab.get(term)
            if idx is None:
                continue
            tf = 1.0 + math.log(cnt) if self.sublinear_tf else float(cnt)
            vec[idx] = tf * self.idf[idx]
        if self.l2_normalize:
            norm = math.sqrt(sum(v * v for v in vec.values()))
            if norm > 0:
                vec = {k: v / norm for k, v in vec.items()}
        return vec

    def vectorize(self, text: str) -> dict[int, float]:
        """把任意文本投影到**已建**空间（未见词项忽略；空文本 → 空向量）。"""
        return self._vectorize_counts(
            Counter(
                tokens(
                    text,
                    min_latin=self.min_latin,
                    cjk_unigram=self.cjk_unigram,
                    markup_tokens=self.markup_tokens,
                )
            )
        )

    # ---------------- 相似度 ----------------
    @staticmethod
    def cosine(a: Mapping[int, float], b: Mapping[int, float]) -> float:
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        dot = sum(w * b.get(k, 0.0) for k, w in a.items())
        return max(0.0, min(1.0, dot))

    def sim(self, i: int, j: int) -> float:
        return self.cosine(self.vectors[i], self.vectors[j])

    # ---------------- 可解释性 ----------------
    def top_terms(self, vec: Mapping[int, float], k: int = 8) -> list[tuple[str, float]]:
        rev = self.inverse_vocab
        items = sorted(vec.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(rev[i], w) for i, w in items[:k]]

    def shared_terms(
        self, a: Mapping[int, float], b: Mapping[int, float], k: int = 6
    ) -> list[tuple[str, float]]:
        rev = self.inverse_vocab
        shared = [
            (idx, wa * b[idx])
            for idx, wa in a.items()
            if idx in b
        ]
        shared.sort(key=lambda kv: (-kv[1], kv[0]))
        return [(rev[i], w) for i, w in shared[:k]]

    @property
    def inverse_vocab(self) -> list[str]:
        if not hasattr(self, "_inv") or len(getattr(self, "_inv", [])) != len(self.vocab):
            inv = [""] * len(self.vocab)
            for term, idx in self.vocab.items():
                inv[idx] = term
            self._inv = inv
        return self._inv

    @property
    def n_docs(self) -> int:
        return len(self.docs)


def build_space(docs: Iterable[str], cfg: Mapping) -> TfidfSpace:
    """按配置构建空间（``cfg`` = 配置里 ``text``+``tfidf`` 两段合并后的 dict）。"""
    text_cfg = cfg.get("text", {}) or {}
    tfidf_cfg = cfg.get("tfidf", {}) or {}
    space = TfidfSpace(
        sublinear_tf=bool(tfidf_cfg.get("sublinear_tf", True)),
        idf_smoothing=float(tfidf_cfg.get("idf_smoothing", 1.0)),
        min_df=int(tfidf_cfg.get("min_df", 1)),
        max_df_ratio=float(tfidf_cfg.get("max_df_ratio", 1.0)),
        l2_normalize=bool(tfidf_cfg.get("l2_normalize", True)),
        min_latin=int(text_cfg.get("min_latin_token_length", 2)),
        cjk_unigram=bool(text_cfg.get("keep_single_cjk", False)),
        markup_tokens=tuple(text_cfg.get("markup_tokens", ()) or ()),
    )
    return space.build(list(docs))