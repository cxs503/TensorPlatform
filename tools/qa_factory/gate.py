#!/usr/bin/env python3
"""QA 产线 · 程序化三查机器门（FEAT-QA-FACTORY 第 1 轮）。

设计判据全部预注册于 docs/qa_factory/PREREG_QA_FACTORY.md（判据先于动手，阈值不事后调）。

三查：
  A 结构完整      —— 技术报告复用 tools/report_corpus_audit 六要素确定性判定；meta（质检元报告）
                     改用「判定表存在」形态判定。
  B 证据指针可解析 —— 正文里的工件/报告/工作单指针逐条判 resolved / contradicted / unverifiable；
                     **根不可用不算失败**（老 worktree 已回收，unverifiable 单列，不冤枉报告）。
  C 关键数字可复算 —— 精度承载数字是否在同行 ±2 行内绑定溯源线索或显式「不可复算」声明。

分档：任一 fail → suspect；无 fail 有 flag → machine-flag；三项全 ok → pass。
纯函数（输入 markdown + 可选根解析器），确定性：同一输入任意次运行同判定。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# 复用仓内既有确定性六要素审计（不重复造轮子）
try:  # pragma: no cover - 由测试触发导入路径差异
    from tools.report_corpus_audit import analyze_report, classify_report_kind
except Exception:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.report_corpus_audit import analyze_report, classify_report_kind

TIERS = ("pass", "machine-flag", "suspect")

# --------------------------------------------------------------------------- #
# 1. 指针抽取
# --------------------------------------------------------------------------- #

META_SCENARIOS = frozenset({"report-qa", "qa-review"})

_P_REL_PATH = re.compile(
    r"(?<![\w/.-])((?:artifacts|docs|runs|tools|src|tests|scripts|references|schemas)"
    r"/[A-Za-z0-9_][A-Za-z0-9_./\-]{2,120})")
_P_ABS_WORK = re.compile(r"(/root/tp-agent/work/m\d+/[A-Za-z0-9_][A-Za-z0-9_./\-]{1,120})")
_P_REPORT_REF = re.compile(r"(?:报告|report)\s*#\s*(\d{1,7})|/tensor/reports/(\d{1,7})"
                           r"|report_id[\"'\s:=]+(\d{1,7})")
_P_MISSION_REF = re.compile(r"(?:mission|工作单|任务单)\s*#?\s*(\d{2,7})"
                            r"|mission_id[\"'\s:=]+(\d{2,7})"
                            r"|(?<![\w/.-])m(\d{2,6})(?![\w])")

_NO_ARTIFACT_DECL = re.compile(
    r"(无工件|工件缺失|工件不存在|无独立工件|工件已回收|artifact[- ]free|no artifacts?"
    r"|工件随工作树回收|不可复算|无法复算)", re.IGNORECASE)


@dataclass
class Pointer:
    kind: str            # path | abs_path | report | mission
    raw: str
    line: int
    status: str = "unverifiable"   # resolved | contradicted | unverifiable
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_pointers(markdown: str) -> List[Pointer]:
    out: List[Pointer] = []
    seen = set()
    for rx, kind in ((_P_ABS_WORK, "abs_path"), (_P_REL_PATH, "path")):
        for m in rx.finditer(markdown):
            raw = m.group(1).rstrip(".,;:)]}`'\"")
            key = (kind, raw)
            if key in seen:
                continue
            seen.add(key)
            out.append(Pointer(kind=kind, raw=raw, line=_line_of(markdown, m.start())))
    for m in _P_REPORT_REF.finditer(markdown):
        val = next(g for g in m.groups() if g)
        key = ("report", val)
        if key in seen:
            continue
        seen.add(key)
        out.append(Pointer(kind="report", raw=val, line=_line_of(markdown, m.start())))
    for m in _P_MISSION_REF.finditer(markdown):
        val = next(g for g in m.groups() if g)
        key = ("mission", val)
        if key in seen:
            continue
        seen.add(key)
        out.append(Pointer(kind="mission", raw=val, line=_line_of(markdown, m.start())))
    return out


# --------------------------------------------------------------------------- #
# 2. 数字与溯源线索
# --------------------------------------------------------------------------- #

_NUM = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s?%?")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_PATHISH = re.compile(r"(?:m\d{2,6}|#\d+|v\d+\.\d+|L\d+)")

_CUE = re.compile(
    r"(artifacts?/|docs/|runs/|\.json\b|\.csv\b|\.npy\b|\.png\b|\.md\b|\.log\b|\.py\b"
    r"|\bL\d+\b|:\d+\b|psql|\bsql\b|复算|重算|recompute|derived|程序化|注入|sha256|sha-?256"
    r"|DOI|doi:|文献|et al|Table\s*\d|表\s*\d|参考值|解析解|analytic|baseline|基线"
    r"|不可复算|无法复算|手抄|人工输入|估计值|假设值|assumed|来源[:：]|source[:：])",
    re.IGNORECASE)


def _significant(tok: str) -> bool:
    """精度承载：小数 ≥3 位有效数字、科学计数、或百分比。"""
    t = tok.strip()
    if t.endswith("%"):
        return True
    if re.search(r"[eE][+-]?\d+", t):
        return True
    if "." in t:
        digits = re.sub(r"[^0-9]", "", t.split(".")[1])
        return len(digits) >= 2 or len(re.sub(r"[^0-9]", "", t)) >= 3
    return False


def mask_pointers(markdown: str) -> str:
    """把路径/引用/代码标识替换为等长空白，避免把 m1122 / L12 / v0.5.0 当数字算。"""
    md = markdown
    for rx in (_P_ABS_WORK, _P_REL_PATH):
        md = rx.sub(lambda m: " " * len(m.group(0)), md)
    md = _PATHISH.sub(lambda m: " " * len(m.group(0)), md)
    return md


_CRITERION_PREFIX = re.compile(r"(?:[<≤>≥~±]|小于|大于|不超过|不低于|低于|高于|至少|至多)\s*$")


def extract_key_numbers(markdown: str, limit: int = 400) -> List[Tuple[str, int]]:
    """抽取「精度承载数字」= 复算对象。

    排除：路径/引用/年份（mask 后仍可能残留的），以及**以判据形式出现的阈值**
    （`<3%`、`≤ 0.05`、`小于 1e-6`）——判据常量不是被复算的测量值，要求它溯源是噪声。
    """
    md = mask_pointers(markdown)
    out: List[Tuple[str, int]] = []
    for m in _NUM.finditer(md):
        tok = m.group(0).strip()
        core = tok.rstrip("%").strip()
        if _YEAR.match(core):
            continue
        if not _significant(tok):
            continue
        prefix = md[max(0, m.start() - 6):m.start()]
        if _CRITERION_PREFIX.search(prefix):
            continue
        out.append((tok, _line_of(md, m.start())))
        if len(out) >= limit:
            break
    return out


def _table_block(lines: Sequence[str], line: int) -> Tuple[int, int]:
    """若该行属于 markdown 表格块，返回块的行区间 [lo, hi)（0-based），否则 (line-1, line)。"""
    i = line - 1
    if not (0 <= i < len(lines)):
        return (i, i + 1)
    if not lines[i].lstrip().startswith("|"):
        return (i, i + 1)
    lo, hi = i, i + 1
    while lo - 1 >= 0 and lines[lo - 1].lstrip().startswith("|"):
        lo -= 1
    while hi < len(lines) and lines[hi].lstrip().startswith("|"):
        hi += 1
    return (lo, hi)


def _section_block(lines: Sequence[str], line: int) -> Tuple[int, int]:
    """该行所属 markdown 章节的行区间（到上下同级或更高级标题为止）。"""
    i = line - 1
    head = re.compile(r"^#{1,6}\s")
    lo = i
    while lo - 1 >= 0 and not head.match(lines[lo - 1]):
        lo -= 1
    hi = i + 1
    while hi < len(lines) and not head.match(lines[hi]):
        hi += 1
    return (lo, hi)


def binding_level(markdown: str, token: str, line: int, window: int = 2) -> Optional[str]:
    """返回绑定层级：'strong'（同行±2 行有线索）| 'table'（所在表格块有线索）|
    'section'（所在章节有溯源声明）| None（未绑定）。

    设计意图：关键数字必须能绑定到出处。出处可以在同一行/邻行（最强），
    也可以在表格表头（`| 计算值 | 参考值 |`），或该章节开头的溯源声明
    （「以下数值由 artifacts/x.json 程序化注入」）。仅把 ±2 行当唯一窗口会把
    「表头即出处」的合法数值表误判为未绑定——这是实现缺陷，已按预注册「调整记录」修正。
    """
    lines = markdown.splitlines()
    lo, hi = max(0, line - 1 - window), min(len(lines), line + window)
    if _CUE.search("\n".join(lines[lo:hi])):
        return "strong"
    tlo, thi = _table_block(lines, line)
    if thi - tlo > 1 and _CUE.search("\n".join(lines[tlo:thi])):
        return "table"
    slo, shi = _section_block(lines, line)
    ctx = "\n".join(lines[slo:shi])
    if len(ctx) <= 8000 and _CUE.search(ctx[:1500]):
        return "section"
    return None


def is_bound(markdown: str, token: str, line: int, window: int = 2) -> bool:
    return binding_level(markdown, token, line, window) is not None


# --------------------------------------------------------------------------- #
# 3. 根解析器（可注入，便于测试；生产用 worktree + PG）
# --------------------------------------------------------------------------- #

@dataclass
class EvidenceRoots:
    """指针可判性上下文。

    work_dir: 该报告所属 mission 的 worktree（存在才可判相对路径）
    repo_roots: 额外的绝对仓根（如平台主树），按序尝试
    report_exists / mission_exists: 可注入的存在性函数（生产绑 PG）
    """
    work_dir: Optional[Path] = None
    repo_roots: Sequence[Path] = ()
    report_exists: Optional[Callable[[int], bool]] = None
    mission_exists: Optional[Callable[[int], bool]] = None

    def root_available(self) -> bool:
        return bool(self.work_dir and self.work_dir.is_dir()) or bool(self.repo_roots)


def _exists_any(roots: Iterable[Path], rel: str) -> Optional[bool]:
    """返回 True=命中, False=有根但都不命中, None=无根可判。"""
    any_root = False
    for r in roots:
        if not r or not Path(r).is_dir():
            continue
        any_root = True
        cand = Path(r) / rel
        try:
            if cand.exists():
                return True
            if "*" in rel and list(Path(r).glob(rel)):
                return True
        except OSError:
            continue
    return False if any_root else None


def resolve_pointer(p: Pointer, roots: EvidenceRoots) -> Pointer:
    if p.kind == "report":
        ok = roots.report_exists(int(p.raw)) if roots.report_exists else None
        p.status = {True: "resolved", False: "contradicted", None: "unverifiable"}[ok]
        p.detail = "tensor_reports.id"
        return p
    if p.kind == "mission":
        ok = roots.mission_exists(int(p.raw)) if roots.mission_exists else None
        p.status = {True: "resolved", False: "contradicted", None: "unverifiable"}[ok]
        p.detail = "tensor_dev_missions.id"
        return p
    if p.kind == "abs_path":
        path = Path(p.raw)
        # 绝对路径自带根：其祖先 worktree 目录存在即可判
        base = Path("/root/tp-agent/work")
        m = re.match(r"/root/tp-agent/work/(m\d+)/", p.raw)
        if m and (base / m.group(1)).is_dir():
            p.status = "resolved" if path.exists() else "contradicted"
            p.detail = f"worktree {m.group(1)}"
        else:
            p.status = "unverifiable"
            p.detail = "worktree 已回收"
        return p
    # 相对路径
    roots_list = [r for r in ([roots.work_dir] if roots.work_dir else []) + list(roots.repo_roots) if r]
    res = _exists_any(roots_list, p.raw)
    p.status = {True: "resolved", False: "contradicted", None: "unverifiable"}[res]
    p.detail = "worktree" if (res is not None and roots.work_dir and roots.work_dir.is_dir()
                              and any((Path(roots.work_dir) / p.raw).parent.parent.exists()
                                      for _ in [0])) else ("repo" if res is not None else "无根可判")
    return p


# --------------------------------------------------------------------------- #
# 4. 三查
# --------------------------------------------------------------------------- #

@dataclass
class CheckResult:
    status: str                 # ok | flag | fail
    reason: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)


def check_structure(markdown: str, *, title: str = "", scenario_key: str = "",
                    figures: int = 0, kind: Optional[str] = None) -> CheckResult:
    md = markdown or ""
    kind = kind or classify_report_kind(title, scenario_key or "")
    if kind == "meta":
        has_table = bool(re.search(r"^\|.*\b(PASS|REJECT|RESUBMIT|MINOR|通过|退回|不合格)\b.*\|",
                                   md, re.IGNORECASE | re.MULTILINE))
        thin = len(md) < 600
        if not has_table:
            return CheckResult("fail", "meta_no_verdict_table",
                               {"chars": len(md), "verdict_table": False})
        return CheckResult("ok" if not thin else "flag",
                           "meta_ok" if not thin else "meta_thin",
                           {"chars": len(md), "verdict_table": True})
    v = analyze_report(md, title=title, scenario_key=scenario_key or "", figures=figures)
    verdict = v.verdict
    status = {"PASS": "ok", "MINOR": "flag", "RESUBMIT": "fail"}[verdict]
    defect_codes = sorted({d.code for d in v.defects})
    return CheckResult(status, f"corpus_audit:{verdict}",
                       {"six_element_complete": v.six_element_complete,
                        "elements": dict(v.elements),
                        "numeric_tables": v.numeric_tables,
                        "results_numeric_tables": v.results_numeric_tables,
                        "error_verdict": v.error_verdict,
                        "defects": defect_codes},
                       [f"{d.code} L{d.line}: {d.detail}" for d in v.defects][:8])


def check_pointers(markdown: str, roots: EvidenceRoots) -> Tuple[CheckResult, List[Pointer]]:
    md = markdown or ""
    ptrs = [resolve_pointer(p, roots) for p in extract_pointers(md)]
    n_res = sum(1 for p in ptrs if p.status == "resolved")
    n_con = sum(1 for p in ptrs if p.status == "contradicted")
    n_unv = sum(1 for p in ptrs if p.status == "unverifiable")
    denom = n_res + n_con
    declared = bool(_NO_ARTIFACT_DECL.search(md))
    ratio = (n_con / denom) if denom else None
    metrics = {"pointers": len(ptrs), "resolved": n_res, "contradicted": n_con,
               "unverifiable": n_unv, "contradicted_ratio": ratio,
               "declared_no_artifacts": declared}
    ev = [f"{p.kind}:{p.raw}@{p.line}->{p.status}" for p in ptrs if p.status != "resolved"][:8]
    if denom == 0:
        if declared:
            return CheckResult("ok", "no_checkable_pointer_declared", metrics, ev), ptrs
        if ptrs:
            return CheckResult("flag", "no_checkable_pointer_root_unavailable", metrics, ev), ptrs
        return CheckResult("flag", "no_pointer_at_all", metrics, ev), ptrs
    if ratio is not None and ratio >= 0.5:
        return CheckResult("fail", "pointer_mostly_contradicted", metrics, ev), ptrs
    if n_con > 0:
        return CheckResult("flag", "pointer_partly_contradicted", metrics, ev), ptrs
    return CheckResult("ok", "pointers_resolved", metrics, ev), ptrs


def check_numbers(markdown: str, *, kind: str = "technical") -> CheckResult:
    md = markdown or ""
    if kind == "meta":
        has_table = bool(re.search(r"^\|.*\b(PASS|REJECT|RESUBMIT|MINOR|通过|退回|不合格)\b.*\|",
                                   md, re.IGNORECASE | re.MULTILINE))
        return CheckResult("ok" if has_table else "flag",
                           "meta_verdict_counts" if has_table else "meta_no_verdict_table",
                           {"mode": "meta"})
    nums = extract_key_numbers(md)
    if not nums:
        return CheckResult("fail", "no_numeric_claim", {"key_numbers": 0, "bound": 0})
    levels = [binding_level(md, t, ln) for t, ln in nums]
    bound = [t for t, lv in zip((t for t, _ in nums), levels) if lv]
    ratio = len(bound) / len(nums)
    declared = bool(_NO_ARTIFACT_DECL.search(md))
    metrics = {"key_numbers": len(nums), "bound": len(bound), "bound_ratio": round(ratio, 4),
               "bound_by_level": {(lv or "unbound"): sum(1 for x in levels if x == lv)
                                  for lv in ("strong", "table", "section", None)},
               "declared_nonrecomputable": declared}
    if ratio < 0.2 and not declared:
        return CheckResult("fail", "numbers_unbound", metrics,
                           [f"unbound e.g. {t!r}@L{ln}" for (t, ln), lv in zip(nums[:5], levels[:5]) if lv is None] or [f"unbound e.g. {nums[0][0]!r}@L{nums[0][1]}"])
    if ratio < 0.6:
        return CheckResult("flag", "numbers_partly_bound", metrics,
                           [f"unbound e.g. {t!r}@L{ln}" for t, ln in nums[:5]])
    return CheckResult("ok", "numbers_bound", metrics)


# --------------------------------------------------------------------------- #
# 5. 分档
# --------------------------------------------------------------------------- #

@dataclass
class GateVerdict:
    report_id: Optional[int]
    tier: str
    checks: Dict[str, Dict[str, Any]]
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def decide_tier(structure: CheckResult, pointers: CheckResult, numbers: CheckResult) -> str:
    statuses = [structure.status, pointers.status, numbers.status]
    if "fail" in statuses:
        return "suspect"
    if "flag" in statuses:
        return "machine-flag"
    return "pass"


def gate_report(*, report_id: Optional[int], markdown: str, title: str = "",
                scenario_key: str = "", figures: int = 0, roots: Optional[EvidenceRoots] = None,
                ) -> GateVerdict:
    md = markdown or ""
    kind = classify_report_kind(title, scenario_key or "")
    a = check_structure(md, title=title, scenario_key=scenario_key, figures=figures, kind=kind)
    b, ptrs = check_pointers(md, roots or EvidenceRoots())
    c = check_numbers(md, kind=kind)
    tier = decide_tier(a, b, c)
    return GateVerdict(
        report_id=report_id, tier=tier,
        checks={"structure": asdict(a), "pointers": asdict(b), "numbers": asdict(c)},
        detail={"kind": kind, "markdown_chars": len(md), "figures": figures,
                "contradicted_pointers": [p.to_dict() for p in ptrs
                                          if p.status == "contradicted"][:10],
                "gate_version": GATE_VERSION},
    )


GATE_VERSION = "qa-gate/1"


def corpus_sha256(rows: Sequence[Dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(str(r.get("id")).encode())
        h.update(b"\x00")
        h.update((r.get("markdown") or "").encode())
        h.update(b"\x00")
    return h.hexdigest()


def summarize(verdicts: Sequence[GateVerdict]) -> Dict[str, Any]:
    """分档分布 + 各查失败/告警原因频次（决定后续优化的靶子）。"""
    tiers: Dict[str, int] = {t: 0 for t in TIERS}
    reasons: Dict[str, int] = {}
    by_kind: Dict[str, Dict[str, int]] = {}
    for v in verdicts:
        tiers[v.tier] = tiers.get(v.tier, 0) + 1
        k = v.detail.get("kind", "technical")
        by_kind.setdefault(k, {t: 0 for t in TIERS})[v.tier] += 1
        for name, chk in v.checks.items():
            if chk["status"] != "ok":
                key = f"{name}:{chk['reason']}"
                reasons[key] = reasons.get(key, 0) + 1
    total = len(verdicts) or 1
    return {"total": len(verdicts), "tiers": tiers,
            "tier_rate": {t: round(n / total, 4) for t, n in tiers.items()},
            "by_kind": by_kind,
            "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
            "gate_version": GATE_VERSION}