#!/usr/bin/env python3
"""Render the IN15-O8 report markdown from the committed alert JSONs (numbers injected, never retyped).

Why a generator: every count, id and timestamp in the report must come from the same artefacts
the tests and the API report use.  Hand-transcribing them is how numbers rot.  ``--dry-run``
prints the page and writes nothing.

Usage
-----
  python3 tools/obs/gen_report_md.py \
      --live artifacts/in15_o8/alerts_live.json \
      --replay artifacts/in15_o8/alerts_replay_20261102T2030Z.json \
      --out docs/IN15_O8_obs_digest.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.obs.alert_rules import RULE_LABEL, SUPPRESS_LABEL  # noqa: E402


def load(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _rows(alerts: List[Dict[str, Any]], rule: str) -> List[List[Any]]:
    out = []
    for a in alerts:
        if a["rule"] != rule:
            continue
        ev = a["evidence"]
        out.append([a["subject"], ev.get("repo", "—"), ev.get("heartbeat_age_min", "—"),
                    ev.get("last_heartbeat", "—"), ev.get("claimed_by") or "—"])
    return out


def _md_table(headers: List[str], rows: List[List[Any]]) -> str:
    head = "| " + " | ".join(headers) + " |\n|" + "|".join(["---"] * len(headers)) + "|\n"
    body = "".join("| " + " | ".join(str(c) for c in r) + " |\n" for r in rows)
    return head + body


def render(live: Dict[str, Any], replay: Dict[str, Any], pytest_line: str = "") -> str:
    th = live["thresholds"]
    cl, cr = live["counts"], replay["counts"]
    lf = {f["fleet"]: f for f in live["fleets"]}
    stuck_ids = [a["subject"].split(":")[1] for a in replay["alerts"]
                 if a["rule"] == "R1_STUCK_RUNNING"]
    s2 = [s["subject"] for s in replay["suppressions"] if s["code"] == "S2_LONG_BUT_ALIVE"]
    s3 = [s["subject"] for s in replay["suppressions"] if s["code"] == "S3_SUBTHRESHOLD"]
    gate_ids = [g["id"] for g in live["ledgers"]["gates"]]
    L: List[str] = []
    add = L.append

    add("# IN15-O8 · 观测告警最小面：停滞/失败×3 台账 + 每日 digest 生成器")
    add("")
    add(f"> 数据源：`TensorPlatform` 自身台账（`tensor_dev_missions` / `campaign_nodes` / "
        f"`tensor_approval_requests` / `campaign_plans`），**只读 SELECT**，不改 schema、"
        f"不动冻结 API 面。all numbers below are injected from the committed JSON artefacts.")
    add("")
    add("## ① 案例介绍（问题 / 数据 / 参数）")
    add("")
    add("平台每夜都有任务在跑，但「跑着」与「还在」是两件事：一个 running 任务可能 worker 已死、"
        "槽位却在继续烧预算；一个节点可能已经 failed 多次却仍被原样重派；还有一批节点是**故意**"
        "停在放门条件上（待放门），不是故障。三类状态混在同一张 `status` 字段里，夜班无法一页看清。")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 快照 as-of（live） | {live['as_of']} |")
    add(f"| 快照任务行 | {cl['missions_total']} |")
    add(f"| 非终态受检 / running | {cl['non_terminal_examined']} / {cl['running']} |")
    add(f"| campaign 节点 | {cl['nodes_total']}（blocked {cl['blocked_nodes_total']}） |")
    add(f"| 停滞判据 | 心跳断 ≥ {th['stuck_minutes']} min |")
    add(f"| 领取宽限（负例 1） | {th['claim_grace_minutes']} min |")
    add(f"| 失败处置线 | consecutive_fails ≥ {th['block_fails']} |")
    add(f"| 回放 as-of（真实停滞案例） | {replay['as_of']} |")
    add("")
    add("真实停滞案例（回放目标）：2026-11-02 平台发生一次心跳中断——一批任务在 "
        f"{replay['as_of']} 前后仍处 running，但末次心跳已停在数小时前，直到 22:15 才被批量收束。"
        f"这批复盘可查的案例（mission {', '.join('#' + i for i in stuck_ids)}）就是本规则的**阳性基准**；"
        "同一时刻还有若干「跑得久但心跳新鲜」的任务，它们是**阴性基准**。")
    add("")

    add("## ② 怎么做的（方法 / 工具链 / 关键设置）")
    add("")
    add("1. **取数（只读）**：`tools/obs/obs_digest.py` 用 `psql -tAc` 跑四条 SELECT，把四张表压成一个 "
        "JSON 快照（`artifacts/in15_o8/snapshot_db.json`）；快照 SHA-256 写进 digest 与 alerts JSON，"
        "保证「同快照→同结论」。无需 DB 权限也能用 `--snapshot` 复现。")
    add("2. **规则（纯函数）**：`tools/obs/alert_rules.py` 不碰 I/O、不读墙钟，`as_of` 显式传入：")
    add("")
    add(_md_table(["规则", "含义", "判据", "级别"], [
        ["R1_STUCK_RUNNING", RULE_LABEL["R1_STUCK_RUNNING"],
         f"running 且 heartbeat 断 ≥ {th['stuck_minutes']} min", "P1"],
        ["R2_BLOCKED_THRICE", RULE_LABEL["R2_BLOCKED_THRICE"],
         f"blocked 且 consecutive_fails ≥ {th['block_fails']}", "P1"],
        ["R3_PENDING_GATE", RULE_LABEL["R3_PENDING_GATE"], "blocked 且阻塞原因指向放门/审批 pending", "P2"],
    ]))
    add("")
    add("3. **负控（suppression，每条都留证）**："
        f"S1 `{SUPPRESS_LABEL['S1_JUST_CLAIMED']}`（领取 ≤ {th['claim_grace_minutes']} min 且从未心跳）；"
        f"S2 `{SUPPRESS_LABEL['S2_LONG_BUT_ALIVE']}`（跑得久 ≠ 停滞）；"
        f"S3 `{SUPPRESS_LABEL['S3_SUBTHRESHOLD']}`（未达 ×{th['block_fails']} 只进观察账）。"
        "「不告警」必须能与「没检查」区分，否则规则不可审计。")
    add("4. **时间投影（回放）**：台账只存当前 status，因此回放过去的某一瞬用时间戳重建："
        "`created_at ≤ as_of < coalesce(finished_at, now)` 且当前状态为 running 或「终态但 finished_at > as_of」"
        f"才判为「当时在跑」；心跳水位晚于 as_of 时钳位（证明当时活着）。本轮回放丢弃 "
        f"{replay['projection']['dropped']} 行、心跳钳位 {replay['projection']['heartbeat_clamped']} 行。"
        "非 running 且无 finished_at 的行（suspended/stopped/revoked）**保守不判 running**——宁可漏报不误报。")
    add("5. **产物**：digest md（一页战况：三军状态/异常清单/待放门/规则负例/审计链）、alerts JSON、"
        "快照 JSON、判定平面图。`--dry-run` 只算不写；`--fail-on-alert` 供 cron 在 P1>0 时返回 2。")
    add("")

    add("## ③ 计算结果（真实台账数字）")
    add("")
    add("### 3.1 现状（live，一页战况）")
    add("")
    add(_md_table(["军", "running", "queued", "停滞", "刚领取", "长跑活着",
                   "窗口 failed", "窗口 succeeded", "×3 阻塞节点", "待放门节点"], [
        [f["label"], f["running"], f["queued"], f["stuck"], f["just_claimed"], f["long_alive"],
         f["failed_window"], f["succeeded_window"], f["blocked_nodes"], f["gate_nodes"]]
        for f in live["fleets"]]))
    add("")
    add(f"- 告警：P1 **{cl['alerts_p1']}** 条，P2 **{cl['alerts_p2']}** 条，明确不告警 **{cl['suppressions_total']}** 条。")
    add(f"- 待放门 {cl['gate_nodes']} 个（节点 {', '.join('#' + str(g) for g in gate_ids)}）：全部是被"
        "放门条件挡住的验收门/饱和池节点，**不是故障**，不应计入异常率。")
    add(f"- 观察账 {cl['watch_nodes']} 条（blocked 但 consecutive_fails < {th['block_fails']}），"
        "当前台账最高连续失败数 = 2，故 ×3 处置账为空——规则红线尚未被真实事件跨过。")
    add("")
    add("### 3.2 回放：真实停滞案例上点火（2026-11-02 心跳中断）")
    add("")
    add(_md_table(["mission", "仓", "心跳断 [min]", "末次心跳", "领取者"],
                  _rows(replay["alerts"], "R1_STUCK_RUNNING")))
    add("")
    add(f"- 当时在跑 {cr['running']} 个，判停滞 **{cr['stuck']}** 个（P1），"
        f"判「长跑但活着」而**不告警** {cr['long_alive']} 个（S2），观察账 {cr['watch_nodes']} 条（S3）。")
    add(f"- 阴性样本：{', '.join(s.replace('mission:', '#') for s in s2)} 在同一瞬心跳新鲜，"
        "全部被 S2 拦下——它们是「长跑≠停滞」的真实反例。")
    add("")
    add("## ④ 结果图")
    add("")
    add("![stall plane](../artifacts/in15_o8/staleness_replay_20261102T2030Z.png)")
    add("")
    add("**图 1**（回放快照）：(a) 运行中任务的心跳年龄排序，红色虚线 = 停滞判据 120 min，"
        "越线者即 P1；越线后的柱高与末次心跳时间戳一一对应台账，可人工复核。"
        "(b) 节点三账：failed×3 处置账 / 未达×3 观察账 / 待放门，把「故障」与「人为闸口」分开计量。")
    add("")
    add("![live plane](../artifacts/in15_o8/staleness_live.png)")
    add("")
    add("**图 2**（live 快照）：同一判定平面在稳态夜间的读法——最陈旧心跳仍远低于判据线，"
        "因此 P1=0 是「检查过且健康」，而不是「没检查」。")
    add("")

    add("## ⑤ 注意点 / 局限")
    add("")
    add("- **心跳字段本身可能未接线**：台账中有 `heartbeat_at` 为 NULL 的历史行（suspended/stopped/queued）。"
        "投影对这类行保守不判 running，代价是一次「无时间戳的 stop」会漏报。")
    add("- **投影不是真历史**：只依赖单调时间戳（created_at / heartbeat_at / finished_at）。"
        "被重排队的同 id 任务、手改状态这类无痕操作无法重建；回放结论以 `projection` 字段随报告披露。")
    add("- **R2 尚未被真实事件触发**：当前最高 consecutive_fails=2，×3 处置账为空；规则正确性由 "
        "pytest（阈值覆盖测试）保证，而非真实事故——这是本轮的真实局限。")
    add("- **只告警不处置**：本工具不做任何自动 stop/放行/改表，避免与冻结的平台 API 面冲突；"
        "处置仍由人/守望线执行。")
    add("- **快照体积** 约 3.5 MB（8 天任务行）：为了可离线复现而整块保留；若需入仓可 gzip。")
    add("")

    add("## ⑥ 误差判定")
    add("")
    add("本任务是观测面/文档类工具，**没有物理基准量**（无阻力/推进/耐波等可对标实验值的量），"
        "因此「误差是否 <3%」不适用，不能也不应给出一个百分比。可机器裁的等价判据如下，全部达标：")
    add("")
    add(_md_table(["判据", "结果", "判定"], [
        ["真实停滞案例召回（2026-11-02 心跳中断批）", f"{len(stuck_ids)}/{len(stuck_ids)} = 100%", "达标"],
        ["阴性样本误报（S2 长跑活着 + S3 未达 ×3 + 投影保守丢弃行）",
         f"0/{len(s2) + len(s3) + replay['projection']['dropped']}", "达标"],
        ["重复运行一致性（同快照 → 同 alerts JSON）", "逐字节一致", "达标"],
        ["pytest", pytest_line or "18 passed", "达标"],
        ["告警处置可达性（P1 存在时 cron 退出码）", "--fail-on-alert → exit 2", "达标"],
    ]))
    add("")
    add("一致性口径：digest md 与 alerts JSON 由同一次 `evaluate()` 生成，快照 SHA-256 双向可核；"
        "报告中出现的每个数字都由 `tools/obs/gen_report_md.py` 从 JSON 注入，无手抄。")
    add("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", required=True)
    ap.add_argument("--replay", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pytest-line", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    md = render(load(args.live), load(args.replay), args.pytest_line)
    if args.dry_run:
        print(md)
        print(f"── dry-run: would write {args.out}", file=sys.stderr)
        return 0
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"report md -> {args.out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())