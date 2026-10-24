#!/usr/bin/env python3
"""verify_2026-10-25.m264.py — m264 operator 班次机器门（零手抄：断言全部直读 readings.json / DB / 工件）

用法（worktree 根）：
  set -a; source /nfs/wangxi/tensor_eco/agent_work/operator.env; set +a
  python3 operator_log/verify_2026-10-25.m264.py
RC=0 全 PASS；RC=1 有 FAIL；RC=2 fail closed（读数缺失）。

门的两类判据（m138 教训）：活体计数会漂 ⇒ 写成不等式或对快照内部一致性；
确指项（本班窗口零新增 failed、旧码守护集合）写成钉死断言。
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READINGS = os.path.join(HERE, "2026-10-25.m264.readings.json")
LOGPATH = os.path.join(HERE, "2026-10-25.md")
REPORT = os.path.join(HERE, "2026-10-25.m264.validation_report.json")
ECO = "/nfs/wangxi/tensor_eco"
SHIFT_START_UTC = "2026-10-24T19:13:02Z"
CANDIDATES = [230, 231, 244, 245, 249, 255, 256, 257, 259, 261]
# 本班实测载旧码守护（启动早于 tensor_agent.py 落盘 16:35:58 CST）
OLD_CODE_PIDS = [2726616, 2726618, 2726910, 2726917]

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": str(detail)[:200]})


def psql(sql):
    env = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")
    r = subprocess.run(["docker", "exec", "open-foundation-postgres", "psql",
                        "-U", "foundation", "-d", "open_digital_foundation",
                        "-A", "-F", "|", "-t", "-c", sql],
                       capture_output=True, text=True, env=env, timeout=60)
    return [ln.split("|") for ln in r.stdout.strip().splitlines() if ln.strip()]


def main():
    if not os.path.exists(READINGS):
        print("fail closed: readings 缺失")
        return 2
    R = json.load(open(READINGS, encoding="utf-8"))
    LOG = open(LOGPATH, encoding="utf-8").read()

    # ── S1 在飞心跳鲜度 ──
    s1 = R["step1_inflight"]
    check("S1-has-inflight", s1["count"] >= 1, f"在飞 {s1['count']}")
    check("S1-hb-fresh", s1["max_hb_s"] < 240, f"max_hb={s1['max_hb_s']}s < 240s")
    check("S1-none-stale", s1["stale_over_240s"] == [], f"stale={s1['stale_over_240s']}")
    check("S1-self-included", 264 in s1["ids"], f"ids={s1['ids']}")
    check("S1-business-legs-present", 262 in s1["ids"],
          f"业务线在飞={sorted(set(s1['ids']) & {262})}")
    check("S1-no-stop-condition", s1["with_stop_condition"] == [],
          f"with_stop={s1['with_stop_condition']}")

    # ── S2 守护计数等式（等式判据，不取剧本 7 —— R18）──
    s2 = R["step2_guardian"]
    check("S2-equation-authority", s2["authority_sumN"] == s2["measured_total"],
          f"ΣN={s2['authority_sumN']} = measured={s2['measured_total']}")
    check("S2-equation-pgrep", s2["authority_sumN"] == s2["pgrep_count"],
          f"ΣN={s2['authority_sumN']} = pgrep={s2['pgrep_count']}")
    check("S2-per-model-equal", s2["per_model_all_equal"] is True,
          f"per_model={ {k: (v['authority'], v['measured']) for k, v in s2['per_model'].items()} }")
    check("S2-slot-workers", s2["slot_workers"] == 10, f"slot={s2['slot_workers']}")
    # 权威源活体直读复核（抓「readings 造假/陈旧」）
    live = json.load(open(f"{ECO}/TensorPlatform/tools/agent/workers.json", encoding="utf-8"))
    live_sum = sum(v for k, v in live.items() if not k.startswith("_"))
    check("S2-authority-live-recheck", live_sum == s2["authority_sumN"],
          f"live ΣN={live_sum} vs readings={s2['authority_sumN']}")

    # ── S2b 换血判据（本班 P3：m258 的 HEAD-diff 判据恒空，改守护启动时点判）──
    b = R["step2b_blood_swap"]
    check("S2b-guards-count-matches", b["guards_n"] == s2["measured_total"],
          f"guards={b['guards_n']} measured={s2['measured_total']}")
    check("S2b-old-code-detected", b["guards_predating_code_n"] == len(OLD_CODE_PIDS),
          f"载旧码 {b['guards_predating_code_n']} 台={b['guards_predating_code']}")
    check("S2b-old-code-pids-pinned",
          sorted(b["guards_predating_code"]) == sorted(OLD_CODE_PIDS),
          f"pids={sorted(b['guards_predating_code'])}")
    check("S2b-code-landing-time", b["code_mtime_local"] == "2026-10-24 16:35:58",
          f"mtime={b['code_mtime_local']}")
    check("S2b-blank-criterion-is-blind", b["blank_criterion_head_diff_empty"] is True,
          "HEAD-diff 恒空 ⇒ m258 判据对未提交修复面天然盲（P3 实证）")
    check("S2b-diff-additive", b["diff_is_additive_only"] is True
          and b["diff_added_lines"] == 5, f"added={b['diff_added_lines']}")
    check("S2b-no-dangerous-additions", b["dangerous_additions"] == [],
          f"危险新增={b['dangerous_additions']}")
    check("S2b-verdict-recorded", "零换血" in b["verdict"] and "旧码" in b["verdict"],
          b["verdict"][:60])
    # 旧码不含四库（P3 的风险面，直读 git HEAD 版复核）
    head_src = subprocess.run(
        ["git", "-C", f"{ECO}/TensorPlatform", "show",
         "HEAD:tools/agent/tensor_agent.py"],
        capture_output=True, text=True, timeout=60).stdout
    disk_src = open(f"{ECO}/TensorPlatform/tools/agent/tensor_agent.py",
                    encoding="utf-8").read()
    check("S2b-old-code-lacks-expansion-repos",
          "TensorBEM" not in head_src and "TensorBEM" in disk_src,
          "HEAD 版无四库、磁盘版有 ⇒ 旧码守护无法 claim 扩编线")
    # 该风险面上班未触发：全日志无 unknown repo
    unknown = subprocess.run(
        ["bash", "-lc", f"grep -h 'unknown repo' {ECO}/agent_work/agent-*.log | wc -l"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    check("S2b-unknown-repo-not-triggered", unknown == "0", f"unknown repo 命中={unknown}")
    # 活体复核判据基准：代码落盘时刻未被改动（mtime 漂移即判据失效，须重采 readings）
    check("S2b-live-code-mtime-unchanged",
          abs(os.stat(f"{ECO}/TensorPlatform/tools/agent/tensor_agent.py").st_mtime
              - b["code_mtime_epoch"]) < 1,
          f"live_mtime={os.stat(f'{ECO}/TensorPlatform/tools/agent/tensor_agent.py').st_mtime:.0f}"
          f" readings={b['code_mtime_epoch']:.0f}")

    # ── S3 中继健康 ──
    s3 = R["step3_relay"]
    check("S3-http-200", s3["http_code"] == 200, f"http={s3['http_code']}")
    check("S3-latency", (s3["time_total_s"] or 99) < 5, f"t={s3['time_total_s']}s")
    check("S3-models-7", s3["models"] == 7, f"models={s3['models']}")
    check("S3-single-endpoint", s3["endpoints_n"] == 1,
          f"endpoints={s3['endpoint_names']}")

    # ── S4 冷却态（本班零写操作：接班时已 {} 空）──
    s4 = R["step4_cooldown"]
    check("S4-state-empty-on-inherit", s4["raw"] == {},
          f"raw={s4['raw']}")
    check("S4-no-pre-clear-snapshot", not s4["pre_clear"],
          f"pre_clear={s4['pre_clear']}")
    check("S4-action-none", s4["action"].startswith("none"),
          s4["action"][:80])
    check("S4-active-now-empty", s4["active_now"] == {},
          f"active={s4['active_now']}")
    live_state = json.load(open(f"{ECO}/agent_work/llm_gateway_state.json", encoding="utf-8"))
    check("S4-live-state-now-empty", live_state == {}, f"live={live_state}")
    # 冷却态空时中继仍 200（端点面健康）
    check("S4-relay-still-200", s3["http_code"] == 200,
          f"http={s3['http_code']}")

    # ── S5 终态 + R13 三段式 ──
    s5 = R["step5_terminal"]
    check("S5-h24-failed-10", s5["h24_failed"] == 10, f"h24_failed={s5['h24_failed']}")
    # 活体计数会随平台推进上漂：写成对快照的不等式（m138 教训：不钉死漂移计数）
    check("S5-h24-succeeded-ge-22", s5["h24_succeeded"] >= 22,
          f"h24_succeeded={s5['h24_succeeded']} >= 22")
    check("S5-since-shift-zero", s5["since_shift_failed"] == 0,
          f"since_shift={s5['since_shift_failed']}")
    check("S5-since-shift-empty-or-clean",
          s5["since_shift_missions"] == []
          or all(m["status"] != "failed" for m in s5["since_shift_missions"]),
          f"since={s5['since_shift_missions']}")
    check("S5-failed-by-stop",
          s5["h24_failed_by_stop"].get("gateway_unavailable") == 8
          and s5["h24_failed_by_stop"].get("agent_lost") == 2
          and "budget_exceeded" not in s5["h24_failed_by_stop"],
          s5["h24_failed_by_stop"])
    # 本班 h24_failed 与 m263 完全相同（同一 10 条 ids），无滑出无新进
    check("S5-h24-same-as-prev-shift",
          {f["id"] for f in s5["failed_missions"]} == {230, 231, 244, 245, 249, 255, 256, 257, 259, 261},
          f"ids={sorted(f['id'] for f in s5['failed_missions'])}")
    # 冷却波结构直读（step5c）：本班窗口 [18:53Z, 19:33Z] 无 failed（波在上一班）
    w = R["step5c_cool_wave"]
    check("S5c-wave-fails-empty-this-shift",
          w["same_wave_fails"] == [],
          f"同波={w['same_wave_fails']}")
    check("S5c-m261-prev-shift-zero-delivery",
          w["m261_prev_shift"]["zero_delivery"] is True
          and w["m261_prev_shift"]["duration_s"] == 3,
          "上一班 m261 零交付 3 秒 tool_calls=0（N3 判据）")
    # 两条 agent_lost（N1 实证腿）仍在 24h 窗内
    for mid in (244, 255):
        row = [f for f in s5["failed_missions"] if f["id"] == mid]
        check(f"S5-m{mid}-agent-lost-recorded",
              len(row) == 1 and row[0]["stop_condition"] == "agent_lost", f"m{mid}={row}")
    for mid in (245, 249):
        row = [f for f in s5["failed_missions"] if f["id"] == mid]
        check(f"S5-m{mid}-gateway-unavailable-recorded",
              len(row) == 1 and row[0]["stop_condition"] == "gateway_unavailable",
              f"m{mid}={row}")
    check("S5-awaiting-zero", s5["awaiting_acceptance"] == 0,
          f"awaiting={s5['awaiting_acceptance']}")
    # 候选清单与 24h 窗口 id 集合逐条对齐（案例 21：漏项即漏判）
    window_ids = {f["id"] for f in s5["failed_missions"]}
    check("S5-candidates-equal-window", window_ids == set(CANDIDATES),
          f"窗口={sorted(window_ids)} 候选={sorted(CANDIDATES)}")
    check("S5-candidates-all-covered",
          set(R["step7_refill_gate"]["verdicts"]) == {f"m{i}" for i in CANDIDATES},
          "候选逐条有判读")
    # DB 活体复核：readings 的 h24 = 活体窗口内 failed 数（含本班窗口新增）
    live_h24 = psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                    f"AND finished_at > timestamptz '{SHIFT_START_UTC}' "
                    "- interval '24 hours'")[0][0]
    live_since = psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                      f"AND finished_at >= '{SHIFT_START_UTC}'")[0][0]
    check("S5-h24-live-recheck",
          int(live_h24) == s5["h24_failed"] + int(live_since),
          f"live={live_h24} readings={s5['h24_failed']}+since={live_since}")
    check("S5-since-shift-live-recheck", int(live_since) == 0, f"live since={live_since}")

    # ── R13 三段式第③段 ──
    r13 = R["step5b_r13"]
    # 当日（CST 10-25）有 1 条冷却事件 01:58:02——上一班冷却波，落在本班起点（03:13:02）之前。
    check("R13-events-today-one-cool-wave",
          r13["events_today"] == ["2026-10-25T01:58:02"],
          f"events_today={r13['events_today']}")
    check("R13-newest-015802", r13["newest_local_ts"] == "2026-10-25T01:58:02",
          f"newest={r13['newest_local_ts']}")
    check("R13-total-all-time", r13["total_all_time"] == 30,
          f"total_all_time={r13['total_all_time']}")
    check("R13-window-empty", r13["events_since_shift"] == [],
          f"since_shift={r13['events_since_shift']}")
    check("R13-transient-window-empty", r13["transient_since_shift"] == [],
          f"transient={r13['transient_since_shift']}")
    check("R13-window-anchor", r13["shift_start_local"] == "2026-10-25T03:13:02",
          r13["shift_start_local"])
    check("R13-cool-before-window-not-inside",
          all(t < r13["shift_start_local"] for t in r13["events_today"]),
          "当日冷却事件均早于班次起点 ⇒ 本班窗口零冷却")

    # ── 定日算法独立复核（m242 修正的延续）──
    da = R["dating_anchors"]
    check("DA-cooldown-verified-nonzero", da["cooldown_events_verified"] >= 18,
          f"verified={da['cooldown_events_verified']}")
    check("DA-cooldown-all-correct", da["cooldown_events_bad"] == 0,
          f"bad={da['cooldown_events_bad']}")
    check("DA-monotonic", da["monotonic_violations"] == [],
          f"violations={da['monotonic_violations']}")

    # ── 第 6 步容器体检 ──
    s6 = R["step6_containers"]
    check("S6-all-healthy", s6["total"] >= 10 and s6["healthy"] == s6["total"],
          f"{s6['healthy']}/{s6['total']} healthy，unhealthy={s6['unhealthy']}")

    # ── 审计面 ──
    a = R["audit"]
    check("A-approval-pending-zero", a["approval"]["pending"] == 0,
          f"pending={a['approval']['pending']}")
    check("A-approval-max-id", a["approval_max_id"] >= 194, f"max_id={a['approval_max_id']}")
    check("A-parking-lot-empty", a["parking_lot"] == "{}", f"lot={a['parking_lot']}")
    check("R16-passall-marker-empty", a["pass_all"]["exists"]
          and a["pass_all"]["size_bytes"] == 0, f"size={a['pass_all']['size_bytes']}")
    check("R16-passall-rounds", a["pass_all"]["rounds"] > 0,
          f"rounds={a['pass_all']['rounds']}")
    check("X-gc-log-perm", "权限不够" in a["gc_log_last"], a["gc_log_last"])
    check("A-shift-switch-on", a["shift_switch_off"] is False, "OFF 未拉")
    check("A-worktree-branch", a["worktree_branch"] == "agent/m264",
          f"branch={a['worktree_branch']}")
    check("A-worktree-count-ge-118", a["worktree_total"] >= 118,
          f"worktree={a['worktree_total']}")

    # ── 日志内容断言（结论式而非流水账）──
    check("LOG-shift-id", "m264" in LOG and "第 100 班" in LOG, "m264 / 第 100 班")
    for kw in ["在飞心跳鲜度", "守护计数", "中继健康", "网关冷却态", "终态 mission result"]:
        check(f"LOG-step-{kw}", kw in LOG, kw)
    check("LOG-r13-three-segments",
          all(k in LOG for k in ["h24_failed", "since_shift_failed", "events_today"]),
          "R13 三段")
    check("LOG-report-section", "## 三、[上报] 项" in LOG, "[上报] 项段在")
    check("LOG-next-shift-advice", "给下一班的建议" in LOG, "建议段在")
    check("LOG-no-placeholder", "TODO" not in LOG and "TBD" not in LOG, "无占位符")
    check("LOG-write-scope-declared",
          "psql 写 / kill / 守护换血 / refill / git push **全零**" in LOG
          and "零写操作" in LOG,
          "写操作面声明（零写操作）在")
    check("LOG-cool-empty-inherited",
          "{}" in LOG and "无需清理" in LOG,
          "冷却态空声明在")
    check("LOG-refill-zero", "零 refill" in LOG, "零 refill 声明")
    check("LOG-inflight-count-matches", f"在飞 **{s1['count']}**" in LOG,
          f"在飞 {s1['count']} 条（日志与 readings 一致）")
    check("LOG-guardian-equation", "ΣN **16**" in LOG, "守护等式 16")
    check("LOG-relay-200", "中继" in LOG and "200" in LOG, "中继 200")
    check("LOG-no-raw-transcript", LOG.count("```") <= 2, "结论式记录（无大段流水账代码块）")
    # 本班三条主结论必须在日志里（N3 续报 / P3 判据缺陷 / R13 口径）
    check("LOG-n3-prev-shift-zero-delivery",
          "N3" in LOG and "m261" in LOG and "tool_calls=0" in LOG,
          "[上报] N3 上一班零交付因果在")
    check("LOG-p3-criterion-defect",
          "P3" in LOG and "恒空" in LOG and "git diff" in LOG,
          "[上报] P3 判据缺陷因果在")
    check("LOG-four-old-guards",
          "4 台" in LOG and all(str(p) in LOG for p in OLD_CODE_PIDS),
          "4 台载旧码守护 pid 逐台在册")
    check("LOG-expansion-risk",
          "unknown repo" in LOG and "TensorBEM" in LOG, "旧码缺四库的风险面在")
    check("LOG-r13-flat-coincidence",
          "持平" in LOG and "同一 10 条" in LOG,
          "R13 h24_failed=10 持平=同一 10 条 ids 无滑出无新进判读在")
    check("LOG-n1-zero-recurrence", "N1" in LOG and "零复发" in LOG, "N1 零复发判读在")
    check("LOG-candidate-sql-aligned", "候选" in LOG and "refill" in LOG,
          "候选清单 refill 纪律在")
    check("LOG-report-items-complete",
          all(k in LOG for k in ("N3", "P3", "N1", "R18", "X", "R13")),
          "N3/P3/N1/R18/X/R13 全部在册")
    check("LOG-blood-swap-todo",
          "换血待办" in LOG and "2726917" in LOG and "勿盲杀" in LOG,
          "换血待办留给下一班（含空闲 pid 与自保提示）")
    check("LOG-m226-window-exit",
          "m226" in LOG and "滑出 24h" in LOG, "m226 滑出巡检窗口提示在")
    check("LOG-redlines", "不 git push" in LOG and "yueyue" in LOG, "红线复核在")

    total = len(checks)
    passed = sum(1 for c in checks if c["pass"])
    report = {
        "shift_mission_id": 264,
        "shift": "第 100 班",
        "shift_start_utc": SHIFT_START_UTC,
        "collected_at_utc": R["collected_at_utc"],
        "readings": os.path.relpath(READINGS, os.path.dirname(HERE)),
        "checks_total": total,
        "checks_passed": passed,
        "checks_failed": total - passed,
        "all_pass": passed == total,
        "results": checks,
    }
    # 报告幂等：写盘前与磁盘现有内容比对，内容相同则不写（避免复跑脏树）
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    old = None
    if os.path.exists(REPORT):
        old = open(REPORT, encoding="utf-8").read()
    if old != payload:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(payload)

    print(json.dumps({k: v for k, v in report.items() if k != "results"},
                     indent=2, ensure_ascii=False))
    print("=" * 60)
    print(f"PASSED: {passed}/{total}")
    if passed != total:
        print("FAILED:")
        for c in checks:
            if not c["pass"]:
                print(f"  ✗ {c['name']}: {c['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())