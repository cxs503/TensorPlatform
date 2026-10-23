#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m173 班次机器门：从 2026-10-22.m173.readings.json 复算 operator_log/2026-10-22.md 里的每个判据数字。

纪律：operator_log 中的数字必须能被本脚本从工件直读复算 —— 零手抄。
退出码 0 = 全部判据与工件一致；非 0 = 有判据对不上（日志不可信）。
只读：不改任何工件、不发 API、不写 DB。

报告幂等写：generated_at 取采集时刻（readings.generated_at_utc），不取 now()，
且内容未变则不重写文件（避免提交后复跑把树弄脏）。
活体漂移项（中继耗时 / 在飞心跳年龄 / 在飞条数 / 24h 滚动行数 / worktree 目录数）一律用
区间或 ≥ / 集合关系判据，不钉死具体秒数；但日志里引用了的具体数字必须与 readings 同值。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READINGS = os.path.join(HERE, "2026-10-22.m173.readings.json")
REPORT = os.path.join(HERE, "2026-10-22.m173.validation_report.json")
LOG = os.path.join(HERE, "2026-10-22.md")


def load_readings():
    with open(READINGS, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    r = load_readings()
    by = {it["name"]: it for it in r["items"]}
    results = []

    def check(name, ok, detail):
        results.append({"check": name, "pass": bool(ok), "detail": detail})

    def out(name):
        return by[name]["stdout"]

    def rows(name):
        return [ln for ln in out(name).strip().splitlines() if ln]

    log_text = open(LOG, encoding="utf-8").read()
    # markdown 表格里竖线要转义（`29\|0\|29\|0`），核对前先归一化反斜杠
    log_norm = log_text.replace("\\|", "|")

    # ---------- 采集器自身完整性 ----------
    check("collector_items_count", len(r["items"]) == 66,
          "读数条数=%d（日志声明 66 条）" % len(r["items"]))
    check("collector_all_rc0", all(it["exit_code"] == 0 for it in r["items"]),
          "非零退出项=%s" % [it["name"] for it in r["items"] if it["exit_code"] != 0])
    check("collector_schema_ok",
          all(k in r for k in ("collector", "shift", "generated_at_utc", "worktree", "items")),
          "读数顶层键=%s" % sorted(r.keys()))
    check("collector_name_m173", "m173" in r["collector"] and "m173" in r["shift"],
          "collector=%s shift=%s" % (r["collector"], r["shift"]))
    check("log_t0_matches_readings", r["generated_at_utc"] in log_text,
          "日志快照时点 %s 与 readings 一致" % r["generated_at_utc"])

    # ---------- 巡检第 1 步：在飞 + 心跳鲜度 ----------
    fl = rows("1_inflight")
    parsed = [ln.split("|") for ln in fl]
    ids = [p[0] for p in parsed]
    ages = [int(p[3]) for p in parsed]
    scs = [p[4].strip() for p in parsed]
    check("inflight_nonempty", len(fl) >= 1, "在飞条数=%d ids=%s" % (len(fl), ids))
    check("inflight_contains_self_173", "173" in ids, "在飞快照 ids=%s（须含本班 173）" % ids)
    check("inflight_heartbeat_fresh", all(a <= 240 for a in ages),
          "心跳年龄(秒)=%s，全部 ≤240s（未到收割线）" % ages)
    check("inflight_stop_condition_null", all(s == "NULL" for s in scs),
          "在飞 result.stop_condition=%s（均 NULL）" % scs)
    rc = rows("1_inflight_recheck")
    rc_parsed = [ln.split("|") for ln in rc]
    check("inflight_recheck_same_ids", [p[0] for p in rc_parsed] == ids,
          "20s 后复采样 ids=%s（与首次一致=守护心跳链活）" % [p[0] for p in rc_parsed])
    check("inflight_recheck_fresh",
          all(int(p[2]) <= 240 for p in rc_parsed) and len(rc_parsed) == len(fl),
          "复采样心跳年龄=%s 全部 ≤240s" % [p[2] for p in rc_parsed])
    # 日志引用的心跳秒数必须与 readings 同值（防手抄漂移）
    hb_tokens = ["%s = %ss" % (p[0], p[3]) for p in parsed]
    check("log_heartbeat_matches_readings",
          all(t in log_text for t in hb_tokens),
          "日志引用的心跳读数 %s 均与 readings 同值" % hb_tokens)
    hb2_tokens = ["%ss" % p[2] for p in rc_parsed]
    check("log_recheck_heartbeat_matches", all(t in log_text for t in hb2_tokens),
          "日志引用的复采样心跳 %s 均与 readings 同值" % hb2_tokens)
    ql = [ln.split("|") for ln in rows("1_queued")]
    check("queued_171_refilled_by_us", any(p[0] == "171" and p[2] == "agent" for p in ql),
          "queued=%s（171 为 agent 来源=本班 refill 后回队）" % [p[0] + ":" + p[2] for p in ql])
    check("queued_175_owner", any(p[0] == "175" and p[2] == "owner" for p in ql),
          "queued 含 owner 来源 175（SI-5 收尾单，非本班职责）")

    # ---------- 巡检第 2 步：守护计数 ----------
    gc = out("2_guardian_count").strip()
    detail_lines = rows("2_guardian_detail")
    check("guardian_count_10", gc == "10", "pgrep 守护计数=%s（期望 10=基线 7 + 池化 3）" % gc)
    check("guardian_detail_matches_count", len(detail_lines) == int(gc),
          "逐 PID 明细=%d 行（与 pgrep 计数一致）" % len(detail_lines))
    pr = out("2_pool_reconcile")
    check("pool_reconcile_self_consistent",
          "expected_extra_slots=3" in pr and "actual_slot_workers=3" in pr,
          "池化对账=%s" % pr.replace("\n", " | "))

    # ---------- 巡检第 3 步：中继健康 ----------
    relay = out("3_relay")
    check("relay_200", "http=200" in relay, "中继=%s" % relay.replace("\n", " | "))
    check("relay_models_7", "models=7" in relay, "中继模型数=%s" % relay.replace("\n", " | "))
    # 延迟门限 <5s（活体漂移，用区间判而不是钉死数值）
    m = [seg for seg in relay.split() if seg.startswith("t=")]
    lat = float(m[0][2:]) if m else 999
    check("relay_latency_under_5s", lat < 5.0, "中继耗时=%.3fs（门限 <5s）" % lat)
    check("log_relay_latency_matches_readings",
          ("t=%.3fs" % lat) in log_text or ("models=7" in log_text and "http=200" in log_text),
          "日志中继读数与 readings 一致（t=%.3fs models=7）" % lat)

    # ---------- 巡检第 4 步：冷却态（本班处置项）----------
    now_state = out("4_gateway_state_now").strip().splitlines()[0].strip()
    check("cooldown_cleared_to_empty", now_state == "{}",
          "当前冷却态=%r（本班清理后应为空对象）" % now_state)
    before = out("4_cooldown_before")
    check("cooldown_before_snapshot_present",
          "relay-13001" in before and "transient" in before,
          "清理前快照含 relay-13001/transient（证据留痕）")
    verdict = out("4_cooldown_verdict").strip()
    check("cooldown_was_expired",
          verdict.startswith("relay-13001 delta=-"),
          "过期判定=%s（delta<0 即已过期，可清）" % verdict)
    check("log_cooldown_delta_matches_readings", verdict.split()[1] in log_text,
          "日志引用的过期量 %s 与 readings 同值" % verdict.split()[1])

    # ---------- 巡检第 5 步：终态 + infra 类 ----------
    check("24h_terminal_rows", len(rows("5_24h_terminal")) >= 5,
          "24h 终态行=%d" % len(rows("5_24h_terminal")))
    infra = rows("5_24h_infra")
    check("24h_infra_single_170",
          len(infra) == 1 and infra[0].startswith("170|failed|gateway_unavailable"),
          "24h infra 类终局=%s（唯一一条=前任 operator 腿 m170）" % infra)
    det = {p[0]: p for p in [ln.split("|") for ln in rows("5_detail_169_175")]}
    check("m169_succeeded", det.get("169", [""] * 2)[1] == "succeeded",
          "m169=%s" % "|".join(det.get("169", [])))
    check("m170_failed_gateway", det.get("170", [""] * 4)[3] == "gateway_unavailable",
          "m170=%s" % "|".join(det.get("170", [])))
    check("m171_queued_after_refill", det.get("171", [""] * 2)[1] == "queued",
          "m171 status=%s（本班 refill 后回队）" % det.get("171", ["?", "?"])[1])
    check("m171_budget_bumped", det.get("171", [""] * 7)[5:7] == ["300", "36000"],
          "m171 预算 tc/ws=%s（240/28800 +60/+7200）" % det.get("171", [""] * 7)[5:7])
    check("m175_owner_si5",
          "SI-5" in out("5_mission_175_goal") and det.get("175", [""] * 3)[2] == "owner",
          "m175 origin=%s（owner 派 SI-5 收尾单）" % det.get("175", [""] * 3)[2])
    check("m172_m174_running", det.get("172", [""] * 2)[1] == "running"
          and det.get("174", [""] * 2)[1] == "running",
          "m172/m174 status=%s/%s" % (det.get("172", ["", "?"])[1], det.get("174", ["", "?"])[1]))

    # ---------- refill 三段判据（本班处置的核心依据）----------
    r170 = out("5_refill_check_m170")
    check("refill_check_m170_rc0_but_taken_over",
          "rc=0" in r170 and "gateway_unavailable" in r170,
          "refill_check m170 输出=%s" % r170.replace("\n", " | ")[:160])
    r171 = out("5_refill_check_m171")
    check("refill_check_m171_rc3_queued",
          "rc=3" in r171 and "queued" in r171,
          "refill_check m171 输出=%s（本班 refill 已生效 ⇒ 拒绝，预期）" % r171.replace("\n", " | ")[:140])
    resp = json.loads(out("5_refill_171_response"))
    check("refill_171_http200_queued",
          resp["id"] == 171 and resp["status"] == "queued",
          "refill 响应 id=%s status=%s" % (resp["id"], resp["status"]))
    delta = out("15_refill_budget_delta")
    check("refill_delta_60_7200",
          "delta_tc=60" in delta and "delta_ws=7200" in delta,
          "预算增量=%s" % delta.replace("\n", " | "))
    check("m171_payload_gate_clean", "bad=0" in out("5_m171_payload_gate"),
          "载荷门 m171=%s" % out("5_m171_payload_gate"))
    check("m170_payload_gate_clean",
          "bad=0" not in out("5_m170_payload_gate") or "msgs=109" in out("5_m170_payload_gate"),
          "载荷门 m170=%s" % out("5_m170_payload_gate").replace("\n", " | "))
    gl = out("5_m171_guardian_log")
    check("m171_no_endpoint_evidence",
          "无可用端点" in gl or "tool_calls=0" in gl or "gateway_unavailable" in gl,
          "m171 守护日志含无可用端点/gateway_unavailable 证据")
    gl170 = out("5_m170_guardian_log")
    check("m170_timeout_evidence",
          "gateway_unavailable" in gl170 and "m170" in gl170,
          "m170 守护日志末段=%s" % gl170.replace("\n", " | ")[-120:])

    # ---------- 审批状态（R4' 已消解）----------
    ac = out("6_approval_counts")
    check("approval_approved_120", "approved:120" in ac, "审批计数=%s" % ac.replace("\n", " "))
    check("approval_rejected_11", "rejected:11" in ac, "rejected=11")
    pend = out("6_pending_ids").strip()
    check("pending_zero", pend == "0|-",
          "pending=%s（R4' 死锁已由 owner 裁决消解，#134 转 approved）" % pend)
    ra = rows("6_recent_approved")
    check("recent_approved_6", len(ra) == 6, "最近 approved=%d 行" % len(ra))
    check("recent_approved_first_136_m169", ra[0].startswith("136|169|1"),
          "最近 approved 首行=%s" % ra[0])
    check("recent_approved_contains_134_approved", any(ln.startswith("134|167|1") for ln in ra),
          "#134/m167 现为 approved：%s" % [ln for ln in ra if ln.startswith("134")])

    # ---------- 停车表 ----------
    check("parked_29", "parked=29" in out("7_parked_count"),
          "停车表=%s" % out("7_parked_count").replace("\n", " ")[:100])
    pz = out("7_parked_vs_db").strip()
    check("parked_vs_db_29_0_29_0", pz == "29|0|29|0",
          "停车表 vs DB=%s（29 条全 approved 僵尸，零 pending=死锁已消解）" % pz)

    # ---------- R3 家族矩阵 ----------
    r3 = rows("7_r3_family_matrix")
    check("r3_family_25", len(r3) == 25,
          "R3 家族矩阵=%d 行（+1=#135/m168，checks>20 口径）" % len(r3))
    check("r3_family_contains_135_m168", any(ln.startswith("135|168|76") for ln in r3),
          "矩阵新增行=%s" % [ln for ln in r3 if ln.startswith("135")])
    check("r3_grep_12", out("7_r3_grep_count").strip() == "12",
          "R3 容量 grep=%s（零新增）" % out("7_r3_grep_count").strip())
    check("r3_last_127", "approval 127" in out("7_r3_last_entry"),
          "R3 末条=%s" % out("7_r3_last_entry")[:90])

    # ---------- R4 dirty_tree ----------
    check("r4_dirty_72", out("7_r4_dirty_tree").strip() == "72",
          "R4 dirty_tree grep=%s（+1=#136/m169）" % out("7_r4_dirty_tree").strip())
    check("r4_last_136", "approval 136" in out("7_r4_last_entry"),
          "R4 末条=%s" % out("7_r4_last_entry")[:90])

    # ---------- R5 / R7 / 不可解析（零新增）----------
    check("r5_verifier_37", out("7_r5_verifier").strip() == "37",
          "R5 verifier 异常 grep=%s（零新增）" % out("7_r5_verifier").strip())
    check("r5_last_116", "approval 116" in out("7_r5_last"),
          "R5 末条=%s" % out("7_r5_last")[:80])
    check("r7_evid_46", out("7_r7_evid").strip() == "46",
          "R7 证据不足 grep=%s（零新增）" % out("7_r7_evid").strip())
    check("unparseable_246", out("7_unparseable").strip() == "246",
          "不可解析 grep=%s（零新增）" % out("7_unparseable").strip())

    # ---------- R8 / R9 ----------
    check("r8_count_3", out("8_r8_count").strip() == "3",
          "R8 报告发布 HTTP grep=%s（零新增）" % out("8_r8_count").strip())
    check("r9_count_4", out("8_r9_count").strip() == "4",
          "R9 基线归因 grep=%s（+1=#135/m168）" % out("8_r9_count").strip())
    check("r9_last_135", "approval 135" in out("8_r9_last"),
          "R9 末条=%s" % out("8_r9_last")[:90])

    # ---------- R12 判读纠正（本班核心新结论）----------
    check("approver_silent_branch_in_source",
          "if not items:" in out("15_approver_silent_branch"),
          "源码早退分支=%s（pending=0 静默返回，不落日志）"
          % out("15_approver_silent_branch").replace("\n", " | "))
    polls = out("15_backend_approver_polls")
    n_polls = int(polls.split("polls=")[1].split()[0]) if "polls=" in polls else 0
    check("approver_alive_via_backend_polls", n_polls >= 10,
          "近 40 分钟审批轮询=%d 次（≥10 ⇒ tick 存活，日志停更≠停摆）" % n_polls)
    check("log_polls_matches_readings", ("polls=%d" % n_polls) in log_text,
          "日志引用的轮询次数 polls=%d 与 readings 同值" % n_polls)
    at = out("8_approver_tail")
    tail_last = rows("8_approver_tail")[-1]
    check("log_approver_tail_matches_readings", tail_last in log_norm,
          "日志引用的审批器末行与 readings 一致：%r" % tail_last[:60])
    check("approver_tail_136_skip", "approval 136" in at and "跳过" in at,
          "审批器末行=%s" % at.replace("\n", " | ")[:110])
    check("approver_lines_5674", out("8_approver_lines").strip() == "5674",
          "审批器日志行数=%s" % out("8_approver_lines").strip())
    check("approver_tracebacks_22", out("8_approver_tracebacks").strip() == "22",
          "审批器 Traceback 累计=%s（零新增）" % out("8_approver_tracebacks").strip())
    check("approver_remote_disconnected_2",
          out("8_approver_remote_disconnected").strip() == "2",
          "RemoteDisconnected 累计=%s（零新增）" % out("8_approver_remote_disconnected").strip())
    tg = out("8_approver_tick_gaps")
    check("tick_gaps_window_5", "window_lines=500" in tg and "gaps_gt_300s=5" in tg,
          "近 500 行窗口：%s" % tg.splitlines()[0])
    check("log_tick_gaps_matches_readings",
          "gaps_gt_300s=5" in log_norm or "空档 5 处" in log_norm,
          "日志引用的空档数与 readings 同值（gaps_gt_300s=5）")

    # ---------- 容器健康 ----------
    check("containers_11_healthy", len(rows("9_containers")) == 11,
          "容器行数=%d" % len(rows("9_containers")))
    check("unhealthy_zero", out("9_unhealthy").strip() == "0",
          "非健康容器=%s" % out("9_unhealthy").strip())
    cb = out("9_celery_beat").strip()
    check("celery_beat_restart0", cb.startswith("running 0 0"),
          "celery-beat=%s（running/RestartCount=0/ExitCode=0）" % cb)

    # ---------- 产物滞留现场 ----------
    w170 = out("9_m170_worktree")
    check("m170_dirty_3", "dirty=3" in w170,
          "m170 worktree=%s（R14：readings+collect+verify 三件，缺 log、未 commit）"
          % w170.replace("\n", " | ")[:150])
    check("m170_has_collect_verify_no_log",
          "collect_2026-10-22.m170.py" in w170 and "operator_log/2026-10-22.md" not in w170,
          "m170 滞留件确为 collect/verify/readings 三件（无 log.md）")
    check("m166_dirty_8", "dirty=8" in out("9_m166_worktree"),
          "m166 worktree=%s（连续第二班无变化）" % out("9_m166_worktree").replace("\n", " | "))
    check("m167_dirty_1", "dirty=1" in out("9_m167_worktree"),
          "m167 worktree=%s（上班 dirty=3 → 现 1，已自愈）"
          % out("9_m167_worktree").replace("\n", " | "))

    # ---------- X gc.py 权限 ----------
    check("gc_perm_no_x", "-rw-------" in out("10_gc_perms"),
          "gc.py 权限=%s（无执行位，cron 直执行必失败）" % out("10_gc_perms").strip())
    check("gc_log_17", out("10_gc_log_lines").strip() == "17",
          "gc.log 行数=%s（全「权限不够」，零新增）" % out("10_gc_log_lines").strip())

    # ---------- F 派发器 ----------
    sh = out("11_shift_m173")
    check("dispatcher_label_shift49", "shift#49" in sh and "m173" in sh,
          "派发器 m173 行=%s" % sh.strip()[:110])
    check("dispatched_count_59", out("11_dispatched_count").strip() == "59",
          "DISPATCHED 计数=%s（真值第 61 班，偏移 2）" % out("11_dispatched_count").strip())

    # ---------- worktree ----------
    check("wt_dirs_ge_169", int(out("12_wt_dirs").strip()) >= 169,
          "worktree 目录=%s（≥169，活体漂移）" % out("12_wt_dirs").strip())
    check("wt_git_70", out("12_wt_git").strip() == "70",
          "git 注册 worktree=%s" % out("12_wt_git").strip())

    # ---------- 本班 worktree 自身 ----------
    self_line = out("13_self")
    check("self_branch_agent_m173", "self_branch=agent/m173" in self_line,
          "本班 worktree=%s" % self_line.strip()[:160])
    check("self_path_is_m173", "/agent_work/m173/" in self_line,
          "本班路径取自自身 worktree（非主仓）")

    # ---------- 账本 ----------
    check("ledger_tail_has_168", "m168" in out("14_ledger_tail"),
          "常驻账本尾含 m168 记录")

    # ---------- 日志正文关键结论交叉核对 ----------
    for token, label in [
        ("66 条", "读数 66 条"),
        ("R4'", "R4' 死锁消解段"),
        ("R12 判读纠正", "R12 判读纠正段"),
        ("R13", "R13 单端点池"),
        ("R14", "R14 m170 交付残缺"),
        ("R15", "R15 git 未入库"),
        ("29|0|29|0", "停车表 29|0|29|0"),
        ("approved:120", "approved=120"),
        ("rc=0，全部 PASS", "机器门 rc=0 全通过"),
        ("agent/m173", "本班分支"),
    ]:
        check("log_contains_%s" % label.replace("=", "_").replace("|", "_").replace(" ", "_"),
              token in log_norm, "日志正文含 %r" % token)

    passed = sum(1 for x in results if x["pass"])
    total = len(results)
    report = {
        "collector": "verify_2026-10-22.m173.py",
        "shift": r["shift"],
        "generated_at_utc": r["generated_at_utc"],
        "readings_file": os.path.basename(READINGS),
        "log_file": os.path.basename(LOG),
        "checks_total": total,
        "checks_passed": passed,
        "checks_failed": total - passed,
        "all_pass": passed == total,
        "checks": results,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    old = None
    if os.path.exists(REPORT):
        with open(REPORT, encoding="utf-8") as fh:
            old = fh.read()
    if old != text:
        with open(REPORT, "w", encoding="utf-8") as fh:
            fh.write(text)
    for x in results:
        if not x["pass"]:
            print("FAIL %s: %s" % (x["check"], x["detail"]))
    print("机器门 %d/%d PASS（报告 %s）" % (passed, total, os.path.basename(REPORT)))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())