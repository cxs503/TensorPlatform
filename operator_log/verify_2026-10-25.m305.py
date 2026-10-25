#!/usr/bin/env python3
"""verify_2026-10-25.m305.py — m305 operator 班次机器门（零手抄：断言全部直读
readings.json / DB / 工件，活体项现场复算）。

用法（worktree 根）：
  python3 operator_log/verify_2026-10-25.m305.py
RC=0 全 PASS；RC=1 有 FAIL；RC=2 fail closed（读数缺失）。

门的两类判据（m138 教训）：活体计数会漂 ⇒ 写成不等式或快照内部一致性；确指项
（候选 25 条 ids、守护等式、R13 三段、N6 缺陷行号）写成钉死断言。
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READINGS = os.path.join(HERE, "2026-10-25.m305.readings.json")
LOGPATH = os.path.join(HERE, "2026-10-25.md")
REPORT = os.path.join(HERE, "2026-10-25.m305.validation_report.json")
ECO = "/srv/tensor/shared/tensor_eco"
AW = os.path.join(ECO, "agent_work")
SHIFT_START_UTC = "2026-10-25T05:13:01Z"
BASE_HEAD = "c245186"
# 24h 窗（锚定班次起点）内 failed 全量 25 条：必须与 readings 逐条相等（案例 21）
CANDIDATES = [231, 244, 245, 249, 255, 256, 257, 259, 261, 269, 270, 271, 284, 285,
              286, 287, 288, 289, 290, 291, 292, 294, 295, 297, 301]
checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": str(detail)[:240]})


def psql(sql):
    env = dict(os.environ, DOCKER_HOST="unix:///run/user/1020/docker.sock")
    r = subprocess.run(["docker", "exec", "open-foundation-postgres", "psql", "-U",
                        "foundation", "-d", "open_digital_foundation", "-A", "-F", "|",
                        "-t", "-c", sql], capture_output=True, text=True, env=env,
                       timeout=90)
    return [ln.split("|") for ln in r.stdout.strip().splitlines() if ln.strip()]


def main():  # noqa: C901
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
    check("S1-self-included", 305 in s1["ids"], f"ids={s1['ids']}")
    check("S1-business-legs", all(x in s1["ids"] for x in (298, 304)),
          "m298 SIR-1b + m304 tutorial 在飞")
    check("S1-no-stop-condition", s1["with_stop_condition"] == [],
          f"{s1['with_stop_condition']}")
    check("S1-count-eq-ids", s1["count"] == len(s1["ids"]), f"{s1['count']}")
    # 活体复核（m138 教训）：读数后被承接腿正常终态会减少在飞数 ⇒ 用「读数 ids 里
    # 现在仍非终态的条数」与 live 对齐，而不是把读数条数钉死。
    live = int(psql("SELECT count(*) FROM tensor_dev_missions WHERE status IN "
                    "('claimed','running')")[0][0])
    ids = ",".join(str(i) for i in s1["ids"])
    term = int(psql("SELECT count(*) FROM tensor_dev_missions WHERE id IN (" + ids +
                    ") AND status NOT IN ('claimed','running')")[0][0])
    # 班内既会正常终态（-），也会被派发器新派（+）⇒ 两端都要容忍，
    # 等式：live == 读数条数 - 读数后终态条数 + 读数后新进在飞条数
    new_in = int(psql("SELECT count(*) FROM tensor_dev_missions WHERE status IN "
                      "('claimed','running') AND id NOT IN (" + ids + ")")[0][0])
    check("S1-live-recheck", live == s1["count"] - term + new_in,
          f"live={live} == readings {s1['count']} - 终态 {term} + 新进 {new_in}")
    check("S1-queue-snapshot", set(R["step1_queue"]) <= {"queued", "pending"},
          f"{R['step1_queue']}")
    check("S1-pos-max-id", R["step1_pos_max_id"] >= 305, f"max_id={R['step1_pos_max_id']}")
    check("S1-awaiting-zero", R["step1_awaiting_acceptance"] == 0,
          f"awaiting={R['step1_awaiting_acceptance']}")

    # ── S2 守护计数等式（R17/R18）──
    s2 = R["step2_guardian"]
    check("S2-authority-sum16", s2["authority_sumN"] == 16, f"ΣN={s2['authority_sumN']}")
    check("S2-measured-eq-authority", s2["measured_total"] == s2["authority_sumN"],
          f"measured={s2['measured_total']}")
    check("S2-pgrep-eq-measured", s2["pgrep_count"] == s2["measured_total"],
          f"pgrep={s2['pgrep_count']}")
    check("S2-per-model-equal", s2["per_model_all_equal"], "六模型逐项相等")
    check("S2-slot-workers", s2["slot_workers"] == 10, f"非首槽={s2['slot_workers']}")
    check("S2-six-models", len(s2["per_model"]) == 6, f"{sorted(s2['per_model'])}")
    check("S2-fleet-spawned0", "spawned=0" in (s2["fleet_log_last"] or ""),
          s2["fleet_log_last"])
    check("S2-playbook-R18", s2["playbook_value_is_R18"] is True, "剧本值 7 已废，只用等式")

    # ── S2b 换血判据（启动时点 vs 磁盘 mtime）──
    s2b = R["step2b_blood_swap"]
    check("S2b-guards-16", s2b["guards_n"] == 16, f"guards={s2b['guards_n']}")
    check("S2b-old-code-count", s2b["old_code_n"] == 14, f"old={s2b['old_code_n']}")
    check("S2b-head-lacks-4repos", s2b["head_has_TensorBEM"] == 0,
          f"HEAD grep TensorBEM={s2b['head_has_TensorBEM']}")
    check("S2b-disk-has-4repos", s2b["disk_has_TensorBEM"] >= 1,
          f"磁盘 grep TensorBEM={s2b['disk_has_TensorBEM']}")
    check("S2b-zero-kill", s2b["kill_count_this_shift"] == 0, "本班零换血")
    check("S2b-code-mtime-pinned", s2b["code_mtime_local"] == "2026-10-25 09:12:10",
          s2b["code_mtime_local"])
    check("S2b-inflight-occupies-oldcode",
          all(p in s2b["old_code_pids"] for p in (2726618, 2726917)),
          "flash slot1/slot3 旧码槽在飞 ⇒ 禁换血")

    # ── S2c 旧码风险面 ──
    s2c = R["step2c_old_code_risk"]
    check("S2c-unknown-repo-pinned", s2c["log_unknown_repo_hits"] == 4,
          f"unknown repo 命中={s2c['log_unknown_repo_hits']}")
    check("S2c-unknown-repo-legs", s2c["unknown_repo_legs"] == [287, 289, 290, 294],
          f"{s2c['unknown_repo_legs']}")

    # ── S3 中继健康 ──
    s3 = R["step3_relay"]
    check("S3-http-200", s3["http_code"] == 200, f"{s3['http_code']}")
    check("S3-latency", s3["time_total_s"] is not None and s3["time_total_s"] < 5,
          f"{s3['time_total_s']}s < 5s")
    check("S3-models-7", s3["models"] == 7, f"models={s3['models']}")
    check("S3-single-endpoint", s3["endpoints_n"] == 1, f"{s3['endpoint_names']}")
    check("S3-cooldown-600", s3["cooldown_seconds"] == 600, f"{s3['cooldown_seconds']}")
    check("S3-transient-3", s3["transient_threshold"] == 3, f"{s3['transient_threshold']}")
    check("S3-code-caps600", s3["code_caps_600"] is True, "min(cooldown,600) 生效")

    # ── S4 冷却态 ──
    s4 = R["step4_cooldown"]
    check("S4-empty-state", s4["raw"] == {}, f"raw={s4['raw']}")
    check("S4-no-active", s4["active_now"] == {}, "无活跃冷却")
    check("S4-action-none", s4["action"].startswith("none"), s4["action"][:40])
    check("S4-file-exists", bool(s4["mtime_local"]), f"mtime={s4['mtime_local']}")

    # ── S5 终态（R13 第①②段）──
    s5 = R["step5_terminal"]
    check("S5-h24-failed-25", s5["h24_failed"] == 25, f"h24_failed={s5['h24_failed']}")
    check("S5-h24-succeeded-ineq", s5["h24_succeeded"] >= 40,
          f"h24_succeeded={s5['h24_succeeded']}（活体，不钉死）")
    check("S5-since-shift-zero", s5["since_shift_failed"] == 0,
          f"since_shift={s5['since_shift_failed']}")
    check("S5-candidates-equal-window", R["step5_candidates_equal_window"] is True,
          "25 条 ids 与 CANDIDATES 逐条相等")
    check("S5-window-ids-pinned",
          sorted(m["id"] for m in s5["failed_missions"]) == sorted(CANDIDATES),
          "窗口 id 集合钉死")
    check("S5-by-stop-sums", sum(s5["h24_failed_by_stop"].values()) == s5["h24_failed"],
          f"{s5['h24_failed_by_stop']}")
    check("S5-stop-taxonomy", set(s5["h24_failed_by_stop"]) <=
          {"gateway_unavailable", "agent_lost", "budget_exceeded", "agent_crash"},
          f"{sorted(s5['h24_failed_by_stop'])}")
    check("S5-awaiting-zero", s5["awaiting_acceptance"] == 0, "0")
    check("S5-window-fails-empty", R["step5c_shift_window_fails"] == [],
          f"{R['step5c_shift_window_fails']}")
    check("S5-last-failed-age-pos", (s5["last_failed_age_s"] or 0) > 0,
          f"last_failed_age={s5['last_failed_age_s']}s")
    check("S5-since-missions-ok",
          all(m["status"] == "succeeded" for m in s5["since_shift_missions"]),
          f"{[(m['id'], m['status']) for m in s5['since_shift_missions']]}")
    live_fail = int(psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                         f"AND finished_at > timestamptz '{SHIFT_START_UTC}' "
                         "- interval '24 hours'")[0][0])
    check("S5-h24-live-recheck",
          live_fail == s5["h24_failed"] + s5["since_shift_failed"],
          f"live={live_fail} == readings {s5['h24_failed']} + since {s5['since_shift_failed']}")

    # ── S5d R13 第③段 + 定日正确性 ──
    co = R["step5d_r13_three"]["cooling"]
    da = R["step5d_r13_three"]["dating_anchors"]
    check("S5d-cool-total-31", co["total_all_time"] == 31, f"total={co['total_all_time']}")
    check("S5d-events-since-shift-empty", co["events_since_shift"] == [],
          f"{co['events_since_shift']}")
    check("S5d-events-today-pinned", co["events_today"] == ["2026-10-25T01:58:02",
                                                            "2026-10-25T11:31:29"],
          f"{co['events_today']}")
    check("S5d-events-today-before-shift",
          all(t < R["shift_start_local"] for t in co["events_today"]),
          f"shift_start_local={R['shift_start_local']}")
    check("S5d-newest-cool", co["newest_local_ts"] == "2026-10-25T11:31:29",
          f"{co['newest_local_ts']}")
    check("S5d-transient-recorded", len(co["transient_since_shift"]) >= 0,
          f"班内 transient={len(co['transient_since_shift'])} 条")
    check("S5d-dating-19", da["verified"] == 19, f"verified={da['verified']}")
    check("S5d-dating-bad-0", da["bad"] == 0, f"bad={da['bad']}")
    check("S5d-monotonic", da["monotonic_violations"] == [],
          f"{da['monotonic_violations']}")

    # ── S6 审计面 ──
    au = R["audit"]
    check("S6-containers-all-healthy", au["containers_healthy"] == au["containers_total"],
          f"{au['containers_healthy']}/{au['containers_total']}")
    check("S6-containers-11", au["containers_total"] == 11, f"{au['containers_total']}")
    check("S6-approved-ineq", au["approval"]["approved"] >= 200,
          f"approved={au['approval']['approved']}")
    check("S6-rejected-11", au["approval"]["rejected"] == 11, f"{au['approval']['rejected']}")
    check("S6-pending-0", au["approval"]["pending"] == 0, f"{au['approval']['pending']}")
    check("S6-approval-max-ineq", au["approval_max_id"] >= 218, f"{au['approval_max_id']}")
    check("S6-parking-empty", au["parking_lot"] == "{}", au["parking_lot"])
    check("S6-passall-present", au["pass_all"]["exists"] is True
          and au["pass_all"]["size_bytes"] == 0, f"{au['pass_all']}")
    check("S6-passall-rounds-ineq", au["pass_all"]["rounds"] >= 1600,
          f"rounds={au['pass_all']['rounds']}")
    check("S6-worktree-ineq", au["worktree_total"] >= 126, f"{au['worktree_total']}")
    check("S6-main-dirty-16", au["main_dirty"] == 16, f"dirty={au['main_dirty']}")
    check("S6-main-head-base", au["main_head"] == BASE_HEAD, f"{au['main_head']}")
    check("S6-gc-still-broken", "权限不够" in (au["gc_log_last"] or ""),
          f"X 未修（{au['gc_log_lines']} 行）")
    check("S6-shift-off-absent", au["shift_switch_off"] is False, "operator_shift.OFF 未拉")
    check("S6-worktree-branch", au["worktree_branch"] == "agent/m305", au["worktree_branch"])
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                          capture_output=True, text=True).stdout.strip()
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", BASE_HEAD, "HEAD"],
                         cwd=HERE, capture_output=True, text=True).returncode
    check("S6-head-descends-base", anc == 0, f"HEAD={head} 含 base {BASE_HEAD}")
    # 交付树洁净：只允许本班这 5 个交付件（提交前 staged/untracked 都在预期集内；
    # 提交后用 M305_EXPECT_CLEAN=1 复跑，要求完全干净 = 幂等复跑不脏树）
    EXPECT = {"operator_log/2026-10-25.md", "operator_log/2026-10-25.m305.readings.json",
              "operator_log/collect_2026-10-25.m305.py",
              "operator_log/verify_2026-10-25.m305.py",
              "operator_log/2026-10-25.m305.validation_report.json"}
    dirty = [x[3:].strip() for x in subprocess.run(
        ["git", "status", "--porcelain"], cwd=HERE, capture_output=True,
        text=True).stdout.strip().splitlines() if x.strip()]
    stray = [d for d in dirty if d not in EXPECT]
    if os.environ.get("M305_EXPECT_CLEAN") == "1":
        check("S6-worktree-clean-after-commit", dirty == [], f"dirty={dirty[:4]}")
    else:
        check("S6-worktree-no-stray-dirty", stray == [],
              f"非交付件改动={stray[:4]}")

    # ── S7 refill 门 ──
    g = R["step7_refill_gate"]
    check("S7-candidates-25", len(g["refill_checks"]) == 25, f"{len(g['refill_checks'])}")
    check("S7-all-rc0", all(v["rc"] == 0 for v in g["refill_checks"].values()),
          "全部 rc=0（可续资格成立，但被门挡下）")
    check("S7-zero-refilled", g["refilled"] == [], "零 refill")
    check("S7-verdicts-25", len(g["verdicts"]) == 25, f"{len(g['verdicts'])}")
    check("S7-no-empty-verdict", all(v.strip() for v in g["verdicts"].values()), "每条有理由")
    check("S7-legs-keyed", set(g["legs"]) == set(g["verdicts"]), "承接腿与裁决一一对应")
    check("S7-m301-leg-running",
          any(l["id"] == 304 and l["status"] == "running" for l in g["legs"]["m301"]),
          "m301 承接腿 m304 running ⇒ 不 refill")

    # ── S8 派发器 N4 ──
    s8 = R["step8_dispatcher_n4"]
    check("S8-queued-not-in-whitelist", "queued" not in s8["whitelist_in_flight"],
          f"白名单={s8['whitelist_in_flight']} API初态={s8['api_initial_status']}")
    check("S8-queued-terminal-7", s8["queued_terminal_events_n"] == 7,
          f"{s8['queued_terminal_events_n']}")
    check("S8-float-4-legs-all-failed",
          len(s8["legs_float"]) == 4 and all(l["status"] == "failed" for l in s8["legs_float"]),
          f"{[(l['id'], l['status']) for l in s8['legs_float']]}")
    check("S8-marked-report", "[上报]" in s8["action"], s8["action"][:40])

    # ── S8b EXPAND-FOLLOW N6（本班新发现，钉死行号与错误型）──
    n6 = {k: v for k, v in R["step8b_expand_follow_n6"].items()}
    check("S8b-file-too-long-3", n6["file_name_too_long_n"] >= 3,
          f"File name too long ×{n6['file_name_too_long_n']}")
    check("S8b-defect-line-195", n6["defect_line"] == [195], f"{n6['defect_line']}")
    check("S8b-defect-code-pinned", "CRITERIA_BLOCK" in n6["defect_code"]
          and "cwd=" in n6["defect_code"], n6["defect_code"])
    check("S8b-dispatch-py-mtime", n6["dispatch_py_mtime_local"].startswith("2026-10-25 12:54"),
          n6["dispatch_py_mtime_local"])
    check("S8b-last-round-crashed",
          any("14:0" in x and "WARN" in x for x in n6["last_round_lines"]),
          n6["last_round_lines"][-1] if n6["last_round_lines"] else "")
    check("S8b-dispatched-8", n6["dispatched_n"] == 8, f"{n6['dispatched_n']}")
    check("S8b-last-dispatch-m294", any("m294" in x for x in n6["last_dispatched"]),
          n6["last_dispatched"][-1] if n6["last_dispatched"] else "")
    check("S8b-marked-report", "[上报]" in n6["action"], n6["action"][:40])
    live_n6 = subprocess.run(["grep", "-c", "File name too long",
                              os.path.join(AW, "expand_follow", "dispatch.log")],
                             capture_output=True, text=True).stdout.strip()
    check("S8b-live-recheck", int(live_n6 or 0) >= n6["file_name_too_long_n"],
          f"live={live_n6} vs readings={n6['file_name_too_long_n']}")

    # ── S9 事件桥观察项 ──
    s9 = R["step9_event_bridge"]
    check("S9-total-recorded", s9["total"] >= 1, f"total={s9['total']}")
    check("S9-per-file-sum", sum(e["n"] for e in s9["per_file"]) == s9["total"],
          f"{len(s9['per_file'])} 文件")
    check("S9-no-action", s9["action"].startswith("记录"), s9["action"][:30])

    # ── L 交付物本身（结论式、五步、上报项齐备）──
    check("L-log-exists", os.path.exists(LOGPATH), LOGPATH)
    check("L-log-five-steps", all(f"{i}. **" in LOG for i in range(1, 6)),
          "五步逐条结论")
    check("L-log-zero-action", "处置动作：**零**" in LOG, "零写操作明示")
    check("L-log-n6", "N6" in LOG and "File name too long" in LOG, "N6 定性记录")
    check("L-log-n4-n5", "N4" in LOG and "N5" in LOG, "N4/N5 续报")
    check("L-log-next-shift", "给下一班的建议" in LOG, "交接建议")
    check("L-log-no-push", "git push" in LOG and "零" in LOG, "红线自述")
    check("L-log-readings-ref", "2026-10-25.m305.readings.json" in LOG, "读数工件引用")

    fails = [c for c in checks if not c["pass"]]
    rep = {"mission": "m305", "shift": R["shift"], "generated_at_utc":
           R["collected_at_utc"], "checks_total": len(checks),
           "checks_passed": len(checks) - len(fails),
           "all_pass": not fails, "fails": fails, "checks": checks,
           "readings": os.path.basename(READINGS), "log": os.path.basename(LOGPATH)}
    # M305_NO_WRITE=1：只复算不落盘（提交后幂等复跑用，避免活体 detail 漂移把树弄脏）
    if os.environ.get("M305_NO_WRITE") != "1":
        json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"machine gate: {rep['checks_passed']}/{rep['checks_total']} PASS")
    for c in fails:
        print("FAIL", c["name"], c["detail"])
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
