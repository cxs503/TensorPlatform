#!/usr/bin/env python3
"""verify_2026-10-24.m237.py — m237 operator 班次机器门（零手抄：断言全部直读 readings.json / DB / 工件）

用法（worktree 根）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a
  python3 operator_log/verify_2026-10-24.m237.py
RC=0 全 PASS；RC=1 有 FAIL；RC=2 fail closed（读数缺失）。
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READINGS = os.path.join(HERE, "2026-10-24.m237.readings.json")
LOGPATH = os.path.join(HERE, "2026-10-24.md")
REPORT = os.path.join(HERE, "2026-10-24.m237.validation_report.json")
ECO = "/srv/tensor/shared/tensor_eco"
SHIFT_START_UTC = "2026-10-24T08:13:02Z"
CANDIDATES = [208, 216, 217, 218, 221, 223, 226, 229, 230, 231]

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
    check("S1-self-included", 237 in s1["ids"], f"ids={s1['ids']}")
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

    # ── S3 中继健康 ──
    s3 = R["step3_relay"]
    check("S3-http-200", s3["http_code"] == 200, f"http={s3['http_code']}")
    check("S3-latency", (s3["time_total_s"] or 99) < 5, f"t={s3['time_total_s']}s")
    check("S3-models-7", s3["models"] == 7, f"models={s3['models']}")
    check("S3-single-endpoint", s3["endpoints_n"] == 1,
          f"endpoints={s3['endpoint_names']}")

    # ── S4 冷却态 ──
    s4 = R["step4_cooldown"]
    check("S4-no-active-cooldown", s4["active_now"] == {},
          f"active_now={s4['active_now']}")
    check("S4-state-empty", s4["raw"] == {}, f"raw={s4['raw']}")
    check("S4-action-none", "none" in s4["action"], s4["action"])

    # ── S5 终态 + R13 三段式 ──
    s5 = R["step5_terminal"]
    check("S5-h24-failed-10", s5["h24_failed"] == 10, f"h24_failed={s5['h24_failed']}")
    check("S5-since-shift-zero", s5["since_shift_failed"] == 0,
          f"since_shift={s5['since_shift_failed']}")
    check("S5-failed-by-stop",
          s5["h24_failed_by_stop"].get("gateway_unavailable") == 9
          and s5["h24_failed_by_stop"].get("budget_exceeded") == 1,
          s5["h24_failed_by_stop"])
    check("S5-awaiting-zero", s5["awaiting_acceptance"] == 0,
          f"awaiting={s5['awaiting_acceptance']}")
    # DB 活体复核：h24 failed 与 readings 一致
    live_h24 = psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                    "AND finished_at > now()-interval '24 hours'")[0][0]
    check("S5-h24-live-recheck", int(live_h24) == s5["h24_failed"],
          f"live={live_h24} readings={s5['h24_failed']}")
    # DB 活体复核：本班窗口 failed=0
    live_since = psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                      f"AND finished_at >= '{SHIFT_START_UTC}'")[0][0]
    check("S5-since-shift-live-recheck", int(live_since) == 0, f"live since={live_since}")

    # ── R13 三段式第③段 ──
    r13 = R["step5b_r13"]
    check("R13-events-today-count", len(r13["events_today"]) == 10,
          f"events_today={len(r13['events_today'])}")
    check("R13-newest-143132", r13["newest_local_ts"] == "2026-10-24T14:31:32",
          f"newest={r13['newest_local_ts']}")
    check("R13-window-empty", r13["events_since_shift"] == [],
          f"since_shift={r13['events_since_shift']}")
    check("R13-transient-window-empty", r13["transient_since_shift"] == [],
          f"transient={len(r13['transient_since_shift'])}")

    # ── S6 容器 ──
    s6 = R["step6_containers"]
    check("S6-all-healthy", s6["healthy"] == s6["total"] and s6["total"] == 11,
          f"healthy {s6['healthy']}/{s6['total']}")
    check("S6-none-unhealthy", s6["unhealthy"] == [], f"unhealthy={s6['unhealthy']}")

    # ── S7 refill 两关：10 条候选逐条 ──
    s7 = R["step7_refill_gate"]
    for i in CANDIDATES:
        k = f"m{i}"
        check(f"S7-{k}-rc0", s7["refill_checks"][k]["rc"] == 0,
              f"rc={s7['refill_checks'][k]['rc']}")
    for i in [208, 216, 217, 218, 221, 223, 229, 230, 231]:
        k = f"m{i}"
        check(f"S7-{k}-gate-blocked", "承接门挡下" in s7["verdicts"][k],
              s7["verdicts"][k])
    check("S7-m226-reported", "[上报]" in s7["verdicts"]["m226"], s7["verdicts"]["m226"])
    # 承接门活体复核：m229/m230/m231 确无「无更晚腿」误判
    for i in (229, 230, 231):
        rows = psql("SELECT id,status FROM tensor_dev_missions WHERE "
                    "left(goal,34)=left((SELECT goal FROM tensor_dev_missions WHERE id="
                    f"{i}),34) AND id>{i} ORDER BY id")
        has_later = any(r[1] in ("running", "claimed", "succeeded", "awaiting_acceptance")
                        for r in rows if int(r[0]) > i)
        check(f"S7-m{i}-later-leg-live", has_later,
              f"later={[(r[0], r[1]) for r in rows]}")
    # m226 确无更晚同前缀腿（否则应改判承接门）
    rows226 = psql("SELECT id,status FROM tensor_dev_missions WHERE "
                   "left(goal,34)=left((SELECT goal FROM tensor_dev_missions WHERE id=226)"
                   ",34) AND id>226 ORDER BY id")
    check("S7-m226-no-later-leg", rows226 == [], f"later={rows226}")

    # ── 审计面 ──
    a = R["audit"]
    check("A-approval-pending-zero", a["approval"]["pending"] == 0,
          f"pending={a['approval']['pending']}")
    check("A-approval-max-id", a["approval_max_id"] == 175, f"max_id={a['approval_max_id']}")
    check("A-parking-lot-empty", a["parking_lot"] == "{}", f"lot={a['parking_lot']}")
    check("R16-passall-marker-empty", a["pass_all"]["exists"]
          and a["pass_all"]["size_bytes"] == 0, f"size={a['pass_all']['size_bytes']}")
    check("R16-passall-rounds", a["pass_all"]["rounds"] > 0,
          f"rounds={a['pass_all']['rounds']}")
    check("X-gc-log-perm", "权限不够" in a["gc_log_last"], a["gc_log_last"])
    check("A-shift-switch-on", a["shift_switch_off"] is False, "OFF 未拉")
    check("A-worktree-branch", a["worktree_branch"] == "agent/m237",
          f"branch={a['worktree_branch']}")

    # ── 日志内容断言（结论式而非流水账：每步必须有结论关键词）──
    check("LOG-shift-id", "m237" in LOG and "第 86 班" in LOG, "m237 / 第86班")
    for kw in ["在飞心跳鲜度", "守护计数", "中继健康", "网关冷却态", "终态 mission result"]:
        check(f"LOG-step-{kw}", kw in LOG, kw)
    check("LOG-r13-three-segments", all(k in LOG for k in ["h24_failed", "since_shift_failed", "events_today"]),
          "R13 三段")
    check("LOG-report-section", "[上报]" in LOG, "[上报] 项在")
    check("LOG-next-shift-advice", "给下一班的建议" in LOG, "建议段在")
    check("LOG-m226", "m226" in LOG and "MP-DAMBREAK" in LOG, "m226 上报在")
    check("LOG-no-placeholder", "TODO" not in LOG and "TBD" not in LOG, "无占位符")
    # 部署/红线：本班零写操作声明
    check("LOG-zero-write", "零写操作" in LOG, "零写操作声明")

    total = len(checks)
    passed = sum(1 for c in checks if c["pass"])
    report = {
        "shift_mission_id": 237,
        "shift": "第86班",
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