"""判级一致率标定（agreement calibration）。

两条互补口径，报告里**分别列出、不合并**：

* ``labeled``：20 例人工标注对抗夹具（构造集）。每例给出"人工审计应判什么"，
  测判级器与标注的一致率（C2 目标 ≥90%）。构造集的局限写在报告里：它验证的是
  "实现是否符合平台等级定义"，不是"等级定义本身是否正确"。
* ``manual``：真实 21 项能力里，人工复核台账已有裁决的条目，自动判级与之一致率。
  注意人工上限（ceiling）会让一致率**部分由构造保证**，因此同时给出
  ``ceiling_free``（不含上限的裸判级）一致率供对照。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .grader import grade_capability
from .mapping import LEVEL_RANK


def run_case(case: Dict[str, Any]) -> str:
    """对单个标注用例执行判级，返回等级。"""
    grade = grade_capability(
        case["capability_id"],
        tests=case.get("tests", {}),
        probes=case.get("probes", []),
        markers=case.get("markers", {"files_scanned": 0}),
        implementation_present=bool(case.get("implementation_present", True)),
        mapped=bool(case.get("mapped", True)),
        ceiling=case.get("ceiling"),
        policy=case.get("policy"),
    )
    return grade.level


def evaluate_labeled(cases: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """标注集一致率。"""
    cases = list(cases)
    agree: List[str] = []
    mismatches: List[Dict[str, str]] = []
    for case in cases:
        got = run_case(case)
        want = case["expected_level"]
        if got == want:
            agree.append(case["capability_id"])
        else:
            mismatches.append({"case": case["capability_id"], "expected": want, "got": got})
    total = len(cases)
    return {
        "cases": total,
        "agree": len(agree),
        "agreement": round(len(agree) / total, 4) if total else 0.0,
        "mismatches": mismatches,
        "C2_labeled_agreement_met": bool(total) and len(agree) / total >= 0.9,
    }


def evaluate_manual(*, capability_ids: Iterable[str],
                    acceptances: Dict[str, Dict[str, Any]],
                    manual_levels: Dict[str, str]) -> Dict[str, Any]:
    """真实能力 vs 人工复核裁决的一致率（含裸判级对照）。"""
    compared: List[Dict[str, str]] = []
    for cid in capability_ids:
        manual = manual_levels.get(cid)
        acc = acceptances.get(cid) or {}
        verdict = acc.get("verdict") or {}
        auto = verdict.get("level")
        if not manual or not auto:
            continue
        compared.append({
            "capability_id": cid,
            "manual": manual,
            "auto_with_ceiling": auto,
            "auto_raw": verdict.get("auto_level_raw") or auto,
        })
    with_ceiling = sum(1 for row in compared if row["manual"] == row["auto_with_ceiling"])
    raw = sum(1 for row in compared if row["manual"] == row["auto_raw"])
    total = len(compared)
    return {
        "compared": total,
        "agree_with_ceiling": with_ceiling,
        "agree_raw": raw,
        "agreement_with_ceiling": round(with_ceiling / total, 4) if total else 0.0,
        "agreement_raw": round(raw / total, 4) if total else 0.0,
        "rows": compared,
        "C2_manual_agreement_met": bool(total) and with_ceiling / total >= 0.9,
    }


def rank_gap(a: str, b: str) -> int:
    """等级序差（用于报告里说明"差了几档"）。"""
    return LEVEL_RANK.get(a, -1) - LEVEL_RANK.get(b, -1)