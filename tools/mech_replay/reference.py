#!/usr/bin/env python3
"""8 个机制的**独立第二实现**（手写命令式），用于与声明式解释器做差分。

为什么要有第二实现
------------------
golden fixture 的判词若由被测实现自己生成，「一致性」是自证的。差分对象换成一份
**不读规则表**的手写实现后，C1 才有信息量：解释器与手写实现任一方写错，差分立刻红。

约定
----
* 返回 ``{"decision": "pass"|"fail", "codes": [规则id...]}``，codes 用与规则表同名的
  条件标签（便于与解释器 diff），但判定逻辑完全独立重写。
* 缺字段/类型错一律抛 :class:`ReferenceInputError`，由调用方翻译成
  ``decision=fail, codes=[MISSING_FIELD]``——与解释器行为对齐（机制不允许静默放行）。
"""

from __future__ import annotations

from typing import Any, Mapping

from tools.mech_replay.mechanisms import GRADES, NODE_STATUSES

MISSING = "MISSING_FIELD"


class ReferenceInputError(Exception):
    def __init__(self, path: str, code: str = MISSING):
        super().__init__(f"{code}: {path}")
        self.path = path
        self.code = code


def _need(record: Mapping[str, Any], path: str) -> Any:
    cur: Any = record
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            raise ReferenceInputError(path)
        cur = cur[part]
    return cur


def _num(record: Mapping[str, Any], path: str) -> float:
    value = _need(record, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceInputError(path, "TYPE_ERROR")
    return float(value)


def _seq(record: Mapping[str, Any], path: str) -> list[Any]:
    value = _need(record, path)
    if not isinstance(value, (list, tuple)):
        raise ReferenceInputError(path, "TYPE_ERROR")
    return list(value)


def _map(record: Mapping[str, Any], path: str) -> dict[str, Any]:
    value = _need(record, path)
    if not isinstance(value, Mapping):
        raise ReferenceInputError(path, "TYPE_ERROR")
    return dict(value)


def _item(item: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in item or item[key] is None:
        raise ReferenceInputError(f"{path}.{key}")
    return item[key]


def _capability_registration(record: Mapping[str, Any]) -> dict[str, Any]:
    grade = _need(record, "evidence_grade")
    codes: list[str] = []
    if grade not in GRADES:
        codes.append("CAP_GRADE_ENUM")
    if grade == "REAL":
        if len(_seq(record, "artifacts")) < 3:
            codes.append("CAP_ARTIFACTS")
        if _num(record, "tests") < 12:
            codes.append("CAP_TESTS")
        if not _need(record, "docs"):
            codes.append("CAP_DOCS")
    if grade == "NOT_SUPPORTED" and _num(record, "artifact_count") != 0:
        codes.append("CAP_NS_NO_ARTIFACT")
    return _out(codes)


def _evidence_grade(record: Mapping[str, Any]) -> dict[str, Any]:
    grade = _need(record, "declared_grade")
    codes: list[str] = []
    if grade not in GRADES:
        codes.append("EG_GRADE_ENUM")
    if grade == "REAL":
        if sum(1 for v in _map(record, "evidence_verified").values() if v) < 6:
            codes.append("EG_VERIFIED")
        if len(_seq(record, "evidence_kinds")) < 3:
            codes.append("EG_KINDS")
        if not _need(record, "reference_anchor"):
            codes.append("EG_ANCHOR")
        if not _need(record, "independent_reimpl"):
            codes.append("EG_INDEP")
    if grade == "NOT_SUPPORTED" and _num(record, "artifact_count") != 0:
        codes.append("EG_NS_EMPTY")
    return _out(codes)


def _machine_gate(record: Mapping[str, Any]) -> dict[str, Any]:
    criteria = _seq(record, "criteria")
    tol = _num(record, "tol_rel")
    codes: list[str] = []
    if len(criteria) < 6:
        codes.append("MG_MIN_CRITERIA")
    if tol > 0.05:
        codes.append("MG_TOL")
    if _num(record, "runs") < 6:
        codes.append("MG_RUNS")
    bad = False
    for item in criteria:
        if not isinstance(item, Mapping):
            raise ReferenceInputError("criteria[]", "TYPE_ERROR")
        try:
            measured = float(_item(item, "measured", "criteria[]"))
            threshold = float(_item(item, "threshold", "criteria[]"))
            direction = str(_item(item, "direction", "criteria[]"))
        except (TypeError, ValueError):
            raise ReferenceInputError("criteria[]", "TYPE_ERROR") from None
        if direction == "max":
            bad = bad or measured > threshold * (1.0 + tol)
        elif direction == "min":
            bad = bad or measured < threshold * (1.0 - tol)
        else:
            raise ReferenceInputError("criteria[].direction", "TYPE_ERROR")
    if bad:
        codes.append("MG_CRITERIA")
    return _out(codes)


def _report_gate(record: Mapping[str, Any]) -> dict[str, Any]:
    codes: list[str] = []
    if not _need(record, "title"):
        codes.append("RG_TITLE")
    if _num(record, "summary_length") < 40:
        codes.append("RG_SUMMARY")
    if sum(1 for v in _map(record, "sections").values() if v) < 6:
        codes.append("RG_SECTIONS")
    if not str(_need(record, "figure_waiver_reason") or "").strip():
        if len(_seq(record, "figures")) < 1:
            codes.append("RG_FIGURES")
    if not _need(record, "error_answer"):
        codes.append("RG_ERROR_ANSWER")
    if _need(record, "status") == "FAIL" and not _need(record, "honest_fail"):
        codes.append("RG_HONEST_FAIL")
    return _out(codes)


def _merge_gate(record: Mapping[str, Any]) -> dict[str, Any]:
    codes: list[str] = []
    if not _need(record, "ci_green"):
        codes.append("MR_CI")
    if _num(record, "approvals") < 6:
        codes.append("MR_APPROVALS")
    if _num(record, "conflicts") != 0:
        codes.append("MR_CONFLICTS")
    if _num(record, "guard_risk") > 0.05:
        codes.append("MR_GUARD")
    if _num(record, "changed_files") > 400:
        codes.append("MR_CHANGED")
    if not _need(record, "base_branch_ok"):
        codes.append("MR_BASE")
    return _out(codes)


def _autonomy_proposal(record: Mapping[str, Any]) -> dict[str, Any]:
    codes: list[str] = []
    if not _need(record, "proposal_title"):
        codes.append("AP_TITLE")
    if _num(record, "prior_art_checks") < 6:
        codes.append("AP_PRIOR_ART")
    if len(_seq(record, "novelty_anchors")) < 1:
        codes.append("AP_ANCHORS")
    if len(_seq(record, "criteria")) < 2:
        codes.append("AP_CRITERIA")
    if any(bool(v) for v in _map(record, "redline_flags").values()):
        codes.append("AP_REDLINE")
    return _out(codes)


def _campaign_status(record: Mapping[str, Any]) -> dict[str, Any]:
    nodes = _seq(record, "nodes")
    codes: list[str] = []
    if len(nodes) < 1:
        codes.append("CS_NODES")
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ReferenceInputError("nodes[]", "TYPE_ERROR")
        status = str(_item(node, "status", "nodes[]"))
        if status not in NODE_STATUSES:
            codes.append("CS_STATUS_ENUM")
            break
    try:
        max_fails = max(float(_item(n, "consecutive_fails", "nodes[]")) for n in nodes)
    except (TypeError, ValueError):
        raise ReferenceInputError("nodes[].consecutive_fails", "TYPE_ERROR") from None
    if max_fails > 4:
        codes.append("CS_MAX_FAILS")
    for node in nodes:
        if str(node.get("status")) != "verified":
            continue
        try:
            evidence = float(_item(node, "evidence_count", "nodes[]"))
        except (TypeError, ValueError):
            raise ReferenceInputError("nodes[].evidence_count", "TYPE_ERROR") from None
        if evidence < 6:
            codes.append("CS_VERIFIED_EVIDENCE")
            break
    return _out(codes)


def _budget_gate(record: Mapping[str, Any]) -> dict[str, Any]:
    codes: list[str] = []
    spent = _num(record, "spent_tokens")
    budget = _num(record, "budget_tokens")
    if budget == 0:
        raise ReferenceInputError("budget_tokens", "TYPE_ERROR")
    if spent / budget > 0.9:
        codes.append("BG_TOKEN_RATIO")
    jobs = _num(record, "concurrent_jobs")
    max_jobs = _num(record, "max_jobs")
    if max_jobs == 0:
        raise ReferenceInputError("max_jobs", "TYPE_ERROR")
    if jobs / max_jobs > 1.0:
        codes.append("BG_CONCURRENCY")
    wall_used = _num(record, "wall_used")
    wall_limit = _num(record, "wall_limit")
    if wall_limit == 0:
        raise ReferenceInputError("wall_limit", "TYPE_ERROR")
    if wall_used / wall_limit > 1.05:
        codes.append("BG_WALL_RATIO")
    if budget < 1000:
        codes.append("BG_BUDGET_MIN")
    if spent < 0:
        codes.append("BG_SPENT_NONNEG")
    return _out(codes)


def _out(codes: list[str]) -> dict[str, Any]:
    uniq = sorted(set(codes))
    return {"decision": "fail" if uniq else "pass", "codes": uniq}


IMPLEMENTATIONS = {
    "capability_registration": _capability_registration,
    "evidence_grade": _evidence_grade,
    "machine_gate": _machine_gate,
    "report_gate": _report_gate,
    "merge_gate": _merge_gate,
    "autonomy_proposal": _autonomy_proposal,
    "campaign_status": _campaign_status,
    "budget_gate": _budget_gate,
}


def reference_verdict(mechanism: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """独立实现判词；输入不合法时退化为 ``fail + [MISSING_FIELD/TYPE_ERROR]``。"""
    try:
        return IMPLEMENTATIONS[mechanism](record)
    except ReferenceInputError as exc:
        return {"decision": "fail", "codes": [exc.code]}


def spec_codes(verdict: Mapping[str, Any]) -> list[str]:
    """把解释器判词压成与独立实现对等的 codes 形态。"""
    return sorted({str(r["code"]) for r in verdict.get("reasons", [])})


__all__ = ["IMPLEMENTATIONS", "reference_verdict", "spec_codes", "ReferenceInputError"]