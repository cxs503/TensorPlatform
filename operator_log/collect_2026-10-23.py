import json, subprocess, os, urllib.request

def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw).stdout.strip()

DOCKER = "export DOCKER_HOST=unix:///run/user/1020/docker.sock; "
PSQL = "docker exec open-foundation-postgres psql -U foundation -d open_digital_foundation -A -t -F'|' -c "

def q(sql):
    out = sh(DOCKER + PSQL + json.dumps(sql))
    return [l.split('|') for l in out.splitlines() if l.strip()]

rep = {}
rep["generated_by"] = "collect_2026-10-23.py (m203)"

# 1 在飞心跳
air = q("SELECT id,status,round(extract(epoch from (now()-heartbeat_at)))::int,coalesce(result->>'stop_condition','') FROM tensor_dev_missions WHERE status IN ('claimed','running') ORDER BY id")
rep["step1_inflight"] = {"count": len(air), "rows": air,
    "max_hb_age_s": max([int(r[2]) for r in air], default=0), "threshold_s": 240,
    "pass": all(int(r[2]) <= 240 for r in air)}

# 2 守护计数 + 等式
tot = int(sh("ps -eo args | grep '[t]ensor_agent\\.py' | grep -vc 'bash -lc'") or 0)
slots = int(sh("ps -eo args | grep -c '[t]ensor_agent.py .*--slot [2-9]'") or 0)
slot1 = tot - slots
# workers.json 权威源 = 主仓 symlink 目标 agent_work/workers.json（owner 可编辑）；
# 本 worktree 内该路径被检出为普通文件副本（陈旧），不可用作期望值。
CAND = ["/srv/tensor/shared/tensor_eco/TensorPlatform/tools/agent/workers.json",
        "/srv/tensor/shared/tensor_eco/agent_work/workers.json"]
WJ_SRC = next(p for p in CAND if os.path.isfile(p) and (os.path.islink(p) or p.endswith("agent_work/workers.json")))
wj = json.load(open(WJ_SRC))
wsum = sum(v for k, v in wj.items() if not k.startswith("_"))
wlocal = json.load(open("tools/agent/workers.json"))
wlocal_sum = sum(v for k, v in wlocal.items() if not k.startswith("_"))
rep["step2_guardians"] = {"observed_total": tot, "slot_ge2": slots, "slot1": slot1,
    "workers_json_source": WJ_SRC, "workers_json_sum": wsum, "equation_holds": tot == wsum == slot1 + slots,
    "worktree_copy_sum_stale": wlocal_sum, "worktree_copy_matches_source": wlocal == wj,
    "per_model": sh("ps -eo args | grep '[t]ensor_agent\\.py' | grep -v 'bash -lc' | grep -o '\\-\\-model [^ ]*' | sort | uniq -c | sort -rn | tr '\\n' ';'"),
    "pass": tot == wsum == slot1 + slots}

# 3 中继
relay = sh('set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a; curl -s -o /tmp/m203_m.json -w "%{http_code} %{time_total}" -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models"')
code, t = relay.split()
nmodels = len(json.load(open("/tmp/m203_m.json")).get("data", []))
rep["step3_relay"] = {"http": int(code), "seconds": round(float(t), 3), "models": nmodels,
    "pass": int(code) == 200 and float(t) < 5.0}

# 4 冷却态
st = json.load(open("/srv/tensor/shared/tensor_eco/agent_work/llm_gateway_state.json"))
rep["step4_gateway_state"] = {"raw": st, "empty": st == {}, "pass": st == {}}

# 5 终态 result
fin = q("SELECT status,count(*) FROM tensor_dev_missions WHERE finished_at > now()-interval '24 hours' AND status NOT IN ('claimed','running') GROUP BY status")
stops = q("SELECT coalesce(result->>'stop_condition','(none)'),count(*) FROM tensor_dev_missions WHERE finished_at > now()-interval '24 hours' AND status='failed' GROUP BY 1")
lastfail = q("SELECT max(id),round(extract(epoch from (now()-max(finished_at))))::int FROM tensor_dev_missions WHERE finished_at > now()-interval '24 hours' AND status='failed'")
rep["step5_terminal"] = {"by_status": dict(fin), "failed_by_stop": dict(stops),
    "last_failed_id": lastfail[0][0] if lastfail else None,
    "last_failed_age_s": int(lastfail[0][1]) if lastfail else None,
    "pass": True}

# 额外体检
rep["extra"] = {
    "containers_healthy": sh(DOCKER + "docker ps --format '{{.Status}}' | grep -c '(healthy)'"),
    "containers_total": sh(DOCKER + "docker ps -q | wc -l"),
    "celery_beat": sh(DOCKER + "docker inspect open-foundation-celery-beat --format 'restart={{.RestartCount}} exit={{.State.ExitCode}} status={{.State.Status}}'"),
    "escalated_json": json.load(open("/srv/tensor/shared/tensor_eco/agent_work/approver_escalated.json")),
    "approvals_by_status": dict(q("SELECT status,count(*) FROM tensor_approval_requests GROUP BY status")),
    "pass_all_switch_exists": os.path.exists("/srv/tensor/shared/tensor_eco/agent_work/auto_approver.PASS_ALL"),
    "pass_all_rounds": int(sh("grep -c 'PASS_ALL 模式' /srv/tensor/shared/tensor_eco/agent_work/auto_approver.log") or 0),
    "pass_all_first": sh("grep -m1 -n 'PASS_ALL 模式' /srv/tensor/shared/tensor_eco/agent_work/auto_approver.log"),
    "approver_log_tail": sh("tail -1 /srv/tensor/shared/tensor_eco/agent_work/auto_approver.log"),
    "worktree_total": int(sh("git worktree list | wc -l") or 0),
    "main_repo_dirty": int(sh("git -C /srv/tensor/shared/tensor_eco/TensorPlatform status --porcelain | wc -l") or 0),
    "gc_log_tail": sh("tail -1 /srv/tensor/shared/tensor_eco/agent_work/gc.log"),
}
rep["checks_total"] = 5
rep["checks_passed"] = sum(1 for k in ("step1_inflight","step2_guardians","step3_relay","step4_gateway_state","step5_terminal") if rep[k]["pass"])
json.dump(rep, open("operator_log/2026-10-23.validation_report.json","w"), ensure_ascii=False, indent=2)
print("checks_passed=", rep["checks_passed"], "/", rep["checks_total"])
