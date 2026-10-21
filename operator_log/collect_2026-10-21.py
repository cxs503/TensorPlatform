#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m144 班次读数采集器：一条命令跑完巡检六步（+平台面体检），stdout 原样落 2026-10-21.readings.json。

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
    # 口径注记（playbook m132 增补）：裸 pgrep -f 可能把采集用 bash -lc 自身
    # 算进去（其 cmdline 含该模式串），故同时抄录排除 bash 包装的等价口径对账。
    ("2_guardian_count", "pgrep -f 'tensor_agen[t].py' | wc -l"),
    ("2_guardian_count_excl_bash",
     "ps -eo args | awk '/[t]ensor_agent\\.py/ && !/bash -lc/ {n++} END{print n+0}'"),
    ("2_guardian_detail",
     "ps -eo pid,args | awk '/[t]ensor_agent\\.py/ && !/bash -lc/ {print $1}' "
     "| while read p; do "
     "echo \"$p start=[$(ps -o lstart= -p $p | tr -s ' ')] cwd=$(readlink /proc/$p/cwd)\"; done"),

    # ---- 巡检第 3 步：中继健康 ----
    ("3_relay",
     "set -a; source {w}/review.env; set +a; "
     "curl -s -o /tmp/m144_models.json -w 'http=%{{http_code}} t=%{{time_total}}' "
     "-H \"Authorization: Bearer $LLM_API_KEY\" $LLM_BASE_URL/models; echo; "
     "python3 -c \"import json;d=json.load(open('/tmp/m144_models.json'));"
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
    ("5_approval_100plus",
     PSQL + "\"SELECT id||'|m'||coalesce(mission_id::text,'-')||'|'||status||'|by='||"
            "coalesce(decided_by::text,'-')||'|at='||"
            "coalesce(to_char(decided_at,'MM-DD HH24:MI:SS'),'-') "
            "FROM tensor_approval_requests WHERE id >= 100 ORDER BY id;\""),
    ("5_approval_drain_src",
     PSQL + "\"SELECT 'by='||coalesce(decided_by::text,'-')||' n='||count(*)||' '||"
            "to_char(min(decided_at),'MM-DD HH24:MI')||'..'||"
            "to_char(max(decided_at),'MM-DD HH24:MI') FROM tensor_approval_requests "
            "WHERE decided_at > now()-interval '3 hours' "
            "GROUP BY decided_by ORDER BY 1;\""),
    ("5_m142_final",
     PSQL + "\"SELECT id||'|'||status||'|'||"
            "coalesce(result->>'stop_condition','NULL')||'|head='||"
            "coalesce(result->>'head_commit','-') "
            "FROM tensor_dev_missions WHERE id IN (142,143,144) ORDER BY id;\""),
    ("5_m137_result",
     PSQL + "\"SELECT coalesce(result->>'stop_condition','NULL')||' tool_calls='||"
            "coalesce(result->'progress'->>'tool_calls','-')||' elapsed='||"
            "coalesce(result->'progress'->>'elapsed_seconds','-') "
            "FROM tensor_dev_missions WHERE id=137;\""),
    ("5_m137_branch",
     "echo head=$(git -C {w}/m137/TensorDEM rev-parse --short HEAD 2>/dev/null) "
     "ahead=$(git -C {w}/m137/TensorDEM rev-list --count HEAD --not e18d83b 2>/dev/null) "
     "dirty=$(git -C {w}/m137/TensorDEM status --short 2>/dev/null | wc -l)".format(w=WORK)),
    ("5_refill_check_rc",
     "set -a; source {w}/operator.env; set +a; "
     "for m in 100 112 119 126 137; do "
     "/srv/tensor/shared/venvs/tensor_eco/bin/python "
     "{p}/tools/agent/refill_check.py $m >/tmp/rc$m.txt 2>&1; "
     "echo \"m$m rc=$? $(tail -1 /tmp/rc$m.txt | cut -c1-70)\"; done"
     .format(w=WORK, p=PLAT)),

    # ---- 巡检第 6 步（playbook 增补）：容器体检 ----
    ("6_containers",
     "docker ps -a --format '{{.Names}}|{{.Status}}' | sort"),
    ("6_celery_beat_inspect",
     "docker inspect -f '{{.State.Status}} restart={{.RestartCount}} "
     "exit={{.State.ExitCode}}' open-foundation-celery-beat 2>&1"),
    ("6_celery_beat_pidfile_rounds",
     "docker logs open-foundation-celery-beat 2>&1 | grep -c Pidfile"),

    # ---- 审批器面：空转轮次 / 停车表残留 / 人工清扫留痕 ----
    ("6_approver_log_tail", "tail -6 {w}/auto_approver.log".format(w=WORK)),
    ("6_approver_escalate_rounds",
     "echo a103=$(grep -c 'approval 103' {w}/auto_approver.log 2>/dev/null) "
     "a104=$(grep -c 'approval 104' {w}/auto_approver.log 2>/dev/null) "
     "a106=$(grep -c 'approval 106' {w}/auto_approver.log 2>/dev/null) "
     "a107=$(grep -c 'approval 107' {w}/auto_approver.log 2>/dev/null) "
     "a111=$(grep -c 'approval 111' {w}/auto_approver.log 2>/dev/null) "
     "a112=$(grep -c 'approval 112' {w}/auto_approver.log 2>/dev/null) "
     "park_lines=$(grep -c '已 escalate 停车' {w}/auto_approver.log 2>/dev/null)"
     .format(w=WORK)),
    ("6_park_file",
     "python3 -c \"import json;d=json.load(open('{w}/approver_escalated.json'));"
     "print('parked=%d' % len(d));"
     "[print(k, v['mission'], v['ts'], v['note'][:70]) for k, v in sorted(d.items())]\""
     .format(w=WORK)),
    ("6_park_residual_vs_db",
     "python3 -c \"import json,subprocess;"
     "d=json.load(open('{w}/approver_escalated.json'));"
     "q=subprocess.run(['docker','exec','open-foundation-postgres','psql','-U','foundation',"
     "'-d','open_digital_foundation','-At','-c','SELECT id||chr(58)||status FROM "
     "tensor_approval_requests ORDER BY id'],capture_output=True,text=True).stdout;"
     "st=dict(l.split(':') for l in q.split() if ':' in l);"
     "print('parked_ids=%s' % sorted(d));"
     "print('db_status=%s' % [(k, st.get(k,'absent')) for k in sorted(d)])\""
     .format(w=WORK)),
    ("6_park_mtime", "stat -c '%y' {w}/approver_escalated.json".format(w=WORK)),

    # ---- R3 容量硬门（延续上报）：口径来源实证 ----
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
            "FROM tensor_approval_requests WHERE id IN (104,112) ORDER BY id;\""),
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

    # ---- 交付面自身状态（纪律亲验，交付前再录一次）----
    # 交付面自身状态：必须指向「本班自己的 worktree」（PLAT 是主仓，分支不同）
    ("8_worktree_self",
     "cd {w} && echo branch=$(git rev-parse --abbrev-ref HEAD) "
     "head=$(git rev-parse --short HEAD) dirty=$(git status --short | wc -l) "
     "ls_tree_log=$(git ls-tree -r --name-only HEAD -- operator_log/ | wc -l)"
     .format(w=WORKTREE)),

    # ---- R4（延续上报）：验收面自造脏树实证 ----
    ("8_m120_worktree_state",
     "d={w}/m120/TensorLBM; echo head=$(git -C $d rev-parse --short HEAD) "
     "dirty=$(git -C $d status --short | wc -l); git -C $d status --short".format(w=WORK)),
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
]


def main() -> int:
    readings = {
        "collected_by": "platform-operator m144 shift",
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