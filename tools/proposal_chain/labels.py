"""标注对的候选采样与评估（人工标注的机器侧脚手架）。

采样的**独立性**纪律
--------------------
候选对不能按“去重器自己的分数”抽取，否则精确率/召回率评估就是循环论证。
这里只用与 TF-IDF cosine 无关的三条结构规则抽候选（规则名与参数来自配置）：

* ``shared_rare_term``：两文档共享 ≥ ``min_shared_rare_terms`` 个稀有词项（df ≤ ``rare_df_cap``）；
* ``same_repo_pair``：同仓、不同 campaigns 的随机对；
* ``cross_repo_pair``：不同仓的随机对（负控主要来源）。

抽样结果写成待标注清单（``candidates``），人工/智能体逐对标注 ``label ∈ {overlap, distinct}``
与 ``reason``；标注文件是**证据工件**（带 sha256），评估器只读标注文件，不再看任何分数。

评估口径
--------
* 召回率 = 正对中被判重复（score ≥ dup_threshold）的比例；
* 误判率 = 负对被判重复的比例（FP rate）；
* 复核带（``review_threshold ≤ score < dup_threshold``）单列统计——既不进召回也不进误判，
  它代表“拦下来给人看”的流量。
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .dedup import Doc
from .text import TfidfSpace, build_space


def _df_map(space: TfidfSpace) -> dict[int, int]:
    df = {i: 0 for i in range(len(space.vocab))}
    for vec in space.vectors:
        for idx in vec:
            df[idx] += 1
    return df


def sample_candidates(
    docs: Sequence[Doc],
    cfg: Mapping[str, Any],
    *,
    n_target: int = 140,
    seed: int = 20261110,
) -> list[dict[str, Any]]:
    """按结构规则抽候选对（不看分数），返回带来源规则的候选清单。"""
    lab = dict(cfg.get("labels", {}) or {})
    rare_df_cap = int(lab.get("rare_df_cap", 12))
    min_shared = int(lab.get("min_shared_rare_terms", 2))
    space = build_space([d.text for d in docs], cfg)
    df = _df_map(space)
    rare = {i for i, d in df.items() if d <= rare_df_cap}
    buckets: dict[int, list[int]] = {}
    for i, vec in enumerate(space.vectors):
        for idx in vec:
            if idx in rare:
                buckets.setdefault(idx, []).append(i)
    counts: dict[tuple[int, int], int] = {}
    for idx in sorted(buckets):
        members = buckets[idx]
        if len(members) < 2 or len(members) > 200:
            continue
        for a_pos in range(len(members)):
            for b in members[a_pos + 1 :]:
                key = (members[a_pos], b) if members[a_pos] < b else (b, members[a_pos])
                counts[key] = counts.get(key, 0) + 1
    shared_rare = [
        (k, c) for k, c in counts.items() if c >= min_shared and docs[k[0]].kind == docs[k[1]].kind
    ]
    shared_rare.sort(key=lambda kc: (-kc[1], docs[kc[0][0]].id, docs[kc[0][1]].id))

    rng = random.Random(seed)
    idxs = list(range(len(docs)))
    same_repo: list[tuple[int, int]] = []
    cross_repo: list[tuple[int, int]] = []
    for _ in range(n_target * 40):
        i, j = rng.sample(idxs, 2)
        if docs[i].repo and docs[i].repo == docs[j].repo and docs[i].campaign != docs[j].campaign:
            same_repo.append((i, j))
        elif docs[i].repo and docs[j].repo and docs[i].repo != docs[j].repo:
            cross_repo.append((i, j))

    plan = [
        ("shared_rare_term", [k for k, _ in shared_rare[: n_target]]),
        ("same_repo_pair", same_repo[: n_target // 3]),
        ("cross_repo_pair", cross_repo[: n_target // 3]),
    ]
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for rule, pairs in plan:
        for i, j in pairs:
            key = tuple(sorted((docs[i].id, docs[j].id)))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "pair_id": f"P{len(out) + 1:04d}",
                    "rule": rule,
                    "a": docs[i].id,
                    "b": docs[j].id,
                    "a_title": docs[i].text.split("\n")[0][:160],
                    "b_title": docs[j].text.split("\n")[0][:160],
                    "a_campaign": docs[i].campaign,
                    "b_campaign": docs[j].campaign,
                    "a_repo": docs[i].repo,
                    "b_repo": docs[j].repo,
                    "shared_rare_terms": counts.get((i, j), 0)
                    if (i, j) in counts
                    else counts.get((j, i), 0),
                    "label": None,
                    "reason": "",
                }
            )
    return out


def candidate_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


@dataclass
class LabelEval:
    positives: int
    negatives: int
    tp: int
    fn: int
    fp: int
    tn: int
    review_on_positives: int
    review_on_negatives: int
    dup_threshold: float = 0.85
    review_threshold: float = 0.55
    backend: str = "tfidf"
    representation: str = "claim"

    # --- 自动档（score ≥ dup_threshold）---
    @property
    def recall(self) -> float:
        return self.tp / self.positives if self.positives else 0.0

    @property
    def fp_rate(self) -> float:
        return self.fp / self.negatives if self.negatives else 0.0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    # --- 机器门档案召回（自动档 ∪ 复核带）---
    @property
    def caught_positives(self) -> int:
        return self.tp + self.review_on_positives

    @property
    def catch_recall(self) -> float:
        return self.caught_positives / self.positives if self.positives else 0.0

    @property
    def review_fp_rate(self) -> float:
        return self.review_on_negatives / self.negatives if self.negatives else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "positives": self.positives,
            "negatives": self.negatives,
            "dup_threshold": self.dup_threshold,
            "review_threshold": self.review_threshold,
            "backend": self.backend,
            "representation": self.representation,
            "tp": self.tp,
            "fn": self.fn,
            "fp": self.fp,
            "tn": self.tn,
            "review_on_positives": self.review_on_positives,
            "review_on_negatives": self.review_on_negatives,
            "recall": round(self.recall, 6),
            "fp_rate": round(self.fp_rate, 6),
            "precision": round(self.precision, 6),
            "catch_recall": round(self.catch_recall, 6),
            "review_fp_rate": round(self.review_fp_rate, 6),
            "caught_positives": self.caught_positives,
        }


def score_matrix(
    docs: Sequence[Doc],
    cfg: Mapping[str, Any],
    *,
    representation: str = "claim",
    use_lsa: bool = True,
) -> tuple[Any, dict[str, int]]:
    """构建指定表示的语义空间，返回 (SemanticSpace, id→index)。"""
    from .semantic import SemanticSpace

    raw = dict(cfg)
    if not use_lsa:
        raw = {**raw, "lsa": {**dict(raw.get("lsa", {}) or {}), "enabled": False}}
    texts = [doc_text_for(d, representation, cfg) for d in docs]
    sem = SemanticSpace.build(texts, raw)
    return sem, {d.id: i for i, d in enumerate(docs)}


def doc_text_for(doc: Doc, representation: str, cfg: Mapping[str, Any]) -> str:
    """文档表示（``claim`` = 标题+首句若干，``title`` = 仅标题，``full`` = 全文）。"""
    if representation == "full":
        return doc.text
    lines = [ln for ln in doc.text.split("\n") if ln.strip()]
    title = lines[0] if lines else ""
    if representation == "title":
        return title
    n_sent = int(cfg.get("tfidf.claim_sentences", 2) if isinstance(cfg, Mapping) else 2)
    body = lines[1:] if len(lines) > 1 else []
    sentences: list[str] = []
    for line in body:
        for chunk in line.replace("！", "。").replace("？", "。").split("。"):
            chunk = chunk.strip()
            if chunk:
                sentences.append(chunk)
            if len(sentences) >= n_sent:
                break
        if len(sentences) >= n_sent:
            break
    return "\n".join([title] + sentences)


def evaluate_labels(
    docs: Sequence[Doc],
    labeled: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    *,
    representation: str = "claim",
    dup_threshold: float | None = None,
    review_threshold: float | None = None,
    use_lsa: bool = True,
    space: Any | None = None,
) -> LabelEval:
    """按标注文件算「自动档召回/误判」与「档案召回/复核带误判」（标注与分数解耦）。"""
    if space is None:
        space, index = score_matrix(docs, cfg, representation=representation, use_lsa=use_lsa)
    else:
        index = {d.id: i for i, d in enumerate(docs)}
    dup = float(
        dup_threshold if dup_threshold is not None else _cfg_get(cfg, "dedup.dup_threshold", 0.85)
    )
    review = float(
        review_threshold
        if review_threshold is not None
        else _cfg_get(cfg, "dedup.review_threshold", 0.55)
    )
    tp = fn = fp = tn = rev_pos = rev_neg = 0
    for row in labeled:
        label = row.get("label")
        if label not in ("overlap", "distinct"):
            continue
        ia, ib = index.get(row["a"]), index.get(row["b"])
        if ia is None or ib is None:
            continue
        s = space.sim(ia, ib)
        if label == "overlap":
            if s >= dup:
                tp += 1
            else:
                fn += 1
                if s >= review:
                    rev_pos += 1
        else:
            if s >= dup:
                fp += 1
            else:
                tn += 1
                if s >= review:
                    rev_neg += 1
    return LabelEval(
        positives=tp + fn,
        negatives=fp + tn,
        tp=tp,
        fn=fn,
        fp=fp,
        tn=tn,
        review_on_positives=rev_pos,
        review_on_negatives=rev_neg,
        dup_threshold=dup,
        review_threshold=review,
        backend=getattr(space, "backend", "tfidf"),
        representation=representation,
    )


def _cfg_get(cfg: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def split_labels(
    labeled: Sequence[Mapping[str, Any]], *, holdout_mod: int = 2
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 pair_id 的稳定哈希把标注对半分（tune / holdout），阈值只在 tune 上选。"""
    tune: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    for row in labeled:
        if row.get("label") not in ("overlap", "distinct"):
            continue
        h = int(hashlib.sha256(str(row.get("pair_id", "")).encode()).hexdigest(), 16)
        (holdout if h % holdout_mod == 0 else tune).append(dict(row))
    return tune, holdout


def threshold_sweep(
    docs: Sequence[Doc],
    labeled: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    *,
    representation: str = "claim",
    use_lsa: bool = True,
    grid: Iterable[float] = (
        0.30, 0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90,
    ),
) -> list[dict[str, float]]:
    """阈值扫描：给「阈值-召回-误判」曲线（图 4 数据源，零手抄）。"""
    space, index = score_matrix(docs, cfg, representation=representation, use_lsa=use_lsa)
    scored: list[tuple[str, float]] = []
    for row in labeled:
        if row.get("label") not in ("overlap", "distinct"):
            continue
        ia, ib = index.get(row["a"]), index.get(row["b"])
        if ia is None or ib is None:
            continue
        scored.append((row["label"], round(space.sim(ia, ib), 6)))
    out: list[dict[str, float]] = []
    for t in grid:
        tp = sum(1 for l, s in scored if l == "overlap" and s >= t)
        fn = sum(1 for l, s in scored if l == "overlap" and s < t)
        fp = sum(1 for l, s in scored if l == "distinct" and s >= t)
        tn = sum(1 for l, s in scored if l == "distinct" and s < t)
        out.append(
            {
                "threshold": t,
                "recall": round(tp / (tp + fn), 6) if tp + fn else 0.0,
                "fp_rate": round(fp / (fp + tn), 6) if fp + tn else 0.0,
                "precision": round(tp / (tp + fp), 6) if tp + fp else 0.0,
                "caught": tp,
                "positives": tp + fn,
                "false_positives": fp,
                "negatives": fp + tn,
            }
        )
    return out


def calibrate_review_threshold(
    sweep: Sequence[Mapping[str, Any]], *, target_recall: float, max_fp_rate: float
) -> dict[str, Any]:
    """在 tune 集的扫描表上选复核带阈值：满足 FP ≤ max_fp_rate 下召回最高的点。"""
    feasible = [r for r in sweep if float(r["fp_rate"]) <= max_fp_rate]
    if not feasible:
        return {"threshold": None, "reason": "no_threshold_meets_fp_budget"}
    best = max(feasible, key=lambda r: (float(r["recall"]), float(r["threshold"])))
    return {
        "threshold": float(best["threshold"]),
        "recall_on_tune": float(best["recall"]),
        "fp_rate_on_tune": float(best["fp_rate"]),
        "target_recall": target_recall,
        "max_fp_rate": max_fp_rate,
        "satisfies_target": float(best["recall"]) >= target_recall,
    }


def write_label_file(path: str | Path, candidates: Sequence[Mapping[str, Any]], meta: Mapping[str, Any]) -> str:
    payload = {
        "schema_version": "proposal-chain-labels/1.0",
        "meta": dict(meta),
        "pairs": list(candidates),
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return candidate_hash(payload.get("pairs"))


def read_label_file(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return [], {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    return list(payload.get("pairs", [])), dict(payload.get("meta", {}))
