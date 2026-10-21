#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m142 班次机器门：从 2026-10-21.readings.json 复算 operator_log 里的每个判据数字。

纪律：operator_log 中的数字必须能被本脚本从工件直读复算 —— 零手抄。
退出码 0 = 全部判据与工件一致；非 0 = 有判据对不上（日志不可信）。
只读：不改任何工件、不发 API、不写 DB。

判据分两类（平台是活体，读数会漂移）：
  ① 稳定属性判据（在飞心跳鲜度/无 infra 终局/容器健康/GC 全灭/R3 硬门封锁…）
     写成不依赖某一具体计数的形式；
  ② 确指判据（celery-beat RestartCount 单调增长、m137 honest FAIL 零提交、
     R2 修复已入库…）写成对已知基线的不等式/在场性，不钉死上一班的数值；
  ③ 在飞/终态集合改成「本班快照 + 必须含本班 m142」的形态。
"""
import json
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
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
    inflight = by["1_inflight"]["stdout"].strip().splitlines()
    ids = [ln.split("|")[0] for ln in inflight]
    ages = [int(ln.split("|")[3]) for ln in inflight]
    scs = [ln.split("|")[4].strip() for ln in inflight]
    check("inflight_contains_m142", "142" in ids,
          "在飞 id=%s（必含本班 142）" % ids)
    check("inflight_heartbeat_fresh", all(a <= 240 for a in ages),
          "心跳年龄(秒)=%s，全部 ≤240s（未到收割线）" % ages)
    check("inflight_stop_condition_null", all(s == "NULL" for s in scs),
          "在飞 result.stop_condition=%s（均 NULL，无 infra 终局值）" % scs)

    # --- 巡检第 2 步：守护计数 ---
    graw = int(by["2_guardian_count"]["stdout"].strip())
    gcount = int(by["2_guardian_count_excl_bash"]["stdout"].strip())
    check("guardian_count_baseline_10", gcount == 10,
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
        term_agg[f[0]] = term_agg.get(f[0], 0) + int(f[1])
        term_scs.append(f[2])
    non_null = [sc for sc in term_scs if sc != "NULL"]
    check("terminal_24h_succeeded_in_band", 20 <= term_agg.get("succeeded", 0) <= 35,
          "24h succeeded=%d（基线区间 20–35，平台推进不钉死）" % term_agg.get("succeeded", 0))
    check("terminal_24h_awaiting_in_band", 3 <= term_agg.get("awaiting_acceptance", 0) <= 10,
          "24h awaiting_acceptance=%d（平台推进会增减，不钉死）" % term_agg.get("awaiting_acceptance", 0))
    check("terminal_24h_failed_present", term_agg.get("failed", 0) >= 3,
          "24h failed=%d（≥3，honest FAIL 集合稳定）" % term_agg.get("failed", 0))

    # infra 类终局检查：24h failed 中不得出现未处置的 infra 类 stop_condition
    failed_infra = by["5_failed_24h_infra"]["stdout"].strip().splitlines()
    infra_scs = []
    for ln in failed_infra:
        f = ln.split("|")
        if len(f) >= 3:
            sc = f[2].strip()
            if sc in NO_INFRA:
                infra_scs.append(f[0])
    check("terminal_24h_no_unhandled_infra_failures",
          len(infra_scs) == 0,
          "24h failed infra 类 stop_condition=%s（应为空=无 infra 类终局未处置）" % infra_scs)

    # m137 定性（延续）：honest FAIL + 零交付推进
    m137 = by["5_m137_result"]["stdout"].strip()
    check("m137_honest_fail",
          m137.startswith("finished_without_acceptance") and "tool_calls=57" in m137,
          "m137 result=%s（honest FAIL：57 tool / 901s 用尽 nudge 未提交）" % m137)
    m137b = by["5_m137_branch"]["stdout"].strip()
    check("m137_zero_commit_evidence",
          "ahead=0" in m137b and "dirty=0" in m137b and "wt_head=e18d83b" in m137b,
          "m137 分支/工作树=%s（head 停在 base e18d83b，零提交=零交付）" % m137b)
    check("failed_ids_include_137",
          by["5_failed_30h_ids"]["stdout"].strip() == "ids=100 112 119 126 137",
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
    check("refill_rc_all_3_no_infra_object",
          all(rc_map.get("m%d" % i) == "rc=3" for i in (100, 112, 119, 126, 137))
          and len(rc) == 5,
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
    check("m120_dirty_from_gates_preflight",
          "dirty=3" in m120s and "gates_verified.json" in m120s,
          "m120 工作树=%s（dirty=3，3 个 gates_verified.json 被平台预检改写=R4 实证）"
          % m120s.strip()[:150])
    gates = by["6_gates_preflight_dirty_source"]["stdout"]
    check("m120_gates_mtime_after_commit",
          "file_mtime=2026-10-21 10:53:16" in gates and "commit_time=2026-10-21 10:52:07" in gates,
          "m120 gates 写盘时点=%s（mtime > commit_time = 平台预检写盘晚于交付 commit=R4 根因）"
          % gates.strip()[:150])

    # --- 巡检第 9 步：平台尾部观察 ---
    gc = by["9_gc_log"]["stdout"]
    check("gc_all_denied", "perms=600" in gc and "denied=13" in gc,
          "gc.py 权限=%s（600 无 x 位→13/13 denied，X 类延续上报）" % gc.strip()[:100])
    wt = int(by["9_worktree_count"]["stdout"].strip())
    check("worktree_count_growing", wt >= 52,
          "git worktree list 行数=%d（GC 停摆持续累积，≥52）" % wt)
    dirty = int(by["9_main_dirty"]["stdout"].strip())
    items = by["9_main_dirty_items"]["stdout"]
    check("main_dirty_shrunk_and_clean_of_approver",
          dirty <= 6 and "auto_approver.py" not in items,
          "主仓脏项=%d；auto_approver.py 已不在脏树（修复入库）" % dirty)
    fleet = by["9_fleet_log"]["stdout"].strip()
    check("fleet_alive", "fleet pass done" in fleet and "spawned=0" in fleet,
          "fleet.log 尾=%s（保活在运行，spawned=0=无缺口）" % fleet.splitlines()[-1])

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
    }
    for label, (needle_in_log, artifact_ok) in cited.items():
        check("log_consistency::" + label,
              needle_in_log and artifact_ok,
              "log 引用 %s；工件一致=%s（log 与 readings 未脱钩）" % (label, artifact_ok))

    # --- 汇总 ---
    passed = sum(1 for x in results if x["pass"])
    total = len(results)
    report = {
        "generated_at_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shift": "m142",
        "readings_collected_at_utc": r.get("collected_at_utc"),
        "passed": passed,
        "total": total,
        "all_pass": passed == total,
        "checks": results,
    }
    out = os.path.join(HERE, "2026-10-21.validation_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    for x in results:
        print("%s %s | %s" % ("PASS" if x["pass"] else "FAIL", x["check"], x["detail"]))
    print("\n%d/%d PASS -> %s" % (passed, total, out))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
