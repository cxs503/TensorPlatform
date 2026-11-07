#!/usr/bin/env python3
"""战史官钟·单拍（owner 2026-11-07 渐退令件）：六源机器快照，纯 SQL 零 LLM 零会话依赖。"""
import json, os, subprocess, sys
from datetime import datetime, timezone

# IN27-H2 门控仓补丁：psql 单次调用硬上限（锁等待不再挂死整条时钟通道）
PSQL_TIMEOUT_S = float(os.environ.get("TP_PG_TIMEOUT_S", "15"))


def psql(sql):
    try:
        r = subprocess.run(["psql","-U","postgres","-d","tensorplatform","-h","/var/run/postgresql","-t","-A","-c",sql],
                           capture_output=True, text=True, timeout=PSQL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print("SQLERR timeout", PSQL_TIMEOUT_S); sys.exit(1)
    if r.returncode: print("SQLERR", r.stderr.strip()[:150]); sys.exit(1)
    return r.stdout.strip()
def one(sql):
    v = psql(sql); return v.splitlines()[0] if v else None
def has(t): return one(f"SELECT to_regclass('{t}') IS NOT NULL;") == "t"

payload = {
    "schema": "chronicle-clock/1",
    "note": "machine clock snapshot; analysis-grade rows come from platform missions",
    "s1_missions": {
        "by_status": dict(l.split("|") for l in psql(
            "SELECT status||'|'||count(*) FROM tensor_dev_missions GROUP BY status ORDER BY status;").splitlines() if l),
        "awaiting_pr_merge": one("SELECT count(*) FROM tensor_dev_missions WHERE status='awaiting_pr_merge';"),
        "queued_stuck_oldest_min": one("SELECT coalesce(round(extract(epoch from (now()-min(created_at)))/60)::text,'0') FROM tensor_dev_missions WHERE status='queued' AND created_at < now()-interval '2 hours';"),
    },
    "s2_campaign": {
        "plans_active": one("SELECT count(*) FROM campaign_plans WHERE status='active';"),
        "nodes_by_status": dict(l.split("|") for l in psql(
            "SELECT status||'|'||count(*) FROM campaign_nodes GROUP BY status ORDER BY status;").splitlines() if l),
        "gates_blocked": ",".join(psql("SELECT (metadata->>'ext_id') FROM campaign_nodes WHERE node_type='gate' AND status='blocked' ORDER BY id;").splitlines()),
    },
    "s3_capability": ({"registered": one("SELECT count(*) FROM capability;"),
                       "with_real_evidence": one("SELECT count(*) FROM capability WHERE reconciled_level='REAL';")}
                      if has("capability") else "table-absent(platform DB 无能力表,属平台边界设计)"),
    "s4_fleet_pulse": {
        "last_onl_busy_held_rel": psql("SELECT online||'/'||busy||'/'||held||'/'||released FROM saturation_pulse ORDER BY ts DESC LIMIT 1;"),
        "pulse_age_min": one("SELECT coalesce(round(extract(epoch from (now()-max(ts)))/60)::text,'9999') FROM saturation_pulse;"),
        "busy_1h_max": one("SELECT coalesce(max(busy),0)::text FROM saturation_pulse WHERE ts > now()-interval '1 hour';"),
    },
    "s5_outcomes": ({
        "confirmed_72h": one("SELECT count(*) FROM outcome_evaluations WHERE status='confirmed' AND created_at::timestamptz > now()-interval '72 hours';"),
        "succeeded_72h": one("SELECT count(*) FROM tensor_dev_missions WHERE status='succeeded' AND finished_at > now()-interval '72 hours';"),
        "coverage_note": "confirmed/succeeded 覆盖率，目标>=80%（IN24-E1）",
        "grade_dist": ",".join(psql("SELECT grade||'='||count(*) FROM outcome_evaluations WHERE status='confirmed' GROUP BY grade ORDER BY count(*) DESC;").splitlines()),
        "independent_share": one("SELECT round(100.0*count(*) FILTER (WHERE reviewer_kind='independent')/greatest(count(*),1))||'%' FROM outcome_evaluations WHERE status='confirmed';"),
        "pending_evals": one("SELECT count(*) FROM outcome_evaluations WHERE status='pending';"),
    } if has("outcome_evaluations") else "table-absent"),
    "s7_voice": {
        "proposer_running": one("SELECT count(*) FROM tensor_dev_missions WHERE goal LIKE '%AUTONOMY-PROPOSER%' AND status='running';"),
        "proposer_queued": one("SELECT count(*) FROM tensor_dev_missions WHERE goal LIKE '%AUTONOMY-PROPOSER%' AND status='queued';"),
        "proposer_done_7d": one("SELECT count(*) FROM tensor_dev_missions WHERE goal LIKE '%AUTONOMY-PROPOSER%' AND status='succeeded' AND finished_at > now()-interval '7 days';"),
        "note": "四权之权1(问题选择权)活性：提案通道=组织自产战役之源；0=声音哑，须查堵点",
    },
    "s8_harness": ({
        "shadow_lag_min": one("SELECT coalesce(round(extract(epoch from (now()-max(ts)))/60)::text,'9999') FROM coordinator_shadow;"),
        "shadow_urgent": one("SELECT count(*) FROM coordinator_shadow WHERE urgent AND ts > now()-interval '2 hours';"),
        "credit_backlog": one("SELECT count(*) FROM outcome_evaluations e WHERE e.status IN ('confirmed','rejected') AND NOT EXISTS (SELECT 1 FROM tensor_credit_ledger l WHERE l.reason LIKE 'eval:'||e.evaluation_id||'|%');"),
        "capability_deltas_24h": one("SELECT count(*) FROM tensor_capability_deltas WHERE created_at > now()-interval '24 hours';"),
        "note": "继承者器官自报：shadow_lag>90min=影子官死/credit_backlog>50=结算断/deltas_24h=0=C 回路又饿——问题自己跳出来，不等 owner 看出症状",
    } if has("coordinator_shadow") else "shadow-not-born-yet"),
    "s6_daemons": {
        "clock_schema": "chronicle-clock/1",
        "daemons_alive": subprocess.run(["sh","-c","ps ax | grep -E 'saturation_regulator_daemon|chronicle_clock_daemon' | grep -v grep | wc -l"],
                                        capture_output=True, text=True).stdout.strip(),
    },
}
row = json.dumps(payload, ensure_ascii=False).replace("'", "''")
r = psql(f"INSERT INTO chronicle_snapshots (ts, payload) VALUES (now(), '{row}'::jsonb) RETURNING ts::text;")
print(datetime.now(timezone.utc).strftime("%F %T"), "clock snapshot:", r)
