"""FAIL/WITHHELD 欠账提取器（报告 markdown + pending 终态判词 → DebtRow 流）。

两类一手载体
------------
1. **报告误差判定表**（``/tensor/reports`` 正文）：列名中英混排（``关键量/基准/误差/<3%？``、
   ``quantity/reference/error/<3%?``），判词列取值形态多（``是/否``、``PASS/WITHHELD``、
   ``✅/❌``、``**否（honest FAIL，…）**``）。
2. **pending 终态判词**（``benchmarks/pending/*/terminal_verdict*.md``）：多为「一行一臂、
   多列误差（T=1/T=2/T=2.96 err%）+ 通过? 列」，也可能是「stage → 判词 → 判据」三段表。

提取纪律
--------
* **行级定位**：每行都带 ``file:line``（物理行号），评测按行匹配，不做模糊对齐——
  避免「同一报告里两行同量名」被算作命中。
* **零猜测**：列角色只按表头同义词映射；判词只按单元格首 token 归类；数值只走
  ``numeric.py`` 的严格扫描。任何拿不准的单元格都不产出欠账（宁可少报，不臆造）。
* **多误差列展开**：一行带多个误差列（不同检查点/不同网格）时，展开成多条欠账，
  量名 = ``<行标签> @ <列标签>``，这样再派时定位精确到检查点。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import mdtables
from .numeric import ParsedNumber, clean_cell, parse_interval, parse_number_cell, scan_leading_number
from .rootcause import REQUEUE_TEMPLATES, classify
from .schema import DEFAULT_THRESHOLD_PCT, DebtRow, Source

# --------------------------------------------------------------------- 词表
ROLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "metric": (
        "关键量",
        "量",
        "项目",
        "指标",
        "名称",
        "申报",
        "检查项",
        "判据项",
        "quantity",
        "metric",
        "item",
        "check",
        "case",
        "stage",
        "阶段",
        "臂",
        "arm",
        "route",
    ),
    "measured": (
        "实测",
        "计算",
        "复算",
        "数值",
        "本实现",
        "本库",
        "measured",
        "computed",
        "current",
        "fd",
        "lattice",
        "仿",
        "运行",
        "结果",
    ),
    "reference": (
        "基准",
        "参考",
        "解析",
        "解析值",
        "真值",
        "reference",
        "analytic",
        "exact",
        "expected",
        "target",
        "理论",
    ),
    "error": (
        "误差",
        "相对误差",
        "偏差",
        "error",
        "err",
        "err%",
        "相对偏差",
        "relerr",
        "δ",
        "dmax",
    ),
    "threshold": ("门槛", "判据", "标准", "容差", "阈值", "threshold", "criterion", "tolerance", "gate"),
    "verdict": (
        "是否<3%",
        "<3%",
        "<3%?",
        "是否<3",
        "通过?",
        "通过",
        "判定",
        "判词",
        "结论",
        "verdict",
        "status",
        "result",
        "结果?",
    ),
}

VERDICT_PASS = ("pass", "p", "yes", "ok", "true")
# 注意：**不收** ``reject``/``f``/``no`` 这类短 token——``GATE_REJECT``（负控被正确拒绝）
# 与 ``rejected``（薄船门按规则拒收）都不是「精度未达标」，收进来会造成系统性假阳性。
VERDICT_FAIL = ("fail", "false")
VERDICT_WITHHELD = ("withheld", "hold", "pending", "悬置")
SKIP_METRIC = {"", "合计", "总计", "结论", "备注", "—", "-", "n/a", "total"}

# 「判决族」小节标记：**只收真正的判决/误差小节名**。裸的「判据 2」「门」不行——
# 报告里「3.2 Holtrop Ca ↔ ΔCf 换算（判据 2）」这类换算/枚举表会被误当判决表，
# 把「两个换算口径的差」当成精度误差（实测假阳性来源）。
DECISION_FAMILY_CUES = (
    "误差判定",
    "误差",
    "判决",
    "判据对账",
    "验收",
    "复核",
    "conformance",
    "gate",
    "verdict",
    "acceptance",
)

# 负例/预期失败表：门**正确地**拒绝才是通过，不是欠账。
NEGATIVE_CUE_TABLES = (
    "负例",
    "负控",
    "演练矩阵",
    "negative control",
    "expected fail",
    "期望失败",
)
# 明确声明「这不是误差」的行：估计量/口径差/归因唯一。
NOT_AN_ERROR_CUES = (
    "不是误差",
    "非误差",
    "不作为误差",
    "归因唯一",
    "负例",
    "负控",
    "negative control",
)


def is_decision_family(heading: str) -> bool:
    """表格小节是否属于「判决族」（误差判定/判据对账/四门判决/验收门…）。

    Gate 的作用：``pending`` 判词文档里还有大量**扫参/诊断**表（``3.1 Guo 力扫参``），
    其中带误差数字但没有判词列——那是「试过的杠杆」而不是「未过的验收项」，若一并提取
    会把已排除的路径重新派单（重复劳动）。故只从判决族表里取欠账。
    """
    h = heading.casefold()
    return any(cue.casefold() in h for cue in DECISION_FAMILY_CUES)


REPO_NAMES = (
    "TensorLBM",
    "TensorFEM",
    "TensorFVM",
    "TensorBEM",
    "TensorDEM",
    "TensorSPH",
    "TensorDG",
    "TensorCAD",
    "TensorPhysics",
    "TensorPlatform",
    "ship-performance-platform",
)


@dataclass
class ReportMeta:
    """报告级元数据（来自 corpus index.json）。"""

    file: str
    report_id: str = ""
    mission_id: str = ""
    title: str = ""
    scenario_key: str = ""
    node: str = ""
    repo: str = ""
    kind: str = "report"
    benchmark: str = ""
    source_path: str = ""


# ------------------------------------------------------------------ 判词归类
def classify_verdict(cell: str, *, allow_yesno: bool = True) -> str | None:
    """单元格 → PASS/FAIL/WITHHELD；识别不了返回 None（不猜）。

    ``allow_yesno=False`` 用于**行内兜底扫描**：``否/是`` 只有在「判词列」语义明确时才算
    判词，否则「覆盖=否」「稳定=是」这类状态列会被误读成精度未达标（实测过的假阳性来源）。
    """
    s = clean_cell(cell)
    if not s:
        return None
    low = s.casefold()
    head = low.replace("**", "").strip(" :：()（）")
    if "withheld" in low:
        return "WITHHELD"
    if "fail" in low:
        return "FAIL"
    for token in VERDICT_WITHHELD:
        if token in low:
            return "WITHHELD"
    for token in VERDICT_FAIL:
        if token == "no":
            if any(low.startswith(k) for k in ("no", "否")):
                return "FAIL"
            continue
        if token in low and token not in ("p", "f"):
            return "FAIL" if token in ("fail", "false", "reject") else None
    # 注意：「×」不能算 FAIL——``succeeded ×9``/``×3 次`` 里的乘号会把成功计数读成失败。
    if allow_yesno and head.startswith("否"):
        return "FAIL"
    if allow_yesno and head.startswith("是"):
        return "PASS"
    if any(ch in s for ch in ("❌", "✗", "✘")):
        return "FAIL"
    if any(ch in s for ch in ("✅", "✓", "✔")):
        return "PASS"
    if allow_yesno and (head in ("否", "未", "不通过", "未达标") or head.startswith("否")):
        return "FAIL"
    if allow_yesno and (head in ("是", "通过", "达标") or head.startswith("是")):
        return "PASS"
    # 单元格**恰好**是 yes/no（报告里的 ``<3%?`` 列常用英文 YES/NO）——只认整格，不做子串匹配，
    # 避免 ``no value``/``none`` 这类短语被读成判词。
    if allow_yesno and head in ("no", "n", "否"):
        return "FAIL"
    if allow_yesno and head in ("yes", "y", "是"):
        return "PASS"
    for token in VERDICT_PASS:
        if token != "p" and low.startswith(token):
            return "PASS"
    if low in ("p", "pass"):
        return "PASS"
    return None


# ------------------------------------------------------------------ 列角色推断
def _squash(text: str) -> str:
    """去空白/标点后比较——报告表头书写极不一致（``是否 <3%`` / ``是否<3%？`` / ``<3%?``）。"""
    return "".join(ch for ch in text if not ch.isspace())


def _role_of(header: str) -> str | None:
    h = mdtables.normalize_header(header)
    if not h:
        return None
    sq = _squash(h)
    for role, words in ROLE_SYNONYMS.items():
        for word in words:
            if sq == _squash(word):
                return role
    for role, words in ROLE_SYNONYMS.items():
        for word in words:
            if len(word) >= 2 and _squash(word) in sq:
                return role
    return None


def infer_roles(table: mdtables.MTable) -> dict[str, int]:
    roles: dict[str, int] = {}
    for idx, header in enumerate(table.header):
        role = _role_of(header)
        if role is None:
            continue
        if role in roles:
            continue
        roles[role] = idx
    if "metric" not in roles and table.header:
        roles["metric"] = 0
    if "verdict" not in roles:
        # 兜底：找一列「短单元格、多数是判词 token」。要求单元格短（≤24 字符）且命中占比
        # ≥60%——长文本列（判据原文/JSON 证据）里出现 ``honest-FAIL`` 之类的词不是判词。
        for idx in range(len(table.header)):
            if idx == roles.get("metric"):
                continue
            vals = [
                classify_verdict(cells[idx], allow_yesno=False)
                for cells, _ in table.rows
                if idx < len(cells) and len(clean_cell(cells[idx])) <= 24
            ]
            hits = [v for v in vals if v]
            if len(hits) >= 2 and len(hits) >= 0.6 * len(vals):
                roles["verdict"] = idx
                break
    if roles.get("verdict") == roles.get("metric"):
        roles.pop("verdict", None)
    return roles


def error_columns(table: mdtables.MTable, roles: dict[str, int]) -> list[int]:
    """误差列：``error`` 角色 + 表头含 err/误差/偏差 的其余列（多检查点表）。"""
    cols: list[int] = []
    if "error" in roles:
        cols.append(roles["error"])
    for idx, header in enumerate(table.header):
        if idx in cols:
            continue
        h = mdtables.normalize_header(header)
        if any(k in h for k in ("err", "误差", "偏差", "δ", "error")):
            cols.append(idx)
    return cols


def _threshold_from_cell(cell: str) -> float | None:
    parsed = parse_number_cell(cell)
    if parsed is not None and 0 < abs(parsed.value) <= 100:
        return abs(parsed.value)
    # 形如 "≤15%" / "< 20%" / "±5%"：取单元格内第一个百分比数
    lead = scan_leading_number(cell)
    if lead is not None and 0 < abs(lead.value) <= 100:
        return abs(lead.value)
    return None


def _pick(role: str, roles: dict[str, int], cells: list[str]) -> str:
    idx = roles.get(role)
    return cells[idx] if idx is not None and idx < len(cells) else ""


# ------------------------------------------------------------------ 归属推断
def find_node_token(text: str) -> str:
    """从标题/正文找战役节点号（``P21-N03`` / ``IN20-R5``），无正则扫描。"""
    for raw in text.replace("|", " ").replace("/", " ").split():
        token = raw.strip("（）()【】[],:：;；*`")
        parts = token.split("-")
        if len(parts) != 2:
            continue
        head, tail = parts
        if not (2 <= len(head) <= 6 and head.isalnum() and head.upper() == head):
            continue
        if not head[0].isalpha():
            continue
        if not (2 <= len(tail) <= 4 and tail.isalnum() and tail.upper() == tail):
            continue
        if not tail[0].isalpha():
            continue
        return token.upper()
    return ""


def _dominant_repo(title: str, text: str) -> str:
    """仓归属：标题优先，其次全文按出现次数取众数（报告常跨仓，正文出现次数最可靠）。"""
    hit = find_repo(title)
    if hit != "unknown":
        return hit
    counts = {name: text.count(name) for name in REPO_NAMES}
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else find_repo(text[:4000])


def find_repo(*texts: str) -> str:
    for text in texts:
        for repo in REPO_NAMES:
            if repo.casefold() in (text or "").casefold():
                return repo
    return "unknown"


# ------------------------------------------------------------- 行 → DebtRow
def _row_context(table: mdtables.MTable, cells: list[str]) -> str:
    return " | ".join([table.heading, " ".join(cells)])


def _build_row(
    *,
    meta: ReportMeta,
    table: mdtables.MTable,
    cells: list[str],
    line: int,
    metric: str,
    verdict: str,
    error: ParsedNumber | None,
    threshold: float | None,
    measured: ParsedNumber | None,
    reference: ParsedNumber | None,
    repo: str,
    case: str,
    extra_context: str = "",
) -> DebtRow:
    context = _row_context(table, cells) + " " + extra_context
    small = error is not None and 3.0 <= abs(error.as_percent) <= 6.0
    cause = classify(context, verdict=verdict, small_overshoot=small)
    threshold_pct = threshold if threshold is not None else DEFAULT_THRESHOLD_PCT
    lt3 = None if error is None else abs(error.as_percent) < 3.0
    deps: tuple[str, ...] = ()
    return DebtRow(
        repo=repo,
        case=case,
        metric=metric,
        measured=measured.value if measured else None,
        reference=reference.value if reference else None,
        error_pct=None if error is None else round(error.as_percent, 6),
        threshold_pct=threshold_pct,
        lt_3pct=lt3,
        rootcause=cause.label,
        requeue_criterion=REQUEUE_TEMPLATES[cause.label],
        depends_on=deps,
        verdict=verdict,
        source=Source(
            kind=meta.kind,
            file=meta.file,
            line=line,
            report_id=meta.report_id,
            mission_id=meta.mission_id,
            repo=repo if meta.kind != "report" else "",
            benchmark=meta.benchmark,
            node=meta.node or case,
        ),
        evidence=cause.evidence,
        raw={
            "cells": [clean_cell(c)[:80] for c in cells],
            "heading": table.heading,
            "header": table.header,
            "cause_score": cause.score,
            "cause_defaulted": cause.defaulted,
            "threshold_source": "column" if threshold is not None else "default",
        },
    )


def extract_from_markdown(text: str, meta: ReportMeta) -> list[DebtRow]:
    """一份 markdown（报告或判词）→ 欠账行列表。"""
    tables = mdtables.parse_tables(text)
    title_text = " ".join([meta.title, meta.scenario_key, meta.file])
    repo = meta.repo or _dominant_repo(title_text, text)
    # pending 判词的「案例」= benchmark 名（``dam_break_sc``），这样同一 benchmark 的
    # 多份判词文件（m163 / terminal_verdict_report）落成同一 case，跨文件近重可被识别；
    # 报告则用战役节点号（P21-N03/IN20-R5），同一节点的多篇报告视作同一 case。
    case = (
        meta.node
        or find_node_token(title_text)
        or (meta.benchmark if meta.kind == "pending_verdict" else "")
        or Path(meta.file).stem
    )
    rows: list[DebtRow] = []

    for table in tables:
        prev_reference = ""
        roles = infer_roles(table)
        err_cols = error_columns(table, roles)
        family = is_decision_family(table.heading)
        heading_low = table.heading.casefold()
        if any(cue.casefold() in heading_low for cue in NEGATIVE_CUE_TABLES):
            continue
        if not err_cols and "verdict" not in roles:
            continue
        if "verdict" not in roles and not family:
            continue
        for cells, line in table.rows:
            row_text = " ".join(cells)
            if any(cue in row_text for cue in NOT_AN_ERROR_CUES):
                continue
            metric_cell = _pick("metric", roles, cells)
            metric = clean_cell(metric_cell)
            if metric.casefold() in SKIP_METRIC:
                continue
            verdict_cell = _pick("verdict", roles, cells)
            verdict = classify_verdict(verdict_cell)
            if verdict is None:
                # 无判词列（或判词列写作长句）时，退化为「行内有判词 token」——
                # 这是 pending 判词表与 key-value 表的常见形态（``| 项 | ❌ NaN@609步 |``）。
                scored = [
                    (classify_verdict(c, allow_yesno=False), c)
                    for c in cells
                    if c and c != metric_cell and len(clean_cell(c)) <= 64
                ]
                hits = [(v, c) for v, c in scored if v is not None]
                if hits:
                    # FAIL 优先于 PASS（一行里同时出现 ⇔ 该行存在未过项）
                    order = {"FAIL": 0, "WITHHELD": 1, "PASS": 2}
                    verdict, verdict_cell = sorted(hits, key=lambda vc: order[vc[0]])[0]
            threshold = _threshold_from_cell(_pick("threshold", roles, cells))
            measured = parse_number_cell(_pick("measured", roles, cells))
            ref_cell = _pick("reference", roles, cells)
            reference = parse_number_cell(ref_cell)
            ref_text = clean_cell(ref_cell)
            # 「同上」是报告里的常见缩写：把上一行基准单元格并入本行上下文，否则本行
            # 会丢掉「Chuang 型参考（二次引用 ±33%）」这类决定性根因线索（r6242:213/214）。
            if ref_text.startswith("同上"):
                extra_context = prev_reference
            else:
                extra_context = ""
                prev_reference = ref_text
            errors = []
            for idx in err_cols:
                if idx >= len(cells):
                    continue
                parsed = scan_leading_number(cells[idx])
                if parsed is not None:
                    errors.append((table.header[idx], parsed))
            head = " | ".join([table.heading, " | ".join(cells)])
            has_band = parse_interval(head) is not None

            if verdict is None:
                # 无判词列：只按数值门槛判（>门槛 → FAIL），并跳过无误差的行
                if not errors:
                    continue
                worst = max(errors, key=lambda e: abs(e[1].as_percent))
                if abs(worst[1].as_percent) <= (threshold or DEFAULT_THRESHOLD_PCT):
                    continue
                verdict = "FAIL"
            else:
                if verdict == "PASS":
                    continue
                if verdict == "WITHHELD" and not errors and not metric:
                    continue

            if verdict == "WITHHELD" and not errors:
                rows.append(
                    _build_row(
                        meta=meta,
                        table=table,
                        cells=cells,
                        line=line,
                        metric=metric,
                        verdict=verdict,
                        error=None,
                        threshold=threshold,
                        measured=measured,
                        reference=reference,
                        repo=repo,
                        case=case,
                        extra_context=extra_context,
                    )
                )
                continue

            if not errors:
                # FAIL 但没有可解析误差：仍记欠账（判词本身就是证据），实测/误差留空
                rows.append(
                    _build_row(
                        meta=meta,
                        table=table,
                        cells=cells,
                        line=line,
                        metric=metric,
                        verdict=verdict,
                        error=None,
                        threshold=threshold,
                        measured=measured,
                        reference=reference,
                        repo=repo,
                        case=case,
                        extra_context=extra_context,
                    )
                )
                continue

            multi = len(errors) > 1
            for col_name, parsed in errors:
                over = abs(parsed.as_percent) > (threshold or DEFAULT_THRESHOLD_PCT)
                # 只有当**表里明写了门槛**、且本列误差在门槛内时，才认为这一列不构成欠账；
                # 门槛缺失时不许用默认 3% 去否决作者写下的 FAIL（作者可能用的是 15%/1.48 pp 判据）。
                if verdict == "FAIL" and threshold is not None and not over and not has_band:
                    continue
                label = f"{metric} @ {clean_cell(col_name)}" if multi else metric
                rows.append(
                    _build_row(
                        meta=meta,
                        table=table,
                        cells=cells,
                        line=line,
                        metric=label,
                        verdict=verdict,
                        error=parsed,
                        threshold=threshold,
                        measured=measured,
                        reference=reference,
                        repo=repo,
                        case=case,
                        extra_context=extra_context,
                    )
                )
    return [r for r in rows if r.metric.strip()]


# ------------------------------------------------------------------ 语料遍历
def load_index(corpus_dir: str | Path) -> dict[str, list[dict]]:
    path = Path(corpus_dir) / "index.json"
    if not path.is_file():
        return {"reports": [], "pending": []}
    return json.loads(path.read_text(encoding="utf-8"))


def iter_corpus(corpus_dir: str | Path) -> Iterable[tuple[Path, ReportMeta]]:
    """按 index.json 遍历语料；缺 index 时按目录约定兜底。"""
    corpus = Path(corpus_dir)
    index = load_index(corpus)
    seen: set[str] = set()
    for rec in index.get("reports", []):
        p = corpus / "reports" / rec["file"]
        if not p.is_file():
            continue
        seen.add(rec["file"])
        yield p, ReportMeta(
            file=f"reports/{rec['file']}",
            report_id=str(rec.get("report_id") or ""),
            mission_id=str(rec.get("mission_id") or ""),
            title=str(rec.get("title") or ""),
            scenario_key=str(rec.get("scenario_key") or ""),
            kind="report",
        )
    for rec in index.get("pending", []):
        p = corpus / "pending" / rec["file"]
        if not p.is_file():
            continue
        seen.add(rec["file"])
        yield p, ReportMeta(
            file=f"pending/{rec['file']}",
            repo=str(rec.get("repo") or ""),
            benchmark=str(rec.get("benchmark") or ""),
            kind="pending_verdict",
            source_path=str(rec.get("source_path") or ""),
            title=str(rec.get("benchmark") or ""),
        )
    # 兜底：未入索引的文件（例如手工追加的语料）
    for sub, kind in (("reports", "report"), ("pending", "pending_verdict")):
        d = corpus / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name in seen:
                continue
            yield p, ReportMeta(file=f"{sub}/{p.name}", kind=kind, repo=find_repo(p.name))


def extract_corpus(corpus_dir: str | Path) -> list[DebtRow]:
    rows: list[DebtRow] = []
    for path, meta in iter_corpus(corpus_dir):
        text = path.read_text(encoding="utf-8", errors="replace")
        rows.extend(extract_from_markdown(text, meta))
    for row in rows:
        row.validate()
    return rows