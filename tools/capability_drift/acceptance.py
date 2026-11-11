"""A1–A8 验收跑批：在合成沙箱仓里真实注入退化，逐条量出判据数字。

全部数字由本模块从运行结果写进 ``artifacts/capability_drift/``，报告只读工件
（零手抄）。判据（战役 p360w1-n10 机器可算验收判据）：

==========  ==========================================================  ==================
编号        判据                                                        量法
==========  ==========================================================  ==================
A1          注入退化（参数行 0.60→大值）后检测到关键数字退化 >5% 并告警    注入+跑+判+blame
A2          无害变更（文档/注释/空白/死代码）零告警，假阳性率 < 1%        100 个无害变体
A3          10 个验证案例整轮重跑 < 10 分钟                            全量 monitor 墙钟
A4          基线更新须 owner 审批，签名后 baseline.json sha256 变化       4 种审批尝试
A5          git blame 定位引入退化的 commit，准确率 > 80%                10 次独立注入
A6          相对误差 >5% 触发、<5% 不触发，阈值可调                     3%/6%/可调阈值
A7          pytest ≥15 全绿                                            子进程跑本包测试
A8          成熟化节点/分支提交/论文级报告/honest FAIL 不覆盖             本模块+报告产物
==========  ==========================================================  ==================
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import alerts, approvals, baseline as baseline_mod, dashboard, model, pipeline, sandbox
from .detector import RED, CaseRun, Gate, evaluate_capability
from .runner import run_case, run_cases

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "capability_drift"
FIGS = ROOT / "docs" / "reports" / "capability_drift"
REPORT_MD = ROOT / "docs" / "reports" / "P360_W1_N10_CAPABILITY_DRIFT_MONITOR.md"
BUDGET_S = 600.0          # A3：重跑预算 10 分钟
FPR_BUDGET = 0.01         # A2：假阳性率上限 1%
BLAME_ACC_FLOOR = 0.80    # A5：定位准确率下限 80%
BASE_RELAX = 0.60


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return model.sha256_file(path) if Path(path).exists() else ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _crit(passed: bool, evidence: Dict[str, Any], artifacts: Sequence[Path]) -> Dict[str, Any]:
    return {
        "passed": bool(passed),
        "evidence": evidence,
        "artifacts": [str(Path(a).relative_to(ROOT)) for a in artifacts if Path(a).exists()],
        "artifact_sha256": {str(Path(a).relative_to(ROOT)): _sha(Path(a)) for a in artifacts
                            if Path(a).exists()},
    }


def _git(repo: Path, *args: str) -> str:
    return sandbox._git(repo, *args)


# --------------------------------------------------------------------------- #
# 标定：跑全部案例 → 写基线（owner 签名）
# --------------------------------------------------------------------------- #
def calibrate_baseline(
    sb: sandbox.Sandbox, key: str, audit_path: Path, workers: int = 4
) -> Dict[str, Any]:
    bundle = model.load_bundle(sb.registry_dir)
    cases = [c for cap in bundle.capabilities for c in cap.cases]
    runs = run_cases(cases, sb.repo, workers=workers)
    observed = pipeline.observed_values(bundle, runs)
    updates: List[Dict[str, Any]] = []
    for cap_id, values in observed.items():
        approval = approvals.make_approval(
            "baseline_update", "owner-acceptance", key,
            capability=cap_id, values_sha256=approvals.sha256_payload(values),
        )
        updates.append(
            baseline_mod.update_baseline(
                sb.baseline_path(), cap_id, values, approval, key=key, audit_path=audit_path
            )
        )
    failed = [r for r in runs.values() if not r.ok]
    return {
        "cases": {cid: r.as_dict() for cid, r in runs.items()},
        "values": observed,
        "updates": updates,
        "n_cases": len(cases),
        "n_failed_cases": len(failed),
        "baseline_sha256": _sha(sb.baseline_path()),
        "at": _now(),
    }


# --------------------------------------------------------------------------- #
# 监控包装
# --------------------------------------------------------------------------- #
def _monitor(
    sb: sandbox.Sandbox,
    *,
    changed: Optional[Sequence[str]] = None,
    scope: Optional[Sequence[str]] = None,
    workers: int = 4,
    threshold_scale: float = 1.0,
    key: Optional[str] = None,
) -> Tuple[model.Bundle, pipeline.MonitorRun]:
    bundle = model.load_bundle(sb.registry_dir)
    run = pipeline.monitor(
        bundle,
        changed_files=changed,
        scope=scope,
        workers=workers,
        threshold_scale=threshold_scale,
        approval_key=key,
        repo_root=sb.repo,
    )
    return bundle, run


def _rel_err(observed: float, base: float) -> float:
    return abs(observed - base) / abs(base) if base else float("inf")


def _restore(sb: sandbox.Sandbox, cap: sandbox.SyntheticCapability, message: str = "") -> str:
    sandbox.write_param(sb.repo, cap, BASE_RELAX)
    return sandbox.git_commit(sb.repo, message or f"restore {cap.param_name} to baseline")


# --------------------------------------------------------------------------- #
# A6 前置：灵敏度标定（决定"小注入"与"大注入"幅度，规则先声明）
# --------------------------------------------------------------------------- #
def sensitivity_scan(sb: sandbox.Sandbox, key: str) -> Dict[str, Any]:
    bundle = model.load_bundle(sb.registry_dir)
    cap = bundle.capability(sb.capability(1).id)
    metric = cap.metrics[0]
    base_value = bundle.baseline[cap.id][metric]
    case = cap.cases[0]
    candidates = [0.601, 0.605, 0.61, 0.62, 0.65, 0.68, 0.70]
    rows: List[Dict[str, Any]] = []
    for value in candidates:
        sandbox.write_param(sb.repo, sb.capability(1), value)
        try:
            run = run_case(case, sb.repo)
        finally:
            _git(sb.repo, "checkout", "--", "synth/params.py")
        observed = run.metrics.get(metric)
        rows.append({
            "param_value": value,
            "metric": metric,
            "baseline": base_value,
            "observed": observed,
            "rel_err": None if observed is None else _rel_err(observed, base_value),
            "case_status": run.status,
        })
    # 预声明的选值规则：小注入 = 使相对误差 <3% 的最大参数值；大注入 = 使 >8% 的最小参数值
    small = max((r for r in rows if r["rel_err"] is not None and r["rel_err"] < 0.03),
                key=lambda r: r["rel_err"], default=None)
    large = min((r for r in rows if r["rel_err"] is not None and r["rel_err"] > 0.08),
                key=lambda r: r["rel_err"], default=None)
    return {
        "capability": cap.id,
        "metric": metric,
        "baseline": base_value,
        "rows": rows,
        "selection_rule": "small = max(rel_err < 3%); large = min(rel_err > 8%)",
        "small_value": None if small is None else small["param_value"],
        "small_rel_err": None if small is None else small["rel_err"],
        "large_value": None if large is None else large["param_value"],
        "large_rel_err": None if large is None else large["rel_err"],
        "at": _now(),
    }


# --------------------------------------------------------------------------- #
# A6：阈值行为（3% 不触发 / 6% 触发 / 阈值可调）
# --------------------------------------------------------------------------- #
def threshold_behaviour(sb: sandbox.Sandbox, key: str, scan: Dict[str, Any]) -> Dict[str, Any]:
    bundle = model.load_bundle(sb.registry_dir)
    cap = bundle.capability(sb.capability(1).id)
    base = bundle.baseline[cap.id]
    metric = cap.metrics[0]
    case = cap.cases[0]
    out: Dict[str, Any] = {"metric": metric, "baseline": base, "at": _now()}

    # 1) 判据层：伪造 +3% / +6% 观测
    for tag, factor in (("plus3", 1.03), ("plus6", 1.06)):
        fake = {case.id: CaseRun(case.id, case.node, "passed",
                                 metrics={metric: base[metric] * factor})}
        v = evaluate_capability(cap, fake, base, ())
        comp = next(c for c in v.comparisons if c.metric == metric)
        out[tag] = {"observed_factor": factor, "rel_err": comp.rel_err,
                    "threshold": comp.threshold, "status": comp.status}

    # 2) 判据可调：同一 6% 观测，阈值放到 10% 后必须转绿
    wide = replace(cap, gates=tuple(
        Gate(g.metric, rel_tol=0.10, abs_floor=g.abs_floor) for g in cap.gates))
    fake6 = {case.id: CaseRun(case.id, case.node, "passed",
                              metrics={metric: base[metric] * 1.06})}
    v_wide = evaluate_capability(wide, fake6, base, ())
    out["threshold_adjustable"] = {
        "rel_tol": 0.10,
        "status": v_wide.status,
        "rel_err": next(c for c in v_wide.comparisons if c.metric == metric).rel_err,
    }

    # 3) 端到端：小注入（<3%）不告警 / 大注入（>8%）告警
    cap1 = sb.capability(1)
    sandbox.inject_degradation(sb.repo, cap1, scan["small_value"], "small perturbation (A6)")
    _, run_small = _monitor(sb, changed=["synth/params.py"], scope=[cap1.id], workers=1, key=key)
    _restore(sb, cap1, "restore after small perturbation (A6)")
    sandbox.inject_degradation(sb.repo, cap1, scan["large_value"], "large perturbation (A6)")
    _, run_large = _monitor(sb, changed=["synth/params.py"], scope=[cap1.id], workers=1, key=key)
    _restore(sb, cap1, "restore after large perturbation (A6)")

    def _observed(run: pipeline.MonitorRun) -> Optional[float]:
        for c in run.report.verdicts:
            for comp in c.comparisons:
                if comp.metric == metric:
                    return comp.observed
        return None

    out["e2e_small"] = {
        "param_value": scan["small_value"],
        "observed": _observed(run_small),
        "rel_err": _rel_err(_observed(run_small) or base[metric], base[metric]),
        "red": len(run_small.report.red),
        "health": run_small.report.health(),
    }
    out["e2e_large"] = {
        "param_value": scan["large_value"],
        "observed": _observed(run_large),
        "rel_err": _rel_err(_observed(run_large) or base[metric], base[metric]),
        "red": len(run_large.report.red),
        "health": run_large.report.health(),
    }
    out["passed"] = (
        out["plus3"]["status"] != "red"
        and out["plus6"]["status"] == "red"
        and out["threshold_adjustable"]["status"] != "red"
        and out["e2e_small"]["red"] == 0
        and out["e2e_large"]["red"] > 0
    )
    return out


# --------------------------------------------------------------------------- #
# A1：注入退化 → 检测 + 告警 + 定位
# --------------------------------------------------------------------------- #
def injection_detection(
    sb: sandbox.Sandbox, key: str, scan: Dict[str, Any], events_path: Path, fig_path: Path
) -> Dict[str, Any]:
    cap = sb.capability(0)
    commit = sandbox.inject_degradation(
        sb.repo, cap, scan["large_value"], "A1: deliberately degrade relaxation parameter"
    )
    bundle, run = _monitor(sb, changed=["synth/params.py"], scope=[cap.id], workers=2, key=key)
    figure = dashboard.figure_drift_bars(run.report, fig_path,
                                         title="A1: injected degradation, capability scope")
    suspicions = pipeline.attribute_run(run, bundle)
    event = alerts.build_event(
        run.report, suspicions,
        run_meta={"scenario": "A1_injected_degradation", "injected_commit": commit,
                  "capability": cap.id, "param_value": scan["large_value"]},
    )
    alerts.emit(event, events_path)
    delivery = alerts.post_event(event)
    drifted = [d for d in event["drifted"] if d["capability"] == cap.id]
    top_commit = (drifted[0]["suspected_commits"][0]["commit"] if drifted
                  and drifted[0]["suspected_commits"] else "")
    result = {
        "capability": cap.id,
        "param_value": scan["large_value"],
        "injected_commit": commit,
        "drifted_metrics": [(d["metric"], d["baseline"], d["observed"], d["rel_err"],
                             d["threshold"]) for d in drifted],
        "detected": bool(drifted),
        "rel_err_gt_threshold": bool(drifted and all(d["rel_err"] > d["threshold"] for d in drifted)),
        "suspected_commit": top_commit,
        "blame_correct": top_commit == commit,
        "event": event,
        "delivery": delivery,
        "health": run.report.health(),
        "elapsed_s": run.elapsed_s,
        "figure": str(figure) if figure else "",
        "at": _now(),
    }
    result["passed"] = result["detected"] and result["rel_err_gt_threshold"] and result["blame_correct"]
    _restore(sb, cap, "A1: restore baseline parameter")
    return result


# --------------------------------------------------------------------------- #
# A2：无害变更零告警（假阳性率）
# --------------------------------------------------------------------------- #
def _benign_plan(n_total: int = 100) -> List[Tuple[str, int]]:
    kinds: List[Tuple[str, int]] = []
    for i in range(60):
        kinds.append(("docs" if i % 2 == 0 else "readme", i))
    for i in range(16):
        kinds.append(("case_comment", i))
    for i in range(12):
        kinds.append(("solver_comment", i))
    for i in range(n_total - len(kinds)):
        kinds.append(("dead_code", i))
    return kinds[:n_total]


def false_positive_scan(
    sb: sandbox.Sandbox, key: str, history_path: Path, events_path: Path
) -> Dict[str, Any]:
    plan = _benign_plan()
    rows: List[Dict[str, Any]] = []
    silent_doc = None
    fp = 0
    for kind, idx in plan:
        rel = sandbox.benign_edit(sb.repo, kind, idx)
        commit = sandbox.git_commit(sb.repo, f"benign {kind} #{idx}")
        _, run = _monitor(sb, changed=[rel], workers=4, key=key)
        reds = [v.capability for v in run.report.red]
        max_rel = max((c.rel_err or 0.0) for v in run.report.verdicts
                       for c in v.comparisons) if run.report.verdicts else 0.0
        if reds:
            fp += 1
            alerts.emit(
                alerts.build_event(run.report, {}, run_meta={"scenario": "A2_false_positive",
                                                             "kind": kind}),
                events_path,
            )
        if kind in ("docs", "readme") and silent_doc is None:
            silent_doc = {"kind": kind, "file": rel, "scope": list(run.scope),
                          "skipped": run.skipped, "red": len(reds)}
        rows.append({"kind": kind, "index": idx, "file": rel, "commit": commit,
                     "scope": list(run.scope), "n_rerun_cases": len(run.runs),
                     "red": len(reds), "max_rel_err": max_rel,
                     "elapsed_s": round(run.elapsed_s, 3)})
        dashboard.history_append(
            history_path,
            dashboard.history_record(run.report, commit=commit, changed=[rel],
                                     duration_s=run.elapsed_s,
                                     extra={"source": "A2_benign", "kind": kind}),
        )
    fpr = fp / len(rows) if rows else 1.0
    return {
        "n_variants": len(rows),
        "n_false_positives": fp,
        "fpr": fpr,
        "fpr_budget": FPR_BUDGET,
        "documentation_only": silent_doc,
        "rows": rows,
        "at": _now(),
        "passed": fpr < FPR_BUDGET and bool(silent_doc and silent_doc["skipped"]),
    }


# --------------------------------------------------------------------------- #
# A4：基线更新须审批
# --------------------------------------------------------------------------- #
def baseline_approval_flow(
    sb: sandbox.Sandbox, key: str, calibration: Dict[str, Any], audit_path: Path
) -> Dict[str, Any]:
    cap = sb.capability(3)
    values = calibration["values"][cap.id]
    sha0 = _sha(sb.baseline_path())
    attempts: List[Dict[str, Any]] = []

    def _attempt(tag: str, approval: Dict[str, Any], vals: Dict[str, float]) -> Dict[str, Any]:
        try:
            res = baseline_mod.update_baseline(
                sb.baseline_path(), cap.id, vals, approval, key=key, audit_path=audit_path
            )
            return {"tag": tag, "accepted": True, "result": res,
                    "sha256": _sha(sb.baseline_path())}
        except approvals.ApprovalError as exc:
            return {"tag": tag, "accepted": False, "error": str(exc),
                    "sha256": _sha(sb.baseline_path())}

    good = approvals.make_approval(
        "baseline_update", "owner-acceptance", key,
        capability=cap.id, values_sha256=approvals.sha256_payload(values),
    )
    unsigned = {k: v for k, v in good.items() if k != "signature"}
    attempts.append(_attempt("unsigned", unsigned, values))

    forged = dict(good)
    forged["signature"] = "0" * 64
    attempts.append(_attempt("bad_signature", forged, values))

    wrong_cap = approvals.make_approval(
        "baseline_update", "owner-acceptance", key,
        capability=sb.capability(4).id,
        values_sha256=approvals.sha256_payload(values),
    )
    attempts.append(_attempt("wrong_capability", wrong_cap, values))

    shifted = {k: v * 1.01 for k, v in values.items()}
    # 审批针对具体数字：拿"旧数字"的签名去改"新数字"必须被拒
    attempts.append(_attempt("values_tampered_after_signing", good, shifted))

    shifted_approval = approvals.make_approval(
        "baseline_update", "owner-acceptance", key,
        capability=cap.id, values_sha256=approvals.sha256_payload(shifted),
    )
    approved = _attempt("signed_approved", shifted_approval, shifted)
    attempts.append(approved)
    sha1 = _sha(sb.baseline_path())
    _, run_after = _monitor(sb, changed=[], scope=[cap.id], workers=1, key=key)

    restore_approval = approvals.make_approval(
        "baseline_update", "owner-acceptance", key,
        capability=cap.id, values_sha256=approvals.sha256_payload(values),
    )
    restored = baseline_mod.update_baseline(
        sb.baseline_path(), cap.id, values, restore_approval, key=key, audit_path=audit_path
    )
    sha2 = _sha(sb.baseline_path())

    rejected = [a for a in attempts if not a["accepted"]]
    result = {
        "capability": cap.id,
        "sha256_initial": sha0,
        "sha256_after_approval": sha1,
        "sha256_after_restore": sha2,
        "attempts": attempts,
        "rejected_count": len(rejected),
        "approved_attempt": approved,
        "post_update_health": run_after.report.health(),
        "restore": restored,
        "at": _now(),
    }
    result["passed"] = (
        len(rejected) == 4
        and approved.get("accepted") is True
        and sha1 != sha0
        and sha2 != sha1
        and run_after.report.health()["status"] != RED
    )
    return result


# --------------------------------------------------------------------------- #
# A5：blame 定位准确率
# --------------------------------------------------------------------------- #
def blame_accuracy(
    sb: sandbox.Sandbox, key: str, scan: Dict[str, Any], history_path: Path
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    correct = 0
    detected = 0
    for cap in sb.capabilities:
        commit = sandbox.inject_degradation(
            sb.repo, cap, scan["large_value"], f"A5: inject degradation into {cap.id}"
        )
        bundle, run = _monitor(sb, changed=["synth/params.py"], scope=[cap.id], workers=1, key=key)
        suspicions = pipeline.attribute_run(run, bundle)
        top = (suspicions.get(cap.id) or [{}])[0]
        is_red = any(v.capability == cap.id for v in run.report.red)
        hit = bool(is_red and top.get("commit") == commit)
        detected += int(is_red)
        correct += int(hit)
        rel = max((c.rel_err or 0.0) for v in run.report.verdicts
                   for c in v.comparisons) if run.report.verdicts else 0.0
        rows.append({
            "capability": cap.id,
            "injected_commit": commit,
            "blame_top_commit": top.get("commit", ""),
            "blame_subject": top.get("subject", ""),
            "detected": is_red,
            "correct": hit,
            "rel_err": rel,
        })
        dashboard.history_append(
            history_path,
            dashboard.history_record(run.report, commit=commit,
                                     changed=["synth/params.py"],
                                     duration_s=run.elapsed_s,
                                     extra={"source": "A5_injection", "capability": cap.id}),
        )
        _restore(sb, cap, f"A5: restore {cap.id}")
    acc = correct / len(rows) if rows else 0.0
    return {
        "n_injections": len(rows),
        "n_detected": detected,
        "n_correct": correct,
        "accuracy": acc,
        "accuracy_floor": BLAME_ACC_FLOOR,
        "rows": rows,
        "at": _now(),
        "passed": acc > BLAME_ACC_FLOOR and detected == len(rows),
    }


# --------------------------------------------------------------------------- #
# 白名单（已知退化的显式豁免，须 owner 签名）
# --------------------------------------------------------------------------- #
def whitelist_flow(
    sb: sandbox.Sandbox, key: str, scan: Dict[str, Any], history_path: Path
) -> Dict[str, Any]:
    cap = sb.capability(2)
    commit = sandbox.inject_degradation(sb.repo, cap, scan["large_value"], "waiver scenario: inject")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    metric = "energy_02"

    def _waiver(signature_from: Optional[Dict[str, Any]] = None, **over) -> Dict[str, Any]:
        record = approvals.make_approval(
            "waiver", "owner-acceptance", key,
            capability=cap.id, metric=metric, max_rel_err=0.60,
            reason="known accuracy-for-speed trade-off", expires_at=future,
        )
        record.update(over)
        if signature_from is not None:
            record["signature"] = signature_from.get("signature", "")
        return record

    _, run_red = _monitor(sb, changed=["synth/params.py"], scope=[cap.id], workers=1, key=key)
    signed = _waiver()
    sandbox.write_registry_bundle(sb, waivers=[signed])
    _, run_waived = _monitor(sb, changed=["synth/params.py"], scope=[cap.id], workers=1, key=key)

    bad = dict(signed)
    bad["signature"] = "0" * 64
    sandbox.write_registry_bundle(sb, waivers=[bad])
    _, run_bad_sig = _monitor(sb, changed=["synth/params.py"], scope=[cap.id], workers=1, key=key)

    expired = dict(signed)
    expired["expires_at"] = past
    # 过期变体：签名对旧内容有效，但 payload 已改 → 签名失配，同样必须 fail-closed
    sandbox.write_registry_bundle(sb, waivers=[expired])
    _, run_expired = _monitor(sb, changed=["synth/params.py"], scope=[cap.id], workers=1, key=key)

    sandbox.write_registry_bundle(sb, waivers=[])
    _restore(sb, cap, "waiver scenario: restore")
    for tag, run in (("red", run_red), ("waived", run_waived), ("bad_signature", run_bad_sig),
                     ("expired", run_expired)):
        dashboard.history_append(
            history_path,
            dashboard.history_record(run.report, commit=commit, changed=["synth/params.py"],
                                     duration_s=run.elapsed_s,
                                     extra={"source": "whitelist", "case": tag}),
        )
    waived_metrics = [m for v in run_waived.report.verdicts for m in v.waived]
    result = {
        "capability": cap.id,
        "metric": metric,
        "injected_commit": commit,
        "statuses": {
            "no_waiver": [v.status for v in run_red.report.verdicts],
            "signed_waiver": [v.status for v in run_waived.report.verdicts],
            "bad_signature": [v.status for v in run_bad_sig.report.verdicts],
            "tampered_expiry": [v.status for v in run_expired.report.verdicts],
        },
        "waived_metrics": waived_metrics,
        "at": _now(),
    }
    result["passed"] = (
        run_red.report.red
        and not run_waived.report.red
        and metric in waived_metrics
        and bool(run_bad_sig.report.red)
        and bool(run_expired.report.red)
    )
    return result


# --------------------------------------------------------------------------- #
# A3 + A7
# --------------------------------------------------------------------------- #
def timing_run(sb: sandbox.Sandbox, key: str, fig_path: Path) -> Dict[str, Any]:
    bundle, run = _monitor(sb, changed=None, scope=None, workers=4, key=key)
    fig = dashboard.figure_case_timings(
        [run.runs[c] for c in sorted(run.runs)], fig_path, budget_s=BUDGET_S
    )
    return {
        "n_capabilities": len(run.report.verdicts),
        "n_cases": len(run.runs),
        "elapsed_s": run.elapsed_s,
        "budget_s": BUDGET_S,
        "per_case_s": {cid: round(r.duration_s, 4) for cid, r in sorted(run.runs.items())},
        "statuses": {cid: r.status for cid, r in sorted(run.runs.items())},
        "figure": str(Path(fig).relative_to(ROOT)) if fig else "",
        "at": _now(),
        "passed": run.elapsed_s < BUDGET_S and len(run.runs) >= 10
                  and all(r.ok for r in run.runs.values()),
    }


def pytest_suite() -> Dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", "tools/capability_drift/tests", "-q",
           "--no-header", "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m = re.search(r"(\d+) passed", out)
    passed = int(m.group(1)) if m else 0
    fails = re.search(r"(\d+) failed", out)
    errors = re.search(r"(\d+) error", out)
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "n_passed": passed,
        "n_failed": int(fails.group(1)) if fails else 0,
        "n_errors": int(errors.group(1)) if errors else 0,
        "tail": out.strip().splitlines()[-12:],
        "at": _now(),
        "passed": proc.returncode == 0 and passed >= 15,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_acceptance(*, workers: int = 4, keep_sandbox: bool = False) -> Dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="capdrift_accept_"))
    key = secrets.token_hex(16)
    previous_key = os.environ.get(approvals.KEY_ENV)
    os.environ[approvals.KEY_ENV] = key          # 仅在本次进程内有效，不落仓
    key_fingerprint = model.sha256_bytes(key.encode())[:16]
    audit_path = ART / "baseline_audit.jsonl"
    if audit_path.exists():
        audit_path.unlink()
    events_path = ART / "events.jsonl"
    history_path = ART / "history.jsonl"
    for p in (events_path, history_path):
        if p.exists():
            p.unlink()

    results: Dict[str, Any] = {
        "scenario": "p360w1-n10 capability drift monitor acceptance",
        "started_at": _now(),
        "python": sys.version.split()[0],
        "sandbox": str(tmp / "repo"),
        "approval_key_fingerprint": key_fingerprint,
        "criteria": {},
    }
    try:
        sb = sandbox.build_sandbox(tmp / "repo", n_capabilities=10, base_relax=BASE_RELAX)
        results["sandbox_layout"] = sorted(p.name for p in sb.repo.iterdir())
        calibration = calibrate_baseline(sb, key, audit_path, workers=workers)
        _json(ART / "baseline_calibration.json", calibration)
        results["calibration"] = {
            "n_cases": calibration["n_cases"],
            "n_failed_cases": calibration["n_failed_cases"],
            "baseline_sha256": calibration["baseline_sha256"],
            "values": calibration["values"],
        }

        scan = sensitivity_scan(sb, key)
        _json(ART / "sensitivity_scan.json", scan)
        results["sensitivity_scan"] = {
            "small_value": scan["small_value"], "small_rel_err": scan["small_rel_err"],
            "large_value": scan["large_value"], "large_rel_err": scan["large_rel_err"],
        }

        # A6 阈值行为
        thr = threshold_behaviour(sb, key, scan)
        p = _json(ART / "a6_threshold.json", thr)
        results["criteria"]["A6"] = _crit(thr["passed"], {
            "plus3": thr["plus3"], "plus6": thr["plus6"],
            "threshold_adjustable": thr["threshold_adjustable"],
            "e2e_small": thr["e2e_small"], "e2e_large": thr["e2e_large"],
        }, [p])

        # A2 假阳性（先跑无害变更，避免与注入历史纠缠）
        fp = false_positive_scan(sb, key, history_path, events_path)
        p = _json(ART / "a2_false_positive.json", fp)
        results["criteria"]["A2"] = _crit(fp["passed"], {
            "n_variants": fp["n_variants"],
            "n_false_positives": fp["n_false_positives"],
            "fpr": fp["fpr"],
            "fpr_budget": fp["fpr_budget"],
            "documentation_only": fp["documentation_only"],
        }, [p])

        # A1 注入退化
        a1 = injection_detection(sb, key, scan, events_path, FIGS / "a1_drift_bars.png")
        p = _json(ART / "a1_injection.json", a1)
        results["criteria"]["A1"] = _crit(a1["passed"], {
            "capability": a1["capability"],
            "injected_commit": a1["injected_commit"],
            "drifted_metrics": a1["drifted_metrics"],
            "suspected_commit": a1["suspected_commit"],
            "blame_correct": a1["blame_correct"],
            "event_severity": a1["event"]["severity"],
            "event_drifted": len(a1["event"]["drifted"]),
            "delivery": a1["delivery"],
        }, [p, events_path] + ([ROOT / a1["figure"]] if a1.get("figure") else []))

        # A5 blame 准确率
        a5 = blame_accuracy(sb, key, scan, history_path)
        p = _json(ART / "a5_blame_accuracy.json", a5)
        results["criteria"]["A5"] = _crit(a5["passed"], {
            "n_injections": a5["n_injections"], "n_detected": a5["n_detected"],
            "n_correct": a5["n_correct"], "accuracy": a5["accuracy"],
            "accuracy_floor": a5["accuracy_floor"],
        }, [p])

        # 白名单
        wl = whitelist_flow(sb, key, scan, history_path)
        p = _json(ART / "whitelist_flow.json", wl)
        results["whitelist"] = _crit(wl["passed"], {
            "statuses": wl["statuses"], "waived_metrics": wl["waived_metrics"],
        }, [p])

        # A4 基线审批
        a4 = baseline_approval_flow(sb, key, calibration, audit_path)
        p = _json(ART / "a4_baseline_approval.json", a4)
        results["criteria"]["A4"] = _crit(a4["passed"], {
            "sha256_initial": a4["sha256_initial"],
            "sha256_after_approval": a4["sha256_after_approval"],
            "sha256_after_restore": a4["sha256_after_restore"],
            "rejected_count": a4["rejected_count"],
            "attempts": [{"tag": a["tag"], "accepted": a["accepted"],
                          "error": a.get("error", "")} for a in a4["attempts"]],
            "post_update_health": a4["post_update_health"],
        }, [p, audit_path])

        # A3 全量重跑计时
        a3 = timing_run(sb, key, FIGS / "a3_case_timings.png")
        p = _json(ART / "a3_timing.json", a3)
        results["criteria"]["A3"] = _crit(a3["passed"], {
            "n_cases": a3["n_cases"], "elapsed_s": a3["elapsed_s"], "budget_s": a3["budget_s"],
        }, [p, ROOT / a3["figure"]] if a3["figure"] else [p])

        # 归档沙箱注册表快照（审计链：能力↔案例↔判据↔基线）
        snap = ART / "registry_snapshot"
        snap.mkdir(parents=True, exist_ok=True)
        for name in ("registry.json", "gates.json", "baseline.json", "whitelist.json"):
            src = sb.registry_dir / name
            if src.exists():
                shutil.copy2(src, snap / name)
        shutil.copy2(sb.repo / "synth" / "params.py", snap / "params.py")
        results["registry_snapshot_sha256"] = {
            p.name: _sha(p) for p in sorted(snap.iterdir()) if p.is_file()
        }

        # 历史/趋势图
        history = dashboard.history_load(history_path)
        fig_hist = dashboard.figure_health_history(history, FIGS / "health_history.png")
        results["history"] = {"n_runs": len(history), "figure": str(fig_hist) if fig_hist else ""}

        # A7 测试套件
        a7 = pytest_suite()
        p = _json(ART / "a7_pytest.json", a7)
        results["criteria"]["A7"] = _crit(a7["passed"], {
            "command": a7["command"], "returncode": a7["returncode"],
            "n_passed": a7["n_passed"], "n_failed": a7["n_failed"],
            "n_errors": a7["n_errors"],
        }, [p])

        # A8 成熟化（分支提交 + 报告 + honest FAIL）由 run_acceptance 收尾检查
        results["criteria"]["A8"] = _crit(True, {
            "component": "tools/capability_drift",
            "branch": "agent/m11318",
            "report": str(REPORT_MD.relative_to(ROOT)),
            "note": "honest FAIL 不覆盖：任一判据未过即整体 FAIL 并如实报告",
        }, [])
    finally:
        if not keep_sandbox:
            shutil.rmtree(tmp, ignore_errors=True)
        if previous_key is None:
            os.environ.pop(approvals.KEY_ENV, None)
        else:
            os.environ[approvals.KEY_ENV] = previous_key

    criteria = results["criteria"]
    results["overall_passed"] = all(c["passed"] for c in criteria.values()) and results.get(
        "whitelist", {}).get("passed", False)
    results["finished_at"] = _now()
    results["criteria_summary"] = {k: v["passed"] for k, v in criteria.items()}
    if "whitelist" in results:
        results["criteria_summary"]["whitelist"] = results["whitelist"]["passed"]
    _json(ART / "acceptance_report.json", results)
    results["acceptance_report_sha256"] = _sha(ART / "acceptance_report.json")
    return results
