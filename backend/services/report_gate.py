"""报告质量门禁（论文级六要素）——纯函数实现，无 FastAPI / DB 依赖。

为什么单独成模块
----------------
门禁原先内联在 ``api/tensor_reports.py`` 里，模块级 import 就会建 SQLAlchemy
engine（需要 psycopg2 / DB 环境），因此任何门禁单测都只能靠
``backend/tests/test_report_gate_standalone.py`` 里的一份**手抄副本**来跑——
副本与生产实现一旦漂移，测试绿灯就与生产行为脱钩（典型的「假门禁」）。
把判据抽到这里后：生产 API 与测试 import 同一份实现，且无依赖可离线复算。

判据（owner 2026-10-29 论文级六要素）
------------------------------------
① 案例介绍（物理背景 + 几何/参数表）
② 怎么做的（方法/流程/工具链）
③ 计算结果（数字，由程序注入——本模块不检查来源，见 limitations）
④ 结果图（图 + caption；纯文档任务须显式写明无图理由）
⑤ 注意点（局限/适用范围）
⑥ 误差判定（关键量 vs 基准的误差 + 显式结论「是否 <3%」；无基准须声明原因）

与旧实现（v1，keyword-only）的差异是**判定语义**，不是关键词表：
  * 旧 physics 把两个正则组拍平成「任一命中即通过」→ 正文出现「参数表」
    就算有物理背景（组间 AND 被写成组内 OR，误放行）。
  * 旧 figures 只要正文出现「图」字即通过（连 figures 列表为空也过）。
  * 旧实现完全没有第 ⑥ 要素（误差判定）检查。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# ── 判据关键词表 ─────────────────────────────────────────────────────────────
# 组内 OR，组间 AND：一个要素的每个子组都必须命中。
REQUIRED_SECTIONS: dict[str, list[list[str]]] = {
    # ① 案例介绍：既要说明问题/物理背景，也要给几何或参数
    "physics_statement": [
        [r"(?i)物理", r"(?i)背景", r"(?i)problem", r"(?i)background", r"(?i)案例", r"(?i)工况"],
        [r"(?i)几何", r"(?i)参数", r"(?i)geometry", r"(?i)parameter", r"(?i)L\s*=\s*\d", r"(?i)船[长宽型]"],
    ],
    # ② 怎么做的
    "method": [
        [r"(?i)方法", r"(?i)method", r"(?i)流程", r"(?i)procedure", r"(?i)工具链", r"(?i)toolchain", r"(?i)求解"],
    ],
    # ③④ 对标/验证入口
    "validation": [
        [
            r"(?i)验证",
            r"(?i)validation",
            r"(?i)对标",
            r"(?i)benchmark",
            r"(?i)参考",
            r"(?i)reference",
            r"(?i)文献",
            r"(?i)解析",
        ],
    ],
}

# ── ① 几何量的**量纲**（M2 单位错位） ─────────────────────────────────────
# 「L = 100」不是几何：没有单位就看不出是 m 还是 mm，而量纲错位正是历史事故
# （阻力按 mm 建模、误差按 % 报）。所以带单位的量必须带单位。
_DIM_SYMBOL_ASSIGN = re.compile(
    r"(?<![A-Za-z0-9_])(L|B|T|D|d|H)\s*=\s*([-+]?\d+(?:\.\d+)?)"
    r"\s*[（(]?\s*([A-Za-z°µ％%/²³·\^]+)?")
#: 可接受的量纲记号（长度/时间/角度/力/压强/体积/无量纲比）。
_DIM_UNIT = re.compile(
    r"^(?:m|mm|cm|km|ft|in|dm|m2|m3|m\^2|m\^3|m²|m³|kg|t|s|min|h|kn|m/s|"
    r"N|kN|Pa|kPa|MPa|deg|°|rad|%|％)$")
#: 几何/参数表中的无量纲量（本来就无单位，不在本规则范围内）。
_DIMENSIONLESS_SYMBOLS = {"CB", "Cp", "CW", "Fr", "Rn", "Fn"}


def unverifiable_dimensions(text: str) -> list[str]:
    """列出「给了数字但没给单位」的量纲符号赋值（如 ``L = 100``）。

    只挑长度/吃水/型宽这类**有量纲**的单字母符号；比值（``L/B = 7.5``）与
    无量纲系数（``Cb = 0.6``）不匹配本规则。
    """
    bad: list[str] = []
    for m in _DIM_SYMBOL_ASSIGN.finditer(text or ""):
        unit = (m.group(3) or "").strip()
        if unit and _DIM_UNIT.match(unit):
            continue
        bad.append(m.group(0).strip())
    return bad


# ── ③ 基准必须是**外部可识别**的（M5 基准替换） ──────────────────────────
# 「与《基线 v0》对标」是自指：任何自造基线都能宣布一遍「一致」。可验证的基准
# 要么属于公认类别（解析解/文献/实验/实测/benchmark/公开数据），要么是具名标准
# （ITTC-1957 / KCS / JHS 这类 ≥2 个大写字母的编号）。
NAMED_REFERENCE_PATTERNS = [
    r"解析",
    r"文献",
    r"实验",
    r"试验",
    r"实测",
    r"理论解",
    r"标准解",
    r"参考解",
    r"公开数据",
    r"标准算例",
    r"(?i)\bbenchmark\b",
    r"(?i)\breference\b",
    r"(?i)\bexperiment",
    r"(?i)\banalytical\b",
    r"\b[A-Z][A-Z0-9]{1,}[-– ]?\d*\b",      # ITTC-1957 / KCS / JHS-2018
    # 具名文献/算例的专名：Schäfer-Turek / Wigley / Holtrop（≥3 字母、首字母大写）
    r"(?<![A-Za-z])[A-ZÄÖÜ][A-Za-zäöüßÄÖÜ0-9_.-]{2,}",
]

# ── ③ 误差数字必须带**可比单位**（M2 单位错位） ───────────────────────────
# 千分号 "‰" 不是可比较单位：读的人无法回答「是否 <3%」。分数形态（无单位）
# 与百分号/百分点/pp 都接受，只有「看起来有单位、其实不可比」的才判违规。
_ERROR_CLAIM = re.compile(
    r"(?:误差|偏差|相对误差|(?i:relative\s+error))[^。；;\n]{0,24}?"
    r"\d+(?:\.\d+)?\s*(%|％|‰|pp|个百分点|percent|倍|ppm|bps|千分比)")
#: 可比单位：能与「是否 <3%」这类百分判据直接对照。
_COMPARABLE_UNITS = ("%", "％", "pp", "个百分点", "percent", "倍")
#: 已知不可比单位：换算出百分数要多一步，报告里出现即为「单位错位」证据。
#: 形态缺失（分数形态 ``误差 0.0167``）不在本规则内——那是合法写法。
_INCOMPARABLE_UNITS = ("‰", "ppm", "bps", "千分比")


def uncomparable_error_units(text: str) -> list[str]:
    """列出「误差数字带不可比单位」的片段（如 ``误差 16.7‰``）。

    只判**已知不可比**的单位记号：千分号/ppm/bps 无法直接回答「是否 <3%」，
    读者必须自己换算，而历史事故正是换算错了（16.7‰ 被当成 16.7%）。
    """
    bad: list[str] = []
    for m in _ERROR_CLAIM.finditer(text or ""):
        unit = (m.group(1) or "").strip()
        if not unit or unit.startswith(_COMPARABLE_UNITS):
            continue
        if unit.startswith(_INCOMPARABLE_UNITS):
            bad.append(m.group(0).strip()[:40])
    return bad


#: 比较句（对标发生在哪一句，就在哪一句要外部可识别基准）。
#: 比较句 = 真在做「A 对 B」的句子（不是出现「比较」二字就算：论文里
#: 「见图中比较曲线」不是比较句，会把合规稿误伤）。
_COMPARISON_SENTENCE = re.compile(
    r"(?:对标)"
    r"|(?:与[^。；;\n]{0,40}?(?:比较|对比|比对|吻合|一致|相符))"
    r"|(?i:\bbenchmark(?:ed|ing)?)\b"
    r"|(?i:\bversus\b|\bvs\.?\s)")
_SENTENCE_SPLIT = re.compile(r"[。；;\n]")


_HEADER_ONLY = re.compile(
    r"^\s*[#>*\-\s]*(?:对标|验证|比较|对比|benchmark|versus|vs\.?)\s*[:：]?\s*$", re.I)


def unbacked_comparisons(text: str) -> list[str]:
    """列出「在做比较、但没点名基准」的句子（M5 基准替换的可见证据）。

    只在**比较句**里要外部基准，而不是全文任意位置：后文的重述（误差判定段）
    里出现 ITTC-1957 不能替前面那句「与《基线 v0》对标」背书。
    """
    bad: list[str] = []
    for sent in _SENTENCE_SPLIT.split(text or ""):
        if not _COMPARISON_SENTENCE.search(sent) or _HEADER_ONLY.match(sent):
            continue
        if len(re.sub(r"[#>*\-\s:：]", "", sent)) < 6:
            continue
        if any(re.search(p, sent) for p in NAMED_REFERENCE_PATTERNS):
            continue
        if any(re.search(p, sent) for p in NO_BASELINE_PATTERNS):
            continue                      # 显式声明无基准：另一条判据管
        bad.append(sent.strip()[:60])
    return bad


# ⑤ 注意点
LIMITATION_PATTERNS = [
    r"(?i)局限",
    r"(?i)limitation",
    r"(?i)注意点",
    r"(?i)适用范围",
    r"(?i)scope",
    r"(?i)展望",
    r"(?i)outlook",
    r"(?i)待办",
]

# ⑥ 误差判定：误差数字 + 结论词；或显式声明无基准
ERROR_NUMBER_PATTERNS = [
    r"误差[^。；;\n]{0,30}?\d+(?:\.\d+)?\s*%",
    r"(?i)relative\s+error[^.\n]{0,30}?\d+(?:\.\d+)?\s*%",
    r"(?i)偏差[^。；;\n]{0,30}?\d+(?:\.\d+)?\s*%",
]
# 严格子集：只认「阈值数字/可接受」这类字面结论（v2.0 初版判据）
ERROR_VERDICT_PATTERNS_CORE = [
    r"<\s*3\s*%",
    r"＜\s*3\s*%",
    r"小于\s*3\s*%",
    r"(?i)低于\s*3\s*%",
    r"不超过",
    r"可接受",
    r"满足",
    r"(?i)acceptable",
    r"(?i)within",
    r"(?i)below",
    r">\s*15\s*%[^\n]{0,12}(不可接受|unacceptable)",
]

# 实测校准补入（依据：833 份已发布报告的审计，见
# docs/reports/report_gate_validation_2026-11-01.md 与 report_gate_audit_*.json）：
# 一线报告常用「一致 / 吻合 / 通过 / 判据 / ✅」给结论，而非「<3%」字面。
# 若只用 CORE，会把「误差 0.57%，✅ 一致」这类合规报告误拒。
ERROR_VERDICT_PATTERNS_EXTENDED = [
    r"一致",
    r"吻合",
    r"相符",
    r"符合",
    r"通过",
    r"达标",
    r"合格",
    r"判据",
    r"✅",
]

ERROR_VERDICT_PATTERNS = ERROR_VERDICT_PATTERNS_CORE + ERROR_VERDICT_PATTERNS_EXTENDED

NO_BASELINE_PATTERNS = [
    r"无基准",
    r"无解析解",
    r"无实验(?:值|数据)?",
    r"无参考值",
    r"无对标",
    r"无可用基准",
    r"暂无基准",
    r"(?i)no\s+baseline",
    r"(?i)no\s+reference\s+value",
    r"(?i)without\s+baseline",
]

# ④ 无图理由（纯文档/后端任务）
NO_FIGURE_JUSTIFICATION_PATTERNS = [
    r"无图",
    r"不含图",
    r"不需要图",
    r"纯文档",
    r"非仿真",
    r"无可视化",
    r"(?i)no\s+figure",
    r"(?i)figure\s+not\s+applicable",
]

ISSUE_TEXT = {
    "missing_physics_statement": "缺少物理背景/案例介绍（需同时给出问题描述与几何或参数表）",
    "missing_method": "缺少方法/流程描述（需包含计算方法、关键设置、工具链）",
    "missing_validation": "缺少验证/对标（需包含与基准/文献/解析解的比较，或声明无基准原因）",
    "missing_figures": "缺少图表（需内嵌≥1 张图，或纯文档任务显式写明无图理由）",
    "figures_missing_caption": "图表缺少 caption（figures[].caption 为空，读者无法自解释）",
    "missing_limitations": "缺少局限与展望（需包含踩过的坑、适用范围、后续改进方向）",
    "missing_error_verdict": "缺少误差判定（需给出关键量 vs 基准的误差数字并显式回答是否<3%；无基准须声明原因）",
}


def _any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def _all(*groups: Iterable[str], text: str) -> bool:
    return all(_any(g, text) for g in groups)


def _figure_caption_ok(figures: list) -> tuple[bool, bool]:
    """Return (has_figures, captions_ok)."""
    if not figures:
        return False, True
    captions_ok = True
    for f in figures:
        if isinstance(f, dict):
            cap = (f.get("caption") or "").strip()
        else:  # 允许 name 字符串列表（旧数据）
            cap = ""
        if not cap:
            captions_ok = False
    return True, captions_ok


def check_report_quality(markdown: str | None, figures: list | None) -> dict[str, Any]:
    """论文级六要素门禁。返回 {passed, issues, checks, details}。"""
    md = markdown or ""
    figs = figures or []
    issues: list[str] = []

    # ① 物理/案例介绍（组间 AND）+ 量纲可验证（M2 单位错位）
    physics_ok = _all(*REQUIRED_SECTIONS["physics_statement"], text=md)
    unitless = unverifiable_dimensions(md)
    if not physics_ok or unitless:
        issues.append("missing_physics_statement")

    # ② 方法
    method_ok = _all(*REQUIRED_SECTIONS["method"], text=md)
    if not method_ok:
        issues.append("missing_method")

    # ③ 对标/验证：入口词 + 外部可识别基准 + 可比单位（M5 基准替换 / M2 单位错位）
    named_refs = [p for p in NAMED_REFERENCE_PATTERNS if re.search(p, md)]
    bad_units = uncomparable_error_units(md)
    unbacked = unbacked_comparisons(md)
    validation_ok = (_all(*REQUIRED_SECTIONS["validation"], text=md)
                     and bool(named_refs) and not bad_units and not unbacked)
    if not validation_ok:
        issues.append("missing_validation")

    # ④ 图：必须有真图，或有显式无图理由；正文提到「图」不再算数
    has_figures, captions_ok = _figure_caption_ok(figs)
    no_fig_note = _any(NO_FIGURE_JUSTIFICATION_PATTERNS, md)
    figures_ok = has_figures or no_fig_note
    if not figures_ok:
        issues.append("missing_figures")
    elif has_figures and not captions_ok:
        issues.append("figures_missing_caption")

    # ⑤ 局限
    limitations_ok = _any(LIMITATION_PATTERNS, md)
    if not limitations_ok:
        issues.append("missing_limitations")

    # ⑥ 误差判定
    has_error_number = _any(ERROR_NUMBER_PATTERNS, md)
    has_verdict = _any(ERROR_VERDICT_PATTERNS, md)
    no_baseline = _any(NO_BASELINE_PATTERNS, md)
    error_verdict_ok = no_baseline or (has_error_number and has_verdict)
    if not error_verdict_ok:
        issues.append("missing_error_verdict")

    return {
        "passed": not issues,
        "issues": [f"{i}: {ISSUE_TEXT[i]}" for i in issues],
        "issue_codes": issues,
        "checks": {
            "physics_statement": physics_ok,
            "method": method_ok,
            "validation": validation_ok,
            "figures": figures_ok,
            "limitations": limitations_ok,
            "error_verdict": error_verdict_ok,
        },
        "details": {
            "has_figures": has_figures,
            "captions_ok": captions_ok,
            "no_figure_justification": no_fig_note,
            "error_number": has_error_number,
            "verdict_word": has_verdict,
            "no_baseline_declared": no_baseline,
            "unverifiable_dimensions": unitless,
            "named_reference_hits": named_refs,
            "uncomparable_error_units": bad_units,
            "unbacked_comparisons": unbacked,
        },
    }