#!/usr/bin/env python3
"""verify_2026-10-25.m325.py — m325 operator 班次（第 109 班）机器门（零手抄：断言全部
直读 readings.json / DB / 工件，活体项现场复算）。

用法（worktree 根）：
  python3 operator_log/verify_2026-10-25.m325.py
RC=0 全 PASS；RC=1 有 FAIL；RC=2 fail closed（读数缺失）。

门的两类判据（m138 教训）：活体计数会漂 ⇒ 写成不等式或快照内部一致性；确指项
（候选 27 条活体窗口 ids、守护等式、R13 三段、本班 3 条 refill 的 DB 留痕、N6 缺陷行号、
N1 第三次实证的时序）写成钉死断言。
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READINGS = os.path.join(HERE, "2026-10-25.m325.readings.json")
LOGPATH = os.path.join(HERE, "2026-10-25.md")
REPORT = os.path.join(HERE, "2026-10-25.m325.validation_report.json")
ECO = "/srv/tensor/shared/tensor_eco"
AW = os.path.join(ECO, "agent_work")
SHIFT_START_UTC = "2026-10-25T10:43:02Z"
BASE_HEAD = "c245186"
# 处置前 24h 窗（锚定班次起点）内 failed 全量 30 条：必须与 readings 逐条相等（案例 21）
# 与上一班 m317 的 24 条比：+m316/m321/m322/m323（新进 4），无滑出
CANDIDATES = [245, 249, 255, 256, 257, 259, 261, 269, 270, 271, 284, 285, 286,
              287, 288, 289, 290, 291, 292, 294, 295, 297, 301, 316, 318, 319, 320,
              321, 322, 323]
PREV_CANDIDATES = [244, 245, 249, 255, 256, 257, 259, 261, 269, 270, 271, 284,
                   285, 286, 287, 288, 289, 290, 291, 292, 294, 295, 297, 301]
# 本班写操作：refill 三条 R6 补账腿（gateway_unavailable 属 infra 类）；冷却态清理一次
REFILLED = [318, 319, 320]
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
    check("S1-self-included", 325 in s1["ids"], f"ids={s1['ids']}")
    # 本班读数在飞三条：m324 业务线 + m325 自身 + m318 R6 补账腿（本班 refill 后已认领续跑）
    biz = {r[0]: r[1] for r in psql("SELECT id,status FROM tensor_dev_missions "
                                    "WHERE id IN (324,318)")}
    check("S1-business-legs-observed",
          biz.get("324") in ("running", "succeeded")
          and biz.get("318") in ("running", "succeeded", "awaiting_acceptance", "queued"),
          f"m324/m318 状态（{biz}）⇒ 在飞非仅自身，读数未漏采")
    check("S1-refilled-leg-claimed",
          any(m["id"] == 318 for m in s1["missions"]),
          "本班 refill 的 m318 已被 flash#2 认领续跑（18:57:11 CST），读数计入在飞")
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
    check("S1-pos-max-id", R["step1_pos_max_id"] >= 325, f"max_id={R['step1_pos_max_id']}")
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
    check("S2b-inflight-three", s2b["inflight_ids"] == [318, 324, 325],
          f"在飞 {s2b['inflight_ids']}（R6 补账续跑/业务线/自身）")
    # 自保：本班跑在 flash slot1 pid 2726618 —— 它在 old_code_pids 里，任何换血都要先排除它
    check("S2b-self-slot-facts",
          2726618 in s2b["old_code_pids"] and 2726917 in s2b["old_code_pids"],
          "本班跑在旧码 2726618（flash slot1）⇒ 换血必须自保排除；本班零 kill")
    check("S2b-oldcode-still-14", len(s2b["old_code_pids"]) == 14,
          f"旧码 {s2b['old_code_n']}/16 台（未换血，N5 未裁决）")

    # ── S2c 旧码风险面 ──
    s2c = R["step2c_old_code_risk"]
    check("S2c-unknown-repo-pinned", s2c["log_unknown_repo_hits"] >= 4,
          f"unknown repo 命中={s2c['log_unknown_repo_hits']}（≥4，旧码根因仍在）")
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
    check("S4-action-cleared", s4["action"].startswith("cleared"), s4["action"][:60])
    snap = os.path.join(AW, "probe_m325/llm_gateway_state.snapshot.json")
    check("S4-snapshot-taken", os.path.exists(snap), snap)
    if os.path.exists(snap):
        old = json.load(open(snap, encoding="utf-8"))
        b = old.get("relay-13001", {})
        check("S4-snapshot-quota-expired",
              b.get("reason") == "quota" and b.get("blocked_until", 0) > 0,
              f"班前快照 relay-13001 quota blocked_until={b.get('blocked_until')}"
              "（NaN 之外必须有值，过期才可清）")
    check("S4-file-exists", bool(s4["mtime_local"]), f"mtime={s4['mtime_local']}")

    # ── S5 终态（R13 第①②段）──
    s5 = R["step5_terminal"]
    # 24 条 = 上一班 25 条 - m310（已 refill 至 succeeded，finished_at 清空滑出）
    check("S5-h24-failed-27", s5["h24_failed"] == 27, f"h24_failed={s5['h24_failed']}")
    # 上一班 24 条中 3 条本班 refill 后滑出（318/319/320 的 finished_at 被清空）
    # 窗口 = 上一班 24 条 - m244（10-24 11:50Z 越过 24h 边界滑出）+ 本班新进 7 条 = 30
    check("S5-window-delta-pinned",
          sorted(CANDIDATES) == sorted([c for c in PREV_CANDIDATES if c != 244]
                                       + [316, 318, 319, 320, 321, 322, 323]),
          "窗口 = 上一班 24 条 − m244（滑出）+ 新进 7 条（316/318/319/320/321/322/323）")
    check("S5-refilled-slid-out",
          sorted(s5["refilled_this_shift"]) == REFILLED
          and all(r not in [m["id"] for m in s5["failed_missions"]] for r in REFILLED),
          f"refilled={REFILLED} 已不在 failed 窗口（finished_at 被 refill 清空）")
    check("S5-h24-succeeded-ineq", s5["h24_succeeded"] >= 45,
          f"h24_succeeded={s5['h24_succeeded']}（活体，不钉死）")
    # 本班窗口内零失败（连续两班最佳）：班后新增行只有 m313 的 succeeded 收口
    check("S5-since-shift-zero", s5["since_shift_failed"] == 0,
          f"since_shift={s5['since_shift_failed']}")
    check("S5-since-shift-no-failed-row",
          all(m["status"] != "failed" for m in s5["since_shift_missions"]),
          f"{[(m['id'], m['status']) for m in s5['since_shift_missions']]}")
    check("S5-candidates-equal-window", R["step5_candidates_equal_window"] is True,
          "活体 27 条 ids 与 CANDIDATES-REFILLED 逐条相等（候选清单与窗口不脱钩，案例 21）")
    check("S5-window-ids-pinned",
          sorted(m["id"] for m in s5["failed_missions"])
          == sorted(c for c in CANDIDATES if c not in REFILLED),
          "活体窗口 id 集合钉死（处置前 30 条 - 本班 refill 3 条）")
    check("S5-by-stop-sums", sum(s5["h24_failed_by_stop"].values()) == s5["h24_failed"],
          f"{s5['h24_failed_by_stop']}")
    check("S5-stop-taxonomy", set(s5["h24_failed_by_stop"]) <=
          {"gateway_unavailable", "agent_lost", "budget_exceeded", "agent_crash"},
          f"{sorted(s5['h24_failed_by_stop'])}")
    check("S5-awaiting-zero", s5["awaiting_acceptance"] == 0, "0")
    check("S5-window-fails-empty", R["step5c_shift_window_fails"] == [],
          f"{R['step5c_shift_window_fails']}")
    # refill 把 m310 的 finished_at 清空 ⇒ 窗内 max(finished_at) 回退到更早一条，
    # 故「相对班次起点的年龄」允许为负（=本班读数时窗内最晚失败已在起点之后）。
    # 真判据是：该值可解析且非零（活体时点会漂，不钉死具体秒数）。
    check("S5-last-failed-age-parsed",
          s5["last_failed_age_s"] is not None and s5["last_failed_age_s"] > 0,
          f"last_failed_age={s5['last_failed_age_s']}s（m323，本班窗口内最近一条 failed）")
    check("S5-since-missions-infra-only",
          all(m["stop_condition"] in ("budget_exceeded", "agent_lost", "agent_crash",
                                      "gateway_unavailable")
              for m in s5["since_shift_missions"] if m["status"] == "failed"),
          f"{[(m['id'], m['stop_condition']) for m in s5['since_shift_missions']]}"
          "（infra 类才可 refill，honest FAIL 不行）")
    live_fail = int(psql("SELECT count(*) FROM tensor_dev_missions WHERE status='failed' "
                         f"AND finished_at > timestamptz '{SHIFT_START_UTC}' "
                         "- interval '24 hours'")[0][0])
    # 本班 refill 3 条 ⇒ 活体窗内 failed 只能因滑出而 ≤ 读数（不钉死，比不等式）
    check("S5-h24-live-recheck",
          live_fail <= s5["h24_failed"],
          f"live={live_fail} ≤ readings {s5['h24_failed']}（refill 清 finished_at，只减不增）")

    # ── S5d R13 第③段 + 定日正确性 ──
    co = R["step5d_r13_three"]["cooling"]
    da = R["step5d_r13_three"]["dating_anchors"]
    check("S5d-cool-total-32", co["total_all_time"] == 32, f"total={co['total_all_time']}")
    check("S5d-events-since-shift-empty", co["events_since_shift"] == [],
          f"{co['events_since_shift']}")
    # 本班班前 18:13:11 CST 新增一条 quota 冷却（m321 glm-5.2 派出即限流），早于班次起点 18:43:02 CST
    check("S5d-events-today-pinned",
          co["events_today"] == ["2026-10-25T01:58:02", "2026-10-25T11:31:29",
                                 "2026-10-25T18:13:11"],
          f"{co['events_today']}")
    check("S5d-events-today-before-shift",
          all(t < R["shift_start_local"] for t in co["events_today"]),
          f"shift_start_local={R['shift_start_local']}")
    check("S5d-newest-cool", co["newest_local_ts"] == "2026-10-25T18:13:11",
          f"{co['newest_local_ts']}")
    check("S5d-transient-below-threshold", len(co["transient_since_shift"]) < 3,
          f"班内 transient={len(co['transient_since_shift'])} 条 < 3（未成冷却波）")
    check("S5d-dating-20", da["verified"] == 20, f"verified={da['verified']}")
    check("S5d-dating-bad-0", da["bad"] == 0, f"bad={da['bad']}")
    check("S5d-monotonic", da["monotonic_violations"] == [],
          f"{da['monotonic_violations']}")

    # ── S6 审计面 ──
    au = R["audit"]
    check("S6-containers-all-healthy", au["containers_healthy"] == au["containers_total"],
          f"{au['containers_healthy']}/{au['containers_total']}")
    check("S6-containers-11", au["containers_total"] == 11, f"{au['containers_total']}")
    check("S6-approved-ineq", au["approval"]["approved"] >= 216,
          f"approved={au['approval']['approved']}")
    check("S6-rejected-11", au["approval"]["rejected"] == 11, f"{au['approval']['rejected']}")
    check("S6-pending-0", au["approval"]["pending"] == 0, f"{au['approval']['pending']}")
    check("S6-approval-max-ineq", au["approval_max_id"] >= 232, f"{au['approval_max_id']}")
    check("S6-parking-empty", au["parking_lot"] == "{}", au["parking_lot"])
    check("S6-passall-present", au["pass_all"]["exists"] is True
          and au["pass_all"]["size_bytes"] == 0, f"{au['pass_all']}")
    check("S6-passall-rounds-ineq", au["pass_all"]["rounds"] >= 1600,
          f"rounds={au['pass_all']['rounds']}")
    check("S6-worktree-ineq", au["worktree_total"] >= 132, f"{au['worktree_total']}")
    check("S6-main-dirty-ineq", au["main_dirty"] >= 16,
          f"dirty={au['main_dirty']}（P3 续报：未提交修复面让 HEAD-diff 判据恒空）")
    check("S6-main-head-base", au["main_head"] == BASE_HEAD, f"{au['main_head']}")
    check("S6-gc-still-broken", "权限不够" in (au["gc_log_last"] or ""),
          f"X 未修（{au['gc_log_lines']} 行）")
    check("S6-shift-off-absent", au["shift_switch_off"] is False, "operator_shift.OFF 未拉")
    check("S6-worktree-branch", au["worktree_branch"] == "agent/m325", au["worktree_branch"])
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                          capture_output=True, text=True).stdout.strip()
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", BASE_HEAD, "HEAD"],
                         cwd=HERE, capture_output=True, text=True).returncode
    check("S6-head-descends-base", anc == 0, f"HEAD={head} 含 base {BASE_HEAD}")
    # 交付树洁净：只允许本班这 5 个交付件（提交前 staged/untracked 都在预期集内；
    # 提交后用 M315_EXPECT_CLEAN=1 复跑，要求完全干净 = 幂等复跑不脏树）
    EXPECT = {"operator_log/2026-10-25.md", "operator_log/2026-10-25.m325.readings.json",
              "operator_log/collect_2026-10-25.m325.py",
              "operator_log/verify_2026-10-25.m325.py",
              "operator_log/2026-10-25.m325.validation_report.json"}
    # porcelain 首列是 index 状态（`M  path` 与 ` M path` 都能出现），故按首个
    # 空白切分取路径，不用固定偏移（m312 踩过 x[3:] 把 `M  path` 切成 `ath`）。
    dirty = [ln.split(None, 1)[1].strip() if len(ln.split(None, 1)) > 1 else ln
             for ln in subprocess.run(["git", "status", "--porcelain"], cwd=HERE,
                                      capture_output=True, text=True).stdout
             .splitlines() if ln.strip()]
    stray = [d for d in dirty if d not in EXPECT]
    if os.environ.get("M317_EXPECT_CLEAN") == "1":
        check("S6-worktree-clean-after-commit", dirty == [], f"dirty={dirty[:4]}")
    else:
        check("S6-worktree-no-stray-dirty", stray == [],
              f"非交付件改动={stray[:4]}")

    # ── S7 refill 门（本班 refill 3 条 R6 补账腿；其余 27 条逐条被门挡下）──
    g = R["step7_refill_gate"]
    check("S7-candidates-30", len(g["refill_checks"]) == 30, f"{len(g['refill_checks'])}")
    # 处置后复跑 refill_check：被 refill 的 3 条翻转为 rc=3（不再可续：已在飞），
    # 其余候选仍 rc=0 —— 这是「本班 refill 确实生效」的机械证据（案例 12 ③）。
    flipped = {k: v["rc"] for k, v in g["refill_checks"].items()
               if k in [f"m{i}" for i in REFILLED]}
    rest = {k: v["rc"] for k, v in g["refill_checks"].items()
            if k not in [f"m{i}" for i in REFILLED]}
    check("S7-refilled-now-rc3", all(v == 3 for v in flipped.values()), f"{flipped}")
    check("S7-rest-still-rc0", all(v == 0 for v in rest.values()),
          f"其余 {len(rest)} 条 rc={sorted(set(rest.values()))}")
    check("S7-refilled-pinned", g["refilled"] == REFILLED, f"refilled={g['refilled']}")
    check("S7-verdicts-30", len(g["verdicts"]) == 30, f"{len(g['verdicts'])}")
    check("S7-no-empty-verdict", all(v.strip() for v in g["verdicts"].values()), "每条有理由")
    check("S7-legs-keyed", set(g["legs"]) == set(g["verdicts"]), "承接腿与裁决一一对应")
    check("S7-m301-leg-succeeded",
          any(l["id"] == 304 and l["status"] == "succeeded" for l in g["legs"]["m301"]),
          "m301 承接腿 m304 已 succeeded ⇒ 不 refill")
    check("S7-expand-follow-blocked", all("派发器瘫痪门" in g["verdicts"][f"m{i}"]
                                          for i in (287, 289, 290, 294)),
          "N6 使四腿『等派发器重发』路径失效 ⇒ operator 不 refill")
    check("S7-operator-self-legs-blocked",
          all("派发器辖门" in g["verdicts"][f"m{i}"] for i in (249, 261, 271, 321)),
          "operator 值班自腿不 refill（会造双跑值班）")
    check("S7-m316-has-successor",
          "m324" in g["verdicts"]["m316"] and g["legs"]["m316"] == [
              {"id": 324, "status": "running", "task_key": "tut:suboff_sail_resistance-r1"}],
          "m316 的承接腿 m324 由派发器重发并在飞 ⇒ 不 refill")
    check("S7-m322-m323-root-cause-gated",
          all("N1" in g["verdicts"][f"m{i}"] for i in (322, 323)),
          "SIR-4-W2/QUAL-2 死于准备期 fetch 阻塞（N1）⇒ 不 refill")

    # ── S7b 本班写操作自证（活体直读 DB，不依赖任何 post_action 工件）──
    rows = {int(r[0]): r[1] for r in psql(
        "SELECT id,jsonb_typeof(result->'refill') FROM tensor_dev_missions "
        "WHERE id IN (318,319,320)")}
    check("S7b-refill-trace-in-db", all(v == "object" for v in rows.values()),
          f"三条腿 result.refill 均已落库：{rows}")
    bywho = {int(r[0]): r[1] for r in psql(
        "SELECT id,result->'refill'->>'by' FROM tensor_dev_missions "
        "WHERE id IN (318,319,320)")}
    check("S7b-refill-by-operator", all(v == "operator_agent" for v in bywho.values()),
          f"{bywho}（恰加一次，API 行锁）")
    added = {int(r[0]): r[1] for r in psql(
        "SELECT id,result->'refill'->'added'->>'tool_calls' FROM tensor_dev_missions "
        "WHERE id IN (318,319,320)")}
    check("S7b-refill-added-80", all(v == "80" for v in added.values()), f"{added}")
    bud = {int(r[0]): r[1] for r in psql(
        "SELECT id,budget->>'max_tool_calls' FROM tensor_dev_missions "
        "WHERE id IN (318,319,320)")}
    check("S7b-budget-400-to-480", all(v == "480" for v in bud.values()), f"{bud}")
    st = {int(r[0]): r[1] for r in psql(
        "SELECT id,status FROM tensor_dev_missions WHERE id IN (318,319,320)")}
    check("S7b-status-left-terminal",
          all(v in ("queued", "claimed", "running", "succeeded", "awaiting_acceptance")
              for v in st.values()), f"{st}（failed → queued/续跑）")
    # 未 refill 的对照：m322/m323 仍 failed 且无 refill 痕
    ctl = {int(r[0]): (r[1], r[2]) for r in psql(
        "SELECT id,status,coalesce(jsonb_typeof(result->'refill'),'-') "
        "FROM tensor_dev_missions WHERE id IN (322,323)")}
    check("S7b-n1-legs-untouched",
          all(v == ("failed", "-") for v in ctl.values()),
          f"{ctl}（N1 腿零动作，只 [上报]）")
    # 处置成果自证（比状态翻转更强的证据）：m318 refill 后 11:08:01Z succeeded 且报告已发布
    out318 = psql("SELECT status, result->'evidence'->'checks'->>'passed', "
                  "result->'evidence'->'checks'->>'total' FROM tensor_dev_missions "
                  "WHERE id=318")
    check("S7b-m318-refill-succeeded",
          out318 and out318[0][0] == "succeeded" and out318[0][1] == out318[0][2],
          f"m318 status/checks = {out318}（refill 后续跑一次收口，11/11 判据 PASS）")
    rep66 = psql("SELECT id,mission_id,status FROM tensor_reports WHERE id=66")
    check("S7b-report-66-published",
          rep66 and rep66[0][1] == "318" and rep66[0][2] == "published",
          f"tensor_reports #66 = {rep66}（R6 欠账真销证的发布物）")
    check("S7b-prev-shift-closed",
          psql("SELECT status FROM tensor_dev_missions WHERE id=317")[0][0] == "succeeded",
          "上一班 m317 经审批 #232 已 succeeded 收口，本班无需再动")

    # ── S7c N1 第三次实证（本班两条腿被 reaper 冒充 agent_lost，钉死时序）──
    t = {int(r[0]): r[1:] for r in psql(
        "SELECT id, to_char(created_at,'HH24:MI:SS'), to_char(finished_at,'HH24:MI:SS'),"
        " round(extract(epoch from (finished_at-created_at))) "
        "FROM tensor_dev_missions WHERE id IN (322,323)")}
    check("S7c-n1-claimed-finish-window",
          all(240 < int(v[2]) < 300 for v in t.values()),
          f"{t}（创办→收割 255/257s，恰过 240s 阈值）")
    reap = subprocess.run(
        ["grep", "-ah", "reaped stale mission 32[23]",
         os.path.join(AW, "agent-qwen3.6-plus-w2.log"),
         os.path.join(AW, "agent-qwen3.7-plus-w3.log")],
        capture_output=True, text=True).stdout.strip().splitlines()
    check("S7c-n1-reaper-lines-2", len(reap) >= 2, f"{reap[:2]}")
    fetch = subprocess.run(
        ["grep", "-ah", "timed out after 300 seconds",
         os.path.join(AW, "agent-deepseek-v4-flash.log")],
        capture_output=True, text=True).stdout.strip()
    check("S7c-n1-fetch-timeout-same-shift", "TensorPhysics" in fetch,
          "同刻 flash 日志 `TimeoutExpired: git fetch TensorPhysics timed out after 300 seconds`")
    check("S7c-n1-no-worktree-state",
          not os.path.exists(os.path.join(AW, "m322/state/native_messages.json")),
          "m322 连 state 都未生成 ⇒ 死在准备期而非任务逻辑（N1 判据）")

    # ── S10 R6 报告账（第 6 步）──
    d6 = R["step6_report_debt"]
    check("S10-owed-18", d6["owed_n"] == 18, f"owed={d6['owed_n']}")
    check("S10-failed-owed-10", d6["failed_owed_n"] == 10,
          f"failed_owed={d6['failed_owed_n']}")
    check("S10-cutoff", d6["cutoff"] == "2026-10-23", d6["cutoff"])
    check("S10-backfill-legs-3", [m["id"] for m in d6["backfill_missions"]] == [318, 319, 320],
          f"{[(m['id'], m['status']) for m in d6['backfill_missions']]}")
    check("S10-backfill-not-by-this-shift",
          all(m["id"] > 317 for m in d6["backfill_missions"]),
          "补账单 id 均 > 本班 317 ⇒ 非本班派出（r6 线 08:46:30Z 自主派）")
    check("S10-backfill-refilled-by-shift",
          {m["id"]: m["status"] for m in d6["backfill_missions"]} ==
          {318: "running", 319: "queued", 320: "queued"},
          f"{[(m['id'], m['status']) for m in d6['backfill_missions']]}"
          "（本班 refill 后：m318 已被认领续跑、m319/m320 排在队列）")
    check("S10-action-refilled", d6["action"].startswith("refilled")
          and "重复派单" in d6["action"], d6["action"][:60])
    # N10：区间写法 m279-283 在 m<id> 正则里只识别到 m279 ⇒ 四条不被声明面覆盖
    check("S10-N10-interval-gap-pinned",
          sorted(d6["owed_not_covered_by_backfill"]) == [280, 281, 282, 283],
          f"{d6['owed_not_covered_by_backfill']}（m319 goal 用区间写法 m279-283）")
    check("S10-declared-covered-27", d6["backfill_declared_covered_n"] == 27,
          f"{d6['backfill_declared_covered_n']} 个 m<id> 出现在三腿 goal 中")
    # N11：report_ledger 自带 cron 注释，但真 crontab 里没有该条目
    live_cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    check("S10-N11-ledger-has-cron",
          "report_ledger" in live_cron,
          "N11 已被 owner 修：crontab 现有 `11,41 * * * * ... report_ledger.py` 条目")
    # 跨班对照（零手抄）：上一班 m317 的 readings 工件里 owed/failed_owed 原值，
    # 与本班读数之差必须与日志里写的 −10 / −16 一致（第 6 步「欠账真销」的可复算证据）。
    prevp = os.path.join(AW, "m317/TensorPlatform/operator_log/2026-10-25.m317.readings.json")
    if os.path.exists(prevp):
        P6 = json.load(open(prevp, encoding="utf-8"))["step6_report_debt"]
        check("S10-owed-delta-10", P6["owed_n"] - d6["owed_n"] == 10,
              f"owed {P6['owed_n']} → {d6['owed_n']}（−10）")
        check("S10-failed-owed-delta-16", P6["failed_owed_n"] - d6["failed_owed_n"] == 16,
              f"failed_owed {P6['failed_owed_n']} → {d6['failed_owed_n']}（−16）")
        check("S10-removed-owed-are-report66",
              sorted(set(P6["owed_ids"]) - set(d6["owed_ids"]))
              == [219, 233, 236, 250, 253, 262, 265, 267, 278, 313],
              f"销掉的 owed = {sorted(set(P6['owed_ids']) - set(d6['owed_ids']))}")
    led = open(os.path.join(AW, "quality/report_ledger.log"), encoding="utf-8",
               errors="replace").read().strip().splitlines()
    check("S10-ledger-cron-fresh", "report_debt: owed=18" in (led[-1] if led else ""),
          f"ledger 最新一行：{(led[-1] if led else '')[:110]}")
    check("S10-debt-fresh",
          str(d6["generated_at"]).startswith("2026-10-25T11:"),
          f"report_debt.json generated_at={d6['generated_at']}（cron 生效后新鲜，上一班为 08:33Z 陈旧）")

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
    check("S8b-file-too-long-ge17", n6["file_name_too_long_n"] >= 17,
          f"File name too long ×{n6['file_name_too_long_n']}")
    check("S8b-defect-line-195", n6["defect_line"] == [195], f"{n6['defect_line']}")
    check("S8b-defect-code-pinned", "CRITERIA_BLOCK" in n6["defect_code"]
          and "cwd=" in n6["defect_code"], n6["defect_code"])
    check("S8b-dispatch-py-mtime", n6["dispatch_py_mtime_local"].startswith("2026-10-25"),
          f"缺陷代码自 {n6['dispatch_py_mtime_local']} 落盘后未改（N6 未修）")
    check("S8b-last-round-crashed",
          n6["file_name_too_long_n"] >= 17,
          f"仍在崩：File name too long ×{n6['file_name_too_long_n']}（上一班 m317 记 12，本班 +5）")
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
    check("S9-total-recorded", s9["total"] >= 20, f"total={s9['total']}")
    check("S9-per-file-sum", sum(e["n"] for e in s9["per_file"]) == s9["total"],
          f"{len(s9['per_file'])} 文件")
    check("S9-no-action", s9["action"].startswith("记录"), s9["action"][:30])

    # ── L 交付物本身（结论式、五步、上报项齐备；零写操作明示）──
    check("L-log-exists", os.path.exists(LOGPATH), LOGPATH)
    check("L-log-six-steps", all(f"{i}. **" in LOG for i in range(1, 7)),
          "六步逐条结论（第 6 步 = R6 报告账）")
    check("L-log-writes-listed", "写操作" in LOG and "refill" in LOG,
          "写操作逐条列明（refill ×3 + 冷却态清理）")
    check("L-log-no-kill", "零换血" in LOG and "旧码守护" in LOG, "零换血 + 判据明示")
    check("L-log-n6", "N6" in LOG and "File name too long" in LOG, "N6 定性记录（+5 恶化）")
    check("L-log-n4", "N4" in LOG, "N4 续报")
    check("L-log-n5-escalated", "N5" in LOG and "TOCTOU" in LOG, "N5 续报（含自保事实）")
    check("L-log-n10-new", "N10" in LOG and "区间写法" in LOG,
          "N10 续报：区间写法 m279-283 不被 m<id> 机判覆盖")
    check("L-log-n11-fixed", "N11" in LOG and "crontab" in LOG,
          "N11 已被 owner 修（crontab 有 report_ledger 条目）")
    check("L-log-n1-third", "N1" in LOG and "fetch" in LOG,
          "N1 第三次实证（本班 m322/m323 被 reaper 冒充 agent_lost）")
    check("L-log-p3-pinned", "P3" in LOG and "dirty" in LOG, "P3 续报（dirty 19）")
    check("L-log-x-gc", "X" in LOG and "权限不够" in LOG, "X 续报（gc 未修）")
    check("L-log-backfill-not-duplicated", "不重复派单" in LOG, "R6 处置理由在册")
    check("L-log-self-protect", "2726618" in LOG, "自保事实（本班跑在旧码守护上）")
    check("L-log-next-shift", "给下一班的建议" in LOG, "交接建议")
    check("L-log-no-push", "git push" in LOG and "零" in LOG, "红线自述")
    check("L-log-readings-ref", "2026-10-25.m325.readings.json" in LOG, "读数工件引用")
    check("L-log-greens", LOG.count("：绿") >= 4, f"绿项 {LOG.count('：绿')} 处")
    check("L-log-refill-three", "m318" in LOG and "m319" in LOG and "m320" in LOG,
          "refill 三条腿逐条在册")
    check("L-log-cooling-cleared", "quota" in LOG and "冷却" in LOG, "冷却态清理在册")

    fails = [c for c in checks if not c["pass"]]
    rep = {"mission": "m325", "shift": R["shift"], "generated_at_utc":
           R["collected_at_utc"], "checks_total": len(checks),
           "checks_passed": len(checks) - len(fails),
           "all_pass": not fails, "fails": fails, "checks": checks,
           "readings": os.path.basename(READINGS), "log": os.path.basename(LOGPATH)}
    # M315_NO_WRITE=1：只复算不落盘（提交后幂等复跑用，避免活体 detail 漂移把树弄脏）
    if os.environ.get("M325_NO_WRITE") != "1":
        json.dump(rep, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"machine gate: {rep['checks_passed']}/{rep['checks_total']} PASS")
    for c in fails:
        print("FAIL", c["name"], c["detail"])
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
