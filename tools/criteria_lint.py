#!/usr/bin/env python3
"""criteria_lint —— 判据可判定性 lint（p372w2-n06，物理域 platform/quality-gate）。

把「这条判据能不能被机器裁」从**事后补规则**变成**派发前的确定性静态判决**：
从任务书切出判据条目（编号列表 / 分号分隔 / 阈值短语 三形态），逐条检查三要素，
缺任一即拦截，并给出**机器可读原因码**。

三要素（方法学定义，零领域词）
------------------------------
====== ==================================================== ==========================
要素   含义                                                  判定形状（rule id）
====== ==================================================== ==========================
阈值   可比较的数值边界                                       ``NUM.comparator`` /
                                                             ``NUM.plusminus`` /
                                                             ``NUM.word_bound`` /
                                                             ``NUM.range``
基准   判据对标/自洽的参照面（闭词表）                         ``REF.lexicon``
记号   使条目可裁决的判定语义（比较符/决策动词/判定符号）       ``V.comparator`` /
                                                             ``V.decision_verb`` /
                                                             ``V.verdict_symbol``
====== ==================================================== ==========================

条目标签（``labels``，可多标签）
--------------------------------
* ``numeric_threshold``        —— 命中阈值要素；
* ``boolean_checklist``        —— 无阈值但有判定记号（布尔/清单式判据）；
* ``reference_anchor_missing`` —— 基准要素缺失（拦截主因之一）。

原因码：见 ``REASON_CODES``（9 种，机器可读）。

派发前门：``pre_dispatch(goal, mode=…)`` → ``off / advisory / enforce / block``；
任何异常一律 fail-open（门坏了不许误伤派发）；``enforce`` 只**追加**槽位建议块
（定界、无数字、可剥离 → 结构上不自证满足）。

纯函数、无时钟、无随机、无网络：同输入必同输出（``input_sha256``/``report_sha256``）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = "criteria-lint/1.0"
GATE_MODES = ("off", "advisory", "enforce", "block")
ENV_MODE = "TP_CRITERIA_LINT_GATE"

# ─────────────────────────── 原因码登记表 ───────────────────────────

REASON_CODES: Dict[str, str] = {
    "CRIT_NO_CRITERIA_BLOCK":
        "任务书里没有可定位的判据块（无判据区标题、无编号/分号/阈值短语形态）",
    "CRIT_POINTER_ONLY":
        "判据块只是指针（如「见战役表 metadata」），正文无判据条目 → 不可离线裁决",
    "CRIT_UNPARSABLE_ITEM":
        "判据条目无法解析（空条目/纯标题/有效字符过短）",
    "CRIT_NO_NUMERIC_THRESHOLD":
        "缺阈值要素：条目内无可比较数值边界（如 <=5%、±0.02、不小于 20 例）",
    "CRIT_NO_REFERENCE_ANCHOR":
        "缺基准要素：条目内无可对标参照面（解析解/实验/文献/基线/自洽/守恒…）",
    "CRIT_NO_VERDICT_MARK":
        "缺判定记号：无比较符/决策动词/判定符号 → 机器无从判真假",
    "CRIT_VAGUE_QUANTIFIER":
        "条目用模糊量词代替数值边界（若干/左右/尽量/合理/足够…），不可机器裁",
    "CRIT_PLACEHOLDER_ONLY":
        "条目只含占位符（<NUM>/<UNIT>/<METRIC>）而无实际数字，属未回填槽位",
    "CRIT_DUPLICATE_ITEM":
        "判据条目与前面条目重复（重复计数会虚增判据数）",
}

ELEMENT_KEYS: Tuple[str, ...] = ("threshold", "reference_anchor", "verdict_mark")
ELEMENT_LABEL: Dict[str, str] = {
    "threshold": "阈值", "reference_anchor": "基准", "verdict_mark": "判定记号"}
MISSING_CODE: Dict[str, str] = {
    "threshold": "CRIT_NO_NUMERIC_THRESHOLD",
    "reference_anchor": "CRIT_NO_REFERENCE_ANCHOR",
    "verdict_mark": "CRIT_NO_VERDICT_MARK",
}

LABEL_NUMERIC = "numeric_threshold"
LABEL_BOOL = "boolean_checklist"
LABEL_NO_REF = "reference_anchor_missing"
LABELS = (LABEL_NUMERIC, LABEL_BOOL, LABEL_NO_REF)

# ─────────────────────────── 形状规则（零领域词）───────────────────────────

_NUM = r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
#: 数字右边界：只排除 ASCII 字母/数字/下划线（"3ms"、"P22"、"a1" 不算阈值），
#: 但允许 CJK 单位（"小于2度"、"不超过5倍" 要算阈值 —— Python 里 `\w` 含 CJK，故不能直接用 \w）
_TAIL = r"(?![A-Za-z0-9_])"
_CMP = r"(?:<=|>=|=<|=>|≤|≥|＜|＞|⩽|⩾|<|>)"

THRESHOLD_RULES: Tuple[Tuple[str, str], ...] = (
    ("NUM.comparator", rf"{_CMP}\s*=?\s*{_NUM}{_TAIL}"),
    ("NUM.plusminus", rf"±\s*{_NUM}{_TAIL}"),
    ("NUM.word_bound",
     rf"(?:不超过|不低于|不大于|不小于|不高于|不少于|不多于|大于等于|小于等于|"
     rf"至多|至少|最多|最少|上限|下限|小于|大于|高于|低于|超过|不足)\s*"
     rf"(?:为|是)?\s*{_NUM}{_TAIL}"),
    ("NUM.range", rf"{_NUM}\s*(?:%|‰|倍)?\s*[–—~-]\s*{_NUM}\s*"
                  rf"(?:%|‰|倍|[A-Za-zµ]+(?:/[A-Za-z]+)?){_TAIL}"),
    # 率型量（一致率/占比/比例 100%）自带边界语义：数值即上限
    ("NUM.rate_value", rf"(?:率|占比|比例|ratio|rate|fraction)\s*"
                        rf"(?:为|是|达到|>=|≥|＝|=)?\s*{_NUM}\s*(?:%|‰)"),
)

#: 基准要素闭词表（方法学词；域词经 --extra-terms / meta 注入，不入代码）
REFERENCE_TERMS: Tuple[str, ...] = (
    "解析解", "实验", "实测", "文献", "基线", "参考值", "基准", "标定", "对照",
    "自洽", "守恒", "复算", "可复算", "对比", "相比", "参照", "退化",
    "极限情形", "极限值", "交叉验证", "互裁", "同输入", "同种子", "可复现",
    "不变量",
    "benchmark", "baseline", "analytical", "experimental", "reference",
    "published", "golden", "vs", "versus", "conservation", "consistency",
    "self-consistent", "degenerate", "limit case", "cross-check", "invariant",
)

#: 绝对不变式规则（数值有界/无异常 → 判定面是全域不变式，自锚）
REFERENCED_INVARIANT_RULES: Tuple[Tuple[str, str], ...] = (
    ("REF.invariant",
     r"(?:无|零)\s*(?:NaN|Inf|nan|inf|报错|异常|错误|违例|失败|回归|遗漏|崩溃|中断|挂死)"),
    # 命名参照（"(SIMMAN 带内)"、"(ITTC 曲线)"）：括号内的外部专名 = 指名的对标对象
    ("REF.named_ref", r"[(（][^)）]{0,24}[A-Z]{2,}[A-Za-z0-9._-]*[^)）]{0,24}[)）]"),
)
#: ASCII 词条走词边界（避免 "vs" 命中 "vsomething"），CJK 词条直接子串匹配
_REF_RE = re.compile(
    "|".join((rf"\b{re.escape(t)}" if t.isascii() else re.escape(t))
             for t in REFERENCE_TERMS),
    re.IGNORECASE,
)

VERDICT_RULES: Tuple[Tuple[str, str], ...] = (
    ("V.comparator", _CMP),
    ("V.decision_verb",
     r"(?:通过|不通过|达标|未达标|一致|相同|误差|残差|偏差|覆盖|拦截|触发|满足|符合|"
     r"判定|裁决|判据|验收|断言|幂等|确定性|可复算|退化|对齐|收敛|"
     r"PASS|FAIL|assert|identical|reproduc)"),
    ("V.verdict_symbol", r"[\u2705\u2611\u2714\u2713\u2718\u274c\u2610\u25a1]"),
    # 绝对不变式（无 NaN/无报错/零违例）本身就是可裁的布尔判定
    ("V.word_comparator",
     r"(?:不超过|不低于|不大于|不小于|不高于|不少于|不多于|大于等于|小于等于|"
     r"至多|至少|最多|最少|上限|下限|小于|大于|高于|低于|超过|不足)"),
    ("V.absence_check",
     r"(?:无|零)\s*(?:NaN|Inf|nan|inf|报错|异常|错误|违例|失败|回归|遗漏|崩溃|中断|挂死)"),
)

VAGUE_TERMS: Tuple[str, ...] = (
    "若干", "左右", "尽量", "合理", "良好", "适当", "足够", "少量", "较多",
    "较高精度", "较高", "较快", "大致", "大约", "约等于", "差不多",
)

_BLOCK_HEADERS = ("机器验收判据", "机器验收标准", "验收判据", "机器可算判据",
                  "可算判据", "判据块", "acceptance criteria")
_POINTER_PAT = re.compile(
    r"(?:见|参见|详见|按|依)\s*(?:战役表|计划表|节点)\s*(?:的)?\s*metadata", re.IGNORECASE)
_PLACEHOLDER_PAT = re.compile(r"<\s*(?:NUM|UNIT|METRIC|THRESHOLD|PATH)\s*>", re.IGNORECASE)

#: 真编号标记（(1)/(a)/①/1./a)）—— 不含分号：分号是**另一形态**，混在一起会
#: 把「分号分隔」误判成「编号列表」并切出碎片条目
_ITEM_MARK_RE = re.compile(
    r"(?:"
    r"[（(]\s*(?:\d{1,2}|[a-zA-Z]|[ivxIVX]{1,4})\s*[)）]"   # (1) (a) (iv)
    r"|(?:^|\n)\s*\d{1,2}\s*[.)、]"                          # 1. 2) 3、
    r"|[\u2460-\u2473]"                                      # ① ② ③
    r"|(?:^|\n)\s*[a-zA-Z]\s*[)）]"                          # a) b)
    r")"
)
#: 任一分隔形态（编号 ∪ 分号），用于「前言句」判定
_ANY_MARK_RE = re.compile(_ITEM_MARK_RE.pattern + r"|[;；]")
_STRIP_CHARS = " \t　:：,，、;；。"
_SENT_SPLIT = re.compile(r"[。\n]+")
_SECTION_PAT = re.compile(r"【[^】]{1,20}】")
_MIN_ITEM_CHARS = 2


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ─────────────────────────── 数据结构 ───────────────────────────

@dataclass(frozen=True)
class Hit:
    element: str
    rule: str
    start: int
    end: int
    text: str

    def as_dict(self) -> Dict[str, Any]:
        return {"element": self.element, "rule": self.rule,
                "span": [self.start, self.end], "text": self.text}


@dataclass
class ItemLint:
    idx: int
    text: str
    labels: List[str] = field(default_factory=list)
    hits: Dict[str, List[Hit]] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)
    blocked: bool = False

    @property
    def sha256(self) -> str:
        return _sha256(self.text)

    def as_dict(self, with_hits: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "idx": self.idx,
            "text": self.text,
            "sha256": self.sha256,
            "labels": list(self.labels),
            "elements": {k: bool(self.hits.get(k)) for k in ELEMENT_KEYS},
            "missing": list(self.missing),
            "reason_codes": list(self.reason_codes),
            "blocked": bool(self.blocked),
        }
        if with_hits:
            out["hits"] = {k: [h.as_dict() for h in v] for k, v in self.hits.items()}
        return out


@dataclass
class LintReport:
    source: Dict[str, Any]
    block_form: str
    block_span: Tuple[int, int]
    items: List[ItemLint] = field(default_factory=list)
    doc_codes: List[str] = field(default_factory=list)

    @property
    def blocked_items(self) -> List[ItemLint]:
        return [it for it in self.items if it.blocked]

    @property
    def reason_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for code in self.doc_codes:
            counts[code] = counts.get(code, 0) + 1
        for it in self.items:
            for code in it.reason_codes:
                counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def verdict(self) -> str:
        """``fail`` = 有不可判定条目 / 整篇无判据块；``pass`` = 全条目三要素齐备。"""
        if self.doc_codes or not self.items:
            return "fail"
        return "fail" if self.blocked_items else "pass"

    def summary(self) -> Dict[str, Any]:
        n = len(self.items)
        blocked = len(self.blocked_items)
        return {
            "items": n,
            "blocked_items": blocked,
            "pass_items": n - blocked,
            "block_rate": round(blocked / n, 4) if n else 1.0,
            "label_counts": {lb: sum(1 for it in self.items if lb in it.labels)
                             for lb in LABELS},
            "reason_code_counts": self.reason_counts,
            "verdict": self.verdict,
        }

    def as_dict(self, with_hits: bool = True) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "source": dict(self.source),
            "input_sha256": self.source.get("sha256", ""),
            "block_form": self.block_form,
            "block_span": list(self.block_span),
            "doc_reason_codes": sorted(set(self.doc_codes)),
            "items": [it.as_dict(with_hits=with_hits) for it in self.items],
            "summary": self.summary(),
            "reason_code_registry": {k: REASON_CODES[k]
                                     for k in sorted(self.reason_counts)},
        }

    def canonical_json(self) -> str:
        return _canon(self.as_dict(with_hits=True))

    @property
    def report_sha256(self) -> str:
        return _sha256(self.canonical_json())


# ─────────────────────────── 判据块切分 ───────────────────────────

def _header_at(anchor: int, text: str) -> str:
    low = text.lower()
    best = ""
    for hdr in _BLOCK_HEADERS:
        if low.find(hdr.lower(), anchor) == anchor and len(hdr) > len(best):
            best = hdr
    return best


def _find_block(goal: str) -> Tuple[str, Tuple[int, int], bool, str]:
    """定位判据块 → ``(body, span, whole, segment)``；截断到下一个【…】章节。

    ``segment`` 含「全部满足…」这类前言（供文档级检查如指针式判据），
    ``body`` 已剥掉前言/区标题（供条目切分）。
    """
    text = goal or ""
    low = text.lower()
    anchor = -1
    for hdr in _BLOCK_HEADERS:
        pos = low.find(hdr.lower())
        if pos >= 0 and (anchor < 0 or pos < anchor):
            anchor = pos
    if anchor < 0:
        anchor = text.find("判据")
    if anchor < 0:
        return text, (0, len(text)), True, text

    start = anchor + len(_header_at(anchor, text))
    # 剥掉区标题后的装饰符（】 / 右括号 / 冒号）与「全部满足…」这类前言句
    m2 = re.match(r"[】\)）\s:：,，]*", text[start:])
    if m2:
        start += m2.end()
    nl = re.search(r"[。\n]", text[start:])
    if nl:
        head = text[start:start + nl.start()]
        if not _ANY_MARK_RE.search(head) and not any(
                re.search(pat, head) for _, pat in THRESHOLD_RULES):
            start += nl.end()
    end = len(text)
    for m in _SECTION_PAT.finditer(text, start):
        end = m.start()
        break
    return text[start:end], (start, end), False, text[anchor:end]


def _split_numbered(block: str) -> List[str]:
    marks = list(_ITEM_MARK_RE.finditer(block))
    if not marks:
        return []
    out: List[str] = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
        chunk = block[start:end].strip(_STRIP_CHARS)
        if chunk:
            out.append(chunk)
    return out


def _split_semicolon(block: str) -> List[str]:
    if "；" not in block and ";" not in block:
        return []
    return [c.strip(_STRIP_CHARS) for c in re.split(r"[;；]", block) if c.strip()]


def _split_threshold_phrase(block: str) -> List[str]:
    out = []
    for s in (x.strip() for x in _SENT_SPLIT.split(block)):
        if s and any(re.search(pat, s) for _, pat in THRESHOLD_RULES):
            out.append(s)
    return out


def extract_items(goal: str) -> Tuple[List[str], str, Tuple[int, int]]:
    """抽取判据条目 → ``(items, block_form, span)``。

    形态优先级：``numbered`` > ``semicolon`` > ``threshold_phrase`` > ``sentence``。
    """
    block, span, whole, _segment = _find_block(goal)
    for form, fn in (("numbered", _split_numbered),
                     ("semicolon", _split_semicolon),
                     ("threshold_phrase", _split_threshold_phrase)):
        items = [it for it in fn(block) if len(it) >= _MIN_ITEM_CHARS]
        if items:
            return items, form, span
    items = [s.strip() for s in _SENT_SPLIT.split(block)
             if len(s.strip()) >= _MIN_ITEM_CHARS]
    return items, ("whole" if whole else "sentence"), span


# ─────────────────────────── 逐条 lint ───────────────────────────

_VAGUE_RE = re.compile("|".join(re.escape(t) for t in VAGUE_TERMS), re.IGNORECASE)


def _rule_hits(element: str, text: str,
               rules: Iterable[Tuple[str, str]]) -> List[Hit]:
    out: List[Hit] = []
    for rule_id, pat in rules:
        for m in re.finditer(pat, text):
            out.append(Hit(element, rule_id, m.start(), m.end(), m.group(0)))
    return out


def lint_item(text: str, idx: int, *,
              extra_reference_terms: Sequence[str] = ()) -> ItemLint:
    """对单条判据做三要素检查（纯函数）。"""
    it = ItemLint(idx=idx, text=text)
    stripped = text.strip()
    it.hits = {k: [] for k in ELEMENT_KEYS}

    if len(re.sub(r"[\s\W_]+", "", stripped)) < _MIN_ITEM_CHARS:
        it.reason_codes.append("CRIT_UNPARSABLE_ITEM")
        it.missing = list(ELEMENT_KEYS)
        it.labels = [LABEL_NO_REF]
        it.blocked = True
        return it

    it.hits["threshold"] = _rule_hits("threshold", text, THRESHOLD_RULES)
    ref_hits = [Hit("reference_anchor", "REF.lexicon", m.start(), m.end(), m.group(0))
                for m in _REF_RE.finditer(text)]
    ref_hits.extend(_rule_hits("reference_anchor", text, REFERENCED_INVARIANT_RULES))
    for term in extra_reference_terms:
        if not term:
            continue
        for m in re.finditer(re.escape(term), text, re.IGNORECASE):
            ref_hits.append(Hit("reference_anchor", "REF.injected",
                                m.start(), m.end(), m.group(0)))
    it.hits["reference_anchor"] = ref_hits
    it.hits["verdict_mark"] = _rule_hits("verdict_mark", text, VERDICT_RULES)

    it.missing = [k for k in ELEMENT_KEYS if not it.hits.get(k)]

    if it.hits["threshold"]:
        it.labels.append(LABEL_NUMERIC)
    elif it.hits["verdict_mark"]:
        it.labels.append(LABEL_BOOL)
    if not it.hits["reference_anchor"]:
        it.labels.append(LABEL_NO_REF)

    for k in it.missing:
        it.reason_codes.append(MISSING_CODE[k])
    if not it.hits["threshold"] and _VAGUE_RE.search(text):
        it.reason_codes.append("CRIT_VAGUE_QUANTIFIER")
    if _PLACEHOLDER_PAT.search(text) and not re.search(_NUM, text):
        it.reason_codes.append("CRIT_PLACEHOLDER_ONLY")

    it.blocked = bool(it.missing)
    return it


def _finish(report: LintReport, rendered: List[str],
            extra_reference_terms: Sequence[str]) -> LintReport:
    seen: Dict[str, int] = {}
    for i, raw in enumerate(rendered, start=1):
        it = lint_item(raw, i, extra_reference_terms=extra_reference_terms)
        h = it.sha256
        if h in seen:
            it.reason_codes.append("CRIT_DUPLICATE_ITEM")
            it.blocked = True
        else:
            seen[h] = i
        report.items.append(it)
    return report


def lint(goal: str, *, source: Optional[Dict[str, Any]] = None,
         extra_reference_terms: Sequence[str] = ()) -> LintReport:
    """对一段任务书做判据可判定性 lint（纯函数）。"""
    text = goal or ""
    items, form, span = extract_items(text)
    src = {"kind": "goal", "chars": len(text), "sha256": _sha256(text)}
    if source:
        src.update(source)
    report = LintReport(source=src, block_form=form, block_span=span)
    segment = _find_block(text)[3]
    if _POINTER_PAT.search(segment) and not items:
        report.doc_codes.append("CRIT_POINTER_ONLY")
    if not items:
        report.doc_codes.append("CRIT_NO_CRITERIA_BLOCK")
    report = _finish(report, items, extra_reference_terms)
    # 整篇兜底切句（无判据区标题）且全篇无任何阈值形状 → 认为没有可定位的判据块
    if form == "whole" and not any(it.hits.get("threshold") for it in report.items):
        if "CRIT_NO_CRITERIA_BLOCK" not in report.doc_codes:
            report.doc_codes.append("CRIT_NO_CRITERIA_BLOCK")
    return report


def render_structured(criteria: Sequence[Any]) -> List[str]:
    """把结构化判据渲染成文本形态（与任务书判据同一套元素检查）。"""
    out: List[str] = []
    for c in criteria:
        if not isinstance(c, dict):
            out.append(str(c))
            continue
        metric = str(c.get("metric") or c.get("name") or "").strip()
        thr = c.get("threshold")
        unit = str(c.get("unit") or "").strip()
        if thr is None:
            out.append(metric or _canon(c))
            continue
        try:
            num_s = f"{float(thr):g}"
        except (TypeError, ValueError):
            num_s = str(thr)
        out.append(f"{metric or 'metric'} <= {num_s}{(' ' + unit) if unit else ''}")
    return out


def lint_structured(criteria: Sequence[Any], *, source: Optional[Dict[str, Any]] = None,
                    extra_reference_terms: Sequence[str] = ()) -> LintReport:
    """对结构化判据（节点 metadata.acceptance_criteria）做 lint。"""
    rendered = render_structured(criteria)
    text = "\n".join(rendered)
    src = {"kind": "structured", "chars": len(text), "sha256": _sha256(text),
           "n_criteria": len(rendered)}
    if source:
        src.update(source)
    report = LintReport(source=src, block_form="structured",
                        block_span=(0, len(text)))
    if not rendered:
        report.doc_codes.append("CRIT_NO_CRITERIA_BLOCK")
    return _finish(report, rendered, extra_reference_terms)


# ─────────────────────────── 派发前门 ───────────────────────────

SLOT_BEGIN = "<!-- CRITERIA-LINT-SLOTS:BEGIN -->"
SLOT_END = "<!-- CRITERIA-LINT-SLOTS:END -->"

_SLOT_TEXT: Dict[str, str] = {
    "threshold": "· 阈值槽位：未检出可比较数值边界，请补「<METRIC> <CMP> <NUM><UNIT>」。",
    "reference_anchor": ("· 基准槽位：未检出参照面，请显式写明对标来源"
                         "（解析解/实验/文献/基线/自洽/守恒 之一）。"),
    "verdict_mark": ("· 判定记号槽位：未检出判定语义，请写出比较符或判定动词"
                     "（通过/误差/残差/一致 …）。"),
}


def suggest_slots(report: LintReport) -> str:
    """回填槽位建议块：只列缺的要素，无数字、定界、可剥离（不自证满足）。"""
    miss: List[str] = []
    for it in report.blocked_items:
        for k in it.missing:
            if k not in miss:
                miss.append(k)
    if not miss and not report.doc_codes:
        return ""
    body = [_SLOT_TEXT[k] for k in ELEMENT_KEYS if k in miss]
    if report.doc_codes:
        body.append("· 判据块槽位：未定位到判据块，请把机器验收判据写成编号列表。")
    return (f"\n{SLOT_BEGIN}\n派发前 criteria-lint 追加的结构槽位建议"
            "（只追加、不改判据语义；回填后请删除本区块）：\n"
            + "\n".join(body) + f"\n{SLOT_END}\n")


def strip_slot_blocks(text: str) -> str:
    """剥离注入块（分类前先剥，杜绝「注入造成假合规」）。"""
    return re.sub(r"\n?" + re.escape(SLOT_BEGIN) + r".*?" + re.escape(SLOT_END) + r"\n?",
                  "", text or "", flags=re.S)


def resolve_mode(mode: Optional[str] = None, *, default: str = "advisory") -> str:
    val = (mode or os.getenv(ENV_MODE) or default or "advisory").strip().lower()
    return val if val in GATE_MODES else default


def pre_dispatch(goal: str, *, mode: Optional[str] = None,
                 meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """派发前门判决 dict（``action`` ∈ pass/pass_logged/inject/refuse）。"""
    m = resolve_mode(mode)
    if m == "off":
        return {"gate": "criteria_lint", "action": "pass", "mode": m, "skipped": True}

    meta = meta or {}
    clean = strip_slot_blocks(goal or "")
    crit = meta.get("acceptance_criteria")
    extra_ref = tuple(meta.get("extra_reference_terms") or ())
    ref = meta.get("ext_id", "")
    if isinstance(crit, (list, tuple)) and crit:
        report = lint_structured(crit, source={"ref": ref},
                                 extra_reference_terms=extra_ref)
    else:
        report = lint(clean, source={"ref": ref}, extra_reference_terms=extra_ref)

    out: Dict[str, Any] = {
        "gate": "criteria_lint",
        "mode": m,
        "verdict": report.verdict,
        "items": len(report.items),
        "blocked_items": len(report.blocked_items),
        "block_rate": report.summary()["block_rate"],
        "doc_reason_codes": sorted(set(report.doc_codes)),
        "reason_code_counts": report.reason_counts,
        "reason_codes": sorted(report.reason_counts),
        "input_sha256": report.source.get("sha256", ""),
        "report_sha256": report.report_sha256,
        "injected": False,
        "goal": goal,
    }
    if report.verdict != "fail":
        out["action"] = "pass"
        return out
    if m == "block":
        out["action"] = "refuse"
        return out
    if m == "enforce":
        new_goal = goal if SLOT_BEGIN in (goal or "") else (goal or "") + suggest_slots(report)
        out.update(action="inject", injected=new_goal != (goal or ""), goal=new_goal)
        return out
    out["action"] = "pass_logged"
    return out


# ─────────────────────── 原因码可达性探针（自检/审计）───────────────────────

#: 合成探针电池（零领域语义）：每条只需触发**一个**目标原因码
CODE_PROBE_CASES: Tuple[Tuple[str, str], ...] = (
    ("CRIT_NO_CRITERIA_BLOCK", "这里只有散文，没有任何可判定条款，也没有数字。"),
    ("CRIT_POINTER_ONLY", "【机器验收判据】见战役表 metadata。"),
    ("CRIT_UNPARSABLE_ITEM", "机器验收判据：(a) 指标 A <= 5% 相对实验；(b)  。"),
    ("CRIT_NO_NUMERIC_THRESHOLD", "机器验收判据：(a) 指标 A 相对实验通过。"),
    ("CRIT_NO_REFERENCE_ANCHOR", "机器验收判据：(a) 指标 A <= 0.05。"),
    ("CRIT_NO_VERDICT_MARK", "机器验收判据：(a) 指标 A 与若干次运行结果差别不大。"),
    ("CRIT_VAGUE_QUANTIFIER", "机器验收判据：(a) 指标 A 尽量高一些；"),
    ("CRIT_PLACEHOLDER_ONLY", "机器验收判据：(a) 指标 A 落在 <NUM><UNIT> 内。"),
    ("CRIT_DUPLICATE_ITEM", "机器验收判据：(a) 指标 A <= 0.05 相对实验；"
                            "(b) 指标 A <= 0.05 相对实验。"),
)


def probe_reason_codes() -> Dict[str, Any]:
    """跑合成探针电池，返回「原因码 → 触发它的合成输入」可达性地图。

    用于审计：登记表里的每个原因码都必须**至少有一条**合成输入能触发它
    （否则该码是装饰品）。纯函数，无 IO。
    """
    emitted: Dict[str, List[str]] = {code: [] for code in REASON_CODES}
    for code, text in CODE_PROBE_CASES:
        rep = lint(text)
        for hit_code in rep.reason_counts:
            emitted.setdefault(hit_code, []).append(text)
    # 条目级原因码（抽取器会把过短碎片滤掉，故用 lint_item 直接探）：
    for raw in ("  ", "。", "- -"):
        for hit_code in lint_item(raw, 1).reason_codes:
            emitted.setdefault(hit_code, []).append(f"lint_item({raw!r})")
    reachable = {c: bool(v) for c, v in sorted(emitted.items())}
    return {
        "registry": sorted(REASON_CODES),
        "reachable": reachable,
        "n_registered": len(REASON_CODES),
        "n_reachable": sum(1 for v in reachable.values() if v),
        "unreachable": sorted(c for c, v in reachable.items() if not v),
        "witness": {c: v[:2] for c, v in sorted(emitted.items()) if v},
    }


# ─────────────────────────── 批量（DB / 列表）───────────────────────────

def lint_many(nodes: Sequence[Dict[str, Any]], *,
              mode: Optional[str] = None,
              extra_reference_terms: Sequence[str] = ()) -> Dict[str, Any]:
    """对一批节点（含 goal / acceptance_criteria / ext_id）做 lint 并汇总。

    纯函数（不做 IO）：DB 取数在 :func:`load_nodes_from_db`，便于测试注入。
    """
    rows: List[Dict[str, Any]] = []
    agg: Dict[str, int] = {}
    for r in nodes:
        crit = r.get("criteria") or r.get("acceptance_criteria")
        meta = {"ext_id": r.get("ext_id") or "", "acceptance_criteria": crit,
                "extra_reference_terms": list(extra_reference_terms)}
        rep = (lint_structured(crit, source={"ref": meta["ext_id"]},
                               extra_reference_terms=extra_reference_terms)
               if isinstance(crit, (list, tuple)) and crit
               else lint(strip_slot_blocks(r.get("goal") or ""),
                         source={"ref": meta["ext_id"]},
                         extra_reference_terms=extra_reference_terms))
        d = pre_dispatch(r.get("goal") or "", mode=mode, meta=meta)
        for code, n in rep.reason_counts.items():
            agg[code] = agg.get(code, 0) + n
        rows.append({
            "node_id": r.get("node_id") or r.get("id"),
            "plan_id": r.get("plan_id"),
            "mission_id": r.get("mission_id"),
            "status": r.get("status"),
            "ext_id": meta["ext_id"],
            "source": "structured" if isinstance(crit, (list, tuple)) and crit else "goal",
            "summary": rep.summary(),
            "action": d.get("action"),
            "input_sha256": d.get("input_sha256", ""),
            "report_sha256": d.get("report_sha256", ""),
        })
    n_items = sum(r["summary"]["items"] for r in rows)
    n_blocked = sum(r["summary"]["blocked_items"] for r in rows)
    return {
        "schema": SCHEMA,
        "n_nodes": len(rows),
        "aggregate": {
            "items": n_items,
            "blocked_items": n_blocked,
            "blocked_nodes": sum(1 for r in rows if r["summary"]["verdict"] == "fail"),
            "item_block_rate": round(n_blocked / n_items, 4) if n_items else 1.0,
            "reason_code_counts": dict(sorted(agg.items())),
            "n_reason_codes_emitted": len(agg),
            "actions": {a: sum(1 for r in rows if r["action"] == a)
                        for a in sorted({r["action"] for r in rows})},
        },
        "nodes": rows,
    }


def load_nodes_from_db(plan_id: Optional[int] = None, limit: int = 500,
                       min_plan_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """只读取 campaign_nodes 任务书（psql：TCP+密码 或 peer）。"""
    where = "metadata ? 'goal'"
    if plan_id is not None:
        where += f" and plan_id = {int(plan_id)}"
    if min_plan_id is not None:
        where += f" and plan_id > {int(min_plan_id)}"
    sql = ("select coalesce(json_agg(row_to_json(t)), '[]'::json) from ("
           "select id as node_id, plan_id, mission_id, status,"
           " metadata->>'ext_id' as ext_id, metadata->>'goal' as goal,"
           " metadata->'acceptance_criteria' as criteria"
           f" from campaign_nodes where {where} order by id limit {int(limit)}) t;")
    db = os.environ.get("TP_PG_DB", "tensorplatform")
    pwd = os.environ.get("TP_PG_PASSWORD", "")
    if pwd:
        proc = subprocess.run(
            ["psql", "-h", os.environ.get("TP_PG_HOST", "127.0.0.1"),
             "-U", os.environ.get("TP_PG_USER", "foundation"), "-d", db, "-tA", "-c", sql],
            capture_output=True, text=True, timeout=180,
            env=dict(os.environ, PGPASSWORD=pwd))
    else:
        proc = subprocess.run(["su", "postgres", "-c",
                               f"psql -d {db} -tA -c {json.dumps(sql)}"],
                              capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr[:300]}")
    return json.loads(proc.stdout.strip() or "[]")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="判据可判定性 lint（派发前门）")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--goal-file", default=None)
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--plan-id", type=int, default=None)
    ap.add_argument("--min-plan-id", type=int, default=None)
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--mode", default=None, choices=GATE_MODES)
    ap.add_argument("--extra-terms", default="")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--no-hits", action="store_true")
    ap.add_argument("--code-probe", action="store_true",
                    help="跑原因码可达性探针（合成电池，零领域语义）")
    args = ap.parse_args(argv)

    extra = tuple(t.strip() for t in args.extra_terms.split(",") if t.strip())

    if args.code_probe:
        payload = probe_reason_codes()
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
        print(_canon(payload)[:2000])
        return 0

    if args.from_db:
        payload = lint_many(load_nodes_from_db(args.plan_id, args.limit,
                                              min_plan_id=args.min_plan_id),
                            mode=args.mode, extra_reference_terms=extra)
        payload["plan_id"] = args.plan_id
        payload["min_plan_id"] = args.min_plan_id
    else:
        if args.goal_file:
            text = open(args.goal_file, encoding="utf-8").read()
        elif args.goal is not None:
            text = args.goal
        else:
            text = sys.stdin.read()
        d = pre_dispatch(text, mode=args.mode)
        rep = lint(strip_slot_blocks(text), source={"ref": "cli"}, extra_reference_terms=extra)
        payload = rep.as_dict(with_hits=not args.no_hits)
        payload["decision"] = {k: v for k, v in d.items() if k != "goal"}

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
    print(_canon(payload)[:4000])
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())