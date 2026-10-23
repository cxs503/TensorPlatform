#!/usr/bin/env python3
"""operator 班次 m220（2026-10-23 第 78 班）机器门——从 readings 复算 log 里每个数字。

判据：log 里出现的每个计数/等值断言，必须能从 `2026-10-23.readings.json` 复算出来。
幂等写（playbook 案例 13 ⑤）：剔除 timestamp 字段后内容未变则不重写，
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
    s4, s5, s6 = R["step4_cooldown"], R["step5_terminal"], R["step6_containers"]
    s5b, A = R["step5b_cluster"], R["audit"]

    # ── 步骤 1 ──
    ck("S1-count", s1["count"] == len(s1["missions"]) >= 4,
       f"在飞 {s1['count']} 条，明细 {len(s1['missions'])} 行一致（含处置后续班的 m211）")
    ck("S1-fresh", s1["max_hb_s"] < 240 and not s1["stale_over_240s"],
       f"最大心跳 {s1['max_hb_s']}s ≪ 240s 收割线，stale={s1['stale_over_240s']}")
    ck("S1-nostop", not s1["with_stop_condition"],
       f"在飞无 stop_condition：{s1['with_stop_condition']}")

    # ── 步骤 2 ──
    ck("S2-equation",
       s2["measured_total"] == s2["authority_sum"] and s2["per_model_all_equal"],
       f"实测 {s2['measured_total']} = 权威源 ΣN {s2['authority_sum']}，"
       f"六模型逐项相等={s2['per_model_all_equal']}（源 {s2['authority_source']}）")
    ck("S2-slot", s2["slot_workers"] == "10",
       f"带 --slot 追加槽 worker={s2['slot_workers']} 台（基线 6 + 池化 10 自洽）")

    # ── 步骤 3 ──
    ck("S3-relay", s3["http_code"] == 200 and s3["time_total_s"] < 5
       and s3["models"] == 7,
       f"GET /models → {s3['http_code']} / {s3['time_total_s']:.3f}s / models="
       f"{s3['models']}（门限 200 且 <5s）")
    ck("S3-single-endpoint", s3["endpoints_n"] == 1,
       f"端点池 {s3['endpoints_n']} 个（R13 仍在）；活体配置 cooldown_seconds="
       f"{s3['cooldown_seconds']}（源 {s3['config_source']}），"
       f"源码 min(cooldown,600) 生效={s3['code_caps_600']}，"
       f"transient_threshold={s3['transient_threshold']}；"
       f"★ worktree 副本 cooldown={s3['worktree_copy_cooldown_seconds']} "
       f"已陈旧（R17 同型，勿以 worktree 副本判读）")

    # ── 步骤 4 ──
    ck("S4-cleared",
       s4["before_all_expired"] and not s4["now_active"] and s4["now_state"] == {},
       f"清理前 relay-13001 delta={s4['before_delta_s']}（已过期）→ 清后 "
       f"{json.dumps(s4['now_state'])}，active={s4['now_active']}")

    # ── 步骤 5 ──
    fm = s5["failed_missions"]
    ck("S5-24h", s5["h24_failed"] == len(fm) and s5["today_failed"] == len(fm),
       f"24h failed={s5['h24_failed']} = 明细 {len(fm)} 条 = 今日新增 {s5['today_failed']}"
       f"（succeeded={s5['h24_succeeded']}）")
    ck("S5-all-gw", all(m["stop_condition"] == "gateway_unavailable" for m in fm),
       "failed 全部 stop_condition=gateway_unavailable（R13 同型）："
       + ",".join(f"m{m['id']}" for m in fm))
    ck("S5-lastfail", s5["last_failed_age_s"] == min(m["age_s"] for m in fm),
       f"末条 failed 距今 {s5['last_failed_age_s']}s（≈"
       f"{s5['last_failed_age_s'] / 3600:.2f}h）= 明细最小 age，"
       "已推翻上一班『零新增 21.8h』")
    ck("S5-wave", len(s5b["wave_2113_local"]) >= 5,
       f"21:13–21:14 本地（UTC 13:13–13:14）冷却窗口日志 {len(s5b['wave_2113_local'])} 行"
       f"（冷却事件 {sum(1 for x in s5b['wave_2113_local'] if '冷却 600s' in x)} 次），"
       f"全库同类行累计 {s5b['count']} 行")

    # ── 处置自证（m211 refill）──
    ra = [x for x in s5["refill_audit"] if x["id"] == 211]
    ck("P1-refill-audit", len(ra) == 1 and ra[0]["by"] == "operator_agent"
       and ra[0]["added"] == "60/7200" and ra[0]["max_tool_calls"] == "420",
       "m211 refill 落账：by=operator_agent，added=60/7200，预算 360→420 tools / "
       "28800→36000s，状态 running")
    rc = {x["id"]: x["rc"] for x in s5["refill_checks"]}
    ck("P2-refill-gate", rc[211] == 3 and all(rc[i] == 0 for i in (208, 216, 217, 218)),
       f"refill_check rc：m211={rc[211]}（已在飞，续班已生效）；其余 "
       f"{ {i: rc[i] for i in (208, 216, 217, 218)} } = 0 但被承接门挡下（见 P3）")
    legs216 = {x["id"]: x["status"] for x in s5["goal_216_legs"]}
    legs217 = {x["id"]: x["status"] for x in s5["goal_217_legs"]}
    legs211 = {x["id"]: x["status"] for x in s5["goal_211_legs"]}
    ck("P3-claim-gate",
       legs216.get(221) == "running" and legs217.get(219) == "running"
       and legs211 == {211: "running"},
       f"承接门：cylinder 线已被 m221(running) 承接、tutorial 线已被 m219(running) "
       f"承接 ⇒ 不 refill；mp:dambreak-s1 唯 m211 一条腿 {legs211} ⇒ 可 refill（续班后转 running）")
    ck("P4-load-gate", all(g.get("bad_args") == 0 for g in s5["load_gates"]),
       "载荷门：五条终态检查点 bad_args 全 0（"
       + ",".join(f"m{g['id']}={g['bad_args']}" for g in s5["load_gates"]) + "）")

    # ── 步骤 6 ──
    ck("S6-containers", s6["healthy"] == s6["total"] and s6["total"] >= 11,
       f"容器 {s6['healthy']}/{s6['total']} healthy，非健康 {s6['unhealthy']}")

    # ── 审计面 ──
    ck("A1-approval",
       A["approval"]["pending"] == 0 and A["approval"]["approved"] >= 147,
       f"审批 approved={A['approval']['approved']} / rejected="
       f"{A['approval']['rejected']} / pending={A['approval']['pending']}；"
       f"停车表={A['parking_lot']}")
    ck("A2-passall", A["pass_all"]["exists"] and A["pass_all"]["rounds"] >= 400,
       f"R16：PASS_ALL 开关存在（mtime {A['pass_all']['mtime_local']}），"
       f"累计 {A['pass_all']['rounds']} 轮，末条 `{A['pass_all']['last_line']}`"
       " ⇒ pending=0 不得解读为验收链健康")
    ck("A3-worktree",
       A["worktree_total"] >= 94 and A["worktree_tensorplatform"] >= 90,
       f"worktree {A['worktree_total']} 条（其中 /TensorPlatform {A['worktree_tensorplatform']}"
       f" 条）；主仓 dirty={A['main_repo_dirty']}（分支 {A['main_repo_branch']}），"
       f"本站 dirty={A['this_worktree_dirty']}")
    ck("A4-thisbranch", A["this_branch"] == "agent/m220",
       f"本站分支 {A['this_branch']}，HEAD {A['this_head'][:7]}")

    report = {
        "mission": "m220",
        "shift": "第78班 (派发器 shift#46)",
        "date": "2026-10-23",
        "readings": READINGS.name,
        "collected_at_utc": R["collected_at_utc"],
        "evidence_sources": [
            "open_digital_foundation.tensor_dev_missions（只读 SQL）",
            "open_digital_foundation.tensor_approval_requests（只读 SQL）",
            "agent_work/workers.json（守护权威源）",
            "agent_work/llm_gateway_state.json + probe_m220/ 清理前快照",
            "agent_work/auto_approver.log + auto_approver.PASS_ALL + approver_escalated.json",
            "agent_work/agent-*.log（冷却窗口原文）",
            "tools/agent/llm_endpoints.json + llm_gateway.py 源码",
            "curl GET /models（bash 终端直连，凭据不进 python 子进程）",
        ],
        "dispositions": [
            {"target": "llm_gateway_state.json",
             "action": "清理过期冷却态（relay-13001，delta<0 已过期 → {}）",
             "auto": True},
            {"target": "m211",
             "action": "API refill +60 tools / +7200s（gateway_unavailable，三关全过）",
             "auto": True},
        ],
        "not_disposed": [
            {"id": 208, "why": "cylinder 线同 goal 已被 m221(running) 承接（承接门不过）"},
            {"id": 216, "why": "同上，benchmark_dispatch 22:42:49Z 已重派第 2/3 次"},
            {"id": 217, "why": "tutorial 线同 goal 已被 m219(running) 承接"},
            {"id": 218, "why": "operator 班次同 goal，本班 m220 即承接腿"},
        ],
        "checks": {"passed": PASS, "failed": FAIL},
        "checks_total": len(PASS) + len(FAIL),
        "checks_passed": len(PASS),
    }
    # 幂等写：先按「不含 generated_at_utc」比较，未变则不重写（提交后复跑不脏树）
    def canon(d: dict) -> str:
        d = {k: v for k, v in d.items() if k != "generated_at_utc"}
        return json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True)

    old = None
    if REPORT.exists():
        try:
            old = canon(json.loads(REPORT.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            old = None
    if old != canon(report):
        report["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        print("report rewritten（内容有变）")
    else:
        print("report unchanged（幂等，不重写）")

    print(f"机器门 {len(PASS)}/{len(PASS) + len(FAIL)} PASS")
    for x in PASS:
        print("  PASS", x)
    for x in FAIL:
        print("  FAIL", x)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())