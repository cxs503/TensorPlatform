"""cycle_baseline.eligibility —— 条件子总体分析（覆盖率不足时的唯一诚实出路）。

问题
----
严格 cohort（窗口内全部终态 mission）上，各段覆盖率 41%–75%，按判据 (d) 全部降级
WITHHELD。但"降级"不等于"没有信息"：缺口本身有明确的结构性来源（无会话关联的 mission
不可能有领取事件；没有 PR 动作的 mission 不可能有落地时间；平台自己标了 report_owed 的
mission 本来就没交报告）。因此对每段定义一个**可证据总体**（evidence-eligible population），
在这个子总体上重算覆盖率与分位：

* 若子总体覆盖率 ≥ 80% → 结论**只对该子总体成立**（MEASURED_CONDITIONAL），不外推到全体；
* 若仍 < 80% → 继续 WITHHELD（例如 t_report：平台自报欠账的 mission 占三成，缺口不是
  仪器问题而是治理问题）。

同时给出**代表性检验**：用全体都有的 ``cycle_s`` 比较"该段有证据"与"该段缺证据"两组的中位
周期。差异大说明缺失与结果相关（MNAR），条件结论更不可外推；差异小说明近似随机缺失，
但仍不解除判据 (d) 的门禁 —— 门禁是硬规则，不做"看起来差不多就放行"的通融。
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from . import spec
from .stats import grade_segment, pcts, rel_err

#: 段 → (可证据总体判定, 判定理由)
ELIGIBILITY: dict[str, tuple[Callable[[dict], bool], str]] = {
    "t_queue": (lambda r: bool(r.get("session_linked")),
                "领取证据只能经 session 事件关联（session_id 为空则不可能有领取时间）"),
    "t_exec": (lambda r: bool(r.get("session_linked")),
               "同 t_queue：执行段以领取时刻为锚"),
    "t_merge": (lambda r: (r.get("n_pr_events") or 0) > 0,
                "落地时间只对发起过 pr_gate 动作的 mission 有意义"),
    "t_report": (lambda r: r.get("report_owed") is False,
                 "平台自报 report_owed=false 才算该交且已交报告（欠账单不算仪器缺失）"),
}


def _reading(p_measured: Optional[float], p_missing: Optional[float]) -> str:
    """代表性读法（机器判定，不用形容词）：ratio 落在 [0.67, 1.5] 视为近似无关。"""
    if not p_measured or not p_missing:
        return "证据不足（缺证据样本无周期数据）"
    ratio = p_measured / p_missing
    if 0.67 <= ratio <= 1.5:
        return "缺失近似与周期无关（MNAR 证据弱），但仍不解除判据 (d) 门禁"
    side = "更长" if ratio > 1 else "更短"
    return (f"缺失与结果相关（MNAR）：有证据组周期中位数为缺证据组的 {ratio:.2f} 倍，"
            f"即有证据组任务{side} → 条件结论不可外推到全体")


def conditional_ledger(records: Sequence[dict], coverage_min: float = spec.COVERAGE_MIN
                       ) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for seg, (pred, why) in ELIGIBILITY.items():
        eligible = [r for r in records if pred(r)]
        measured = [r for r in eligible if r.get(seg) is not None]
        missing = [r for r in eligible if r.get(seg) is None]
        _ = missing  # 仅供审计：条件子总体内的缺失条数
        cov = (len(measured) / len(eligible)) if eligible else None
        # 代表性检验在**全体 cohort** 上分组：该段有证据 vs 无证据（严格 cohort 的缺失结构）
        cyc_m = [r["cycle_s"] for r in records
                 if r.get(seg) is not None and r.get("cycle_s") is not None]
        cyc_x = [r["cycle_s"] for r in records
                 if r.get(seg) is None and r.get("cycle_s") is not None]
        p_m = pcts(cyc_m, qs=(0.5,))["p50"]
        p_x = pcts(cyc_x, qs=(0.5,))["p50"]
        out[seg] = {
            "eligible_population": len(eligible),
            "eligible_share_of_cohort": (len(eligible) / len(records)) if records else None,
            "measured_n": len(measured),
            "coverage_within": cov,
            "grade": grade_segment(cov, coverage_min),
            "grade_label": ("MEASURED_CONDITIONAL" if (cov or 0) >= coverage_min
                            else "WITHHELD"),
            **pcts([r[seg] for r in measured]),
            "why": why,
            "bookkeeping_inconsistency": (
                {"n_no_event_despite_eligible": sum(1 for r in eligible
                                                    if (r.get("n_reports") or 0) == 0),
                 "share": (sum(1 for r in eligible if (r.get("n_reports") or 0) == 0)
                           / len(eligible)) if eligible else None,
                 "reading": "平台台账称不欠报告但无报告行：口径与实交不一致"
                            "（治理缺口，不是仪器缺失）"}
                if seg == "t_report" else None),
            "representativeness": {
                "cycle_p50_measured_s": p_m,
                "cycle_p50_missing_s": p_x,
                "ratio_measured_over_missing": (p_m / p_x) if (p_m and p_x) else None,
                "rel_diff": rel_err(p_m, p_x),
                "n_missing_with_cycle": len(cyc_x),
                "reading": _reading(p_m, p_x),
            },
        }
    ok = [s for s, v in out.items() if v["grade_label"] == "MEASURED_CONDITIONAL"]
    out["summary"] = {
        "coverage_min": coverage_min,
        "measured_conditional": ok,
        "withheld": [s for s in ELIGIBILITY if s not in ok],
        "scope_warning": "条件结论只对可证据子总体成立，不得外推到全体 cohort",
    }
    return out