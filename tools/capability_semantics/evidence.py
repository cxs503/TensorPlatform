"""evidence 层审计：把三元组判成证据等级，并给出可复核理由码。

判定级联（顺序即优先级；每条都在单测里固定，不许事后调）

============  ====================================================  =====================
序            条件                                                  派生等级 / 理由码
============  ====================================================  =====================
1             有 stance=contradicts 的审计裁决                       取最弱裁决等级 / ``CONTRADICTED_BY_AUDIT``
2             无 code 交付、也无证据                                 ``NOT_SUPPORTED`` / ``NO_DELIVERY_NO_EVIDENCE``
3             无 code 交付、有证据/验证工件                            ``WITHHELD`` / ``NO_CODE_DELIVERY_WITH_EVIDENCE``
4             有 code + 数值通过工件（最小误差 ≤ numeric_pass_pct）    ``REAL`` / ``NUMERIC_PASS_VERIFIED``
5             有 code + 测试（含数值断言）                            ``WITHHELD`` / ``UNVERIFIED_IMPLEMENTATION``
6             有 code + 测试（无数值断言）                            ``WITHHELD`` / ``TEST_WITHOUT_NUMERIC_ASSERT``
7             只有 code                                             ``STUB`` / ``CODE_ONLY``
============  ====================================================  =====================

纪律：**代码存在 ≠ 证据存在**。第 4 条要求「独立验证工件 + 数值判据通过」，
否则一律 WITHHELD——这正是本节点要治的病根（用交付物冒充证据）。
精度另立一维：``accuracy_flag`` 按 3%/15% 两档标注，不与等级混同，
避免「算得不准」被写成「没实现」或反之。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from .model import SemanticTriple, level_rank, stable_counts
from .sources import SemanticsConfig

#: 理由码 → 中文释义（报告与 API 共用，避免多处措辞漂移）
REASON_TEXT: dict[str, str] = {
    "CONTRADICTED_BY_AUDIT": "独立审计裁决与声明相反，取最弱裁决等级为上限",
    "NO_DELIVERY_NO_EVIDENCE": "既无交付物也无证据 → 不支持",
    "NO_CODE_DELIVERY_WITH_EVIDENCE": "有证据但无代码交付 → 未验证",
    "NO_CODE_DELIVERY_WITH_TEST_REFERENCE": "只有测试引用、扫描未定位到实现文件 → 未验证",
    "NUMERIC_PASS_VERIFIED": "有代码交付且有数值通过工件（误差 ≤ 3%）",
    "UNVERIFIED_IMPLEMENTATION": "有代码与数值断言测试，但缺独立验证工件 → 未验证",
    "TEST_WITHOUT_NUMERIC_ASSERT": "有测试但无数值断言，不能作为精度证据 → 未验证",
    "CODE_ONLY": "只有代码交付、无测试无工件 → 空壳待验",
    "PATCHED": "经误用治理补丁重判",
}

#: WITHHELD 根因归类（判据 c 允许 >30% 时以原因分析替代，必须逐因给根因）
WITHHELD_ROOT_CAUSE: dict[str, str] = {
    "NO_CODE_DELIVERY_WITH_TEST_REFERENCE":
        "扫描关键词门控保守：有测试引用但未把实现文件绑定到该能力 → 交付绑定缺口（非必然无实现）",
    "NO_CODE_DELIVERY_WITH_EVIDENCE":
        "有验证/文档工件但扫描未定位实现文件 → 交付与证据脱节，证据无法落到代码",
    "UNVERIFIED_IMPLEMENTATION":
        "有代码与数值断言测试，但缺独立的数值通过工件（基准/实验对标）→ 平台级验证供给不足",
    "TEST_WITHOUT_NUMERIC_ASSERT":
        "测试无数值断言，不能作为精度证据 → 测试质量缺口",
}

ACCURACY_TEXT: dict[str, str] = {
    "within_pass": "最小误差 ≤ 3%（平台通过档）",
    "above_pass": "3% < 最小误差 ≤ 15%（可用但未达通过档）",
    "above_accept": "最小误差 > 15%（不可接受档）",
    "no_numeric": "无带数值判据的验证工件",
}


def accuracy_flag(triple: SemanticTriple, cfg: SemanticsConfig) -> str:
    """精度维（与等级维正交）：按最小误差落 3%/15% 两档。"""
    err = triple.min_error_pct
    if err is None:
        return "no_numeric"
    try:
        e = float(err)
    except (TypeError, ValueError):
        return "no_numeric"
    if e != e:  # NaN 防护：NaN 既不 ≤ 也不 >，必须显式挡掉
        return "no_numeric"
    if e <= cfg.threshold("numeric_pass_pct"):
        return "within_pass"
    if e <= cfg.threshold("numeric_accept_pct"):
        return "above_pass"
    return "above_accept"


def _contradictions(triple: SemanticTriple) -> list[str]:
    out = []
    for ev in triple.evidences:
        if str(ev.stance).lower().startswith("contradict") and ev.asserted_level:
            out.append(ev.asserted_level)
    return out


def derive_level(triple: SemanticTriple, cfg: SemanticsConfig) -> tuple[str, str, str]:
    """返回 (派生等级, 理由码, 精度档)。纯函数：不修改入参。"""
    acc = accuracy_flag(triple, cfg)

    contra = _contradictions(triple)
    if contra:
        ceiling = min(contra, key=level_rank)
        return ceiling, "CONTRADICTED_BY_AUDIT", acc

    code = triple.code
    if not code:
        if triple.tests:
            return "WITHHELD", "NO_CODE_DELIVERY_WITH_TEST_REFERENCE", acc
        if triple.evidences:
            return "WITHHELD", "NO_CODE_DELIVERY_WITH_EVIDENCE", acc
        return "NOT_SUPPORTED", "NO_DELIVERY_NO_EVIDENCE", acc

    passes = triple.numeric_passes
    if passes:
        err = triple.min_error_pct
        if err is not None and err == err and err <= cfg.threshold("numeric_pass_pct"):
            return "REAL", "NUMERIC_PASS_VERIFIED", acc

    tests = triple.tests
    if tests:
        if triple.numeric_asserts >= int(cfg.threshold("min_numeric_assert_tests_mock")):
            return "WITHHELD", "UNVERIFIED_IMPLEMENTATION", acc
        return "WITHHELD", "TEST_WITHOUT_NUMERIC_ASSERT", acc

    return "STUB", "CODE_ONLY", acc


def grade_triples(triples: Sequence[SemanticTriple], cfg: SemanticsConfig,
                  overrides: dict[str, tuple[str, str]] | None = None) -> None:
    """就地写入每行三元组的派生等级 / 理由码 / 精度档（``overrides`` 为治理补丁）。"""
    ov = overrides or {}
    for t in triples:
        level, code, acc = derive_level(t, cfg)
        if t.capability_id in ov:
            level, code = ov[t.capability_id]
        t.derived_level = level
        t.reason_code = code
        t.accuracy_flag = acc


def audit_evidence(triples: Sequence[SemanticTriple], cfg: SemanticsConfig) -> dict[str, Any]:
    """证据等级审计报告（判据 (c)：≥20 条完成评定 + WITHHELD 比例）。"""
    rows: list[dict[str, Any]] = []
    for t in triples:
        rows.append({
            "capability_id": t.capability_id,
            "domain": t.domain,
            "declared_level": t.declared_level,
            "derived_level": t.derived_level,
            "reason_code": t.reason_code,
            "reason_text": REASON_TEXT.get(t.reason_code, t.reason_code),
            "accuracy_flag": t.accuracy_flag,
            "accuracy_text": ACCURACY_TEXT.get(t.accuracy_flag, t.accuracy_flag),
            "delivery_count": len(t.deliveries),
            "code_count": len(t.code),
            "test_count": len(t.tests),
            "verified_count": len(t.verified),
            "evidence_count": len(t.evidences),
            "numeric_pass_count": len(t.numeric_passes),
            "numeric_pass_subject_backed": len(t.numeric_passes_subject_backed),
            "min_error_pct": t.min_error_pct,
            "claim_rank_gt_derived": level_rank(t.declared_level) > level_rank(t.derived_level),
        })
    rows.sort(key=lambda r: (level_rank(r["derived_level"]), r["capability_id"]))
    total = len(rows)
    withheld = [r for r in rows if r["derived_level"] == "WITHHELD"]
    ratio = (len(withheld) / total) if total else 0.0
    reasons = stable_counts([r["reason_code"] for r in rows])
    accuracy = stable_counts([r["accuracy_flag"] for r in rows])
    level_dist = stable_counts([r["derived_level"] for r in rows])
    overclaim = [r["capability_id"] for r in rows if r["claim_rank_gt_derived"]]

    min_graded = int(cfg.threshold("min_graded_entries"))
    withheld_max = cfg.threshold("withheld_ratio_max")
    reasons_for_withheld = {
        code: {
            "count": n,
            "share_of_withheld": round(n / len(withheld), 4) if withheld else 0.0,
            "text": REASON_TEXT.get(code, code),
        }
        for code, n in reasons.items() if code in (
            "NO_CODE_DELIVERY_WITH_EVIDENCE",
            "NO_CODE_DELIVERY_WITH_TEST_REFERENCE",
            "UNVERIFIED_IMPLEMENTATION",
            "TEST_WITHOUT_NUMERIC_ASSERT",
        )
    }
    withheld_groups: dict[str, list[str]] = {}
    for r in withheld:
        withheld_groups.setdefault(r["reason_code"], []).append(r["capability_id"])
    withheld_groups = {k: sorted(v) for k, v in sorted(withheld_groups.items())}
    root_causes = {k: WITHHELD_ROOT_CAUSE.get(k, REASON_TEXT.get(k, k))
                   for k in withheld_groups}
    covered = sum(len(v) for v in withheld_groups.values())
    analysis_complete = bool(withheld) and covered == len(withheld)
    return {
        "graded_entries": total,
        "min_graded_entries": min_graded,
        "criterion_graded_ok": total >= min_graded,
        "level_distribution": level_dist,
        "withheld_count": len(withheld),
        "withheld_ratio": round(ratio, 4),
        "withheld_ratio_max": withheld_max,
        "criterion_withheld_ok": ratio < withheld_max,
        "withheld_breakdown": reasons_for_withheld,
        "withheld_groups": withheld_groups,
        "withheld_root_causes": root_causes,
        "withheld_analysis_complete": analysis_complete,
        "reason_histogram": reasons,
        "accuracy_histogram": accuracy,
        "overclaim_ids": overclaim,
        "real_entries": [r["capability_id"] for r in rows if r["derived_level"] == "REAL"],
        "real_subject_backed": [r["capability_id"] for r in rows
                                if r["derived_level"] == "REAL"
                                and r["numeric_pass_subject_backed"] > 0],
        "entries": rows,
    }


__all__ = ["REASON_TEXT", "ACCURACY_TEXT", "WITHHELD_ROOT_CAUSE", "accuracy_flag", "derive_level", "grade_triples",
           "audit_evidence"]