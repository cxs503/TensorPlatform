"""退化判据：重跑数字 vs 基线，相对误差 > 阈值即红色告警；白名单豁免须 owner 签名。

判据口径（预声明，可调）：
- ``rel_err = |observed - baseline| / |baseline|``；基线绝对值 ≤ ``abs_floor`` 时用
  ``|observed - baseline| / abs_floor``（零点无定义，显式给尺度，不许静默除零）。
- ``rel_err > rel_tol`` → **red**（退化）；否则 green。
- 案例失败/超时/数字缺失 → **yellow**（不可验证，不能当绿放行）。
- 基线缺失 → ``no_baseline``（首跑标定状态，不产生告警）。
- 白名单：``rel_err ≤ max_rel_err`` 且签名有效且未过期的红条目 → 记 waived（不再计红）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import approvals
from .model import Bundle, Capability, Capability as _Cap, Gate, Waiver
from .runner import CaseRun

GREEN = "green"
YELLOW = "yellow"
RED = "red"
NO_BASELINE = "no_baseline"
WAIVED = "waived"

COMPARISON_OK = "ok"
COMPARISON_RED = "red"
COMPARISON_UNVERIFIABLE = "unverifiable"
COMPARISON_NO_BASELINE = "no_baseline"
COMPARISON_WAIVED = "waived"


@dataclass
class Comparison:
    capability: str
    metric: str
    baseline: Optional[float]
    observed: Optional[float]
    rel_err: Optional[float]
    threshold: float
    status: str
    abs_err: Optional[float] = None
    note: str = ""
    waiver: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "metric": self.metric,
            "baseline": self.baseline,
            "observed": self.observed,
            "rel_err": self.rel_err,
            "abs_err": self.abs_err,
            "threshold": self.threshold,
            "status": self.status,
            "note": self.note,
            "waiver": self.waiver,
        }


@dataclass
class Verdict:
    capability: str
    solver: str
    status: str
    comparisons: List[Comparison] = field(default_factory=list)
    case_status: Dict[str, str] = field(default_factory=dict)
    drifted: List[str] = field(default_factory=list)
    waived: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "solver": self.solver,
            "status": self.status,
            "drifted_metrics": list(self.drifted),
            "waived_metrics": list(self.waived),
            "reasons": list(self.reasons),
            "cases": dict(self.case_status),
            "comparisons": [c.as_dict() for c in self.comparisons],
        }


@dataclass
class DriftReport:
    verdicts: List[Verdict]
    scope: Tuple[str, ...]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def red(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.status == RED]

    @property
    def yellow(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.status == YELLOW]

    @property
    def green(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.status == GREEN]

    def health(self) -> Dict[str, Any]:
        waived = sum(len(v.waived) for v in self.verdicts)
        if self.red:
            status = RED
        elif self.yellow:
            status = YELLOW
        else:
            status = GREEN
        return {
            "status": status,
            "red": len(self.red),
            "yellow": len(self.yellow),
            "green": len(self.green),
            "waived_metrics": waived,
            "capabilities": len(self.verdicts),
        }

    def drifted_capabilities(self) -> List[str]:
        return [v.capability for v in self.verdicts if v.status == RED]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "scope": list(self.scope),
            "health": self.health(),
            "verdicts": [v.as_dict() for v in self.verdicts],
        }


def relative_error(observed: float, baseline: float, abs_floor: float) -> Tuple[float, float]:
    abs_err = abs(observed - baseline)
    denom = abs(baseline)
    if denom <= abs_floor:
        return abs_err / abs_floor, abs_err
    return abs_err / denom, abs_err


def _waiver_match(
    waivers: Sequence[Waiver],
    cap_id: str,
    metric: str,
    rel_err: float,
    *,
    key: Optional[str],
    now: Optional[datetime],
) -> Tuple[Optional[Waiver], str]:
    """返回 (命中的有效豁免, 拒绝原因)。签名无效/过期/超限 → 不豁免（fail-closed）。"""
    for w in waivers:
        if w.capability != cap_id:
            continue
        if w.metric and w.metric != metric:
            continue
        try:
            approvals.verify_approval(w.raw, kind="waiver", key=key)
        except approvals.ApprovalError as exc:
            continue
        if approvals.approval_is_expired(w.raw, now):
            continue
        if w.max_rel_err is not None and rel_err > w.max_rel_err + 1e-12:
            continue
        return w, ""
    return None, "no valid waiver"


def evaluate_capability(
    capability: Capability,
    runs: Mapping[str, CaseRun],
    baseline: Mapping[str, float],
    waivers: Sequence[Waiver],
    *,
    threshold_scale: float = 1.0,
    approval_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Verdict:
    case_status = {cid: r.status for cid, r in runs.items()}
    observed: Dict[str, float] = {}
    case_failed = False
    for r in runs.values():
        if not r.ok:
            case_failed = True
        observed.update(r.metrics)

    verdict = Verdict(
        capability=capability.id,
        solver=capability.solver,
        status=GREEN,
        case_status=case_status,
    )
    if case_failed:
        verdict.reasons.append("verification case failed/timed out")

    for metric in capability.metrics:
        gate = capability.gate(metric) or Gate(metric=metric)
        threshold = gate.rel_tol * float(threshold_scale)
        base = baseline.get(metric)
        obs = observed.get(metric)
        if base is None:
            verdict.comparisons.append(
                Comparison(
                    capability=capability.id,
                    metric=metric,
                    baseline=None,
                    observed=obs,
                    rel_err=None,
                    threshold=threshold,
                    status=COMPARISON_NO_BASELINE,
                    note="no baseline recorded (first run / calibration)",
                )
            )
            verdict.reasons.append(f"{metric}: no baseline")
            continue
        if obs is None:
            verdict.comparisons.append(
                Comparison(
                    capability=capability.id,
                    metric=metric,
                    baseline=base,
                    observed=None,
                    rel_err=None,
                    threshold=threshold,
                    status=COMPARISON_UNVERIFIABLE,
                    note="metric not recorded by verification case",
                )
            )
            verdict.reasons.append(f"{metric}: not recorded")
            continue
        rel_err, abs_err = relative_error(obs, base, gate.abs_floor)
        comp = Comparison(
            capability=capability.id,
            metric=metric,
            baseline=base,
            observed=obs,
            rel_err=rel_err,
            abs_err=abs_err,
            threshold=threshold,
            status=COMPARISON_OK,
        )
        if rel_err > threshold:
            waiver, why = _waiver_match(
                waivers, capability.id, metric, rel_err, key=approval_key, now=now
            )
            if waiver is not None:
                comp.status = COMPARISON_WAIVED
                comp.waiver = {
                    "approver": waiver.approver,
                    "approved_at": waiver.approved_at,
                    "reason": waiver.reason,
                    "max_rel_err": waiver.max_rel_err,
                    "expires_at": waiver.expires_at,
                }
                verdict.waived.append(metric)
            else:
                comp.status = COMPARISON_RED
                comp.note = why
                verdict.drifted.append(metric)
        verdict.comparisons.append(comp)

    if verdict.drifted:
        verdict.status = RED
    elif case_failed or any(
        c.status in (COMPARISON_UNVERIFIABLE, COMPARISON_NO_BASELINE) for c in verdict.comparisons
    ):
        verdict.status = YELLOW
    else:
        verdict.status = GREEN
    return verdict


def evaluate(
    bundle: Bundle,
    runs_by_capability: Mapping[str, Mapping[str, CaseRun]],
    *,
    scope: Optional[Sequence[str]] = None,
    threshold_scale: float = 1.0,
    approval_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> DriftReport:
    scope_ids = tuple(scope) if scope is not None else bundle.ids
    verdicts: List[Verdict] = []
    for cap in bundle.capabilities:
        if cap.id not in scope_ids:
            continue
        verdicts.append(
            evaluate_capability(
                cap,
                runs_by_capability.get(cap.id, {}),
                bundle.baseline.get(cap.id, {}),
                bundle.whitelist,
                threshold_scale=threshold_scale,
                approval_key=approval_key,
                now=now,
            )
        )
    return DriftReport(verdicts=verdicts, scope=scope_ids)


def comparison_table(report: DriftReport) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for v in report.verdicts:
        for c in v.comparisons:
            rows.append(c.as_dict())
    return rows