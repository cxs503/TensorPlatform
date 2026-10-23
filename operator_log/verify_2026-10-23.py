#!/usr/bin/env python3
"""operator 班次 m222（2026-10-23 第 79 班）机器门——从 readings 复算 log 里每个数字。

判据：log 里出现的每个计数/等值断言，必须能从 `2026-10-23.readings.json` 复算出来。
幂等写（playbook 案例 13 ⑤）：剔除 generated_at_utc 后内容未变则不重写，
故提交后复跑不会把树弄脏。

用法：python3 operator_log/verify_2026-10-23.py   → rc=0 全 PASS
"""
from __future__ import annotations

import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
READINGS = HERE / "2026-10-23.readings.json"
REPORT = HERE / "2026-10-23.validation_report.json"
PASS: list[str] = []
FAIL: list[str] = []


def ck(name: str, cond: bool, detail: str) -> None:
    (PASS if cond else FAIL).append(f"{name}: {detail}")


def main() -> int:
    R = json.loads(READINGS.read_text(encoding="utf-8"))
    s1, s2, s3 = R["step1_inflight"], R["step2_guardian"], R["step3_relay"]
    s4, s5, s5b, s6 = R["step4_cooldown"], R["step5_terminal"], R["step5b_cluster"], R["step6_containers"]
    A = R["audit"]

    # ── 步骤 1：在飞 + 心跳鲜度 ──
    ck("S1-count", s1["count"] == len(s1["missions"]) == 3,
       f"在飞 {s1['count']} 条，明细 {len(s1['missions'])} 行一致"
       f"（{','.join('m%d' % m['id'] for m in s1['missions'])}）")
    ck("S1-fresh", s1["max_hb_s"] < 240 and not s1["stale_over_240s"],
       f"最大心跳 {s1['max_hb_s']}s ≪ 240s 收割线，stale={s1['stale_over_240s']}")
    ck("S1-nostop", not s1["with_stop_condition"],
       f"在飞无 stop_condition：{s1['with_stop_condition']}")

    # ── 步骤 2：守护计数等式 ──
    ck("S2-equation",
       s2["measured_total"] == s2["authority_sum"] and s2["per_model_all_equal"],
       f"实测 {s2['measured_total']} = 权威源 ΣN {s2['authority_sum']}，"
       f"六模型逐项相等={s2['per_model_all_equal']}（源 {s2['authority_source']}）")
    ck("S2-slot", s2["slot_workers"] == "10",
       f"带 --slot 追加槽 worker={s2['slot_workers']} 台；"
       f"pgrep 口径 {s2['pgrep_count']}（口径差异见 log 说明）")

    # ── 步骤 3：中继 ──
    ck("S3-relay", s3["http_code"] == 200 and s3["time_total_s"] < 5
       and s3["models"] == 7,
       f"GET /models → {s3['http_code']} / {s3['time_total_s']:.3f}s / "
       f"models={s3['models']}（门限 200 且 <5s）")
    ck("S3-single-endpoint", s3["endpoints_n"] == 1,
       f"端点池 {s3['endpoints_n']} 个 {s3['endpoint_names']}（R13 结构性风险仍在）；"
       f"活体 cooldown_seconds={s3['cooldown_seconds']}、"
       f"transient_threshold={s3['transient_threshold']}（源 {s3['config_source']}），"
       f"源码 min(cooldown,600) 生效={s3['code_caps_600']}")
    ck("S3-r17", s3["worktree_copy_stale"] is True,
       f"R17 仍在：worktree 副本 cooldown={s3['worktree_copy_cooldown_seconds']} "
       f"≠ 主仓活体 {s3['cooldown_seconds']} ⇒ 端点配置判读只读主仓")

    # ── 步骤 4：冷却态 ──
    ck("S4-empty", s4["raw"] == {} and not s4["now_active"],
       f"llm_gateway_state.json={json.dumps(s4['raw'])}，active={s4['now_active']}"
       f"（无过期项待清，本班零写操作）")

    # ── 步骤 5：终态 ──
    fm = s5["failed_missions"]
    ck("S5-24h", s5["h24_failed"] == len(fm) == 4 and s5["today_failed"] == 4,
       f"24h failed={s5['h24_failed']} = 明细 {len(fm)} 条 = 今日新增 "
       f"{s5['today_failed']}（succeeded={s5['h24_succeeded']}）")
    ck("S5-all-gw", all(m["stop_condition"] == "gateway_unavailable" for m in fm),
       "failed 全 stop_condition=gateway_unavailable（R13 同型）："
       + ",".join("m%d@%s" % (m["id"], m["finished"]) for m in fm))
    ck("S5-no-new", s5["since_shift_failed"] == 0,
       f"本班窗口（>={R['shift_start_utc']}）新增 failed={s5['since_shift_failed']} "
       f"⇒ R13 本班零新增；末条 failed 距今 {s5['last_failed_age_s']}s"
       f"（≈{s5['last_failed_age_s'] / 3600:.2f}h）")
    # 两条 SQL 非同一事务，秒级抖动容差 ±3s（不是手抄误差，是时点差）
    ck("S5-lastfail", abs(s5["last_failed_age_s"] - min(m["age_s"] for m in fm)) <= 3,
       f"末条 failed 距今 {s5['last_failed_age_s']}s ≈ 明细最小 age "
       f"{min(m['age_s'] for m in fm)}s（两查询时点差 ≤3s，口径自洽）")
    ck("S5-awaiting", s5["awaiting_acceptance"] == 0,
       f"awaiting_acceptance={s5['awaiting_acceptance']}（R16 屏蔽，勿解读为验收链健康）")

    # ── 步骤 5b：R13 群杀窗口 + 日志分日坑 ──
    c = s5b["cooling"]
    ck("S5b-newest",
       c["newest_local_ts"] == "2026-10-23T21:13:16",
       f"今日最新冷却事件 {c['newest_local_ts']}（CST）= UTC 13:13:16，"
       f"与 m220 记录的同一波重合 ⇒ 距今 "
       f"~{s5['last_failed_age_s'] / 3600:.1f}h 零新增（以末条 failed age 为口径）；"
       f"冷却行累计 {c['total']} 条、无可用端点 {s5b['no_endpoint_lines']} 条")
    ck("S5b-stale-excluded",
       "agent-qwen3.7-plus.log" in c["stale_files_excluded"],
       "日志分日坑已挡："
       + ",".join(c["stale_files_excluded"])
       + " 的 mtime 非今日，其 [22:52]/[23:36] 行属昨日，未计入今日判据")

    # ── 承接门（不 refill 的机械理由）──
    legs208 = {x["id"]: x["status"] for x in s5["goal_208_legs"]}
    legs216 = {x["id"]: x["status"] for x in s5["goal_216_legs"]}
    legs217 = {x["id"]: x["status"] for x in s5["goal_217_legs"]}
    ck("P-legs-208", legs208.get(221) == "running" and legs208.get(208) == "failed",
       f"cylinder 线腿 {legs208} ⇒ m208/m216 均有更晚承接腿 m221(running)，"
       "再加 refill = 同 goal 双跑")
    ck("P-legs-216", legs216.get(221) == "running",
       f"cylinder 线（m216 前缀）腿 {legs216} ⇒ 承接腿 m221(running)")
    ck("P-legs-217", legs217.get(219) == "succeeded",
       f"tutorial 线腿 {legs217} ⇒ 已被 m219 **succeeded** 承接（成果交付，重派无意义）")
    ck("P-refillcheck",
       [x["rc"] for x in s5["refill_checks"]] == [0, 0, 0, 0],
       "refill_check 四腿 rc=0（单用会误导，须叠加承接门）："
       + ",".join("m%d" % x["id"] for x in s5["refill_checks"]))

    # ── 处置面：本班零写操作 ──
    ck("D-zero-write",
       [x["by"] for x in s5["refill_audit"]] == ["operator_agent"]
       and [x["id"] for x in s5["refill_audit"]] == [211],
       f"refill 审计面仅 m211（上一班 m220 所为，{s5['refill_audit'][0]['added']}），"
       "本班零 refill / 零冷却态写 / 零 kill / 零换血 / 零 psql 写")

    # ── 步骤 6：容器 ──
    ck("S6-containers", s6["healthy"] == s6["total"] >= 11,
       f"容器 {s6['healthy']}/{s6['total']} healthy，非健康 {s6['unhealthy']}")

    # ── 审计面 ──
    ck("A1-approval",
       A["approval"]["pending"] == 0 and A["approval"]["approved"] == 149,
       f"审批 approved={A['approval']['approved']} / rejected="
       f"{A['approval']['rejected']} / pending={A['approval']['pending']}；"
       f"停车表={A['parking_lot']}")
    ck("A2-passall", A["pass_all"]["exists"] and A["pass_all"]["rounds"] >= 451,
       f"R16 仍在：PASS_ALL 开关存在（mtime {A['pass_all']['mtime_local']}），"
       f"累计 {A['pass_all']['rounds']} 轮，末条 `{A['pass_all']['last_line']}`")
    ck("A3-worktree",
       A["worktree_total"] == 95 and A["worktree_tensorplatform"] == 91,
       f"worktree {A['worktree_total']} 条（其中 /TensorPlatform "
       f"{A['worktree_tensorplatform']} 条，另 4 条非本仓）；"
       f"主仓 dirty={A['main_dirty']}（{A['main_branch']} @{A['main_head']}）")
    ck("A4-thisbranch", A["worktree_branch"] == "agent/m222",
       f"本站分支 {A['worktree_branch']}，HEAD {A['worktree_head']}，"
       f"交付前 dirty={A['worktree_dirty_before_delivery']}")
    ck("A5-gc", "权限不够" in A["gc_log_last"],
       f"X 延续：gc.log 末行 `{A['gc_log_last']}` ⇒ worktree GC 仍全灭")

    report = {
        "mission": "m222",
        "shift": "第79班（派发器标注见 goal）",
        "date": "2026-10-23",
        "readings": READINGS.name,
        "collected_at_utc": R["collected_at_utc"],
        "evidence_sources": [
            "open_digital_foundation.tensor_dev_missions（只读 SQL）",
            "open_digital_foundation.tensor_approval_requests（只读 SQL）",
            "agent_work/workers.json（守护权威源）+ ps -eo args 实测",
            "agent_work/llm_gateway_state.json",
            "agent_work/auto_approver.log + auto_approver.PASS_ALL + approver_escalated.json",
            "agent_work/agent-*.log（冷却事件原文，按 mtime 分日）",
            "tools/agent/llm_endpoints.json + llm_gateway.py（主仓活体，非 worktree 副本）",
            "curl GET /models（bash 终端直连，凭据不进 python 子进程）",
        ],
        "dispositions": [],
        "not_disposed": [
            {"id": 208, "why": "cylinder 线同 goal 已被 m221(running) 承接（承接门不过）"},
            {"id": 216, "why": "同上"},
            {"id": 217, "why": "tutorial 线同 goal 已被 m219(succeeded) 承接"},
            {"id": 218, "why": "operator 班次同 goal，本班 m222 即承接腿"},
        ],
        "checks": {"passed": PASS, "failed": FAIL},
        "checks_total": len(PASS) + len(FAIL),
        "checks_passed": len(PASS),
    }

    def canon(d: dict) -> str:
        d = {k: v for k, v in d.items() if k != "generated_at_utc"}
        return json.dumps(d, ensure_ascii=False, sort_keys=True)

    old = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else None
    if old is None or canon(old) != canon(report):
        report["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        print("report rewritten")
    else:
        print("report unchanged (idempotent)")

    for x in PASS:
        print("PASS", x)
    for x in FAIL:
        print("FAIL", x)
    print(f"checks {len(PASS)}/{len(PASS) + len(FAIL)} PASS")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())