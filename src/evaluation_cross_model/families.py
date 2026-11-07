"""两个**独立推理族**的物理证据判级（IN47-N08）。

独立性约束（写进代码，测试也会核验）
------------------------------------
- 两族各自实现、互不调用；输入只共享同一个 :class:`CaseRecord`。
- 两族都**看不到** ``CaseRecord.ref_self_consistency_pct``（参考值自身自一致性审计），
  这条只属于预注册参考标准 :func:`corpus.oracle_label`。因此「族判级 vs 参考标准」
  不是同义反复，可以当作效度度量。
- 两族的决策机制不同：
  * **族 A（deviation-interval 规则级联）**：对 ``|dev|`` 做硬阈值区间判定，带
    ``u_ref/2`` 的判定死区（区间内取保守侧），完全不使用 ``cross_route_pct``。
  * **族 B（证据加权合成）**：把参考等级质量、归一化残差 z=|dev|/tol、行为证据、
    交叉实现一致性四项加权成连续分，用独立阈值 τ 切分，属于「软」判据。
- 输出同一套证据词汇（REAL/WITHHELD/MOCK/STUB/NOT_SUPPORTED）与 E/S 等级，
  才可以比对一致性。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from .corpus import (
    EVIDENCE_LEVELS,
    MOCK,
    NOT_SUPPORTED,
    REAL,
    REF_CLASS_QUALITY,
    STUB,
    TOLERANCE_PCT,
    WITHHELD,
    CaseRecord,
)

#: 证据词汇 → (E 等级, S 等级)。等级序沿用平台既有 S-N<S1<...<S5 / E0<...<E4 语义。
LABEL_TO_GRADE: Dict[str, tuple] = {
    REAL: ("E3", "S4"),
    WITHHELD: ("E1", "S2"),
    MOCK: ("E0", "S1"),
    STUB: ("E0", "S1"),
    NOT_SUPPORTED: ("E0", "S-N"),
}

#: 族 A 的判定死区系数（相对 u_ref）。**默认 0**：族 A 直接实现平台预注册标准
#: ``|dev| ≤ 15% → REAL``，不引入额外自由参数；判级抖动由评价协议（复测取中位，
#: 见 :mod:`robustness`，实测翻转率 ≤5%）承担。死区取非零值的消融结果见
#: :func:`ablation.deadband_ablation` —— 死区会把贴着容差的合格案例误判为 WITHHELD。
DEADBAND_FACTOR = 0.0
#: 族 A 的参考等级白名单下限（0.70 = 解析/回归/公布值；仓内引用与仓内交叉实现不足）。
REF_QUALITY_MIN = 0.70


def _q_accuracy(err: float, tol_pct: float) -> float:
    """归一化残差的软折扣：z=1（正好等于容差）取 0.5，单调递减。"""
    z = abs(err) / tol_pct if tol_pct else float("inf")
    return 1.0 / (1.0 + z ** 3)


def _boundary_composite(ref_quality: float, tol_pct: float = TOLERANCE_PCT) -> float:
    """合成边界案例的合成分：|dev| 恰等于容差、该参考等级、行为证据齐备、无第二实现。

    该案例按预注册精度标准**不应**被判 REAL，因此它就是族 B 阈值 τ 的解析标定点。
    """
    return (0.45 * ref_quality + 0.30 * _q_accuracy(tol_pct, tol_pct)
            + 0.15 * 1.0 + 0.10 * 0.5)


#: τ 的标定边际（边界案例必须掉在 τ 以下，取 0.01 的余量）。
TAU_MARGIN = 0.01


def tau_for(ref_class: str) -> float:
    """按参考等级解析标定 REAL 阈值：同等级边界案例的合成分 + 边际。

    标定只用**合成**边界案例，不接触物理语料 → 不存在语料过拟合。
    等效地，族 B 的判定等价于「|dev| 需低于预注册容差（约 14.4% < 15%）」，
    参考等级越低，要求越严（因为 q_ref 权重被边界案例一起吸收）。
    """
    return round(_boundary_composite(REF_CLASS_QUALITY.get(ref_class, 0.0)) + TAU_MARGIN, 4)


#: 族 B 的 REAL 阈值（最高参考等级的基准值，供测试与文档引用）。
TAU_REAL_B = tau_for("analytic")

E_RANK = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}
S_RANK = {"S-N": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}


def _verdict(family: str, label: str, score: float, rationale: str,
             detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    e_level, grade = LABEL_TO_GRADE[label]
    return {
        "family": family,
        "label": label,
        "e_level": e_level,
        "grade": grade,
        "score": round(float(score), 4),
        "rationale": rationale,
        "detail": detail or {},
    }


def _structural(case: CaseRecord, *, require_independent_ref: bool = True) -> Optional[tuple]:
    """两族共用的**结构性**前置判定（证据结构事实，不涉及数值判据）。

    这部分不是「调参」，而是证据 schema 的硬约束：
    - 几何门未通过 → NOT_SUPPORTED；
    - 无有限输出 → STUB；对 ≥3 组输入输出恒定 → MOCK；
    - **证据独立性门**：REAL 必须有独立外部参考。仓内引用（``repo_citation``，未核对原文）
      与仓内交叉实现（``internal_crosscheck``）都不是独立证据，直接判 WITHHELD——
      这是「交叉模型验证必须抵御自我循环」的第一道闸。
    """
    if not case.geometry.get("ok", True):
        return NOT_SUPPORTED, "几何门未通过"
    if case.stub or not case.value_present:
        return STUB, "无有限输出"
    if not case.responds_to_inputs:
        return MOCK, "对 ≥3 组输入输出恒定"
    if require_independent_ref:
        q = REF_CLASS_QUALITY.get(case.ref_class, 0.0)
        if case.ref_class not in ("none",) and q < REF_QUALITY_MIN:
            return WITHHELD, (f"参考等级 {case.ref_class}(q={q:.2f}) 非独立外部证据，"
                              f"低于独立性门 {REF_QUALITY_MIN:.2f}")
    return None


# ───────────────────────── 族 A：deviation-interval 规则级联 ─────────────────────────


def family_a(case: CaseRecord, *, tol_pct: float = TOLERANCE_PCT,
             deadband_factor: float = DEADBAND_FACTOR,
             require_independent_ref: bool = True) -> Dict[str, Any]:
    """规则级联族：预注册阈值 + 证据独立性门，硬判（死区系数可消融）。"""
    st = _structural(case, require_independent_ref=require_independent_ref)
    if st is not None:
        label, why = st
        return _verdict("A:interval-rule", label, 0.0, why, {"stage": "structural"})

    if not case.domain_ok:
        return _verdict("A:interval-rule", WITHHELD, 0.0, "模型在文档化适用域外",
                        {"stage": "domain"})
    if not case.reproducible:
        return _verdict("A:interval-rule", WITHHELD, 0.0, "重复调用不一致", {"stage": "repeat"})
    if case.reference is None or case.ref_class == "none":
        return _verdict("A:interval-rule", WITHHELD, 0.0, "无独立参考", {"stage": "reference"})

    err = case.abs_error_pct
    if err is None:
        return _verdict("A:interval-rule", WITHHELD, 0.0, "偏离不可计算", {"stage": "error"})

    band = deadband_factor * case.ref_uncertainty_pct
    lo, hi = tol_pct - band, tol_pct + band
    quality = REF_CLASS_QUALITY.get(case.ref_class, 0.0)

    if err <= lo:
        label, why = REAL, f"|dev|={err:.2f}% ≤ {lo:.2f}%（阈值 {tol_pct:.0f}% 减死区）"
    elif err >= hi:
        label, why = WITHHELD, f"|dev|={err:.2f}% ≥ {hi:.2f}%（超阈）"
    else:  # 死区内：取保守侧，抑制边界抖动
        label = WITHHELD
        why = f"|dev|={err:.2f}% 落在死区 [{lo:.2f}%, {hi:.2f}%] → 保守判 WITHHELD"
    return _verdict("A:interval-rule", label, 1.0 - err / tol_pct if tol_pct else 0.0, why,
                    {"stage": "deviation", "abs_dev_pct": round(err, 4),
                     "deadband_pct": round(band, 4), "ref_quality": quality})


# ───────────────────────── 族 B：证据加权合成 ─────────────────────────


def family_b(case: CaseRecord, *, tol_pct: float = TOLERANCE_PCT,
             require_independent_ref: bool = True) -> Dict[str, Any]:
    """证据加权族：连续打分 + 参考等级相关的解析标定阈值 τ。"""
    st = _structural(case, require_independent_ref=require_independent_ref)
    if st is not None:
        label, why = st
        return _verdict("B:weighted", label, 0.0, why, {"stage": "structural"})

    q_quality = REF_CLASS_QUALITY.get(case.ref_class, 0.0)
    q_behaviour = 1.0 if (case.domain_ok and case.reproducible) else 0.0
    err = case.abs_error_pct
    q_acc = _q_accuracy(err, tol_pct) if err is not None else 0.0
    if case.cross_route_pct is not None:
        q_cross = max(0.0, 1.0 - abs(case.cross_route_pct) / tol_pct)
    else:
        q_cross = 0.5  # 无第二实现 → 中性，不加分也不减分

    score = 0.45 * q_quality + 0.30 * q_acc + 0.15 * q_behaviour + 0.10 * q_cross
    tau = tau_for(case.ref_class)
    label = REAL if score >= tau else WITHHELD
    why = (f"composite={score:.3f} (q_ref={q_quality:.2f}, q_acc={q_acc:.3f}, "
           f"q_beh={q_behaviour:.0f}, q_xroute={q_cross:.2f}) {'≥' if label == REAL else '<'} τ={tau}")
    return _verdict("B:weighted", label, score, why,
                    {"stage": "composite", "q_ref": round(q_quality, 4),
                     "q_acc": round(q_acc, 4), "q_behaviour": q_behaviour,
                     "q_cross": round(q_cross, 4), "tau": tau})


# ───────────────────────── 一致性工具 ─────────────────────────


def label_distance(a: str, b: str) -> int:
    """两判级的 E 等级距离（未知等级记 99）。"""
    ea = E_RANK.get(LABEL_TO_GRADE.get(a, ("E0", "S-N"))[0], 0)
    eb = E_RANK.get(LABEL_TO_GRADE.get(b, ("E0", "S-N"))[0], 0)
    return abs(ea - eb)


def agree(va: Dict[str, Any], vb: Dict[str, Any]) -> bool:
    """一致 = 证据词汇相同且 E 等级相同（与平台既有 cross_model 口径一致）。"""
    return va["label"] == vb["label"] and va["e_level"] == vb["e_level"]