"""判据抽取器：把报告/文档里的「误差判定表」解析成四元组记录。

为什么要有抽取器
----------------
判决的历史证据几乎全是**表格**：一列是被测量、一列是基准、一列是误差/阈值、一列是
"是否满足"。这些表人读毫无歧义，机器读却处处是坑（"1e-10" 里的减号不是区间分隔符，
"0.04-0.06" 是区间不是减法，">90%" 是单边界不是容差）。抽取器把这些坑显式化：

* 先做**数值 token 化**，再看 token 之间的字面文本决定关系（``vs`` / 纯连字符 / 其他），
  这样指数记数法 ``1e-10`` 永远被当作单个 token。
* 角色由表头关键词决定；关键词表从 ``config/criteria_metrology/parse_terms.json`` 注入，
  代码里只有角色名（quantity/reference/measured/tolerance/verdict/note）。
* 只认"能算出数"的行；认不出的行**不猜**，进 ``rejected`` 桶并给原因——
  抽取率是报告的一部分，不是隐藏的实现细节。

零领域语义：本模块不认识任何物理量名；"误差/基准/容差"是度量学术语，不是领域词条。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .model import Criterion, Interval, Metric, Verdict

# --------------------------------------------------------------------------- #
# 关键词（可由配置文件覆盖）
# --------------------------------------------------------------------------- #

DEFAULT_TERMS: dict[str, list[str]] = {
    "quantity": ["关键量", "被测量", "物理量", "项目", "指标", "quantity", "量"],
    "reference": ["基准", "参考", "来源", "对照", "reference", "baseline"],
    "measured": ["实测", "实际", "本实现", "本节点结果", "结果", "measured", "value"],
    "error": ["误差", "偏差", "相对差", "error", "deviation"],
    "tolerance": ["要求", "阈值", "容差", "容许", "判据带", "threshold", "tolerance", "要求值"],
    "verdict": ["是否", "判定", "满足", "结论", "verdict"],
    "note": ["说明", "备注", "注意", "局限", "note", "comment"],
}

#: 角色优先级：一个表头同时命中多个角色时，取"更专门"的那个。
#: ``error`` 排在 ``measured`` 之前：``误差`` 列装的是"已经算好的偏差"，
#: 而 ``实测`` 列装的是被测量本身——两者混用会把误差当被测量，判决全错。
ROLE_PRIORITY = ("tolerance", "verdict", "reference", "error", "measured", "quantity", "note")

TOKEN_RE = re.compile(r"(?P<num>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?P<pct>[%％])?")
SCI_GUARD_RE = re.compile(r"[eE][+-]?\d+$")

#: 数值占比门槛与"短单元格"豁免（短到无法成为散文时直接认作数值）
MIN_NUMERIC_RATIO = 0.25
SHORT_CELL = 12

_PASS_MARKS = ("✅", "是", "pass", "满足", "通过", "yes", "✓")
_FAIL_MARKS = ("❌", "否", "fail", "不满足", "未通过", "no", "✗")
_PARTIAL_MARKS = ("⚠", "部分", "partial")


@dataclass
class NumberToken:
    value: float
    percent: bool
    start: int
    end: int
    raw: str


@dataclass
class ParsedRow:
    """解析成功的一行（还没组装成 Criterion）。"""

    criterion_id: str
    quantity: str | None
    y: float | None
    y_ref: float | None
    interval: Interval | None
    tau: float | None
    metric: Metric
    comparator: str
    declared_verdict: Verdict | None
    reported_error: float | None
    derived_y: bool
    unit: str | None
    condition: str | None
    notes: str | None
    source: dict[str, Any] = field(default_factory=dict)

    def to_criterion(self) -> Criterion:
        c = Criterion(
            criterion_id=self.criterion_id,
            y=self.y,
            y_ref=self.y_ref,
            tau=self.tau,
            metric=self.metric,
            unit=self.unit,
            condition=self.condition,
            reference_interval=self.interval,
            comparator=self.comparator,
            quantity=self.quantity,
            declared_verdict=self.declared_verdict,
            source=self.source,
            notes=self.notes,
        )
        if self.reported_error is not None:
            c = Criterion(
                criterion_id=c.criterion_id,
                y=c.y,
                y_ref=c.y_ref,
                tau=c.tau,
                metric=c.metric,
                unit=c.unit,
                condition=c.condition,
                reference_interval=c.reference_interval,
                comparator=c.comparator,
                quantity=c.quantity,
                declared_verdict=c.declared_verdict,
                source={**c.source, "reported_error": self.reported_error},
                notes=c.notes,
            )
        return c


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #


#: 日期/时刻：先整段屏蔽，否则 ``2026-11-07T01:10`` 会被当成被测量 2026 与区间 11-07
DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?")
CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def normalize_text(s: str) -> str:
    s = DATE_RE.sub(" ", s)
    s = CLOCK_RE.sub(" ", s)
    return (
        s.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("～", "~")
        .replace("〜", "~")
        .replace("±", "+-")
        .replace("（", "(")
        .replace("）", ")")
        .replace("％", "%")
    )


def tokenize_numbers(text: str) -> list[NumberToken]:
    """数值 token 化。指数记数法作为单 token，不把 ``-`` 误当区间分隔符。"""
    text = normalize_text(text)
    out: list[NumberToken] = []
    for m in TOKEN_RE.finditer(text):
        raw = m.group("num")
        # 前一个字符是字母/数字（如 ``B2``）则不是一个独立数值
        if m.start() > 0 and (text[m.start() - 1].isalpha()):
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if not math.isfinite(val):
            continue
        out.append(NumberToken(val, bool(m.group("pct")), m.start(), m.end(), raw))
    return out


def _between(text: str, a: NumberToken, b: NumberToken) -> str:
    return normalize_text(text)[a.end : b.start].strip()


_PAIR_WORDS = ("vs", "v.s", "对", "比", "→", "->", "&")


def analyze_numeric_cell(text: str) -> dict[str, Any]:
    """分析一个单元格的数值结构。

    返回 ``{"numbers": [...], "relation": "interval"|"pair"|"single"|"multi"|"none"}``。
    区间判定要求两个 token 之间的字面文本是**纯连字符**（长度 ≤12、只含 -~— 与空白），
    这排除了 ``1e-10``（单 token）与 ``3.83%-0.017%``（两侧都是百分数，是减不是区间）。
    """
    toks = tokenize_numbers(text)
    if not toks:
        return {"numbers": [], "relation": "none", "tokens": []}
    if len(toks) == 1:
        return {"numbers": [toks[0].value], "relation": "single", "tokens": toks, "percent": toks[0].percent}
    rel = "multi"
    for a, b in zip(toks, toks[1:]):
        mid = _between(text, a, b)
        if mid and set(mid) <= set("-~ ") and len(mid) <= 12 and not (a.percent or b.percent):
            rel = "interval"
            b_abs = abs(b.value)
            break
        # 连字符被吃进第二个数的符号位（"0.04-0.06" 的 token 是 0.04 与 -0.06）：
        # 若两侧同量级（比值 ∈ [1e-2, 1e2]）且都不是百分数，按区间读。
        if (
            not mid
            and b.raw.startswith("-")
            and not (a.percent or b.percent)
            and a.value != 0.0
            and 1e-2 <= abs(b.value) / abs(a.value) <= 1e2
        ):
            rel = "interval"
            break
        if mid.lower().strip(" .") in _PAIR_WORDS:
            rel = "pair"
            break
    numbers = [t.value for t in toks]
    if rel == "interval" and len(numbers) >= 2:
        # 区间端点一律取正值（"-0.06" 只是被吃掉了分隔符）
        numbers = [numbers[0], abs(numbers[1])] + numbers[2:]
    return {"numbers": numbers, "relation": rel, "tokens": toks, "percent": any(t.percent for t in toks)}


def numeric_confidence(cell: str) -> dict[str, Any]:
    """判定一个单元格是"数值单元格"还是"散文单元格"。

    规则：数值文本（含单位/百分号外的一切非数值字符都算散文）占比 ≥ ``MIN_NUMERIC_RATIO``
    或全单元格 ≤ ``SHORT_CELL`` 字符 → 数值单元格；否则视为散文，**不从中取数**。
    这条规则挡掉最典型的一类误抽取：形如
    ``任务判据：≥20 候选、tier1 ≥8、GAP ≥6`` 的散文里散落的数字。
    """
    text = normalize_text(cell or "")
    if not text.strip():
        return {"is_numeric": False, "ratio": 0.0, "n_numbers": 0}
    toks = tokenize_numbers(text)
    if not toks:
        return {"is_numeric": False, "ratio": 0.0, "n_numbers": 0}
    num_chars = sum(t.end - t.start for t in toks)
    ratio = num_chars / max(len(text.strip()), 1)
    is_numeric = (len(text.strip()) <= SHORT_CELL) or ratio >= MIN_NUMERIC_RATIO
    return {"is_numeric": is_numeric, "ratio": ratio, "n_numbers": len(toks)}


def cell_role(header: str, terms: dict[str, list[str]] | None = None) -> str | None:
    t = (terms or DEFAULT_TERMS)
    h = normalize_text(header).lower()
    hits = {role: any(kw.lower() in h for kw in kws) for role, kws in t.items()}
    for role in ROLE_PRIORITY:
        if hits.get(role):
            return role
    return None


def split_markdown_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_separator_row(cells: Sequence[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip()) and any(cells)


def parse_tolerance(text: str) -> tuple[float | None, str, Metric]:
    """从"要求"单元格解析容差与比较符。

    ``<2%`` → (0.02, "<=", relative)；``≥15 pass`` → (15, ">=", one_sided)；
    ``=1±1%`` → (0.01, "<=", relative)；``<1e-10`` → (1e-10, "<=", absolute)。
    """
    t = normalize_text(text or "")
    if not t.strip():
        return None, "<=", Metric.RELATIVE
    comp = "<="
    if re.search(r"(≥|>=|>)\s*\d", t):
        comp = ">="
    elif re.search(r"(≤|<=|<)\s*\d", t):
        comp = "<="
    toks = tokenize_numbers(t)
    if not toks:
        return None, comp, Metric.RELATIVE
    tok = toks[0]
    if tok.percent:
        return tok.value / 100.0, comp, Metric.RELATIVE
    # 无百分号的容差：≤1 视为相对比例，>1 视为单边界量（如 "≥15 pass"）
    if tok.value <= 1.0:
        return tok.value, comp, Metric.RELATIVE
    return tok.value, comp, Metric.ONE_SIDED


def parse_declared_verdict(text: str) -> Verdict | None:
    t = normalize_text(text or "").lower()
    if not t.strip():
        return None
    if any(m in t for m in _PARTIAL_MARKS):
        return Verdict.UNDECIDABLE
    if any(m in t for m in _FAIL_MARKS):
        return Verdict.FAIL
    if any(m in t for m in _PASS_MARKS):
        return Verdict.PASS
    return None


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# 行 → 记录
# --------------------------------------------------------------------------- #

_HEADER_TAU_RE = re.compile(r"(?:<|≤|<=|＜)\s*(\d+(?:\.\d+)?)\s*[%％]")


def tolerance_from_header(header: str) -> tuple[float | None, str | None]:
    """从表头里读容差：``是否 <3%`` / ``<3% ？`` 这类列头本身就带着 τ。

    报告纪律把容差写在**列头**（"是否 <3%"），而不是单列一列"要求"。抽取器必须认这个
    事实，否则绝大多数历史误差表都会被当成"无容差"丢掉。
    """
    t = normalize_text(header or "")
    m = _HEADER_TAU_RE.search(t)
    if not m:
        return None, None
    try:
        return float(m.group(1)) / 100.0, "<="
    except ValueError:
        return None, None


def build_row_record(
    cells: dict[str, str],
    *,
    source: dict[str, Any],
    terms: dict[str, list[str]] | None = None,
    header_map: dict[str, str] | None = None,
) -> tuple[ParsedRow | None, str | None]:
    """把「角色 → 单元格文本」组装成一条判据记录。

    返回 ``(record, reject_reason)``。任何一侧算不出数就拒绝，绝不猜。
    """
    tol_cell = cells.get("tolerance") or ""
    ref_cell = cells.get("reference") or ""
    meas_cell = cells.get("measured") or ""
    err_cell = cells.get("error") or ""
    quant_cell = cells.get("quantity") or ""
    verdict_cell = cells.get("verdict") or ""
    note_cell = cells.get("note") or ""

    tau, comparator, metric = parse_tolerance(tol_cell)
    if tau is None and header_map:
        h_tau, h_comp = tolerance_from_header(header_map.get("verdict", ""))
        if h_tau is not None:
            tau, comparator = h_tau, h_comp or "<="
            metric = Metric.RELATIVE
    declared = parse_declared_verdict(verdict_cell)

    y: float | None = None
    y_ref: float | None = None
    interval: Interval | None = None
    reported_error: float | None = None
    derived = False
    notes: list[str] = []

    ref_conf = numeric_confidence(ref_cell)
    ref_info = analyze_numeric_cell(ref_cell) if ref_conf["is_numeric"] else {"numbers": [], "relation": "none"}
    meas_conf = numeric_confidence(meas_cell)
    meas_info = analyze_numeric_cell(meas_cell) if meas_conf["is_numeric"] else {"numbers": [], "relation": "none"}
    err_conf = numeric_confidence(err_cell)
    err_info = analyze_numeric_cell(err_cell) if err_conf["is_numeric"] else {"numbers": [], "relation": "none"}

    # 1) 基准侧：区间优先（区间是 T3 的结构证据）；区间基准才有的"多口径"是结构事实
    if ref_info["relation"] == "interval" and len(ref_info["numbers"]) >= 2:
        interval = Interval(ref_info["numbers"][0], ref_info["numbers"][1])
    elif ref_info["numbers"]:
        y_ref = ref_info["numbers"][0]

    # 2) 被测量侧：先看"实测"列（装被测量本身），再看"误差"列（装已算好的偏差）
    if meas_info["relation"] == "pair" and len(meas_info["numbers"]) >= 2:
        y = meas_info["numbers"][0]
        if interval is None:
            y_ref = meas_info["numbers"][1]
    elif meas_info["numbers"]:
        y = meas_info["numbers"][0]

    if err_info["numbers"]:
        v = err_info["numbers"][0]
        reported_error = v / 100.0 if err_info.get("percent") else v
        # 允许"误差"列里直接给出 y vs y_ref 的成对写法
        if err_info["relation"] == "pair" and len(err_info["numbers"]) >= 2:
            y = err_info["numbers"][0]
            if interval is None:
                y_ref = err_info["numbers"][1]

    # 3) 只有基准 + 误差：反推被测量（方向不可知 ⇒ 取 +err，必须登记 derived_y）
    if y is None and reported_error is not None:
        base = y_ref if y_ref is not None else (interval.mid if interval is not None else None)
        if base is not None:
            y = base * (1.0 + reported_error)
            derived = True
            notes.append(
                "y_derived_from_reported_error: 方向不可知（取 +err），仅用于退化测绘"
            )

    if y is None and y_ref is None and interval is None:
        return None, "no_numeric_reference"
    if y is None:
        # 有误差但既无基准也无区间 ⇒ 反推不出被测量，这条行不是可判判据
        return None, "no_measurement"
    if tau is None:
        notes.append("tau_missing: 该行没有可解析的容差，判据不完整")

    cid = _sha(
        json.dumps(
            {
                "quantity": quant_cell,
                "meas": meas_cell,
                "ref": ref_cell,
                "err": err_cell,
                "tol": tol_cell,
                "verdict": verdict_cell,
                "src": source.get("path") or source.get("report_id"),
                "line": source.get("line"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )[:16]

    rec = ParsedRow(
        criterion_id=cid,
        quantity=quant_cell or None,
        y=y,
        y_ref=y_ref,
        interval=interval,
        tau=tau,
        metric=metric,
        comparator=comparator,
        declared_verdict=declared,
        reported_error=reported_error,
        derived_y=derived,
        unit=None,
        condition=None,
        notes="; ".join(notes) if notes else None,
        source={
            **source,
            "derived_y": derived,
            "cells": dict(cells),
            "numeric_confidence": {
                "reference": ref_conf["ratio"],
                "measured": meas_conf["ratio"],
                "error": err_conf["ratio"],
            },
        },
    )
    return rec, None


def parse_markdown_criteria(
    text: str,
    *,
    source: dict[str, Any] | None = None,
    terms: dict[str, list[str]] | None = None,
    require_tolerance_role: bool = False,
) -> tuple[list[ParsedRow], list[dict[str, Any]]]:
    """从 markdown 文本抽取全部误差判定行。

    返回 ``(records, rejected)``；``rejected`` 每条带 ``reason`` 与行号，抽取率可审计。
    ``require_tolerance_role`` 为真时只认"有专门要求列"的表（严格模式）；
    缺省关闭，因为报告纪律把容差写在列头。
    """
    src = dict(source or {})
    rows: list[ParsedRow] = []
    rejected: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip().startswith("|"):
            i += 1
            continue
        header_cells = split_markdown_row(line)
        if i + 1 >= len(lines) or not is_separator_row(split_markdown_row(lines[i + 1])):
            i += 1
            continue
        roles = [cell_role(h, terms) for h in header_cells]
        role_set = set(r for r in roles if r)
        if require_tolerance_role and "tolerance" not in role_set:
            i += 1
            continue
        if not ({"tolerance", "reference", "measured", "error"} & role_set):
            i += 1
            continue
        header_map: dict[str, str] = {}
        for role, h in zip(roles, header_cells):
            if role and role not in header_map:
                header_map[role] = h
        i += 2
        while i < len(lines) and lines[i].strip().startswith("|"):
            cells = split_markdown_row(lines[i])
            if len(cells) >= len(roles):
                mapped: dict[str, str] = {}
                for role, cell in zip(roles, cells):
                    if role and role not in mapped:
                        mapped[role] = cell
                rec, reason = build_row_record(
                    mapped,
                    source={**src, "line": i + 1, "table_header": header_cells, "row": lines[i].strip()},
                    terms=terms,
                    header_map=header_map,
                )
                if rec is not None:
                    rows.append(rec)
                else:
                    rejected.append(
                        {"line": i + 1, "reason": reason, "row": lines[i].strip()[:200], "roles": mapped}
                    )
            else:
                rejected.append({"line": i + 1, "reason": "column_count_mismatch", "row": lines[i].strip()[:200]})
            i += 1
    return rows, rejected


def parse_record(rec: dict[str, Any]) -> Criterion:
    """结构化记录（案件/夹具/工件 JSON）→ Criterion。"""
    return Criterion.from_dict(rec)


def harvest_markdown_dir(
    paths: Iterable[Path],
    *,
    root: Path | None = None,
    terms: dict[str, list[str]] | None = None,
    min_tau: bool = True,
) -> dict[str, Any]:
    """批量抽取目录内 markdown 的判据行（确定性顺序），返回语料快照。"""
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    files = sorted(Path(p) for p in paths)
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(root)) if root else str(p)
        rows, rej = parse_markdown_criteria(
            text,
            source={"kind": "markdown", "path": rel},
            terms=terms,
        )
        for r in rows:
            if min_tau and r.tau is None:
                rejected.append({"path": rel, "line": r.source.get("line"), "reason": "tau_missing"})
                continue
            rec = r.to_criterion().as_dict()
            rec["source"]["path"] = rel
            records.append(rec)
        for x in rej:
            rejected.append({"path": rel, **x})
    return {
        "n_files": len(files),
        "n_records": len(records),
        "n_rejected": len(rejected),
        "records": records,
        "rejected": rejected[:500],
    }