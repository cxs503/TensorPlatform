#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m156 班次读数采集器：一条命令跑完巡检五步（+平台面体检），stdout 原样落 2026-10-21.readings.json。

纪律：判据数字零手抄 —— 本脚本只做「跑命令 + 原样抄录 stdout/exit_code」，
不做任何解析、汇总或推断；汇总与结论在 operator_log/2026-10-21.md 中引用。
只读：无 psql 写、无 kill、无 API 写、无 docker 变更。
"""
import json
import os
import subprocess
import sys
import time

WORK = "/srv/tensor/shared/tensor_eco/agent_work"
ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")
PLAT = "/srv/tensor/shared/tensor_eco/TensorPlatform"
# 本班自己的 worktree（交付件落这里；PLAT 是主仓，仅用于平台侧观察）
WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PSQL = ("docker exec open-foundation-postgres psql -U foundation "
        "-d open_digital_foundation -At -F'|' -c ")

COMMANDS = [
    ("t0_utc", "date -u +%FT%TZ"),

    # ---- 巡检第 1 步：在飞 + 心跳鲜度（>240s = 即将收割）----
    ("1_inflight",
     PSQL + "\"SELECT id||'|'||status||'|'||to_char(heartbeat_at,'MM-DD HH24:MI:SS')"
            "||'|'||round(extract(epoch from now()-heartbeat_at))::int"
            "||'|'||coalesce(result->>'stop_condition','NULL')"
            "||'|'||coalesce(claimed_by,'-') FROM tensor_dev_missions "
            "WHERE status IN ('claimed','running') ORDER BY id;\""),

    # ---- 巡检第 2 步：守护计数（基线 10）----
    ("2_guardian_count", "pgrep -f 'tensor_agen[t].py' | wc -l"),
    ("2_guardian_count_excl_bash",
     "for i in 1 2 3; do "
     "ps -eo args | awk '/[t]ensor_agent\\.py/ && !/bash -lc/ {n++} END{print n+0}'; "
     "sleep 4; done | tr '\\n' ' '; echo"),
    ("2_guardian_detail",
     "ps -eo pid,args | awk '/[t]ensor_agent\\.py/ && !/bash -lc/ {print $1}' "
     "| while read p; do "
     "echo \"$p start=[$(ps -o lstart= -p $p | tr -s ' ')] cwd=$(readlink /proc/$p/cwd)\"; done"),

    # ---- 巡检第 3 步：中继健康 ----
    ("3_relay",
     "set -a; source {w}/review.env; set +a; "
     "curl -s -o /tmp/m149_models.json -w 'http=%{{http_code}} t=%{{time_total}}' "
     "-H \"Authorization: Bearer $LLM_API_KEY\" $LLM_BASE_URL/models; echo; "
     "python3 -c \"import json;d=json.load(open('/tmp/m149_models.json'));"
     "print('models=%d' % len(d.get('data',[])))\"".format(w=WORK)),

    # ---- 巡检第 4 步：网关冷却态 ----
    ("4_cooldown", "cat {w}/llm_gateway_state.json; echo".format(w=WORK)),

    # ---- 巡检第 5 步：终态 mission result + 审批 ----
    ("5_terminal_24h",
     PSQL + "\"SELECT status||'|'||count(*)||'|'||coalesce(result->>'stop_condition','NULL') "
            "FROM tensor_dev_missions WHERE status NOT IN ('claimed','running') "
            "AND created_at > now()-interval '24 hours' "
            "GROUP BY status, result->>'stop_condition' ORDER BY status;\""),
    ("5_failed_30h",
     PSQL + "\"SELECT id||'|'||to_char(heartbeat_at,'MM-DD HH24:MI')||'|'||"
            "coalesce(result->>'stop_condition','NULL')||'|'||"
            "round(extract(epoch from now()-heartbeat_at)/3600)::int||'h' "
            "FROM tensor_dev_missions WHERE status='failed' "
            "AND heartbeat_at > now()-interval '30 hours' ORDER BY id;\""),
    ("5_failed_30h_ids",
     PSQL + "\"SELECT 'ids='||string_agg(id::text,' ' ORDER BY id) "
            "FROM tensor_dev_missions WHERE status='failed' "
            "AND heartbeat_at > now()-interval '30 hours';\""),
    ("5_failed_24h_infra",
     PSQL + "\"SELECT id||'|'||to_char(heartbeat_at,'MM-DD HH24:MI')||'|'||"
            "coalesce(result->>'stop_condition','NULL') "
            "FROM tensor_dev_missions WHERE status='failed' "
            "AND heartbeat_at > now()-interval '24 hours' ORDER BY id;\""),
    ("5_approvals",
     PSQL + "\"SELECT status||'='||count(*) FROM tensor_approval_requests "
            "GROUP BY status ORDER BY 1;\""),
    ("5_approval_pending",
     PSQL + "\"SELECT coalesce(string_agg(id::text,' ' ORDER BY id),'(none)') "
            "FROM tensor_approval_requests WHERE status NOT IN ('approved','rejected');\""),
    ("5_approval_115plus",
     PSQL + "\"SELECT id||'|m'||coalesce(mission_id::text,'-')||'|'||status||'|by='||"
            "coalesce(decided_by::text,'-')||'|created='||"
            "to_char(created_at,'MM-DD HH24:MI:SS')||'|decided='||"
            "coalesce(to_char(decided_at,'MM-DD HH24:MI:SS'),'-') "
            "FROM tensor_approval_requests WHERE id >= 115 ORDER BY id;\""),
    ("5_approval_drain_src",
     PSQL + "\"SELECT 'by='||coalesce(decided_by::text,'-')||' n='||count(*)||' '||"
            "to_char(min(decided_at),'MM-DD HH24:MI')||'..'||"
            "to_char(max(decided_at),'MM-DD HH24:MI') FROM tensor_approval_requests "
            "WHERE decided_at > now()-interval '3 hours' "
            "GROUP BY decided_by ORDER BY 1;\""),
    ("5_approval_100plus",
     PSQL + "\"SELECT id||'|m'||coalesce(mission_id::text,'-')||'|'||status||'|by='||"
            "coalesce(decided_by::text,'-')||'|at='||"
            "coalesce(to_char(decided_at,'MM-DD HH24:MI:SS'),'-') "
            "FROM tensor_approval_requests WHERE id >= 100 ORDER BY id;\""),
    ("5_recent_final",
     PSQL + "\"SELECT id||'|'||status||'|'||"
            "coalesce(result->>'stop_condition','NULL')||'|head='||"
            "coalesce(result->>'head_commit','-') "
            "FROM tensor_dev_missions WHERE id IN (150,151,152,153,154,155,156) ORDER BY id;\""),
    ("5_m137_result",
     PSQL + "\"SELECT coalesce(result->>'stop_condition','NULL')||' tool_calls='||"
            "coalesce(result->'progress'->>'tool_calls','-')||' elapsed='||"
            "coalesce(result->'progress'->>'elapsed_seconds','-') "
            "FROM tensor_dev_missions WHERE id=137;\""),
    ("5_m126_result",
     PSQL + "\"SELECT coalesce(result->>'stop_condition','NULL')||' tool_calls='||"
            "coalesce(result->'progress'->>'tool_calls','-')||' headline='||"
            "left(coalesce(result->>'headline','-'),60)||' hb='||"
            "to_char(heartbeat_at,'MM-DD HH24:MI') "
            "FROM tensor_dev_missions WHERE id=126;\""),
    ("5_m137_branch",
     "echo head=$(git -C {w}/m137/TensorDEM rev-parse --short HEAD 2>/dev/null) "
     "ahead=$(git -C {w}/m137/TensorDEM rev-list --count HEAD --not e18d83b 2>/dev/null) "
     "dirty=$(git -C {w}/m137/TensorDEM status --short 2>/dev/null | wc -l)".format(w=WORK)),
    ("5_refill_check_rc",
     "set -a; source {w}/operator.env; set +a; "
     "for id in 100 112 119 126 137 150; do "
     "/srv/tensor/shared/venvs/tensor_eco/bin/python {p}/tools/agent/refill_check.py $id 2>/dev/null; "
     "echo \"m$id rc=$?\"; done".format(w=WORK, p=PLAT)),

    # ---- 巡检第 6 步：容器体检（N 类延续）----
    ("6_containers",
     "docker ps --format '{{.Names}}|{{.Status}}' | sort"),
    ("6_celery_beat_inspect",
     "docker inspect open-foundation-celery-beat --format "
     "'restart={{.RestartCount}} exitcode={{.State.ExitCode}}' 2>&1"),

    # ---- R3/R4 停车表 + 容量门 ----
    ("6_park_file",
     "python3 -c \"import json;d=json.load(open('{w}/approver_escalated.json'));"
     "print('parked='+str(len(d)));"
     "[print(k+' '+str(v.get('mission','-'))+' '+v.get('note','')[:40]) for k,v in sorted(d.items())]\""
     .format(w=WORK)),
    ("6_repo_suite_capacity",
     "cd {p} && /srv/tensor/shared/hermes-agent/.venv/bin/python -m pytest -x -q tests "
     "2>&1 | tail -1".format(p=PLAT)),
    ("6_r3_lock_evidence",
     PSQL + "\"SELECT id||'|claimed='||coalesce(evidence->'checks'->>'total','-')||"
            "'|excerpt_checks='||coalesce(jsonb_array_length("
            "evidence->'summary'->'evidence_machine'->'files'->0->'checks')::text,'0')||"
            "'|passed_null='||coalesce((SELECT count(*)::text FROM "
            "jsonb_array_elements(evidence->'summary'->'evidence_machine'->'files'->0->'checks') e "
            "WHERE (e->>'passed') IS NULL),'0') "
            "FROM tensor_approval_requests WHERE id IN (104,112,113,114,115,116,117,118,119) ORDER BY id;\""),
    ("6_r3_capacity_truncation",
     "grep -n 'checks\\[:20\\]' {p}/tools/agent/tensor_agent.py; "
     "grep -n 'def checks_capacity' {p}/tools/agent/auto_approver.py".format(p=PLAT)),

    # ---- 换血判据（案例 3/7）：修复面落盘 vs 守护启动 ----
    ("7_last_commit",
     "git -C {p} log -1 --format='%h %ad %s' --date=format:'%m-%d %H:%M:%S' | cat"
     .format(p=PLAT)),
    ("7_import_face_commit",
     "git -C {p} log -1 --format='%h %ad' --date=format:'%m-%d %H:%M:%S' -- "
     "tools/agent/tensor_agent.py | cat".format(p=PLAT)),
    ("7_face_diff_vs_8e548e7",
     "git -C {p} diff --stat 8e548e7..HEAD -- tools/agent/tensor_agent.py | cat; "
     "echo hunks=$(git -C {p} diff 8e548e7..HEAD -- tools/agent/tensor_agent.py | grep -c '^@@')"
     .format(p=PLAT)),
    ("7_busy_guardian_claims",
     "for m in qwen3.6-plus glm-5.2 qwen3.7-plus deepseek-v4-flash Qwen3.8-27B glm-5.1 "
     "MiniMax-M2.5-AWQ; do echo \"$m: $(grep -h 'claimed mission' "
     "{w}/agent-$m.log 2>/dev/null | tail -1 | cut -c1-90)\"; done".format(w=WORK)),

    # ---- 审批器「裁决输出不可解析」类是否已由 v6 消解 ----
    ("7_approver_parsefail",
     "echo total=$(grep -c '裁决输出不可解析' {w}/auto_approver.log); "
     "grep -n '裁决输出不可解析' {w}/auto_approver.log | tail -1".format(w=WORK)),
    ("7_approver_version_commits",
     "git -C {p} log -1 --format='v6=%h %ad' --date=format:'%m-%d %H:%M:%S' 435e9c8; "
     "git -C {p} log -1 --format='head=%h %ad' --date=format:'%m-%d %H:%M:%S'".format(p=PLAT)),
    ("7_capacity_gate_rounds",
     "echo over_capacity=$(grep -c '超过其口径容量' {w}/auto_approver.log) "
     "dirty_tree=$(grep -c '工作树有' {w}/auto_approver.log) "
     "log_lines=$(wc -l < {w}/auto_approver.log)".format(w=WORK)),

    # ---- 池化 worker 与 workers.json 机械对账 ----
    ("7_pool_reconcile",
     "python3 -c \"import json;c=json.load(open('{p}/tools/agent/workers.json'));"
     "print('expected_extra_slots=%d' % sum(max(0,v-1) for v in c.values() if isinstance(v,int)))\""
     "; echo actual_slot_workers=$(ps -eo args | grep -c '[t]ensor_agent.py .*--slot [2-9]')".format(p=PLAT)),
    ("7_pool_slot_detail",
     "ps -eo pid,args | awk '/[t]ensor_agent[.]py/ && /--slot [2-9]/ {print $1}'"),
    ("7_pool_cwd_probe",
     "for p in $(ps -eo pid,args | awk '/[t]ensor_agent[.]py/ && /--slot [2-9]/ {print $1}'); "
     "do echo \"$p cwd=$(readlink /proc/$p/cwd)\"; done; "
     "echo dir_exists=$([ -e " + WORK + "/wt_workers ] && echo yes || echo no); "
     "echo repo_refs=$(grep -rl wt_workers " + PLAT + "/tools 2>/dev/null | wc -l)"),
    ("7_approver_verify_timeout",
     "echo verifier_timeout_total=$(grep -c 'verifier 异常' {w}/auto_approver.log); "
     "grep -n 'verifier 异常' {w}/auto_approver.log | tail -2".format(w=WORK)),
    ("7_approver_116_reason",
     "grep -n 'approval 116' {w}/auto_approver.log | head -2".format(w=WORK)),
    ("7_approver_park_111",
     "grep -n 'approval 111' {w}/auto_approver.log | tail -1".format(w=WORK)),

    # ---- 交付面自身状态（纪律亲验，交付前再录一次）----
    ("8_worktree_self",
     "cd {w} && echo branch=$(git rev-parse --abbrev-ref HEAD) "
     "head=$(git rev-parse --short HEAD) dirty=$(git status --short | wc -l) "
     "ls_tree_log=$(git ls-tree -r --name-only HEAD -- operator_log/ | wc -l)"
     .format(w=WORKTREE)),

    # ---- R4（延续上报）：验收面自造脏树实证 ----
    ("8_m120_worktree_state",
     "d={w}/m120/TensorLBM; echo head=$(git -C $d rev-parse --short HEAD) "
     "dirty=$(git -C $d status --short | wc -l); git -C $d status --short".format(w=WORK)),
    ("8_m147_worktree_state",
     "d={w}/m147/TensorPhysics; echo head=$(git -C $d rev-parse --short HEAD 2>/dev/null) "
     "dirty=$(git -C $d status --short 2>/dev/null | wc -l) "
     "gates_verified=$(find $d -name gates_verified.json 2>/dev/null | wc -l) "
     "head_date=$(git -C $d log -1 --format=%ci 2>/dev/null)".format(w=WORK)),
    # ---- R4 第五例（本班新）：m145 交付面被 ops 层（benchmark_dispatch）复算改写 ----
    ("8_m145_worktree_state",
     "d={w}/m145/TensorLBM; echo head=$(git -C $d rev-parse --short HEAD) "
     "commit_time=$(git -C $d show -s --format=%ci HEAD | cut -c1-19) "
     "dirty=$(git -C $d status --short | wc -l); git -C $d status --short".format(w=WORK)),
    ("8_m145_gates_mtimes",
     "find {w}/m145/TensorLBM -name gates_verified.json "
     "-printf '%TY-%Tm-%Td %TH:%TM:%TS %p\\n' 2>/dev/null".format(w=WORK)),
    ("8_m145_second_writer",
     "grep -n 'm145: gates 复算通过' {w}/benchmark_closure/benchmark_dispatch.log | tail -2; "
     "grep -n 'gates_verified.json' {w}/benchmark_closure/benchmark_dispatch.py | head -3; "
     "grep -n 'dest = gp.parent' {w}/benchmark_closure/gates_check.py | head -3".format(w=WORK)),
    ("8_m151_worktree_state",
     "d={w}/m151/TensorPhysics; echo head=$(git -C $d rev-parse --short HEAD 2>/dev/null) "
     "dirty=$(git -C $d status --short 2>/dev/null | wc -l); "
     "git -C $d status --short 2>/dev/null".format(w=WORK)),
    ("8_m150_worktree_state",
     "d={w}/m150/TensorLBM; echo head=$(git -C $d rev-parse --short HEAD 2>/dev/null) "
     "dirty=$(git -C $d status --short 2>/dev/null | wc -l)".format(w=WORK)),
    ("8_m120_gates_preflight_source",
     "D={w}/m120/TensorLBM/benchmarks/pending/cylinder_re40_st/gates_verified.json; "
     "echo file_mtime=$(stat -c '%y' $D | cut -c1-19); "
     "echo commit_time=$(git -C {w}/m120/TensorLBM show -s --format=%ci f459f4bf | cut -c1-19)"
     .format(w=WORK)),
    ("8_m120_ckpt_payload_gate",
     "python3 -c \"import json;d=json.load(open('{w}/m120/state/native_messages.json'));"
     "ms=d.get('messages',d) if isinstance(d,dict) else d;print('msgs=%d' % len(ms))\""
     .format(w=WORK)),

    # ---- 延续上报项复核 ----
    ("9_fleet_log", "tail -3 {w}/fleet.log 2>&1".format(w=WORK)),
    ("9_crontab_fleet", "crontab -l 2>/dev/null | grep -c fleet"),
    ("9_gc_log",
     "echo total=$(wc -l < {w}/gc.log) "
     "perms=$(stat -c '%a %n' {p}/tools/agent/gc.py) "
     "denied=$(grep -c '权限不够' {w}/gc.log)".format(w=WORK, p=PLAT)),
    ("9_worktree_count",
     "git -C {p} worktree list | wc -l".format(p=PLAT)),
    ("9_main_dirty",
     "git -C {p} status --short | wc -l".format(p=PLAT)),
    ("9_main_dirty_items",
     "git -C {p} status --short".format(p=PLAT)),
    ("9_operator_shift_tail", "tail -4 {w}/operator_shift.log".format(w=WORK)),
    ("9_approver_post_drain",
     "echo log_lines=$(wc -l < {w}/auto_approver.log); "
     "grep -n 'pending=' {w}/auto_approver.log | tail -2; "
     "grep -n 'approval 119' {w}/auto_approver.log | head -2; "
     "echo last_ts=$(grep -oE '^\\[[0-9:]+\\]' {w}/auto_approver.log | tail -1)"
     .format(w=WORK)),
    ("9_approval_totals",
     PSQL + "\"SELECT 'total='||count(*)||' maxid='||max(id)||' pending='||"
            "count(*) FILTER (WHERE status NOT IN ('approved','rejected')) "
            "FROM tensor_approval_requests;\""),

    # ---- R6（本班新识别）：owner stop 后 mission stopped 但 worker 未退（僵尸循环）----
    ("10_m151_row",
     PSQL + "\"SELECT id||'|'||status||'|stop_condition='||"
            "coalesce(result->>'stop_condition','NULL')||'|hb_age_s='||"
            "round(extract(epoch from now()-heartbeat_at))::int||'|head='||"
            "coalesce(result->'progress'->>'head','-')||'|tools='||"
            "coalesce(result->'progress'->>'tool_calls','-')||'|claimed_by='||"
            "coalesce(claimed_by,'-') FROM tensor_dev_missions WHERE id=151;\""),
    ("10_m151_stop_api",
     "docker logs open-foundation-backend --since 6h 2>&1 | "
     "grep 'missions/151/stop' | tail -2"),
    ("10_m151_zombie_loop",
     "echo acceptance_403_lines=$(grep -c 'm151: acceptance HTTP 403' "
     "{w}/agent-qwen3.7-plus.log); "
     "grep -n 'm151: acceptance HTTP 403' {w}/agent-qwen3.7-plus.log | head -1; "
     "grep -n 'm151: acceptance HTTP 403' {w}/agent-qwen3.7-plus.log | tail -1; "
     "grep -n 'm151: run ended without acceptance' {w}/agent-qwen3.7-plus.log | tail -1; "
     "echo state_mtime=$(stat -c '%y' {w}/m151/state/native_messages.json | cut -c1-19)"
     .format(w=WORK)),
    ("10_m151_refill_rc",
     "set -a; source {w}/operator.env; set +a; "
     "/srv/tensor/shared/venvs/tensor_eco/bin/python {p}/tools/agent/refill_check.py 151 2>/dev/null; "
     "echo \"m151 rc=$?\"".format(w=WORK, p=PLAT)),
    ("10_m150_row",
     PSQL + "\"SELECT id||'|'||status||'|'||coalesce(result->>'stop_condition','NULL')||"
            "'|tools='||coalesce(result->'progress'->>'tool_calls','-')||"
            "'|head='||coalesce(result->'progress'->>'head','-')||'|hb='||"
            "to_char(created_at,'MM-DD HH24:MI')||'..'||to_char(heartbeat_at,'MM-DD HH24:MI') "
            "FROM tensor_dev_missions WHERE id=150;\""),
    ("10_m150_worktree",
     "d={w}/m150/TensorLBM; echo head=$(git -C $d rev-parse --short HEAD) "
     "head_date=$(git -C $d log -1 --format=%ci | cut -c1-19) "
     "dirty=$(git -C $d status --short | wc -l)".format(w=WORK)),
    ("10_m150_log",
     "grep -n 'm150' {w}/agent-qwen3.7-plus-w2.log | tail -4".format(w=WORK)),

    # ---- m156 新块：R3 第八例（#122 = 上班 m152 operator 交付）+ #121 证据不足停车 ----
    ("10_m152_approval",
     PSQL + "\"SELECT id||'|m'||coalesce(mission_id::text,'-')||'|'||status||"
            "'|claimed='||coalesce(evidence->'checks'->>'total','-')||"
            "'|passed='||coalesce(evidence->'checks'->>'passed','-')||"
            "'|excerpt='||coalesce(jsonb_array_length(evidence->'summary'->"
            "'evidence_machine'->'files'->0->'checks')::text,'0')||'|created='||"
            "to_char(created_at,'MM-DD HH24:MI:SS')||'|decided='||"
            "coalesce(to_char(decided_at,'MM-DD HH24:MI:SS'),'-') "
            "FROM tensor_approval_requests WHERE id IN (121,122) ORDER BY id;\""),
    ("10_r3_park_growth",
     "grep -n 'approval 12[12]' {w}/auto_approver.log | tail -3; "
     "python3 -c \"import json;d=json.load(open('{w}/approver_escalated.json'));"
     "print('parked=%d' % len(d));"
     "print('keys='+' '.join(sorted(d.keys(),key=int)))\"".format(w=WORK)),
    ("10_beat_crash_rate",
     "export DOCKER_HOST=unix:///run/user/1020/docker.sock; "
     "b=''; first=''; last=''; "
     "for i in 1 2 3 4 5; do "
     "v=$(docker inspect open-foundation-celery-beat --format '{{.RestartCount}}'); "
     "b=$b' '$v; "
     "if [ -z $first ]; then first=$v; fi; last=$v; sleep 20; done; "
     "echo restart_seq=$b; echo first=$first last=$last delta=$((last-first))_per_100s; "
     "docker inspect open-foundation-celery-beat --format "
     "'status={{.State.Status}} restart={{.RestartCount}} exitcode={{.State.ExitCode}}'"),
    ("10_m151_zombie_recheck",
     "echo m151_403_lines=$(grep -c 'm151: acceptance HTTP 403' "
     "{w}/agent-qwen3.7-plus.log); "
     "echo total_403_lines_all_logs=$(grep -h 'acceptance HTTP 403' "
     "{w}/agent-*.log 2>/dev/null | wc -l); "
     "echo last_m151_403=$(grep -h 'm151: acceptance HTTP 403' "
     "{w}/agent-qwen3.7-plus.log | tail -1 | cut -c1-12)".format(w=WORK)),
    ("10_m155_row",
     PSQL + "\"SELECT id||'|'||status||'|tools='||"
            "coalesce(result->'progress'->>'tool_calls','-')||'|hb_age_s='||"
            "round(extract(epoch from now()-heartbeat_at))::int||'|claimed_by='||"
            "coalesce(claimed_by,'-') FROM tensor_dev_missions WHERE id>=155 ORDER BY id;\""),
    # ---- 本班新识别 F：operator_shift 班次号回退（分页口径）----
    ("10_shift_counter",
     "docker exec open-foundation-postgres psql -U foundation "
     "-d open_digital_foundation -At -c \""
     "SELECT 'marker_in_last100_page='||count(*) FROM (SELECT goal FROM "
     "tensor_dev_missions ORDER BY id DESC LIMIT 100) t "
     "WHERE goal LIKE '%值班班次%';\"; "
     "docker exec open-foundation-postgres psql -U foundation "
     "-d open_digital_foundation -At -c \""
     "SELECT 'marker_all='||count(*)||' max_id='||max(id) FROM "
     "tensor_dev_missions WHERE goal LIKE '%值班班次%';\"; "
     "grep -oE 'shift#[0-9]+' {w}/operator_shift.log | tail -2; "
     "tail -1 {w}/operator_shift.log".format(w=WORK)),

    # ---- 本班新观察 R7：审批器证据面 —— 摘录无锚数值 / 摘要与复跑数字不一致 → 证据不足停车 ----
    ("10_r7_evidence",
     PSQL + "\"SELECT id||'|m'||coalesce(mission_id::text,'-')||'|'||status||"
            "'|files='||coalesce(jsonb_array_length(evidence->'summary'->"
            "'evidence_machine'->'files')::text,'-')||'|file0_keys='||"
            "coalesce((SELECT string_agg(k,',' ORDER BY k) FROM "
            "jsonb_object_keys(evidence->'summary'->'evidence_machine'->'files'->0) k),'-')||"
            "'|has_checks='||coalesce((evidence->'summary'->'evidence_machine'->'files'->0 "
            "? 'checks')::text,'-') FROM tensor_approval_requests "
            "WHERE id IN (110,121,122) ORDER BY id;\""
     "; grep -n 'approval 110' {w}/auto_approver.log | tail -1; "
     "grep -n 'approval 121' {w}/auto_approver.log | head -1".format(w=WORK)),

    ("10_inflight_final",
     PSQL + "\"SELECT id||'|'||status||'|'||"
            "round(extract(epoch from now()-heartbeat_at))::int||'|'||"
            "coalesce(claimed_by,'-') FROM tensor_dev_missions "
            "WHERE status IN ('claimed','running') ORDER BY id;\""),
]


def main() -> int:
    readings = {
        "collected_by": "platform-operator m156 shift",
        "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": ("每条为命令原文 + stdout 原样 + 退出码；本文件由 "
                 "collect_2026-10-21.py 机器生成，零手抄"),
        "items": [],
    }
    for name, cmd in COMMANDS:
        t0 = time.time()
        # UTF-8 显式解码（案例 12 采集器缺陷修复，务必沿用）：locale 非 UTF-8 时
        # text=True 会按 ANSI 解码，产出非 UTF-8 字节导致 JSON 工件不可读。
        p = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=ENV)
        readings["items"].append({
            "name": name,
            "command": cmd,
            "exit_code": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
            "elapsed_s": round(time.time() - t0, 3),
        })
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "2026-10-21.readings.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(readings, fh, ensure_ascii=False, indent=2)
    n = len(readings["items"])
    print(f"collected {n} readings -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
