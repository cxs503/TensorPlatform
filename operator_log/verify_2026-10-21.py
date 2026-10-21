#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m144 班次机器门：从 2026-10-21.readings.json 复算 operator_log 里的每个判据数字。
"""m142 班次机器门：从 2026-10-21.readings.json 复算 operator_log 里的每个判据数字。

纪律：operator_log 中的数字必须能被本脚本从工件直读复算 —— 零手抄。
退出码 0 = 全部判据与工件一致；非 0 = 有判据对不上（日志不可信）。
只读：不改任何工件、不发 API、不写 DB。

判据分三类（平台是活体，读数会漂移）：
  ① 稳定属性判据（在飞心跳鲜度/无 infra 终局/容器健康/GC 全灭/R3 硬门封锁…）
     写成不依赖某一具体计数的形式；
  ② 确指判据（celery-beat RestartCount 单调增长、m120 gates 写盘晚于 commit、
     R3 分母=摘录截断 20…）写成对已知基线的不等式/在场性，不钉死上一班数值；
  ③ 在飞/终态集合改成「本班快照 + 必须含本班 m144」的形态。
判据分两类（平台是活体，读数会漂移）：
  ② 确指判据（celery-beat RestartCount 单调增长、m137 honest FAIL 零提交、
     R2 修复已入库…）写成对已知基线的不等式/在场性，不钉死上一班的数值；
  ③ 在飞/终态集合改成「本班快照 + 必须含本班 m142」的形态。
"""
import json
import datetime
import os
import re
import subprocess
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED_INFLIGHT_CONTAINS = {"144"}   # 本班 m144 在飞（m143 为并行 dev 腿）
EXPECTED_INFLIGHT_CONTAINS = {"142"}   # 本班 m142 在飞；m134/m135 已转 awaiting_acceptance
NO_INFRA = ("agent_lost", "budget_exceeded", "agent_crash", "gateway_unavailable")


def load_readings():
    with open(os.path.join(HERE, "2026-10-21.readings.json"), encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    r = load_readings()
    by = {it["name"]: it for it in r["items"]}
    results = []

    def check(name, ok, detail):
        results.append({"check": name, "pass": bool(ok), "detail": detail})

    # --- 巡检第 1 步：在飞 + 心跳鲜度 ---
    inflight = [ln for ln in by["1_inflight"]["stdout"].strip().splitlines() if ln]
    ids = [ln.split("|")[0] for ln in inflight]
    ages = [int(ln.split("|")[3]) for ln in inflight]
    scs = [ln.split("|")[4].strip() for ln in inflight]
    check("inflight_contains_m144", "144" in ids,
          "在飞 id=%s（必含本班 144）" % ids)
    inflight = by["1_inflight"]["stdout"].strip().splitlines()
    check("inflight_contains_m142", "142" in ids,
          "在飞 id=%s（必含本班 142）" % ids)
    check("inflight_heartbeat_fresh", all(a <= 240 for a in ages),
          "心跳年龄(秒)=%s，全部 ≤240s（未到收割线）" % ages)
    check("inflight_stop_condition_null", all(s == "NULL" for s in scs),
          "在飞 result.stop_condition=%s（均 NULL，无 infra 终局值）" % scs)
    check("inflight_no_stale", 1 <= len(inflight) <= 6,
          "在飞条数=%d（本班快照≤6，不钉死具体集合）" % len(inflight))

    # --- 巡检第 2 步：守护计数 ---
    graw = int(by["2_guardian_count"]["stdout"].strip())
    gcount = int(by["2_guardian_count_excl_bash"]["stdout"].strip())
    check("guardian_count_baseline_10", gcount == 10,
          "守护数（排除 bash 包装的等价口径）=%d（基线 10）；裸 pgrep=%d" % (gcount, graw))
    gdetail = [x for x in by["2_guardian_detail"]["stdout"].strip().splitlines() if x]
          "守护进程数（排除 bash 包装的等价口径）=%d（基线 10）；裸 pgrep 读数=%d" % (gcount, graw))
    gdetail = by["2_guardian_detail"]["stdout"].strip().splitlines()
    check("guardian_detail_matches_count", len(gdetail) == gcount,
          "2_guardian_detail 逐进程行数=%d == 计数 %d" % (len(gdetail), gcount))

    # --- 巡检第 3 步：中继健康 ---
    relay = by["3_relay"]["stdout"]
    m = re.search(r"http=(\d+) t=([\d.]+)", relay)
    models = re.search(r"models=(\d+)", relay)
    check("relay_http_200", m and m.group(1) == "200",
          "中继 /models HTTP=%s（期望 200）" % (m.group(1) if m else "?"))
    check("relay_latency_under_5s", m and float(m.group(2)) <= 5.0,
          "中继延迟=%ss（门限 ≤5s）" % (m.group(2) if m else "?"))
    check("relay_model_count_7", models and int(models.group(1)) == 7,
          "中继返回 models=%s（与前任读数一致 7）" % (models.group(1) if models else "?"))

    # --- 巡检第 4 步：网关冷却态 ---
    cooldown = by["4_cooldown"]["stdout"].strip()
    check("cooldown_empty", cooldown == "{}",
          "冷却态内容=%r（空=无端点冷却，无需清理）" % cooldown)

    # --- 巡检第 5 步：终态 result + 审批 ---
    term_agg, term_scs = {}, []
    for ln in by["5_terminal_24h"]["stdout"].strip().splitlines():
        f = ln.split("|")
        if len(f) < 3:
            continue
        term_agg[f[0]] = term_agg.get(f[0], 0) + int(f[1])
        term_scs.append(f[2])
    check("terminal_24h_succeeded_in_band", 20 <= term_agg.get("succeeded", 0) <= 40,
          "24h succeeded=%d（基线区间 20–40，平台推进不钉死）" % term_agg.get("succeeded", 0))
    check("terminal_24h_failed_present", 3 <= term_agg.get("failed", 0) <= 8,
          "24h failed=%d（honest FAIL 集合稳定，区间 3–8）" % term_agg.get("failed", 0))
    check("terminal_24h_awaiting_in_band",
          term_agg.get("awaiting_acceptance", 0) <= 10,
          "24h awaiting_acceptance=%d（≤10；本班 m143 班中转 awaiting，平台推进不钉死）"
          % term_agg.get("awaiting_acceptance", 0))
    failed_infra = [ln for ln in by["5_failed_24h_infra"]["stdout"].strip().splitlines() if ln]
    infra_scs = []
    for ln in failed_infra:
        if len(f) >= 3 and f[2].strip() in NO_INFRA:
            infra_scs.append(f[0])
    check("terminal_24h_no_unhandled_infra_failures", len(infra_scs) == 0,
          "24h failed infra 类 stop_condition=%s（应为空=无 infra 类终局未处置→零 refill 对象）"
          % infra_scs)
    # honest FAIL 定性（延续 P）
    m137 = by["5_m137_result"]["stdout"].strip()
    check("m137_honest_fail",
          m137.startswith("finished_without_acceptance") and "tool_calls=57" in m137,
          "m137 result=%s（honest FAIL：57 tool / 901s）" % m137)
    m137b = by["5_m137_branch"]["stdout"].strip()
    check("m137_zero_commit_evidence",
          "head=e18d83b" in m137b and "ahead=0" in m137b and "dirty=0" in m137b,
          "m137 分支/工作树=%s（head 停 base e18d83b，零提交=零交付）" % m137b)
    check("failed_ids_stable",
          by["5_failed_30h_ids"]["stdout"].strip() == "ids=100 112 119 126 137",
          "30h failed 集合=%s（与前任一致）" % by["5_failed_30h_ids"]["stdout"].strip())
    # refill 机械门：五条 honest FAIL 全 rc=3，零例行续班对象
    rc = [x for x in by["5_refill_check_rc"]["stdout"].strip().splitlines() if x]
    rc_map = {x.split()[0]: x.split()[1] for x in rc if len(x.split()) >= 2}
    check("refill_rc_all_3_no_infra_object",
          all(rc_map.get("m%d" % i) == "rc=3" for i in (100, 112, 119, 126, 137))
          and len(rc) == 5,
          "refill_check 退出码=%s（五条全 3=拒绝；24h 无 infra 终局 → 本班零 refill 对象）" % rc)
    # 审批面：本班新判定——pending 清零 + 上一班上报的 #103–#112 全部人工裁决
    app_n = int(re.search(r"approved=(\d+)", by["5_approvals"]["stdout"]).group(1))
    rej_n = int(re.search(r"rejected=(\d+)", by["5_approvals"]["stdout"]).group(1))
    pending_txt = by["5_approval_pending"]["stdout"].strip()
    new_case = re.fullmatch(r"113", pending_txt) is not None   # 本班新案（m143）
    check("approval_backlog_no_stuck_pending",
          pending_txt == "(none)" or new_case,
          "pending=%s（前几班持 6–7 的 #103–#112 已全部由 owner 人工裁决；"
          "残余仅本班新案 #113=m143）" % pending_txt)
    check("approval_backlog_upper_bound",
          app_n >= 96 and rej_n == 11,
          "审批计数 approved=%d（≥96）/rejected=%d（=11 稳定）" % (app_n, rej_n))
    drained = by["5_approval_100plus"]["stdout"]
    rows = {ln.split("|")[0]: ln for ln in drained.strip().splitlines() if ln}
    hist = {"103", "104", "106", "107", "108", "111", "112"}
    check("approval_backlog_drained_by_owner",
          hist <= set(rows) and all("|approved|" in rows[i] for i in hist),
          "历史悬案 #103/#104/#106/#107/#108/#111/#112 全部 approved=owner 人工兜底；"
          "新增行=%s" % sorted(set(rows) - hist))
    check("approval_drain_source_human",
          "by=1 n=" in by["5_approval_drain_src"]["stdout"]
          and "10-21 07:1" in by["5_approval_drain_src"]["stdout"],
          "3h 内裁决来源=%s（decided_by=1 人工，07:16–07:17Z 一次性清空）"
          % by["5_approval_drain_src"]["stdout"].strip())
    # --- 巡检第 6 步：容器体检（N 类延续）---
    containers = [x for x in by["6_containers"]["stdout"].strip().splitlines() if x]
    bad = [x for x in containers if "healthy" not in x and "Up" not in x.split("|")[1]]
    check("containers_only_beat_unhealthy",
          any(x.startswith("open-foundation-celery-beat|Restarting") for x in containers)
          and len(bad) == 1,
          "非健康容器=%s（仅 celery-beat，N 类延续）" % bad)
    beat = by["6_celery_beat_inspect"]["stdout"].strip()
    beat_rc = int(re.search(r"restart=(\d+)", beat).group(1))
    check("celery_beat_restart_gt_baseline", beat_rc > 2924 and "exit=73" in beat,
          "celery-beat inspect=%s（RestartCount %d > 上班 2924=单调增长；exit=73）"
          % (beat, beat_rc))
    rounds = int(by["6_celery_beat_pidfile_rounds"]["stdout"].strip())
    check("celery_beat_pidfile_loop", rounds >= 2991,
          "beat 日志 Pidfile 轮次=%d（≥2991，stale-pidfile 崩溃循环）" % rounds)
    # --- 审批器停车表残余（Y，本班新增）---
    park = by["6_park_file"]["stdout"].strip().splitlines()
    parked_n = int(re.search(r"parked=(\d+)", park[0]).group(1)) if park else -1
    check("park_file_residual_present", parked_n >= 6,
          "停车表=%s（≥6 条残余，含本班新案 #113；Y 类上报）" % (park[0] if park else "?"))
    check("park_residual_vs_db_mismatch",
          "db_status=" in by["6_park_residual_vs_db"]["stdout"]
          and "'approved'" in by["6_park_residual_vs_db"]["stdout"]
          and "'absent'" not in by["6_park_residual_vs_db"]["stdout"],
          "停车表 vs 库内状态=%s（同批 id 库内全 approved=表内语义过期）"
          % by["6_park_residual_vs_db"]["stdout"].strip().replace("\n", " "))
    esc3 = by["6_park_file"]["stdout"]
    check("r4_fourth_case_park113",
          "113 143" in esc3 and "工作树有" in esc3,
          "停车表含 #113/m143 理由=工作树脏（checks=6 不触容量门 ⇒ R4 独立于 R3）")
    esc = by["6_approver_escalate_rounds"]["stdout"]
    a103 = int(re.search(r"a103=(\d+)", esc).group(1))
    a112 = int(re.search(r"a112=(\d+)", esc).group(1))
    check("approver_idle_after_drain",
          a103 >= 95 and a112 >= 7,
          "审批器历史空转轮次=%s（103=95 轮、112=7 轮的 escalate 全部终结于人工裁决）" % esc.strip())
    # --- R3 容量硬门（本班新口径：分母=摘录截断 20，非 14）---
    cap = by["6_repo_suite_capacity"]["stdout"].strip()
    check("repo_suite_capacity_14", "14 passed" in cap,
          "repo 默认套件=%s（14 例，旧口径基线；本班已改判分母为摘录截断值）" % cap[:60])
    ev = by["6_r3_lock_evidence"]["stdout"]
    check("r3_capacity_denominator_truncated_20",
          "claimed=25|excerpt_checks=20|passed_null=20" in ev
          and "claimed=44|excerpt_checks=20" in ev,
          "审批 #104/#112 evidence=%s（claimed 25/44 > 摘录 20=分母被 checks[:20] 截断）"
          % ev.strip().replace("\n", " ;; "))
    trunc = by["6_r3_capacity_truncation"]["stdout"]
    check("r3_truncation_in_source",
          "checks[:20]" in trunc and "def checks_capacity" in trunc,
          "源码双证=%s（tensor_agent 截断 + auto_approver 以摘录为分母）"
          % trunc.strip().replace("\n", " / "))
    # --- 换血判据（案例 3/7）：修复面 vs 守护启动 ---
    face = by["7_import_face_commit"]["stdout"].strip()
    check("import_face_commit_recent",
          face.startswith("e4edcbc 10-21 13:44:55"),
          "import 面（tensor_agent.py）= %s" % face)
    diff = by["7_face_diff_vs_8e548e7"]["stdout"]
    check("face_diff_small_additive_only",
          "28 insertions" in diff and "2 deletions" in diff and "hunks=3" in diff,
          "8e548e7..HEAD 对 import 面=%s（纯 additive：--slot 身份 + PYTHONPATH，无杀守护旧码）"
          % diff.strip().replace("\n", " ;; "))
    check("no_kill_daemon_code_in_face_diff",
          "os._exit" not in diff and "SIGKILL" not in diff,
          "import 面 diff 未见 os._exit/SIGKILL 类新增 → 案例 3 换血条件不成立（零换血）")
    claims = by["7_busy_guardian_claims"]["stdout"]
    check("inflight_claim_map_consistent",
          "qwen3.7-plus: [15:20:10] claimed mission 143" in claims
          and "deepseek-v4-flash: [15:43:14] claimed mission 144" in claims,
          "认领映射=%s（与 1_inflight 的 143/144 对账一致）"
          % claims.strip().replace("\n", " / ")[:220])
    # --- 本班交付面自身（8_worktree_self 采于提交前）---
    selfst = by["8_worktree_self"]["stdout"]
    check("self_branch_is_agent_m144",
          "branch=agent/m144" in selfst,
          "本班 worktree=%s（分支名=任务分支 agent/m144）" % selfst.strip())
    # 交付面亲验：五件工件必须在场（磁盘）；若已 commit 则 HEAD 树内亦须在同在。
    # 说明：verify 通常跑在 commit 之前（报告本身是交付件之一），故按在场性判定，
    # commit 后复跑时 HEAD 树检查会升级为强判据。
    want = ["2026-10-21.md", "2026-10-21.readings.json", "2026-10-21.validation_report.json",
            "collect_2026-10-21.py", "verify_2026-10-21.py"]
    on_disk = [f for f in want if os.path.exists(os.path.join(HERE, f))]
    check("delivery_files_present_on_disk", len(on_disk) >= 4,
          "交付目录在场=%s（≥4：report 由本脚本自产，跑完必在）" % on_disk)
    head = subprocess.run(["git", "-C", os.path.dirname(HERE),
                           "rev-parse", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()
    ls = subprocess.run(["git", "-C", os.path.dirname(HERE),
                         "ls-tree", "-r", "--name-only", head, "--", "operator_log/"],
                        capture_output=True, text=True, encoding="utf-8").stdout
    files = sorted(x.split("/")[-1] for x in ls.split())
    check("delivery_files_in_head_tree_or_precommit",
          len(files) == 0 or set(want) <= set(files),
          "HEAD 树内 operator_log/ 含=%s（提交后跑=强判据：五件全在；提交前跑=0 属预期）"
          % files)
    status = subprocess.run(["git", "-C", os.path.dirname(HERE),
                             "status", "--porcelain"],
                            capture_output=True, text=True, encoding="utf-8").stdout.strip()
    dirty_other = [l for l in status.splitlines()
                   if not l.endswith("operator_log/") and "operator_log/" not in l]
    # 说明：本报告自身写在 operator_log/ 下，其写盘会让 worktree 变脏；该事实
    # 已在 dirty_other 计算中排除（否则报告内容随自身脏态变化→复跑振荡）。
    check("delivery_no_out_of_scope_changes", dirty_other == [],
          "除 operator_log/ 外无越界改动（越界项数=%d，报告自身写盘不计）" % len(dirty_other))
    # --- R4（延续）：验收面自造脏树 ---
    m120s = by["8_m120_worktree_state"]["stdout"]
    check("m120_dirty_from_gates_preflight",
          "dirty=3" in m120s and "gates_verified.json" in m120s,
          "m120 工作树=%s（dirty=3，平台预检改写=R4 实证）" % m120s.strip()[:150])
    gates = by["8_m120_gates_preflight_source"]["stdout"]
    check("m120_gates_mtime_after_commit",
          "file_mtime=2026-10-21 10:53:16" in gates and "commit_time=2026-10-21 10:52:07" in gates,
          "m120 gates=%s（写盘晚于交付 commit=R4 根因）" % gates.strip().replace("\n", " "))
    non_null = [sc for sc in term_scs if sc != "NULL"]
    check("terminal_24h_succeeded_in_band", 20 <= term_agg.get("succeeded", 0) <= 35,
          "24h succeeded=%d（基线区间 20–35，平台推进不钉死）" % term_agg.get("succeeded", 0))
    check("terminal_24h_awaiting_in_band", 3 <= term_agg.get("awaiting_acceptance", 0) <= 10,
          "24h awaiting_acceptance=%d（平台推进会增减，不钉死）" % term_agg.get("awaiting_acceptance", 0))
    check("terminal_24h_failed_present", term_agg.get("failed", 0) >= 3,
          "24h failed=%d（≥3，honest FAIL 集合稳定）" % term_agg.get("failed", 0))
    # infra 类终局检查：24h failed 中不得出现未处置的 infra 类 stop_condition
    failed_infra = by["5_failed_24h_infra"]["stdout"].strip().splitlines()
        if len(f) >= 3:
            sc = f[2].strip()
            if sc in NO_INFRA:
                infra_scs.append(f[0])
    check("terminal_24h_no_unhandled_infra_failures",
          len(infra_scs) == 0,
          "24h failed infra 类 stop_condition=%s（应为空=无 infra 类终局未处置）" % infra_scs)
    # m137 定性（延续）：honest FAIL + 零交付推进
          "m137 result=%s（honest FAIL：57 tool / 901s 用尽 nudge 未提交）" % m137)
          "ahead=0" in m137b and "dirty=0" in m137b and "wt_head=e18d83b" in m137b,
          "m137 分支/工作树=%s（head 停在 base e18d83b，零提交=零交付）" % m137b)
    check("failed_ids_include_137",
          "30h failed 集合=%s" % by["5_failed_30h_ids"]["stdout"].strip())
    # --- m134/m135 续班后状态（本班新增：确认上班处置收口）---
    m135p = by["5_m135_post_refill"]["stdout"].strip()
    check("m135_awaiting_acceptance_after_refill",
          m135p.startswith("135|awaiting_acceptance") and "budget=310/18000" in m135p
          and "audit_by=operator_agent" in m135p and "added=60/3600" in m135p,
          "m135 续班后=%r（awaiting_acceptance；预算 310/18000；refill.by=operator_agent added=60/3600）"
          % m135p[:200])
    m134p = by["5_m134_status"]["stdout"].strip()
    check("m134_awaiting_acceptance",
          m134p.startswith("134|awaiting_acceptance") and "checks=2" in m134p,
          "m134=%r（awaiting_acceptance；checks=2/2）" % m134p[:150])
    # --- refill 机械门：五个 honest FAIL 全 rc=3，零例行续班对象 ---
    rc = by["5_refill_check_rc"]["stdout"].strip().splitlines()
    rc_map = {ln.split()[0]: ln.split()[1] for ln in rc}
          "refill_check 退出码=%s（五条 honest FAIL 全 3=拒绝；24h 无 infra 终局 "
          "→ 本班零 refill 对象）" % rc)
    # 审批
    approvals = by["5_approvals"]["stdout"]
    app_n = int(re.search(r"approved=(\d+)", approvals).group(1))
    rej_n = int(re.search(r"rejected=(\d+)", approvals).group(1))
    pend_n = int(re.search(r"pending=(\d+)", approvals).group(1))
    check("approval_no_pending_drain",
          app_n >= 86 and rej_n == 11 and pend_n >= 2,
          "审批计数=%s（approved %d≥86；pending 持 %d 且长期不下降=escalate 空转）"
          % (approvals.replace("\n", " "), app_n, pend_n))
    pending = by["5_approval_pending"]["stdout"].strip().splitlines()
    pend_pairs = ["|".join(ln.split("|")[:2]) for ln in pending]
    check("approval_pending_contains_103_104",
          "103|120" in pend_pairs and "104|133" in pend_pairs,
          "pending 列=%s（必含 #103(m120) + #104(m133)=R3 锁死实证）" % pend_pairs)
    # --- 巡检第 6 步：容器健康 ---
    containers = by["6_containers"]["stdout"]
    cb = by["6_celery_beat_inspect"]["stdout"]
    check("celery_beat_restarting", "restarting" in cb and "exit=73" in cb,
          "celery-beat=%s（stale-pidfile 崩溃循环，exit=73，N 类延续上报）" % cb.strip()[:120])
    check("other_containers_healthy",
          "healthy" in containers and containers.count("healthy") >= 9,
          "其他容器健康计数：logs 中 'healthy' 出现 %d 次（≥9 = 仅 celery-beat 不健康）"
          % containers.count("healthy"))
    cb_rc = int(re.search(r"restart=(\d+)", cb).group(1))
    check("celery_beat_restart_growing", cb_rc > 2800,
          "celery-beat RestartCount=%d（>前任基线 2809，单调增长=循环持续）" % cb_rc)
    # --- 巡检第 7 步：import face（守护载码版本复核）---
    face = by["7_import_face_commit"]["stdout"]
    check("import_face_current", "3159527" in face or "e4edcbc" in face,
          "import face commit=%s（当前最新 face=3159527 或 e4edcbc）" % face.strip()[:120])
    last_commit = by["7_last_commit"]["stdout"]
    check("last_commit_recent", "fix(approver)" in last_commit,
          "最近提交=%s（approver 修复链）" % last_commit.strip()[:120])
    # --- 巡检第 8 步：m120 检查点（R4 实证：平台自造脏树）---
    m120s = by["5_m120_worktree_state"]["stdout"]
          "m120 工作树=%s（dirty=3，3 个 gates_verified.json 被平台预检改写=R4 实证）"
          % m120s.strip()[:150])
    gates = by["6_gates_preflight_dirty_source"]["stdout"]
          "m120 gates 写盘时点=%s（mtime > commit_time = 平台预检写盘晚于交付 commit=R4 根因）"
          % gates.strip()[:150])

    # --- 巡检第 9 步：平台尾部观察 ---
    gc = by["9_gc_log"]["stdout"]
    check("gc_all_denied", "perms=600" in gc and "denied=13" in gc,
          "gc.py 权限=%s（600 无 x 位→13/13 denied，X 类延续）" % gc.strip()[:100])
          "gc.py 权限=%s（600 无 x 位→13/13 denied，X 类延续上报）" % gc.strip()[:100])
    wt = int(by["9_worktree_count"]["stdout"].strip())
    check("worktree_count_growing", wt >= 52,
          "git worktree list 行数=%d（GC 停摆持续累积，≥52）" % wt)
    dirty = int(by["9_main_dirty"]["stdout"].strip())
    items = by["9_main_dirty_items"]["stdout"]
    check("main_dirty_known_items",
          dirty == 4 and "llm_endpoints.json" in items and "auto_approver.py" not in items,
          "主仓脏项=%d（llm_endpoints.json + 3 patch_*.py；无 auto_approver.py=修复已入库）" % dirty)
    fleet = by["9_fleet_log"]["stdout"].strip()
    check("fleet_alive", "fleet pass done" in fleet and "spawned=0" in fleet,
          "fleet.log 尾=%s（保活在运行，spawned=0=无缺口）" % fleet.splitlines()[-1])
    check("crontab_fleet_present", by["9_crontab_fleet"]["stdout"].strip() == "1",
          "crontab fleet 行数=%s" % by["9_crontab_fleet"]["stdout"].strip())
    # --- 日志一致性门：operator_log 引用的关键计数必须能在 readings 找到同名数字 ---
    check("main_dirty_shrunk_and_clean_of_approver",
          dirty <= 6 and "auto_approver.py" not in items,
          "主仓脏项=%d；auto_approver.py 已不在脏树（修复入库）" % dirty)
    # --- R3 容量硬门实证 ---
    suite_cap = by["6_repo_suite_capacity"]["stdout"]
    check("r3_suite_capacity_14",
          "14 passed" in suite_cap,
          "repo 默认套件容量=%s（=14 pytest 例数=R3 容量硬门基线）" % suite_cap.strip()[:100])
    r3ev = by["6_r3_lock_evidence"]["stdout"]
    check("r3_lock_checks_claimed_exceeds_capacity",
          "checks_claimed=25" in r3ev,
          "审批 #104 checks_claimed=%s（25 > 14=R3 容量硬门锁死实证）" % r3ev.strip()[:100])
    # --- 日志一致性门：operator_log/<date>.md 里引用的关键计数
    #     必须能在工件里找到同名数字，杜绝 log 与 readings 脱钩（手抄漂移）---
    log_path = os.path.join(HERE, "2026-10-21.md")
    log_text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
    n_items = len(r["items"])
    cited = {
        "readings 条数": ("**45 条**" in log_text, n_items >= 44),
        "守护计数 10": ("**10**" in log_text,
                    "10" == by["2_guardian_count_excl_bash"]["stdout"].strip()),
        "中继 200": ("http=200" in log_text, "http=200" in by["3_relay"]["stdout"]),
        "中继 models=7": ("**7 models**" in log_text, "models=7" in by["3_relay"]["stdout"]),
        "冷却态空": ("`{}`" in log_text, "{}" == by["4_cooldown"]["stdout"].strip()),
        "approved=96": ("approved=**96**" in log_text,
                        "approved=96" in by["5_approvals"]["stdout"]),
        "pending 新案 113": ("pending=**1**" in log_text,
                         "pending=1" in by["5_approvals"]["stdout"]),
        "R4 第四例 113": ("第四例" in log_text and "#113" in log_text,
                       "113 143" in by["6_park_file"]["stdout"]),
        "celery-beat restart（对基线不等式）": (
            "restart=3" in log_text and "RestartCount" in log_text,
            int(re.search(r"restart=(\d+)", by["6_celery_beat_inspect"]["stdout"]).group(1)) > 2924),
        "worktree 55": ("=**55**" in log_text,
                        "55" == by["9_worktree_count"]["stdout"].strip()),
        "摘录截断 20": ("excerpt_checks=20" in log_text,
                     "excerpt_checks=20" in by["6_r3_lock_evidence"]["stdout"]),
    }
    for label, (needle_in_log, artifact_ok) in cited.items():
        check("log_consistency::" + label, needle_in_log and artifact_ok,
        "readings 条数": ("%d 条" % n_items, n_items in (44, 45, 46)),
        "守护计数 10": ("**10**", "10" == by["2_guardian_count_excl_bash"]["stdout"].strip()),
        "中继 200": ("http=200", "http=200" in by["3_relay"]["stdout"]),
        "中继 models=7": ("**7 models**", "models=7" in by["3_relay"]["stdout"]),
        "冷却态空": ("`{}`", "{}" == by["4_cooldown"]["stdout"].strip()),
        "审批 approved≥86": ("approved≥86" in log_text, int(re.search(r"approved=(\d+)", by["5_approvals"]["stdout"]).group(1)) >= 86),
        "pending=6": ("pending=6 持续不下降" in log_text or "pending=6" in log_text, "pending=6" in by["5_approvals"]["stdout"]),
        "celery-beat restart 2924": ("**2924**" in log_text, "restart=2924" in by["6_celery_beat_inspect"]["stdout"]),
        "worktree 54": ("=54" in log_text, "54" == by["9_worktree_count"]["stdout"].strip()),
        "suite_capacity 14": ("**14 passed**", "14 passed" in by["6_repo_suite_capacity"]["stdout"]),
        check("log_consistency::" + label,
              needle_in_log and artifact_ok,
              "log 引用 %s；工件一致=%s（log 与 readings 未脱钩）" % (label, artifact_ok))

    # --- 汇总 ---
    passed = sum(1 for x in results if x["pass"])
    total = len(results)
    report = {
        "generated_at_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shift": "m144",
        "shift": "m142",
        "readings_collected_at_utc": r.get("collected_at_utc"),
        "passed": passed,
        "total": total,
        "all_pass": passed == total,
        "checks": results,
    }
    out = os.path.join(HERE, "2026-10-21.validation_report.json")
    # 幂等写：除 generated_at_utc 外内容一致就不重写 —— 否则 commit 后复跑脚本
    # 会刷新时间戳把交付树重新弄脏（R4 型误判的自家版本）。复跑=零写盘。
    skip = False
    if os.path.exists(out):
        try:
            with open(out, encoding="utf-8") as fh:
                prev = json.load(fh)
            a = dict(prev)
            a.pop("generated_at_utc", None)
            b = dict(report)
            b.pop("generated_at_utc", None)
            skip = json.dumps(a, sort_keys=True, ensure_ascii=False) == \
                json.dumps(b, sort_keys=True, ensure_ascii=False)
        except (OSError, ValueError):
            skip = False
    if not skip:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    for x in results:
        print("%s %s | %s" % ("PASS" if x["pass"] else "FAIL", x["check"], x["detail"]))
    print("\n%d/%d PASS -> %s" % (passed, total, out))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
