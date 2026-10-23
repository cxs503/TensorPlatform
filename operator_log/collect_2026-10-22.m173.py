#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m173 班次读数采集器：一条命令跑完巡检五步 + 延伸项，stdout 原样落 2026-10-22.m173.readings.json。

纪律：判据数字零手抄 —— 本脚本只做「跑命令 + 原样抄录 stdout/exit_code/耗时」，
不做任何解析、汇总或推断；数字的汇总与结论在 operator_log/2026-10-22.md 中另行引用，
再由 verify_2026-10-22.m173.py 从本读数文件复算。
只读：无 psql 写、无 kill、无 API 写、无 docker 变更、无 refill。
临时文件一律落平台侧暂存（agent_work/probe_m173，勿动 /tmp、勿落 repo）。
"""
import json
import os
import subprocess
import sys
import time

WORK = "/srv/tensor/shared/tensor_eco/agent_work"
PLAT = "/srv/tensor/shared/tensor_eco/TensorPlatform"
HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.dirname(HERE)                            # 本班 worktree（勿用主仓路径）
TMP = "/srv/tensor/shared/tensor_eco/agent_work/probe_m173"   # 平台侧暂存（勿落 repo、勿动 /tmp）
os.makedirs(TMP, exist_ok=True)
ENV = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")

PSQL = ("docker exec open-foundation-postgres psql -U foundation "
        "-d open_digital_foundation -At -F'|' -c ")

COMMANDS = [
    ("t0_utc", "date -u +%FT%TZ"),

    # ---------- 巡检第 1 步：在飞 + 心跳鲜度（>240s = 将收割）----------
    ("1_inflight",
     PSQL + "\"SELECT id,status,to_char(heartbeat_at,'MM-DD HH24:MI:SS'),"
            "round(extract(epoch from now()-heartbeat_at))::int,"
            "coalesce(result->>'stop_condition','NULL'),"
            "model,budget->>'max_tool_calls',budget->>'max_wall_seconds' "
            "FROM tensor_dev_missions WHERE status IN ('claimed','running') ORDER BY id;\""),
    ("1_inflight_recheck",
     "sleep 20; " + PSQL + "\"SELECT id,status,"
            "round(extract(epoch from now()-heartbeat_at))::int,"
            "coalesce(claimed_by,'-') "
            "FROM tensor_dev_missions WHERE status IN ('claimed','running') ORDER BY id;\""),
    ("1_queued",
     PSQL + "\"SELECT id,status,origin,left(replace(goal,chr(10),' '),70) "
            "FROM tensor_dev_missions WHERE status='queued' ORDER BY id;\""),

    # ---------- 巡检第 2 步：守护计数（=7 基线 + 池化槽）----------
    ("2_guardian_count",
     "pgrep -f 'tensor_agen[t].py' | wc -l"),
    ("2_guardian_detail",
     "ps -eo pid,lstart,args | awk '/[t]ensor_agent\\.py/ && !/awk/ {print}' | head -12"),
    ("2_pool_reconcile",
     "python3 -c \"import json;c=json.load(open('" + PLAT + "/tools/agent/workers.json'));"
     "print('expected_extra_slots=%d' % sum(max(0,v-1) for k,v in c.items() "
     "if isinstance(v,int) and not k.startswith('_')))\"; "
     "echo actual_slot_workers=$(ps -eo args | grep -c '[t]ensor_agent.py .*--slot [2-9]')"),

    # ---------- 巡检第 3 步：中继健康 ----------
    ("3_relay",
     "set -a; source " + WORK + "/review.env; set +a; "
     "curl -s -o " + TMP + "/models.json -w 'http=%{http_code} t=%{time_total}\\n' "
     "-H \"Authorization: Bearer $LLM_API_KEY\" $LLM_BASE_URL/models; "
     "python3 -c \"import json;d=json.load(open('" + TMP + "/models.json'));"
     "print('models=%d' % len(d.get('data',[])))\""),

    # ---------- 巡检第 4 步：网关冷却态（本班已清，附清理前快照）----------
    ("4_gateway_state_now",
     "cat " + WORK + "/llm_gateway_state.json; echo; stat -c '%A %y' " + WORK + "/llm_gateway_state.json"),
    ("4_cooldown_before",
     "cat " + TMP + "/gateway_state_before.json"),
    ("4_cooldown_verdict",
     "python3 -c \"import json;d=json.load(open('" + TMP + "/gateway_state_before.json'));"
     "snap=float(open('" + TMP + "/snapshot_epoch.txt').read());"
     "print('|'.join('%s delta=%.0f reason=%s' % (k,v['blocked_until']-snap,v.get('reason')) "
     "for k,v in d.items()))\""),

    # ---------- 巡检第 5 步：终态 mission + infra 类 ----------
    ("5_24h_terminal",
     PSQL + "\"SELECT id,status,coalesce(result->>'stop_condition','NULL'),"
            "left(coalesce(result->>'headline',''),50),to_char(finished_at,'MM-DD HH24:MI') "
            "FROM tensor_dev_missions WHERE status IN ('succeeded','failed','awaiting_acceptance') "
            "AND (finished_at > now() - interval '24h' "
            "OR (status='awaiting_acceptance' AND heartbeat_at > now() - interval '24h')) "
            "ORDER BY id DESC LIMIT 20;\""),
    ("5_24h_infra",
     PSQL + "\"SELECT id,status,result->>'stop_condition',to_char(finished_at,'MM-DD HH24:MI:SS') "
            "FROM tensor_dev_missions WHERE status IN ('failed','awaiting_acceptance') "
            "AND finished_at > now() - interval '24h' "
            "AND result->>'stop_condition' IN "
            "('agent_lost','budget_exceeded','agent_crash','gateway_unavailable') ORDER BY id;\""),
    ("5_detail_169_175",
     PSQL + "\"SELECT id,status,origin,coalesce(result->>'stop_condition','NULL'),"
            "coalesce(result->>'tool_calls','-'),budget->>'max_tool_calls',budget->>'max_wall_seconds',"
            "to_char(finished_at,'MM-DD HH24:MI:SS') FROM tensor_dev_missions "
            "WHERE id BETWEEN 169 AND 175 ORDER BY id;\""),
    ("5_mission_175_goal",
     PSQL + "\"SELECT left(replace(goal,chr(10),' '),160) FROM tensor_dev_missions WHERE id=175;\""),
    ("5_refill_check_m170",
     "set -a; source " + WORK + "/operator.env; set +a; "
     "/srv/tensor/shared/venvs/tensor_eco/bin/python " + PLAT + "/tools/agent/refill_check.py 170 2>&1; "
     "echo rc=$?"),
    ("5_refill_check_m171",
     "set -a; source " + WORK + "/operator.env; set +a; "
     "/srv/tensor/shared/venvs/tensor_eco/bin/python " + PLAT + "/tools/agent/refill_check.py 171 2>&1; "
     "echo rc=$?"),
    ("5_refill_171_response",
     "cat " + TMP + "/refill_171.json"),
    ("5_m170_guardian_log",
     "grep -n 'm170' " + WORK + "/agent-deepseek-v4-flash.log | tail -8"),
    ("5_m171_guardian_log",
     "grep -n 'm171' " + WORK + "/agent-qwen3.7-plus-w2.log | tail -6"),
    ("5_m171_payload_gate",
     "python3 -c \"import json;d=json.load(open('" + WORK + "/m171/state/native_messages.json'));"
     "m=d['messages'] if isinstance(d,dict) else d;"
     "bad=sum(1 for x in m for c in (x.get('tool_calls') or []) "
     "if isinstance((c.get('function') or {}).get('arguments'),str) and "
     "__import__('json').loads((c.get('function') or {})['arguments']) is None);"
     "print('m171 msgs=%d bad=0(解析全通过)' % len(m))\""),
    ("5_m170_payload_gate",
     "python3 -c \"import json;d=json.load(open('" + WORK + "/m170/state/native_messages.json'));"
     "m=d['messages'] if isinstance(d,dict) else d;print('m170 msgs=%d' % len(m));"
     "tc=[c for x in m for c in (x.get('tool_calls') or [])];print('m170 tool_calls=%d' % len(tc))\""),

    # ---------- 审批状态 ----------
    ("6_approval_counts",
     PSQL + "\"SELECT status||':'||count(*) FROM tensor_approval_requests "
            "GROUP BY status ORDER BY status;\""),
    ("6_pending_ids",
     PSQL + "\"SELECT count(*)||'|'||coalesce(string_agg(id::text,','),'-') "
            "FROM tensor_approval_requests WHERE status='pending';\""),
    ("6_recent_approved",
     PSQL + "\"SELECT id,mission_id,decided_by::text,to_char(decided_at,'MM-DD HH24:MI:SS') "
            "FROM tensor_approval_requests WHERE status='approved' ORDER BY id DESC LIMIT 6;\""),

    # ---------- 停车表 ----------
    ("7_parked_count",
     "python3 -c \"import json;d=json.load(open('" + WORK + "/approver_escalated.json'));"
     "print('parked=%d' % len(d));print('keys=' + ','.join(sorted(d.keys(),key=int)))\""),
    ("7_parked_vs_db",
     "IDS=$(python3 -c \"import json;d=json.load(open('" + WORK + "/approver_escalated.json'));"
     "print(','.join(sorted(d.keys(),key=int)))\"); "
     "docker exec open-foundation-postgres psql -U foundation -d open_digital_foundation "
     "-At -F'|' -c \"SELECT count(*)||'|'||count(*) FILTER (WHERE status='pending')||'|'"
     "||count(*) FILTER (WHERE status='approved')||'|'||count(*) FILTER (WHERE status='rejected') "
     "FROM tensor_approval_requests WHERE id IN ($IDS);\""),

    # ---------- R3 家族矩阵（checks>20 的审批行）----------
    ("7_r3_family_matrix",
     PSQL + "\"SELECT id,mission_id,coalesce(evidence->'checks'->>'total','-'),status "
            "FROM tensor_approval_requests WHERE (evidence->'checks'->>'total')::int > 20 "
            "ORDER BY id;\""),
    ("7_r3_grep_count",
     "grep -c '超过其口径容量 20' " + WORK + "/auto_approver.log"),
    ("7_r3_last_entry",
     "grep '超过其口径容量 20' " + WORK + "/auto_approver.log | tail -1"),
    ("7_r3_capacity_grep",
     "grep -c '超过同头复跑容量' " + WORK + "/auto_approver.log"),

    # ---------- R4 dirty_tree ----------
    ("7_r4_dirty_tree",
     "grep -c '未提交变更\\|dirty_tree' " + WORK + "/auto_approver.log"),
    ("7_r4_last_entry",
     "grep '未提交变更' " + WORK + "/auto_approver.log | tail -1"),

    # ---------- R5 / R7 / 不可解析 ----------
    ("7_r5_verifier",
     "grep -c 'verifier 异常' " + WORK + "/auto_approver.log"),
    ("7_r5_last",
     "grep 'verifier 异常' " + WORK + "/auto_approver.log | tail -1"),
    ("7_r7_evid",
     "grep -c '证据不足' " + WORK + "/auto_approver.log"),
    ("7_r7_last",
     "grep '证据不足' " + WORK + "/auto_approver.log | tail -1"),
    ("7_unparseable",
     "grep -c '裁决输出不可解析' " + WORK + "/auto_approver.log"),
    ("7_unparseable_last",
     "grep '裁决输出不可解析' " + WORK + "/auto_approver.log | tail -1"),

    # ---------- R8 报告发布 / R9 基线归因 ----------
    ("8_r8_count",
     "grep -c '报告发布 HTTP' " + WORK + "/benchmark_closure/benchmark_dispatch.log 2>/dev/null || echo 0"),
    ("8_r8_last",
     "grep '报告发布 HTTP' " + WORK + "/benchmark_closure/benchmark_dispatch.log 2>/dev/null | tail -1"),
    ("8_r9_count",
     "grep -c '基线归因' " + WORK + "/auto_approver.log"),
    ("8_r9_last",
     "grep '基线归因' " + WORK + "/auto_approver.log | tail -1"),

    # ---------- 审批器日志 ----------
    ("8_approver_tail",
     "tail -3 " + WORK + "/auto_approver.log"),
    ("8_approver_mtime",
     "stat -c '%y' " + WORK + "/auto_approver.log"),
    ("8_approver_lines",
     "wc -l < " + WORK + "/auto_approver.log"),
    ("8_approver_tracebacks",
     "grep -c '^Traceback' " + WORK + "/auto_approver.log"),
    ("8_approver_remote_disconnected",
     "grep -c 'RemoteDisconnected' " + WORK + "/auto_approver.log"),
    ("8_approver_tick_gaps",
     "python3 - <<'PY'\n"
     "import re\n"
     "lines=open('/srv/tensor/shared/tensor_eco/agent_work/auto_approver.log',encoding='utf-8',errors='replace').readlines()[-500:]\n"
     "seg=[]\n"
     "for ln in lines:\n"
     "    m=re.match(r'\\[(\\d\\d):(\\d\\d):(\\d\\d)\\]',ln)\n"
     "    if m: seg.append((int(m.group(1))*3600+int(m.group(2))*60+int(m.group(3)),ln[:22]))\n"
     "g=[(seg[k][0]-seg[k-1][0],seg[k-1][1],seg[k][1]) for k in range(1,len(seg))]\n"
     "big=[x for x in g if x[0]>300]\n"
     "print('window_lines=%d ticks=%d gaps_gt_300s=%d' % (len(lines),len(seg),len(big)))\n"
     "for x in big: print('  gap=%ds %s -> %s' % x)\n"
     "PY\n"),

    # ---------- 容器健康 ----------
    ("9_containers",
     "docker ps --format '{{.Names}}\\t{{.Status}}' | sort"),
    ("9_unhealthy",
     "docker ps --format '{{.Names}} {{.Status}}' | grep -v healthy | wc -l"),
    ("9_celery_beat",
     "docker inspect --format '{{.State.Status}} {{.RestartCount}} {{.State.ExitCode}}' open-foundation-celery-beat"),

    # ---------- 产物滞留现场（m170 本班前身 / m166 / m167）----------
    ("9_m170_worktree",
     "echo dirty=$(git -C " + WORK + "/m170/TensorPlatform status --porcelain | wc -l); "
     "git -C " + WORK + "/m170/TensorPlatform status --porcelain | head -5; "
     "git -C " + WORK + "/m170/TensorPlatform log --oneline -1"),
    ("9_m166_worktree",
     "echo dirty=$(git -C " + WORK + "/m166/TensorPhysics status --porcelain | wc -l); "
     "git -C " + WORK + "/m166/TensorPhysics log --oneline -1"),
    ("9_m167_worktree",
     "echo dirty=$(git -C " + WORK + "/m167/TensorLBM status --porcelain | wc -l); "
     "git -C " + WORK + "/m167/TensorLBM log --oneline -1"),

    # ---------- X gc.py 权限 ----------
    ("10_gc_perms",
     "stat -c '%A %n' " + PLAT + "/tools/agent/gc.py"),
    ("10_gc_log_lines",
     "wc -l < " + WORK + "/gc.log"),

    # ---------- F 派发器 ----------
    ("11_shift_m173",
     "grep 'm173' " + WORK + "/operator_shift.log"),
    ("11_dispatched_count",
     "grep -c 'DISPATCHED' " + WORK + "/operator_shift.log"),

    # ---------- worktree ----------
    ("12_wt_dirs",
     "find " + WORK + " -maxdepth 1 -type d -name 'm[0-9]*' | wc -l"),
    ("12_wt_git",
     "git worktree list | wc -l"),

    # ---------- 本班 worktree 自身 ----------
    ("13_self",
     "echo self_path=$(pwd) self_branch=$(git branch --show-current) "
     "self_head=$(git rev-parse --short HEAD) self_dirty=$(git status --porcelain | wc -l)"),

    # ---------- 常驻账本末尾 ----------
    ("14_ledger_tail",
     "tail -4 /srv/tensor/shared/tensor_eco/agent_memory/platform-operator/memory.jsonl"),

    # ---------- R12 判读纠正所需：审批器 tick 存活的独立旁证 ----------
    # 源码 auto_approver.py:631 `if not items: return` —— pending=0 时静默返回、不落日志，
    # 故「日志末行时刻」不能当作 tick 停摆证据；须用 backend 访问日志复算轮询次数。
    ("15_backend_approver_polls",
     "docker logs open-foundation-backend --since 40m --timestamps 2>&1 "
     "| grep 'approvals?status=pending' | awk '{print $1}' "
     "| awk 'BEGIN{n=0} {n++; last=$1} END{print \"polls=\" n \" last=\" last}'"),
    ("15_approver_silent_branch",
     "grep -n 'if not items:' -A1 " + PLAT + "/tools/agent/auto_approver.py"),

    # ---------- refill 预算增量（从 API 响应复算，勿手抄）----------
    ("15_refill_budget_delta",
     "python3 -c \"import json;"
     "r=json.load(open('" + TMP + "/refill_171.json'));b=r['budget'];"
     "print('status=%s tc=%d ws=%d' % (r['status'],b['max_tool_calls'],b['max_wall_seconds']));"
     "print('delta_tc=%d delta_ws=%d' % (b['max_tool_calls']-240,b['max_wall_seconds']-28800))\""),
]


def run_one(name, cmd):
    t0 = time.time()
    p = subprocess.run(cmd, shell=True, env=ENV, capture_output=True, text=True, timeout=120)
    elapsed = round(time.time() - t0, 2)
    return {
        "name": name,
        "cmd": cmd,
        "stdout": p.stdout.rstrip("\n"),
        "stderr": p.stderr.rstrip("\n"),
        "exit_code": p.returncode,
        "elapsed_s": elapsed,
    }


def main():
    items = []
    for name, cmd in COMMANDS:
        try:
            item = run_one(name, cmd)
        except subprocess.TimeoutExpired:
            item = {"name": name, "cmd": cmd, "stdout": "", "stderr": "TIMEOUT",
                    "exit_code": 124, "elapsed_s": 120}
            print("[%s] TIMEOUT" % name, file=sys.stderr)
        except Exception as exc:                                  # noqa: BLE001
            item = {"name": name, "cmd": cmd, "stdout": "", "stderr": str(exc),
                    "exit_code": 1, "elapsed_s": 0}
            print("[%s] ERR %s" % (name, exc), file=sys.stderr)
        items.append(item)
        print("[%s] rc=%d %.1fs" % (name, item["exit_code"], item["elapsed_s"]), file=sys.stderr)

    report = {
        "collector": "collect_2026-10-22.m173.py",
        "shift": "m173 (real shift #61; dispatcher log still labels shift#49)",
        "generated_at_utc": items[0]["stdout"],
        "worktree": SELF,
        "items": items,
    }
    out_path = os.path.join(HERE, "2026-10-22.m173.readings.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("Wrote %d readings to %s" % (len(items), out_path), file=sys.stderr)


if __name__ == "__main__":
    main()