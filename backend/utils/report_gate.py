"""报告证据闸门 v2 — 论文级报告的可机器判据校验（纯函数，无 DB / 无 IO 依赖）。

背景（为何要 v2）
-----------------
``api/tensor_reports.py`` 里的 v1 闸门 ``_check_report_quality`` 只做**关键词存在性**
匹配：``物理|背景`` 命中即算有「案例介绍」，``方法`` 命中即算有「方法」。这类闸门对
「关键词堆砌」完全无免疫力——一段 ``物理 几何 方法 验证 图 局限`` 的占位文本即可满分
通过，而真实报告缺「结果数字表」「误差判定」时反而无告警。响应 1066（验证闸门自身的
变异审计）的同类教训：**只查关键词的闸门 = 假门禁**。

v2 判据（每条都要求"证据"而不只是"词"）
--------------------------------------
E1 案例介绍   物理/背景词 + 同一段落内出现数字（几何/参数须可核）
E2 方法流程   方法词 + 同一段落内出现数字（版本/步数/设置）
E3 结果数字   存在 markdown 表格（≥2 数据行，≥1 数值单元）或 ≥3 处带单位数值
E4 图件       声明了图 → 每张图必须能被 ``figure_probe`` 取到真 PNG（魔数校验）；
              未声明图 → 正文必须显式写明「无图」+ 理由
E5 注意点     局限/对照/适用范围词 + 该段落可读长度 ≥ 24 字符
E6 误差判定   必须显式出现「<3%」类阈值语句，且存在 ≥1 个误差百分比数值，
              并给出是/否 判决（表头含"误差"的表格内出现 是/否，或阈值邻近 200 字符内出现判决词）

反刷分：任一要素若只在**不含任何数字**的段落里被命中，记为 ``keyword_only_<element>``
——纯词表堆砌不再能拿到该要素分。

判决输出保持 v1 字段名兼容（``passed`` / ``issues`` / ``checks``），新增
``gate_version`` / ``score`` / ``details``。本体只回答「证据是否齐备」，不回答「数值是否
正确」——后者必须由解析解/文献基准承担，闸门不越权。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

GATE_VERSION = 2
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ── 词表（只用于定位"声称"，证据强度由下面的伴随判据给） ────────────────────
_PHYSICS = re.compile(r"物理|背景|案例介绍|工况背景|problem\s+background", re.I)
_GEOMETRY = re.compile(
    r"几何|geometry|参数表|主尺度|垂线间长|方形系数|排水量|parameter|Lpp|Cb\b", re.I
)
_METHOD = re.compile(r"方法|method|流程|procedure|做法|工具链|toolchain|算法|设置", re.I)
_VALIDATION = re.compile(
    r"验证|validation|对标|benchmark|参考值|reference|文献|解析解|误差|error", re.I
)
_FIGURE_WORD = re.compile(r"图|figure|云图|contour|曲线|curve|谱", re.I)
_LIMITATION = re.compile(
    r"局限|limitation|注意点|坑|适用范围|适用范围|不足|未做|未覆盖|caveat|outlook", re.I
)

# ── 误差判定的硬结构 ────────────────────────────────────────────────────────
_THRESHOLD = re.compile(r"(?:<|＜|小于|低于|≤|不超过)\s*3\s*%|3\s*%\s*(?:阈值|门槛|门限|以内|标准)")
_VERDICT_WORD = re.compile(r"是|否|达成|满足|未达成|不满足|通过|未通过|yes|no|pass|fail", re.I)
_ERROR_PCT = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*%")
_ERROR_WORD = re.compile(r"误差|error|偏差|相对误差|deviation", re.I)
_NO_FIGURE = re.compile(
    r"(?:无图|不附图|未附图|没有图|no\s+figures?|无图表|纯文(?:档|本)任务)", re.I
)

_MIN_SENTENCE = 24  # 段落最小可读长度（去空白字符数）


@dataclass
class Element:
    key: str
    label: str
    ok: bool
    reason: str = ""
    code: str | None = None


def _blocks(markdown: str) -> list[str]:
    """按空行切段落；表格行整体作为一块保留。"""
    out: list[str] = []
    buf: list[str] = []
    for line in markdown.splitlines():
        if line.strip() == "":
            if buf:
                out.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        out.append("\n".join(buf))
    return out


def _has_digit(text: str) -> bool:
    return re.search(r"\d", text) is not None


def _hit_blocks(blocks: Sequence[str], pattern: re.Pattern) -> list[str]:
    return [b for b in blocks if pattern.search(b)]


def _evidence(blocks: Sequence[str], pattern: re.Pattern, *, need_digit: bool) -> tuple[bool, bool]:
    """返回 (有证据, 只有纯词堆砌)。"""
    hits = _hit_blocks(blocks, pattern)
    if not hits:
        return False, False
    if not need_digit:
        readable = any(len(re.sub(r"\s", "", b)) >= _MIN_SENTENCE for b in hits)
        return readable, not readable
    backed = [b for b in hits if _has_digit(b)]
    return bool(backed), not backed


# ── markdown 表格解析 ───────────────────────────────────────────────────────
def _tables(markdown: str) -> list[list[list[str]]]:
    """抽出所有 markdown 表格，返回 [[header, row1, row2, ...], ...]。"""
    tables: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # 分隔行
            cur.append(cells)
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)
    return tables


def _numeric_table(markdown: str) -> tuple[bool, str]:
    for tbl in _tables(markdown):
        body = tbl[1:]
        if len(body) < 2:
            continue
        numeric = sum(1 for row in body if any(re.search(r"-?\d", c) for c in row))
        if numeric >= 2:
            return True, f"{len(body)} 行数据表（{numeric} 行含数值）"
    n_unit = len(re.findall(r"-?\d+(?:\.\d+)?\s*(?:%|kN|kPa|MPa|m/s|kg|mm|m\b|s\b|Hz|dB|kW|kWh|°|deg)", markdown))
    if n_unit >= 3:
        return True, f"正文 {n_unit} 处带单位数值"
    return False, "未见数值表/带单位数值"


def _error_judgment(markdown: str) -> tuple[bool, str]:
    thresh_hits = list(_THRESHOLD.finditer(markdown))
    if not thresh_hits:
        return False, "缺少显式「<3%」阈值判决语句"
    # 阈值语句自身含 "3%"，不能当作误差数值——先抹掉再找误差
    residual = markdown
    for m in reversed(thresh_hits):
        residual = residual[: m.start()] + " " + residual[m.end():]
    # 误差数值必须落在"误差语境"里（±100 字符内出现 误差/error/偏差），
    # 否则正文里任何一个 "%"（如"较基线 +3.2%"）都会被误当成误差证据。
    err_num = None
    for m in _ERROR_PCT.finditer(residual):
        window = residual[max(0, m.start() - 100): m.end() + 100]
        if _ERROR_WORD.search(window):
            err_num = m.group(0)
            break
    if err_num is None:
        return False, "有阈值语句但无任何误差语境中的百分比数值"
    # 判决词须与阈值相关：优先看表头含「误差」的表格里的 是/否
    for tbl in _tables(markdown):
        header = " ".join(tbl[0])
        if _ERROR_WORD.search(header):
            for row in tbl[1:]:
                if any(_VERDICT_WORD.search(c) for c in row):
                    return True, "误差判定表含显式判决"
    for m in _THRESHOLD.finditer(markdown):
        window = markdown[max(0, m.start() - 200): m.end() + 200]
        if _VERDICT_WORD.search(window) and _ERROR_WORD.search(window):
            return True, "阈值附近含判决词"
    return False, "误差数值与判决未在同一判定语境"


def _figure_check(
    markdown: str,
    figures: Sequence[Any],
    figure_probe: Callable[[int], bytes] | None,
    report_id: int | None,
) -> tuple[bool, str, list[str]]:
    issues: list[str] = []
    if not figures:
        if _NO_FIGURE.search(markdown):
            return True, "声明无图并给出理由（可接受）", issues
        return False, "既无图件也未声明无图理由", ["missing_figures"]
    ok_all = True
    notes: list[str] = []
    for fig in figures:
        name = fig.get("name") if isinstance(fig, dict) else str(fig)
        caption = (fig.get("caption") if isinstance(fig, dict) else "") or ""
        if len(caption.strip()) < 8:
            issues.append(f"v2_figure_caption_thin:{name}")
            ok_all = False
        if figure_probe is None or report_id is None:
            continue
        try:
            raw = figure_probe(report_id)
        except Exception as exc:  # noqa: BLE001 — 探针失败即证据缺失
            issues.append(f"v2_figure_unreadable:{name}:{type(exc).__name__}")
            ok_all = False
            continue
        if not raw.startswith(PNG_MAGIC):
            issues.append(f"v2_figure_not_png:{name}")
            ok_all = False
        elif len(raw) < 1024:
            issues.append(f"v2_figure_too_small:{name}")
            ok_all = False
        else:
            notes.append(f"{name}:{len(raw)}B")
    return ok_all, "图件取证 " + (", ".join(notes) if notes else "（未探测，仅元数据）"), issues


def evaluate(
    markdown: str,
    figures: Sequence[Any] | None = None,
    *,
    figure_probe: Callable[[int], bytes] | None = None,
    report_id: int | None = None,
) -> dict[str, Any]:
    """核心判决：返回 v1 兼容字段 + v2 明细。"""
    figures = list(figures or [])
    md = markdown or ""
    blocks = _blocks(md)

    elements: list[Element] = []

    physics_ev, physics_only = _evidence(blocks, _PHYSICS, need_digit=True)
    geom_ev, geom_only = _evidence(blocks, _GEOMETRY, need_digit=True)
    e1 = physics_ev or geom_ev
    elements.append(
        Element("physics_statement", "案例介绍/物理背景", e1,
                "" if e1 else "缺物理背景或几何参数的**带数字**描述",
                None if e1 else "missing_physics_statement")
    )
    if not e1 and (physics_only or geom_only):
        elements.append(Element("anti_gaming_physics", "反刷分", False,
                                "背景词仅出现在无数字段落", "keyword_only_physics_statement"))

    method_ev, method_only = _evidence(blocks, _METHOD, need_digit=True)
    elements.append(Element("method", "方法/流程", method_ev,
                            "" if method_ev else "缺方法与关键设置（须含数字证据）",
                            None if method_ev else "missing_method"))
    if not method_ev and method_only:
        elements.append(Element("anti_gaming_method", "反刷分", False,
                                "方法词仅出现在无数字段落", "keyword_only_method"))

    valid_ev, valid_only = _evidence(blocks, _VALIDATION, need_digit=True)
    elements.append(Element("validation", "验证/对标", valid_ev,
                            "" if valid_ev else "缺基准对标或误差数值",
                            None if valid_ev else "missing_validation"))

    numeric_ev, numeric_note = _numeric_table(md)
    elements.append(Element("numeric_results", "结果数字表", numeric_ev, numeric_note,
                            None if numeric_ev else "v2_missing_numeric_results"))

    fig_ok, fig_note, fig_issues = _figure_check(md, figures, figure_probe, report_id)
    elements.append(Element("figures", "图件", fig_ok, fig_note,
                            None if fig_ok else "missing_figures"))
    elements.append(Element("figure_evidence", "图件取证", not fig_issues,
                            fig_note, None if not fig_issues else fig_issues[0]))
    for fi in fig_issues[1:]:
        elements.append(Element("figure_evidence2", "图件取证", False, fi, fi))

    limit_ev, limit_only = _evidence(blocks, _LIMITATION, need_digit=False)
    elements.append(Element("limitations", "注意点/局限", limit_ev,
                            "" if limit_ev else "缺局限与适用范围段落",
                            None if limit_ev else "missing_limitations"))

    err_ok, err_note = _error_judgment(md)
    elements.append(Element("error_judgment", "误差判定<3%", err_ok, err_note,
                            None if err_ok else "v2_missing_error_judgment"))

    skip = {"anti_gaming_physics", "anti_gaming_method", "figure_evidence", "figure_evidence2"}
    hard = [e for e in elements if e.key not in skip]
    passed_n = sum(1 for e in hard if e.ok)
    issues = [e.code for e in elements if e.code and not e.ok]
    passed = all(e.ok for e in elements)

    checks = {e.key: e.ok for e in elements}
    # v1 字段名对齐（下游 qa_status 只看 passed/issues）
    return {
        "gate_version": GATE_VERSION,
        "passed": passed,
        "score": round(passed_n / len(hard), 4),
        "issues": issues,
        "checks": checks,
        "details": {e.key: {"ok": e.ok, "label": e.label, "note": e.reason} for e in elements},
        "figure_note": fig_note,
    }


def evaluate_path(markdown: str, figure_paths: Sequence[Path] | None = None) -> dict[str, Any]:
    """离线便捷入口：按磁盘文件校验图件（测试/审计脚本用）。"""
    paths = list(figure_paths or [])
    figs = [{"name": p.stem, "caption": "disk-figure-caption-placeholder"} for p in paths]

    def _probe(_rid: int) -> bytes:
        raise FileNotFoundError("use evaluate_path")

    if not paths:
        return evaluate(markdown, figs)
    # 逐张直接读盘
    issues_seen: list[str] = []
    ok = True
    for p in paths:
        try:
            raw = p.read_bytes()
        except OSError:
            issues_seen.append(f"v2_figure_unreadable:{p.stem}")
            ok = False
            continue
        if not raw.startswith(PNG_MAGIC) or len(raw) < 1024:
            issues_seen.append(f"v2_figure_not_png:{p.stem}")
            ok = False
    base = evaluate(markdown, figs)
    if not ok:
        base["passed"] = False
        base["issues"] = list(base["issues"]) + issues_seen
        base["checks"]["figure_evidence"] = False
    return base


__all__ = ["GATE_VERSION", "PNG_MAGIC", "evaluate", "evaluate_path", "_tables"]