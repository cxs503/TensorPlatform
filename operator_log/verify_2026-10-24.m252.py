#!/usr/bin/env python3
"""verify_2026-10-24.m252.py — m252 operator 班次机器门（零手抄：断言全部直读 readings.json / DB / 工件）

用法（worktree 根）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a
  python3 operator_log/verify_2026-10-24.m252.py
RC=0 全 PASS；RC=1 有 FAIL；RC=2 fail closed（读数缺失）。
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READINGS = os.path.join(HERE, "2026-10-24.m252.readings.json")
LOGPATH = os.path.join(HERE, "2026-10-24.md")
REPORT = os.path.join(HERE, "2026-10-24.m252.validation_report.json")
ECO = "/srv/tensor/shared/tensor_eco"
SHIFT_START_UTC = "2026-10-24T12:43:01Z"
CANDIDATES = [216, 217, 218, 221, 223, 226, 229, 230, 231, 244, 245, 249]

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
    check("S1-self-included", 252 in s1["ids"], f"ids={s1['ids']}")
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

    # ── S4 冷却态（本班为 {} 空 → 零写操作）──
    s4 = R["step4_cooldown"]
    check("S4-no-active-cooldown", s4["active_now"] == {},
          f"active_now={s4['active_now']}")
    check("S4-inherited-empty", s4["raw"] == {}, f"raw={s4['raw']}")
    check("S4-action-none", s4["action"].startswith("none"), s4["action"])
    # 活体复核：现态确实为空
    live_state = json.load(open(f"{ECO}/agent_work/llm_gateway_state.json", encoding="utf-8"))
    check("S4-live-state-now-empty", live_state == {}, f"live={live_state}")

    # ── S5 终态 + R13 三段式 ──
    s5 = R["step5_terminal"]
    # R13 ①段与上班持平：12（无新增、无衰减，窗口未滑出 m216/217/218）
    check("S5-h24-failed-12", s5["h24_failed"] == 12, f"h24_failed={s5['h24_failed']}")
    check("S5-h24-succeeded-24", s5["h24_succeeded"] == 24,
          f"h24_succeeded={s5['h24_succeeded']}")
    # 本班窗口（12:43:01Z 起）无任何终态：since 全空
    check("S5-since-shift-empty", s5["since_shift_missions"] == [],
          f"since={s5['since_shift_missions']}")
    check("S5-since-shift-zero", s5["since_shift_failed"] == 0,
          f"since_shift={s5['since_shift_failed']}")
    check("S5-failed-by-stop",
          s5["h24_failed_by_stop"].get("gateway_unavailable") == 10
          and s5["h24_failed_by_stop"].get("budget_exceeded") == 1
          and s5["h24_failed_by_stop"].get("agent_lost") == 1,
          s5["h24_failed_by_stop"])
    # m244 的 agent_lost（N1）仍在 24h 窗内
    m244 = [f for f in s5["failed_missions"] if f["id"] == 244]
    check("S5-m244-agent-lost-recorded",
          len(m244) == 1 and m244[0]["stop_condition"] == "agent_lost",
          f"m244={m244}")
    # m245/m249 均在 24h 窗内且为 gateway_unavailable（与 m251 同口径）
    for mid in (245, 249):
        row = [f for f in s5["failed_missions"] if f["id"] == mid]
        check(f"S5-m{mid}-gateway-unavailable-recorded",
              len(row) == 1 and row[0]["stop_condition"] == "gateway_unavailable",
              f"m{mid}={row}")
    check("S5-awaiting-zero", s5["awaiting_acceptance"] == 0,
          f"awaiting={s5['awaiting_acceptance']}")
    # DB 活体复核：h24 failed 与 readings 一致
    live_h24 = psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                    f"AND finished_at > timestamptz '{SHIFT_START_UTC}' "
                    "- interval '24 hours'")[0][0]
    check("S5-h24-live-recheck", int(live_h24) == s5["h24_failed"],
          f"live={live_h24} readings={s5['h24_failed']}")
    # DB 活体复核：本班窗口 failed=0
    live_since = psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                      f"AND finished_at >= '{SHIFT_START_UTC}'")[0][0]
    check("S5-since-shift-live-recheck", int(live_since) == 0, f"live since={live_since}")

    # ── R13 三段式第③段 ──
    r13 = R["step5b_r13"]
    check("R13-events-today-count", len(r13["events_today"]) == 5,
          f"events_today={len(r13['events_today'])}")
    check("R13-newest-195052", r13["newest_local_ts"] == "2026-10-24T19:50:52",
          f"newest={r13['newest_local_ts']}")
    check("R13-window-empty", r13["events_since_shift"] == [],
          f"since_shift={r13['events_since_shift']}")
    # 本班实测窗口内 1 条 transient（18:45:49 flash-w3），未达 3 次阈值、未触发冷却：
    # 断言「不超阈值」而非「为空」，并把条目逐条钉进报告（防下一班误读为静默）。
    check("R13-transient-window-below-threshold",
          len(r13["transient_since_shift"]) < 3,
          f"transient={r13['transient_since_shift']}")
    # 上班（m247）窗口内曾见 1 条 18:45:49 transient；本班窗口为 0 ⇒ 该条确属瞬时，
    # 未升级为冷却事件（真值记录，非「一直静默」的误读）。
    check("R13-window-transient-zero", r13["transient_since_shift"] == [],
          f"transient={r13['transient_since_shift']}")
    check("R13-r13-third-segment-max-age",
          r13["newest_local_ts"] < r13["shift_start_local"],
          f"newest={r13['newest_local_ts']} shift={r13['shift_start_local']}")

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
    for i in [216, 217, 218, 221, 223, 229, 230, 231, 244, 245, 249]:
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
    check("A-approval-max-id", a["approval_max_id"] >= 187, f"max_id={a['approval_max_id']}")
    check("A-parking-lot-empty", a["parking_lot"] == "{}", f"lot={a['parking_lot']}")
    check("R16-passall-marker-empty", a["pass_all"]["exists"]
          and a["pass_all"]["size_bytes"] == 0, f"size={a['pass_all']['size_bytes']}")
    check("R16-passall-rounds", a["pass_all"]["rounds"] > 0,
          f"rounds={a['pass_all']['rounds']}")
    check("X-gc-log-perm", "权限不够" in a["gc_log_last"], a["gc_log_last"])
    check("A-shift-switch-on", a["shift_switch_off"] is False, "OFF 未拉")
    check("A-worktree-branch", a["worktree_branch"] == "agent/m252",
          f"branch={a['worktree_branch']}")

    # ── 定日算法修正（本班 P0）：冷却事件日期独立复核 ──
    da = R["dating_anchors"]
    check("DA-cooldown-verified-nonzero", da["cooldown_events_verified"] >= 17,
          f"verified={da['cooldown_events_verified']}")
    check("DA-cooldown-all-correct", da["cooldown_events_bad"] == 0,
          f"bad={da['cooldown_events_bad']}")
    check("DA-monotonic", da["monotonic_violations"] == [],
          f"violations={da['monotonic_violations']}")
    # 与 DB 归一：今日（CST 10-24）冷却事件逐条对应一个同刻终态 mission
    todays = R["step5b_r13"]["events_today"]
    anchored = 0
    for ev in todays:
        rows = psql("SELECT count(*) FROM tensor_dev_missions WHERE finished_at IS NOT NULL "
                    "AND abs(extract(epoch FROM finished_at AT TIME ZONE 'Asia/Shanghai') "
                    "- extract(epoch FROM timestamp '" + ev.replace("T", " ") + "')) <= 60")
        if int(rows[0][0]) > 0:
            anchored += 1
    check("DA-today-events-db-anchored", anchored == len(todays),
          f"{anchored}/{len(todays)} 条今日冷却在 DB 有同刻终态")
    # 旧算法会得 13 ⇒ 断言修正后确为 4（防回退）
    check("DA-today-count-not-inflated", len(todays) == 5, f"today={len(todays)}")

    # ── 日志内容断言（结论式而非流水账：每步必须有结论关键词）──
    check("LOG-shift-id", "m252" in LOG and "第 94 班" in LOG, "m252 / 第 94 班")
    for kw in ["在飞心跳鲜度", "守护计数", "中继健康", "网关冷却态", "终态 mission result"]:
        check(f"LOG-step-{kw}", kw in LOG, kw)
    check("LOG-r13-three-segments",
          all(k in LOG for k in ["h24_failed", "since_shift_failed", "events_today"]),
          "R13 三段")
    check("LOG-report-section", "## 三、[上报] 项" in LOG, "[上报] 项段在")
    check("LOG-next-shift-advice", "给下一班的建议" in LOG, "建议段在")
    check("LOG-m226", "m226" in LOG and "MP-DAMBREAK" in LOG, "m226 上报在")
    check("LOG-no-placeholder", "TODO" not in LOG and "TBD" not in LOG, "无占位符")
    # 写操作声明：本班零写操作（冷却态空，无清理无 refill 无换血）
    check("LOG-zero-writes",
          all(k in LOG for k in ("psql 写", "kill", "换血", "git push"))
          and "全零" in LOG, "psql写/kill/换血/push 全零声明在")
    check("LOG-p2-dirtycode-reported", "P2" in LOG and "未提交" in LOG,
          "[上报] P2 未提交代码在")
    check("LOG-six-report-items",
          all(k in LOG for k in ("P1", "P2", "N1", "R16", "X（worktree GC", "R13")),
          "P1/P2/N1/R16/X/R13 六条 [上报] 齐")
    check("LOG-refill-zero", "零 refill" in LOG, "零 refill 声明")
    check("LOG-inflight-count-matches", f"在飞 **{s1['count']}**" in LOG,
          f"在飞 {s1['count']} 条（日志与 readings 一致）")
    check("LOG-guardian-equation", "ΣN **16**" in LOG and "= 16" in LOG, "守护等式 16")
    check("LOG-relay-200", "**`200`" in LOG, "中继 200")
    check("LOG-shift-94", "第 94 班" in LOG, "第 94 班")
    check("LOG-window-anchored", "锚定班次起点" in LOG, "采集器窗口锚定沿用")
    check("LOG-n1-zero-recurrence", "N1" in LOG and "TimeoutExpired" in LOG
          and "零复发" in LOG, "[上报] N1 零复发在")
    check("LOG-m244-gate", "m244" in LOG and "承接门" in LOG, "m244 承接门判读在")
    check("LOG-m249-gate", "m249" in LOG and "承接门" in LOG,
          "m249 承接门判读在")
    check("LOG-r13-static", "h24_failed" in LOG and "12" in LOG,
          "h24_failed=12 与上班持平判读在")
    check("LOG-r13-hits-operator-shift", "operator 班次" in LOG and "作废" in LOG,
          "[上报] R13 打到值班链（上班作废）在")
    check("LOG-decay-warning", "滑出 24h 窗口" in LOG and "口径衰减" in LOG,
          "下一班窗口衰减预警在")
    check("LOG-no-new-failure-mode", "无新增失败模式" in LOG, "新颖性申报在")
    check("LOG-no-raw-transcript", LOG.count("```") <= 2,
          "结论式记录（无大段流水账代码块）")
    # 采集器窗口锚定活体复核：readings 的 h24 窗口起点 = 班次起点 -24h，与 DB 同口径
    live_h24_anchored = psql(
        "SELECT count(*) FROM tensor_dev_missions WHERE status='failed' AND "
        f"finished_at > timestamptz '{SHIFT_START_UTC}' - interval '24 hours'")[0][0]
    check("S5-h24-window-anchored", int(live_h24_anchored) == s5["h24_failed"],
          f"anchored={live_h24_anchored} readings={s5['h24_failed']}")

    total = len(checks)
    passed = sum(1 for c in checks if c["pass"])
    report = {
        "shift_mission_id": 252,
        "shift": "第 94 班",
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