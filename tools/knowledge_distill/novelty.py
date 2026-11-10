"""新颖性门控：候选提案种子 vs 既有提案池 / 已验证案例。

为什么需要门控
--------------
蒸馏器最容易产出的不是新想法，而是**已经存在过的想法换了个说法**：局限条目里 80% 的
"下一步"与提案池里的历史提案高度同义。把重复提案投进池子只会占掉槽位、污染池面，
比不提案更糟（判例 S-N 轨）。因此输出前必须与参考池做三路比较并留证据：

* ``jaccard``  —— 字符 n-gram 集合 Jaccard（对模板句最敏感）；
* ``tfidf_cos``—— 在同一参考语料上拟合的 TF-IDF 余弦（对稀有子串加权）；
* ``emb_cos``  —— 子词哈希嵌入余弦（对语序改写更宽容）；
* ``token_containment`` —— 词元 IDF 加权包含度（同义改写的主召回通道）。
* ``sim = max(jaccard, w*tfidf_cos + (1-w)*emb_cos)``，再取参考池最大值
  ``max_sim``；``novelty = 1 - max_sim``。

两条硬约束（缺一不可）
----------------------
1. ``novelty ≥ novelty_min``（默认 0.55，即与最近邻相似度 ≤ 0.45）；
2. 与**全部**参考条目的平均相似度 ``mean_sim ≤ mean_sim_max``（默认 0.40）——
   只卡最大值会放行"一条像、其余全像模板"的伪新颖条目。

参考池读取
----------
目录/文件都吃；JSON/JSONL 递归抽取文本键（标题/目标/理由/摘要/正文…），markdown 按
段落切块（避免整篇长文把余弦稀释成"处处不相似"）。每个参考条目带路径与 sha256，
匹配结果可回原文核对。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .extract import redact
from .features import HashedEmbedding, Tfidf, TokenIndex, ngram_jaccard
from .params import DistillParams

TEXT_KEYS = ("title", "name", "why_frontier", "goal", "physical_objective", "summary",
             "text", "description", "gap_description", "objective", "rationale",
             "next_step", "limitation", "honest_fail")
MAX_REF_CHARS = 600
MIN_REF_CHARS = 8


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        text = " ".join(node.split())
        if len(text) >= MIN_REF_CHARS:
            out.append(text[:MAX_REF_CHARS])
        return
    if isinstance(node, Mapping):
        keyed = [node[k] for k in TEXT_KEYS if k in node]
        if keyed:
            for sub in keyed:
                _strings(sub, out)
            return
        for value in node.values():
            _strings(value, out)
        return
    if isinstance(node, Iterable) and not isinstance(node, (bytes, bytearray)):
        for sub in node:
            _strings(sub, out)


def _markdown_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if buf:
                blocks.append(" ".join(buf).strip())
                buf = []
            continue
        buf.append(line.strip().lstrip("#").strip())
    if buf:
        blocks.append(" ".join(buf).strip())
    out: list[str] = []
    for block in blocks:
        for start in range(0, len(block), MAX_REF_CHARS):
            chunk = block[start:start + MAX_REF_CHARS].strip()
            if len(chunk) >= MIN_REF_CHARS:
                out.append(chunk)
    return out


class ReferenceCorpus:
    """既有提案/案例的文本参考池（按需惰性建索引）。"""

    def __init__(self, params: DistillParams | None = None) -> None:
        self.params = (params or DistillParams()).validate()
        self.entries: list[dict[str, Any]] = []
        self._tfidf = Tfidf(self.params)
        self._emb = HashedEmbedding(self.params.emb_dim, self.params.ngram_min,
                                    self.params.ngram_max)
        self._tokens = TokenIndex()
        self._tfidf_vecs: list[Mapping[str, float]] = []
        self._emb_vecs: list[Sequence[float]] = []
        self._tok_vecs: list[Mapping[str, float]] = []
        self._ensured = False

    # ---------------------------------------------------------------- 读取
    def add_paths(self, paths: Iterable[str | Path]) -> int:
        added = 0
        for raw in paths:
            p = Path(raw)
            files = sorted(p.rglob("*")) if p.is_dir() else [p]
            for f in files:
                if not f.is_file() or f.suffix.lower() not in (".json", ".jsonl", ".md", ".txt"):
                    continue
                added += self._add_file(f)
        self._ensured = False
        return added

    def _add_texts(self, texts: Sequence[str], base: str, digest: str) -> int:
        added = 0
        for text in texts:
            text, _ = redact(text)  # 产物与报告要落库，参考池同样先脱敏
            self.entries.append({
                "ref_id": f"{base}#{added}",
                "text": text,
                "source_path": base.split("#")[0],
                "source_sha256": digest,
            })
            added += 1
        return added

    def _add_file(self, path: Path) -> int:
        try:
            raw = path.read_bytes()
        except OSError:
            return 0
        digest = _sha256_bytes(raw)
        text = raw.decode("utf-8", errors="replace")
        if path.suffix.lower() == ".md" or path.suffix.lower() == ".txt":
            return self._add_texts(_markdown_blocks(text), str(path), digest)
        texts: list[str] = []
        if path.suffix.lower() == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    _strings(json.loads(line), texts)
                except json.JSONDecodeError:
                    continue
        else:
            try:
                _strings(json.loads(text), texts)
            except json.JSONDecodeError:
                return 0
        return self._add_texts(texts, str(path), digest)

    # ---------------------------------------------------------------- 索引
    def ensure_index(self) -> "ReferenceCorpus":
        if self._ensured:
            return self
        self._tfidf = Tfidf(self.params).fit([e["text"] for e in self.entries])
        self._tfidf_vecs = [self._tfidf.transform(e["text"]) for e in self.entries]
        self._emb_vecs = [self._emb.embed(e["text"]) for e in self.entries]
        self._tokens.fit([e["text"] for e in self.entries])
        self._tok_vecs = [self._tokens.weights(e["text"]) for e in self.entries]
        self._ensured = True
        return self

    def __len__(self) -> int:
        return len(self.entries)

    def max_similarity(self, text: str, top_k: int | None = 128) -> dict[str, Any]:
        """与参考池比对：词元通道全量精确 + 头部候选取三通道精确。

        性能事实（实测 1.7 万条参考）：逐条算字符 n-gram Jaccard 单条查询 6.8 s，
        全量跑 21 个候选要 140 s。故分两级：词元通道对**全部**参考精确计算（便宜），
        取前 ``top_k`` 条再算字符级 Jaccard / TF-IDF 余弦 / 嵌入余弦（贵）。
        头部候选之外条目的相似度取词元通道值（保守下界：三通道取最大不会更小），
        因此 ``max_sim`` 在"字符级完全相同 ⇒ 词元也必然高"的实际语料上等价于全量精确；
        等价性抽检口径见报告 §3。
        """
        self.ensure_index()
        if not self.entries:
            return {"max_sim": 0.0, "mean_sim": 0.0, "jaccard": 0.0, "tfidf_cos": 0.0,
                    "emb_cos": 0.0, "token_containment": 0.0, "token_sim": 0.0,
                    "match_ref_id": None, "match_text": None, "ref_count": 0,
                    "exact_refs": 0}
        tok = self._tokens.weights(text)
        ratio = self.params.token_min_length_ratio
        tok_sims = [self._tokens.similarity(tok, v, ratio) for v in self._tok_vecs]
        k = len(self.entries) if top_k is None else max(1, min(top_k, len(self.entries)))
        if k == len(self.entries) and len(self.entries) > 2000:
            raise ValueError(f"全量精确比对参考池过大（{len(self.entries)} 条）：请设 top_k")
        order = sorted(range(len(self.entries)), key=lambda i: (-tok_sims[i], i))[:k]
        vec = self._tfidf.transform(text)
        emb = self._emb.embed(text)
        w = self.params.tfidf_weight
        # 均值口径：头部候选 + 等距抽样（确定性）上做三通道精确，其余条目取词元通道；
        # 抽样集合固定（步长 = N/256），同一输入逐次相同。
        stride = max(1, len(self.entries) // 256)
        sampled = list(range(0, len(self.entries), stride))[:256]
        exact = sorted(set(order) | set(sampled))
        per_ref = list(tok_sims)
        best = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, order[0])
        for idx in exact:
            entry = self.entries[idx]
            jac = ngram_jaccard(text, entry["text"], self.params.ngram_min,
                                self.params.ngram_max)
            cos_t = max(0.0, _cosine_sparse(vec, self._tfidf_vecs[idx]))
            cos_e = max(0.0, HashedEmbedding.cosine(emb, self._emb_vecs[idx]))
            cont = self._tokens.containment(tok, self._tok_vecs[idx])
            combined = max(jac, w * cos_t + (1.0 - w) * cos_e, tok_sims[idx])
            per_ref[idx] = combined
            if combined > best[0] + 1e-12:
                best = (combined, jac, cos_t, cos_e, cont, tok_sims[idx], idx)
        match = self.entries[best[6]]
        return {
            "max_sim": round(best[0], 6),
            "mean_sim": round(sum(per_ref) / len(per_ref), 6),
            "jaccard": round(best[1], 6),
            "tfidf_cos": round(best[2], 6),
            "emb_cos": round(best[3], 6),
            "token_containment": round(best[4], 6),
            "token_sim": round(best[5], 6),
            "match_ref_id": match["ref_id"],
            "match_source": match["source_path"],
            "match_sha256": match["source_sha256"],
            "match_text": match["text"][:160],
            "ref_count": len(self.entries),
            "exact_refs": len(exact),
            "mean_sim_exact_refs": len(exact),
        }


def _cosine_sparse(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    from .features import cosine  # 局部导入避免循环
    return cosine(a, b)


def gate(candidates: Sequence[Mapping[str, Any]], corpus: ReferenceCorpus,
         params: DistillParams | None = None, *, top_k: int = 128) -> dict[str, Any]:
    """对候选种子做新颖性门控。

    ``candidates``: 至少含 ``seed_id`` 与 ``novelty_text``（用于比较的文本：标题+缺口+代表引用）。
    """
    params = (params or DistillParams()).validate()
    corpus.ensure_index()
    decisions: list[dict[str, Any]] = []
    for cand in candidates:
        text = str(cand.get("novelty_text") or cand.get("gap_description") or
                   cand.get("title") or "")
        m = corpus.max_similarity(text, top_k=top_k)
        novelty = round(1.0 - m["max_sim"], 6)
        passed = (novelty >= params.novelty_min) and (m["mean_sim"] <= params.mean_sim_max)
        reasons: list[str] = []
        if novelty < params.novelty_min:
            reasons.append(f"novelty {novelty:.3f} < {params.novelty_min}")
        if m["mean_sim"] > params.mean_sim_max:
            reasons.append(f"mean_sim {m['mean_sim']:.3f} > {params.mean_sim_max}")
        decisions.append({
            "seed_id": cand.get("seed_id"),
            "novelty": novelty,
            "passed": passed,
            "reasons": reasons,
            **m,
        })
    passed_ids = [d["seed_id"] for d in decisions if d["passed"]]
    sims = [d["max_sim"] for d in decisions if d["passed"]]
    means = [d["mean_sim"] for d in decisions if d["passed"]]
    return {
        "novelty_min": params.novelty_min,
        "mean_sim_max": params.mean_sim_max,
        "candidates": len(decisions),
        "passed": len(passed_ids),
        "passed_ids": passed_ids,
        "mean_max_sim_of_passed": round(sum(sims) / len(sims), 6) if sims else 0.0,
        "mean_sim_of_passed": round(sum(means) / len(means), 6) if means else 0.0,
        "max_sim_of_passed": round(max(sims), 6) if sims else 0.0,
        "decisions": decisions,
    }


__all__ = ["ReferenceCorpus", "gate"]