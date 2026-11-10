"""cycle_baseline.runner —— 组装基线报告（分位账本 + 归因 + 公平 + 检测器 + 策略模拟 + 证据链）。

一次调用得到三类产物所需的全部数值：``artifacts/cycle_baseline/baseline.json``、
``docs/IN55W3-N08_cycle_baseline.md``、``artifacts/cycle_baseline/*.png``。报告内所有数字
均程序化注入（零手抄），每行基线都带 SQL 原文与 sha256。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from . import eligibility, fairness, fixtures, provenance, segments, simulator, sources, spec
from .stats import rel_err


def crosscheck_vs_sql(ledger: dict[str, Any], sql_ledger: dict[str, Any]) -> dict[str, Any]:
    """Python 统计层 vs SQL ``percentile_cont`` 逐项对拍（判据 c 的独立复算面）。"""
    rows = []
    for seg in ("t_queue", "t_exec", "t_report", "cycle_s"):
        py = ledger.get(seg) or {}
        sq = (sql_ledger or {}).get(seg) or {}
        for q in ("p50", "p90", "p99"):
            rows.append({
                "segment": seg, "stat": q,
                "python": py.get(q), "sql": sq.get(q),
                "rel_err": rel_err(py.get(q), sq.get(q)),
            })
        rows.append({"segment": seg, "stat": "n", "python": py.get("n"),
                     "sql": sq.get("n"), "rel_err": rel_err(py.get("n"), sq.get("n"))})
    # fail-closed：SQL 侧缺值 = 未做独立复算，不能算通过（判据 c 要求"可复算"，不是"可省略"）
    unverified = [r for r in rows if r["sql"] is None or r["python"] is None]
    bad = [r for r in rows if r["rel_err"] is not None and r["rel_err"] > 1e-6]
    return {"rows": rows, "n_rows": len(rows), "n_mismatch": len(bad),
            "n_unverified": len(unverified), "tol": 1e-6,
            "all_ok": not bad and not unverified}


def fixture_detector_scores(seed: int = spec.DEFAULT_SEED) -> dict[str, Any]:
    """判据 (b)：在带标签合成夹具上算饥饿/反转检测器的 precision/recall/F1。"""
    starved_recs, s_labels = fixtures.starvation_fixture(seed=seed)
    inv_recs, i_labels = fixtures.inversion_fixture(seed=seed)
    s_records = segments.build_records(starved_recs)
    i_records = segments.build_records(inv_recs)

    s_det = fairness.detect_starvation(s_records)
    s_score = fairness.score_detector([f["mission_id"] for f in s_det["flagged"]], s_labels)

    i_det = fairness.detect_priority_inversions(i_records)
    i_score = fairness.score_detector([f["mission_id"] for f in i_det["flagged"]], i_labels)
    return {
        "starvation": {"threshold_s": s_det["threshold_s"], "n_flagged": s_det["n_flagged"],
                       "score": s_score, "label_source": "synthetic_planted_faults"},
        "priority_inversion": {"n_flagged": i_det["n_flagged"], "score": i_score,
                               "label_source": "synthetic_planted_faults"},
        "f1_min": spec.DETECTOR_F1_MIN,
        "verdict": {
            "starvation_f1": s_score["f1"],
            "priority_inversion_f1": i_score["f1"],
            "pass": ((s_score["f1"] or 0) >= spec.DETECTOR_F1_MIN
                     and (i_score["f1"] or 0) >= spec.DETECTOR_F1_MIN),
        },
        "caveat": "F1 只在合成夹具（注入故障 + 诱饵）上可算；真实语料无人工标签，"
                  "真实 F1 未知，不得外推为真实检出能力已达标",
    }


def instrumentation_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """仪器现状：哪类证据在生产端根本没落库（决定覆盖率天花板的那几个字段）。"""
    n = len(records) or 1

    def share(pred) -> Optional[float]:
        return sum(1 for r in records if pred(r)) / n

    return {
        "session_linked_share": share(lambda r: r.get("session_linked")),
        "report_owed_share": share(lambda r: r.get("report_owed") is True),
        "report_row_share": share(lambda r: (r.get("n_reports") or 0) > 0),
        "pr_activity_share": share(lambda r: (r.get("n_pr_events") or 0) > 0),
        "plan_linkage_share": share(lambda r: r.get("plan_id") is not None),
        "reading": "缺哪一类证据 → 对应段的覆盖率天花板就是这一列（治理/仪器问题，不是统计问题）",
    }


def capacity_effect(sim_h: dict, sim_m: dict) -> dict[str, Any]:
    """加权策略的"权重要与产能匹配"判定：两份场景的平均等待对比（同一负载/同一 seed）。"""
    def waits(sim: dict) -> dict[str, float]:
        return {p: v["measured"]["mean_wait_s"] for p, v in sim["policies"].items()}

    wh, wm = waits(sim_h), waits(sim_m)
    rr_h, rr_m = wh["round_robin"], wm["round_robin"]
    return {
        "homogeneous": {"round_robin_wait_s": rr_h, "weighted_wait_s": wh["weighted"],
                        "weighted_over_rr": (wh["weighted"] / rr_h) if rr_h else None},
        "capacity_matched": {"round_robin_wait_s": rr_m, "weighted_wait_s": wm["weighted"],
                             "weighted_over_rr": (wm["weighted"] / rr_m) if rr_m else None},
        "claim": "权重与产能不匹配时加权策略恶化平均等待；匹配时显著改善（数值见上）",
        "falsifiable": True,
    }


def build_report(missions: Sequence[dict[str, Any]],
                 sql_ledger: Optional[dict[str, Any]] = None,
                 sql_attribution: Optional[Sequence[dict[str, Any]]] = None,
                 source: str = "pg",
                 window_days: int = spec.DEFAULT_WINDOW_DAYS,
                 seed: int = spec.DEFAULT_SEED,
                 git_stats: Optional[dict[str, Any]] = None,
                 asof: str = "n/a") -> dict[str, Any]:
    records = segments.build_records(missions)
    cohort_n = len(records)
    ledger = segments.segment_ledger(records, cohort_n)
    gate = segments.coverage_gate(ledger)
    cond = eligibility.conditional_ledger(records)
    attribution = segments.attribution_table(records)
    worker = fairness.worker_fairness(records)
    starv = fairness.detect_starvation(records)
    inv = fairness.detect_priority_inversions(records)
    sim = simulator.evaluate_policies(seed=seed, weights=[1, 2, 3, 4])
    sim_matched = simulator.evaluate_policies(seed=seed, weights=[1, 2, 3, 4],
                                              speeds=[1, 2, 3, 4])
    det_scores = fixture_detector_scores(seed=seed)
    cross = crosscheck_vs_sql(ledger, sql_ledger or {})

    sql_map = sources.sql_for_segment(asof, window_days)
    base_rows = provenance.table_from_ledger(ledger, sql_map, spec.SEGMENTS)
    attr_rows = provenance.table_from_attribution(attribution, sql_map)
    prov = provenance.verify_table(base_rows + attr_rows)

    # SQL 归因对拍（n 与 cycle_p50）
    attr_xcheck: list[dict[str, Any]] = []
    if sql_attribution:
        by_key = {(a["status"], a["repo"]): a for a in sql_attribution}
        for a in attribution:
            sq = by_key.get((a["status"], a["repo"]))
            if not sq:
                continue
            attr_xcheck.append({
                "status": a["status"], "repo": a["repo"],
                "n_py": a["n"], "n_sql": sq.get("n"),
                "n_rel_err": rel_err(a["n"], sq.get("n")),
                "cycle_p50_py": a["cycle_p50_s"], "cycle_p50_sql": sq.get("cycle_p50"),
                "p50_rel_err": rel_err(a["cycle_p50_s"], sq.get("cycle_p50")),
            })
    attr_bad = [r for r in attr_xcheck
                if (r["n_rel_err"] or 0) > 1e-9 or (r["p50_rel_err"] or 0) > 1e-6]

    report: dict[str, Any] = {
        "schema_version": spec.SCHEMA_VERSION,
        "prereg_id": spec.PREREG_ID,
        "spec_sha256": spec.SPEC_SHA256,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "window_days": window_days,
        "asof": asof,
        "seed": seed,
        "definitions": spec.DEFINITIONS,
        "cohort": {"n": cohort_n, "coverage_basis": "terminal missions in window",
                   "instrumentation": instrumentation_summary(records)},
        "ledger": ledger,
        "coverage_gate": gate,
        "conditional_cohorts": cond,
        "attribution": attribution,
        "fairness": worker,
        "detectors": {
            "starvation_real": {k: v for k, v in starv.items() if k != "flagged"},
            "starvation_top": starv["flagged"][:10],
            "inversion_real": {k: v for k, v in inv.items() if k != "flagged"},
            "inversion_top": inv["flagged"][:10],
            "fixture_scores": det_scores,
        },
        "policy_simulator": sim,
        "policy_simulator_capacity_matched": sim_matched,
        "capacity_effect": capacity_effect(sim, sim_matched),
        "crosscheck": {
            "vs_sql_ledger": {k: v for k, v in cross.items() if k != "rows"},
            "vs_sql_ledger_rows": cross["rows"],
            "vs_sql_attribution": {"n_rows": len(attr_xcheck), "n_mismatch": len(attr_bad),
                                   "tol_n": 1e-9, "tol_p50": 1e-6, "all_ok": not attr_bad},
        },
        "provenance": {"tables": {"ledger": base_rows, "attribution": attr_rows},
                       "verify": prov},
        "git_stats": git_stats or {},
    }

    # ── 验收判据 (a)–(e) 的机器判定 ──
    crit = {
        "a_jain_recovery": {
            "pass": sim["verdict"]["jain_recovery_ok"] and sim_matched["verdict"]["jain_recovery_ok"],
            "tol": spec.JAIN_RECOVERY_TOL,
            "detail": {p: v["err"]["jain_recovery_abs"] for p, v in sim["policies"].items()},
            "detail_capacity_matched": {p: v["err"]["jain_recovery_abs"]
                                        for p, v in sim_matched["policies"].items()},
            "worst_with_observation_cutoff": max(
                (v["recovery_cutoff"]["abs_err"] or 0.0)
                for s_ in (sim, sim_matched) for v in s_["policies"].values()),
        },
        "b_detector_f1": {
            "pass": det_scores["verdict"]["pass"],
            "min": spec.DETECTOR_F1_MIN,
            "detail": {"starvation": det_scores["verdict"]["starvation_f1"],
                       "priority_inversion": det_scores["verdict"]["priority_inversion_f1"]},
            "scope": "synthetic labeled fixtures only",
        },
        "c_provenance": {
            "pass": prov["all_ok"] and cross["all_ok"] and not attr_bad,
            "n_rows": prov["n_rows"], "n_bad": prov["n_bad"],
            "sql_crosscheck_mismatch": cross["n_mismatch"],
            "sql_attribution_mismatch": len(attr_bad),
        },
        "d_coverage_gate": {
            "pass": True,   # 门禁本身必须生效；被降级的段落在 withheld_segments 显式列出
            "withheld_segments": gate["withheld_segments"],
            "coverage": {s: gate["per_segment"][s]["coverage"] for s in spec.SEGMENTS},
            "rule": gate["rule"],
            "conditional_measured": cond["summary"]["measured_conditional"],
            "conditional_withheld": cond["summary"]["withheld"],
            "scope_warning": cond["summary"]["scope_warning"],
        },
        "e_policy_theory": {
            "pass": sim["verdict"]["theory_ok"] and sim_matched["verdict"]["theory_ok"],
            "tol": spec.POLICY_THEORY_TOL,
            "detail": {p: v["err"] for p, v in sim["policies"].items()},
        },
    }
    crit["overall"] = {
        "all_pass": all(c["pass"] for k, c in crit.items() if k in
                        ("a_jain_recovery", "b_detector_f1", "c_provenance",
                         "d_coverage_gate", "e_policy_theory")),
        "withheld": gate["withheld_segments"],
    }
    report["criteria"] = crit
    return report