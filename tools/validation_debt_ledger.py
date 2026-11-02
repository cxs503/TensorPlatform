#!/usr/bin/env python3
"""验证欠账台账（data 席）：把平台报告里的「局限 / 注意点 / 待办」节抽成机器可判的台账。

动机
----
平台已发布 1000+ 篇论文级报告，几乎每篇都带「注意点 / 局限 / 适用范围 / 待办」节。
这是全平台最便宜、最真实的一手「验证欠账」语料，但长期只能人工翻页：
owner 的常设优先级是「验证欠账 > 功能补全 > 失效测试」，而欠账本身没有台账。

本工具做四件事（全部离线、只读）：

1. **分节抽取**：按 markdown 标题定位章节，标题命中 ``局限/注意点/待办/未完成/风险/
   适用范围/limitation`` 等关键词即视为欠账节；节内按列表项 / 表格行 / 段落切成条目。
2. **条目归税**：每条欠账按关键词映射到
   - 21 项平台能力（与 ``capabilities`` 审计口径同名，如 ``calc.resistance``）；
   - 9 个船舶总体性能学科（阻力/推进/耐波/操纵/稳性/结构/振动噪声/绿色航行/冰区航行）；
   - 严重度分级：``STUB``/``MOCK``/``WITHHELD``/``ASSUMPTION``/``LIMIT``。
3. **闭合交叉验证**：条目里出现的仓库相对路径（``x/y.py`` 等）逐个 ``os.path.exists``
   复核——**声称有产物但产物不在工作树**是"伪闭合"，单独计数，不当成已闭合。
4. **欠账聚类**：跨报告同义条目按词元 Jaccard 聚成一簇，**复发报告数**即欠账权重
   （一条欠账在 N 篇独立报告里重复出现 ⇒ 长期未清偿）。

设计与纪律
----------
* 只读：既不改数据库也不改报告；数据源可换成 ``--from-json`` 快照，便于离线复算与测试。
* 零第三方依赖：仅标准库；matplotlib 只在 ``--figures`` 时按需导入。
* 可判定：抽取规则、归税规则、聚类阈值都是显式常量，测试用内联夹具钉死行为。
* 不臆造：条目原文逐字保留，``text`` 不做任何改写；严重度只是"有利于复核"的标签，
  最终判级仍由人/审计单确认。

用法
----
    python3 tools/validation_debt_ledger.py --out artifacts/validation_debt
    python3 tools/validation_debt_ledger.py --from-json reports.json --out /tmp/ledger
    python3 tools/validation_debt_ledger.py --out artifacts/validation_debt --figures

产物
----
* ``artifacts/validation_debt/ledger.json``：逐条目 + 统计 + 聚类 + 交叉验证结果
* ``artifacts/validation_debt/ledger.md``：人读摘要表
* ``--figures``：``artifacts/validation_debt/fig_debt_by_domain.png`` 等
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"

# ── 1. 分节规则 ────────────────────────────────────────────────────────────────
# 标题命中任一关键词即视为「欠账节」。中文与英文混排都要吃（报告双语标题常见）。
SECTION_KEYWORDS: Tuple[str, ...] = (
    "局限", "注意点", "待办", "未完成", "风险", "适用范围", "不足",
    "limitation", "caveat", "todo", "open issue", "known issue",
)
# 明确不是欠账的节（即使标题里带关键词也剔除），例如"局限性与后续计划已全部关闭"
SECTION_EXCLUDE_RE = re.compile(r"^\s*(\d+[\.\)]?\s*)?(误差判定|结论|参考)", re.I)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[\.\)]\s+|\|\s*)")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$")

# ── 2. 归税规则 ────────────────────────────────────────────────────────────────
# 能力名与平台 capabilities 审计口径一致（audit_capabilities 工具的 21 项）。
CAPABILITY_RULES: Tuple[Tuple[str, str], ...] = (
    (r"calc\.resistance|阻力计算|总阻力|Rt\b|Holtrop|ITTC-?78", "calc.resistance"),
    (r"calc\.stability|稳性|GZ\b|完整稳性|GM\b|初稳性", "calc.stability"),
    (r"propeller\.open_water|敞水|螺旋桨|Wageningen|KT\b|KQ\b|自航", "propeller.open_water"),
    (r"geometry\.hydro_curves|静水力曲线|邦戎|排水量曲线", "geometry.hydro_curves"),
    (r"geometry\.verify_official|官方几何|几何校验", "geometry.verify_official"),
    (r"geometry\.generate|型线生成|型线", "geometry.generate"),
    (r"geometry\.list_models|模型清单|标准模型库", "geometry.list_models"),
    (r"shiphull\.generate|船体生成|船壳", "shiphull.generate"),
    (r"stdmodels\.lookup|标准船型|KCS\b|KVLCC2|DTMB|SUBOFF", "stdmodels.lookup"),
    (r"dev\.gate_5415|5415 网关|5415 门", "dev.gate_5415"),
    (r"ai\.predict|AI 预测|机器学习|代理模型", "ai.predict"),
    (r"methods\.computation|计算方法|求解器", "methods.computation"),
    (r"knowledge\.docs|知识库|文档检索", "knowledge.docs"),
    (r"gov\.stats|治理统计", "gov.stats"),
    (r"\bstub\b|空壳|占位实现", "misc.stubs"),
)

DOMAIN_RULES: Tuple[Tuple[str, str], ...] = (
    (r"阻力|resistance|\bRt\b|Holtrop|ITTC", "阻力"),
    (r"推进|propuls|螺旋桨|敞水|自航|推力|扭矩|waterjet|喷水", "推进"),
    (r"耐波|seakeeping|横摇|纵摇|垂荡|波浪|sloshing|晃荡|谱矩", "耐波"),
    (r"操纵|maneuver|MMG|回转|Z 形|zigzag|航向", "操纵"),
    (r"稳性|stability|GZ\b|GM\b|倾覆|破舱", "稳性"),
    (r"结构|structure|屈曲|buckling|疲劳|疲劳|强度|有限元|FEM", "结构"),
    (r"振动|噪声|acoustic|vibration|声学|模态", "振动噪声"),
    (r"绿色|排放|EEXI|CII|能效|EEDI|碳", "绿色航行"),
    (r"冰区|ice\b|冰阻力|冰载荷|DEM", "冰区航行"),
    # 元陈述桶：这些条目谈的是平台/报告/治理自身，不属于任何物理学科。
    # 单列出来是为了让「未归类」只统计真·无标签条目，而不是把治理欠账混进噪声。
    (r"报告|质检|派单|提案|门禁|战役|谱系|审计|席位|预算|mission|prereg|看板|台账", "平台治理"),
)

# 图里用 ASCII 标签，避免 matplotlib 缺 CJK 字体导致方框（数值不变）。
DOMAIN_LABEL_EN: Dict[str, str] = {
    "阻力": "resistance", "推进": "propulsion", "耐波": "seakeeping", "操纵": "maneuvering",
    "稳性": "stability", "结构": "structure", "振动噪声": "vib-acoustics",
    "绿色航行": "green-shipping", "冰区航行": "ice", "平台治理": "platform-gov",
}

# 严重度：顺序即优先级（STUB/MOCK 最硬，LIMIT 最软）。命中多条时取最硬。
SEVERITY_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("STUB", r"\bSTUB\b|空壳|占位|未实现|未接|尚未实现|not implemented", "空壳/未实现"),
    ("MOCK", r"\bMOCK\b|硬编码|伪数据|假数据|伪造|随机代替|placeholder", "硬编码/伪造"),
    ("WITHHELD", r"WITHHELD|缺证据|无法复核|未能验证|未验证|未实测|未比对|无基准|未测试|未做|未标定|未覆盖|未开展", "缺证据/未验证"),
    ("ASSUMPTION", r"假设|假定|近似|简化|外推|经验公式|forced|兜底", "假设/近似"),
    ("LIMIT", r"局限|限制|适用范围|不适用|偏差|误差|仅适用|精度受", "局限声明"),
)
SEVERITY_ORDER = {"STUB": 0, "MOCK": 1, "WITHHELD": 2, "ASSUMPTION": 3, "LIMIT": 4}

# ── 3. 闭合交叉验证 ────────────────────────────────────────────────────────────
ARTIFACT_RE = re.compile(
    r"(?<![\w/])((?:[\w.\-]+/)*[\w.\-]+\.(?:py|json|md|csv|npz|npy|png|yaml|yml|sh|toml))"
)
CLOSED_HINTS_RE = re.compile(
    r"已关闭|已清偿|已修复|已闭合|已改为|已修正|已纠正|已重跑|已回填"
    r"|clos(?:ed|ure)\b|resolved|already fixed", re.I)
# 正向自证句不是欠账：抽检（m1705）里"证明…正确 ✓""全程无 NaN""全部 PASS"曾被误收。
# m1860 抽检补入 ✅(U+2705) 与「未改写测试」类英文自证句：两者都只声明"已达标"，不含待办。
POSITIVE_HINTS_RE = re.compile(
    r"[✓✔✅]|全程无|无 NaN|全部 ?PASS|证明.{0,12}(?:正确|一致|通过)"
    r"|no test (?:was|is) (?:rewritten|modified|xfailed)|nothing was xfailed", re.I)
# 纯表格残片（如 "网格/收敛性 | 273 | 42.1%"）不构成可判条目。
TABLE_FRAGMENT_RE = re.compile(r"^[^。；;]{0,40}\|\s*[\d.]+%?\s*(?:\|\s*[\d.]+%?\s*)*$")
# 数值表格行（≥3 列且至少一格是纯数字/百分比/科学计数）：逐行抽会把数据表每行
# 都变成一条假欠账（m1860 抽检实例："ship-scale accounting (CA added) | +15.5 | 56.39 | 0"）。
TABLE_ROW_CELLS_RE = re.compile(r"\s*\|\s*")
NUMERIC_CELL_RE = re.compile(r"^[+\-−]?\d+(?:\.\d+)?(?:%|e[-+]?\d+)?$", re.I)
# 欠账指标词：同时用于"文字表格行 vs 表头行"的判别（见 table_row_kind）。
DEBT_MARKER_RE = re.compile(
    r"局限|限制|未|缺|待|风险|假设|近似|误差|偏差|不适用|仅适用|不可|无法|无法确认|TODO", re.I)
# 守卫：正向/闭合判据只在条目里没有"仍然欠着"的活口时生效，防止
# "✅ 已可用，但 X 尚未验证" 这类复合句被整条丢掉（m1860 抽检识别的风险）。
LIVE_DEBT_GUARD_RE = re.compile(r"尚未|仍未|仍需|未覆盖|未验证|未实现|未标定|待办|TODO|须补|应补")

MIN_ITEM_CHARS = 10
CLUSTER_JACCARD = 0.5
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_.]{2,}|[\u4e00-\u9fff]{2,}")


# ── 抽取核心 ──────────────────────────────────────────────────────────────────
def _norm_title(t: str) -> str:
    return re.sub(r"^[\s\d.、\)]*", "", t).strip()


def is_debt_section(title: str) -> bool:
    """标题是否属于欠账节。"""
    if SECTION_EXCLUDE_RE.match(title):
        return False
    low = title.lower()
    return any(k.lower() in low for k in SECTION_KEYWORDS)


def split_sections(markdown: str) -> List[Tuple[str, str]]:
    """按 markdown 标题切节，返回 [(标题, 正文)]；标题按层级压平（取最近的标题链）。"""
    sections: List[Tuple[str, str]] = []
    cur_title: Optional[str] = None
    buf: List[str] = []
    for line in (markdown or "").splitlines():
        m = HEADING_RE.match(line)
        if m:
            if cur_title is not None:
                sections.append((cur_title, "\n".join(buf)))
            cur_title = m.group(2).strip()
            buf = []
            continue
        if cur_title is not None:
            buf.append(line)
    if cur_title is not None:
        sections.append((cur_title, "\n".join(buf)))
    return sections


def is_numeric_table_row(line: str) -> bool:
    """数据表行判定：≥3 列且至少一格是纯数字/百分比/科学计数。

    这类行（如 ``ship-scale accounting (CA added) | +15.5 | 56.39 | 0``）逐行抽出来
    只有数字没有命题，是可判性最差的一类假欠账；而"风险登记表"式的文字行
    （``R11 | 根目录级 figures 写入 | LOW | LIKELY | ...``）仍是真实欠账，必须保留。
    区别只在"有没有数值格"，不用"有没有竖线"，这是本判定唯一的分界线。
    """
    if line.count("|") < 2:
        return False
    cells = [c.strip() for c in TABLE_ROW_CELLS_RE.split(line.strip().strip("|")) if c.strip()]
    if len(cells) < 3:
        return False
    return any(NUMERIC_CELL_RE.match(c) for c in cells[1:])


def table_row_kind(line: str) -> Optional[str]:
    """把"带竖线的行"分三类，决定它是数据、表头还是承载命题的文字行。

    * ``numeric``：≥3 列且至少一格是纯数字 → 数据行，丢弃（逐行抽只会产出数字碎片）。
    * ``header``：各格都很短且无句读/指标词 → 表头行，丢弃。
    * ``text``：长句或含指标词的表格行（风险登记表 ``R11 | ... | LOW | LIKELY``）
      → **保留**，但必须自成一条，不能与上一行续接（否则会拼出"表头+正文"的怪条目，
      m1860 抽检的 after 阶段冒出过这样一条）。
    * ``None``：不是表格行（竖线 <2 或列数 <3）。
    """
    if line.count("|") < 2:
        return None
    cells = [c.strip() for c in TABLE_ROW_CELLS_RE.split(line.strip().strip("|")) if c.strip()]
    if len(cells) < 3:
        return None
    if any(NUMERIC_CELL_RE.match(c) for c in cells[1:]):
        return "numeric"
    longest = max(len(c) for c in cells)
    # 判据只看"句读 / 长句"，不看指标词："Ct 误差 (%)" 这类表头格也含指标词，
    # 用指标词会把表头误判成欠账行（m1860 测试 test_header_row_is_dropped 抓到的假阴性）。
    has_prose = any(re.search(r"[。；;，,：:]", c) for c in cells)
    return "text" if (has_prose or longest > 20) else "header"


def extract_items_diag(body: str) -> Tuple[List[str], Dict[str, int]]:
    """把节正文切成条目，并返回丢弃统计（供台账 stats 记账，不吞证据）。

    丢弃分三类，逐类计数：``table_rows``（数值表行）、``closed``（已闭合/已修好）、
    ``positive``（正向自证句）。三者都只在条目里没有"仍然欠着"的活口时生效。
    """
    diag = {"table_rows": 0, "table_headers": 0, "closed": 0, "positive": 0}
    items: List[str] = []
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if TABLE_SEP_RE.match(line):
            continue
        kind = table_row_kind(line)
        if kind in ("numeric", "header"):
            diag["table_rows" if kind == "numeric" else "table_headers"] += 1
            continue
        if kind == "text":
            cells = [c.strip() for c in TABLE_ROW_CELLS_RE.split(line.strip().strip("|")) if c.strip()]
            items.append(" ".join(cells))
            continue
        if LIST_ITEM_RE.match(line):
            text = re.sub(r"^\s*(?:[-*+]\s+|\d+[\.\)]\s+|\|\s*)", "", line).strip()
            text = text.rstrip("|").strip()
            if text:
                items.append(text)
        elif items:
            # 续行：并入上一条，避免把一个句子拆成两条欠账
            items[-1] = (items[-1] + " " + line.strip()).strip()
        else:
            items.append(line.strip())
    out: List[str] = []
    for it in items:
        it = re.sub(r"\s+", " ", it).strip(" |")
        if len(it) < MIN_ITEM_CHARS:
            continue
        guarded = bool(LIVE_DEBT_GUARD_RE.search(it))
        if not guarded and CLOSED_HINTS_RE.search(it):
            diag["closed"] += 1
            continue
        if not guarded and POSITIVE_HINTS_RE.search(it):
            diag["positive"] += 1
            continue
        if TABLE_FRAGMENT_RE.match(it):
            continue
        out.append(it)
    return out, diag


def extract_items(body: str) -> List[str]:
    """把节正文切成条目：列表项 / 表格行 / 段落；续行并入上一条。"""
    return extract_items_diag(body)[0]


def map_tags(text: str, rules: Sequence[Tuple[str, ...]]) -> List[str]:
    """按规则表把文本映射为标签（保序去重）。规则项可以是 2 元或 3 元组。"""
    hits: List[str] = []
    for rule in rules:
        if re.search(rule[0], text, re.I):
            if rule[1] not in hits:
                hits.append(rule[1])
    return hits


def severity_of(text: str) -> Tuple[str, str]:
    """返回 (级别, 说明)；未命中任何规则时为 LIMIT/未分类。"""
    for level, pat, why in SEVERITY_RULES:
        if re.search(pat, text, re.I):
            return level, why
    return "LIMIT", "未分类声明"


def artifact_refs(text: str) -> List[str]:
    return sorted(set(m.group(1) for m in ARTIFACT_RE.finditer(text)))


def is_path_like(ref: str) -> bool:
    """含目录分隔的引用才当作可定位的仓库相对路径；裸文件名（`acceptance.json`）无法定位。"""
    return "/" in ref.strip("./")


def check_artifacts(refs: Iterable[str], repo_root: str,
                    search_roots: Sequence[str] = ()) -> Dict[str, Any]:
    """逐引用检查存在性。

    返回 ``{ref: {"path_like": bool, "exists": bool, "roots_checked": n}}``。
    平台报告语料跨多个仓/工作树（TensorLBM/TensorPhysics/…），单仓根必然误判，
    所以允许给多个搜索根；**裸文件名不参与"伪闭合"计数**（无法定位 = 证据不足，
    不是罪证——这一区分是本工具诚实性的关键）。
    """
    roots = [repo_root] + [r for r in search_roots if r]
    out: Dict[str, Any] = {}
    for r in refs:
        rel = r.lstrip("./")
        exists = any(os.path.exists(os.path.join(root, rel)) for root in roots)
        out[r] = {"path_like": is_path_like(r), "exists": exists, "roots_checked": len(roots)}
    return out


def tokens(text: str) -> set:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def cluster_items(items: List[Dict[str, Any]], threshold: float = CLUSTER_JACCARD) -> List[Dict[str, Any]]:
    """同义欠账聚类（union-find on token Jaccard）。返回按复发报告数降序的簇。"""
    toks = [tokens(it["text"]) for it in items]
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if jaccard(toks[i], toks[j]) >= threshold:
                union(i, j)

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(i)

    clusters: List[Dict[str, Any]] = []
    for root, idxs in groups.items():
        reports = sorted({items[i]["report_id"] for i in idxs})
        rep = min(idxs, key=lambda i: (-len(items[i]["text"]), items[i]["report_id"]))
        caps = sorted({c for i in idxs for c in items[i]["capabilities"]})
        doms = sorted({d for i in idxs for d in items[i]["domains"]})
        sev = Counter(items[i]["severity"] for i in idxs)
        worst = min(sev, key=lambda s: SEVERITY_ORDER.get(s, 9))
        missing = sorted({ref for i in idxs for ref, ok in items[i]["artifact_exists"].items() if not ok})
        clusters.append({
            "cluster_id": 0,  # 排序后统一编号
            "representative": items[rep]["text"],
            "n_reports": len(reports),
            "n_items": len(idxs),
            "report_ids": reports,
            "capabilities": caps,
            "domains": doms,
            "severity_counts": dict(sorted(sev.items())),
            "worst_severity": worst,
            "missing_artifacts": missing,
        })
    clusters.sort(key=lambda c: (-c["n_reports"], -c["n_items"], c["representative"]))
    for k, c in enumerate(clusters, 1):
        c["cluster_id"] = k
    return clusters


def build_ledger(reports: List[Dict[str, Any]], repo_root: str = ".",
                 search_roots: Sequence[str] = ()) -> Dict[str, Any]:
    """reports: [{id, title, markdown, mission_id?, scenario_key?}] → 台账 dict。"""
    items: List[Dict[str, Any]] = []
    dropped = {"table_rows": 0, "table_headers": 0, "closed": 0, "positive": 0}
    n_with = 0
    for rep in reports:
        rid = rep.get("id")
        md = rep.get("markdown") or ""
        found_any = False
        for title, body in split_sections(md):
            if not is_debt_section(_norm_title(title)):
                continue
            found_any = True
            extracted, diag = extract_items_diag(body)
            for k in dropped:
                dropped[k] += diag[k]
            for text in extracted:
                sev, why = severity_of(text)
                items.append({
                    "report_id": rid,
                    "mission_id": rep.get("mission_id"),
                    "scenario_key": rep.get("scenario_key"),
                    "section": _norm_title(title),
                    "text": text,
                    "severity": sev,
                    "severity_reason": why,
                    "capabilities": map_tags(text, CAPABILITY_RULES),
                    "domains": map_tags(text, DOMAIN_RULES),
                    "artifact_exists": check_artifacts(artifact_refs(text), repo_root, search_roots),
                })
        if found_any:
            n_with += 1

    items.sort(key=lambda it: (SEVERITY_ORDER.get(it["severity"], 9), it["report_id"] or 0, it["text"]))
    clusters = cluster_items(items)
    all_refs: Dict[str, Dict[str, Any]] = {}
    for it in items:
        for ref, info in it["artifact_exists"].items():
            all_refs[ref] = info
    path_like = {r: i for r, i in all_refs.items() if i["path_like"]}
    missing = sorted(r for r, i in path_like.items() if not i["exists"])
    stats = {
        "reports_scanned": len(reports),
        "reports_with_debt_section": n_with,
        "items_total": len(items),
        "clusters_total": len(clusters),
        "items_per_report": round(len(items) / max(n_with, 1), 2),
        # 丢弃记账：可判性审计（m1860）要求"丢了什么"必须可见，不能只报剩多少。
        "dropped_table_rows": dropped["table_rows"],
        "dropped_table_headers": dropped["table_headers"],
        "dropped_closed_statements": dropped["closed"],
        "dropped_positive_statements": dropped["positive"],
        "by_severity": dict(sorted(Counter(it["severity"] for it in items).items())),
        "by_domain": dict(sorted(Counter(d for it in items for d in it["domains"]).items(), key=lambda kv: -kv[1])),
        "by_capability": dict(sorted(Counter(c for it in items for c in it["capabilities"]).items(), key=lambda kv: -kv[1])),
        "items_without_domain": sum(1 for it in items if not it["domains"]),
        "artifact_refs_total": len(all_refs),
        "artifact_refs_path_like": len(path_like),
        "artifact_refs_unlocatable_bare_name": len(all_refs) - len(path_like),
        "artifact_refs_missing": missing,
        "recurrent_clusters_ge2": sum(1 for c in clusters if c["n_reports"] >= 2),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules": {
            "section_keywords": list(SECTION_KEYWORDS),
            "min_item_chars": MIN_ITEM_CHARS,
            "cluster_jaccard": CLUSTER_JACCARD,
            "severity_priority": [k for k, _, _ in SEVERITY_RULES],
        },
        "stats": stats,
        "clusters": clusters,
        "items": items,
    }


# ── 数据源 ────────────────────────────────────────────────────────────────────
def load_reports_psql(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """只读拉取平台报告。口令取 TP_PG_PASSWORD（与 psql 客户端一致）。"""
    sql = (f"SELECT COALESCE(json_agg(t), '[]') FROM ("
           f"SELECT id, title, markdown, mission_id, scenario_key FROM tensor_reports "
           f"ORDER BY id{'' if limit is None else f' LIMIT {int(limit)}'}) t;")
    env = dict(os.environ)
    env.setdefault("PATH", "/usr/bin:/bin")
    p = subprocess.run(["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform",
                        "-tAc", sql], capture_output=True, text=True, env=env, timeout=180)
    if p.returncode != 0:
        raise SystemExit(f"psql 失败: {p.stderr.strip()[:400]}")
    data = json.loads(p.stdout or "[]")
    return data


def load_reports_json(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("reports") or data.get("data") or []
    return data


# ── 输出 ──────────────────────────────────────────────────────────────────────
def render_markdown(ledger: Dict[str, Any], top: int = 20) -> str:
    s = ledger["stats"]
    lines = [
        "# 验证欠账台账（自动生成，勿手改）",
        "",
        f"生成时间 {ledger['generated_at']} · schema v{ledger['schema_version']}",
        "",
        f"- 扫描报告 **{s['reports_scanned']}** 篇，含欠账节 **{s['reports_with_debt_section']}** 篇",
        f"- 欠账条目 **{s['items_total']}** 条，聚类 **{s['clusters_total']}** 簇"
        f"（复发 ≥2 篇的簇 {s['recurrent_clusters_ge2']} 个）",
        f"- 严重度分布 {s['by_severity']}",
        f"- 产物引用 {s['artifact_refs_total']} 条：可定位（含路径）{s['artifact_refs_path_like']} 条，"
        f"其中工作树内找不到 {len(s['artifact_refs_missing'])} 条；"
        f"裸文件名 {s['artifact_refs_unlocatable_bare_name']} 条（无法定位，不计伪闭合）",
        "",
        "## 学科分布（按条目数）",
        "",
        "| 学科 | 条目数 |",
        "|---|---|",
    ]
    for k, v in s["by_domain"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["| 未归类 | " + str(s["items_without_domain"]) + " |", "", "## 能力分布（Top 10）", "",
              "| 能力 | 条目数 |", "|---|---|"]
    for k, v in list(s["by_capability"].items())[:10]:
        lines.append(f"| `{k}` | {v} |")
    lines += ["", f"## 复发欠账簇 Top {top}", "", "| # | 复发报告数 | 条目 | 最硬严重度 | 学科 | 能力 | 代表原文（截断） |",
              "|---|---|---|---|---|---|---|"]
    for c in ledger["clusters"][:top]:
        rep = c["representative"].replace("|", "／")
        if len(rep) > 90:
            rep = rep[:90] + "…"
        lines.append("| {i} | {n} | {m} | {s} | {d} | {c} | {t} |".format(
            i=c["cluster_id"], n=c["n_reports"], m=c["n_items"], s=c["worst_severity"],
            d="、".join(c["domains"]) or "—", c="、".join(c["capabilities"]) or "—", t=rep))
    if s["artifact_refs_missing"]:
        lines += ["", "## 伪闭合：条目声称有产物但工作树内不存在", "", "| 路径 |", "|---|"]
        lines += [f"| `{p}` |" for p in s["artifact_refs_missing"][:40]]
    lines.append("")
    return "\n".join(lines)


def write_outputs(ledger: Dict[str, Any], out_dir: str, top: int = 20) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "ledger.json")
    mp = os.path.join(out_dir, "ledger.md")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=1)
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(ledger, top=top))
    return {"ledger_json": jp, "ledger_md": mp}


def make_figures(ledger: Dict[str, Any], out_dir: str) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = ledger["stats"]
    paths: List[str] = []

    doms = list(s["by_domain"].items())
    if doms:
        fig, ax = plt.subplots(figsize=(7.5, 3.8))
        ax.bar([DOMAIN_LABEL_EN.get(d, d) for d, _ in doms], [n for _, n in doms], color="#3d6fa8")
        ax.set_ylabel("debt items")
        ax.set_title("Validation debt items by discipline")
        ax.tick_params(axis="x", rotation=30)
        fig.tight_layout()
        p = os.path.join(out_dir, "fig_debt_by_domain.png")
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)

    top = ledger["clusters"][:10]
    if top:
        fig, ax = plt.subplots(figsize=(8, 4.2))
        labels = [f"#{c['cluster_id']}" for c in top]
        vals = [c["n_reports"] for c in top]
        ax.barh(labels[::-1], vals[::-1], color="#a8483d")
        ax.set_xlabel("reports mentioning the same debt")
        ax.set_title("Top-10 recurrent validation debt clusters")
        fig.tight_layout()
        p = os.path.join(out_dir, "fig_recurrent_debt.png")
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)

    sev = list(s["by_severity"].items())
    if sev:
        fig, ax = plt.subplots(figsize=(5.5, 3.2))
        ax.bar([k for k, _ in sev], [v for _, v in sev], color="#5a5a5a")
        ax.set_ylabel("items")
        ax.set_title("Debt items by severity class")
        fig.tight_layout()
        p = os.path.join(out_dir, "fig_debt_severity.png")
        fig.savefig(p, dpi=150)
        plt.close(fig)
        paths.append(p)
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="验证欠账台账：报告局限节 → 机器可判台账")
    ap.add_argument("--from-json", help="离线报告快照（list 或 {reports:[...]})")
    ap.add_argument("--limit", type=int, default=None, help="从数据库读取时的报告条数上限")
    ap.add_argument("--out", default="artifacts/validation_debt")
    ap.add_argument("--repo-root", default=".", help="产物存在性检查的根目录")
    ap.add_argument("--search-roots", default="", help="额外搜索根（逗号分隔，多仓工作树语料用）")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args(argv)

    reports = load_reports_json(args.from_json) if args.from_json else load_reports_psql(args.limit)
    extra = [r for r in (args.search_roots or "").split(",") if r]
    ledger = build_ledger(reports, repo_root=args.repo_root, search_roots=extra)
    outs = write_outputs(ledger, args.out, top=args.top)
    if args.figures:
        outs["figures"] = make_figures(ledger, args.out)  # type: ignore[assignment]
    s = ledger["stats"]
    print(json.dumps({k: s[k] for k in ("reports_scanned", "reports_with_debt_section", "items_total",
                                        "clusters_total", "recurrent_clusters_ge2")}, ensure_ascii=False))
    for k, v in outs.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())