"""报告语料完整性闸门：重复发布聚类 + 表内数字算术自洽审计。

为什么需要这个工具
------------------
平台的「证据」就是 tensor_reports 表里的 1000+ 篇报告：能力分级（REAL/MOCK/STUB/
WITHHELD）、已合并功能复核通过率、验证欠账清偿率（m1996：523 簇 → 4.02%）、
谱系追溯 KPI（454/499）全部从这张表里数出来。但表格本身没有任何去重 / 内部
算术一致性检查：

* 同一张单（mission_id）可以重复发布同名报告（实测 37 簇 ≥0.90 + 9 簇 0.60–0.90，
  57 篇重复，语料计数虚高 4.18%），后发版本若修订了数字，前一篇仍以 published
  身份被下游按「篇数」计数；
* 报告里的表格是「计算值 | 参考值 | 相对误差%」三连，**相对误差这一列是手算/手抄
  的高危位**——分母取错、正负号取反、四舍五入到错位，机器从未复核过。

两件事都做成可回归基线（`--gate`），让它们成为 CI 级判据而不是人眼在 1364 篇里发现。

判据一：重复发布（正文指纹）
----------------------------
1. 文档指纹：正文按 CJK 字符 / ASCII 词切 token，取 k=5 的 shingle 集合，
   Jaccard = |A∩B| / |A∪B|（blake2b-64 去重哈希，跨进程可复现）。
2. 候选对生成（避免 O(N²)）：同 mission_id、同 scenario_key、或归一化标题相同
   （标题里的 ``（m1797）`` 之类 mission 标记先剥掉，故跨单同名也进候选）。
3. 簇判定（并查集，J ≥ `--dup-threshold` 默认 0.90）：

       DUPLICATE_REPUBLISH   簇内最大 J ≥ 0.90：同一成果重复落库
       NEAR_DUPLICATE        0.60 ≤ J < 0.90：同一成果的修订稿 / 换壳重发

   簇内 `canonical` = id 最小（最早）一篇；其余计为 `extra`（语料计数虚高量）。

判据二：表内数字算术自洽（零语义判断，纯算术）
----------------------------------------------
Markdown 表格行里若有「… | a | b | **±x%** |」结构，则末列百分号**必须**由前面两个
数值之一作分母复算出来：

    e_b = (a − b) / b × 100        （b 为参考值，最常见约定）
    e_a = (a − b) / a × 100        （反约定）

容差 `--arith-tol`（默认 0.05 个百分点绝对 + 1% 相对，取大者；百分数常见两位小数，
故绝对 0.05 覆盖四舍五入）。四种裁决：

    OK                与 e_b 或 e_a 在容差内 → 记账自洽
    SIGN_CONVENTION   与 −e_b 或 −e_a 在容差内 → 符号约定相反（b−a 口径），单列披露
    AMBIGUOUS_DENOM   与两种分母都差 > 容差，但行内存在第三个数能复算出 → 分母取错
    MISMATCH          行内任何数对都复算不出该百分数 → 手抄/公式错误

`AMBIGUOUS_DENOM` 与 `MISMATCH` 是缺陷候选；`SIGN_CONVENTION` 只计数（口径差异，非错误）。

为什么不检测「跨报告同名指标数值冲突」
--------------------------------------
试过并**实测失败**：按 (scenario_key, 上下文短语, 单位) 分组比较头条百分数，在真实
语料上给出 9 条冲突，人工逐条裁决仅 1 条真（ship_resistance_benchmark_verification
的 30.75% vs 5.55%，后者明确是「结构性假 FAIL 修复」后的可认证精度），精度 1/9
≈ 11%。根因：上下文短语（"误差"）不能指认单一物理量，而 scenario_key 粒度粗
（`report-backfill` 这类是桶不是算例）。低精度检测器不能当闸门，故本模块只保留
**算术自洽**这条零语义判据；该负结果写进 docs/report_corpus_integrity_m2053.md。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

__all__ = [
    "Report",
    "DupCluster",
    "ArithFlag",
    "CorpusResult",
    "normalize_title",
    "tokenize",
    "shingles",
    "jaccard",
    "cluster_duplicates",
    "table_rows",
    "parse_number",
    "audit_table_row",
    "audit_tables",
    "audit_table_row",
    "analyze",
    "load_corpus_json",
    "load_corpus_db",
    "gate_check",
    "OK",
    "SIGN_CONVENTION",
    "ABS_CONVENTION",
    "ROUNDING_ARTIFACT",
    "PERCENT_POINT_CONFUSION",
    "MISMATCH",
    "number_interval",
    "classify_interval",
    "classify",
    "calibrate_table",
    "parse_tables",
    "Table",
    "Calibration",
    "DUPLICATE_REPUBLISH",
    "NEAR_DUPLICATE",
    "K_SHINGLE",
    "DUP_THRESHOLD",
    "NEAR_THRESHOLD",
    "ARITH_TOL",
]

#: shingle 长度（token 数）。
K_SHINGLE = 5
#: 判 DUPLICATE_REPUBLISH 的 Jaccard 下限。
DUP_THRESHOLD = 0.90
#: 判 NEAR_DUPLICATE 的 Jaccard 下限。
NEAR_THRESHOLD = 0.60
#: 表内百分数复算容差：绝对（百分点）与相对两项取大者。
ARITH_TOL = 0.05

DUPLICATE_REPUBLISH = "DUPLICATE_REPUBLISH"
NEAR_DUPLICATE = "NEAR_DUPLICATE"
OK = "OK"
SIGN_CONVENTION = "SIGN_CONVENTION"
MISMATCH = "MISMATCH"

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+|\d+(?:\.\d+)?")
#: 标题尾部的 mission 标记：（m2015）/ (m1797) / 【m2015】 —— 归属不同不算内容不同。
_MISSION_TAG_RE = re.compile(r"[（(\[【]\s*m\d{3,5}\s*[)）\]】]", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[\s\u3000,，。.;；:：!！?？\"'“”‘’()（）\[\]【】<>《》/\\|\-_=+*#~`]+")

#: 数值单元（允许粗体/斜体标记、千分位逗号、科学计数、Unicode 负号，
#: 以及尾随的**来源注释** ``3.0 (Armaly)`` —— 注释是出处标签，不是单位换算）
_NUM_CELL_RE = re.compile(
    r"^[\s*_`]*"
    r"(?P<sign>[−\-+]?)"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?(?:[eE][−\-+]?\d+)?)"
    r"[\s*_`]*(?:\([^()]*\))?[\s*_`]*$"
)
#: 百分数单元（末列）
_PCT_CELL_RE = re.compile(r"^[\s*_`]*[−\-+]?\d+(?:\.\d+)?\s*(?:%|‰)[\s*_`]*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


@dataclass
class Report:
    id: int
    mission: Optional[int]
    scenario_key: str
    title: str
    summary: str
    markdown: str
    created_at: str = ""
    status: str = "published"

    @property
    def headline(self) -> str:
        return f"{self.title}\n{self.summary}"


@dataclass
class DupCluster:
    kind: str
    ids: List[int]
    missions: List[Optional[int]]
    scenario_keys: List[str]
    titles: List[str]
    canonical: int
    extra: int
    max_jaccard: float

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class ArithFlag:
    report_id: int
    scenario_key: str
    kind: str
    a: float
    b: float
    stated_pct: float
    recomputed_pct: float
    abs_diff: float
    row: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class CorpusResult:
    n_reports: int
    n_missions: int
    n_scenario_keys: int
    clusters: List[DupCluster] = field(default_factory=list)
    flags: List[ArithFlag] = field(default_factory=list)
    n_tables_calibrated: int = 0
    n_rows_checked: int = 0
    n_rows_skipped: int = 0
    dup_threshold: float = DUP_THRESHOLD
    arith_tol: float = ARITH_TOL

    @property
    def n_duplicate_clusters(self) -> int:
        return sum(1 for c in self.clusters if c.kind == DUPLICATE_REPUBLISH)

    @property
    def n_near_clusters(self) -> int:
        return sum(1 for c in self.clusters if c.kind == NEAR_DUPLICATE)

    @property
    def n_extra_reports(self) -> int:
        return sum(c.extra for c in self.clusters)

    def _count(self, kind: str) -> int:
        return sum(1 for f in self.flags if f.kind == kind)

    @property
    def n_ok(self) -> int:
        return self._count(OK)

    @property
    def n_sign(self) -> int:
        return self._count(SIGN_CONVENTION)

    @property
    def n_abs(self) -> int:
        return self._count(ABS_CONVENTION)

    @property
    def n_rounding(self) -> int:
        return self._count(ROUNDING_ARTIFACT)

    @property
    def n_percent_point(self) -> int:
        return self._count(PERCENT_POINT_CONFUSION)

    @property
    def n_mismatch(self) -> int:
        return self._count(MISMATCH)

    @property
    def n_defects(self) -> int:
        return self.n_mismatch

    def summary(self) -> Dict[str, object]:
        checked = self.n_rows_checked
        return {
            "n_reports": self.n_reports,
            "n_missions": self.n_missions,
            "n_scenario_keys": self.n_scenario_keys,
            "n_duplicate_clusters": self.n_duplicate_clusters,
            "n_near_clusters": self.n_near_clusters,
            "n_extra_reports": self.n_extra_reports,
            "inflation_pct": round(100.0 * self.n_extra_reports / self.n_reports, 3) if self.n_reports else 0.0,
            "n_tables_calibrated": self.n_tables_calibrated,
            "n_rows_checked": checked,
            "n_rows_skipped": self.n_rows_skipped,
            "n_arith_ok": self.n_ok,
            "n_arith_sign": self.n_sign,
            "n_arith_abs": self.n_abs,
            "n_arith_rounding": self.n_rounding,
            "n_arith_percent_point": self.n_percent_point,
            "n_arith_mismatch": self.n_mismatch,
            "defect_rate_pct": round(100.0 * self.n_defects / checked, 3) if checked else 0.0,
            "dup_threshold": self.dup_threshold,
            "arith_tol": self.arith_tol,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.summary()
        d["clusters"] = [c.to_dict() for c in self.clusters]
        d["flags"] = [f.to_dict() for f in self.flags]
        return d


# --------------------------------------------------------------------- 指纹


def normalize_title(title: str) -> str:
    """标题归一化：去 mission 标记、标点、空白，ASCII 小写。"""
    t = unicodedata.normalize("NFKC", title or "")
    t = _MISSION_TAG_RE.sub("", t)
    t = _PUNCT_RE.sub("", t)
    return t.lower()


def tokenize(text: str) -> List[str]:
    """CJK 逐字 + ASCII 逐词 + 数字整体；用于构造 shingle。"""
    return _TOKEN_RE.findall(unicodedata.normalize("NFKC", text or "").lower())


def _h64(tok: str) -> int:
    return int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(), "big")


def shingles(text: str, k: int = K_SHINGLE) -> Set[int]:
    """k-shingle 去重哈希集合；token 数 < k 时退化为单 token 集合。"""
    toks = tokenize(text)
    if not toks:
        return set()
    if len(toks) < k:
        return {_h64(t) for t in toks}
    return {_h64("\x1f".join(toks[i:i + k])) for i in range(len(toks) - k + 1)}


def jaccard(a: Set[int], b: Set[int]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


# ----------------------------------------------------------------- 聚类


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _candidate_pairs(reports: Sequence[Report]) -> Iterable[Tuple[int, int]]:
    """按 mission / scenario_key / 归一化标题分桶生成候选对（i < j）。"""
    buckets: Dict[Tuple[str, object], List[int]] = defaultdict(list)
    for idx, r in enumerate(reports):
        if r.mission is not None:
            buckets[("mission", r.mission)].append(idx)
        if r.scenario_key:
            buckets[("scenario", r.scenario_key)].append(idx)
        buckets[("title", normalize_title(r.title))].append(idx)
    seen: Set[Tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = sorted((members[a], members[b]))
                if (i, j) not in seen:
                    seen.add((i, j))
                    yield i, j


def cluster_duplicates(
    reports: Sequence[Report],
    signatures: Optional[Sequence[Set[int]]] = None,
    dup_threshold: float = DUP_THRESHOLD,
    near_threshold: float = NEAR_THRESHOLD,
) -> List[DupCluster]:
    """并查集聚类：J ≥ near_threshold 的候选对连边，簇内 max J 决定 kind。"""
    sigs = list(signatures) if signatures is not None else [shingles(r.markdown) for r in reports]
    uf = _UnionFind()
    pair_j: Dict[Tuple[int, int], float] = {}
    for i, j in _candidate_pairs(reports):
        jv = jaccard(sigs[i], sigs[j])
        pair_j[(i, j)] = jv
        if jv >= near_threshold:
            uf.union(i, j)

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(reports)):
        if i in uf.parent:
            groups[uf.find(i)].append(i)

    clusters: List[DupCluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        jmax = max((pair_j.get((min(a, b), max(a, b)), 0.0)
                    for k, a in enumerate(members) for b in members[k + 1:]), default=0.0)
        kind = DUPLICATE_REPUBLISH if jmax >= dup_threshold else NEAR_DUPLICATE
        members = sorted(members, key=lambda i: reports[i].id)
        clusters.append(DupCluster(
            kind=kind,
            ids=[reports[i].id for i in members],
            missions=[reports[i].mission for i in members],
            scenario_keys=[reports[i].scenario_key for i in members],
            titles=[reports[i].title[:90] for i in members],
            canonical=reports[members[0]].id,
            extra=len(members) - 1,
            max_jaccard=round(jmax, 6),
        ))
    clusters.sort(key=lambda c: c.canonical)
    return clusters


# ------------------------------------------------------- 表内算术自洽
#
# 逐行判据会被「带注释的参考值」（`0.00833 (ITTC-57)`）和「千分位雷诺数」
# （`100,000`）骗到：结构上看似紧，配上的却不是 (值, 参考值) 那一对。m2053 实测
# 逐行裁决 349 条报警，抽样几乎全是这一种假阳。故改成**表级列标定 + 逐行裁决**：


@dataclass
class Table:
    report_id: int
    scenario_key: str
    header: List[str]
    rows: List[List[str]]

    def cell(self, row: Sequence[str], col: int) -> str:
        return row[col] if 0 <= col < len(row) else ""


#: 误差列的表头词元（含 ASCII 与符号写法）。
_ERR_HEADER_RE = re.compile(r"误差|偏差|相对误差|差异|error|err\b|deviation|diff|Δ", re.IGNORECASE)


def parse_tables(markdown: str) -> List[Table]:
    """把 markdown 里的每张表解析成 (表头, 数据行) ；表头必须是第一行、第二行是分隔线。"""
    lines = (markdown or "").splitlines()
    tables: List[Table] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        block = []
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            block.append(lines[j].strip())
            j += 1
        if len(block) >= 3:
            header = [c.strip() for c in block[0].strip("|").split("|")]
            if _TABLE_SEP_RE.match(block[1]):
                rows = []
                for bl in block[2:]:
                    cells = [c.strip() for c in bl.strip("|").split("|")]
                    if len(cells) == len(header):
                        rows.append(cells)
                if rows and len(header) >= 3:
                    tables.append(Table(report_id=-1, scenario_key="", header=header, rows=rows))
        i = j
    return tables


def table_rows(markdown: str) -> Iterable[List[str]]:
    """兼容旧接口：逐行吐出表格数据行（不含表头）。"""
    for t in parse_tables(markdown):
        for r in t.rows:
            yield r


def parse_number(cell: str) -> Optional[float]:
    """解析单元格为浮点数；不是纯数值（含单位/文字/括号注释）则返回 None。"""
    m = _NUM_CELL_RE.match(cell or "")
    if not m:
        return None
    raw = m.group("num").replace(",", "")
    try:
        v = float(raw)
    except ValueError:
        return None
    if m.group("sign") in ("-", "−"):
        v = -v
    return v


_NUM_DECIMALS_RE = re.compile(r"^(?P<sign>[−\-+]?)(?P<int>[\d,]+)(?:\.(?P<frac>\d+))?(?:[eE](?P<exp>[−\-+]?\d+))?$")


def number_interval(cell: str) -> Optional[Tuple[float, float]]:
    """按单元格**打印精度**给出真值区间：0.0034 → [0.00335, 0.00345]。

    报告把中间量截断到 4 位再算百分数很常见；不把截断算进去，就会把「印刷精度不足」
    误判成「算错」（m2053 实测：逐行判据的头号假阳来源）。
    """
    txt = (cell or "").replace("*", "").replace("`", "").replace("_", "").replace(",", "")
    txt = re.sub(r"\s+", "", txt)
    m = _NUM_CELL_RE.match(cell or "")
    if not m:
        return None
    core = m.group("num").replace(",", "")
    core = re.sub(r"\s+", "", core)
    mm = _NUM_DECIMALS_RE.match(core)
    if not mm:
        return None
    sign = -1.0 if m.group("sign") in ("-", "−") else 1.0
    frac = mm.group("frac") or ""
    exp = int((mm.group("exp") or "0").replace("−", "-"))
    half = 0.5 * 10 ** (-len(frac)) * 10 ** exp
    lo = sign * float(core) * 0  # placeholder to keep float path simple
    try:
        v = sign * float(core)
    except ValueError:
        return None
    if sign < 0:
        return (v - half, v + half)
    return (v - half, v + half)


_PCT_PARSE_RE = re.compile(r"^(?P<sign>[−\-+]?)(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>%|‰)$")


def parse_percent(cell: str) -> Optional[float]:
    """解析百分数单元；‰ 折算成百分数（×0.1）以同口径比较。

    注意：不能用通用标点清洗后再 float —— 那会把小数点当标点删掉（538.5 → 5385，
    m2053 实测踩过）。这里只剥 markdown 标记与空白，再用正则取数。
    """
    txt = (cell or "").replace("*", "").replace("`", "").replace("_", "")
    txt = re.sub(r"\s+", "", txt)
    m = _PCT_PARSE_RE.match(txt)
    if not m:
        return None
    v = float(m.group("num"))
    if m.group("sign") in ("-", "−"):
        v = -v
    return v * (0.1 if m.group("unit") == "‰" else 1.0)


def _within(stated: float, recomputed: float, tol: float) -> bool:
    lim = max(tol, 1.0e-2 * abs(recomputed)) + 1.0e-9
    return abs(stated - recomputed) <= lim


#: 三种记账约定：带符号相对误差、符号相反（b−a 口径）、绝对值口径。
OK = "OK"
SIGN_CONVENTION = "SIGN_CONVENTION"
ABS_CONVENTION = "ABS_CONVENTION"
ROUNDING_ARTIFACT = "ROUNDING_ARTIFACT"
PERCENT_POINT_CONFUSION = "PERCENT_POINT_CONFUSION"


def classify(stated: float, recomputed: float, tol: float = ARITH_TOL) -> Optional[str]:
    """判定 stated 与复算值属于哪种约定；都对不上返回 None（=缺陷）。"""
    if _within(stated, recomputed, tol):
        return OK
    if _within(stated, -recomputed, tol):
        return SIGN_CONVENTION
    if _within(abs(stated), abs(recomputed), tol):
        return ABS_CONVENTION
    return None


def classify_interval(stated: float, a_iv: Tuple[float, float], b_iv: Tuple[float, float],
                      tol: float = ARITH_TOL) -> Optional[str]:
    """在 a、b 的印刷精度区间内解析求解：存在取值能复算 → 非缺陷（舍入/截断）。

    不能只采样区间四角或中点：边界解常落在区间内部（m2053 实测 1002 号行的 7.5%
    就落在四角之内，被四角采样误判成缺陷）。这里对每种约定反解 a = b*(1+e) 或
    b = a/(1+e)，再判是否落在对应打印区间内，等价于区间求根。
    """
    for target in (stated, -stated):
        e = target / 100.0
        if e == -1.0:
            continue
        for b in (b_iv[0], b_iv[1]):
            a = b * (1.0 + e)
            if a_iv[0] <= a <= a_iv[1]:
                return ROUNDING_ARTIFACT
        for a in (a_iv[0], a_iv[1]):
            b = a / (1.0 + e)
            if b_iv[0] <= b <= b_iv[1]:
                return ROUNDING_ARTIFACT
    return None


@dataclass
class Calibration:
    err_col: int
    val_col: int
    ref_col: int
    matched: int
    total: int

    @property
    def rate(self) -> float:
        return self.matched / self.total if self.total else 0.0


def audit_table_row(cells: Sequence[str], tol: float = ARITH_TOL) -> Optional[Dict[str, object]]:
    """逐行紧结构裁决（测试/单行复核用）：恰好 1 个百分数 + 恰好 2 个数值。

    批量审计走 calibrate_table + audit_tables（表级列标定），逐行版只用于
    「结构被强制」的单行复核与回归测试。
    """
    pcts = [(i, parse_percent(c)) for i, c in enumerate(cells)]
    pcts = [(i, v) for i, v in pcts if v is not None]
    if len(pcts) != 1:
        return None
    pct_idx, stated = pcts[0]
    nums = [(i, parse_number(c)) for i, c in enumerate(cells) if i != pct_idx]
    nums = [(i, v) for i, v in nums if v is not None]
    if len(nums) != 2:
        return None
    a, b = nums[0][1], nums[1][1]
    if a == 0.0 or b == 0.0:
        return None
    e = (a - b) / b * 100.0
    verdict = classify(stated, e, tol)
    if verdict is None:
        verdict = (classify_interval(stated, number_interval(cells[nums[0][0]]) or (a, a),
                                     number_interval(cells[nums[1][0]]) or (b, b), tol)
                   or (_ for _ in ()).throw(StopIteration) if False else None)
    return {"kind": verdict or MISMATCH, "a": a, "b": b, "stated_pct": stated,
            "recomputed_pct": e, "abs_diff": abs(abs(stated) - abs(e)),
            "row": " | ".join(cells)[:200]}


def calibrate_table(table: Table, tol: float = ARITH_TOL, min_rows: int = 3,
                    min_rate: float = 0.6) -> Optional[Calibration]:
    """找误差列 + 最能复算误差的 (值列, 参考列) 组合。

    标定是**机械**的：枚举全部 (值列, 参考列) 组合，命中数取「三种约定任一命中」的
    行数；只有命中率 ≥ min_rate 且可判行数 ≥ min_rows 才算「可认证表」，否则整表跳过。
    符号/绝对值口径不参与标定（逐行判），避免表级约定压掉少数派行。
    """
    err_col = None
    for idx, h in enumerate(table.header):
        if _ERR_HEADER_RE.search(h or ""):
            err_col = idx
            break
    if err_col is None:
        return None
    best: Optional[Calibration] = None
    cols = [c for c in range(len(table.header)) if c != err_col]
    for val_col in cols:
        for ref_col in cols:
            if val_col == ref_col:
                continue
            matched = total = 0
            for row in table.rows:
                st = parse_percent(table.cell(row, err_col))
                a = parse_number(table.cell(row, val_col))
                b = parse_number(table.cell(row, ref_col))
                if st is None or a is None or b is None or a == 0.0 or b == 0.0:
                    continue
                if abs(math.log10(abs(a) / abs(b))) > 2.0:
                    continue  # 数量级差 >100 倍：单位不同的两列，不构成 (值, 参考值)
                total += 1
                if classify(st, (a - b) / b * 100.0, tol) is not None:
                    matched += 1
            if total < min_rows:
                continue
            cand = Calibration(err_col, val_col, ref_col, matched, total)
            if cand.rate < min_rate:
                continue
            if best is None or cand.matched > best.matched:
                best = cand
    return best


def audit_tables(reports: Sequence[Report], tol: float = ARITH_TOL) -> Tuple[List[ArithFlag], int, int, int]:
    """全语料表级标定 + 逐行裁决。

    返回 (flags, n_tables_calibrated, n_rows_checked, n_rows_skipped)。
    仅可认证表的数据行入账；不可认证表的行计入 skipped（能力边界，不冒充缺陷）。
    """
    flags: List[ArithFlag] = []
    n_tab = n_checked = n_skipped = 0
    for r in reports:
        for t in parse_tables(r.markdown):
            cal = calibrate_table(t, tol)
            if cal is None:
                n_skipped += len(t.rows)
                continue
            n_tab += 1
            for row in t.rows:
                st = parse_percent(t.cell(row, cal.err_col))
                a = parse_number(t.cell(row, cal.val_col))
                b = parse_number(t.cell(row, cal.ref_col))
                if st is None or a is None or b is None or a == 0.0 or b == 0.0:
                    n_skipped += 1
                    continue
                n_checked += 1
                e = (a - b) / b * 100.0
                verdict = classify(st, e, tol)
                if verdict is None:
                    a_iv = number_interval(t.cell(row, cal.val_col)) or (a, a)
                    b_iv = number_interval(t.cell(row, cal.ref_col)) or (b, b)
                    verdict = classify_interval(st, a_iv, b_iv, tol)
                if verdict is None and _within(st, abs(a - b) * 100.0, tol):
                    verdict = PERCENT_POINT_CONFUSION
                kind = verdict if verdict is not None else MISMATCH
                rec = e
                flags.append(ArithFlag(
                    report_id=r.id, scenario_key=r.scenario_key, kind=kind,
                    a=a, b=b, stated_pct=st, recomputed_pct=rec,
                    abs_diff=abs(abs(st) - abs(rec)),
                    row=" | ".join(row)[:200],
                ))
    flags.sort(key=lambda f: (f.kind != MISMATCH, -(f.abs_diff if f.abs_diff == f.abs_diff else 0.0)))
    return flags, n_tab, n_checked, n_skipped


# ---------------------------------------------------------------- 主流程


def analyze(
    reports: Sequence[Report],
    dup_threshold: float = DUP_THRESHOLD,
    near_threshold: float = NEAR_THRESHOLD,
    arith_tol: float = ARITH_TOL,
) -> CorpusResult:
    sigs = [shingles(r.markdown) for r in reports]
    clusters = cluster_duplicates(reports, sigs, dup_threshold, near_threshold)
    flags, n_tab, checked, skipped = audit_tables(reports, arith_tol)
    return CorpusResult(
        n_reports=len(reports),
        n_missions=len({r.mission for r in reports if r.mission is not None}),
        n_scenario_keys=len({r.scenario_key for r in reports if r.scenario_key}),
        clusters=clusters,
        flags=flags,
        n_tables_calibrated=n_tab,
        n_rows_checked=checked,
        n_rows_skipped=skipped,
        dup_threshold=dup_threshold,
        arith_tol=arith_tol,
    )


def load_corpus_json(path: str) -> List[Report]:
    """JSON 快照：[{id, mission_id, scenario_key, title, summary, markdown, ...}]。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = raw.get("reports") or raw.get("items") or []
    out: List[Report] = []
    for r in raw:
        out.append(Report(
            id=int(r["id"]),
            mission=(int(r["mission_id"]) if r.get("mission_id") not in (None, "", -1) else None),
            scenario_key=str(r.get("scenario_key") or ""),
            title=str(r.get("title") or ""),
            summary=str(r.get("summary") or ""),
            markdown=str(r.get("markdown") or ""),
            created_at=str(r.get("created_at") or ""),
            status=str(r.get("status") or "published"),
        ))
    out.sort(key=lambda r: r.id)
    return out


def load_corpus_db(dsn: str = "tensorplatform") -> List[Report]:
    """只读从 PostgreSQL 导出语料（COPY ... TO STDOUT CSV），不改库。"""
    sql = (
        "copy (select id, coalesce(mission_id, -1), coalesce(scenario_key,''), "
        "status, created_at::text, title, summary, markdown from tensor_reports order by id) "
        "to stdout with (format csv)"
    )
    proc = subprocess.run(
        ["psql", "-U", os.environ.get("PGUSER", "postgres"), "-d", dsn, "-A", "-t", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    csv.field_size_limit(10 ** 8)
    out: List[Report] = []
    for row in csv.reader(io.StringIO(proc.stdout)):
        if len(row) < 8:
            continue
        out.append(Report(
            id=int(row[0]),
            mission=(int(row[1]) if row[1] not in ("", "-1") else None),
            scenario_key=row[2],
            status=row[3],
            created_at=row[4],
            title=row[5],
            summary=row[6],
            markdown=row[7],
        ))
    return out


def gate_check(result: CorpusResult, baseline: Dict[str, object]) -> Tuple[bool, List[str]]:
    """回归闸门：重复簇不得新增；算术缺陷数不得超过基线 + 允许增量。"""
    problems: List[str] = []
    base_keys = {tuple(sorted(c.get("ids", []))) for c in baseline.get("clusters", [])}
    cur_keys = {tuple(sorted(c.ids)) for c in result.clusters}
    new = cur_keys - base_keys
    if new:
        problems.append(f"新增重复簇 {len(new)} 个（基线 {len(base_keys)}）：{sorted(new)[:5]}")
    allow = float(baseline.get("defect_slack", 0.0))
    base_def = int(baseline.get("n_arith_mismatch", 0))
    if result.n_defects > base_def + allow:
        problems.append(f"表内算术缺陷 {result.n_defects} 超基线 {base_def}+{allow:g}")
    return (not problems), problems


def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="报告语料完整性审计（重复发布 + 表内算术自洽）")
    ap.add_argument("--corpus", help="JSON 语料快照；缺省用 --from-db")
    ap.add_argument("--from-db", action="store_true", help="从 PostgreSQL 只读导出语料")
    ap.add_argument("--db", default="tensorplatform")
    ap.add_argument("--dup-threshold", type=float, default=DUP_THRESHOLD)
    ap.add_argument("--arith-tol", type=float, default=ARITH_TOL)
    ap.add_argument("--out", help="结果 JSON 路径")
    ap.add_argument("--gate", help="基线 JSON；给定时做回归判定，失败退出码 1")
    args = ap.parse_args(argv)

    if args.corpus:
        corpus = load_corpus_json(args.corpus)
    elif args.from_db:
        corpus = load_corpus_db(args.db)
    else:
        ap.error("需要 --corpus 或 --from-db")

    res = analyze(corpus, dup_threshold=args.dup_threshold, arith_tol=args.arith_tol)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res.to_dict(), f, ensure_ascii=False, indent=1)
    print(json.dumps(res.summary(), ensure_ascii=False))

    if args.gate:
        with open(args.gate, "r", encoding="utf-8") as f:
            baseline = json.load(f)
        ok, problems = gate_check(res, baseline)
        for p in problems:
            print("GATE:", p, file=sys.stderr)
        if not ok:
            return 1
        print("GATE: PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())