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

import os
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

    # ① 物理/案例介绍（组间 AND）
    physics_ok = _all(*REQUIRED_SECTIONS["physics_statement"], text=md)
    if not physics_ok:
        issues.append("missing_physics_statement")

    # ② 方法
    method_ok = _all(*REQUIRED_SECTIONS["method"], text=md)
    if not method_ok:
        issues.append("missing_method")

    # ③ 对标/验证
    validation_ok = _all(*REQUIRED_SECTIONS["validation"], text=md)
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
        },
    }

# ─────────────────────────── v1 档案（keyword-only 生产门禁的逐字复刻） ───────────
# 动机：v1 判据原本内联在 ``api/tensor_reports.py`` 里，无法被纯函数测试直接引用，
# 于是出现了两份实现（生产 / 手抄副本）与一次「v1 仍在生产、v2 只在审计里」的欠账
# （docs/reports/report_gate_audit_2026-11-01.json：v1 误放行率 35.84%）。
# 这里把 v1 逐字保存为可 import 的档案实现，供 (a) 迁移期兼容，
# (b) 审计工具作为 v1 真身参与对账，(c) 单测钉死「共享 v1 ≡ 历史 v1」。
REQUIRED_SECTIONS_V1: dict[str, list[str]] = {
    "physics_statement": [
        r"(?i)(物理|背景|problem|background|物理背景|案例介绍)",
        r"(?i)(几何|参数|geometry|parameter|参数表)",
    ],
    "traceable_criteria": [
        r"(?i)(方法|method|流程|procedure|工具链|toolchain)",
        r"(?i)(验证|validation|对标|benchmark|参考|reference|文献|误差|error)",
    ],
    "figures": [
        r"(?i)(图|figure|云图|contour|曲线|curve|比较|comparison)",
    ],
    "limitations": [
        r"(?i)(局限|limitation|注意|note|适用|scope|范围|展望|outlook|future)",
    ],
}


def check_report_quality_v1(markdown: str | None, figures: list | None) -> dict[str, Any]:
    """v1（keyword-only）门禁：与 2026-11-01 前的生产实现逐字等价。

    已知失效模式（审计量化，勿再引入新代码）：
      * ① 要素把组间 AND 拍平成组内 OR（只写「参数表」即过）；
      * ④ 只要正文出现「图」字即算有图（figures 为空也过）；
      * 完全没有第 ⑥ 要素（误差判定）检查。
    保留它的唯一理由：``warn`` 迁移期需要与线上历史判决逐位同构。
    """
    md = markdown or ""
    figs = figures or []
    issues: list[str] = []

    physics_found = any(
        re.search(pattern, md) for pattern in REQUIRED_SECTIONS_V1["physics_statement"]
    )
    if not physics_found:
        issues.append("missing_physics_statement: 缺少物理背景/案例介绍（需包含物理问题描述、几何参数）")

    method_found = any(re.search(p, md) for p in REQUIRED_SECTIONS_V1["traceable_criteria"][:1])
    validation_found = any(re.search(p, md) for p in REQUIRED_SECTIONS_V1["traceable_criteria"][1:])
    if not method_found:
        issues.append("missing_method: 缺少方法/流程描述（需包含计算方法、关键设置、工具链）")
    if not validation_found:
        issues.append("missing_validation: 缺少验证/对标（需包含与基准/文献/解析解的比较，或声明无基准原因）")

    figure_word = any(re.search(p, md) for p in REQUIRED_SECTIONS_V1["figures"])
    if not figure_word and not figs:
        issues.append("missing_figures: 缺少图表（需包含场量云图/比较曲线，或纯文档任务需写明无图理由）")

    limitations_found = any(re.search(p, md) for p in REQUIRED_SECTIONS_V1["limitations"])
    if not limitations_found:
        issues.append("missing_limitations: 缺少局限与展望（需包含踩过的坑、适用范围、后续改进方向）")

    return {
        "passed": not issues,
        "issues": issues,
        "checks": {
            "physics_statement": physics_found,
            "method": method_found,
            "validation": validation_found,
            "figures": figure_word or bool(figs),
            "limitations": limitations_found,
        },
    }


# ─────────────────────────── 灰度开关与统一判决 ────────────────────────────────
GATE_MODE_ENV = "TP_REPORT_GATE_MODE"
GATE_MODES: tuple[str, ...] = ("off", "warn", "enforce")
DEFAULT_GATE_MODE = "warn"


def gate_mode(explicit: str | None = None) -> str:
    """解析灰度模式。显式入参非法 → ValueError（API 层转 422）；环境变量非法 → 回落默认。

    off     ：完全保持 v1 判决（等价于灰度上线前的线上行为）。
    warn    ：v1 判决不变（**不改 qa_status**），v2 六要素判决作为 advisory 一并返回并落库取证。
    enforce ：由 v2 六要素判决决定 qa_status。
    """
    if explicit is not None:
        candidate = str(explicit).strip().lower()
        if candidate not in GATE_MODES:
            raise ValueError(f"invalid report gate mode: {explicit!r} (allowed: {', '.join(GATE_MODES)})")
        return candidate
    raw = str(os.environ.get(GATE_MODE_ENV, DEFAULT_GATE_MODE)).strip().lower()
    return raw if raw in GATE_MODES else DEFAULT_GATE_MODE


def evaluate_report(markdown: str | None, figures: list | None = None, mode: str | None = None) -> dict[str, Any]:
    """一次算两个判决（v1 + v2 六要素），按灰度模式决定谁有权写 qa_status。

    返回结构刻意把「谁判决的」(decided_by)、「两版是否分歧」(diverges)、
    「warn 期本可翻盘」(would_flip) 都显式化，便于对账与后续 enforce 决策。
    """
    resolved = gate_mode(mode)
    v1 = check_report_quality_v1(markdown, figures)
    v2 = check_report_quality(markdown, figures)
    decided_by = "v1" if resolved in ("off", "warn") else "v2"
    decision = v1 if decided_by == "v1" else v2
    return {
        "mode": resolved,
        "decided_by": decided_by,
        "passed": decision["passed"],
        "issues": decision["issues"],
        "checks": decision["checks"],
        "v1": {"passed": v1["passed"], "issues": v1["issues"], "checks": v1["checks"]},
        "v2": {
            "passed": v2["passed"],
            "issues": v2["issues"],
            "issue_codes": v2["issue_codes"],
            "checks": v2["checks"],
            "details": v2["details"],
        },
        # warn 期：v2 只做 advisory，绝不改 qa_status；这些是「将来 enforce 会打回」的候选
        "v2_advisory_issues": v2["issues"] if decided_by == "v1" else [],
        "diverges": v1["passed"] != v2["passed"],
        "would_flip": decided_by == "v1" and v1["passed"] and not v2["passed"],
    }
