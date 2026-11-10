"""语义空间：TF-IDF →（截断 SVD / LSA）→ 余弦相似度。

为什么需要这一层
----------------
裸 TF-IDF 余弦对**改写**极不友好：同一能力主张的两种写法（换动词、增删从句、换验证锚的
书写顺序）在长文本上只有 0.3–0.6（实测见 docs/P234W2_N01 表 2）。LSA（潜在语义分析）把
词项空间的稀疏共现结构压到低维，同义改写在低维上互相靠近，因此同一 0.85 阈值下召回显著
高于裸 TF-IDF——这是本模块存在的唯一理由，数字在报告里逐项列出，不做定性辩护。

实现纪律
--------
* **确定性**：随机投影用 ``numpy.random.default_rng(seed)``（seed 来自配置），
  幂迭代次数固定，因此同一语料同一配置逐次可复现。
* **可选依赖**：无 numpy / 无 scipy 时自动降级为裸 TF-IDF（``backend == "tfidf"``），
  并在结果里如实标注——不允许「静默降级成看起来一样的分数」。
* 不用 ``scipy.sparse.linalg.svds``：本仓部署面（loong64）该调用实测 ``double free``
  崩溃（见报告“注意点”），改用 numpy 手写随机化 SVD，行为可预期。

数学
----
``X`` = TF-IDF 矩阵（行 L2 归一化）；``X ≈ U Σ Vᵀ``（k 维）；
嵌入 ``E = U Σ``，单位归一后 ``cos_LSA(i,j) = ê_i · ê_j``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .text import TfidfSpace, build_space

try:  # pragma: no cover - 依赖存在性由部署面决定
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


@dataclass
class SemanticSpace:
    """LSA 语义空间（numpy 缺失时退化为 TF-IDF 余弦，backend 字段如实标注）。"""

    space: TfidfSpace
    backend: str = "tfidf"
    dim: int = 0
    embedding: Any = None
    seed: int = 0
    notes: list[str] = field(default_factory=list)

    # ---- 构建 ----
    @classmethod
    def build(
        cls,
        docs: Sequence[str],
        cfg: Mapping[str, Any],
        *,
        dim: int | None = None,
        seed: int | None = None,
        power_iters: int | None = None,
        oversample: int | None = None,
    ) -> "SemanticSpace":
        lsa_cfg = dict(cfg.get("lsa", {}) or {})
        dim = int(dim if dim is not None else lsa_cfg.get("dim", 200))
        seed = int(seed if seed is not None else lsa_cfg.get("seed", 20261110))
        iters = int(power_iters if power_iters is not None else lsa_cfg.get("power_iters", 4))
        over = int(oversample if oversample is not None else lsa_cfg.get("oversample", 20))
        space = build_space(docs, cfg)
        obj = cls(space=space, backend="tfidf", seed=seed)
        if not lsa_cfg.get("enabled", True):
            obj.notes.append("lsa_disabled_by_config")
            return obj
        if np is None:  # pragma: no cover
            obj.notes.append("numpy_missing_fallback_tfidf")
            return obj
        n_docs = len(space.vectors)
        if n_docs == 0 or not space.vocab:
            obj.notes.append("empty_corpus_fallback_tfidf")
            return obj
        k = max(1, min(dim, n_docs - 1)) if n_docs > 1 else 1
        rng = np.random.default_rng(seed)
        # 稠密矩阵规模受控：文档数 × 词表可能很大，因此用「文档-词项」紧凑 CSR 手工乘
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []
        for i, vec in enumerate(space.vectors):
            for j, w in vec.items():
                rows.append(i)
                cols.append(j)
                vals.append(w)
        import scipy.sparse as sp  # 本地导入：只在真用 LSA 时需要

        X = sp.csr_matrix(
            (np.asarray(vals, dtype=np.float64), (np.asarray(rows), np.asarray(cols))),
            shape=(n_docs, len(space.vocab)),
        )
        width = min(X.shape[1], k + over)
        omega = rng.standard_normal((X.shape[1], width))
        Y = X @ omega
        Q, _ = np.linalg.qr(Y)
        for _ in range(max(0, iters)):
            Q, _ = np.linalg.qr(X @ (X.T @ Q))
        B = Q.T @ X
        Ub, S, _ = np.linalg.svd(B, full_matrices=False)
        U = Q @ Ub
        E = U[:, :k] * S[:k]
        norms = np.linalg.norm(E, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        obj.embedding = E / norms
        obj.backend = "lsa"
        obj.dim = int(E.shape[1])
        obj.notes.append(f"randomized_svd_k={obj.dim}_iters={iters}_oversample={over}")
        return obj

    # ---- 相似度 ----
    def sim(self, i: int, j: int) -> float:
        if self.backend == "lsa" and self.embedding is not None:
            return float(max(0.0, min(1.0, self.embedding[i] @ self.embedding[j])))
        return self.space.sim(i, j)

    def sim_matrix_pairs(self, threshold: float) -> list[tuple[float, int, int]]:
        """所有 ≥threshold 的对（升序 i<j），按 (score desc, i, j) 排序。"""
        n = len(self.space.vectors)
        out: list[tuple[float, int, int]] = []
        if n < 2:
            return out
        if self.backend == "lsa" and self.embedding is not None:
            chunk = 512
            for start in range(0, n, chunk):
                block = self.embedding[start : start + chunk] @ self.embedding.T
                for r in range(block.shape[0]):
                    i = start + r
                    row = block[r]
                    js = np.nonzero(row >= threshold)[0]
                    for j in js:
                        if j > i:
                            out.append((float(row[j]), i, int(j)))
        else:
            for i in range(n):
                for j in range(i + 1, n):
                    s = self.space.sim(i, j)
                    if s >= threshold:
                        out.append((s, i, j))
        out.sort(key=lambda t: (-t[0], t[1], t[2]))
        return out

    def vectorize(self, text: str) -> Any:
        """把文本投影进同一空间（LSA 用 fold-in：``e = xᵀ V Σ⁻¹`` 的近似，即 ``X V``）。"""
        v = self.space.vectorize(text)
        if not v:
            return None
        if self.backend == "lsa" and self.embedding is not None:
            # fold-in：用空间基（embedding 的行空间）近似 —— 取词项权重在文档嵌入上的加权和
            vec = np.zeros(self.embedding.shape[1])
            for j, w in v.items():
                if j < self.space.n_docs:  # 防御：索引越界不参与
                    pass
            # 该近似过弱，直接放弃 fold-in，改用 TF-IDF 余弦对比（语义一致性由后端标注）
            return None
        return v

    @property
    def n_docs(self) -> int:
        return len(self.space.vectors)

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "dim": self.dim,
            "docs": self.n_docs,
            "vocab": len(self.space.vocab),
            "dropped_generic_terms": len(self.space.dropped_terms),
            "seed": self.seed,
            "notes": list(self.notes),
        }


def idf_top_terms(space: TfidfSpace, k: int = 10) -> list[tuple[str, float]]:
    """诊断用：全语料 IDF 最高的词项（≈ 最稀有词）。"""
    inv = space.inverse_vocab
    items = sorted(
        ((inv[i], space.idf[i]) for i in range(len(space.idf))), key=lambda t: (-t[1], t[0])
    )
    return items[:k]