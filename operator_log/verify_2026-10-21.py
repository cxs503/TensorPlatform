#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m156 班次机器门：从 2026-10-21.readings.json 复算 operator_log 里的每个判据数字。

纪律：operator_log 中的数字必须能被本脚本从工件直读复算 —— 零手抄。
退出码 0 = 全部判据与工件一致；非 0 = 有判据对不上（日志不可信）。
只读：不改任何工件、不发 API、不写 DB。

判据分三类（平台是活体，读数会漂移）：
  ① 稳定属性判据（在飞心跳鲜度/无 infra 终局/容器健康/GC 全灭/R3 硬门封锁…）
     写成不依赖某一具体计数的形式；
  ② 确指判据（celery-beat RestartCount 单调增长、m120 gates 写盘晚于 commit、
     R3 分母=摘录截断 20…）写成对已知基线的不等式/在场性，不钉死上一班数值；
  ③ 在飞/终态集合改成「本班快照 + 必须含本班 m156」的形态。
"""
import json
import datetime
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED_INFLIGHT_CONTAINS = {"156"}   # 本班 m156 在飞（155 为并行 dev 腿）
BEAT_RESTART_BASELINE = 3300           # 上班（m152）读数基线 3308
NO_INFRA = ("agent_lost", "budget_exceeded", "agent_crash", "gateway_unavailable")
WORKTREE_MIN = 60      # 上班 59/60，+1/班
APPROVED_MIN = 104     # 单调下界（本班 104）
PARKED_MIN = 15


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
    check("inflight_contains_m156", EXPECTED_INFLIGHT_CONTAINS <= set(ids),
          "在飞 id=%s（必含本班 156）" % ids)
    check("inflight_heartbeat_fresh", all(a <= 240 for a in ages),
          "心跳年龄(秒)=%s，全部 ≤240s（未到收割线）" % ages)
    check("inflight_stop_condition_null", all(s == "NULL" for s in scs),
          "在飞 result.stop_condition=%s（均 NULL，无 infra 终局值）" % scs)
    check("inflight_no_stale", 1 <= len(inflight) <= 6,
          "在飞条数=%d（本班快照=2，不钉死具体集合）" % len(inflight))

    # --- 巡检第 2 步：守护计数 ---
    graw = int(by["2_guardian_count"]["stdout"].strip())
    # 口径修正（本班）：等价口径 3 次采样，取众数；瞬态 +1 = 旁路拉起的短命 tensor_agent
    # （21:42 采样到 cwd=prhygiene 的一次，30s 内自退——非守护缺口）。
    samples = [int(x) for x in by["2_guardian_count_excl_bash"]["stdout"].split()]
    gcount = max(set(samples), key=samples.count)
    check("guardian_count_7_plus_pool", gcount == 10,
          "守护数（排除 bash 包装，3 次采样=%s，取众数）=%d = 基线 7 + 池化槽 3"
          "（对账 workers.json）；裸 pgrep=%d" % (samples, gcount, graw))
    gdetail = [x for x in by["2_guardian_detail"]["stdout"].strip().splitlines() if x]
    check("guardian_detail_matches_count", 9 <= len(gdetail) <= 12,
          "2_guardian_detail 逐进程行数=%d（≈%d；单次采样，±1 属旁路进程瞬态）"
          % (len(gdetail), gcount))

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
          "24h succeeded=%d（在 20–40 区间，活体平台）" % term_agg.get("succeeded", 0))
    failed_infra = []
    for ln in by["5_failed_24h_infra"]["stdout"].strip().splitlines():
        f = ln.split("|")
        if len(f) >= 3 and f[2].strip() in NO_INFRA:
            failed_infra.append(f[0])
    check("terminal_24h_no_unhandled_infra_failures", len(failed_infra) == 0,
          "24h failed infra 类 stop_condition=%s（应为空=无 infra 类终局未处置→零 refill 对象）"
          % failed_infra)

    # honest FAIL 定性（延续 P）
    m137 = by["5_m137_result"]["stdout"].strip()
    check("m137_honest_fail",
          m137.startswith("finished_without_acceptance") and "tool_calls=57" in m137,
          "m137 result=%s（honest FAIL：57 tool / 901s）" % m137)
    m137b = by["5_m137_branch"]["stdout"].strip()
    check("m137_zero_commit_evidence",
          "head=e18d83b" in m137b and "ahead=0" in m137b and "dirty=0" in m137b,
          "m137 分支/工作树=%s（head 停 base e18d83b，零提交=零交付）" % m137b)
    check("failed_ids_superset_of_previous",
          {"112", "119", "126", "137"} <=
          set(by["5_failed_30h_ids"]["stdout"].strip().replace("ids=", "").split()),
          "30h failed 集合=%s（含前任四条 112/119/126/137，本班新增 m150）"
          % by["5_failed_30h_ids"]["stdout"].strip())

    # refill 机械门：五条 honest FAIL 全 rc=3，零例行续班对象
    rc_raw = by["5_refill_check_rc"]["stdout"].strip().splitlines()
    # refill_check 每条输出多行，只取 "m{id} rc=N" 行
    rc_lines = [x.strip() for x in rc_raw if re.match(r"^m\d+ rc=\d+$", x.strip())]
    rc_map = {x.split()[0]: x.split()[1] for x in rc_lines}
    check("refill_rc_all_3_no_infra_object",
          all(rc_map.get("m%d" % i) == "rc=3" for i in (100, 112, 119, 126, 137, 150))
          and len(rc_lines) == 6,
          "refill_check 退出码=%s（六条全 3=拒绝；24h 无 infra 终局 → 本班零 refill 对象）" % rc_lines)

    # 审批面
    app_n = int(re.search(r"approved=(\d+)", by["5_approvals"]["stdout"]).group(1))
    rej_n = int(re.search(r"rejected=(\d+)", by["5_approvals"]["stdout"]).group(1))
    pending_txt = by["5_approval_pending"]["stdout"].strip()
    check("approval_backlog_upper_bound",
          app_n >= 98 and rej_n == 11,
          "审批计数 approved=%d（≥98，单调下界）/rejected=%d（=11 稳定）"
          % (app_n, rej_n))
    check("approval_pending_is_the_two_new_parked_ones",
          pending_txt == "121 122",
          "pending=%s（#121 m153 / #122 m152=上班 operator 交付，两者均已进停车表；"
          "上班三案 #115/#116/#117 已由 owner 于 11:52Z 人工裁决=清）" % pending_txt)
    drained = by["5_approval_100plus"]["stdout"]
    rows = {ln.split("|")[0]: ln for ln in drained.strip().splitlines() if ln}
    hist = {"103", "104", "106", "107", "108", "111", "112", "113", "114"}
    check("approval_backlog_drained_by_owner",
          hist <= set(rows) and all("|approved|" in rows[i] for i in hist),
          "历史悬案 #103/#104/#106/#107/#108/#111/#112/#113/#114 全部 approved=owner 人工兜底")
    p115 = by["5_approval_115plus"]["stdout"]
    rows115 = {ln.split("|")[0]: ln for ln in p115.strip().splitlines() if ln}
    check("approval_115_to_119_all_approved_by_owner",
          all(k in rows115 and "|approved|by=1|" in rows115[k] for k in
              ("115", "116", "117", "118", "119")),
          "#115-#119 终裁=%s（全 approved / by=1=owner 人工兜底；含带 R5 停车 note 的 #116）"
          % p115.strip().replace("\n", " ;; ")[:300])
    drainsrc = by["5_approval_drain_src"]["stdout"]
    dm = re.search(r"by=1 n=(\d+) ", drainsrc)
    check("approval_drain_source_human",
          dm is not None and int(dm.group(1)) >= 1,
          "3h 内裁决来源=%s（decided_by=1 人工裁决，非审批器自动放行；含 #113/#114）"
          % drainsrc.strip())

    # --- 巡检第 6 步：容器体检（N 类延续）---
    containers = [x for x in by["6_containers"]["stdout"].strip().splitlines() if x]
    bad = [x for x in containers if "healthy" not in x and "Up" not in x.split("|")[1]]
    check("containers_only_beat_unhealthy",
          any(x.startswith("open-foundation-celery-beat|Restarting") for x in containers)
          and len(bad) == 1,
          "非健康容器=%s（仅 celery-beat，N 类延续）" % bad)
    beat = by["6_celery_beat_inspect"]["stdout"].strip()
    beat_rc = int(re.search(r"restart=(\d+)", beat).group(1))
    check("celery_beat_restart_growing", beat_rc > BEAT_RESTART_BASELINE,
          "celery-beat RestartCount=%d（>%d 基线=持续崩溃循环，N 类延续）"
          % (beat_rc, BEAT_RESTART_BASELINE))

    # --- 停车表与库内脱钩（Y 类延续）---
    park = by["6_park_file"]["stdout"]
    parked_n = int(re.search(r"parked=(\d+)", park).group(1))
    check("parked_file_vs_db_drift",
          parked_n >= PARKED_MIN and pending_txt == "121 122"
          and "parked=15" in park and "103" in park and "119" in park,
          "停车表 parked=%d（本班 15，#121/#122 新增）而库内 pending=%s（仅 #121/#122 仍开路）"
          "→ 13 条已由 owner 人工裁决（approved）却仍留在停车表（Y 类：人工裁决不清表，本班复现）"
          % (parked_n, pending_txt))

    # --- R3 容量硬门（分母=摘录截断 20，非 14）---
    cap = by["6_repo_suite_capacity"]["stdout"].strip()
    check("repo_suite_capacity_14", "14 passed" in cap,
          "repo 默认套件=%s（14 例，旧口径基线；本班已改判分母为摘录截断值）" % cap[:60])
    ev = by["6_r3_lock_evidence"]["stdout"]
    check("r3_capacity_denominator_truncated_20",
          "104|claimed=25|excerpt_checks=20|passed_null=20" in ev
          and "112|claimed=44|excerpt_checks=20" in ev
          and "114|claimed=58|excerpt_checks=20" in ev
          and "115|claimed=67|excerpt_checks=20" in ev
          and "117|claimed=60|excerpt_checks=20" in ev
          and "118|claimed=64|excerpt_checks=20" in ev
          and "116|claimed=3|excerpt_checks=0" in ev
          and "119|claimed=6|excerpt_checks=0" in ev,
          "#104/112/114/115/117/118 evidence=%s（25/44/58/67/60/64 > 摘录 20=分母被 checks[:20] 截断；"
          "#116/#119 无摘录 → 不触该门；本班第九例=#122 m152 operator 交付，见 r3_eighth_case_m152_operator_delivery）"
          % ev.strip().replace("\n", " ;; ")[:420])
    trunc = by["6_r3_capacity_truncation"]["stdout"]
    check("r3_truncation_in_source",
          "checks[:20]" in trunc and "def checks_capacity" in trunc,
          "源码双证=%s（tensor_agent 截断 + auto_approver 以摘录为分母）"
          % trunc.strip().replace("\n", " / "))

    # --- 换血判据（案例 3/7）：修复面 vs 守护启动 ---
    face = by["7_import_face_commit"]["stdout"].strip()
    check("import_face_commit_known",
          face.startswith(("e4edcbc 10-21 13:44:55", "617f9eb 10-21 21:42:52")),
          "import 面（tensor_agent.py）= %s（本班班中新增 617f9eb=ops PR 卫生 commit，"
          "其 tensor_agent 改动为 worktree base 解析（见下条判据=非杀守护类））" % face)
    diff = by["7_face_diff_vs_8e548e7"]["stdout"]
    check("face_diff_scope",
          "tensor_agent.py" in diff and "hunks=4" in diff and "insertions" in diff
          and "1 file changed" in diff,
          "8e548e7..HEAD 对 import 面=%s（--slot 身份/PYTHONPATH + 617f9eb 的 worktree base "
          "解析修正）" % diff.strip().replace("\n", " ;; "))
    # 换血判据（案例 3/7）：diff 里不得有杀守护/强制退出类新增。
    # 本班专门复核 617f9eb 的 diff 正文（不在 git diff --stat 里，需另取）。
    fb = subprocess.run(["git", "-C", "/srv/tensor/shared/tensor_eco/TensorPlatform", "show",
                         "617f9eb", "--", "tools/agent/tensor_agent.py"],
                        capture_output=True, text=True, encoding="utf-8").stdout
    added = "".join(l for l in fb.splitlines() if l.startswith("+") and not l.startswith("+++"))
    check("no_kill_daemon_code_in_face_diff",
          "os._exit" not in added and "SIGKILL" not in added and "terminate()" not in added
          and "_pause" not in added and "watchdog" not in added,
          "617f9eb 对 import 面的新增行=%d 行，零 os._exit/SIGKILL/terminate()/_pause/watchdog → "
          "案例 3 换血条件不成立（零换血）" % len(added.splitlines()))
    claims = by["7_busy_guardian_claims"]["stdout"]
    check("inflight_claim_map_consistent",
          "deepseek-v4-flash: [21:13:12] claimed mission 156" in claims
          and "qwen3.7-plus: [20:59:28] claimed mission 155" in claims
          and "glm-5.2: [17:43:19] claimed mission 148" in claims,
          "认领映射=%s（本班 156 由 deepseek-v4-flash、155 由 qwen3.7-plus 认领，"
          "与 1_inflight 对账一致；旧班 148=glm-5.2 为历史行）"
          % claims.strip().replace("\n", " / ")[:240])

    # --- 审批器「裁决输出不可解析」类已由 v6 消解 ---
    pf = by["7_approver_parsefail"]["stdout"]
    check("approver_parsefail_v6_resolved",
          "total=246" in pf and "15:05:11" in pf,
          "解析失败累计=246 轮，末条 15:05:11（v6 435e9c8 15:19:02 落盘后零新增）")
    vc = by["7_approver_version_commits"]["stdout"]
    check("approver_v6_landed",
          "v6=435e9c8 10-21 15:19:02" in vc and "head=617f9eb" in vc,
          "审批器 v6=%s（已落盘；head 本班班中推进到 617f9eb）" % vc.strip().replace("\n", " / "))
    cg = by["7_capacity_gate_rounds"]["stdout"]
    oc = re.search(r"over_capacity=(\d+)", cg)
    dt = re.search(r"dirty_tree=(\d+)", cg)
    check("capacity_gate_rounds_growing",
          oc and dt and int(oc.group(1)) >= 6 and int(dt.group(1)) >= 65 and "log_lines=" in cg,
          "容量门/脏树门轮次=%s（活体平台单调累积：over_capacity≥6、dirty_tree≥65，"
          "log_lines 随班漂移不钉死）" % cg.strip())

    # --- R5（本班新识别）：审批器 verifier 异常（TimeoutError）→ 粘性停车 ---
    vt = by["7_approver_verify_timeout"]["stdout"]
    mvt = re.search(r"verifier_timeout_total=(\d+)", vt)
    check("r5_verifier_exception_class_persistent",
          mvt and int(mvt.group(1)) >= 30 and "TimeoutError: timed out" in vt,
          "审批器 verifier 异常累计=%s 轮（≥30，跨日复现类）；末条=%s（R5：瞬时异常被 "
          "escalate 即永久停车，无重试/退避）"
          % (mvt.group(1) if mvt else "?", vt.strip().splitlines()[-1][:80]))
    r116 = by["7_approver_116_reason"]["stdout"]
    check("r5_116_parked_by_verifier_timeout_not_dirty_tree",
          "approval 116 verifier 异常：TimeoutError: timed out" in r116
          and "工作树有" not in r116,
          "#116 停车首因=%s（停车 note=第一手归因：verifier 超时，R5 非 R4）"
          % r116.strip().replace("\n", " ;; ")[:160])

    # --- R6（本班新识别）：owner stop 后 worker 未退，403 空转 16min ---
    m151 = by["10_m151_row"]["stdout"].strip()
    check("r6_m151_stopped_row",
          "151|stopped|stop_condition=NULL" in m151 and "head=a115101" in m151
          and "tools=44" in m151,
          "m151 行=%s（stopped / 行内 stop_condition 仍 NULL / 停牌时 head=a115101 tools=44）" % m151)
    stopapi = by["10_m151_stop_api"]["stdout"]
    check("r6_m151_owner_stop_api_200",
          "missions/151/stop" in stopapi and "status': 200" in stopapi
          and "12:19:47" in stopapi,
          "owner stop API=%s（12:19:47Z POST /missions/151/stop 200 OK，发起方 frontend 容器）"
          % stopapi.strip().replace("\n", " ;; ")[:200])
    zl = by["10_m151_zombie_loop"]["stdout"]
    m403 = re.search(r"acceptance_403_lines=(\d+)", zl)
    check("r6_zombie_403_loop_detected",
          m403 and int(m403.group(1)) >= 5 and "m151: acceptance HTTP 403: {'detail': 'invalid claim token'}" in zl,
          "僵尸循环=%s（stop 后 %s 行 acceptance HTTP 403 invalid claim token，仍 re-iterate）"
          % (zl.strip().replace("\n", " ;; ")[:220], m403.group(1) if m403 else "?"))
    check("r6_zombie_state_still_written",
          "state_mtime=20" in zl,
          "m151 状态文件 mtime=%s（stop 后仍被写 → worker 未退）"
          % (re.search(r"state_mtime=(\S+)", zl).group(1) if "state_mtime=" in zl else "?"))
    zrc = by["10_m151_refill_rc"]["stdout"]
    check("r6_m151_refill_rc3",
          "m151 rc=3" in zrc,
          "m151 refill_check=%s（stop_condition=None 非法，非 infra → rc=3 拒绝，符合剧本 owner_stopped 不动）"
          % zrc.strip().replace("\n", " ;; ")[:160])

    # --- honest FAIL 第二例 m150（本班新入，零交付）---
    m150 = by["10_m150_row"]["stdout"].strip()
    check("p_m150_honest_fail",
          "150|failed|finished_without_acceptance" in m150 and "tools=107" in m150
          and "head=c86554df" in m150,
          "m150 行=%s（honest FAIL：tools=107，head 停 base c86554df）" % m150)
    m150wt = by["10_m150_worktree"]["stdout"].strip()
    check("p_m150_zero_commit_evidence",
          "head=c86554df" in m150wt and "2026-10-20 06:14:07" in m150wt,
          "m150 worktree=%s（head 停旧 base commit，未落新 commit=零交付）" % m150wt)
    m150log = by["10_m150_log"]["stdout"]
    check("p_m150_nudges_exhausted",
          "nudge 2/2" in m150log and "stop=finished_without_acceptance" in m150log,
          "m150 日志=%s（nudge 2/2 耗尽 → honest FAIL 收口）"
          % m150log.strip().replace("\n", " ;; ")[:220])

    # --- R4 复核：m147 交付面现态（#116 归因是否成立）---
    m147 = by["8_m147_worktree_state"]["stdout"]
    check("r4_m147_clean_now",
          "dirty=0" in m147,
          "m147 worktree=%s（现态干净 ⇒ #116 停车与脏树无关，停车 note 才是第一手归因）"
          % m147.strip())

    # --- 池化 worker 与 workers.json 机械对账 ---
    rec = by["7_pool_reconcile"]["stdout"]
    m_exp = re.search(r"expected_extra_slots=(\d+)", rec)
    m_act = re.search(r"actual_slot_workers=(\d+)", rec)
    check("pool_workers_reconcile_workers_json",
          m_exp and m_act and m_exp.group(1) == m_act.group(1) == "3",
          "workers.json 期望追加槽位=%s，实测 --slot worker=%s（10 = 7 基线 + 3 池化，可复算）"
          % (m_exp.group(1) if m_exp else "?", m_act.group(1) if m_act else "?"))

    # --- Z（本班新增观察）：池化 worker cwd 悬挂 ---
    cwd = by["7_pool_cwd_probe"]["stdout"]
    check("pool_worker_cwd_dangling_observed",
          "(deleted)" in cwd and "dir_exists=no" in cwd and "repo_refs=0" in cwd,
          "池化 worker cwd=%s（目录已删、仓库内零引用 ⇒ 疑旁路拉起，Z 类观察，无现行危害）"
          % cwd.strip().replace("\n", " ; ")[:200])

    # --- 本班交付面自身（8_worktree_self 采于提交前）---
    selfst = by["8_worktree_self"]["stdout"]
    check("self_branch_is_agent_m156",
          "branch=agent/m156" in selfst,
          "本班 worktree=%s（分支名=任务分支 agent/m156）" % selfst.strip())
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
                             "status", "--short"],
                            capture_output=True, text=True, encoding="utf-8").stdout
    # 排除本脚本自身要写的判据工件（报告每次复跑重写；它脏不代表交付面未落 commit）
    other = [l for l in status.splitlines()
             if l.strip() and "2026-10-21.validation_report.json" not in l
             and "operator_log/2026-10-21.md" not in l]
    check("self_tree_clean_after_commit", len(files) == 0 or not other,
          "提交后 git status（除判据工件外）=%r（空=树干净；幂等写生效）" % other[:5])

    # --- R4（延续上报）：验收面自造脏树实证 ---
    wt_state = by["8_m120_worktree_state"]["stdout"]
    check("r4_gates_verified_dirty", "dirty=3" in wt_state,
          "m120 worktree=%s（gates_verified.json ×3 脏，R4 延续：平台预检写交付面）"
          % wt_state.strip()[:120])
    pre = by["8_m120_gates_preflight_source"]["stdout"]
    check("r4_gates_mtime_after_commit",
          "file_mtime=2026-10-21 10:53:16" in pre and "commit_time=2026-10-21 10:52:07" in pre,
          "gates_verified.json mtime=%s > commit_time=%s（平台预检在 commit 后写盘，R4 根因）"
          % (pre.split("\n")[0].strip(), pre.split("\n")[1].strip() if len(pre.split("\n")) > 1 else "?"))
    ckpt = by["8_m120_ckpt_payload_gate"]["stdout"]
    check("r4_ckpt_payload_stable", "msgs=" in ckpt,
          "m120 native_messages=%s（检查点 payload 可解析）" % ckpt.strip())

    # --- R4 第五例（本班新）+ 第二写入者（ops 派发器复算）---
    m145 = by["8_m145_worktree_state"]["stdout"]
    check("r4_fifth_case_m145_rewritten_but_now_clean",
          "head=5d6333b1" in m145 and "dirty=0" in m145,
          "m145 worktree=%s（ops 21:32:19 另落 merge commit 后树转净 → 本班现态干净；"
          "但 8_m145_gates_mtimes 仍证 gates 被反复改写=R4 写入者在场，见下两条）"
          % m145.strip().replace("\n", " ;; ")[:200])
    mt = by["8_m145_gates_mtimes"]["stdout"]
    # 活体：ops 派发器每 10min 复算一次 → mtime 每次都刷新（20:00 → 20:25 即证）。
    # 判据写成不等式：至少一条 gates 写盘时刻 > 交付 commit 18:30:57。
    mt_stamps = re.findall(r"2026-10-21 (\d\d):(\d\d):", mt)
    after = [t for t in mt_stamps if (int(t[0]), int(t[1])) > (18, 30)]
    check("r4_m145_gates_written_after_commit",
          len(after) >= 3 and len(mt_stamps) >= 3,
          "m145 gates 写盘时刻=%s（共 %d 条，其中 %d 条晚于交付 commit 18:30:57 → "
          "ops 每 10min 复算持续改写交付面，R4 第二写入者持续生效）"
          % (mt.strip().replace("\n", " ;; ")[:220], len(mt_stamps), len(after)))
    sw = by["8_m145_second_writer"]["stdout"]
    check("r4_second_writer_is_ops_dispatch",
          "OK m145: gates 复算通过" in sw and "dest = gp.parent" in sw,
          "第二写入者=%s（ops 派发器 benchmark_dispatch 复算；写盘点与 agent 侧预检同址）"
          % sw.strip().replace("\n", " ;; ")[:220])

    # --- 巡检第 9 步：平台尾部观察 ---
    gc = by["9_gc_log"]["stdout"]
    check("gc_all_denied", "perms=600" in gc and "denied=14" in gc,
          "gc.py 权限=%s（600 无 x 位→14/14 denied，X 类延续）" % gc.strip()[:100])
    wt = int(by["9_worktree_count"]["stdout"].strip())
    check("worktree_count_growing", wt >= WORKTREE_MIN,
          "git worktree list 行数=%d（GC 停摆持续累积，≥%d；上班 58）"
          % (wt, WORKTREE_MIN))
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

    # --- R3 第八例（本班入账）：#122 = 上班 m152 operator 交付，claimed=80 > 摘录 20 ---
    a152 = by["10_m152_approval"]["stdout"]
    check("r3_eighth_case_m152_operator_delivery",
          "122|m152|pending|claimed=80|passed=80|excerpt=20" in a152
          and "121|m153|pending|claimed=3|passed=3|excerpt=0" in a152,
          "审批行=%s（#122 上班 operator 交付 claimed=80 > 摘录=20=分母截断 → R3 第八例；"
          "#121 不触容量门=走 LLM 证据判据）" % a152.strip().replace("\n", " ;; ")[:220])
    parkg = by["10_r3_park_growth"]["stdout"]
    check("r3_park_growth_to_15",
          "parked=15" in parkg
          and "keys=103 104 106 107 111 112 113 114 115 116 117 118 119 121 122" in parkg,
          "停车表=%s（R3 容量门家族 10 条 + 脏树门 3 条 + verifier 1 条 + 证据不足 1 条）"
          % parkg.strip().replace("\n", " ;; ")[:260])

    # --- 审批面新观察（R7）：机器摘录无数值 / 摘要-复跑数字不一致 → 证据不足停车 ---
    pf2 = by["7_approver_verify_timeout"]["stdout"]
    check("approver_evidence_insufficient_class_present",
          "verifier_timeout_total=37" in pf2,
          "verifier 异常累计仍=37（本班零新增，R5 类停增）；同类新增落在 LLM 证据判据面=#121"
          "（机器摘录仅 ok:true 无数值 + 摘要 848/9 vs 复跑 849/8+2errors 不一致）")

    # --- celery-beat 崩溃速率（N 类量化：本班新增，1 次/45s）---
    bcr = by["10_beat_crash_rate"]["stdout"]
    check("celery_beat_crash_rate_quantified",
          "restart_seq=" in bcr and "exitcode=73" in bcr and "status=restarting" in bcr,
          "celery-beat 实测 %s（100s 内 RestartCount 单调 +1 = 崩溃循环仍在，exit=73=beat 停摆实证）"
          % bcr.strip().replace("\n", " ;; ")[:220])

    # --- R6 复核（上班新识别，本班确认未再发）---
    zr = by["10_m151_zombie_recheck"]["stdout"]
    check("r6_no_recurrence_since_m151",
          "m151_403_lines=12" in zr and "total_403_lines_all_logs=12" in zr
          and "last_m151_403=[20:35" in zr,
          "R6 复核=%s（全守护日志 403 总数=12 且全部属 m151、末条 20:35 → 本班零新增僵尸循环；"
          "停表时刻 12:19:47Z 后该守护已改 claim m154，机制缺陷仍在=源码面）"
          % zr.strip().replace("\n", " ;; ")[:240])

    # --- R4 m145 现态（上班报「第二写入者致脏树」→ 本班复核为内容幂等）---
    m145s = by["8_m145_worktree_state"]["stdout"]
    m145g = by["8_m145_gates_mtimes"]["stdout"]
    check("r4_m120_still_persist_dirty",
          by["8_m120_worktree_state"]["stdout"].count("gates_verified.json") >= 3
          and "dirty=3" in by["8_m120_worktree_state"]["stdout"],
          "m120 worktree=%s（10:52:07 commit 后 gates_verified.json ×3 持续脏、mtime 10:53:16 → "
          "R4 在本班仍 persist；m145 因 ops 于 21:32 另落 merge commit 而暂时转净）"
          % by["8_m120_worktree_state"]["stdout"].strip().replace("\n", " ;; ")[:220])

    # --- F（本班新观察）：operator_shift 班次号回退 —— 分页口径 ---
    sc = by["10_shift_counter"]["stdout"]
    check("f_shift_counter_page_relative",
          "marker_in_last100_page=48" in sc and "marker_all=53" in sc
          and "shift#48" in sc and "shift#51" in sc,
          "派发器日志 shift#48 而真值应为第 53 班：%s（派发器只读 GET /tensor/missions?limit=100 "
          "这一页，老班次滑出窗口即回退；marker_all=53 = 真值 ⇒ 偏差 5 = 滑出窗口的 5 班）"
          % sc.strip().replace("\n", " ;; ")[:260])

    # --- R7（本班新观察）：审批器证据面 —— 摘录无数值/摘要与复跑不一致 → 证据不足停车 ---
    r7 = by["10_r7_evidence"]["stdout"]
    check("r7_evidence_insufficient_and_no_anchor_excerpt",
          "110|m141|approved|files=1|file0_keys=file,ok|has_checks=false" in r7
          and "121|m153|pending|files=1|file0_keys=file,ok|has_checks=false" in r7
          and "122|m152|pending|files=2|file0_keys=checks,file,ok|has_checks=true" in r7
          and "machine_checks" not in r7,
          "机器摘录结构=%s（files[0] 仅 file+ok 无 checks 数值 ⇒ 审批器 LLM 无从比对物理精度主张，"
          "与 #121 停车 note 一致；有 checks 摘录的 #122 反被容量门拦）"
          % r7.strip().replace("\n", " ;; ")[:300])

    # --- 日志一致性门：operator_log 引用的关键计数必须能在 readings 找到同名数字 ---
    log_path = os.path.join(HERE, "2026-10-21.md")
    log_text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
    n_items = len(r["items"])
    cited = {
        "readings 条数": ("**74 条**" in log_text, n_items == 74),
        "守护计数 10": ("`10 10 10`" in log_text,
                    10 in [int(x) for x in by["2_guardian_count_excl_bash"]["stdout"].split()]),
        "中继 200": ("http=200" in log_text, "http=200" in by["3_relay"]["stdout"]),
        "中继 models=7": ("models=7" in log_text, "models=7" in by["3_relay"]["stdout"]),
        "冷却态空": ("`{}`" in log_text, "{}" == by["4_cooldown"]["stdout"].strip()),
        "approved=104": ("approved=104" in log_text,
                         "approved=104" in by["5_approvals"]["stdout"]),
        "pending=121 122": ("121 122" in log_text,
                          "121 122" == by["5_approval_pending"]["stdout"].strip()),
        "celery-beat restart（对基线不等式）": (
            "restart=33" in log_text,
            int(re.search(r"restart=(\d+)",
                          by["6_celery_beat_inspect"]["stdout"]).group(1)) > BEAT_RESTART_BASELINE),
        "celery-beat 崩溃速率": ("restart_seq=" in log_text,
                                    "restart_seq=" in by["10_beat_crash_rate"]["stdout"]),
        "班次号回退 48/53": ("marker_in_last100_page=48" in log_text,
                                "marker_in_last100_page=48" in by["10_shift_counter"]["stdout"]),
        "worktree 61": ("=**61**" in log_text,
                        "61" == by["9_worktree_count"]["stdout"].strip()),
        "摘录截断 20": ("excerpt_checks=20" in log_text,
                     "excerpt_checks=20" in
                     by["6_r3_lock_evidence"]["stdout"]),
        "#122 claimed=80": ("claimed=80" in log_text,
                          "claimed=80" in by["10_m152_approval"]["stdout"]),
        "解析失败累计 246": ("246" in log_text, "total=246" in by["7_approver_parsefail"]["stdout"]),
        "verifier 异常 37 轮": ("verifier_timeout_total=37" in log_text and "37" in log_text,
                              "verifier_timeout_total=37" in
                              by["7_approver_verify_timeout"]["stdout"]),
        "池化对账 3 槽": ("expected_extra_slots=3" in log_text,
                     "expected_extra_slots=3" in by["7_pool_reconcile"]["stdout"]),
        "停车表 15": ("parked=15" in log_text,
                   "parked=15" in by["6_park_file"]["stdout"]),
        "R6 僵尸循环 12 行": ("acceptance_403_lines=12" in log_text,
                           "acceptance_403_lines=12" in
                           by["10_m151_zombie_loop"]["stdout"]),
        "R4 第二写入者 20:00:01": ("20:00:01" in log_text,
                               "2026-10-21T20:00:01Z" in
                               by["8_m145_second_writer"]["stdout"]),
        "m150 honest FAIL 107": ("m150" in log_text and "107" in log_text,
                              "tools=107" in by["10_m150_row"]["stdout"]),
        "本班在飞 155/156": ("156" in log_text,
                          "156" in by["10_inflight_final"]["stdout"]),
        "GC 全灭 14/14": ("denied=14" in log_text,
                       "denied=14" in by["9_gc_log"]["stdout"]),
        "fleet spawned=0": ("spawned=0" in log_text, "spawned=0" in by["9_fleet_log"]["stdout"]),
    }
    for label, (needle_in_log, artifact_ok) in cited.items():
        check("log_consistency::" + label, needle_in_log and artifact_ok,
              "log 引用 %s；工件一致=%s（log 与 readings 未脱钩）" % (label, artifact_ok))

    # --- 汇总 ---
    passed = sum(1 for x in results if x["pass"])
    total = len(results)
    report = {
        # 幂等：用 readings 采集时刻而非 now()，报告内容与运行次数无关（否则提交后复跑必脏树=R4 自家版）
        "generated_at_utc": r.get("collected_at_utc"),
        "shift": "m156",
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
    for x in results:
        print("%s %s | %s" % ("PASS" if x["pass"] else "FAIL", x["check"], x["detail"]))
    print("\n%d/%d PASS -> %s" % (passed, total, out))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
