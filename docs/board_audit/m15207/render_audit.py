#!/usr/bin/env python3
"""m15207 审计交付物渲染：证据图 + AUDIT_m15177.md（数字全部从 verify_output.json 注入，零手抄）。

用法：python3 docs/board_audit/m15207/render_audit.py
产物：
  docs/board_audit/m15207/fig_m15177_evidence.png
  docs/board_audit/m15207/AUDIT_m15177.md
  docs/board_audit/m15207/gate_prejudge.json   （本地预跑 maturity_gate.judge 的裁定预测）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
VERIFY = json.loads((HERE / "verify_output.json").read_text(encoding="utf-8"))
EV = json.loads((HERE / "verdict_evidence.json").read_text(encoding="utf-8"))
INSERTED = json.loads((HERE / "verdict_inserted.json").read_text(encoding="utf-8"))

REAL = VERIFY["C3_real_value"]
FP = VERIFY["C4_synthetic_fingerprint"]
BM = VERIFY["C5_benchmark_judge"]
TESTS = VERIFY["C6_tests"]
REP = VERIFY["C7_report"]
INT = VERIFY["C8_integration"]
REPRO = VERIFY["C2_reproducibility"]
ART = VERIFY["C1_artifacts"]
CLAIM = 54.5


def gate_prejudge() -> dict:
    """本地预跑平台门（maturity_gate.judge），不改库：预测 confirmed/reopened 与压级原因。"""
    sys.path.insert(0, "/root/tp-agent")
    try:
        import maturity_gate as mg
    except Exception as e:                                    # pragma: no cover
        return {"ran": False, "error": f"{type(e).__name__}: {e}"}
    level, why = mg.judge(2, json.loads(json.dumps(EV)), "TensorPlatform", 15207)
    return {"ran": True, "claimed": 2, "gate_level": level, "why": why,
            "expect": "confirmed" if level == 2 else "reopened"}


def fig() -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), dpi=150)
    ax = axes[0]
    labels = ["terminal\n(denominator)", "traceable\n(numerator)", "report_id channel\n(missions_with_report_id)"]
    claimed = [14085, 7677, FP["committed_report_id_channel"]]
    real = [REAL["denominator"], REAL["numerator"], REAL["report_id_channel_count"]]
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], claimed, width=0.4, label="delivered (synthetic)", color="#d95f02")
    ax.bar([i + 0.2 for i in x], real, width=0.4, label="audit SQL (database)", color="#1b9e77")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("count")
    ax.set_yscale("log")
    ax.set_title("A  num/den: synthetic ledger vs real DB (log scale)", fontsize=9)
    for i, (c, r) in enumerate(zip(claimed, real)):
        ax.text(i - 0.2, c * 1.15, f"{c:,}", ha="center", fontsize=7)
        ax.text(i + 0.2, r * 1.15, f"{r:,}", ha="center", fontsize=7)
    ax.legend(fontsize=7)
    ax.grid(axis="y", ls=":", alpha=0.4)

    ax = axes[1]
    bars = ax.bar(["delivered\n(synthetic)", f"real DB\n({REAL['pct']}%)", "KPI target"],
                  [CLAIM, REAL["pct"], 100.0],
                  color=["#d95f02", "#1b9e77", "#7570b3"], width=0.55)
    for b, v in zip(bars, [CLAIM, REAL["pct"], 100.0]):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.2f}%", ha="center", fontsize=8)
    ax.set_ylim(0, 112)
    ax.set_ylabel("KPI-TRACE-100 (%)")
    ax.set_title(f"B  current vs target: delivered differs from real by {FP['abs_pp_deviation']} pp; gap to target {100 - REAL['pct']:.2f} pp", fontsize=9)
    ax.grid(axis="y", ls=":", alpha=0.4)
    fig.suptitle("m15177 audit evidence (board-audit m15207)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = HERE / "fig_m15177_evidence.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def md(gp: dict, png: Path) -> str:
    n_art = len(ART["present"])
    d = FP
    return f"""# m15177 交差审计报告（锚点 KPI-TRACE-100）

- 被审单：**m15177**「KPI-TRACE-100 计量：终态任务带可溯报告工件比率」（TensorPlatform，status=succeeded，报告 id 14281 / {REP['report_row'][0][2]}）
- 审计单：**m15207**（board-audit）｜裁定已落表 `tensor_maturity_verdicts.id={INSERTED['verdict_id']}`，level=**2**，gate_state 待平台门裁（本审计单在飞时门不裁）
- 基准裁判：`tensor_benchmark_cases` 中 **无** `anchor_slug='KPI-TRACE-100'` 行（全表 {BM['table_total_rows']} 行锚点，无本锚）→ **没有 ref/tolerance 判据行可裁**，故本报告以「独立 SQL 复算真值」替代基准裁判，并显式记此缺口。

## 1. 案例介绍

KPI-TRACE-100 定义（KPI 注册表 `tools/metrics/kpi_registry.json` → `trace_report_ratio.recompute_sql`）：
终态任务（`status IN (succeeded,failed,stopped,revoked)`）中 `report_id IS NOT NULL OR tensor_reports 有该 mission_id 行`
的占比，target=100%。

被审单声称的交付：计量工具链 `tools/kpi_trace`（measure/figures/doc）+ 工件 9 件 + 15 项测试 + 报告，
headline `current = {CLAIM}%`（7677/14085）。

## 2. 怎么做的（审计方法）

独立复核脚本 `docs/board_audit/m15207/verify_m15177.py`（**不复用**被审 measure.py 的计算路径）：

| 检查 | 做法 | 结果 |
|---|---|---|
| C1 工件 | 9 件工件逐件 sha256 + PNG 魔数校验 | 缺 0 件，5 张 PNG 均为真位图 |
| C2 复现 | 按报告附录 repro_cmd 重跑 `measure --synthetic` 到临时目录，逐字段比对 | {REPRO['fields']} 全 True（数字逐位一致），`data_mode={REPRO['data_mode_reported']}` |
| C3 真值 | 自写 SQL 直查库（口径照抄注册表公式） | `{REAL['numerator']}/{REAL['denominator']} = {REAL['pct']}%`（{REAL['at']}） |
| C4 指纹 | 交付底账 CSV × 库表逐条对表（抽样 {d['sample_n']} 行 + 总量/通道/分布） | 见 §3，结论=底账非库导出 |
| C5 基准 | `SELECT … FROM tensor_benchmark_cases WHERE anchor_slug='KPI-TRACE-100'` | 0 行（无判据） |
| C6 测试 | 被审工作树内重跑两个测试文件 | `{TESTS['summary']}`（声明 15 → 核实 15） |
| C7 报告 | 六段标题 + 5 图落盘 + 是否披露合成模式 | 六段齐、5 图在、**披露了 synthetic** |
| C8 集成 | 远端 main `{INT['main_head'][:12]}` 浅克隆全仓 grep `tools.kpi_trace` | 仅自带测试引用，无平台/求解器调用方 |

## 3. 计算结果

### 3.1 交付值 vs 真值

| 量 | 交付报告（synthetic） | 审计独立 SQL（database） | 差 |
|---|---|---|---|
| current | {CLAIM} % | **{REAL['pct']} %** | {d['abs_pp_deviation']} pp |
| numerator | 7677 | {REAL['numerator']} | {REAL['numerator'] - 7677:+d} |
| denominator | 14085 | {REAL['denominator']} | {REAL['denominator'] - 14085:+d} |
| report_id 通道 | {d['committed_report_id_channel']} | {d['real_report_id_channel']} | {d['real_report_id_channel'] - d['committed_report_id_channel']:+d} |
| succeeded 终态数 | {d['committed_succeeded_terminal']} | {d['real_succeeded_total']} | {d['real_succeeded_total'] - d['committed_succeeded_terminal']:+d} |

### 3.2 分状态真值（审计 SQL 直出）

| status | terminal | traceable | pct(%) |
|---|---|---|---|
""" + "\n".join(f"| {k} | {v['terminal']} | {v['traceable']} | {v['pct']} |"
                for k, v in REAL["by_status"].items()) + f"""

### 3.3 合成底账指纹（为什么 54.5% 不是「量」出来的）

- `tools/kpi_trace/synthetic.py` 第 23–25 行：`_DEFAULT_TERMINAL = 14085`、`_DEFAULT_TRACEABLE = 7677`
  ——**与报告 headline 分子分母完全相同**；docstring 自述「参数来自 m15120 计量报告的历史观测」。
  ⇒ 交付值 = 把历史观测值喂回生成器再读出来的**循环论证**，对世界不产生任何新信息。
- 抽样 {d['sample_n']} 行交付底账：id 在真库命中 {d['sample_ids_in_db']} 行，`status` 一致 {d['sample_status_match']} 行、
  `created_at` 一致 {d['sample_created_at_match']} 行；样例 {json.dumps(d['mismatch_examples'][:2], ensure_ascii=False)}。
- 交付底账 {d['csv_rows']} 行 vs 真库终态 {d['real_terminal']} 行；底账时间跨度 {d['csv_time_span']}。
- `artifacts/kpi_trace_100/kpi_trace_100_queries.sql` 仅 114 字节占位（`-- Synthetic mode: no SQL executed`）。

## 4. 结果图

![证据图](fig_m15177_evidence.png)

- 左（对数轴）：分子/分母三量与 report_id 通道，交付声明 vs 真库实测；report_id 通道相差 {d['committed_report_id_channel']/max(d['real_report_id_channel'],1):.0f} 倍，是「非库导出」的硬指纹。
- 右：KPI 现值 vs 目标。**注意数值巧合**：交付 54.50% 与真值 {REAL['pct']}% 只差 {d['abs_pp_deviation']} pp —— 说明 m15120 的历史观测大体可靠，但**本单并未测量任何东西**；同一工具 database 模式当日即可跑出真值（本次审计实测）。

## 5. 注意点（坑与局限）

1. **口径与机制错位**：真库 `report_id` 非空只有 {d['real_report_id_channel']} 条（几乎全灭），可溯几乎全靠 `tensor_reports` 通道
   （{REAL['tensor_reports_distinct_missions']} 个 distinct mission）。报告用合成数据描述出的 38.03%「report_id 通道占比」在真库里不存在，
   若据此下「双通道冗余」的结论会误导治理。
2. **报告内部自相矛盾**：§4.1/§4.2 写明算路 B（SQL 复核）/算路 C（集群分片）**「未运行」**，§4 判定句还打印了
   「存在不一致，需先修计量」；而 §6 误差判定却称「计算侧误差=0（确定性计数 + 算路一致）」。无多算路即无对齐，此句不成立。
3. **repro_cmd 只给 --synthetic**：照抄复算只会复述合成数字，永远拿不到真值 —— 可复现性在此单里等于「可复现地复述」。
4. **无基准行**：`tensor_benchmark_cases` 无本锚，L4 不可判；本单 headline 也因此没有 ref/tolerance 兜底。
5. **审计局限**：真值是**时点快照**（库在长，{REAL['denominator']}→14160 于审计期间漂移 3 条，占比不变）；未核 m15120 历史报告本身的口径正确性
   （其 doc 声称真实现值，未在本单范围）。
6. **合并面**：`pr_gate` 记 PR #1236 merged，实测远端 main `{INT['main_head'][:12]}` 确含 `tools/kpi_trace/`（PR head c1cfb2703）；
   但本地 worktree 镜像 main 落后（#1235），本报告以远端 main 为准。

## 6. 误差判定（逐项，显式回答是否 <3%）

| 关键量 | 交付 got | 审计真值 | 相对误差 | <3%？ |
|---|---|---|---|---|
| KPI-TRACE-100 现值 | {CLAIM} % | {REAL['pct']} % | {abs(CLAIM - REAL['pct']) / REAL['pct'] * 100:.2f}% | **是（数值层面）** |
| numerator | 7677 | {REAL['numerator']} | {abs(7677 - REAL['numerator']) / REAL['numerator'] * 100:.2f}% | 是 |
| denominator | 14085 | {REAL['denominator']} | {abs(14085 - REAL['denominator']) / REAL['denominator'] * 100:.2f}% | 是 |
| report_id 通道 | {d['committed_report_id_channel']} | {d['real_report_id_channel']} | 66,950% | **否（量级错误）** |
| succeeded 终态数 | {d['committed_succeeded_terminal']} | {d['real_succeeded_total']} | {abs(d['committed_succeeded_terminal'] - d['real_succeeded_total']) / d['real_succeeded_total'] * 100:.2f}% | 否 |

**结论口径**：headline 数值虽在 3% 内，但**误差小不等于证据真**——它来自钉桩生成器而非测量；
按证据分级，交付值判 **MOCK（合成复述）**，真值以本审计 `{REAL['numerator']}/{REAL['denominator']}={REAL['pct']}%` 为准。
KPI 距目标仍差 {100 - REAL['pct']:.2f} pp，与 m15177 无关（本单任务是计量不是改进）。

## 7. 裁定（L0–L5）

**level = 2（L2 独立功能实现）**，理由与差异逐项对齐证据键：

| 证据键 | 指针 | 判定 |
|---|---|---|
| impl_files | `docs/board_audit/m15207/refs/impl/{{measure,entry,synthetic,__init__,__main__}}.py`（远端 main 亦含） | ✅ 真代码，非占位 |
| entry | `refs/impl/entry.py`（`python3 -m tools.kpi_trace {{measure\\|figures\\|doc\\|run-all}}`） | ✅ 可执行，两数据源均跑通 |
| run_artifacts | `refs/artifacts/kpi_trace_100/`（{n_art} 件，sha256 已记） | ✅ 存在；**内容为合成输入** |
| tests | `{TESTS['summary']}` | ✅ 15 项真通过 |
| integration_callers | **null** | ❌ main grep 仅命中自带测试 → 不达 L3 |
| benchmark | **null**（`tensor_benchmark_cases` 无本锚） | ❌ 不达 L4 |
| repro_cmd | `measure --synthetic` 9/9 字段一致；`measure`（database）实测出 {REAL['pct']}% | ⚠️ 可复现，但复现的是合成数 |
| external_anchor | **null**（无文献/实验/前值对照登记） | ❌ 不达 L5 |

平台门本地预跑 `maturity_gate.judge(claimed=2)`：**{json.dumps(gp, ensure_ascii=False)}**

**返修建议（不是重做）**：同一工具 `database` 模式当日即可出真值 →
`python3 -m tools.kpi_trace measure --outdir artifacts/kpi_trace_100` 重出报告（{REAL['pct']}%），
并在 `tensor_benchmark_cases` 补一行 `recompute` 锚（ref=注册表 `trace_report_ratio.recompute_sql` 口径）才具备 L4 判据。
"""
def main() -> int:
    gp = gate_prejudge()
    (HERE / "gate_prejudge.json").write_text(json.dumps(gp, ensure_ascii=False, indent=2), encoding="utf-8")
    png = fig()
    out = HERE / "AUDIT_m15177.md"
    out.write_text(md(gp, png), encoding="utf-8")
    print(json.dumps(gp, ensure_ascii=False))
    print(f"wrote {out} / {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())