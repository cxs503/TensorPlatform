"""回归检测：当前 run 与基线逐指标对比，漂移超阈值告警。

预声明口径（写进代码而**不是**事后挑的）：默认容差 3%（``DEFAULT_DRIFT``，
与战役节点 N08 判据一致），数字可被 ``--drift`` / 配置 ``drift_threshold`` 覆盖。

告警分级（fail-loud，但不把"新算例"当回归）：

===============  ========  ====================================================
kind             severity  触发条件
===============  ========  ====================================================
verdict_flip     critical  基线 pass → 当前 fail
case_error       critical  当前算例抛异常
drift            warn      相对漂移 > 容差
drift            critical  相对漂移 > 3×容差（显著退化）
case_missing     warn      基线有、当前无（覆盖度下降 = 静默失守）
case_new         info      当前新增算例（非回归，但入账）
baseline_absent  info      无基线可对比（首次运行，不允许当"无回归"吹）
===============  ========  ====================================================

**重要**：基线缺失返回的是 ``status="no_baseline"`` 而不是 "干净"——"没比过"
绝不等于"没退化"（诚实纪律）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .model import is_finite

DEFAULT_DRIFT = 0.03
DRIFT_CRITICAL_FACTOR = 3.0
# 基线值贴 0 时的分母地板：避免 |cur-base|/|base| 在机器零处爆成天文数字
# （否则 rel_error 从 0.0 变到 2e-16 会被误报成"回归"——假告警同样是不诚实）。
ZERO_FLOOR = 1e-12


def drift_of(current: Any, baseline: Any, floor: float = ZERO_FLOOR) -> Optional[float]:
    """基线相对漂移 |cur-base| / max(|base|, floor)；非有限 → None。"""
    if not (is_finite(current) and is_finite(baseline)):
        return None
    c, b = float(current), float(baseline)
    return abs(c - b) / max(abs(b), floor)

# 不作为漂移指标的字段（配置/规模参数，变化属于算例设计变更而非退化）
_NON_TRACKED = {"intervals", "terms", "iterations", "evals", "n", "steps"}


def build_baseline(suite: Mapping[str, Any],
                   provenance: Optional[Mapping[str, Any]] = None,
                   created_at: Optional[str] = None) -> Dict[str, Any]:
    """从 SuiteVerdict 抽出可比对基线（只留数值指标 + 裁决）。"""
    cases: Dict[str, Any] = {}
    for c in suite.get("cases", []):
        metrics = {k: v for k, v in (c.get("metrics") or {}).items() if is_finite(v)}
        cases[c["case_id"]] = {"passed": bool(c.get("passed")), "metrics": metrics}
    prov = dict(provenance or {})
    return {
        "suite": suite.get("suite"),
        "created_at": created_at or prov.get("generated_at"),
        "engine_version": prov.get("engine_version"),
        "config_sha256": prov.get("config_sha256"),
        "git_sha": prov.get("git_sha"),
        "cases": cases,
    }


def _tracked(metrics: Mapping[str, Any], track: Optional[Sequence[str]]) -> Dict[str, float]:
    """取可跟踪指标。``track`` 显式给出时以它为准（显式优于默认策略）。"""
    out: Dict[str, float] = {}
    for k, v in metrics.items():
        if not is_finite(v):
            continue
        if track:
            if k not in track:
                continue
        elif k in _NON_TRACKED:
            continue
        out[k] = float(v)
    return out


def compare(suite: Mapping[str, Any],
            baseline: Optional[Mapping[str, Any]],
            drift_threshold: float = DEFAULT_DRIFT,
            track_metrics: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """返回回归报告 dict（含 alerts / status / counts）。"""
    if not baseline:
        return {
            "status": "no_baseline",
            "drift_threshold": drift_threshold,
            "tracked_metrics": list(track_metrics) if track_metrics else "all_numeric",
            "alerts": [],
            "counts": {"compared_cases": 0, "drift": 0, "critical": 0, "info": 0},
            "note": "no baseline supplied: absence of comparison is NOT evidence of no regression",
        }

    base_cases = baseline.get("cases") or {}
    cur_cases = {c["case_id"]: c for c in suite.get("cases", [])}
    alerts: List[Dict[str, Any]] = []

    for cid in sorted(set(base_cases) | set(cur_cases)):
        b = base_cases.get(cid)
        c = cur_cases.get(cid)
        if b is None:
            alerts.append({"kind": "case_new", "severity": "info", "case_id": cid,
                           "message": f"case {cid} added since baseline (not a regression)"})
            continue
        if c is None:
            alerts.append({"kind": "case_missing", "severity": "warn", "case_id": cid,
                           "message": f"case {cid} present in baseline but absent now (coverage loss)"})
            continue
        if c.get("status") == "error":
            alerts.append({"kind": "case_error", "severity": "critical", "case_id": cid,
                           "message": f"case {cid} errored: {c.get('error')}"})
            continue
        if b.get("passed") and not c.get("passed"):
            alerts.append({"kind": "verdict_flip", "severity": "critical", "case_id": cid,
                           "message": f"case {cid} flipped pass -> fail"})
        bm = _tracked(b.get("metrics") or {}, track_metrics)
        cm = _tracked(c.get("metrics") or {}, track_metrics)
        for metric in sorted(set(bm) & set(cm)):
            d = drift_of(cm[metric], bm[metric])
            if d is None or d <= drift_threshold:
                continue
            sev = "critical" if d > DRIFT_CRITICAL_FACTOR * drift_threshold else "warn"
            alerts.append({
                "kind": "drift", "severity": sev, "case_id": cid, "metric": metric,
                "baseline": bm[metric], "current": cm[metric], "drift": d,
                "drift_pct": round(100.0 * d, 4),
                "threshold": drift_threshold,
                "message": (f"{cid}.{metric} drifted {100*d:.2f}% "
                            f"({bm[metric]:.6g} -> {cm[metric]:.6g}) > {100*drift_threshold:.1f}%"),
            })

    counts = {
        "compared_cases": len(set(base_cases) & set(cur_cases)),
        "drift": sum(1 for a in alerts if a["kind"] == "drift"),
        "critical": sum(1 for a in alerts if a["severity"] == "critical"),
        "info": sum(1 for a in alerts if a["severity"] == "info"),
    }
    status = "clean" if not alerts else ("regression" if counts["critical"] or counts["drift"] else "noted")
    return {
        "status": status,
        "drift_threshold": drift_threshold,
        "tracked_metrics": list(track_metrics) if track_metrics else "all_numeric",
        "baseline_created_at": baseline.get("created_at"),
        "baseline_git_sha": baseline.get("git_sha"),
        "counts": counts,
        "alerts": alerts,
    }


def has_regression(report: Mapping[str, Any]) -> bool:
    """存在漂移或 critical 告警 → True（runner 据此置退出码 2）。"""
    return any(a["kind"] in ("drift", "verdict_flip", "case_error") for a in report.get("alerts", []))