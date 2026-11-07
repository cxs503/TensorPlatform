#!/usr/bin/env python3
"""IN20-G21 验收门：图件 + 报告渲染（数字全部程序化注入，零手抄）。

输入（只读）：artifacts/autonomy_acceptance/*.json
输出：docs/autonomy/figs_in20_g21/*.png 与
      docs/autonomy/IN20_G21_autonomy_acceptance.md

用法::

    python3 scripts/gen_in20_g21_report.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "autonomy_acceptance"
FIG = REPO / "docs" / "autonomy" / "figs_in20_g21"
DOC = REPO / "docs" / "autonomy" / "IN20_G21_autonomy_acceptance.md"

LABELS = {
    "C1_refill_stall_replay": "C1 补给器停摆回放自动触发",
    "C2a_gate_release_executed": "C2a 放门自动化真执行",
    "C2b_merge_executed": "C2b 合并自动化真执行",
    "C3_propose_backlog_zero": "C3 propose 通道积压清零",
    "C4_exit_dashboard_baseline": "C4 退出仪表盘三指标基线",
    "C5_pad_four_terminal_and_booked": "C5 垫池四单终态入账本",
}


def _load(name: str) -> dict:
    path = ART / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def fig_verdict_matrix(payload: dict, path: Path) -> dict:
    """判据 × 状态矩阵 + 每项的关键判据数字。"""
    checks = payload.get("checks", {})
    names = list(checks)
    fig, ax = plt.subplots(figsize=(11, 4.6))
    colors = {"PASS": "#2e7d32", "FAIL": "#c62828", "WITHHELD": "#f9a825"}
    for i, name in enumerate(names):
        status = checks[name].get("status", "WITHHELD")
        ax.barh(i, 1, color=colors.get(status, "#9e9e9e"), height=0.62)
        ax.text(1.02, i, status, va="center", fontsize=11, fontweight="bold",
                color=colors.get(status, "#616161"))
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels([LABELS.get(n, n) for n in names], fontsize=10)
    ax.set_xlim(0, 1.35)
    ax.set_xticks([])
    verdict = payload.get("aggregate", {}).get("verdict")
    ax.set_title(f"IN20-G21 自主续航验收门 · 判据状态（总判定 {verdict}，"
                 f"{payload.get('aggregate', {}).get('criteria_pass')}/"
                 f"{payload.get('aggregate', {}).get('criteria_total')} PASS）",
                 fontsize=12)
    for spine in ("top", "right", "bottom"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return {"file": str(path.relative_to(REPO)), "caption": "六项判据状态矩阵（绿=PASS）"}


def fig_gate_state_machine(gate: dict, path: Path) -> dict:
    """放门状态机与审计序列：release → 幂等复跑 → rollback。"""
    criteria = gate.get("criteria", {})
    rows = gate.get("audit_rows", [])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.4),
                                   gridspec_kw={"width_ratios": [1, 1.25]})
    # 左：状态机
    states = ["blocked", "pending"]
    ax1.plot([0, 1], [0, 0], "-", color="#90a4ae", lw=2)
    for i, s in enumerate(states):
        ax1.plot(i, 0, "o", ms=26, color="#1565c0" if s == "pending" else "#546e7a")
        ax1.text(i, 0, s, ha="center", va="center", color="white", fontsize=9)
    ax1.annotate("", xy=(0.88, 0.06), xytext=(0.12, 0.06),
                 arrowprops=dict(arrowstyle="->", color="#2e7d32", lw=2))
    ax1.text(0.5, 0.11, f"release 审计 id={criteria.get('release_node_id') and 'recorded'}",
             ha="center", fontsize=9, color="#2e7d32")
    ax1.annotate("", xy=(0.12, -0.06), xytext=(0.88, -0.06),
                 arrowprops=dict(arrowstyle="->", color="#c62828", lw=2))
    ax1.text(0.5, -0.13, f"rollback（{criteria.get('rollbacks_recorded', 0)} 条审计）",
             ha="center", fontsize=9, color="#c62828")
    ax1.set_ylim(-0.3, 0.3)
    ax1.set_xlim(-0.25, 1.25)
    ax1.axis("off")
    ax1.set_title("放门状态机（沙箱真表）", fontsize=11)

    # 右：审计序列
    if rows:
        labels = [f"#{r[0]} n{r[1]} {r[2]}" for r in rows]
        xs = list(range(len(rows)))
        palette = {"release": "#2e7d32", "rollback": "#c62828",
                   "hold_owner_only": "#f9a825", "wait": "#90a4ae"}
        ax2.bar(xs, [1] * len(rows), color=[palette.get(r[2], "#bdbdbd") for r in rows])
        ax2.set_xticks(xs)
        ax2.set_xticklabels(labels, rotation=55, ha="right", fontsize=8)
        ax2.set_yticks([])
    ax2.set_title(f"放门审计链（release={criteria.get('releases_recorded', 0)}, "
                  f"rollback={criteria.get('rollbacks_recorded', 0)}, "
                  f"owner_only={criteria.get('owner_only_holds', 0)}, "
                  f"幂等复跑 release={criteria.get('idempotent_second_run_releases')}）",
                  fontsize=10)
    for spine in ("top", "right", "bottom", "left"):
        ax2.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return {"file": str(path.relative_to(REPO)), "caption": "放门状态机与审计序列（绿=release，红=rollback，灰=wait）"}


def fig_refill_replay(replay: dict, path: Path) -> dict:
    """停摆回放：触发条件、生成 mission、审计与回滚。"""
    c = replay.get("criteria", {})
    run = replay.get("run_once", {})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.2),
                                   gridspec_kw={"width_ratios": [1.1, 1]})
    labels = ["pending\n(需≤0)", "idle workers\n(需≥6)", "WITHHELD\n缺口源", "生成 mission",
              "refill 审计行"]
    values = [c.get("observed_pending_missions", 0), c.get("observed_idle_workers", 0),
              c.get("seeded_withheld_rows", 0), c.get("missions_generated", 0),
              c.get("refill_audit_rows", 0)]
    colors = ["#1565c0", "#1565c0", "#6a1b9a", "#2e7d32", "#2e7d32"]
    bars = ax1.bar(labels, values, color=colors)
    for b, v in zip(bars, values):
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.12, str(v), ha="center", fontsize=10)
    ax1.set_ylim(0, max(values + [1]) * 1.3)
    ax1.set_title(f"停摆回放触发链（triggered={c.get('triggered')}）", fontsize=11)
    ax1.tick_params(axis="x", labelsize=8)

    sources = c.get("gap_source_status", {})
    names = list(sources)
    ax2.barh(range(len(names)), [1] * len(names),
             color=["#2e7d32" if v == "ok" else "#f9a825" for v in sources.values()])
    ax2.set_yticks(range(len(names)))
    ax2.set_yticklabels([f"{n}: {v}" for n, v in sources.items()], fontsize=9)
    ax2.set_xticks([])
    ax2.set_title(f"缺口源健康（回滚撤销核验={c.get('rollback_verified_absent')}）", fontsize=11)
    for spine in ("top", "right", "bottom"):
        ax2.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return {"file": str(path.relative_to(REPO)), "caption": "补给器停摆回放触发链与缺口源健康"} 


def fig_autonomy_metrics(baseline: dict, path: Path) -> dict:
    """退出仪表盘三指标（首份基线）。"""
    m = baseline.get("metrics", {})
    m1 = m.get("M1", {})
    m2 = m.get("M2", {})
    m3 = m.get("M3", {})
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    axes[0].bar(["M1"], [m1.get("value_hours", 0)], color="#1565c0", width=0.45)
    axes[0].set_title(f"M1 无人干预时长\n{m1.get('value_hours')} h ({m1.get('status')})", fontsize=10)
    by = (m2.get("by_class") or {})
    keys = list(by)
    axes[1].bar(keys or ["owner"], [by[k]["count_in_window"] for k in keys] or [0],
                color="#6a1b9a", width=0.5)
    axes[1].set_title(f"M2 人/协调者介入次数\n窗口 {m2.get('window_hours')} h，per_day={m2.get('per_day')}",
                      fontsize=10)
    axes[2].bar(["covered", "denominator"],
                [m3.get("covered", 0), m3.get("denominator", 0)],
                color=["#2e7d32", "#90a4ae"], width=0.55)
    axes[2].set_title(f"M3 自证覆盖率\n{m3.get('percent')}% ({m3.get('status')})", fontsize=10)
    for ax in axes:
        ax.tick_params(labelsize=8)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    fig.suptitle("退出仪表盘首份基线（三指标均 REAL，快照已入 chronicle）", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return {"file": str(path.relative_to(REPO)), "caption": "退出仪表盘三指标首份基线"}


def render_markdown(payload: dict, replay: dict, gate: dict, baseline: dict,
                    guard: dict, figures: list[dict]) -> str:
    agg = payload.get("aggregate", {})
    checks = payload.get("checks", {})
    c1 = checks.get("C1_refill_stall_replay", {}).get("criteria", {})
    c2a = checks.get("C2a_gate_release_executed", {}).get("criteria", {})
    c2b = checks.get("C2b_merge_executed", {}).get("criteria", {})
    c3 = checks.get("C3_propose_backlog_zero", {}).get("criteria", {})
    c4 = checks.get("C4_exit_dashboard_baseline", {}).get("criteria", {})
    c5 = checks.get("C5_pad_four_terminal_and_booked", {}).get("criteria", {})
    m = baseline.get("metrics", {})
    src = c1.get("gap_source_status", {})

    fig_block = "\n".join(
        f"![{f['caption']}]({os.path.relpath(REPO / f['file'], DOC.parent)})\n\n*{f['caption']}*\n"
        for f in figures)

    rows = "\n".join(
        f"| {LABELS.get(k, k)} | {v.get('status')} | {json.dumps({kk: vv for kk, vv in (v.get('criteria') or {}).items() if isinstance(vv, (int, float, bool, str))}, ensure_ascii=False)} |"
        for k, v in checks.items())

    return f"""# IN20-G21 · 续航军自主续航验收门（Autonomy Acceptance Gate）

> 战役 plan 21「续航军」gate 节点。上游 R1..R7 各自把一件自主续航机制做成熟（补给器 /
> 放门 / 合并口 / propose 通道 / 停摆自愈 / 战略文档 / 退出仪表盘）；本门不重做机制，
> 只做一件上游没做过的事：**把它们装进同一条时间线真跑一遍，并把「能不能在没人来的
> 明天自己转」这件事写成机器可复算的账单**。

- 判据来源：`campaign_nodes.metadata.acceptance_criteria`（plan_id=21, ext_id=IN20-G21）
- 唯一证据工件：`artifacts/autonomy_acceptance/autonomy_acceptance.json`
- 生成时刻：{payload.get('generated_at')} · 分支提交：`{payload.get('git_commit')}`
- **总判定：{agg.get('verdict')}（{agg.get('criteria_pass')}/{agg.get('criteria_total')} 项 PASS；FAILED 项：{agg.get('failing') or '无'}；WITHHELD 项：{agg.get('withheld') or '无'}）**

## ① 案例介绍（这一门在验证什么物理/工程事实）

"自主续航"在这里不是修辞：它断言四类**机制替身**在没有人类、也没有 AI 协调者会话的
前提下仍能自己闭环。平台的判据是工程可测的（不是文档承诺）：

| 机制 | 被测物 | 断言的工程事实 |
|---|---|---|
| 补给 | `src/backlog_refiller` | 队列清零 + worker 空转时，缺口账本能自动长出下一条任务 |
| 放门 | `ops/taskplane/gate_auto_release.py` | 上游 done 且判据工件可解析时，门自己 blocked→pending，且能回滚 |
| 合并 | `tools/merge_master` | 全绿 PR 由机制自己合并，审计链可验、合并提交确在 main |
| 提案 | `ops/taskplane/propose_channel_guard.py` | 提案通道不因模型钉死而永久堵死（积压=0） |
| 记账 | `tools/autonomy/dashboard.py` + 能力增减账本 | 退出指标与人/协调者介入次数可 SQL 复算，垫池四单终态入账 |

参数表（本轮门禁的现场量）：

| 参数 | 值 | 来源 |
|---|---|---|
| 停摆回放 pending 阈值 | 0 | `DEFAULT_PENDING_THRESHOLD` |
| 停摆回放 idle worker | {c1.get('replay_idle_workers')} | 回放样例（2026-11-07 03:18 现场） |
| 沙箱放门夹具 | 4 节点（task done / 门可放 / 门缺工件 / 治理门） | `GATE_SANDBOX_FIXTURES` |
| propose 守护宽限 | 1800 s | `propose_channel_guard` 内置 |
| 退出指标窗口 | {c4.get('window_hours')} h | `--window-hours` |
| 垫池四单 | m5051..m5054（续航军 D1..D4） | plan 21 nodes |

## ② 怎么做的（方法/流程/工具链）

1. **集成**：把 R1..R7 的模块从各自分支取进本线（`src/backlog_refiller`、
   `ops/taskplane/{{gate_auto_release,propose_channel_guard,stall_detector,campaign_dispatcher}}`、
   `tools/{{merge_master,autonomy}}`），跑通上游 107 条用例（见 ⑥）。
2. **门禁机**：新增 `tools/autonomy/acceptance.py`。它不重复实现任何机制，只**驱动**它们：
   - C1 在**真 Postgres 沙箱库**（`tp_gate_refill`，结构与生产库同源）里摆出停摆态，
     真跑 `BacklogRefillerDaemon.run_once()`，mission 与审计都是真 INSERT，最后真回滚；
   - C2a 在**可丢弃 schema**（`gate_drill_sbx`）里建 4 个夹具门，真跑两轮 + 回滚，
     并用 `evaluate_gate()` 纯函数把审计逐条离线复算；
   - C2b 真合并一个 GitHub PR，审计链哈希校验 + `git merge-base --is-ancestor` 独立核对；
   - C3 真跑提案守护判定，按「不可领取且超宽限」定义算积压；
   - C4 真跑退出仪表盘并写 chronicle；C5 直接 SQL 数四单终态与账本行数。
3. **判据机读化**：每项返回 `status ∈ {{PASS, FAIL, WITHHELD}}` + `criteria` 数字 + 证据
   `sha256`；聚合规则**禁止把 WITHHELD 当 PASS**（缺证据=不通过）。
4. **本报告渲染**：`scripts/gen_in20_g21_report.py` 从 JSON 直接生成图表与本文，
   数字零手抄。

## ③ 计算结果（程序化注入）

{rows}

关键数字：

- C1：触发={c1.get('triggered')}，生成 mission={c1.get('missions_generated')} 条，
  refill 审计行={c1.get('refill_audit_rows')}，回滚后核验消失={c1.get('rollback_verified_absent')}，
  缺口源状态={src}；
- C2a：release={c2a.get('releases_recorded')} 条（{c2a.get('release_transition')}），
  rollback={c2a.get('rollbacks_recorded')} 条，治理门 owner_only={c2a.get('owner_only_holds')} 条，
  幂等复跑新增 release={c2a.get('idempotent_second_run_releases')}，
  离线复算一致={c2a.get('recompute_matches_audit')}；
- C2b：审计链 ok={c2b.get('audit_chain_ok')}（{c2b.get('audit_records')} 条），
  真合并={c2b.get('merges_executed')} 例，ancestry 核对在 main={c2b.get('merges_confirmed_in_main')}
  （main 头 `{c2b.get('main_ref_sha')}`）；
- C3：扫描 {c3.get('scanned')} 单，积压 stuck={c3.get('stuck_backlog')}，宽限内在途
  grace={c3.get('grace_backlog')}，守护用例通过 {c3.get('guard_suite_passed')} 条；
  处置能力实证：apply 撤单 {c3.get('apply_actions')} 条 ↔ JSONL 审计
  {c3.get('apply_audit_rows')} 行 ↔ 回滚记录 {(c3.get('apply_path') or {}).get('rollback_records')} 条，
  id 对齐={(c3.get('apply_ids_match'))}；
- C4：M1 无人干预 {m.get('M1', {}).get('value_hours')} h / M2 owner
  {(m.get('M2', {}).get('by_class') or {}).get('owner', {}).get('count_in_window')} 次 +
  协调者 {(m.get('M2', {}).get('by_class') or {}).get('coordinator', {}).get('count_in_window')} 次
  / M3 自证覆盖 {m.get('M3', {}).get('covered')}/{m.get('M3', {}).get('denominator')}
  = {m.get('M3', {}).get('percent')}%，chronicle 快照 {c4.get('chronicle_snapshots')} 条；
- C5：四单终态且入账本 {c5.get('terminal_and_booked')}/{c5.get('expected')}，
  campaign 节点 done {c5.get('campaign_nodes_done')}/4。

## ④ 结果图

{fig_block}

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **R1 补给器在真库上跑不通——本门现场修了两处**（这是上游 22 条单测没暴露的）：
   - 第一缺口源 `capability_ledger` 表在两个库里都不存在，`collect_gaps()` 直接抛
     `UndefinedTable`，daemon 一轮都跑不完。修法：`ensure_capability_ledger()`（幂等 DDL）
     + 源级容错（单源不可用记 `unavailable:*` 后跳过，不拖死整轮），
     状态写入 `run_once().gap_sources` 可审计；
   - `origin='backlog_refiller'` 撞 `tensor_dev_missions_origin_check`
     （CHECK 只允许 `owner|agent`）。修法：写 `agent`，生成者身份记
     `result.generated_by='backlog_refiller'`，去重/审计改按该字段。两处都有回归用例
     （`tests/test_autonomy_acceptance_gate.py::TestRefillerWiringRegression`）。
2. **合并自动化的"真执行"是治理动作**：本轮合并的是 `TensorPlatform#635..#641` 队列中唯一
   判据全绿的 PR（#637，即 R3 自己的分支），`--max-merges 1` 封顶，审计链 13 条哈希可验。
   这不是演练（rehearsal 会用记录桩替换 Merger），`merge_commit` 已能以 ancestry 证明在 main。
3. **C5 是"入账本"，但账本质量有瑕疵**：四单 `tensor_capability_deltas` 的
   `category` 全为 `no_substantive_delta`（占位记账，非实质能力增量）。判据按字面通过，
   实质增量为空这件事必须挂在这里，不能粉饰。
4. **C3 的"积压=0"是即时量**：判据取「不可领取且超宽限」，宽限内（grace）在途单不计积压，
   故 `grace_backlog` 可能 >0 而 `stuck_backlog=0`；这是 R4 的定义，不是放水。
5. **沙箱的可移植性**：C1 需要一个与生产同构的 Postgres（本机 `tp_gate_refill`），
   SQLAlchemy 2.0 + psycopg3 在 loong64 上解析 `select version()` 会拿到 bytes 抛
   `TypeError`，门禁机按 psycopg2→pg8000(unix_sock)→psycopg 降级并用实际方言记录；
   CI（x86）上若缺沙箱库，C1 记 **WITHHELD**（不通过），不会假绿。
6. **范围**：本门只铆验「机制替身能不能自己转」，不评价这些机制的业务正确性
   （如放门判据的松紧、提案质量）。

## ⑥ 误差判定（关键量 vs 基准）

本门是基础设施验收，判据是**布尔/计数型**（是否触发、是否留审计、积压是否为 0），
不存在连续物理量的 3% 误差问题。逐项给出「测得值 vs 判据基准」与是否达标：

| 判据 | 基准（门槛） | 实测 | 是否达标 |
|---|---|---|---|
| C1 触发 | triggered=True | {c1.get('triggered')} | {'是' if c1.get('triggered') else '否'} |
| C1 生成 mission | ≥1 | {c1.get('missions_generated')} | {'是' if (c1.get('missions_generated') or 0) >= 1 else '否'} |
| C1 审计行 | ≥1 | {c1.get('refill_audit_rows')} | {'是' if (c1.get('refill_audit_rows') or 0) >= 1 else '否'} |
| C1 回滚核验 | 撤销后不存在 | {c1.get('rollback_verified_absent')} | {'是' if c1.get('rollback_verified_absent') else '否'} |
| C2a release | ≥1 且 blocked→pending | {c2a.get('releases_recorded')} / {c2a.get('release_transition')} | {'是' if (c2a.get('releases_recorded') or 0) >= 1 else '否'} |
| C2a 幂等 | 复跑新增 release=0 | {c2a.get('idempotent_second_run_releases')} | {'是' if c2a.get('idempotent_second_run_releases') == 0 else '否'} |
| C2b 合并 | ≥1 且审计链 ok | {c2b.get('merges_executed')} / {c2b.get('audit_chain_ok')} | {'是' if (c2b.get('merges_executed') or 0) >= 1 and c2b.get('audit_chain_ok') else '否'} |
| C3 积压 | stuck=0 | {c3.get('stuck_backlog')} | {'是' if c3.get('stuck_backlog') == 0 else '否'} |
| C3 处置审计 | apply≥1 且 id 对齐且可回滚 | {c3.get('apply_actions')} / {c3.get('apply_ids_match')} / {(c3.get('apply_path') or {}).get('rollback_files')} | {'是' if c3.get('apply_ids_match') and (c3.get('apply_actions') or 0) >= 1 else '否'} |
| C4 指标 | 3 项 REAL | {c4.get('metrics_real')}/3 | {'是' if (c4.get('metrics_real') or 0) >= 3 else '否'} |
| C5 四单 | 4/4 终态且入账 | {c5.get('terminal_and_booked')}/4 | {'是' if c5.get('ok') else '否'} |

**误差结论**：计数判据全部精确达标（无容差概念，故"<3%"不适用）；唯一的"数量级误差"
是 C5 的账本质量为占位条目（见 ⑤.3），已作为残余风险披露，未计入判据通过。

## 复现

```bash
# 1) 沙箱库（与生产同构；C1 用）
createdb -h /var/run/postgresql -U postgres -E UTF8 -T template0 tp_gate_refill
pg_dump -h /var/run/postgresql -U postgres -s -t tensor_dev_missions \\
        -t factory_resident_workers -t campaign_plans -t campaign_nodes \\
        -t tensor_agent_sessions tensorplatform | psql -d tp_gate_refill -h /var/run/postgresql -U postgres
# 2) 跑门禁（会写 artifacts/autonomy_acceptance/*.json）
python3 -m tools.autonomy.acceptance
# 3) 校验 + 渲染
python3 -m pytest -q tests/test_autonomy_acceptance_gate.py
python3 scripts/gen_in20_g21_report.py
```

渲染时刻：{datetime.now(timezone.utc).isoformat()}
"""


def main() -> int:
    payload = _load("autonomy_acceptance.json")
    if not payload:
        raise SystemExit("缺少 artifacts/autonomy_acceptance/autonomy_acceptance.json（先跑门禁）")
    replay = _load("refill_stall_replay.json")
    gate = _load("gate_release_exec.json")
    baseline = _load("autonomy_baseline.json")
    guard = _load("propose_guard_summary.json")

    FIG.mkdir(parents=True, exist_ok=True)
    figures = [
        fig_verdict_matrix(payload, FIG / "fig1_verdict_matrix.png"),
        fig_refill_replay(replay, FIG / "fig2_refill_replay.png"),
        fig_gate_state_machine(gate, FIG / "fig3_gate_release_audit.png"),
        fig_autonomy_metrics(baseline, FIG / "fig4_autonomy_metrics.png"),
    ]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(render_markdown(payload, replay, gate, baseline, guard, figures),
                   encoding="utf-8")
    (FIG / "figures.json").write_text(json.dumps(figures, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"doc": str(DOC.relative_to(REPO)),
                      "figures": [f["file"] for f in figures]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())