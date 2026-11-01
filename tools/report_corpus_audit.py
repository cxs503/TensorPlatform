#!/usr/bin/env python3
"""报告语料可复算质量测绘 / 报告纪律确定性闸门 (report-corpus-audit).

背景（owner 2026-10-29/2026-11-01 报告纪律升级）：
每一单交付的进展报告必须齐备六要素 ——
  ① 案例介绍（物理背景/几何/参数表）
  ② 怎么做的（方法/流程/关键设置/工具链）
  ③ 计算结果（数字表）
  ④ 结果图（场量云图 + 与解析解/文献/基线的比较曲线；纯文档/后端任务须写明理由）
  ⑤ 注意点（踩过的坑/局限/适用范围）
  ⑥ 误差判定（关键量 vs 基准误差逐项列出，并**显式**回答是否 <3%；无基准须声明原因）

现状缺口：报告纪律此前只由「质检席」LLM 逐篇主观复审（ops/report_qa.py 派单给 agent），
判定不可复算、跨批不可比、缺陷类别无量化台账。本模块把同一套纪律做成**确定性静态检查**：
同一篇 markdown 任意次运行给出同一判定，缺陷带行号证据，可全语料批量跑出覆盖率/缺陷率/
欠账主题频次，为质检席与写报告的 agent 提供机器闸门与自查工具。

用法:
  # 全库测绘（走 psql，只读；输出 JSON + PNG + markdown 表）
  python3 tools/report_corpus_audit.py --db --out artifacts/report_corpus_audit

  # 发布前自查单篇（返回码 1 = 有 RESUBMIT 级缺陷，可直接进 pre-commit / CI）
  python3 tools/report_corpus_audit.py --self docs/my_report.md

纪律：本模块只做静态文本判定，不改任何报告；`--db` 只读 SELECT。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# 1. 六要素与缺陷等级
# --------------------------------------------------------------------------- #

ELEMENT_LABELS: Dict[str, str] = {
    "case": "①案例介绍(物理背景/几何/参数表)",
    "method": "②方法流程(设置/工具链)",
    "results": "③计算结果(数字表)",
    "figures": "④结果图(云图+比较曲线)",
    "caveats": "⑤注意点(坑/局限/适用范围)",
    "error": "⑥误差判定(是否<3%)",
}

# 报告类型分层：六要素纪律是对「技术/进展类报告」的要求；QA 元报告的形态是逐篇判定表，
# 不含物理案例节是**设计如此**，不计入六要素覆盖统计（否则缺陷率被系统性污染——实测 253/827 篇）。
META_SCENARIOS = frozenset({"report-qa", "qa-review"})
_META_TITLE_RE = re.compile(r"^\s*【\s*QA\s*】|批次质检|质检报告|质检批次|批次复审")


def classify_report_kind(title: str = "", scenario_key: str = "") -> str:
    """返回 'meta'（QA/质检元报告）或 'technical'（技术/进展报告，受六要素纪律约束）。"""
    if (scenario_key or "").strip().lower() in META_SCENARIOS:
        return "meta"
    if _META_TITLE_RE.search(title or ""):
        return "meta"
    return "technical"


# 严重级：RESUBMIT=必须补（纪律硬要求缺失）；MINOR=建议改进；INFO=仅统计
SEVERITY = {
    "MISSING_HEADINGS": "RESUBMIT",
    "MISSING_ELEMENT_CASE": "RESUBMIT",
    "MISSING_ELEMENT_METHOD": "RESUBMIT",
    "MISSING_ELEMENT_CAVEATS": "RESUBMIT",
    "RESULTS_NO_NUMERIC_TABLE": "RESUBMIT",
    "RESULTS_NO_SECTION": "RESUBMIT",
    "FIGURE_MISSING_UNDECLARED": "RESUBMIT",
    "ERROR_VERDICT_MISSING": "RESUBMIT",
    "ERROR_VERDICT_AMBIGUOUS": "RESUBMIT",
    "FIGURE_EXEMPT_UNDECLARED_REASON": "MINOR",
    "THIN_MARKDOWN": "MINOR",
    "NO_QUANT_TITLE": "INFO",
}

# 章节关键词（中英双语；小写匹配）
_ELEM_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "case": ("案例", "背景", "几何", "参数表", "工况", "物理背景", "算例", "研究对象", "问题描述",
             "problem setup", "introduction", "background", "geometry", "case study", "scope"),
    "method": ("方法", "流程", "步骤", "做法", "怎么做", "实现", "工具链", "关键设置", "算法",
               "方案", "实施", "method", "methodology", "approach", "workflow", "pipeline",
               "implementation", "design", "setup"),
    "results": ("计算结果", "结果", "数字表", "结果表", "results", "benchmark", "数值结果"),
    "caveats": ("注意点", "注意", "局限", "踩坑", "适用范围", "限制", "待办", "遗留",
                "limitation", "caveat", "known issue"),
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$", re.MULTILINE)
_NUM_CELL_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*%?")

# 显式「是否 <3%」判定句式（必须落在同一行/短句内才算显式，避免含糊）
_RATIO_EXPLICIT_RES: Tuple[re.Pattern, ...] = (
    re.compile(r"是否\s*[<＜≤]\s*3\s*%"),                       # 是否<3%
    re.compile(r"[<＜≤]\s*3\s*%\s*[：:，,。]?\s*(?:是|否|成立|不成立|达标|未达标)"),
    re.compile(r"(?:相对误差|误差|偏差|deviation|error)[^。\n|]{0,40}?[<＜≤]\s*3\s*%"),
    re.compile(r"是否\s*(?:达标|优于)\s*3\s*%"),
    # 英文报告常见形态：表头列 "<3%?"、"within 3%"、"远低于 3% 阈值"、"≪ 3%"
    re.compile(r"[<＜≤]\s*3\s*%\s*\?"),
    re.compile(r"(?:is\s+it|whether|within|under|below|less\s+than)\s*[<＜≤<]?\s*3\s*%", re.IGNORECASE),
    re.compile(r"(?:远低于|远小于|≪|远低于|明显低于)\s*(?:3|3%|3 %)"),
)
# 允许的替代声明：无基准且说明原因
_NO_BASELINE_RE = re.compile(
    r"(无[^。\n]{0,4}基准|无(?:可用|参考)?(?:实验数据|解析解|参考值|文献值)|缺少(?:基准|参考数据|实验数据)"
    r"|no\s+(?:available\s+)?(?:baseline|reference|experimental\s+data))", re.IGNORECASE)
_REASON_RE = re.compile(r"(原因|理由|因为|故|因此|由于|because|therefore|reason)")
_FIG_EXEMPT_RE = re.compile(
    r"(纯文档|无图|不适用图|后端任务|纯后端|无须图|无需图|不涉及图|no\s+figures?\s+needed"
    r"|figure[s]?\s+not\s+applicable)", re.IGNORECASE)

# 「局限/待办」文本 → 验证欠账主题（确定性关键词规则，可扩）
DEBT_THEMES: Dict[str, Tuple[str, ...]] = {
    "网格/收敛性": ("网格", "收敛", "grid", "convergence", "y+", "离散误差"),
    "实验/文献基准缺失": ("基准", "对标", "验证数据", "实验数据", "文献值", "benchmark", "validation data"),
    "模型简化/忽略项": ("简化", "忽略", "未计入", "假设", "assumption", "neglect", "忽略项"),
    "尺度效应/外推": ("尺度效应", "外推", "scaling", "full-scale", "缩尺"),
    "标定/参数不确定": ("标定", "校准", "calibration", "参数不确定性", "拟合系数"),
    "湍流/物理模型局限": ("湍流模型", "k-epsilon", "k-ω", "sst", "turbulence model", "本构"),
    "计算成本/规模上限": ("计算成本", "算力", "耗时", "规模上限", "cost", "wall clock", "自由度"),
    "耦合项缺失": ("单向耦合", "强耦合", "未耦合", "耦合项", "one-way", "two-way"),
    "数据可得性/传感器": ("数据缺失", "传感器", "实测数据", "数据集", "sensor", "dataset"),
    "验证域外推/适用范围": ("适用范围", "外推", "适用域", "仅适用于", "regime"),
    "工程化落地": ("工程化", "部署", "产品化", "落地", "production"),
}


# --------------------------------------------------------------------------- #
# 2. 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class Heading:
    level: int
    text: str
    line: int


@dataclass
class Defect:
    code: str
    severity: str
    detail: str
    line: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReportVerdict:
    report_id: Optional[int]
    title: str
    scenario_key: str
    markdown_chars: int
    elements: Dict[str, str] = field(default_factory=dict)         # element -> ok|weak|missing
    element_evidence: Dict[str, str] = field(default_factory=dict)
    defects: List[Defect] = field(default_factory=list)
    numeric_tables: int = 0
    results_numeric_tables: int = 0
    figures: int = 0
    error_verdict: str = "missing"      # explicit | no_baseline_declared | ambiguous | missing
    error_evidence: str = ""
    debt_themes: List[str] = field(default_factory=list)
    debt_chars: int = 0
    report_kind: str = "technical"

    @property
    def six_element_complete(self) -> bool:
        return all(v == "ok" for v in self.elements.values())

    @property
    def verdict(self) -> str:
        sev = {d.severity for d in self.defects}
        if "RESUBMIT" in sev:
            return "RESUBMIT"
        if "MINOR" in sev:
            return "MINOR"
        return "PASS"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["defects"] = [x.to_dict() for x in self.defects]
        d["six_element_complete"] = self.six_element_complete
        d["verdict"] = self.verdict
        return d


# --------------------------------------------------------------------------- #
# 3. 解析原语（纯函数，单测覆盖）
# --------------------------------------------------------------------------- #

def parse_headings(markdown: str) -> List[Heading]:
    out: List[Heading] = []
    for m in _HEADING_RE.finditer(markdown):
        line = markdown.count("\n", 0, m.start()) + 1
        out.append(Heading(level=len(m.group(1)), text=m.group(2).strip(), line=line))
    return out


def section_body(markdown: str, headings: Sequence[Heading], idx: int) -> str:
    """返回第 idx 个标题的正文（到下一个同级或更高级标题为止）。"""
    h = headings[idx]
    start = markdown.find("\n", markdown.find(h.text)) + 1
    if start <= 0:
        start = 0
    end = len(markdown)
    for nxt in headings[idx + 1:]:
        if nxt.level <= h.level:
            end = markdown.find(nxt.text)
            if end < 0:
                end = len(markdown)
            break
    return markdown[start:end]


def numeric_table_count(markdown: str, min_numeric_cells: int = 3) -> int:
    """统计「含 >=min_numeric_cells 个数值单元格」的 markdown 表格块数量。"""
    blocks: List[List[str]] = []
    cur: List[str] = []
    for line in markdown.splitlines():
        if _TABLE_ROW_RE.match(line):
            cur.append(line)
        else:
            if cur:
                blocks.append(cur)
                cur = []
    if cur:
        blocks.append(cur)
    n = 0
    for blk in blocks:
        if len(blk) < 2:          # 至少表头 + 分隔行
            continue
        cells = [c for row in blk for c in row.strip().strip("|").split("|")]
        numeric = sum(1 for c in cells if _NUM_CELL_RE.search(c) and re.search(r"\d", c))
        if numeric >= min_numeric_cells:
            n += 1
    return n


def _find_in_text(text: str, keywords: Iterable[str]) -> Optional[str]:
    low = text.lower()
    for kw in keywords:
        if kw.lower() in low:
            return kw
    return None


def section_numeric_tables(markdown: str, headings: Sequence[Heading],
                           keywords: Iterable[str]) -> int:
    """只统计指定关键词命中的章节**内部**的数值表数量。

    六要素③要求「计算结果」这一节里给出数字表；全局有表不算数（参数表不能顶替结果表）。
    """
    for i, h in enumerate(headings):
        if _find_in_text(h.text, keywords):
            return numeric_table_count(section_body(markdown, headings, i))
    return 0


def error_verdict(markdown: str) -> Tuple[str, str]:
    """⑥ 误差判定状态 + 证据片段。"""
    lines = markdown.splitlines()
    for pattern in _RATIO_EXPLICIT_RES:
        for i, line in enumerate(lines, 1):
            m = pattern.search(line)
            if m:
                return "explicit", f"L{i}: {line.strip()[:120]}"
    no_base = _NO_BASELINE_RE.search(markdown)
    if no_base and _REASON_RE.search(markdown[no_base.start(): no_base.start() + 200]):
        snippet = markdown[no_base.start(): no_base.start() + 120].replace("\n", " ")
        return "no_baseline_declared", f"「{no_base.group(0)}」: {snippet.strip()[:110]}"
    if re.search(r"(误差|偏差|deviation|相对误差)", markdown, re.IGNORECASE):
        for i, line in enumerate(lines, 1):
            if re.search(r"(误差|偏差|deviation)", line, re.IGNORECASE):
                return "ambiguous", f"L{i}: {line.strip()[:120]}"
    return "missing", ""


def figure_exemption(markdown: str) -> Optional[str]:
    m = _FIG_EXEMPT_RE.search(markdown)
    if not m:
        return None
    line_start = markdown.rfind("\n", 0, m.start()) + 1
    line_end = markdown.find("\n", m.end())
    return markdown[line_start: line_end if line_end > 0 else len(markdown)].strip()[:140]


def extract_debt_text(markdown: str, headings: Sequence[Heading]) -> str:
    """抽取「局限/待办/注意/后续」类章节正文，作为验证欠账语料。"""
    chunks: List[str] = []
    kws = ("局限", "待办", "注意", "后续", "遗留", "limitation", "caveat", "todo", "next step")
    matched_any = False
    for i, h in enumerate(headings):
        if _find_in_text(h.text, kws):
            chunks.append(section_body(markdown, headings, i))
            matched_any = True
    if not matched_any:
        # 兜底：整篇里以「局限/待办」开头的行段落
        for m in re.finditer(r"^\s*(?:[-*>]\s*)?(?:局限|待办|注意点)", markdown, re.MULTILINE):
            chunks.append(markdown[m.start(): m.start() + 600])
    return "\n".join(chunks)


def classify_debt_themes(debt_text: str) -> List[str]:
    if not debt_text.strip():
        return []
    hits = []
    low = debt_text.lower()
    for theme, kws in DEBT_THEMES.items():
        if any(kw.lower() in low for kw in kws):
            hits.append(theme)
    return hits


# --------------------------------------------------------------------------- #
# 4. 单篇判定
# --------------------------------------------------------------------------- #

def analyze_report(
    markdown: str,
    *,
    report_id: Optional[int] = None,
    title: str = "",
    scenario_key: str = "",
    figures: int = 0,
) -> ReportVerdict:
    md = markdown or ""
    headings = parse_headings(md)
    v = ReportVerdict(
        report_id=report_id,
        title=title,
        scenario_key=scenario_key or "",
        markdown_chars=len(md),
        figures=figures,
        numeric_tables=numeric_table_count(md),
        results_numeric_tables=section_numeric_tables(md, headings, _ELEM_KEYWORDS["results"]),
        debt_chars=0,
        report_kind=classify_report_kind(title, scenario_key or ""),
    )

    if not headings:
        v.defects.append(Defect("MISSING_HEADINGS", SEVERITY["MISSING_HEADINGS"],
                                "全文无 markdown 标题，无法定位六要素章节", None))

    # ①②③⑤：标题/关键词命中
    heading_text = "\n".join(h.text for h in headings)
    for element in ("case", "method", "results", "caveats"):
        kw = _find_in_text(heading_text, _ELEM_KEYWORDS[element])
        if kw:
            v.elements[element] = "ok"
            v.element_evidence[element] = f"命中标题关键词「{kw}」"
        else:
            v.elements[element] = "missing"
            v.element_evidence[element] = "标题与正文均未见对应关键词"

    # ③ 进一步要求数字表
    if v.elements["results"] == "ok":
        if v.results_numeric_tables == 0:
            v.defects.append(Defect("RESULTS_NO_NUMERIC_TABLE", SEVERITY["RESULTS_NO_NUMERIC_TABLE"],
                                    "结果章节内无数值表（六要素③要求数字表；全文其他处的参数表不能顶替）",
                                    None))
        else:
            v.element_evidence["results"] += f"；结果节内数值表 {v.results_numeric_tables} 个"
    else:
        v.defects.append(Defect("RESULTS_NO_SECTION", SEVERITY["RESULTS_NO_SECTION"],
                                "缺结果章节（六要素③）"))

    for element in ("case", "method", "caveats"):
        if v.elements[element] == "missing":
            v.defects.append(Defect(f"MISSING_ELEMENT_{element.upper()}",
                                    SEVERITY[f"MISSING_ELEMENT_{element.upper()}"],
                                    "缺章节：" + ELEMENT_LABELS[element]))

    # ④ 图件：figures 载荷 或 明确豁免理由
    exempt = figure_exemption(md)
    if figures > 0:
        v.elements["figures"] = "ok"
        v.element_evidence["figures"] = f"内嵌 figures={figures}"
    elif exempt:
        v.elements["figures"] = "ok"
        v.element_evidence["figures"] = f"声明豁免：{exempt}"
    else:
        v.elements["figures"] = "missing"
        v.element_evidence["figures"] = "无 figures 且未写明无图理由"
        v.defects.append(Defect("FIGURE_MISSING_UNDECLARED", SEVERITY["FIGURE_MISSING_UNDECLARED"],
                                "无结果图且未声明理由（owner 升级要求：纯文档/后端任务须写明）"))
    # 若正文声称无图但理由空泛（只写"无图"不写原因）→ MINOR
    if figures == 0 and exempt and not _REASON_RE.search(exempt):
        if _FIG_EXEMPT_RE.fullmatch(exempt.strip()) or len(exempt) < 30:
            v.defects.append(Defect("FIGURE_EXEMPT_UNDECLARED_REASON",
                                    SEVERITY["FIGURE_EXEMPT_UNDECLARED_REASON"],
                                    f"无图豁免未给出实质理由：{exempt}"))

    # ⑥ 误差判定
    ev, evid = error_verdict(md)
    v.error_verdict, v.error_evidence = ev, evid
    if ev in ("explicit", "no_baseline_declared"):
        v.elements["error"] = "ok"
        v.element_evidence["error"] = evid
    elif ev == "ambiguous":
        v.elements["error"] = "weak"
        v.element_evidence["error"] = evid
        v.defects.append(Defect("ERROR_VERDICT_AMBIGUOUS", SEVERITY["ERROR_VERDICT_AMBIGUOUS"],
                                "提到误差但未显式回答是否 <3%（或声明无基准）：" + evid))
    else:
        v.elements["error"] = "missing"
        v.element_evidence["error"] = "全文无误差判定表述"
        v.defects.append(Defect("ERROR_VERDICT_MISSING", SEVERITY["ERROR_VERDICT_MISSING"],
                                "缺误差判定节（六要素⑥）"))

    # 内容厚度
    if len(md) < 600:
        v.defects.append(Defect("THIN_MARKDOWN", SEVERITY["THIN_MARKDOWN"],
                                f"正文字数 {len(md)} < 600，疑为一句话占位"))
    if title and not re.search(r"\d", title):
        v.defects.append(Defect("NO_QUANT_TITLE", SEVERITY["NO_QUANT_TITLE"],
                                "标题无任何数字/量词，难以索引到具体量"))

    # 欠账主题
    debt = extract_debt_text(md, headings)
    v.debt_chars = len(debt)
    v.debt_themes = classify_debt_themes(debt)
    return v


# --------------------------------------------------------------------------- #
# 5. 语料级统计
# --------------------------------------------------------------------------- #

def corpus_stats(verdicts: Sequence[ReportVerdict], scope: str = "all") -> Dict[str, Any]:
    """scope: all | technical | meta（按 report_kind 过滤；technical 是六要素纪律的判据口径）。"""
    if scope in ("technical", "meta"):
        verdicts = [v for v in verdicts if v.report_kind == scope]
    n = len(verdicts)
    if n == 0:
        return {"n_reports": 0, "scope": scope}

    def rate(k: int) -> float:
        return round(k / n, 4)

    element_ok = {e: sum(1 for v in verdicts if v.elements.get(e) == "ok") for e in ELEMENT_LABELS}
    element_weak = {e: sum(1 for v in verdicts if v.elements.get(e) == "weak") for e in ELEMENT_LABELS}
    defect_counts: Dict[str, int] = {}
    for v in verdicts:
        for d in v.defects:
            defect_counts[d.code] = defect_counts.get(d.code, 0) + 1
    theme_counts: Dict[str, int] = {}
    for v in verdicts:
        for t in v.debt_themes:
            theme_counts[t] = theme_counts.get(t, 0) + 1
    verdict_counts = {"PASS": 0, "MINOR": 0, "RESUBMIT": 0}
    for v in verdicts:
        verdict_counts[v.verdict] += 1
    by_scenario: Dict[str, Dict[str, int]] = {}
    for v in verdicts:
        key = v.scenario_key or "(none)"
        slot = by_scenario.setdefault(key, {"n": 0, "six_element_complete": 0, "figure_ok": 0,
                                            "error_ok": 0})
        slot["n"] += 1
        slot["six_element_complete"] += 1 if v.six_element_complete else 0
        slot["figure_ok"] += 1 if v.elements.get("figures") == "ok" else 0
        slot["error_ok"] += 1 if v.elements.get("error") == "ok" else 0

    return {
        "n_reports": n,
        "scope": scope,
        "verdict_counts": verdict_counts,
        "verdict_rate": {k: rate(c) for k, c in verdict_counts.items()},
        "six_element_complete": sum(1 for v in verdicts if v.six_element_complete),
        "six_element_complete_rate": rate(sum(1 for v in verdicts if v.six_element_complete)),
        "element_ok_rate": {e: rate(c) for e, c in element_ok.items()},
        "element_ok_count": element_ok,
        "element_weak_count": element_weak,
        "defect_counts": dict(sorted(defect_counts.items(), key=lambda kv: -kv[1])),
        "defect_rates": {k: rate(c) for k, c in sorted(defect_counts.items(), key=lambda kv: -kv[1])},
        "debt_theme_counts": dict(sorted(theme_counts.items(), key=lambda kv: -kv[1])),
        "debt_theme_rates": {k: rate(c) for k, c in sorted(theme_counts.items(), key=lambda kv: -kv[1])},
        "by_scenario": dict(sorted(by_scenario.items(), key=lambda kv: -kv[1]["n"])),
        "mean_chars": round(sum(v.markdown_chars for v in verdicts) / n, 1),
        "figure_rate": rate(sum(1 for v in verdicts if v.figures > 0)),
        "numeric_table_rate": rate(sum(1 for v in verdicts if v.numeric_tables > 0)),
        "results_table_rate": rate(sum(1 for v in verdicts if v.results_numeric_tables > 0)),
    }


# --------------------------------------------------------------------------- #
# 6. 数据加载（只读）
# --------------------------------------------------------------------------- #

_SQL = (
    "SELECT coalesce(json_agg(t ORDER BY t.id), '[]'::json)::text FROM ("
    "  SELECT id, title, coalesce(scenario_key,'') AS scenario_key,"
    "         coalesce(mission_id,0) AS mission_id, markdown,"
    "         jsonb_array_length(figures) AS n_figures, qa_status,"
    "         to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS created_at"
    "  FROM tensor_reports WHERE status = 'published'"
    ") t"
)


def load_reports_psql(db: str = "tensorplatform", user: str = "foundation",
                      host: str = "127.0.0.1", password: Optional[str] = None,
                      limit: int = 0) -> List[Dict[str, Any]]:
    import os
    env = {"PGPASSWORD": password if password is not None else os.environ.get("PGPASSWORD", ""),
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    cp = subprocess.run(["psql", "-h", host, "-U", user, "-d", db, "-tAc", _SQL],
                        capture_output=True, text=True, env=env, timeout=300)
    if cp.returncode != 0:
        raise RuntimeError(f"psql failed: {cp.stderr[:400]}")
    rows = json.loads(cp.stdout.strip() or "[]")
    if limit and limit > 0:
        rows = rows[-limit:]
    return rows


# --------------------------------------------------------------------------- #
# 7. 输出：JSON / markdown / 图
# --------------------------------------------------------------------------- #

def corpus_sha256(rows: Sequence[Dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(str(r.get("id")).encode())
        h.update(b"\x00")
        h.update((r.get("markdown") or "").encode())
        h.update(b"\x00")
    return h.hexdigest()


def render_markdown(stats: Dict[str, Any], verdicts: Sequence[ReportVerdict], *,
                    sha: str, source: str,
                    stats_all: Optional[Dict[str, Any]] = None,
                    stats_meta: Optional[Dict[str, Any]] = None) -> str:
    lines: List[str] = []
    n = stats["n_reports"]
    lines.append(f"# 报告语料质量测绘（{n} 篇）\n")
    lines.append(f"- 数据源：{source}")
    lines.append(f"- 语料指纹 (sha256)：`{sha}`")
    lines.append(f"- 统计口径：**{stats.get('scope', 'all')}**（六要素纪律只约束技术/进展类报告；"
                 f"QA 元报告的形态是逐篇判定表，单独统计）")
    lines.append(f"- 平均正文长度：{stats['mean_chars']} 字符\n")
    if stats_all is not None:
        lines.append("### 0. 三口径汇总\n")
        lines.append("| 口径 | n | 六要素全齐 | 全齐率 | PASS |")
        lines.append("|---|---|---|---|---|")
        for label, st in (("all(全语料)", stats_all), ("technical(技术报告)", stats),
                          ("meta(QA元报告)", stats_meta)):
            if not st or not st.get("n_reports"):
                continue
            lines.append(f"| {label} | {st['n_reports']} | {st['six_element_complete']} | "
                         f"{st['six_element_complete_rate']*100:.1f}% | {st['verdict_counts']['PASS']} |")
        lines.append("")

    lines.append("## 1. 六要素覆盖率\n")
    lines.append("| 要素 | ok 篇数 | 覆盖率 | 弱 |")
    lines.append("|---|---|---|---|")
    for e, label in ELEMENT_LABELS.items():
        lines.append(f"| {label} | {stats['element_ok_count'][e]} | "
                     f"{stats['element_ok_rate'][e]*100:.1f}% | {stats['element_weak_count'][e]} |")
    lines.append(f"\n六要素全齐：{stats['six_element_complete']}/{n} = "
                 f"{stats['six_element_complete_rate']*100:.1f}%\n")

    lines.append("## 2. 缺陷类别频次\n")
    lines.append("| 缺陷码 | 篇数 | 占比 |")
    lines.append("|---|---|---|")
    for code, cnt in stats["defect_counts"].items():
        lines.append(f"| {code} | {cnt} | {stats['defect_rates'][code]*100:.1f}% |")

    lines.append("\n## 3. 判定分布\n")
    lines.append("| 判定 | 篇数 | 占比 |")
    lines.append("|---|---|---|")
    for k, c in stats["verdict_counts"].items():
        lines.append(f"| {k} | {c} | {stats['verdict_rate'][k]*100:.1f}% |")

    lines.append("\n## 4. 验证欠账主题（来自「局限/待办」章节，规则分类）\n")
    lines.append("| 主题 | 篇数 | 占比 |")
    lines.append("|---|---|---|")
    for t, c in stats["debt_theme_counts"].items():
        lines.append(f"| {t} | {c} | {stats['debt_theme_rates'][t]*100:.1f}% |")

    lines.append("\n## 5. 按场景键（scenario_key）前 10\n")
    lines.append("| scenario_key | n | 六要素全齐 | 图 ok | 误差 ok |")
    lines.append("|---|---|---|---|---|")
    for key, s in list(stats["by_scenario"].items())[:10]:
        lines.append(f"| {key} | {s['n']} | {s['six_element_complete']} | {s['figure_ok']} | {s['error_ok']} |")

    worst = sorted(verdicts, key=lambda v: (-sum(1 for d in v.defects if d.severity == "RESUBMIT"),
                                            -len(v.defects)))[:10]
    lines.append("\n## 6. 缺陷最多的 10 篇（返工候选）\n")
    lines.append("| id | 判定 | RESUBMIT 缺陷 | 标题 |")
    lines.append("|---|---|---|---|")
    for v in worst:
        nres = sum(1 for d in v.defects if d.severity == "RESUBMIT")
        lines.append(f"| {v.report_id} | {v.verdict} | {nres} | {v.title[:48]} |")
    return "\n".join(lines) + "\n"


def make_figures(stats: Dict[str, Any], outdir: Path) -> List[Path]:
    """生成 3 张图。图内文字用 ASCII：本机无 CJK 字体（fc-list 空），中文会渲染成豆腐块。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    EN_LABEL = {
        "case": "1 case (background/geometry)",
        "method": "2 method/workflow",
        "results": "3 results (numeric table)",
        "figures": "4 figures",
        "caveats": "5 caveats/limitations",
        "error": "6 error verdict (<3%)",
    }

    # fig1 六要素覆盖率
    labels = [EN_LABEL[e] for e in ELEMENT_LABELS]
    vals = [stats["element_ok_rate"][e] * 100 for e in ELEMENT_LABELS]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    bars = ax.barh(labels, vals, color="#3d6fb4")
    ax.set_xlim(0, 100)
    ax.set_xlabel("coverage rate (%)")
    ax.set_title(f"Six-element coverage of technical reports (n={stats['n_reports']})")
    for b, v in zip(bars, vals):
        ax.text(v + 1, b.get_y() + b.get_height() / 2, f"{v:.1f}%", va="center", fontsize=8)
    ax.invert_yaxis()
    fig.tight_layout()
    p = outdir / "fig1_element_coverage.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(p)

    # fig2 缺陷频次
    codes = list(stats["defect_counts"].keys())[:10]
    cnts = [stats["defect_counts"][c] for c in codes]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.bar(range(len(codes)), cnts, color="#c0504d")
    ax.set_xticks(range(len(codes)))
    ax.set_xticklabels(codes, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("reports")
    ax.set_title("Defect frequency, deterministic static check (technical scope)")
    fig.tight_layout()
    p = outdir / "fig2_defect_frequency.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(p)

    # fig3 欠账主题
    theme_en = {
        "网格/收敛性": "grid/convergence",
        "实验/文献基准缺失": "no experimental/literature baseline",
        "模型简化/忽略项": "model simplification",
        "尺度效应/外推": "scale effect / extrapolation",
        "标定/参数不确定": "calibration / uncertain params",
        "湍流/物理模型局限": "turbulence model limits",
        "计算成本/规模上限": "compute cost / size cap",
        "耦合项缺失": "missing coupling terms",
        "数据可得性/传感器": "data availability / sensors",
        "验证域外推/适用范围": "validity domain / scope",
        "工程化落地": "production readiness",
    }
    themes = list(stats["debt_theme_counts"].keys())[:11]
    tvals = [stats["debt_theme_counts"][t] for t in themes]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    bars = ax.barh([theme_en.get(t, t) for t in themes], tvals, color="#4b8b3b")
    ax.set_xlabel("reports mentioning topic")
    ax.set_title("Verification-debt topics mined from limitations sections (rule-based)")
    for b, v in zip(bars, tvals):
        ax.text(v + 0.2, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=8)
    ax.invert_yaxis()
    fig.tight_layout()
    p = outdir / "fig3_debt_themes.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(p)
    return paths


# --------------------------------------------------------------------------- #
# 8. CLI
# --------------------------------------------------------------------------- #

def run_self(path: Path) -> int:
    md = path.read_text(encoding="utf-8", errors="replace")
    figures = 0
    v = analyze_report(md, title=path.name, figures=figures)
    print(json.dumps(v.to_dict(), ensure_ascii=False, indent=2))
    return 0 if v.verdict != "RESUBMIT" else 1


def run_db(args: argparse.Namespace) -> int:
    rows = load_reports_psql(db=args.db, user=args.user, host=args.host,
                             password=args.password, limit=args.limit)
    verdicts = [
        analyze_report(r.get("markdown") or "", report_id=r.get("id"), title=r.get("title") or "",
                       scenario_key=r.get("scenario_key") or "",
                       figures=int(r.get("n_figures") or 0))
        for r in rows
    ]
    stats = corpus_stats(verdicts, scope="technical")
    stats_all = corpus_stats(verdicts, scope="all")
    stats_meta = corpus_stats(verdicts, scope="meta")
    sha = corpus_sha256(rows)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at_utc": stamp,
        "source": f"psql {args.host}/{args.db} tensor_reports (status=published)",
        "corpus_sha256": sha,
        "sql": _SQL,
        "limit": args.limit,
        "stats": stats,
        "stats_all": stats_all,
        "stats_meta": stats_meta,
        "reports": [v.to_dict() for v in verdicts],
    }
    (outdir / f"audit_{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    md = render_markdown(stats, verdicts, sha=sha, source=payload["source"],
                         stats_all=stats_all, stats_meta=stats_meta)
    (outdir / f"audit_{stamp}.md").write_text(md, encoding="utf-8")
    figs = make_figures(stats, outdir)
    # 稳定命名（供报告引用/复算 diff）
    (outdir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(md)
    print("artifacts:", ", ".join(str(p) for p in [outdir / f'audit_{stamp}.json', *figs]))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="报告语料可复算质量测绘 / 报告纪律确定性闸门")
    ap.add_argument("--db", action="store_true", help="对全库已发布报告跑测绘")
    ap.add_argument("--self", dest="self_path", type=Path, help="单篇自查（退出码 1 = RESUBMIT）")
    ap.add_argument("--out", default="artifacts/report_corpus_audit")
    ap.add_argument("--limit", type=int, default=0, help="只取最新 N 篇（0=全部）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--user", default="foundation")
    ap.add_argument("--dbname", dest="db_name", default="tensorplatform")
    ap.add_argument("--password")
    args = ap.parse_args(argv)
    # --db 是布尔开关，库名走 --dbname
    args.db = args.db_name if args.db else None
    if args.self_path:
        return run_self(args.self_path)
    if args.db:
        return run_db(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())