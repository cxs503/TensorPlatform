"""清偿流水线与成熟化门 C1..C7。

输入：case 账本（corpus）+ 运行时（可选注入）。输出：逐案清偿判决 + 复现率 + 门控结果。
全部判决都是数据的函数（无时间、无随机、无外部 IO），因此同输入两次跑必得同 sha256（C6）。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from tools.repro_settlement.attribution import (LABELS, UNATTRIBUTED, classify,
                                                richardson_residual)
from tools.repro_settlement.canonical import sha256_obj, stable_digest
from tools.repro_settlement.envlock import (build_lock, completeness, integrity,
                                            live_gap, verify_lock)

SCHEMA = "repro-settlement/report/1"
PASS_LINE = 0.95
MIN_TESTS = 25
AUDIT_MIN_AGREEMENT = 0.80
REQUIRED_ARCHES = ("x86_64", "loongarch64")
GATES: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5", "C6", "C7")

#: 计入复现率的判决（清偿后）
COUNTED_PASS = ("pass", "blocked_settled", "drift_rebaselined")
#: 仍计入缺口的判决
COUNTED_GAP = ("blocked", "drift_open", "drift_unattributed", "error")


def _norm(status: str) -> str:
    s = str(status).strip().lower()
    if s in ("pass", "passed", "ok"):
        return "pass"
    if s in ("drift", "mismatch", "numeric_mismatch"):
        return "drift"
    if s in ("blocked", "environment_error", "dependency_missing", "timeout"):
        return "blocked"
    return "error"


def settle_case(case: Mapping[str, Any], *, runtime: Mapping[str, Any] | None = None,
                tol: float = 1e-9) -> dict[str, Any]:
    """单案清偿：blocked→环境锁可复现性；drift→四路归因 + 重基线条件。"""
    cid = case["case_id"]
    status = _norm(case.get("status"))
    out: dict[str, Any] = {
        "case_id": cid,
        "status_before": status,
        "label": None,
        "lock_sha256": None,
        "fix": None,
        "fail_source": case.get("fail_source"),
        "notes": [],
    }
    if status == "pass":
        out["verdict"] = "pass"
        return out

    if status == "blocked":
        spec = dict(case.get("blocked") or {})
        lock = spec.get("lock")
        if lock is None:
            lock = build_lock(cid, pins=spec.get("pins", ()),
                              solver_commit=spec.get("solver_commit"),
                              solver_version=spec.get("solver_version"),
                              runtime=runtime)
        out["lock_sha256"] = lock.get("sha256")
        check = verify_lock(lock, runtime)
        issues = completeness(lock)
        ok = integrity(lock) and not issues
        out["lock_status"] = check["status"]
        out["lock_missing"] = check["missing"]
        out["lock_completeness"] = issues
        out["live_gap"] = live_gap(lock)
        out["lock_schema"] = lock.get("schema")
        if ok:
            out["verdict"] = "blocked_settled"
            out["fix"] = ("环境锁已重建（完整性通过）：按锁钉 python/架构/求解器 commit/依赖版本重跑；"
                          f"锁指纹 {str(lock.get('sha256'))[:12]}；本机缺口 {out['live_gap'] or '无'}")
        else:
            out["verdict"] = "blocked"
            out["fix"] = ("锁不完整或不可校验（完整性 "
                          f"{issues or 'ok'} / 目标环境 {check['status']}）：补齐后重新生成锁")
        return out

    if status == "drift":
        spec = dict(case.get("drift") or {})
        verdict = classify(spec.get("evidence"), tol=tol)
        out.update({"label": verdict["label"], "confidence": verdict["confidence"],
                    "signals": verdict["signals"], "fix": verdict["fix"],
                    "delta_rel": spec.get("delta_rel"),
                    "tolerance": spec.get("tolerance")})
        delta, tolv = spec.get("delta_rel"), spec.get("tolerance")
        resid = delta
        corr = dict(spec.get("correction") or {})
        if corr.get("method") == "richardson" and isinstance(delta, (int, float)):
            resid = richardson_residual(delta, corr.get("ratio"), corr.get("order"))
            out["residual_rel"] = round(resid, 9)
            out["correction"] = corr
        within = (isinstance(resid, (int, float)) and isinstance(tolv, (int, float))
                  and abs(resid) <= abs(tolv))
        rebaselined = bool(spec.get("rebaselined")) and verdict["label"] in LABELS
        if verdict["label"] == UNATTRIBUTED:
            out["verdict"] = "drift_unattributed"
            out["notes"].append("归因证据不足，不猜测，送人审")
        elif verdict["label"] == "d_baseline_version" and rebaselined:
            out["verdict"] = "drift_rebaselined"
            out["notes"].append("基准数据版本漂移 → 记录值失效，重基线入账（留痕）")
        elif rebaselined and within:
            out["verdict"] = "drift_rebaselined"
            out["notes"].append("已归因且残差在声明容差内 → 重基线入账（留痕）")
        elif within:
            out["verdict"] = "drift_rebaselined"
            out["notes"].append("已归因且残差在声明容差内（未显式重基线，按容差入账）")
        else:
            out["verdict"] = "drift_open"
            out["notes"].append("已归因但残差超容差 → 仍计缺口，附修复建议")
        return out

    out["verdict"] = "error"
    return out


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    if key == "before":
        pass_n = sum(1 for r in rows if r["status_before"] == "pass")
    else:
        pass_n = sum(1 for r in rows if r["verdict"] in COUNTED_PASS)
    total = len(rows)
    return None if total == 0 else round(pass_n / total, 6)


def _arch_gate(ci: Mapping[str, Any] | None) -> dict[str, Any]:
    ci = dict(ci or {})
    archs = dict(ci.get("architectures") or {})
    bad = [a for a in REQUIRED_ARCHES if str(archs.get(a, "")).lower() not in ("pass", "ok", "green")]
    return {"ok": not bad, "missing_or_failing": bad,
            "architectures": {a: archs.get(a, "absent") for a in REQUIRED_ARCHES}}


def settle(corpus: Mapping[str, Any], *, runtime: Mapping[str, Any] | None = None,
           tol: float = 1e-9, previous_digest: str | None = None) -> dict[str, Any]:
    """跑全量清偿，返回报告（含 C1..C7 门控）。"""
    cases = list(corpus.get("cases") or [])
    rows = [settle_case(c, runtime=runtime, tol=tol) for c in cases]

    before = _rate(rows, "before")
    after = _rate(rows, "after")
    blocked_rows = [r for r in rows if r["status_before"] == "blocked"]
    drift_rows = [r for r in rows if r["status_before"] == "drift"]
    nonpass_now = [r for r in rows if r["verdict"] not in COUNTED_PASS]

    gates: dict[str, dict[str, Any]] = {}
    gates["C1"] = {"ok": after is not None and after >= PASS_LINE,
                   "value": after, "threshold": PASS_LINE, "before": before}
    ok_c2 = all(r.get("lock_sha256") and not r.get("lock_completeness") for r in blocked_rows)
    gates["C2"] = {"ok": bool(blocked_rows) and ok_c2, "n_blocked": len(blocked_rows),
                   "locked": sum(1 for r in blocked_rows if r.get("lock_sha256")),
                   "complete": sum(1 for r in blocked_rows if not r.get("lock_completeness")),
                   "target_env_verified": sum(1 for r in blocked_rows
                                              if r.get("lock_status") in ("exact", "patch_drift")),
                   "live_reproducible": sum(1 for r in blocked_rows if not r.get("live_gap")),
                   "live_gaps": {r["case_id"]: r.get("live_gap") for r in blocked_rows
                                 if r.get("live_gap")}}
    ok_c3 = all(r.get("label") in LABELS for r in drift_rows)
    gates["C3"] = {"ok": bool(drift_rows) and ok_c3, "n_drift": len(drift_rows),
                   "labelled": sum(1 for r in drift_rows if r.get("label") in LABELS),
                   "distribution": {lab: sum(1 for r in drift_rows if r.get("label") == lab)
                                    for lab in LABELS}}
    ci = dict(corpus.get("ci") or {})
    arch = _arch_gate(ci)
    n_tests = int(ci.get("pytest_count") or 0)
    gates["C4"] = {"ok": n_tests >= MIN_TESTS and arch["ok"], "pytest_count": n_tests,
                   "min_tests": MIN_TESTS, "arches": arch}

    sample = list(corpus.get("audit_sample") or [])
    by_id = {r["case_id"]: r for r in rows}
    agree = [s for s in sample if by_id.get(s.get("case_id"), {}).get("label") == s.get("human_label")]
    agreement = None if not sample else round(len(agree) / len(sample), 6)
    gates["C5"] = {"ok": agreement is not None and agreement >= AUDIT_MIN_AGREEMENT,
                   "agreement": agreement, "n_sampled": len(sample),
                   "threshold": AUDIT_MIN_AGREEMENT,
                   "mismatches": [s.get("case_id") for s in sample
                                  if by_id.get(s.get("case_id"), {}).get("label") != s.get("human_label")]}

    report_core = {"schema": SCHEMA, "cases": rows,
                   "repro_rate_before": before, "repro_rate_after": after,
                   "gates": {k: v for k, v in gates.items() if k != "C6"}}
    digest = stable_digest(report_core)
    gates["C6"] = {"ok": previous_digest is None or previous_digest == digest,
                   "digest": digest, "previous_digest": previous_digest}
    gates["C7"] = {"ok": all(r.get("fail_source") for r in nonpass_now),
                   "open_cases": [r["case_id"] for r in nonpass_now],
                   "unsourced": [r["case_id"] for r in nonpass_now if not r.get("fail_source")],
                   "matrix_rows": len(rows), "cases": len(rows)}

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {
        "schema": SCHEMA,
        "corpus_id": corpus.get("corpus_id"),
        "corpus_sha256": sha256_obj(corpus),
        "n_cases": len(rows),
        "cases": rows,
        "counts_before": {s: sum(1 for r in rows if r["status_before"] == s)
                          for s in ("pass", "blocked", "drift", "error")},
        "counts_after": dict(sorted(counts.items())),
        "repro_rate_before": before,
        "repro_rate_after": after,
        "gates": gates,
        "all_gates_pass": all(v["ok"] for v in gates.values()),
        "digest": digest,
    }
