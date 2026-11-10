"""决策门：把一致性系数变成**可执行的判级纪律**（不是又一个统计数字）。

规则（战役 metadata 口径，阈值单点定义在本模块 ``THRESHOLDS``）

============================  ==================================================
条件                           动作
============================  ==================================================
α̂ < 0.667                     ``REQUIRE_THIRD``：该批双评单元的结论不足以支撑
                              升账，必须引入**第三评审判**再定；仅有两评的单元
                              一律不得据此升账。
α̂ ≥ 0.667                     ``PASS``：双评单元的结论可作升账依据。
n_pairable < 50 或             ``WITHHELD``：**不出数**。附「CI 半宽 ≤0.1 所需最小
UNKEYED 占比 > 50%            评审判数」表，说明要达到可判需要补多少双评。
单评审级（1 个判级者）          ``UNRELIABLE``：无独立复核，**禁作台账升账依据**
                              ——这条与 α 的取值无关，是设计性缺陷。
============================  ==================================================

三条不可妥协的语义
------------------
1. **WITHHELD ≠ PASS**：样本不足时门必须「关着」，不能默认放行。``enforcement``
   字段显式写 ``"SUSPENDED"``，且所有单审记录仍逐条标 ``UNRELIABLE``。
2. **单审不可救**：α 是**批**层面的系数，再高也不能把「一个成果只有一个判级者」
   变成可复核。因此单审记录的 ``ledger_upgrade_allowed`` 恒为 ``False``。
3. **不追责到人**：判级者用复合键标识，本门只对「判级配置」下结论，不做人物
   画像、不排名（与台账「禁加权禁排名」纪律一致）。
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from tools.eval_reliability import dataset

#: 单点契约：报告 / 测试 / CLI 都读这里，避免阈值漂移
THRESHOLDS: dict[str, float] = {
    "alpha_gate": 0.667,          # 一致性门（α 低于此值需第三评审判）
    "min_pairable_units": 50,     # 出数所需最少双评单元数
    "max_unkeyed_share": 0.50,    # UNKEYED 占比上限（超过即不可分析）
    "target_ci_halfwidth": 0.10,  # 报告要给出的精度目标
}

PASS = "PASS"
REQUIRE_THIRD = "REQUIRE_THIRD"
WITHHELD = "WITHHELD"

ADMISSIBLE = "ADMISSIBLE"
NEEDS_THIRD = "NEEDS_THIRD"
UNRELIABLE = "UNRELIABLE"
PROVISIONAL = "PROVISIONAL"


def alpha_verdict(alpha: float, *, n_pairable: int, unkeyed_share: float,
                  ci: dict[str, Any] | None = None,
                  thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    """把 α̂ 折成 ``PASS`` / ``REQUIRE_THIRD`` / ``WITHHELD``（含理由与证据）。"""
    th = {**THRESHOLDS, **(thresholds or {})}
    reasons: list[str] = []
    if not math.isfinite(float(alpha)) or n_pairable <= 0:
        reasons.append("alpha_unavailable")
    if n_pairable < th["min_pairable_units"]:
        reasons.append(f"n_pairable={n_pairable}<{int(th['min_pairable_units'])}")
    if math.isfinite(float(unkeyed_share)) and unkeyed_share > th["max_unkeyed_share"]:
        reasons.append(f"unkeyed_share={unkeyed_share:.3f}>{th['max_unkeyed_share']:.2f}")

    if reasons:
        status = WITHHELD
    elif float(alpha) < th["alpha_gate"]:
        status = REQUIRE_THIRD
        reasons.append(f"alpha={float(alpha):.3f}<{th['alpha_gate']:.3f}")
    else:
        status = PASS
        reasons.append(f"alpha={float(alpha):.3f}>={th['alpha_gate']:.3f}")

    near_gate = None
    if ci and ci.get("status") == "ok" and math.isfinite(float(alpha)):
        # 区间跨过门槛 → 结论对判级配置敏感，标 borderline（不改变状态，但必须报）
        if ci["lo"] < th["alpha_gate"] <= ci["hi"]:
            near_gate = "CI_CROSSES_GATE"
    return {
        "status": status,
        "alpha": float(alpha),
        "alpha_gate": th["alpha_gate"],
        "n_pairable": int(n_pairable),
        "unkeyed_share": float(unkeyed_share),
        "reasons": reasons,
        "ci_flag": near_gate,
        "enforcement": "SUSPENDED" if status == WITHHELD else "ACTIVE",
        "third_judge_required": status == REQUIRE_THIRD,
        "ledger_upgrade_allowed": status == PASS,
        "thresholds": th,
    }


def _bucket(n_graders: int) -> str:
    return "single" if n_graders <= 1 else ("pair" if n_graders == 2 else "three_plus")


def mission_label(n_graders: int, verdict: dict[str, Any]) -> dict[str, Any]:
    """单个成果（mission）的判级标签 + 是否可作升账依据。"""
    status = verdict.get("status")
    if n_graders <= 1:
        label = UNRELIABLE
        reason = "single_rater_no_independent_check"
        allowed = False
    elif status == PASS:
        label = ADMISSIBLE
        reason = "two_or_more_raters_and_alpha_pass"
        allowed = True
    elif status == REQUIRE_THIRD:
        if n_graders >= 3:
            label = ADMISSIBLE
            reason = "three_plus_raters_satisfy_third_judge_requirement"
            allowed = True
        else:
            label = NEEDS_THIRD
            reason = "alpha_below_gate_needs_third_judge"
            allowed = False
    else:  # WITHHELD
        label = PROVISIONAL
        reason = "alpha_withheld_no_reliability_estimate"
        allowed = False
    return {"label": label, "reason": reason,
            "ledger_upgrade_allowed": allowed, "n_graders": int(n_graders)}


def apply(records: Sequence[dataset.ReliabilityRecord],
          verdict: dict[str, Any]) -> dict[str, Any]:
    """把门作用到台账记录上：逐 mission 标注 + 汇总 + 补齐缺口所需工作量。

    ``to_reach_min_pairable``：把「单审 mission」补成双评还需要多少次额外判级，
    才能让 ``n_pairable`` 达到 ``min_pairable_units``——这是把 WITHHELD 变 PASS
    的最小可执行投入，直接可派工。
    """
    counts_by_mission: dict[str, set[str]] = {}
    for r in records:
        if r.keyed and r.s_level() is not None:
            counts_by_mission.setdefault(r.mission_id or "", set()).add(r.grader or "")
    sizes = {m: len(g) for m, g in counts_by_mission.items()}

    per_mission: list[dict[str, Any]] = []
    label_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {"single": 0, "pair": 0, "three_plus": 0}
    for mid, n in sorted(sizes.items(), key=lambda kv: str(kv[0])):
        lab = mission_label(n, verdict)
        bucket_counts[_bucket(n)] += 1
        label_counts[lab["label"]] = label_counts.get(lab["label"], 0) + 1
        per_mission.append({"mission_id": mid, **lab})

    min_pair = int(verdict.get("thresholds", THRESHOLDS)["min_pairable_units"])
    need = max(0, min_pair - sum(1 for n in sizes.values() if n >= 2))
    singles = [m for m, n in sizes.items() if n <= 1]
    return {
        "verdict": verdict,
        "bucket_counts": bucket_counts,
        "label_counts": label_counts,
        "per_mission": per_mission,
        "single_rater_missions": len(singles),
        "to_reach_min_pairable": {
            "needed_extra_second_evaluations": need,
            "candidate_single_missions": sorted(singles, key=str)[:20],
            "min_pairable_units": min_pair,
        },
        "policy": {
            "single_rater_forbidden_for_ledger_upgrade": True,
            "third_judge_required": bool(verdict.get("third_judge_required")),
            "enforcement": verdict.get("enforcement"),
        },
    }


def render_policy(verdict: dict[str, Any], applied: dict[str, Any]) -> str:
    """把门结论写成一段可直接贴进台账的纪律文字（禁排名、禁加权口径）。"""
    b = applied.get("bucket_counts", {})
    lab = applied.get("label_counts", {})
    need = applied.get("to_reach_min_pairable", {}).get("needed_extra_second_evaluations")
    lines = [
        f"判级可靠性裁决：**{verdict.get('status')}**"
        f"（α={verdict.get('alpha'):.3f}，门槛 {verdict.get('alpha_gate')}，"
        f"可配对单元 n={verdict.get('n_pairable')}，UNKEYED 占比 "
        f"{verdict.get('unkeyed_share'):.3f}）。",
        f"判级配置分布：单评 {b.get('single', 0)} / 双评 {b.get('pair', 0)} / "
        f"三评及以上 {b.get('three_plus', 0)} 个成果。",
        f"标签分布：可升账 {lab.get(ADMISSIBLE, 0)} / 需第三审判 {lab.get(NEEDS_THIRD, 0)} / "
        f"待定 {lab.get(PROVISIONAL, 0)} / 不可复核 {lab.get(UNRELIABLE, 0)}。",
        "纪律：①单审判级**禁作台账升账依据**（无独立复核，与 α 取值无关）；"
        "②α<0.667 时两评单元须补第三评审判；③本门只对判级配置下结论，"
        "不对判级者个人排名或画像（与台账「禁加权禁排名」一致）。",
    ]
    if need:
        lines.append(f"达到可判（n_pairable≥{verdict.get('thresholds', {}).get('min_pairable_units')}）"
                     f"还需补 {need} 次独立第二判级。")
    return "\n\n".join(lines)


__all__ = ["THRESHOLDS", "PASS", "REQUIRE_THIRD", "WITHHELD", "ADMISSIBLE",
           "NEEDS_THIRD", "UNRELIABLE", "PROVISIONAL", "alpha_verdict",
           "mission_label", "apply", "render_policy"]