#!/usr/bin/env python3
"""提案去重门（proposal dedup gate）——三层表征 + 关键词扫描 + 近重复兄弟聚类。

**要防的东西**：提案池里最常见的伪新意不是逐字复读，而是「换词不换招」——同一机制
换个说法、调个常数、交换两句话的顺序，就交上来当新提案；再接一层「只引用了已验证
语料里的关键词」的套壳提案。仅靠令牌级 Jaccard 会漏掉这两类。

五特征（全部域无关、无外部模型依赖）
--------------------------------
1. **表层 lexical**：NFKC + 小写归一后，拉丁词（len>=2）+ CJK 二元组集合的 Jaccard；
2. **骨架 skeleton**：先把通用连接/动作近义词归一到代表词、把数字/百分数替换为 ``<n>``，
   再取集合——顺序与措辞被抹平，**机制骨架**留下，对「只调参的克隆」敏感；
   包容度 ``|A∩B|/min(|A|,|B|)`` 抓「长提案吞掉短提案」的抄袭方向；
3. **二元 IDF 余弦**：以提案池为背景算二元组 IDF 做加权余弦；
4. **字符 IDF 余弦**：字符级（去空白）IDF 余弦。中文里同机制改写会换二元组但**留字**，
   这一层是唯一能抓住「人工改写型近重复」的信号（实测见 artifacts/in99_n10/metrics.json）；
5. **最长公共子序列占比**：字符级 LCS / max(len)，对语序调整鲁棒，压制共现虚词的噪声。

综合分 ``score = w_char*字符IDF余弦 + w_lcs*LCS - w_skel*骨架Jaccard + w_cos*二元IDF余弦``
（删掉重复的措辞层，见 ``DedupConfig``）。权重与阈值都可注入，默认值由 dev 划分选定。

关键词扫描
----------
从提案里按 IDF 取前 k 个信息量词项，与参考语料（已验证条目/已有节点目标，**由调用方注入**）
求交；命中率超过 ``max_reference_overlap`` 判 ``KEYWORD_COLLISION``——这是「拿已验证
成果的关键词套一遍当新提案」的机器拦截。

近重复兄弟聚类
--------------
同批提案两两打分，``score >= theta_sibling`` 连边，并查集聚类；每簇保留代表
（摘要最长，平局取输入序最小），其余判 ``NEAR_DUPLICATE_SIBLING`` 并给出代表 id。

fail-closed：摘要过短、缺交付物标记、词项为空、参考语料不可解析——一律拒，绝不因为
「看不出来」而放行（与 ``ops/taskplane/campaign_dispatcher.py`` 的硬门同一打法）。

阈值可调：所有数值都在 ``DedupConfig`` 里，可由 JSON 文件注入
（``DedupConfig.load``）；默认值来自 dev 划分上的阈值扫描，holdout 报告见
``artifacts/in99_n10/metrics.json``。

零领域语义：输入只有（提案摘要文本, 交付物标记, 参考语料）。
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

MIN_SUMMARY_CHARS = 60
VERSION = "in99-n10-v1"

# 通用功能词（学科中立；只收虚词/极泛动作词，避免误伤）
_STOP_TERMS: Tuple[str, ...] = (
    "的", "了", "和", "与", "及", "是", "在", "对", "把", "被", "为", "以", "等",
    "并", "而", "就", "都", "也", "其", "之", "这", "那", "该", "有", "无", "不",
    "可", "能", "要", "会", "将", "从", "到", "由", "即", "则", "且", "或", "按",
    "用", "于", "上", "下", "中", "里", "时", "后", "前", "每", "各", "我们", "你",
    "一个", "一种", "进行", "通过", "以及", "同时", "需要", "必须", "应当", "可以",
)

# 通用连接/动作近义词归一（不含任何学科词，保持域无关）
_SYNONYMS: Tuple[Tuple[str, str], ...] = tuple(sorted({
    ("给出", "提供"), ("提供", "给出"), ("采用", "使用"), ("使用", "采用"),
    ("运用", "使用"), ("利用", "使用"), ("构建", "构造"), ("构造", "构建"),
    ("建立", "构造"), ("生成", "构造"), ("验证", "核验"), ("核验", "验证"),
    ("检验", "验证"), ("说明", "阐述"), ("阐述", "说明"), ("需要", "要求"),
    ("应当", "要求"), ("改进", "优化"), ("提升", "优化"), ("提高", "优化"),
    ("计算", "求解"), ("求解", "计算"), ("估计", "估算"), ("估算", "估计"),
    ("误差", "偏差"), ("偏差", "误差"), ("步骤", "环节"), ("环节", "步骤"),
    ("方案", "思路"), ("思路", "方案"), ("检查", "校验"), ("校验", "检查"),
    ("记录", "登记"), ("登记", "记录"), ("写入", "落盘"), ("落盘", "写入"),
    ("自动", "机器"), ("机器", "自动"), ("接口", "契约"), ("契约", "接口"),
}, key=lambda kv: -len(kv[0])))

_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
_LATIN_RE = re.compile(r"[a-z][a-z0-9_./+-]{1,}")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
_WS_RE = re.compile(r"\s+")


# ─────────────────────────── 配置 ───────────────────────────

@dataclass(frozen=True)
class DedupConfig:
    """去重门阈值（全部可注入；默认值 = dev 划分扫描所得，见 artifacts）。"""

    min_summary_chars: int = MIN_SUMMARY_CHARS
    theta_clone: float = 0.24          # 与池内既有提案判近重复的综合分阈值（dev 选定后冻结）
    theta_sibling: float = 0.22        # 同批兄弟连边阈值（同批略松：同源即合并）
    w_char: float = 0.50               # 字符 IDF 余弦（改写型近重复的主信号）
    w_lcs: float = 0.25                # 字符 LCS 占比
    w_skeleton: float = 0.15           # 骨架 Jaccard（只调参克隆的主信号）
    w_cosine: float = 0.10             # 二元 IDF 余弦
    keyword_top_k: int = 12            # 关键词扫描取前 k 个高 IDF 词项
    min_keyword_idf: float = 1.0       # 只统计有判别力的词项
    max_reference_overlap: float = 0.45   # 与参考语料关键词命中率上限
    min_terms: int = 8                 # 词项过少 = 看不清 → 拒
    require_deliverables: bool = True

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "DedupConfig":
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"未知配置项: {sorted(unknown)}")
        return cls(**{k: v for k, v in data.items()})  # type: ignore[arg-type]

    @classmethod
    def load(cls, path: str | Path | None) -> "DedupConfig":
        if path is None:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)


# ─────────────────────────── 表征 ───────────────────────────

def normalize(text: str) -> str:
    """NFKC + 小写 + 空白压缩。"""
    if not isinstance(text, str):
        raise TypeError(f"文本必须是 str，得 {type(text).__name__}")
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text).lower()).strip()


def apply_synonyms(text: str) -> str:
    out = text
    for src, dst in _SYNONYMS:
        out = out.replace(src, dst)
    return out


def term_set(text: str, *, drop_stop: bool = True) -> Set[str]:
    """表层词项集合：拉丁词 + CJK 二元组（可选去功能词）。"""
    norm = normalize(text)
    terms: Set[str] = set(_LATIN_RE.findall(norm))
    for run in _CJK_RUN_RE.findall(norm):
        if len(run) == 1:
            terms.add(run)
            continue
        for i in range(len(run) - 1):
            terms.add(run[i:i + 2])
    if drop_stop:
        terms = {t for t in terms if t not in _STOP_TERMS}
    return terms


def skeleton_set(text: str) -> Set[str]:
    """骨架集合：近义词归一 + 数字→<n>，抹平措辞与参数。"""
    norm = normalize(text)
    norm = apply_synonyms(norm)
    norm = _NUM_RE.sub("<n>", norm)
    return term_set(norm, drop_stop=True)


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _containment(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    small = min(len(a), len(b))
    return len(a & b) / small if small else 0.0


def _idf_map(sets: Sequence[Set[str]]) -> Dict[str, float]:
    n = len(sets)
    df: Dict[str, int] = {}
    for terms in sets:
        for t in terms:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def build_idf(docs: Iterable[str]) -> IdfModel:
    """平滑 IDF：log((N+1)/(df+1)) + 1（无 sklearn 依赖，确定性）。双层一次建成。"""
    texts = list(docs)
    return IdfModel(terms=_idf_map([term_set(t) for t in texts]),
                    chars=_idf_map([char_set(t) for t in texts]),
                    n_docs=len(texts))


def _vec(terms: Set[str], idf: Mapping[str, float], default_idf: float) -> Dict[str, float]:
    return {t: idf.get(t, default_idf) for t in terms}


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(a[t] * b[t] for t in common)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return num / (na * nb) if na and nb else 0.0


@dataclass(frozen=True)
class IdfModel:
    """双层 IDF：terms（二元组/拉丁词）与 chars（字符），由语料一次建成。"""

    terms: Dict[str, float]
    chars: Dict[str, float]
    n_docs: int = 0


def char_set(text: str) -> Set[str]:
    """字符集合（去空白）——中文改写会换二元组但留字。"""
    return {c for c in normalize(text) if not c.isspace()}


def lcs_ratio(a: str, b: str) -> float:
    """字符级最长公共子序列占比（O(n*m)，提案摘要量级可忽略）。"""
    A = [c for c in normalize(a) if not c.isspace()]
    B = [c for c in normalize(b) if not c.isspace()]
    if not A or not B:
        return 0.0
    prev = [0] * (len(B) + 1)
    for i in range(len(A)):
        cur = [0] * (len(B) + 1)
        ai = A[i]
        for j in range(len(B)):
            cur[j + 1] = prev[j] + 1 if ai == B[j] else max(cur[j], prev[j + 1])
        prev = cur
    return prev[-1] / max(len(A), len(B))


@dataclass(frozen=True)
class Similarity:
    lexical_jaccard: float
    skeleton_jaccard: float
    containment: float
    cosine: float
    char_cosine: float
    lcs: float
    score: float

    def to_dict(self) -> dict:
        return {
            "lexical_jaccard": round(self.lexical_jaccard, 4),
            "skeleton_jaccard": round(self.skeleton_jaccard, 4),
            "containment": round(self.containment, 4),
            "cosine": round(self.cosine, 4),
            "char_cosine": round(self.char_cosine, 4),
            "lcs": round(self.lcs, 4),
            "score": round(self.score, 4),
        }


def similarity(a: str, b: str, idf: Optional[IdfModel] = None,
               cfg: Optional[DedupConfig] = None) -> Similarity:
    cfg = cfg or DedupConfig()
    model = idf if isinstance(idf, IdfModel) else build_idf([a, b])
    la, lb = term_set(a), term_set(b)
    sa, sb = skeleton_set(a), skeleton_set(b)
    ca, cb = char_set(a), char_set(b)
    cos = _cosine(_vec(la, model.terms, 1.0), _vec(lb, model.terms, 1.0))
    ccos = _cosine(_vec(ca, model.chars, 1.0), _vec(cb, model.chars, 1.0))
    lev = _jaccard(la, lb)
    skel = _jaccard(sa, sb)
    cont = _containment(sa, sb)
    lcs = lcs_ratio(a, b)
    score = (cfg.w_char * ccos + cfg.w_lcs * lcs
             + cfg.w_skeleton * skel + cfg.w_cosine * cos)
    return Similarity(lev, skel, cont, cos, ccos, lcs, score)


# ─────────────────────────── 关键词扫描 ───────────────────────────

@dataclass(frozen=True)
class KeywordHit:
    keyword: str
    source_id: str
    idf: float

    def to_dict(self) -> dict:
        return {"keyword": self.keyword, "source_id": self.source_id, "idf": round(self.idf, 3)}


def top_terms(text: str, idf: Mapping[str, float], k: int = 12, min_idf: float = 1.0) -> List[str]:
    terms = term_set(text) | skeleton_set(text)
    scored = [(t, idf.get(t, 1.0)) for t in terms]
    scored = [(t, w) for t, w in scored if w >= min_idf and len(t) >= 2]
    scored.sort(key=lambda tw: (-tw[1], tw[0]))
    return [t for t, _ in scored[:k]]


def scan_keywords(proposal: str, references: Sequence[Mapping[str, str]],
                  idf: Mapping[str, float], cfg: Optional[DedupConfig] = None) -> List[KeywordHit]:
    """与参考语料（已验证条目/既有节点目标）做关键词求交，返回命中列表。"""
    cfg = cfg or DedupConfig()
    terms = set(top_terms(proposal, idf, cfg.keyword_top_k, cfg.min_keyword_idf))
    hits: List[KeywordHit] = []
    for ref in references:
        rid = str(ref.get("id", "?"))
        rtext = ref.get("text") or ref.get("goal") or ""
        for t in sorted(terms & term_set(rtext)):
            hits.append(KeywordHit(t, rid, idf.get(t, 1.0)))
    # 去重：同关键词只留 IDF 最高的一条来源（确定性 tie-break 按 source_id）
    best: Dict[str, KeywordHit] = {}
    for h in hits:
        cur = best.get(h.keyword)
        if cur is None or (h.idf, cur.source_id) > (cur.idf, h.source_id):
            best[h.keyword] = h
    return sorted(best.values(), key=lambda h: (-h.idf, h.keyword))


# ─────────────────────────── 裁决 ───────────────────────────

@dataclass
class Verdict:
    proposal_id: str
    accepted: bool
    code: str
    reasons: List[str] = field(default_factory=list)
    best_match_id: Optional[str] = None
    score: float = 0.0
    sim: Optional[Similarity] = None
    keyword_hits: List[KeywordHit] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "accepted": self.accepted,
            "code": self.code,
            "reasons": list(self.reasons),
            "best_match_id": self.best_match_id,
            "score": round(self.score, 4),
            "sim": self.sim.to_dict() if self.sim else None,
            "keyword_hits": [h.to_dict() for h in self.keyword_hits],
        }


def evaluate_proposal(proposal: Mapping[str, object],
                      pool: Sequence[Mapping[str, object]] = (),
                      references: Sequence[Mapping[str, str]] = (),
                      cfg: Optional[DedupConfig] = None,
                      idf: Optional[IdfModel] = None) -> Verdict:
    """对单条提案做门裁决（fail-closed）。

    proposal: {"id": str, "summary": str, "deliverables": [...]?}
    pool:     池内既有提案（同结构）
    references: 参考语料 [{"id","text"}]——已验证条目/既有节点目标，由调用方注入
    """
    cfg = cfg or DedupConfig()
    pid = str(proposal.get("id", "?"))
    summary = proposal.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return Verdict(pid, False, "MISSING_SUMMARY", ["摘要缺失或非字符串"])
    if len(summary.strip()) < cfg.min_summary_chars:
        return Verdict(pid, False, "SUMMARY_TOO_SHORT",
                       [f"摘要 {len(summary.strip())} 字 < 下限 {cfg.min_summary_chars}"])
    if cfg.require_deliverables and not proposal.get("deliverables"):
        return Verdict(pid, False, "MISSING_DELIVERABLES", ["缺交付物标记"])
    if len(term_set(summary)) < cfg.min_terms:
        return Verdict(pid, False, "TERMS_TOO_FEW",
                       [f"词项 {len(term_set(summary))} < 下限 {cfg.min_terms}（看不清→拒）"])

    corpus = [str(p.get("summary") or "") for p in pool]
    idf_eff = idf if idf is not None else build_idf(
        corpus + [summary] + [str(r.get("text", "")) for r in references])

    best_id, best_sim = None, None
    for p in pool:
        other = str(p.get("summary") or "")
        if not other or str(p.get("id")) == pid:
            continue
        sim = similarity(summary, other, idf_eff, cfg)
        if best_sim is None or sim.score > best_sim.score:
            best_id, best_sim = str(p.get("id")), sim

    if best_sim is not None and best_sim.score >= cfg.theta_clone:
        return Verdict(pid, False, "NEAR_DUPLICATE",
                       [f"与 {best_id} 综合相似度 {best_sim.score:.3f} >= 阈值 {cfg.theta_clone:.3f}"],
                       best_id, best_sim.score, best_sim)

    hits = scan_keywords(summary, references, idf_eff.terms, cfg) if references else []
    k = max(1, cfg.keyword_top_k)
    if hits and len(hits) / k > cfg.max_reference_overlap:
        return Verdict(pid, False, "KEYWORD_COLLISION",
                       [f"参考语料关键词命中 {len(hits)}/{k} > 上限 {cfg.max_reference_overlap:.2f}",
                        "命中: " + ",".join(h.keyword for h in hits[:8])],
                       best_id, best_sim.score if best_sim else 0.0, best_sim, hits)

    return Verdict(pid, True, "OK", [], best_id, best_sim.score if best_sim else 0.0, best_sim, hits)


def detect_siblings(proposals: Sequence[Mapping[str, object]],
                    cfg: Optional[DedupConfig] = None) -> List[List[str]]:
    """同批兄弟聚类（并查集），返回每个簇的提案 id 列表（按最小 id 排序）。"""
    cfg = cfg or DedupConfig()
    ids = [str(p.get("id", i)) for i, p in enumerate(proposals)]
    summaries = [str(p.get("summary") or "") for p in proposals]
    idf = build_idf(summaries)
    parent = list(range(len(proposals)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(proposals)):
        for j in range(i + 1, len(proposals)):
            if similarity(summaries[i], summaries[j], idf, cfg).score >= cfg.theta_sibling:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)

    groups: Dict[int, List[str]] = {}
    for i, pid in enumerate(ids):
        groups.setdefault(find(i), []).append(pid)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def resolve_siblings(proposals: Sequence[Mapping[str, object]],
                     cfg: Optional[DedupConfig] = None) -> List[Verdict]:
    """兄弟裁决：每簇保留代表（摘要最长，平局取输入序最小），其余拒。"""
    cfg = cfg or DedupConfig()
    order = {str(p.get("id", i)): i for i, p in enumerate(proposals)}
    by_id = {str(p.get("id", i)): p for i, p in enumerate(proposals)}
    out: List[Verdict] = []
    for group in detect_siblings(proposals, cfg):
        if len(group) == 1:
            out.append(Verdict(group[0], True, "OK"))
            continue
        rep = sorted(group, key=lambda pid: (-len(str(by_id[pid].get("summary") or "")), order[pid]))[0]
        out.append(Verdict(rep, True, "OK", ["簇代表"]))
        for pid in group:
            if pid == rep:
                continue
            out.append(Verdict(pid, False, "NEAR_DUPLICATE_SIBLING",
                               [f"与同批 {rep} 判为近重复兄弟（阈值 {cfg.theta_sibling:.2f}）"],
                               rep))
    return sorted(out, key=lambda v: order[v.proposal_id])


def gate(proposals: Sequence[Mapping[str, object]],
         pool: Sequence[Mapping[str, object]] = (),
         references: Sequence[Mapping[str, str]] = (),
         cfg: Optional[DedupConfig] = None) -> Dict[str, object]:
    """批量过门：先个体裁决（vs 池 + 参考语料），再兄弟聚类，返回可落盘报告。"""
    cfg = cfg or DedupConfig()
    corpus = [str(p.get("summary") or "") for p in list(pool) + list(proposals)]
    idf = build_idf(corpus + [r.get("text", "") for r in references])
    per: List[Verdict] = []
    accepted_now: List[Mapping[str, object]] = []
    for p in proposals:
        v = evaluate_proposal(p, list(pool) + accepted_now, references, cfg, idf)
        per.append(v)
        if v.accepted:
            accepted_now.append(p)
    sibling = resolve_siblings(accepted_now, cfg)
    sib_map = {v.proposal_id: v for v in sibling}
    merged: List[Verdict] = []
    for v in per:
        if not v.accepted:
            merged.append(v)
            continue
        s = sib_map.get(v.proposal_id)
        merged.append(s if s is not None else v)
    n_in = len(proposals)
    n_out = sum(1 for v in merged if v.accepted)
    return {
        "version": VERSION,
        "config": cfg.to_dict(),
        "n_in": n_in,
        "n_accepted": n_out,
        "n_rejected": n_in - n_out,
        "verdicts": [v.to_dict() for v in merged],
        "clusters": detect_siblings(proposals, cfg),
    }