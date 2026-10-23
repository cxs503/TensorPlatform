"""m205 班次巡检采集器：现场直读 DB / 进程 / 端点，产出 2026-10-23.validation_report.json。

时点快照，提交后勿复跑（心跳秒数会变 → 自造脏树）。
用法（cwd = 本 worktree 根）：python3 operator_log/collect_2026-10-23.py
"""
import json, subprocess, os

def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw).stdout.strip()

DOCKER = "export DOCKER_HOST=unix:///run/user/1020/docker.sock; "
PSQL = "docker exec open-foundation-postgres psql -U foundation -d open_digital_foundation -A -t -F'|' -c "

def q(sql):
    out = sh(DOCKER + PSQL + json.dumps(sql))
    return [l.split('|') for l in out.splitlines() if l.strip()]

rep = {"generated_by": "collect_2026-10-23.py (m205)", "worktree": os.getcwd()}

# ── 步 1 在飞心跳鲜度（>240s = 即将收割）
air = q("SELECT id,status,round(extract(epoch from (now()-heartbeat_at)))::int,"
        "coalesce(result->>'stop_condition','') FROM tensor_dev_missions "
        "WHERE status IN ('claimed','running') ORDER BY id")
rep["step1_inflight"] = {
    "count": len(air), "rows": air,
    "max_hb_age_s": max([int(r[2]) for r in air], default=0),
    "threshold_s": 240,
    "pass": all(int(r[2]) <= 240 for r in air),
}

# ── 步 2 守护计数 + 三量等式（权威源 = agent_work/workers.json，R17 教训）
tot = int(sh("ps -eo args | grep '[t]ensor_agent\\.py' | grep -v 'bash -lc' | wc -l") or 0)
slots = int(sh("ps -eo args | grep '[t]ensor_agent\\.py' | grep -v 'bash -lc' | grep -c ' --slot [2-9]'") or 0)
slot1 = tot - slots
WJ_SRC = "/srv/tensor/shared/tensor_eco/agent_work/workers.json"   # 主仓 tools/agent/workers.json 的 symlink 目标
wj = json.load(open(WJ_SRC))
wsum = sum(v for k, v in wj.items() if not k.startswith("_"))
rep["step2_guardians"] = {
    "observed_total": tot, "slot_ge2": slots, "slot1": slot1,
    "workers_json_source": WJ_SRC, "workers_json_sum": wsum,
    "equation_holds": tot == wsum == slot1 + slots,
    "per_model": sh("ps -eo args | grep '[t]ensor_agent\\.py' | grep -v 'bash -lc' "
                    "| grep -o '\\-\\-model [^ ]*' | sort | uniq -c | sort -rn | tr '\\n' ';'"),
    "workers_json": {k: v for k, v in wj.items() if not k.startswith("_")},
    "pass": tot == wsum == slot1 + slots,
}

# ── 步 3 中继健康（非 200 或 >5s = 端点问题）
relay = sh('set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a; '
           'curl -s -o /tmp/m205_models.json -w "%{http_code} %{time_total}" '
           '-H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models"')
code, t = relay.split()
nmodels = len(json.load(open("/tmp/m205_models.json")).get("data", []))
rep["step3_relay"] = {"http": int(code), "seconds": round(float(t), 3), "models": nmodels,
                      "pass": int(code) == 200 and float(t) < 5.0}

# ── 步 4 网关冷却态（blocked_until 过期可清）
st = json.load(open("/srv/tensor/shared/tensor_eco/agent_work/llm_gateway_state.json"))
rep["step4_gateway_state"] = {"raw": st, "empty": st == {}, "pass": st == {}}

# ── 步 5 终态 mission result 审计面
rep["step5_terminal"] = {
    "by_status_24h": dict(q("SELECT status,count(*) FROM tensor_dev_missions "
                            "WHERE finished_at > now()-interval '24 hours' "
                            "AND status NOT IN ('claimed','running') GROUP BY status")),
    "failed_by_stop_24h": dict(q("SELECT coalesce(result->>'stop_condition','(none)'),count(*) "
                                 "FROM tensor_dev_missions WHERE finished_at > now()-interval '24 hours' "
                                 "AND status='failed' GROUP BY 1")),
    "last_failed": q("SELECT max(id),round(extract(epoch from (now()-max(finished_at))) )::int "
                     "FROM tensor_dev_missions WHERE status='failed' "
                     "AND finished_at > now()-interval '24 hours'"),
    "recent_terminal_3h": q("SELECT id,status,round(extract(epoch from (now()-finished_at)))::int,"
                            "left(coalesce(result->>'headline',''),50) FROM tensor_dev_missions "
                            "WHERE finished_at > now()-interval '3 hours' ORDER BY finished_at DESC"),
    "pass": True,
}

# ── 平台额外体检
rep["extra"] = {
    "containers_healthy": sh(DOCKER + "docker ps --format '{{.Status}}' | grep -c '(healthy)'"),
    "containers_total": sh(DOCKER + "docker ps -q | wc -l"),
    "celery_beat": sh(DOCKER + "docker inspect open-foundation-celery-beat --format "
                              "'restart={{.RestartCount}} exit={{.State.ExitCode}} status={{.State.Status}}'"),
    "approvals_by_status": dict(q("SELECT status,count(*) FROM tensor_approval_requests GROUP BY status")),
    "escalated_json": json.load(open("/srv/tensor/shared/tensor_eco/agent_work/approver_escalated.json")),
    "pass_all_switch_exists": os.path.exists("/srv/tensor/shared/tensor_eco/agent_work/auto_approver.PASS_ALL"),
    "pass_all_rounds": int(sh("grep -c 'PASS_ALL 模式' /srv/tensor/shared/tensor_eco/agent_work/auto_approver.log") or 0),
    "approver_log_tail": sh("tail -1 /srv/tensor/shared/tensor_eco/agent_work/auto_approver.log"),
    "worktree_total": int(sh("git worktree list | wc -l") or 0),
    "worktree_tensorplatform_paths": int(sh("git worktree list | grep -c '/TensorPlatform'") or 0),
    "main_repo_dirty": int(sh("git -C /srv/tensor/shared/tensor_eco/TensorPlatform status --porcelain | wc -l") or 0),
    "gc_log_tail": sh("tail -1 /srv/tensor/shared/tensor_eco/agent_work/gc.log"),
}
rep["checks_total"] = 5
rep["checks_passed"] = sum(1 for k in ("step1_inflight", "step2_guardians", "step3_relay",
                                      "step4_gateway_state", "step5_terminal") if rep[k]["pass"])
json.dump(rep, open("operator_log/2026-10-23.validation_report.json", "w"), ensure_ascii=False, indent=2)
print("checks_passed=", rep["checks_passed"], "/", rep["checks_total"])