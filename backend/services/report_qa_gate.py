"""报告 QA 常设门 v2（owner 2026-11-11 FEAT-TK-GOV-INFRA 令）——四查 + S1/S2/S3 分级。

为什么单独成模块
----------------
与 ``report_gate.py`` 同纪律：纯函数、无 FastAPI/DB 依赖，生产 API 与 QA 守护
import 同一份实现，离线可复算。六要素判据**复用** ``report_gate.check_report_quality``
（唯一实现，禁止再 fork——见 ``backend/tests/test_report_gate_validation.py``
的防 fork 守卫）。

四查（owner 2026-11-11 原文判据）
--------------------------------
① 题目-证据-锚点-增量对位（证据先行门，复用论文线 GATES_V3 判据）：
   六要素全过 + 正文含 ≥1 证据锚点（报告 id / 工件路径 / git commit / 具名基准）
   + 题目关键词与正文对位（题目承诺的内容正文必须兑现）。
② 数据可溯（result 复核 API 链）：
   正文引用的 tensor_reports id 必须经 API 链复核存在（GET /tensor/reports/{id}）；
   工件路径必须存在于仓检出；git commit 必须可解析。零锚点 = 数据不可溯。
③ S1/S2/S3 分级判据写 grade_reason：
   S3 = 外部锚（具名外部定量基准：ITTC/SIMMAN/DTRC/ISO/IACS/经典解析/已发表
        benchmark）∧ 可复现（≥1 个可复核工件路径或 commit）∧ 对仓 prior art
        有增量（新增/改进/首次/相对基线的定量 delta）；
   S2 = 有内部验证（解析解/内部基线/参考报告对位）但缺外部锚或缺增量；
   S1 = 六要素未过或无任何验证/证据。
   零 S3+ 是 owner 点名的病：分级必须如实给 S3，不许一律压 S1/S2。
④ 拒绝理由结构化落 qa_rejection_reason：
   JSON ``{"first_cause": ..., "failed_checks": [...], "grade": ..., "detail": {...}}``。

verify 注入
----------
``qa_gate_v2(report, verify)`` 的 ``verify`` 是三个回调：
``report_id(int)->bool``（API 链）、``path(str)->bool``（fs）、``commit(str)->bool``（git）。
单测用假 verify；守护进程注入真 API/fs/git 复核。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

from .report_gate import NAMED_REFERENCE_PATTERNS, check_report_quality

# ── 证据锚点抽取 ─────────────────────────────────────────────────────────────
# 报告 id 引用：report #1234 / tensor_reports id=1234 / 报告 1234
_REPORT_ID_PATTERNS = [
    re.compile(r"(?i)\breport[s]?\s*#?\s*(\d{2,6})\b"),
    re.compile(r"(?i)\btensor_reports\s*(?:id\s*)?[=:]?\s*(\d{2,6})\b"),
    re.compile(r"报告\s*#?\s*(\d{2,6})\b"),
]
# 工件路径：仓内相对路径（runs/ artifacts/ docs/ scripts/ tests/ ...）
_ARTIFACT_PATH = re.compile(
    r"\b((?:runs|artifacts|docs|scripts|tests|benchmarks|data|examples|tools|ops)"
    r"(?:/[A-Za-z0-9_\-\.]+)+/?)"
)
# git commit：40 位 hex，或 commit/sha 前缀引导的 7-12 位 hex（裸短 hex 会误伤数据值）
_COMMIT_PATTERNS = [
    re.compile(r"\b[a-f0-9]{40}\b"),
    re.compile(r"(?i)\b(?:commit|sha|sha256?|rev)\s*[:=]?\s*([a-f0-9]{7,12})\b"),
]

#: 外部定量锚（S3 判据①）：具名外部基准/规范/经典文献。
#: 与 report_gate.NAMED_REFERENCE_PATTERNS 的区别：这里只收**外部可识别**的
#: 定量锚（内部报告 id、内部基线不算外部锚）。
EXTERNAL_ANCHOR_PATTERNS = [
    r"\bITTC\d*",
    r"\bSIMMAN\d*",
    r"\bDTRC\b",
    r"\bISO\s*\d+",
    r"\bIACS\b",
    r"\bHoltrop\b",
    r"\bMennen\b",
    r"\bWigley\b",
    r"\bKCS\b",
    r"\bKVLCC\b",
    r"\bJHS\b",
    r"\bTimoshenko\b",
    r"\bPaik\b",
    r"\bFaulkner\b",
    r"\bStokes\b",
    r"\bNavier\b",
    r"\bPoiseuille\b",
    r"\bLaplace\b",
    r"\bFroude\b",
    r"\bReynolds\b",
    r"\bMorrison\b",
    r"\bMorison\b",
    r"\bDarcy\b",
    r"\bWeisbach\b",
    r"\bBernoulli\b",
    r"\bCauchy\b",
    r"\bHooke\b",
    r"\bFourier\b",
    r"\bMonte\s*Carlo\b",
    r"\bGauss[- ]Legendre\b",
    r"\bTaylor\b",
    r"\bLagrange\b",
    r"\bNewton[- ]Raphson\b",
    r"\bGauss[- ]Seidel\b",
    r"\bJacobi\b",
    r"\bConjugate\s*Gradient\b",
    r"\bRANS\b",
    r"\bLES\b",
    r"\bDNS\b",
    r"\bATB\b",
    r"\bNACA\b",
    r"\bEuler\b",
    r"\bMorrison\b",
]

#: 增量声明（S3 判据③）：对 prior art 的增量——新增/改进/首次/定量 delta。
INCREMENT_PATTERNS = [
    r"新增",
    r"首次",
    r"改进",
    r"突破",
    r"修复",
    r"修正",
    r"扩展",
    r"提升",
    r"降低",
    r"缩短",
    r"加速",
    r"(?i)\bnew\b",
    r"(?i)\bfirst[- ]time",
    r"(?i)\bimprove[ds]?",
    r"(?i)\bfix(es|ed)?\b",
    r"(?i)\bextend[se]d?\b",
    r"(?i)\breduce[ds]?",
    r"(?i)\bspeed[- ]?up",
    r"(?i)\bdelta\b",
    r"相对基线",
    r"较基线",
    r"对比基线",
    r"较此前",
    r"较上一",
    r"相比此前",
]

#: 题目对位：题目里的显著词（≥2 字符英文词 / CJK 双字词）在正文的命中率阈值。
TITLE_ALIGN_MIN = 0.5
_TITLE_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "with", "and",
    "vs", "via", "per", "from", "into", "that", "this", "its", "are", "was",
    "the", "be", "to", "of", "in", "on", "at", "by", "as", "is", "it",
    "XVAL", "CASE", "GAP", "RCP", "PR",
}


def _title_tokens(title: str) -> list[str]:
    """题目显著词：英文词（≥3 字符，去停用词）+ CJK 双字滑窗。"""
    toks: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", title or ""):
        if w.lower() not in _TITLE_STOP:
            toks.append(w)
    cjk = re.findall(r"[\u4e00-\u9fff]+", title or "")
    for run in cjk:
        for k in range(len(run) - 1):
            toks.append(run[k:k + 2])
    return toks


def extract_anchors(md: str) -> dict[str, list]:
    """抽取正文证据锚点：报告 id / 工件路径 / git commit / 具名基准。"""
    md = md or ""
    rids: list[int] = []
    for pat in _REPORT_ID_PATTERNS:
        for m in pat.finditer(md):
            v = int(m.group(1))
            if v not in rids:
                rids.append(v)
    paths: list[str] = []
    for m in _ARTIFACT_PATH.finditer(md):
        p = m.group(1).rstrip(".,;:)]}>'\"")
        if p not in paths:
            paths.append(p)
    commits: list[str] = []
    for pat in _COMMIT_PATTERNS:
        for m in pat.finditer(md):
            c = m.group(1) if pat.groups else m.group(0)
            if c not in commits:
                commits.append(c)
    named = [p for p in NAMED_REFERENCE_PATTERNS if re.search(p, md)]
    ext = [p for p in EXTERNAL_ANCHOR_PATTERNS if re.search(p, md)]
    return {
        "report_ids": rids,
        "artifact_paths": paths,
        "commits": commits,
        "named_refs": named,
        "external_anchors": ext,
    }


def title_alignment(title: str, md: str) -> tuple[bool, list[str]]:
    """题目-正文对位：题目显著词 ≥50% 在正文出现（题目承诺正文必须兑现）。

    短题目（<3 个显著词）放宽为 ≥1 命中即可——避免「XVAL-XXX」这类
    纯代号题目被双字滑窗误伤。
    """
    toks = _title_tokens(title)
    if not toks:
        return True, []
    md_l = (md or "").lower()
    hit = [t for t in set(toks) if t.lower() in md_l]
    need = max(1, int(len(set(toks)) * TITLE_ALIGN_MIN))
    return len(hit) >= need, [t for t in set(toks) if t.lower() not in md_l]


def check_evidence_first(title: str, md: str, figures: list) -> dict:
    """查①：题目-证据-锚点-增量对位（证据先行门，GATES_V3 判据）。

    = 六要素全过（复用 report_gate 唯一实现）+ ≥1 证据锚点 + 题目对位。
    """
    base = check_report_quality(md, figures)
    anchors = extract_anchors(md)
    has_anchor = bool(
        anchors["report_ids"] or anchors["artifact_paths"]
        or anchors["commits"] or anchors["named_refs"]
    )
    align_ok, missing = title_alignment(title, md)
    issues = list(base["issue_codes"])
    if not has_anchor:
        issues.append("no_evidence_anchor")
    if not align_ok:
        issues.append("title_misaligned")
    return {
        "ok": not issues,
        "issues": issues,
        "six_element": base,
        "anchors": anchors,
        "title_missing": missing,
    }


def check_traceability(anchors: dict, verify: Mapping[str, Callable]) -> dict:
    """查②：数据可溯（result 复核 API 链）。

    引用的报告 id 经 verify['report_id']（API 链）复核存在；工件路径经
    verify['path']（fs）复核；commit 经 verify['commit']（git）复核。
    零锚点 = 不可溯；任一引用悬空 = 不可溯。
    """
    detail: dict[str, Any] = {"report_ids": {}, "paths": {}, "commits": {}}
    problems: list[str] = []
    for rid in anchors.get("report_ids", []):
        ok = bool(verify.get("report_id") and verify["report_id"](rid))
        detail["report_ids"][str(rid)] = ok
        if not ok:
            problems.append(f"report#{rid} 不存在")
    for p in anchors.get("artifact_paths", []):
        ok = bool(verify.get("path") and verify["path"](p))
        detail["paths"][p] = ok
        if not ok:
            problems.append(f"工件 {p} 不存在")
    for c in anchors.get("commits", []):
        ok = bool(verify.get("commit") and verify["commit"](c))
        detail["commits"][c] = ok
        if not ok:
            problems.append(f"commit {c[:12]} 不可解析")
    ok = bool(anchors.get("report_ids") or anchors.get("artifact_paths")
              or anchors.get("commits") or anchors.get("named_refs")) and not problems
    return {"ok": ok, "detail": detail, "problems": problems}


def grade_report(md: str, anchors: dict, six: dict) -> tuple[str, str]:
    """查③：S1/S2/S3 分级（判据写 grade_reason）。

    S3 = 外部锚 ∧ 可复现 ∧ 对 prior art 有增量（owner 2026-11-11 判据）。
    S2 = 有内部验证（六要素 validation 过）但缺外部锚或缺增量。
    S1 = 六要素未过或无任何验证/证据。
    """
    md = md or ""
    ext = bool(anchors.get("external_anchors"))
    repro = bool(anchors.get("artifact_paths") or anchors.get("commits"))
    incr = any(re.search(p, md) for p in INCREMENT_PATTERNS)
    validation_ok = bool(six.get("checks", {}).get("validation"))
    six_ok = bool(six.get("passed"))

    if six_ok and ext and repro and incr:
        return "S3", (
            "S3: 外部锚=" + ",".join(anchors["external_anchors"][:3])
            + " | 可复现=" + (anchors["artifact_paths"][:1] or anchors["commits"][:1])[0]
            + " | 增量声明命中"
        )
    if six_ok and validation_ok:
        miss = []
        if not ext:
            miss.append("缺外部锚")
        if not repro:
            miss.append("缺可复现工件")
        if not incr:
            miss.append("缺增量声明")
        return "S2", "S2: 内部验证过（六要素 validation）；" + "、".join(miss)
    if validation_ok:
        return "S2", "S2: 有验证入口但六要素未全过（" + ",".join(six.get("issue_codes", [])) + "）"
    return "S1", "S1: 六要素未过（" + ",".join(six.get("issue_codes", [])) + "）"


def qa_gate_v2(report: Mapping[str, Any], verify: Mapping[str, Callable]) -> dict:
    """四查总门。report = {id,title,markdown,figures,mission_id,...}。

    返回 {passed, qa_status, issues, grade, grade_reason, first_cause,
    failed_checks, audit}。qa_rejection_reason 取 audit 的 JSON 序列化。
    """
    md = report.get("markdown") or ""
    title = report.get("title") or ""
    figures = report.get("figures") or []

    c1 = check_evidence_first(title, md, figures)
    c2 = check_traceability(c1["anchors"], verify)
    grade, grade_reason = grade_report(md, c1["anchors"], c1["six_element"])

    failed = []
    if not c1["ok"]:
        failed.append("evidence_first")
    if not c2["ok"]:
        failed.append("traceability")

    # 首因（first cause）：按 ①→② 顺序取第一个失败查；查③④ 是分级/落账，
    # 不单独构成拒绝（分级 S1 且六要素未过 → 拒绝；S2/S3 且 ①② 过 → 通过）。
    first_cause = None
    if not c1["ok"]:
        for code in c1["issues"]:
            if code in ("missing_physics_statement", "missing_method",
                        "missing_validation", "missing_figures",
                        "missing_limitations", "missing_error_verdict"):
                first_cause = "six_element:" + code
                break
        if first_cause is None:
            first_cause = ("no_evidence_anchor" if "no_evidence_anchor" in c1["issues"]
                           else "title_misaligned")
    elif not c2["ok"]:
        first_cause = "traceability:" + (c2["problems"][0] if c2["problems"] else "no_anchor")

    passed = not failed
    audit = {
        "gate": "qa_gate_v2",
        "checks": {
            "evidence_first": c1["ok"],
            "traceability": c2["ok"],
            "grade": grade,
            "structured_rejection": True,
        },
        "six_element_issues": c1["six_element"]["issue_codes"],
        "title_missing": c1["title_missing"][:10],
        "trace_problems": c2["problems"][:10],
        "anchors": {
            "report_ids": c1["anchors"]["report_ids"][:20],
            "artifact_paths": c1["anchors"]["artifact_paths"][:20],
            "commits": c1["anchors"]["commits"][:10],
            "external_anchors": c1["anchors"]["external_anchors"][:10],
        },
        "grade": grade,
        "grade_reason": grade_reason,
        "first_cause": first_cause,
        "failed_checks": failed,
    }
    return {
        "passed": passed,
        "qa_status": "passed" if passed else "rejected",
        "grade": grade,
        "grade_reason": grade_reason,
        "first_cause": first_cause,
        "failed_checks": failed,
        "issues": c1["six_element"]["issues"],
        "audit": audit,
        "qa_rejection_reason": (
            json.dumps(audit, ensure_ascii=False) if not passed else None
        ),
    }
