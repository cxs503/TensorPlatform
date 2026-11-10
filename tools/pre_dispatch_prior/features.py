"""派发前可行性先验 —— 判据特征层（零领域语义，全部时序因果）。

四族特征（对应任务书 ①：判据复杂度 / 依赖深度 / repo 成熟度 / 提案新颖度）
--------------------------------------------------------------------------
1. **判据复杂度**（只看 ``acceptance_criteria`` 的 JSON 结构，不读内容）：
   ``criteria_n_log``（判据条数 log1p）、``criteria_structured_share``（字典型判据占比，
   即可机读的结构化判据比例）、``criteria_numeric_share``（带数值阈值 + 比较运算符的判据
   占比 = 「硬指标」密度）、``criteria_bool_share``（布尔/PASS 型门占比）、``has_criteria``。
   直觉：判据越多越硬，验收越难 → 期望验收概率更低。
2. **依赖拓扑**：``dep_depth``（同计划内依赖链最长深度）、``dep_count``、``dep_fanout``
   （有多少节点依赖它 = 关键路径压力）。
3. **repo 成熟度**（严格更早的样本上算，Laplace 平滑）：``repo_prior_n_log``、
   ``repo_prior_success``（该仓既往验收成功率，无先验时取 0.5 中性值）。
4. **提案新颖度**：``novelty = 1 - max_{更早节点} cos_sim(tokens)``，取 1.0 表示史上首见。
   新颖度越高 → 越可能在未知区翻车（也可反向：新颖度高的活更容易被优先放行/复用工具）。
   方向不预设，由训练窗学。

辅特征：``plan_progress``（同计划更早节点的 done 占比）、``risk_ord``（low/medium/high →
0/0.5/1，缺省 0.5 中性）、``is_gate``（gate/checkpoint 节点）、``is_maturity``、
``budget_hours_log``。

分词（新颖度用）刻意保持**语言无关**：拉丁词（小写、len≥2）+ CJK 二元组。不做停用词表，
不做领域词表——否则就把领域语义写进平台了。
"""
from __future__ import annotations

import dataclasses
import math
import re
from collections import Counter
from typing import Any, Sequence

from .dataset import RawNode, label_primary

FEATURE_NAMES: tuple[str, ...] = (
    "criteria_n_log",
    "criteria_structured_share",
    "criteria_numeric_share",
    "criteria_bool_share",
    "has_criteria",
    "dep_depth",
    "dep_count",
    "dep_fanout",
    "repo_prior_n_log",
    "repo_prior_success",
    "novelty",
    "plan_progress",
    "risk_ord",
    "is_gate",
    "is_maturity",
    "budget_hours_log",
)

FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "criteria_complexity": ("criteria_n_log", "criteria_structured_share",
                            "criteria_numeric_share", "criteria_bool_share", "has_criteria"),
    "dependency_topology": ("dep_depth", "dep_count", "dep_fanout"),
    "repo_maturity": ("repo_prior_n_log", "repo_prior_success"),
    "proposal_novelty": ("novelty",),
    "context": ("plan_progress", "risk_ord", "is_gate", "is_maturity", "budget_hours_log"),
}

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[a-z0-9_]+")
_COMPARATORS = {"<", "<=", "≤", ">", ">=", "≥", "==", "=", "!=", "≈", "~"}
_RISK_ORD = {"low": 0.0, "medium": 0.5, "high": 1.0}


# ───────────────────────────── 判据结构解析 ─────────────────────────────

def criteria_items(meta: dict[str, Any]) -> list[Any]:
    """把各种形态的 ``acceptance_criteria`` 规整成条目列表（不解释语义）。"""
    crit = meta.get("acceptance_criteria")
    if crit is None:
        alt = meta.get("acceptance") or meta.get("criteria")
        crit = alt
    if crit is None:
        return []
    if isinstance(crit, str):
        return [crit] if crit.strip() else []
    if isinstance(crit, dict):
        # 单条字典判据（含 metric/criterion 键）vs 判据集合字典
        if {"metric", "criterion", "operator", "threshold"} & set(crit):
            return [crit]
        return list(crit.values())
    if isinstance(crit, (list, tuple)):
        return [c for c in crit if c is not None and c != ""]
    return [crit]


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def criteria_features(meta: dict[str, Any]) -> dict[str, float]:
    items = criteria_items(meta)
    n = len(items)
    structured = numeric = boolean = 0
    for item in items:
        if isinstance(item, dict):
            structured += 1
            threshold = item.get("threshold", item.get("target", item.get("value")))
            unit = str(item.get("unit") or "").lower()
            op = str(item.get("operator") or item.get("op") or "").strip()
            if unit == "bool" or isinstance(threshold, bool) or str(threshold).upper() == "PASS":
                boolean += 1
            elif _is_numeric(threshold) and (op in _COMPARATORS or op == ""):
                numeric += 1
            elif isinstance(threshold, str) and any(op_ in threshold for op_ in ("PASS", "pass")):
                boolean += 1
        elif isinstance(item, str):
            # 自由文本判据：只数「有没有出现比较运算符/百分号」，不读语义
            if any(op in item for op in ("<", ">", "≤", "≥", "%")):
                numeric += 1
    denom = n if n else 1
    return {
        "criteria_n_log": math.log1p(n),
        "criteria_structured_share": structured / denom if n else 0.0,
        "criteria_numeric_share": numeric / denom if n else 0.0,
        "criteria_bool_share": boolean / denom if n else 0.0,
        "has_criteria": 1.0 if n else 0.0,
    }


# ───────────────────────────── 分词 & 余弦 ─────────────────────────────

def tokenize(text: str) -> Counter:
    """语言无关分词：拉丁词（小写，len≥2）+ CJK 二元组（单字丢弃）。"""
    text = (text or "").lower()
    counts: Counter = Counter(_LATIN.findall(text))
    for tok, c in list(counts.items()):
        if len(tok) < 2:
            del counts[tok]
    chars = _CJK.findall(text)
    for a, b in zip(chars, chars[1:]):
        counts[a + b] += 1
    return counts


def _norm(counts: Counter) -> float:
    return math.sqrt(sum(v * v for v in counts.values()))


def cosine(a: Counter, b: Counter, na: float | None = None, nb: float | None = None) -> float:
    if not a or not b:
        return 0.0
    na = _norm(a) if na is None else na
    nb = _norm(b) if nb is None else nb
    if na <= 0 or nb <= 0:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(v * big.get(k, 0) for k, v in small.items())
    return dot / (na * nb)


# ───────────────────────────── 特征矩阵 ─────────────────────────────

@dataclasses.dataclass
class FeatureSet:
    """特征矩阵 + 溯源列（不含任何节点文本）。"""

    feature_names: tuple[str, ...]
    rows: list[list[float]]
    node_ids: list[int]
    plan_ids: list[int]
    created_at: list[str]
    plan_created_at: list[str]
    labels: list[int | None]
    statuses: list[str]

    def __len__(self) -> int:
        return len(self.rows)

    def matrix(self, indices: Sequence[int] | None = None):
        import numpy as np
        idx = range(len(self.rows)) if indices is None else list(indices)
        return np.asarray([self.rows[i] for i in idx], dtype=float)

    def vector(self, index: int):
        import numpy as np
        return np.asarray(self.rows[index], dtype=float)

    def labeled_indices(self) -> list[int]:
        return [i for i, y in enumerate(self.labels) if y is not None]


class HistoryState:
    """因果累计器：历史节点喂进来，给「更晚节点」算特征。

    这是**派发前**的实际用法：历史到 T 为止 → 新节点（待派发）在 T 之后的特征只依赖历史。
    ``extract_features`` 对历史全序列逐点调用同一个类，因此「前缀算特征 == 全集算特征再切前缀」，
    泄漏无处可藏，且离线路径与在线路径共用同一段代码（不会出现两套口径）。
    """

    def __init__(self) -> None:
        self.depth_cache: dict[int, float] = {}
        self.parents: dict[int, tuple[int, ...]] = {}
        self.fanout: Counter = Counter()
        self.repo_hist: dict[str, list[int]] = {}
        self.plan_hist: dict[int, list[int]] = {}
        self._docs: list[tuple[Counter, float]] = []
        self._postings: dict[str, set[int]] = {}

    # -- 观测历史 ----------------------------------------------------
    def observe(self, node: RawNode, label: int | None = None) -> None:
        self.parents[node.node_id] = node.dependencies
        for d in node.dependencies:
            self.fanout[d] += 1
        y = label_primary(node) if label is None else label
        if y is not None:
            self.repo_hist.setdefault(node.repo, []).append(int(y))
            self.plan_hist.setdefault(node.plan_id, []).append(int(y))
        counts = tokenize(node.text)
        if counts:
            j = len(self._docs)
            self._docs.append((counts, _norm(counts)))
            for tok in counts:
                self._postings.setdefault(tok, set()).add(j)

    # -- 依赖深度（只在历史图上回溯） --------------------------------
    def depth(self, node_id: int, deps: Sequence[int] = (), _stack: frozenset[int] = frozenset()) -> float:
        if node_id in self.depth_cache:
            return self.depth_cache[node_id]
        if node_id in _stack:
            return 0.0
        _stack = _stack | {node_id}
        best = 0.0
        for d in deps:
            parents = self.parents.get(d)
            if parents is None:
                continue
            best = max(best, self.depth(d, parents, _stack) + 1.0)
        self.depth_cache[node_id] = best
        return best

    # -- 特征 --------------------------------------------------------
    def features_for(self, node: RawNode, novelty_override: float | None = None) -> list[float]:
        meta = node.metadata
        crit = criteria_features(meta)
        repo = node.repo
        hist = self.repo_hist.get(repo, [])
        n_prior = len(hist)
        repo_prior_success = (sum(hist) + 1.0) / (n_prior + 2.0)

        if novelty_override is not None:
            novelty = float(novelty_override)
        else:
            counts = tokenize(node.text)
            norm = _norm(counts)
            best_sim = 0.0
            if counts and self._docs:
                candidates: set[int] = set()
                for tok in counts:
                    candidates |= self._postings.get(tok, set())
                for j in candidates:
                    doc, doc_norm = self._docs[j]
                    sim = cosine(counts, doc, norm, doc_norm)
                    if sim > best_sim:
                        best_sim = sim
            novelty = (1.0 - best_sim) if self._docs else 1.0

        plan_list = self.plan_hist.get(node.plan_id, [])
        plan_progress = (sum(plan_list) / len(plan_list)) if plan_list else 0.0

        risk_raw = str(meta.get("risk") or "").lower()
        risk_ord = _RISK_ORD.get(risk_raw, 0.5)
        budget = meta.get("budget_hours")
        budget_log = (math.log1p(float(budget))
                      if _is_numeric(budget) and float(budget) > 0 else 0.0)

        return [
            crit["criteria_n_log"],
            crit["criteria_structured_share"],
            crit["criteria_numeric_share"],
            crit["criteria_bool_share"],
            crit["has_criteria"],
            float(self.depth(node.node_id, node.dependencies)),
            float(len(node.dependencies)),
            float(self.fanout.get(node.node_id, 0)),
            math.log1p(n_prior),
            repo_prior_success,
            float(novelty),
            plan_progress,
            float(risk_ord),
            1.0 if node.node_type in ("gate", "checkpoint") else 0.0,
            1.0 if meta.get("is_maturity") else 0.0,
            budget_log,
        ]


def novelty_batch(texts: Sequence[str], block: int = 256) -> list[float]:
    """批量精确新颖度（稀疏矩阵分块算 max 余弦，O(N²) 但向量化）。

    与 ``HistoryState`` 的单点实现**数学同义**（都是「与严格更早文档的最大余弦」），
    批处理只是把它向量化：``test_novelty_batch_matches_online`` 钉死两者一致。
    无 scipy 时回落到朴素实现（慢但正确）。
    """
    counts = [tokenize(t) for t in texts]
    n = len(counts)
    try:
        from scipy.sparse import csr_matrix
    except Exception:                       # pragma: no cover - 环境无 scipy 的兜底
        out: list[float] = []
        docs: list[tuple[Counter, float]] = []
        for c in counts:
            nrm = _norm(c)
            best = 0.0
            for doc, doc_norm in docs:
                best = max(best, cosine(c, doc, nrm, doc_norm))
            out.append(1.0 - best if docs else 1.0)
            if c:
                docs.append((c, nrm))
        return out

    import numpy as np
    vocab: dict[str, int] = {}
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for i, c in enumerate(counts):
        nrm = _norm(c)
        if nrm <= 0:
            continue
        for tok, v in c.items():
            j = vocab.get(tok)
            if j is None:
                j = len(vocab)
                vocab[tok] = j
            rows.append(i)
            cols.append(j)
            vals.append(v / nrm)
    if not n:
        return []
    M = csr_matrix((vals, (rows, cols)), shape=(n, max(len(vocab), 1)))
    novelty = np.ones(n, dtype=float)
    for start in range(0, n, block):
        stop = min(start + block, n)
        sim = (M[start:stop] @ M.T).toarray()
        for r in range(start, stop):
            span = sim[r - start, :r]
            if span.size:
                novelty[r] = 1.0 - float(span.max())
    return novelty.tolist()


def extract_features(nodes: Sequence[RawNode]) -> FeatureSet:
    """按因果序（created_at, node_id）扫描：先算特征（只看历史），再把自己并入历史。

    新颖度用向量化批量算法（与在线单点实现同义），其余特征走 ``HistoryState``。
    因此「前缀算特征」与「全集算特征再切前缀」逐值一致（``test_features_are_causal``）。
    """
    order = sorted(nodes, key=lambda n: n.sort_key)
    novelty = novelty_batch([n.text for n in order])
    state = HistoryState()
    rows: list[list[float]] = []
    for i, n in enumerate(order):
        row = state.features_for(n, novelty_override=novelty[i])
        assert len(row) == len(FEATURE_NAMES)
        rows.append(row)
        state.observe(n)
    return FeatureSet(
        feature_names=FEATURE_NAMES,
        rows=rows,
        node_ids=[n.node_id for n in order],
        plan_ids=[n.plan_id for n in order],
        created_at=[n.created_at for n in order],
        plan_created_at=[n.plan_created_at for n in order],
        labels=[label_primary(n) for n in order],
        statuses=[n.status for n in order],
    )


def spec_to_node(spec: dict, node_id: int = 10**9, plan_id: int = 10**9) -> RawNode:
    """把「待派发节点规格」变成 RawNode（派发前路径：只有计划侧信息，无结局）。"""
    from .dataset import RawNode, node_text, parse_iso
    meta = dict(spec.get("metadata") or {})
    for key in ("acceptance_criteria", "risk", "repo", "is_maturity", "budget_hours", "goal"):
        if key in spec and key not in meta:
            meta[key] = spec[key]
    name = str(spec.get("name") or spec.get("title") or "")
    return RawNode(
        node_id=int(spec.get("node_id", node_id)),
        plan_id=int(spec.get("plan_id", plan_id)),
        created_at=parse_iso(str(spec.get("created_at") or "2100-01-01T00:00:00+00:00")),
        plan_created_at=parse_iso(str(spec.get("plan_created_at")
                                     or spec.get("created_at") or "2100-01-01T00:00:00+00:00")),
        status=str(spec.get("status") or "pending"),
        node_type=str(spec.get("node_type") or "task"),
        text=str(spec.get("text") or node_text(name, meta)),
        dependencies=tuple(int(x) for x in (spec.get("dependencies") or ())),
        metadata=meta,
    )


def feature_dict(fs: FeatureSet, index: int) -> dict[str, float]:
    return {name: fs.rows[index][k] for k, name in enumerate(fs.feature_names)}