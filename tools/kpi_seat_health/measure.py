#!/usr/bin/env python3
"""KPI-SEAT-HEALTH 计量器 —— 只计量不改进。

锚点: KPI-SEAT-HEALTH
口径: 席位健康度现值首测定基线（kpi_meter 计量单）
消账门: confirmed 计量现值 ≥ 80%

数据源:
  - tensor_dev_missions (任务主账)
  - tensor_reports (报告工件)
  - tensor_agent_health / v_agent_health_seat_latest (席位健康快照)
  - tensorbench_results (基准测试)
  - knowledge_base / knowledge_rules / knowledge_injections (知识库)
  - ship_samples (样本)
  - metered_usage (计量)
  - integrity_events (完整性事件)
  - tensor_quality_patrol (质量巡逻)

健康定义 (FEAT-AGENT-HARNESS):
  健康分 = 四分量加权 + 缺失重归一化 + 标记乘性罚分
  S = 成功率 (w=0.50)
  C = 可控率 (w=0.20)
  A = 失败可归因率 (w=0.10)
  M = 记忆活力 (w=0.20)
  raw = 100 * Σ(w_i·c_i) / Σ(w_i | c_i ≠ None)
  score = raw × Π penalty_k
  penalty: RECUR_FAIL=0.65 / LOW_SUCCESS=0.60 / MEMORY_STARVED=0.75

  健康席位定义: health_score ≥ 80 (消账门)
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DSN = "postgresql://foundation:foundation123@localhost:5432/tensorplatform"


def run_sql_json(sql: str) -> Any:
    """执行 SQL 并返回 JSON 解析后的结果。"""
    proc = subprocess.run(
        ["psql", "-X", "-q", "-d", DSN, "-c", sql, "-t", "-A"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:500]}")
    val = proc.stdout.strip()
    if not val:
        return None
    try:
        return json.loads(val)
    except json.JSONDecodeError:
        return val


def run_sql_rows(sql: str) -> list[list[str]]:
    """执行 SQL 并返回行列表。"""
    proc = subprocess.run(
        ["psql", "-X", "-q", "-d", DSN, "-c", sql, "-t", "-A", "-F", "|"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:500]}")
    lines = proc.stdout.strip().split("\n")
    if not lines or lines == [""]:
        return []
    return [line.split("|") for line in lines if line.strip()]


def run_sql_single(sql: str) -> Any:
    """执行 SQL 并返回单个值。"""
    proc = subprocess.run(
        ["psql", "-X", "-q", "-d", DSN, "-c", sql, "-t", "-A"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:500]}")
    val = proc.stdout.strip()
    try:
        return int(val)
    except ValueError:
        try:
            return float(val)
        except ValueError:
            return val


def safe_int(v: str, default: int = 0) -> int:
    try:
        return int(v) if v.strip() else default
    except ValueError:
        return default


def safe_float(v: str, default: float = 0.0) -> float:
    try:
        return float(v) if v.strip() else default
    except ValueError:
        return default


def measure_seat_health() -> dict[str, Any]:
    """计量席位健康度核心指标。"""
    seat_summary = run_sql_json("""
        SELECT json_build_object(
            'total_seats', COUNT(*),
            'unhealthy_seats', COUNT(*) FILTER (WHERE cardinality(markers) > 0),
            'recur_fail_seats', COUNT(*) FILTER (WHERE 'RECUR_FAIL' = ANY(markers)),
            'low_success_seats', COUNT(*) FILTER (WHERE 'LOW_SUCCESS' = ANY(markers)),
            'memory_starved_seats', COUNT(*) FILTER (WHERE 'MEMORY_STARVED' = ANY(markers)),
            'avg_health_score', ROUND(AVG(health_score)::numeric, 2),
            'median_health_score', ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY health_score)::numeric, 2),
            'min_health_score', MIN(health_score),
            'max_health_score', MAX(health_score),
            'total_missions_7d', SUM(missions_total),
            'total_succeeded_7d', SUM(missions_succeeded),
            'fleet_success_rate_pct', ROUND((SUM(missions_succeeded)::numeric / NULLIF(SUM(missions_total), 0)) * 100, 2)
        )
        FROM v_agent_health_seat_latest
        WHERE seat_kind = 'worker';
    """)

    score_buckets_raw = run_sql_rows("""
        SELECT 
            CASE 
                WHEN health_score >= 80 THEN 'healthy(≥80)'
                WHEN health_score >= 60 THEN 'warning(60-79)'
                WHEN health_score >= 40 THEN 'poor(40-59)'
                ELSE 'critical(<40)'
            END as bucket,
            COUNT(*) as count,
            ROUND(AVG(health_score)::numeric, 2) as avg_score
        FROM v_agent_health_seat_latest
        WHERE seat_kind = 'worker'
        GROUP BY 1
        ORDER BY MIN(health_score);
    """)
    score_buckets = [{"bucket": r[0], "count": safe_int(r[1]), "avg_score": safe_float(r[2])} for r in score_buckets_raw]

    unhealthy_raw = run_sql_rows("""
        SELECT seat_key, health_score, markers, missions_total, missions_succeeded,
               ROUND((missions_succeeded::numeric / NULLIF(missions_total, 0)) * 100, 2) as success_rate,
               forced_finish_count, lessons_injected, memory_hits
        FROM v_agent_health_seat_latest
        WHERE seat_kind = 'worker' AND cardinality(markers) > 0
        ORDER BY health_score ASC
        LIMIT 20;
    """)
    unhealthy_details = []
    for r in unhealthy_raw:
        unhealthy_details.append({
            "seat_key": r[0], "health_score": safe_float(r[1]), "markers": r[2],
            "missions_total": safe_int(r[3]), "missions_succeeded": safe_int(r[4]),
            "success_rate": safe_float(r[5]), "forced_finish_count": safe_int(r[6]),
            "lessons_injected": safe_int(r[7]), "memory_hits": safe_int(r[8]),
        })

    healthy_raw = run_sql_rows("""
        SELECT seat_key, health_score, markers, missions_total, missions_succeeded,
               ROUND((missions_succeeded::numeric / NULLIF(missions_total, 0)) * 100, 2) as success_rate,
               forced_finish_count, lessons_injected, memory_hits
        FROM v_agent_health_seat_latest
        WHERE seat_kind = 'worker' AND cardinality(markers) = 0
        ORDER BY health_score DESC
        LIMIT 10;
    """)
    healthy_details = []
    for r in healthy_raw:
        healthy_details.append({
            "seat_key": r[0], "health_score": safe_float(r[1]), "markers": r[2],
            "missions_total": safe_int(r[3]), "missions_succeeded": safe_int(r[4]),
            "success_rate": safe_float(r[5]), "forced_finish_count": safe_int(r[6]),
            "lessons_injected": safe_int(r[7]), "memory_hits": safe_int(r[8]),
        })

    return {
        "seat_summary": seat_summary,
        "score_buckets": score_buckets,
        "unhealthy_details": unhealthy_details,
        "healthy_details": healthy_details,
    }


def measure_mission_kpis() -> dict[str, Any]:
    missions = run_sql_json("""
        SELECT json_build_object(
            'total_missions', COUNT(*),
            'active_missions', COUNT(*) FILTER (WHERE status IN ('claimed', 'running')),
            'succeeded_missions', COUNT(*) FILTER (WHERE status = 'succeeded'),
            'completed_missions', COUNT(*) FILTER (WHERE status = 'completed'),
            'failed_missions', COUNT(*) FILTER (WHERE status = 'failed'),
            'stopped_missions', COUNT(*) FILTER (WHERE status = 'stopped'),
            'revoked_missions', COUNT(*) FILTER (WHERE status = 'revoked'),
            'terminal_total', COUNT(*) FILTER (WHERE status IN ('succeeded', 'failed', 'stopped', 'revoked')),
            'success_rate_pct', ROUND(COUNT(*) FILTER (WHERE status = 'succeeded')::numeric / 
                NULLIF(COUNT(*) FILTER (WHERE status IN ('succeeded', 'failed', 'stopped', 'revoked')), 0) * 100, 2)
        )
        FROM tensor_dev_missions;
    """)
    avg_duration = run_sql_single("""
        SELECT ROUND(AVG(EXTRACT(EPOCH FROM (finished_at - created_at)))::numeric, 2)
        FROM tensor_dev_missions 
        WHERE status IN ('succeeded', 'completed') AND finished_at IS NOT NULL;
    """)
    return {"missions": missions, "avg_mission_duration_seconds": avg_duration}


def measure_report_kpis() -> dict[str, Any]:
    reports = run_sql_json("""
        SELECT json_build_object(
            'total_reports', COUNT(*),
            'published_reports', COUNT(*) FILTER (WHERE status = 'published'),
            'qa_passed', COUNT(*) FILTER (WHERE qa_status = 'passed'),
            'qa_pending', COUNT(*) FILTER (WHERE qa_status = 'pending'),
            'qa_rejected', COUNT(*) FILTER (WHERE qa_status = 'rejected'),
            'qa_passed_pct', ROUND(COUNT(*) FILTER (WHERE qa_status = 'passed')::numeric / NULLIF(COUNT(*), 0) * 100, 2)
        )
        FROM tensor_reports;
    """)
    return {"reports": reports}


def measure_sample_kpis() -> dict[str, Any]:
    samples = run_sql_json("""
        SELECT json_build_object(
            'total_samples', COUNT(*),
            'passed_samples', COUNT(*) FILTER (WHERE passed = true),
            'pass_rate_pct', ROUND(COUNT(*) FILTER (WHERE passed = true)::numeric / NULLIF(COUNT(*), 0) * 100, 2)
        )
        FROM ship_samples;
    """)
    return {"samples": samples}


def measure_benchmark_kpis() -> dict[str, Any]:
    benchmarks = run_sql_json("""
        SELECT json_build_object(
            'total_benchmarks', COUNT(*),
            'passed_benchmarks', COUNT(*) FILTER (WHERE passed = true),
            'pass_rate_pct', ROUND(COUNT(*) FILTER (WHERE passed = true)::numeric / NULLIF(COUNT(*), 0) * 100, 2)
        )
        FROM tensorbench_results;
    """)
    return {"benchmarks": benchmarks}


def measure_knowledge_kpis() -> dict[str, Any]:
    kb = run_sql_json("""
        SELECT json_build_object(
            'tensor_reports', (SELECT COUNT(*) FROM tensor_reports),
            'knowledge_base', (SELECT COUNT(*) FROM knowledge_base),
            'knowledge_rules', (SELECT COUNT(*) FROM knowledge_rules),
            'knowledge_injections', (SELECT COUNT(*) FROM knowledge_injections),
            'kb_samples_total', (SELECT COUNT(*) FROM tensor_reports) + (SELECT COUNT(*) FROM knowledge_base)
        );
    """)
    return {"knowledge": kb}


def measure_integrity_kpis() -> dict[str, Any]:
    integrity = run_sql_json("""
        SELECT json_build_object(
            'unresolved_events', COUNT(*) FILTER (WHERE resolved = false),
            'total_events', COUNT(*)
        )
        FROM integrity_events;
    """)
    return {"integrity": integrity}


def measure_quality_patrol() -> dict[str, Any]:
    patrol_raw = run_sql_rows("""
        SELECT ts, merged_7d, l2plus_new, jsonb_array_length(alerts) as alert_count
        FROM tensor_quality_patrol
        ORDER BY ts DESC
        LIMIT 5;
    """)
    patrol = []
    for r in patrol_raw:
        patrol.append({"ts": r[0], "merged_7d": safe_int(r[1]), "l2plus_new": safe_int(r[2]), "alert_count": safe_int(r[3])})
    return {"quality_patrol": patrol}


def compute_health_rate(seat_summary: dict) -> dict[str, Any]:
    total = seat_summary.get("total_seats", 0)
    unhealthy = seat_summary.get("unhealthy_seats", 0)
    healthy = total - unhealthy
    health_rate = round(healthy / total * 100, 2) if total > 0 else 0.0
    
    healthy_by_threshold = run_sql_single("""
        SELECT COUNT(*) 
        FROM v_agent_health_seat_latest 
        WHERE seat_kind = 'worker' AND health_score >= 80;
    """)
    health_rate_80 = round(healthy_by_threshold / total * 100, 2) if total > 0 else 0.0
    
    return {
        "health_rate_pct": health_rate,
        "health_rate_80_pct": health_rate_80,
        "healthy_seats": healthy,
        "healthy_seats_80": healthy_by_threshold,
        "total_seats": total,
        "unhealthy_seats": unhealthy,
        "gate_target": 80.0,
        "gate_pass": health_rate_80 >= 80.0,
    }


def build_result() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    
    seat_health = measure_seat_health()
    mission_kpis = measure_mission_kpis()
    report_kpis = measure_report_kpis()
    sample_kpis = measure_sample_kpis()
    benchmark_kpis = measure_benchmark_kpis()
    knowledge_kpis = measure_knowledge_kpis()
    integrity_kpis = measure_integrity_kpis()
    quality_patrol = measure_quality_patrol()
    
    health_rate = compute_health_rate(seat_health["seat_summary"])
    
    result = {
        "kpi": "KPI-SEAT-HEALTH",
        "definition": "席位健康度现值 = 健康席位比例（health_score ≥ 80 的席位 / 总席位数）",
        "measured_at": now.isoformat(),
        "current": health_rate["health_rate_80_pct"],
        "unit": "%",
        "target": 80.0,
        "gap": round(80.0 - health_rate["health_rate_80_pct"], 2),
        "gate_pass": health_rate["gate_pass"],
        "gate_target": 80.0,
        "data_sources": [
            "SQL: v_agent_health_seat_latest (席位健康快照视图)",
            "SQL: tensor_dev_missions (任务主账)",
            "SQL: tensor_reports (报告工件)",
            "SQL: tensorbench_results (基准测试)",
            "SQL: knowledge_base / knowledge_rules / knowledge_injections (知识库)",
            "SQL: ship_samples (样本)",
            "SQL: integrity_events (完整性事件)",
            "SQL: tensor_quality_patrol (质量巡逻)",
        ],
        "seat_health": {
            "summary": seat_health["seat_summary"],
            "score_buckets": seat_health["score_buckets"],
            "health_rate": health_rate,
            "unhealthy_details": seat_health["unhealthy_details"],
            "healthy_details": seat_health["healthy_details"],
        },
        "mission_kpis": mission_kpis,
        "report_kpis": report_kpis,
        "sample_kpis": sample_kpis,
        "benchmark_kpis": benchmark_kpis,
        "knowledge_kpis": knowledge_kpis,
        "integrity_kpis": integrity_kpis,
        "quality_patrol": quality_patrol,
        "top_gaps": [
            f"席位健康率仅 {health_rate['health_rate_80_pct']}%（目标 80%），{seat_health['seat_summary'].get('unhealthy_seats', 0)}/{seat_health['seat_summary'].get('total_seats', 0)} 席位带病标记",
            f"RECUR_FAIL 标记席位 {seat_health['seat_summary'].get('recur_fail_seats', 0)} 个（占病席位 {(seat_health['seat_summary'].get('recur_fail_seats', 0) / max(1, seat_health['seat_summary'].get('unhealthy_seats', 1)) * 100):.1f}%）",
            f"平均健康分 {seat_health['seat_summary'].get('avg_health_score', 0)}，中位数 {seat_health['seat_summary'].get('median_health_score', 0)}",
            f"舰队成功率 {seat_health['seat_summary'].get('fleet_success_rate_pct', 0)}%（7d 窗口）",
            f"基准测试仅 {benchmark_kpis['benchmarks'].get('passed_benchmarks', 0)} 项通过（目标 ≥5）",
            f"知识库样本 {knowledge_kpis['knowledge'].get('kb_samples_total', 0)}（目标 ≥100）",
        ],
        "proposed_anchors": [
            {"anchor": "KPI-SEAT-RECUR-0", "one_line": "RECUR_FAIL 席位清零：验收=该标记 COUNT(*)=0"},
            {"anchor": "KPI-SEAT-HEALTH-80", "one_line": "健康席位比例≥80%：验收=health_score≥80 的席位/总席位≥0.8"},
            {"anchor": "KPI-SEAT-MEMORY-50", "one_line": "记忆活力提升：验收=memory_hits>0 的席位占比≥50%"},
            {"anchor": "KPI-SEAT-SUCCESS-70", "one_line": "舰队成功率≥70%：验收=7d 窗口成功率≥70%"},
            {"anchor": "KPI-BENCHMARK-10", "one_line": "基准测试通过数≥10：验收=passed_benchmarks≥10"},
        ],
    }
    
    return result


def main():
    outdir = Path(__file__).resolve().parents[2] / "artifacts" / "kpi_seat_health"
    outdir.mkdir(parents=True, exist_ok=True)
    
    result = build_result()
    
    result_path = outdir / "result.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Result saved to {result_path}")
    
    print(f"\n{'='*60}")
    print(f"KPI-SEAT-HEALTH 计量结果")
    print(f"{'='*60}")
    print(f"测量时刻: {result['measured_at']}")
    print(f"当前值:   {result['current']} {result['unit']}")
    print(f"目标值:   {result['target']} {result['unit']}")
    print(f"缺口:     {result['gap']} pp")
    print(f"消账门:   {'PASS' if result['gate_pass'] else 'FAIL'} (目标≥{result['gate_target']}%)")
    print(f"\n席位健康:")
    ss = result["seat_health"]["summary"]
    print(f"  总席位:     {ss.get('total_seats', 0)}")
    print(f"  健康席位:   {result['seat_health']['health_rate']['healthy_seats_80']}")
    print(f"  病席位:     {ss.get('unhealthy_seats', 0)}")
    print(f"  平均健康分: {ss.get('avg_health_score', 0)}")
    print(f"  中位健康分: {ss.get('median_health_score', 0)}")
    print(f"\nTop gaps:")
    for i, gap in enumerate(result["top_gaps"], 1):
        print(f"  {i}. {gap}")


if __name__ == "__main__":
    main()
