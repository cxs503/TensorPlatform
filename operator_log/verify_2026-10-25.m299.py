#!/usr/bin/env python3
"""verify_2026-10-25.m299.py — m299 operator 班次机器门（零手抄：断言全部直读 readings.json / DB / 工件）

用法（worktree 根）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a
  python3 operator_log/verify_2026-10-25.m299.py
RC=0 全 PASS；RC=1 有 FAIL；RC=2 fail closed（读数缺失）。

门的两类判据（m138 教训）：活体计数会漂 ⇒ 写成不等式或对快照内部一致性；
确指项（本班换血 pid、副作用腿 m301、R13 三段）写成钉死断言。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READINGS = os.path.join(HERE, "2026-10-25.m299.readings.json")
LOGPATH = os.path.join(HERE, "2026-10-25.md")
REPORT = os.path.join(HERE, "2026-10-25.m299.validation_report.json")
ECO = "/srv/tensor/shared/tensor_eco"
SHIFT_START_UTC = "2026-10-25T04:13:02Z"
# 24h 窗口（锚定班次起点）内 failed 全量 25 条 —— 必须与 readings 完全一致（案例 21）
CANDIDATES = [231, 244, 245, 249, 255, 256, 257, 259, 261, 269, 270, 271, 284, 285,
              286, 287, 288, 289, 290, 291, 292, 294, 295, 297, 301]
# 本班精确 kill 的 2 台空闲旧码守护（磁盘 mtime 之前启动）
KILLED_PIDS = [2726616, 2726910]
# 保留的 2 台旧码守护（在飞 / 本班自身）
KEPT_OLD_CODE_PIDS = [2726618, 2726917]

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": str(detail)[:220]})


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
    check("S1-self-included", 299 in s1["ids"], f"ids={s1['ids']}")
    check("S1-business-leg-present", 298 in s1["ids"],
          f"业务线在飞={sorted(set(s1['ids']) & {298})}")
    check("S1-no-stop-condition", s1["with_stop_condition"] == [],
          f"with_stop={s1['with_stop_condition']}")

    # ── S2 守护计数等式（等式判据，不取剧本 7 —— R18）──
    s2 = R["step2_guardian"]
    check("S2-equation-authority", s2["authority_sumN"] == s2["measured_total"],
          f"ΣN={s2['authority_sumN']} = measured={s2['measured_total']}")
    check("S2-equation-pgrep", s2["authority_sumN"] == s2["pgrep_count"],
          f"ΣN={s2['authority_sumN']} = pgrep={s2['pgrep_count']}")
    check("S2-authority-16", s2["authority_sumN"] == 16,
          f"ΣN={s2['authority_sumN']}（workers.json 现值）")
    check("S2-per-model-equal", s2["per_model_all_equal"] is True,
          f"per_model={ {k: (v['authority'], v['measured']) for k, v in s2['per_model'].items()} }")
    check("S2-slot-workers", s2["slot_workers"] == 10, f"slot={s2['slot_workers']}")
    live = json.load(open(f"{ECO}/TensorPlatform/tools/agent/workers.json", encoding="utf-8"))
    live_sum = sum(v for k, v in live.items() if not k.startswith("_"))
    check("S2-authority-live-recheck", live_sum == s2["authority_sumN"],
          f"live ΣN={live_sum} vs readings={s2['authority_sumN']}")

    # ── S2b 旧码判据（守护启动时点 vs 磁盘 mtime；HEAD-diff 因主仓 dirty 恒空 P3）──
    b = R["step2b_blood_swap"]
    check("S2b-guards-count-matches", b["guards_n"] == s2["measured_total"],
          f"guards={b['guards_n']} measured={s2['measured_total']}")
    check("S2b-code-mtime-pinned", b["code_mtime_local"] == "2026-10-25 09:12:10",
          f"磁盘 mtime={b['code_mtime_local']}")
    check("S2b-diff-additive-only", b["diff_is_additive_only"] is True,
          f"additive={b['diff_is_additive_only']} lines={b['diff_added_lines']}")
    check("S2b-no-dangerous-additions", b["dangerous_additions"] == [],
          f"dangerous={b['dangerous_additions']}")
    check("S2b-head-diff-blank-p3", b["blank_criterion_head_diff_empty"] is True,
          "HEAD-diff 判据恒空（主仓 dirty 未提交，P3）")
    check("S2b-old-code-still-listed", all(p in b["old_code_pids"] for p in KEPT_OLD_CODE_PIDS),
          f"旧码剩={b['old_code_pids']}")
    check("S2b-killed-not-in-old-code",
          not (set(KILLED_PIDS) & set(b["old_code_pids"])),
          f"已 kill 的 {KILLED_PIDS} 不在现役旧码集合")
    check("S2b-head-lacks-new-repos", b["extended_repo_risk"]["HEAD_grep_TensorBEM"] == "0",
          f"HEAD 版 TensorBEM 命中={b['extended_repo_risk']['HEAD_grep_TensorBEM']}")
    check("S2b-unknown-repo-hits-4",
          b["extended_repo_risk"]["log_unknown_repo_hits"] == 4,
          f"unknown repo 命中={b['extended_repo_risk']['log_unknown_repo_hits']}（m287/289/290/294）")

    # ── S2c 换血动作事实 ──
    c = R["step2c_blood_swap_action"]
    check("S2c-killed-pids-pinned", c["killed_pids"] == KILLED_PIDS, f"killed={c['killed_pids']}")
    check("S2c-kept-pids-pinned", c["kept_old_code_pids"] == KEPT_OLD_CODE_PIDS,
          f"kept={c['kept_old_code_pids']}")
    check("S2c-guard-count-restored", c["guard_count_after"] == 16,
          f"换血后守护={c['guard_count_after']}")
    check("S2c-fleet-spawn-recorded",
          any("spawned" in ln and "spawned=0" not in ln for ln in c["fleet_log_tail"]),
          f"fleet 拉起行={c['fleet_log_tail']}")
    check("S2c-action-executed", c["action"].startswith("executed"), c["action"][:80])

    # ── S2d 换血副作用（本班诚实记录：TOCTOU 打死 m301）──
    d = R["step2d_blood_swap_side_effect"]
    check("S2d-casualty-is-301", d["casualty_mission"] == 301, f"casualty={d['casualty_mission']}")
    check("S2d-casualty-row-agent-lost",
          d["row"] and d["row"]["status"] == "failed" and d["row"]["duration_s"] == 251,
          f"row={d['row']}")
    check("S2d-casualty-claimed-by-old-guard",
          d["row"] and "deepseek-v4-flash#2" in d["row"]["claimed_by"],
          f"claimed_by={d['row']['claimed_by'] if d['row'] else None}")
    check("S2d-toctou-gap-4s", d["gap_s_between_claim_and_kill"] == 4,
          f"claim→kill 间隔={d['gap_s_between_claim_and_kill']}s")
    check("S2d-self-heal-by-dispatcher", "重发" in d["self_heal"], d["self_heal"][:90])
    check("S2d-reported-n5", d["disposition"].startswith("[上报] N5"),
          d["disposition"][:90])
    # 活体复核：m301 未被 refill（仍是 failed、无 refill 审计）
    live301 = psql("SELECT status, result->'refill' IS NOT NULL FROM tensor_dev_missions "
                   "WHERE id=301")
    check("S2d-live-301-not-refilled",
          live301 and live301[0][0] == "failed" and live301[0][1] == "f",
          f"live m301={live301}")

    # ── S3 中继健康 ──
    s3 = R["step3_relay"]
    check("S3-http-200", s3["http_code"] == 200, f"http={s3['http_code']}")
    check("S3-latency", (s3["time_total_s"] or 99) < 5, f"t={s3['time_total_s']}s")
    check("S3-models-7", s3["models"] == 7, f"models={s3['models']}")
    check("S3-single-endpoint", s3["endpoints_n"] == 1, f"endpoints={s3['endpoint_names']}")
    check("S3-code-caps-600", s3["code_caps_600"] is True, "min(cooldown,600) 在")

    # ── S4 冷却态（本班清理了过期 quota 冷却）──
    s4 = R["step4_cooldown"]
    check("S4-state-empty-now", s4["raw"] == {}, f"raw={s4['raw']}")
    check("S4-pre-clear-quota", (s4["pre_clear"].get("relay-13001", {}).get("reason")
                                 == "quota"),
          f"pre_clear={s4['pre_clear']}")
    check("S4-pre-clear-expired",
          s4["pre_clear_delta_s"].get("relay-13001", 1) < 0,
          f"blocked_until-now={s4['pre_clear_delta_s']}（负=已过期，可清）")
    check("S4-pre-clear-marked-at", s4["pre_clear_marked_at_utc"].get("relay-13001")
          == "2026-10-25T03:31:29Z", s4["pre_clear_marked_at_utc"])
    check("S4-snapshot-exists", os.path.exists(s4["pre_clear_snapshot_path"]),
          s4["pre_clear_snapshot_path"])
    check("S4-action-cleared", s4["action"].startswith("cleared"), s4["action"][:90])
    live_state = json.load(open(f"{ECO}/agent_work/llm_gateway_state.json", encoding="utf-8"))
    check("S4-live-state-empty", live_state == {}, f"live={live_state}")

    # ── S5 终态 + R13 三段式 ──
    s5 = R["step5_terminal"]
    check("S5-h24-failed-25", s5["h24_failed"] == 25, f"h24_failed={s5['h24_failed']}")
    check("S5-h24-succeeded-ge-40", s5["h24_succeeded"] >= 40,
          f"h24_succeeded={s5['h24_succeeded']} >= 40")
    check("S5-since-shift-2", s5["since_shift_failed"] == 2,
          f"since_shift_failed={s5['since_shift_failed']}")
    check("S5-failed-by-stop",
          s5["h24_failed_by_stop"].get("gateway_unavailable") == 17
          and s5["h24_failed_by_stop"].get("agent_lost") == 7
          and s5["h24_failed_by_stop"].get("budget_exceeded") == 1,
          s5["h24_failed_by_stop"])
    check("S5-window-ids-equal-candidates",
          {f["id"] for f in s5["failed_missions"]} == set(CANDIDATES),
          f"ids={sorted(f['id'] for f in s5['failed_missions'])}")
    live_win = psql("SELECT string_agg(id::text, ',' ORDER BY id) FROM tensor_dev_missions "
                    f"WHERE status='failed' AND finished_at > timestamptz "
                    f"'{SHIFT_START_UTC}' - interval '24 hours'")
    check("S5-live-window-recheck",
          live_win and live_win[0][0].split(",") == [str(i) for i in sorted(CANDIDATES)],
          f"live={live_win[0][0] if live_win else None}")
    check("S5-since-shift-includes-301",
          301 in {m["id"] for m in s5["since_shift_missions"]},
          f"since={[m['id'] for m in s5['since_shift_missions']]}")
    check("S5-awaiting-zero", s5["awaiting_acceptance"] == 0,
          f"awaiting={s5['awaiting_acceptance']}")
    # 三条 agent_lost 关键腿在册（N1/换血面）
    for mid in (287, 289, 290, 294, 301):
        row = [f for f in s5["failed_missions"] if f["id"] == mid]
        check(f"S5-m{mid}-agent-lost-recorded",
              len(row) == 1 and row[0]["stop_condition"] == "agent_lost", f"m{mid}={row}")
    row297 = [f for f in s5["failed_missions"] if f["id"] == 297]
    check("S5-m297-budget-exceeded",
          len(row297) == 1 and row297[0]["stop_condition"] == "budget_exceeded",
          f"m297={row297}")

    # ── S5c 冷却波定性 ──
    w = R["step5c_cool_wave"]
    check("S5c-wave-marks-quota", w["cool_mark_utc"] == "2026-10-25T03:31:29Z",
          w["cool_mark_utc"])
    check("S5c-this-shift-window-clean", w["same_wave_fails"] == [],
          f"本班 ±20min 窗口同波失败={w['same_wave_fails']}")
    check("S5c-prev-shift-271-failed",
          w["prev_shift_m271"]["status"] == "failed"
          and w["prev_shift_m271"]["zero_delivery"] is True,
          f"m271={w['prev_shift_m271']['status']} zero_delivery")
    check("S5c-prev-shift-duration",
          w["prev_shift_m271"]["duration_s"] > 10000,
          f"m271 duration={w['prev_shift_m271']['duration_s']}s")
    check("S5c-m261-n3-case",
          w["m261_n3_case"]["zero_delivery"] is True
          and w["m261_n3_case"]["duration_s"] == 3, "N3 案例在册")
    # 同波 10 条秒死的事实活体复核
    live_wave = psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                     "AND result->>'stop_condition'='gateway_unavailable' "
                     "AND finished_at >= timestamptz '2026-10-25T03:31:00Z' "
                     "AND finished_at < timestamptz '2026-10-25T03:40:00Z'")
    check("S5c-live-wave-count-10", live_wave and live_wave[0][0] == "10",
          f"03:31~03:40Z gateway 秒死={live_wave[0][0] if live_wave else None}")

    # ── S5b R13 三段 + 定日 ──
    r13 = R["step5b_r13"]
    check("R13-events-since-shift-empty", r13["events_since_shift"] == [],
          f"since_shift={r13['events_since_shift']}")
    check("R13-transient-since-shift-empty", r13["transient_since_shift"] == [],
          f"transient={r13['transient_since_shift']}")
    check("R13-events-today-includes-quota",
          "2026-10-25T11:31:29" in r13["events_today"],
          f"today={r13['events_today']}")
    check("R13-events-today-before-shift",
          all(t < r13["shift_start_local"] for t in r13["events_today"]),
          f"today={r13['events_today']} < shift_start={r13['shift_start_local']}")
    da = R["dating_anchors"]
    check("DA-cooldown-verified-nonzero", da["cooldown_events_verified"] >= 19,
          f"verified={da['cooldown_events_verified']}")
    check("DA-cooldown-all-correct", da["cooldown_events_bad"] == 0,
          f"bad={da['cooldown_events_bad']}")
    check("DA-monotonic", da["monotonic_violations"] == [],
          f"violations={da['monotonic_violations']}")

    # ── S6 容器体检 ──
    s6 = R["step6_containers"]
    check("S6-all-healthy", s6["total"] >= 10 and s6["healthy"] == s6["total"],
          f"{s6['healthy']}/{s6['total']} healthy，unhealthy={s6['unhealthy']}")

    # ── S7 refill 门：零 refill，25 条全被判读 ──
    s7 = R["step7_refill_gate"]
    check("S7-all-refill-check-rc0",
          all(v["rc"] == 0 for v in s7["refill_checks"].values()),
          {k: v["rc"] for k, v in s7["refill_checks"].items()})
    check("S7-candidates-cover-window",
          sorted(int(k[1:]) for k in s7["refill_checks"]) == sorted(CANDIDATES),
          f"n={len(s7['refill_checks'])}")
    check("S7-zero-refilled", s7["refilled"] == [], f"refilled={s7['refilled']}")
    check("S7-no-unjudged-candidate",
          all("需按剧本处置" not in v for v in s7["verdicts"].values()),
          [k for k, v in s7["verdicts"].items() if "需按剧本处置" in v])
    check("S7-301-verdict-blames-this-shift",
          "TOCTOU" in s7["verdicts"]["m301"] and "重发" in s7["verdicts"]["m301"],
          s7["verdicts"]["m301"][:120])
    check("S7-expand-verdict-code-root-cause",
          all("unknown repo" in s7["verdicts"][f"m{i}"] for i in (287, 289, 290, 294)),
          "EXPAND-FOLLOW 四腿按代码面根因挡下")
    check("S7-float-verdict-dup-legs",
          all("4 条腿" in s7["verdicts"][f"m{i}"] for i in (284, 285, 286, 288)),
          "floating 线按 N4 重复腿挡下")

    # ── S8 派发器 queued 缺陷（N4 第二次实证）──
    s8 = R["step8_dispatcher_queued_defect"]
    check("S8-whitelist-lacks-queued", "queued" not in s8["whitelist_in_flight"],
          f"whitelist={s8['whitelist_in_flight']}")
    check("S8-api-initial-queued", s8["api_initial_status"] == "queued",
          s8["api_initial_status"])
    check("S8-queued-events-7", s8["queued_terminal_events_n"] == 7,
          f"n={s8['queued_terminal_events_n']}")
    check("S8-float-line-dup-legs",
          len(s8["legs_float"]) >= 4,
          f"float legs={[l['id'] for l in s8['legs_float']]}")
    check("S8-float-state-retries-3",
          s8["state_item_float"] and s8["state_item_float"].get("retries") == 3,
          f"state={s8['state_item_float']}")
    check("S8-action-reported", s8["action"].startswith("[上报]"), s8["action"][:80])

    # ── 审计面 ──
    a = R["audit"]
    check("A-approval-pending-zero", a["approval"]["pending"] == 0,
          f"pending={a['approval']['pending']}")
    check("A-approval-max-id", a["approval_max_id"] >= 215, f"max_id={a['approval_max_id']}")
    check("A-parking-lot-empty", a["parking_lot"] == "{}", f"lot={a['parking_lot']}")
    check("R16-passall-marker-empty", a["pass_all"]["exists"]
          and a["pass_all"]["size_bytes"] == 0, f"size={a['pass_all']['size_bytes']}")
    check("R16-passall-rounds", a["pass_all"]["rounds"] > 0,
          f"rounds={a['pass_all']['rounds']}")
    check("X-gc-log-perm", "权限不够" in a["gc_log_last"], a["gc_log_last"])
    check("A-shift-switch-on", a["shift_switch_off"] is False, "OFF 未拉")
    check("A-worktree-branch", a["worktree_branch"] == "agent/m299",
          f"branch={a['worktree_branch']}")
    check("A-worktree-count-ge-122", a["worktree_total"] >= 122,
          f"worktree={a['worktree_total']}")
    check("P3-main-dirty-16", a["main_dirty"] == 16, f"main_dirty={a['main_dirty']}")

    # ── 日志内容断言（结论式而非流水账）──
    check("LOG-shift-id", "m299" in LOG and "第 103 班" in LOG, "m299 / 第 103 班")
    for kw in ["在飞心跳鲜度", "守护计数", "中继健康", "网关冷却态", "终态 mission result"]:
        check(f"LOG-step-{kw}", kw in LOG, kw)
    check("LOG-r13-three-segments",
          all(k in LOG for k in ["h24_failed", "since_shift_failed", "events_today"]),
          "R13 三段")
    check("LOG-report-section", "## 三、[上报] 项" in LOG, "[上报] 项段在")
    check("LOG-action-section", "## 二、处置动作" in LOG, "处置动作段在")
    check("LOG-next-shift-advice", "给下一班的建议" in LOG, "建议段在")
    check("LOG-no-placeholder", "TODO" not in LOG and "TBD" not in LOG, "无占位符")
    check("LOG-no-raw-transcript", LOG.count("```") <= 2, "结论式记录（≤1 代码块）")
    check("LOG-blood-swap-declared",
          "换血" in LOG and all(str(p) in LOG for p in KILLED_PIDS),
          f"kill pid {KILLED_PIDS} 逐台在册")
    check("LOG-kept-old-code-declared",
          all(str(p) in LOG for p in KEPT_OLD_CODE_PIDS),
          f"保留旧码 pid {KEPT_OLD_CODE_PIDS} 在册")
    check("LOG-n5-toctou", "N5" in LOG and "TOCTOU" in LOG and "m301" in LOG,
          "[上报] N5 换血 TOCTOU 在册")
    check("LOG-n4-second-proof", "N4" in LOG and "第二次" in LOG,
          "[上报] N4 第二次实证在册")
    check("LOG-quota-wave", "quota" in LOG and "03:31:29Z" in LOG,
          "[上报] quota 冷却波在册")
    check("LOG-p-case8", "P（案例 8" in LOG or "案例 8" in LOG, "[上报] P 交付保留互锁")
    check("LOG-n1-state", "N1" in LOG and "timed out after 300 seconds" in LOG,
          "N1 复发情况在册")
    check("LOG-x-still-open", "权限不够" in LOG and "125" in LOG, "X 未修 + worktree 数在册")
    check("LOG-inflight-count-matches", f"在飞 **{s1['count']}**" in LOG,
          f"在飞 {s1['count']} 条（日志与 readings 一致）")
    check("LOG-guardian-equation", "ΣN **16**" in LOG, "守护等式 16")
    check("LOG-relay-200", "中继" in LOG and "200" in LOG, "中继 200")
    check("LOG-refill-zero", "零**" in LOG or "**零**" in LOG, "零 refill 声明")
    check("LOG-cooldown-cleared", "已过期" in LOG and "probe_m299" in LOG,
          "冷却态清理与快照路径在册")
    check("LOG-expand-repos", all(k in LOG for k in ("TensorBEM", "TensorSPH", "TensorDG")),
          "旧码缺三库风险面在册")
    check("LOG-worktree-125", "125" in LOG, "worktree 125 在册")

    total = len(checks)
    passed = sum(1 for c in checks if c["pass"])
    report = {
        "shift_mission_id": 299,
        "shift": "第 103 班",
        "shift_start_utc": SHIFT_START_UTC,
        "collected_at_utc": R["collected_at_utc"],
        "readings": os.path.relpath(READINGS, os.path.dirname(HERE)),
        "checks_total": total,
        "checks_passed": passed,
        "checks_failed": total - passed,
        "all_pass": passed == total,
        "results": checks,
    }
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
    raise SystemExit(main())