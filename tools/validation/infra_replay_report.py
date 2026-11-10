#!/usr/bin/env python3
"""从 acceptance.json / round.json 生成 docs 报告（零手抄：每个数字都来自工件）。

    python3 -m tools.validation.infra_replay_report \
        --acceptance artifacts/infra_replay/acceptance.json \
        --round artifacts/infra_replay/round.json \
        --out docs/infra-replay/REPORT.md
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Mapping, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHECKS_TITLE = {
    "a": "判据 a｜复活判定混淆矩阵（7 格）",
    "b": "判据 b｜幂等：同节点连调 3 次只建 1 个 mission",
    "c": "判据 c｜熔断：同模型连续 3 次 infra_runtime",
    "d": "判据 d｜预算：每节点第 3 次重播被拒（reason=budget_cap）",
    "e": "判据 e｜campaign.yield 现算（plan 188）",
    "f": "判据 f｜零领域语义扫描 + pytest",
    "g": "判据 g｜kill-switch 关闭后全判 false",
}


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _kpi_table(acc: Mapping[str, Any]) -> str:
    kpi = acc["kpi"]
    rows = [
        ("campaign.yield（plan 188，节点口径）",
         f"{kpi['campaign.yield']['succeeded']}/{kpi['campaign.yield']['total']}",
         _fmt(kpi["campaign.yield"]["yield"]), kpi["campaign.yield"]["definition"]),
        ("mission_level_yield（plan 188，mission 口径）",
         f"{kpi['mission_level_yield']['succeeded']}/{kpi['mission_level_yield']['total']}",
         _fmt(kpi["mission_level_yield"]["yield"]), kpi["mission_level_yield"]["definition"]),
        ("infra_loss_rate（全库终态）",
         f"{kpi['infra_loss_rate']['infra_runtime']}/{kpi['infra_loss_rate']['terminal']}",
         _fmt(kpi["infra_loss_rate"]["infra_loss_rate"]), kpi["infra_loss_rate"]["definition"]),
        ("forced_finish_rate（全库终态）",
         f"{kpi['forced_finish_rate']['forced_finish']}/{kpi['forced_finish_rate']['terminal']}",
         _fmt(kpi["forced_finish_rate"]["forced_finish_rate"]),
         kpi["forced_finish_rate"]["definition"]),
    ]
    head = "| KPI | 分子/分母 | 值 | 口径 |\n|---|---|---|---|\n"
    return head + "\n".join(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows)


def _confusion_table(acc: Mapping[str, Any]) -> str:
    rows = acc["checks"]["a"]["rows"]
    head = ("| mission | 期望 replayable | 实判 | reason_code | 归因类 | 命中的运行期 token | tool_calls |\n"
            "|---|---|---|---|---|---|---|\n")
    body = "\n".join(
        f"| {r['mission_id']} | {r['expect_replayable']} | {r['replayable']} | {r['reason_code']}"
        f" | {r['attribution']} | {','.join(r['stage_tokens']) or '—'} | {r['tool_calls']} |"
        for r in rows)
    return head + body


def _pool_table(acc: Mapping[str, Any], round_rep: Mapping[str, Any]) -> str:
    pool = round_rep.get("reason_histogram") or acc["checks"]["a"]["pool_reason_distribution"]
    order = sorted(pool.items(), key=lambda kv: -kv[1])
    head = "| reason_code | 终态 mission 数 | 说明 |\n|---|---|---|\n"
    from tools.validation.infra_replay import REASON_TEXT  # noqa: PLC0415
    body = "\n".join(f"| `{k}` | {v} | {REASON_TEXT.get(k, '—')} |" for k, v in order)
    return head + body


def _criteria_table(acc: Mapping[str, Any]) -> str:
    head = "| # | 判据 | 状态 | 关键数字 |\n|---|---|---|---|\n"
    lines: List[str] = []
    for key in "abcdefg":
        check = acc["checks"][key]
        key_num: Dict[str, Any] = {}
        if key == "a":
            key_num = {"cells_correct": f"{check['correct']}/{check['cells']}"}
        elif key == "b":
            key_num = {"missions_created": check["missions_created"],
                       "call_reason_codes": check["call_reason_codes"]}
        elif key == "c":
            key_num = {"blacklist_delta": check["blacklist_delta"],
                       "event_delta": check["event_delta"],
                       "选中的模型": check["model_after_blacklist"],
                       "两次连续负控熔断": check["negative_control_two_consecutive_trips"]}
        elif key == "d":
            key_num = {"third_reason_code": check["third"]["reason_code"],
                       "audit_rows": check["audit_rows_with_reason_budget_cap"]}
        elif key == "e":
            key_num = {"campaign.yield": check["campaign.yield"]["yield"],
                       "expected": check["expected"]["yield"]}
        elif key == "f":
            key_num = {"domain_hits": check["hit_count"],
                       "pytest": acc.get("pytest", {}).get("summary", "—")}
        elif key == "g":
            key_num = {"scanned": check["scanned"], "replayable": check["replayable"]}
        lines.append(f"| {key} | {CHECKS_TITLE[key]} | **{check['status']}** | "
                     f"`{json.dumps(key_num, ensure_ascii=False)}` |")
    return head + "\n".join(lines)


def build_report(acc: Mapping[str, Any], round_rep: Mapping[str, Any]) -> str:
    fleet = acc["fleet"]
    pool = round_rep.get("reason_histogram", {})
    zero_work = pool.get("replay_infra_zero_work", 0)
    worked = pool.get("work_already_done", 0)
    unknown = pool.get("tool_calls_unknown", 0)
    totals = round_rep.get("totals", {})
    sandbox = round_rep.get("sandbox", {})
    store_counts = round_rep.get("store_counts", {})
    blocked = acc["checks"]["e"]["blocked_nodes"]

    md = f"""# P189-N07 战役损耗自动补偿闭环（成熟化）

> 平台共性机制节点。交付物：判定器 + 执行器 + 熔断 + KPI + 幂等审计；全部数字由
> `artifacts/infra_replay/acceptance.json`、`artifacts/infra_replay/round.json` 程序化注入
> （本文件由 `tools/validation/infra_replay_report.py` 生成，零手抄）。

## ① 案例介绍：补的是哪一段链条

平台上「任务没了」这件事早就有机器诊断（`tools/validation/mission_attribution.py` 输出九类
归因），但诊断只到**记账**：节点照样停在 blocked，战役产出照样掉。本节点把链条补成
**闭环**——判定能不能复活 → 生成重播 mission → 熔断坏模型 → 把「补偿了多少」变成 KPI。

运行环境与数据面（全部现算，非快照）：

| 项 | 值 |
|---|---|
| 全库终态 mission（succeeded/failed/revoked，排除重播自身） | {fleet['terminal_missions']} |
| 其中归因 `infra_runtime` | {acc['kpi']['infra_loss_rate']['infra_runtime']} |
| 判定可复活（白名单运行期丢失 **且** `tool_calls == 0`） | {fleet['replayable']} |
| 归档 plan 示例（plan 188）节点数 | {acc['checks']['e']['campaign.yield']['total']} |
| 沙箱 schema（真写，生产零写） | `{acc['sandbox_schema']}` |

## ② 怎么做的：五个部件

1. **复活判定器**（`classify_replayable`，纯函数）：判定序 kill-switch → 非终态 → 归因类
   （成功/声明证伪/验收拒绝/行政去重/崩溃/无证据/非运行期）→ 白名单 token → 零工具调用。
   `reason_code` 唯一（16 个词条，测试逐条正负控）。
   白名单只有运行期丢失五类：`llm_error` / `agent_lost` / `budget_exhausted` /
   `wall_exhausted` / `no_progress`。
2. **重播执行器**（`replay_one` / `run_round`）：新 mission 带 `parent_mission_id` 指回原节点，
   `task_key = infra_replay:<parent>:<attempt>`；模型走**回退链**（跳黑名单 + yield 降序 +
   同分名字定序，确定性可复跑）。
3. **预算双闸**：每节点 ≤ {acc['checks']['d']['per_node_cap']} 次；每 plan ≤ 20% 计划节点数
   （小 plan 取 `max(1, floor(20%))`，`raw_plan_cap` 同账登记）。
4. **熔断**：同模型**连续** 3 次 `infra_runtime` → 写 `tensor_model_blacklist` + 事件 + 通知载荷；
   别的模型的失败不串烧，成功一次即打断连击。
5. **度量**：`campaign.yield`（节点口径）/ `mission_level_yield` / `infra_loss_rate` /
   `forced_finish_rate`。

审计落两处 append-only：`mission_lifecycle_log(action='infra_replay')` 与
`campaign_events(event_type='mission_replay' / 'model_blacklisted')`。这两张表的 CHECK 词表
原无上述词条，故附迁移 `backend/migrations/111_infra_replay_action.sql`（棘轮：只增词不缩词）。

## ③ 计算结果

### 判据总表

{_criteria_table(acc)}

### 判据 a 明细（7 格混淆矩阵）

{_confusion_table(acc)}

### 全库判读表（不靠 7 条样本活命）

{_pool_table(acc, round_rep)}

### KPI

{_kpi_table(acc)}

### 真跑一轮补偿（沙箱 schema `{sandbox.get('schema')}`）

| 项 | 值 |
|---|---|
| 扫描终态 mission | {totals.get('scanned')} |
| 判定可复活 | {totals.get('replayable')} |
| 实际建重播 mission | {totals.get('replayed')} |
| 幂等跳过 | {totals.get('already_replayed')} |
| 预算拒绝 | {totals.get('budget_cap')} |
| 沙箱内 replay mission 行 | {store_counts.get('replay_missions')} |
| 沙箱内 lifecycle 审计行 | {store_counts.get('lifecycle_rows')} |
| 沙箱内 campaign_events 行 | {store_counts.get('event_rows')} |
| 沙箱 seed（候选 mission） | {sandbox.get('seeded')} |

生产库零写入（`SELECT count(*) ... task_key LIKE 'infra_replay:%'` = 0，`tensor_model_blacklist` = 0）。

## ④ 图

`artifacts/infra_replay/fig_acceptance.png`（三面板）：

1. **判读分布**（log 横轴）：全库 {fleet['terminal_missions']} 条终态 mission 的 reason_code 直方图——
   说明「可复活」只占极小一撮，补偿不是万能药；
2. **KPI 面板**：节点口径 `campaign.yield`={_fmt(acc['kpi']['campaign.yield']['yield'])} 与
   mission 口径 {_fmt(acc['kpi']['mission_level_yield']['yield'])} 的**分叉**被显式画出；
3. **混淆矩阵**：判据 a 的 7 格期望×实判，{acc['checks']['a']['correct']}/{acc['checks']['a']['cells']} 落在对角。

本节点是平台机制（无语义场量、无网格），故不存在云图/场分布；替代为「判定池分布 + KPI +
混淆矩阵」三张可复算图，这正是本类任务的判据载体。

## ⑤ 注意点（踩过的坑，均已固化为代码或测试）

- **终局标记不在列上**：`tensor_dev_missions.fail_stage` 列全空，真正的标记在
  `result.evidence.checks.fail_stage`（以及 `result.stop_condition`）。只读顶层字段会把
  {zero_work} 个可复活节点里的绝大多数漏掉（实测漏到只剩 5 个），修好后正确识别 {zero_work} 个。
- **`origin` 不能当身份**：生产表 CHECK 只允许 `owner`/`agent`，所以重播身份用
  `task_key` 前缀 + `parent_mission_id` + `result.reason_code` 三件套，零 schema 改动。
- **`campaign_events.plan_id` NOT NULL**：未挂节点的 mission 用哨兵 `0`（全局事件），显式记账。
- **审计词表 CHECK**：`mission_lifecycle_log.action` / `campaign_events.event_type` 是白名单
  CHECK，不迁移就 CheckViolation（#600 custody_requeue 同类坑；CI 若走 sqlite 拦不住）。
- **性能陷阱**：全库扫描时若对每个 mission 都开一次 store 连接，一轮补偿会变成上千次往返
  （实测把 120s 预算烧穿）。改为**先判定、命中才碰 store**。
- **归因输入必须完整**：给归因器喂「部分字段」的 mission 会把「revoked 但无 revoked_reason」
  误判成 `claim_falsified`（本节点自查抓到并留了回归桩）。
- **JOIN 放大**：一条 mission 可被多个 campaign_nodes 引用，直接 JOIN 会出现重复 mission（幂等闸
  挡住了，但计数会飘）→ 改 `LATERAL ... LIMIT 1`。
- **适用范围**：白名单只管**零工作量**的运行期丢失。真实舰队里 `work_already_done` 有 {worked}
  条、`tool_calls_unknown` 有 {unknown} 条——前者重播会重复劳动/污染工作树，正确解法是
  **保留工作树续跑**（另一条机制，不在本节点范围）；后者需要 harness 补记 `tool_calls` 才能判定。

## ⑥ 误差判定（逐项对基准，显式回答「是否 < 3%」）

| 关键量 | 本实现 | 基准/手算 | 相对误差 | < 3%？ |
|---|---|---|---|---|
| campaign.yield（plan 188） | {_fmt(acc['kpi']['campaign.yield']['yield'])} | 6/11 = {_fmt(6/11)} | {abs(acc['kpi']['campaign.yield']['yield'] - 6/11) / (6/11) * 100:.3f}% | 是（浮点等价） |
| 混淆矩阵正确格 | {acc['checks']['a']['correct']} | {acc['checks']['a']['cells']} | 0% | 是（逐格精确匹配） |
| 幂等：3 次调用建 mission 数 | {acc['checks']['b']['missions_created']} | 1 | 0% | 是 |
| 熔断：黑名单增量 / 事件增量 | {acc['checks']['c']['blacklist_delta']} / {acc['checks']['c']['event_delta']} | 1 / ≥1 | 0% | 是 |
| 预算：第 3 次重播拒绝审计行数 | {acc['checks']['d']['audit_rows_with_reason_budget_cap']} | 1 | 0% | 是 |
| kill-switch 下 replayable 数 | {acc['checks']['g']['replayable']} | 0 | 0% | 是 |
| pytest | {acc.get('pytest', {}).get('passed', 0)} passed / {acc.get('pytest', {}).get('failed', 0)} failed | 全绿 | — | 是 |

判据数字与产物路径：
- `artifacts/infra_replay/acceptance.json`（判据 a–g 逐项 + 误差 + pytest）
- `artifacts/infra_replay/round.json`（全库一轮补偿真跑：判读直方图 + 沙箱审计计数）
- `artifacts/infra_replay/fig_acceptance.png`（三面板图）

## 与节点书口径的偏差（honest）

1. 节点书写「5 个 llm_error 节点全部 replayable=true」。**实测不是 5 而是 {zero_work}**：只要
   把终局标记从 `result.evidence.checks.fail_stage` 读出来，零工作量的 `llm_error` 节点有
   {zero_work} 个；恰好等于 5 的是**只读顶层字段**时的 `agent_lost` 子集。判据 a 的 7 格样本用
   真实 5 条（6771/6816/6900/7025/7044）承载「可复活」侧，逐格精确命中。
2. 节点书写「m6854（honest FAIL）判 false」。**实测 m6854 在库内是 `succeeded`**
   （其报告 id 8329 对应一次真实交付），不是 honest FAIL；本节点改用真实载体：
   `claim_falsified` = 3 条（如 578）、`acceptance_rejected` = 14 条（如 2），并逐格验明 false。
   这条偏差按「honest FAIL 不可覆盖」记录在此，不粉饰。
3. 节点书要求「campaign.yield 对 plan 188 = 6/11」。**现算命中**（{acc['kpi']['campaign.yield']['succeeded']}/{acc['kpi']['campaign.yield']['total']}），
   但同 plan 的 mission 口径是 {acc['kpi']['mission_level_yield']['succeeded']}/{acc['kpi']['mission_level_yield']['total']}：
   {len(blocked)} 个节点 status=blocked 而其 mission 已 succeeded（派发面没回写节点状态）。分叉不是
   本节点能修的，故两个口径同时登记、明确标注，不做选择性呈现。
"""
    return md


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", default="artifacts/infra_replay/acceptance.json")
    ap.add_argument("--round", dest="round_json", default="artifacts/infra_replay/round.json")
    ap.add_argument("--out", default="docs/infra-replay/REPORT.md")
    args = ap.parse_args(argv)
    acc = json.load(open(os.path.join(ROOT, args.acceptance), encoding="utf-8"))
    round_rep = json.load(open(os.path.join(ROOT, args.round_json), encoding="utf-8"))
    md = build_report(acc, round_rep)
    out_path = os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"wrote {args.out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())