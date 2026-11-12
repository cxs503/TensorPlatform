"""生产看板：owner 八指标（不看完成任务数，看质量指标）。

指标全部由 SQL 从 tk_* 表与 metered_usage 现算，零手抄；每轮 log 一行 + tk_metric_snapshots 落表。
"""

METRIC_TITLES = [
    ("pending_reports", "待处理报告数"),
    ("dispatched_tasks", "已分发任务数"),
    ("processed_ok", "成功加工数"),
    ("failed_and_rework", "失败与返工数"),
    ("review_pass_rate", "独立审核通过率"),
    ("locator_success_rate", "证据定位成功率"),
    ("avg_cost_per_report", "每份报告平均成本"),
    ("knowledge_reuse", "知识复用次数"),
]


def collect_metrics(store):
    m = {}
    m["pending_reports"] = int(store.scalar(
        "SELECT count(DISTINCT report_id) FROM tk_work_packages "
        "WHERE status IN ('pending','blocked','rework')", default=0))
    m["dispatched_tasks"] = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages WHERE mission_id IS NOT NULL", default=0))
    m["processed_ok"] = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages "
        "WHERE status IN ('validated','review','ingested')", default=0))
    m["failed"] = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages WHERE status = 'failed'", default=0))
    m["rework"] = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages WHERE attempt > 0", default=0))
    m["failed_and_rework"] = m["failed"] + m["rework"]

    reviewed = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages WHERE task_type = '04' "
        "AND gates ? 'review_verdict'", default=0))
    passed = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages WHERE task_type = '04' "
        "AND gates->>'review_verdict' = 'pass'", default=0))
    m["review_pass_rate"] = (passed / reviewed) if reviewed else None
    m["reviewed"] = reviewed
    m["review_passed"] = passed

    gated = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages WHERE gates ? 'g5_source_locator_resolvable'",
        default=0))
    loc_ok = int(store.scalar(
        "SELECT count(*) FROM tk_work_packages "
        "WHERE gates->'g5_source_locator_resolvable'->>'ok' = 'true'", default=0))
    m["locator_success_rate"] = (loc_ok / gated) if gated else None
    m["locator_gated"] = gated
    m["locator_ok"] = loc_ok

    reports_touched = int(store.scalar(
        "SELECT count(DISTINCT report_id) FROM tk_work_packages WHERE mission_id IS NOT NULL",
        default=0))
    tok = int(store.scalar(
        "SELECT COALESCE(sum(u.tokens_used),0) FROM metered_usage u JOIN "
        "(SELECT DISTINCT mission_id FROM tk_work_packages WHERE mission_id IS NOT NULL) p "
        "ON p.mission_id = u.mission_id", default=0))
    secs = int(float(store.scalar(
        "SELECT COALESCE(sum(EXTRACT(EPOCH FROM (COALESCE(m.finished_at, now()) - m.created_at))"
        "),0) FROM tensor_dev_missions m JOIN "
        "(SELECT DISTINCT mission_id FROM tk_work_packages WHERE mission_id IS NOT NULL) p "
        "ON p.mission_id = m.id", default=0)))
    m["cost"] = {"tokens": tok, "compute_seconds": secs,
                 "reports_measured": reports_touched}
    m["avg_cost_per_report"] = {
        "tokens": round(tok / reports_touched, 1) if reports_touched else None,
        "compute_seconds": round(secs / reports_touched, 1) if reports_touched else None,
        "metered_tokens": tok > 0,
    }
    m["knowledge_reuse"] = int(store.scalar("SELECT count(*) FROM tk_knowledge_uses", default=0))
    m["drafts_emitted"] = int(store.scalar("SELECT count(*) FROM tk_ingest_drafts", default=0))
    m["drafts_verified"] = int(store.scalar(
        "SELECT count(*) FROM tk_ingest_drafts WHERE verification_status <> 'unverified'",
        default=0))
    return m


def render(m):
    lines = []
    for key, title in METRIC_TITLES:
        v = m.get(key)
        if key == "avg_cost_per_report":
            v = (f"tokens={v['tokens']} compute_s={v['compute_seconds']} "
                 f"(metered={v['metered_tokens']})")
        elif isinstance(v, float):
            v = f"{v:.1%}"
        lines.append(f"{title}: {v}")
    return " ｜ ".join(lines)


def one_line(m):
    return (f"待处理{m['pending_reports']} 已分发{m['dispatched_tasks']} "
            f"成功{m['processed_ok']} 失败/返工{m['failed_and_rework']} "
            f"审核通过率{_pct(m['review_pass_rate'])} 定位成功率{_pct(m['locator_success_rate'])} "
            f"成本/报告{m['avg_cost_per_report']['tokens']}tok/"
            f"{m['avg_cost_per_report']['compute_seconds']}s 复用{m['knowledge_reuse']}")


def _pct(v):
    return "n/a" if v is None else f"{v:.0%}"