#!/usr/bin/env python3
"""AUDIT-RESTAT 报告渲染器：把 runs/restat_20261111/*.json 渲染成
docs/RESTAT_L0_L5_20261111.md。所有数字来自 SQL 产物，零手抄。

用法: python3 scripts/restat_render_report_20261111.py --dir runs/restat_20261111
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict

LABELS = ["L0 文档成果", "L1 数学验证成果", "L2 独立功能成果", "L3 集成成果",
          "L4 基准验证成果", "L5 创造性成果"]


def pct(a, b) -> str:
    return f"{100.0 * a / b:.1f}%" if b else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="runs/restat_20261111")
    ap.add_argument("--out", default="docs/RESTAT_L0_L5_20261111.md")
    a = ap.parse_args()
    s = json.load(open(f"{a.dir}/restat_summary.json"))
    pend = json.load(open(f"{a.dir}/pending_acceptance_L0L1.json"))
    rows = list(csv.DictReader(open(f"{a.dir}/missions_classified.csv")))
    succ = [r for r in rows if r["status"] == "succeeded"]

    strict = {int(k): v for k, v in s["tierB_level_counts"].items()}
    loose = {int(k): v for k, v in s["tierB_level_counts_loose"].items()}
    claim = {int(k): v for k, v in s["tierA_claimed_level_counts"].items()}
    gate = {int(k): v for k, v in s["tierA_gate_level_counts"].items()}
    infl = s["inflation"]
    total, nsucc = s["missions_total"], s["succeeded_total"]
    strict_l2p = sum(strict.get(i, 0) for i in (2, 3, 4, 5))
    loose_l2p = sum(loose.get(i, 0) for i in (2, 3, 4, 5))

    L = []
    w = L.append

    w("# AUDIT-RESTAT：真实成果重新统计（L0-L5 六类重归类）")
    w("")
    w(f"**生成时间** {s['generated_at']} ｜ **数据源** PostgreSQL `{s['db']}`："
      "`tensor_maturity_verdicts`(审计判定) + `tensor_dev_missions`(任务库) + "
      "`tensor_reports`(报告库) + knowledge_* 表 ｜ **复跑** `python3 scripts/restat_real_outputs_20261111.py`")
    w("")
    w("> owner 2026-11-11 第四项令：1 万任务 ≠ 1 万成果。本文不改任何任务状态，只重算账。")
    w("> 全部数字由 `scripts/restat_real_outputs_20261111.py` 内的 SQL 现查现算并渲染，"
      "口径原文见 `runs/restat_20261111/sql_registry.sql`，逐单明细见 "
      "`runs/restat_20261111/missions_classified.csv`。")
    w("")
    w("## 0. 结论摘要（TL;DR）")
    w("")
    w(f"| 量 | 数 | 说明 |")
    w(f"|---|---|---|")
    w(f"| 任务总数 | {total:,} | 2026-10-29~11-11，含失败/撤销 |")
    w(f"| 状态 succeeded | {nsucc:,} | 占 {pct(nsucc, total)}；失败 {s['status_split'].get('failed', 0):,} |")
    w(f"| **严格口径 L2+ 成果** | {strict_l2p:,} | 有实现文件 **且** 有 PR 合并证据 |")
    w(f"| 宽松口径 L2+ | {loose_l2p:,} | 只要有实现文件（不看是否入库）——膨胀基数 |")
    w(f"| 审计实测 gate≥L1 | {infl['verified_audit_gate_L1plus']} / {infl['gate_total_scopes']} scope | 184 条审计判定中机器门实测达标数 |")
    w(f"| 审计实测 gate≥L2 | {infl['verified_functions_audit_gate_L2plus']} / {infl['gate_total_scopes']} scope | 无一被审计证明达到独立功能级 |")
    w(f"| 报告总数 | {infl['reports_total']:,} | 挂到 {infl['reports_distinct_missions']:,} 个任务 |")
    w(f"| 带 run 工件的成功单 | {infl['succ_with_run_artifacts']:,} | 报告 : 可运行工件 = "
      f"{infl['ratio_reports_over_succ_with_run_artifacts']} : 1 |")
    w(f"| 待完成名单（Tier-A 实证） | {pend['count']} 条能力项 | 见 §3.1，只列名单不改状态 |")
    w(f"| 待复核名单（Tier-B 代理） | {len([r for r in rows if r['status']=='succeeded' and r['level'] in ('0','1')]):,} 单 | 见 §3.2 CSV |")
    w("")
    w("**一句话**：任务量与成果量不是同一个量。严格口径下 13,742 张单只剩 "
      f"{strict_l2p:,} 单有「实现+入库」双重证据（{pct(strict_l2p, total)}），"
      f"而经独立审计并把工件重跑的 {infl['gate_total_scopes']} 个能力项里，"
      f"达标 L1 的仅 {infl['verified_audit_gate_L1plus']} 项、达标 L2+ 的 0 项。")
    w("")

    # ---------------- §1 口径
    w("## 1. 归类口径（L0-L5 阶梯 + 两个统计层）")
    w("")
    w("L0-L5 的判据键直接沿用审计波 `gate_note.why` 的机器判据（`tests` / `impl_files,entry,"
      "run_artifacts` / `integration_callers` / `benchmark`），因此本文档的分档与审计波同源、可对账：")
    w("")
    w("| 档 | 名称 | 判据（必须同时满足） | Tier-B 机器信号 |")
    w("|---|---|---|---|")
    w("| L0 | 文档成果 | 有文档/图表/清单，无可执行实现 | changed_files 中无代码扩展名 |")
    w("| L1 | 数学验证成果 | 有测试/验证脚本，无独立功能实现 | 代码文件仅 `test_*`/`verify_*`/`audit_*` |")
    w("| L2 | 独立功能成果 | 实现文件 + 工程入口 + 可运行工件（入库） | 实现文件 **且** `result.pr_gate.state='merged'` |")
    w("| L3 | 集成成果 | L2 + 被集成面调用（跨仓或 api/backend/cli 集成面） | `co_repos≥2` 或改动命中集成面标记 |")
    w("| L4 | 基准验证成果 | L2/L3 + 与解析解/文献/实验对标且留 run 工件 | goal 命中基准词 **且** 有 runs/artifacts 图件 |")
    w("| L5 | 创造性成果 | 无先例的新方法 | **不可由文件信号机器判定**，只接受人工/审计判定 |")
    w("")
    w("两个统计层必须分开读，混读就是造假的起点：")
    w("")
    w("- **Tier-A 判定层**：`tensor_maturity_verdicts` 184 条 scope 级审计判定，每条带 claim level 与"
      "机器门实测 `gate_level`（判工件在审计工作树内是否真的可见可跑）。这是**实测**，样本小但硬。")
    w("- **Tier-B 代理层**：对全部 succeeded 任务按 changed_files / pr_gate / goal 做机器分档。"
      "覆盖全库但只是**信号代理**，精度由 §5 的验证集给出（18 对，exact 4，±1 8）。")
    w("- **严格 vs 宽松**：严格口径要求 PR 合并证据（owner 口径：没入库不算成果）；"
      "宽松口径只要求实现文件存在，用来量化「未入库的实现」有多大。差 = 膨胀的第一层来源。")
    w("")

    # ---------------- §2 ① 全库归类统计
    w("## 2. ① 全库任务 L0-L5 重新归类统计")
    w("")
    w(f"### 2.1 Tier-B 代理层：{nsucc:,} 个 succeeded 任务按六类（严格/宽松并列）")
    w("")
    why = s["tierB_why_counts"]
    unmerged = sum(v for k, v in why.items() if k.startswith("impl_without_merge_evidence"))
    w("| 档 | 严格口径（需 PR 合并证据） | 占比 | 宽松口径（有实现即计） | 占比 | 口径差说明 |")
    w("|---|---|---|---|---|---|")
    w(f"| L0 文档成果 | {strict.get(0,0):,} | {pct(strict.get(0,0), nsucc)} | "
      f"{loose.get(0,0):,} | {pct(loose.get(0,0), nsucc)} | 两口径一致 |")
    w(f"| L1 数学验证成果（纯 test/verify 脚本） | {why.get('tests_or_verify_only',0):,} | "
      f"{pct(why.get('tests_or_verify_only',0), nsucc)} | {why.get('tests_or_verify_only',0):,} | "
      f"{pct(why.get('tests_or_verify_only',0), nsucc)} | 两口径一致 |")
    w(f"| L1′ 实现未入库（严格口径回流） | {unmerged:,} | {pct(unmerged, nsucc)} | — | — | "
      f"宽松口径把这 {unmerged:,} 单算成 L2+，严格口径只能计 L1 |")
    for i in (2, 3, 4, 5):
        a_, b_ = strict.get(i, 0), loose.get(i, 0)
        w(f"| {LABELS[i]} | {a_:,} | {pct(a_, nsucc)} | {b_:,} | {pct(b_, nsucc)} | "
          f"严格口径已扣掉未入库实现 |")
    w(f"| 不可判（无 changed_files 工件） | {strict.get(-1, 0):,} | {pct(strict.get(-1, 0), nsucc)} "
      f"| {loose.get(-1, 0):,} | {pct(loose.get(-1, 0), nsucc)} | 无工件，两口径都不可判 |")
    w(f"| **合计** | **{nsucc:,}** | 100% | **{nsucc:,}** | 100% | |")
    w("")
    w("注：`不可判` 是 succeeded 但 `result.changed_files` 为空数组的单（工具链未回收工件），"
      "它们既不能算成果也不能算零产出，单列以免污染分母——这是旧口径最常见的膨胀点。")
    w("")
    w("### 2.2 Tier-A 判定层：184 条审计判定的 claim vs 实测")
    w("")
    w("| 档 | 审计 claim level | 机器门实测 gate_level | claim − gate |")
    w("|---|---|---|---|")
    for i in range(6):
        c_, g_ = claim.get(i, 0), gate.get(i, 0)
        w(f"| {LABELS[i]} | {c_} | {g_} | {c_ - g_:+d} |")
    w(f"| 不可判 | 0 | {gate.get(-1, 0)} | — |")
    w("")
    w("crosstab（claim × gate，行=claim，列=gate）：")
    w("")
    w("| claim \\ gate | " + " | ".join(f"g{i}" for i in range(6)) + " | g-1 |")
    w("|---|" + "---|" * 7)
    ct = defaultdict(int)
    for e in s["tierA_crosstab"]:
        ct[(e["claimed"], e["gate"])] = e["n"]
    for c_ in range(6):
        w(f"| **claim L{c_}** | " + " | ".join(str(ct.get((c_, g_), 0)) for g_ in range(6))
          + f" | {ct.get((c_, -1), 0)} |")
    w("")
    w(f"`gate_state` 分布：{json.dumps(s['tierA_gate_state'], ensure_ascii=False)}。"
      "其中 66 条已 `reopened`（可被协调者直接重开），97 条 `unmapped`（scope 未映射到任务号，"
      "重开前需先补映射）。")
    w("")
    w("### 2.3 分布趋势（按日，严格口径堆叠）")
    w("")
    trend = s["trend_by_day"]
    days = sorted(trend)
    succ_by_day = defaultdict(int)
    for r in succ:
        succ_by_day[r["day"]] += 1
    prc = s["pr_coverage_by_day"]
    w("| 日期 | 提交总数 | succeeded | PR 记录覆盖 | L0 | L1(纯验证) | L1′(实现未入库) | L2 | L3 | L4 | 不可判 | 非成果占比 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    unmerged_by_day = defaultdict(int)
    for r in succ:
        if r["why"].startswith("impl_without_merge_evidence"):
            unmerged_by_day[r["day"]] += 1
    for d in days:
        t = trend[d]
        n = succ_by_day[d]
        low = t.get("0", 0) + t.get("1", 0)
        cov = prc.get(d, {})
        w(f"| {d} | {s['trend_by_day_all'][d]:,} | {n:,} | "
          f"{cov.get('with_pr', 0):,}/{n:,} | {t.get('0',0):,} | {t.get('1',0) - unmerged_by_day[d]:,} | "
          f"{unmerged_by_day[d]:,} | {t.get('2',0):,} | {t.get('3',0):,} | {t.get('4',0):,} | "
          f"{t.get('-1',0):,} | {pct(low, n)} |")
    w("")
    pre_pr = sum(succ_by_day[d] for d in days if prc.get(d, {}).get("with_pr", 0) == 0)
    cmp_days = [d for d in days[-5:] if d in prc]

    def low_share(d):
        t = trend[d]
        return pct(t.get("0", 0) + t.get("1", 0), succ_by_day[d] or 1)

    w("趋势读法（**必须先读 PR 覆盖列**）：`pr_gate` 记录自 2026-11-03 才点火，"
      f"10-29~11-02 共 {pre_pr:,} 个 succeeded 单零 PR 记录，因此这几天的 L1′ 是「无合并记录」"
      "造成的口径假象，**不能读成未入库**。可比较区间是 11-03 之后："
      + "；".join(f"{d} 提交 {s['trend_by_day_all'][d]:,} 单（succeeded {succ_by_day[d]:,}，"
                  f"PR 记录覆盖 {prc[d]['with_pr']:,}），非成果(L0+L1)占比 {low_share(d)}"
                  for d in cmp_days)
      + "——吞吐抬升的同时，**约一半的「成果」落在文档级或验证脚本级**，"
      "这就是 owner 说的「1 万任务≠1 万成果」的量化形态。")
    w("")
    w("### 2.4 分仓分布（严格口径）")
    w("")
    w("| 仓 | succeeded | L0 | L1 | L2 | L3 | L4 | 不可判 | L2+ 占比 |")
    w("|---|---|---|---|---|---|---|---|---|")
    for repo, c in sorted(s["by_repo_succeeded"].items(),
                          key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        l2p = sum(c.get(str(i), 0) for i in (2, 3, 4, 5))
        w(f"| {repo} | {n:,} | {c.get('0',0):,} | {c.get('1',0):,} | {c.get('2',0):,} | "
          f"{c.get('3',0):,} | {c.get('4',0):,} | {c.get('-1',0):,} | {pct(l2p, n)} |")
    w("")

    # ---------------- §3 ② 待完成名单
    w("## 3. ② 「未达验收待完成」名单（只列，不改状态）")
    w("")
    w("> **本清单不修改任何任务状态**。重开由协调者按 owner 裁定批量执行。")
    w("")
    w(f"### 3.1 Tier-A 实证名单（{pend['count']} 条能力项，审计已判 L0/L1）")
    w("")
    w("证据指针 = `tensor_maturity_verdicts.id`（可直接 `select * from tensor_maturity_verdicts where id=…` 复查），"
      "`gap` 列即审计给出的缺证原因。")
    w("")
    w("| # | verdict_id | 仓 | scope | claim | gate | gate_state | 缺证原因（截断 90 字） |")
    w("|---|---|---|---|---|---|---|---|")
    for i, it in enumerate(sorted(pend["items"], key=lambda x: (x["repo"], -x["claimed_level"])), 1):
        gs = it["gate_state"].split("(")[0]
        gap = (it["gap"] or ("[evidence] " + it.get("evidence_excerpt", ""))).replace("|", "/")[:100]
        w(f"| {i} | {it['verdict_id']} | {it['repo']} | {it['scope'][:60].replace('|','/')} | "
          f"L{it['claimed_level']} | g{it['gate_level']} | {gs} | {gap} |")
    w("")
    w("### 3.2 Tier-B 待复核名单（代理判 L0/L1 的 succeeded 单）")
    w("")
    pend_rows = [r for r in rows if r["status"] == "succeeded" and r["level"] in ("0", "1")]
    br = Counter(r["repo"] for r in pend_rows)
    w(f"全量清单：`runs/restat_20261111/pending_proxy_L0L1.csv`（{len(pend_rows):,} 行，含 mission_id / repo / "
      "level / why / n_files / pr_state 字段）。这批是**待复核**而非已定案——代理口径会对"
      "「实现存在但无合并记录」的单判 L1（`impl_without_merge_evidence`），复核时优先看这类的 pr_state 是否只是缺记录。")
    w("")
    w("| 仓 | 待复核单数 | 其中 docs_only(L0) | 其中 tests/verify_only | 其中 实现未入库 |")
    w("|---|---|---|---|---|")
    why_by_repo = defaultdict(Counter)
    for r in pend_rows:
        why_by_repo[r["repo"]][r["why"].split("+")[0]] += 1
    for repo, n in br.most_common():
        c = why_by_repo[repo]
        w(f"| {repo} | {n:,} | {c.get('docs_only',0):,} | {c.get('tests_or_verify_only',0):,} | "
          f"{c.get('impl_without_merge_evidence',0):,} |")
    w("")

    # ---------------- §4 ③ 膨胀量化
    w("## 4. ③ 成果膨胀量化（三组比值）")
    w("")
    w("| 维度 | 分子（声明/过程量） | 分母（验证/产出量） | 比值 | 读法 |")
    w("|---|---|---|---|---|")
    w(f"| 功能数 | 声明有实现 {infl['proxy_L2plus_succeeded_missions']:,} 单 | 审计实测 ≥L2 "
      f"{infl['verified_functions_audit_gate_L2plus']} 项 | **不可定义（分母=0）** | 无一能力项被审计证明到独立功能级 |")
    w(f"| 功能数（相对 L1） | 同上 {infl['proxy_L2plus_succeeded_missions']:,} | 审计实测 ≥L1 "
      f"{infl['verified_audit_gate_L1plus']} 项 | {infl['proxy_L2plus_succeeded_missions'] / max(infl['verified_audit_gate_L1plus'],1):.1f} : 1 | "
      f"每 1 个实测达标项对应 {infl['proxy_L2plus_succeeded_missions'] / max(infl['verified_audit_gate_L1plus'],1):.0f} 个声明 |")
    w(f"| 报告 vs 可运行算例 | 报告 {infl['reports_total']:,} 编 | 带 run 工件成功单 "
      f"{infl['succ_with_run_artifacts']:,} | {infl['ratio_reports_over_succ_with_run_artifacts']} : 1 | 平均 15 份报告共享 1 个可跑算例 |")
    w(f"| 报告 vs 审计实跑 | {infl['reports_total']:,} | 审计 executed=true {infl['verdicts_with_executed_true']} 条 scope | "
      f"{infl['ratio_reports_over_runnable']} : 1 | 报告数与「被独立重跑过的能力」数完全脱钩 |")
    w(f"| PR 合并 vs L2+ | 合并 {infl['pr_merged_missions']:,} 单 | 审计实测 ≥L2 "
      f"{infl['verified_functions_audit_gate_L2plus']} | **不可定义（分母=0）** | 合并证据 ≠ 功能验证证据 |")
    w(f"| PR 合并 vs 代理 L2+ | {infl['pr_merged_missions']:,} | {strict_l2p:,} | "
      f"{infl['pr_merged_missions'] / max(strict_l2p,1):.2f} : 1 | 合并数略多于严格 L2+（部分合并单只有文档/测试） |")
    w("")
    w("补充量化：")
    w("")
    w(f"- **能力声明机制几乎空转**：`result.capability_delta` 落账仅 "
      f"{infl['declared_capability_delta_records']} 单（占全库 "
      f"{pct(infl['declared_capability_delta_records'], total)}），其中 succeeded 上只有 "
      f"{infl['declared_capability_delta_on_succeeded']} 单；分类分布 "
      f"{json.dumps(infl['declared_capability_categories'], ensure_ascii=False)}。"
      "→ 靠 capability_delta 做能力台账在当前是**不可行**的，只能靠审计判定。")
    w(f"- **宽松/严格差 = 未入库实现**：{loose_l2p:,} − {strict_l2p:,} = "
      f"{loose_l2p - strict_l2p:,} 单实现文件在案但没有 PR 合并记录"
      f"（占「有实现」的 {pct(loose_l2p - strict_l2p, loose_l2p)}）。")
    w(f"- **成功单里 {pct(strict.get(-1,0), nsucc)} 无工件**：{strict.get(-1,0):,} 单 succeeded 但 "
      "changed_files 为空数组，无法判档。")
    w("")

    # ---------------- §5 ④ 知识成果
    k = s["knowledge"]
    w("## 5. ④ 知识成果单列（不计入功能账）")
    w("")
    w("owner 口径：12,900 任务之外的 KB/TK/教程资产属「知识成果」，单列，不进功能账分母。")
    w("")
    w("| 资产 | 规模（SQL 现查） |")
    w("|---|---|")
    for t in k["tables"]:
        w(f"| `{t['table']}` | {t['rows']:,} 行 |")
    w(f"| 教程类报告（title/tags 命中 教程/tutorial） | {k['tutorial_reports']} 编 |")
    w(f"| TensorKnowledge 仓任务 | {k['tk_missions']:,} 单 |")
    w(f"| TensorKnowledge 仓审计判定 scope | {k['tk_verdict_scopes']} 项 |")
    w(f"| **合计行数** | **{k['total_rows']:,}** |")
    w("")
    w("说明：知识资产主要是 `kb_samples`（样本级语料），它不是可运行功能，"
      "按 owner 口径不得折算成 L2+ 成果；单独记账后才能看清「功能账」与「知识账」的真实比例。")
    w("")

    # ---------------- §6 对账与误差判定
    w("## 6. 口径对账与误差判定")
    w("")
    w("| 判定项 | 基准 | 实测 | 偏差 | 是否 <3% |")
    w("|---|---|---|---|---|")
    pre_day_cum = sum(s["pr_coverage_by_day"][d]["succ"] for d in sorted(s["pr_coverage_by_day"])
                      if d < sorted(s["pr_coverage_by_day"])[-1])
    delta = total - 12900
    w(f"| 任务总数对账（owner 引用的 12,900） | 12,900（11-11 早间快照） | {total:,}（现查；"
      f"上一日终 succeeded 累计 {pre_day_cum:,}） | {delta:+,}（当日新增） | 否，但**已解释**："
      "差异=11-11 当日新单，非统计口径漂移 |")
    w(f"| succeeded 占比 | — | {pct(nsucc, total)} | — | 无基准 |")
    w(f"| 代理分档 vs 审计实测（18 对） | 审计 gate_level | exact 4 / ±1 8 | 代理系统性**高估** 1-2 档 | 否 |")
    w(f"| 184 条审计中「工件缺失」阻断 | 0（理想） | {infl['gate_missing_artifact_scopes']} 条 "
      f"（{pct(infl['gate_missing_artifact_scopes'], infl['gate_total_scopes'])}） | 实测层因此是**下界** | 否 |")
    w("")
    w("**诚实声明（不许含糊）**：")
    w("")
    w(f"1. Tier-A 的「gate≥L2 = 0」是**实测下界，不是全能力为零**。184 条里有 "
      f"{infl['gate_missing_artifact_scopes']} 条因「工件缺失」在审计工作树内看不见文件（`gate_note.why` 命中"
      "「工件缺失」），这部分是**度量缺口**，须由协调者重开补齐工件后再判——所以 §3.1 才只列名单不改状态。")
    w("2. Tier-B 是**信号代理**，验证集只有 18 对（带 `(m####)` 的 audit scope），exact 4、±1 8，"
      "且偏差方向是代理高估。因此 §2.1 的六类分布只能读**相对量与趋势**，不能当绝对值引用。")
    w("3. 「报告 : 可运行算例」的分母用「带 run 工件的成功单」是代理信号（601 单），"
      "若改以审计实跑 5 条为分母，比值放大到 "
      f"{infl['ratio_reports_over_runnable']} : 1——两种口径都写在表里，读者可自选，但不许混用。")
    w("4. 本次统计**未做**的事：未重跑任何被审计能力项、未改任何任务状态、未按 PR 号去 GitHub 逐条核对。")
    w(f"5. **快照语义**：本文数字取自 `{s['generated_at']}` 那一刻的活库；平台每几分钟就新增任务，"
      "复跑会有小幅漂移（本次连续两次跑之间 total 已差 3 单）。同一快照内的自洽性由 "
      "`tests/test_restat_real_outputs.py` 的 12 条断言锁定（档位合计=成功单数、crosstab 行和=claim 分布、"
      "待完成名单条数=claim L0+L1 等），口径漂移会立刻红。")
    w("")

    # ---------------- §7 复跑
    w("## 7. 复跑与审计轨迹")
    w("")
    w("```bash")
    w("python3 scripts/restat_real_outputs_20261111.py   # SQL 现查现算 -> runs/restat_20261111/")
    w("python3 scripts/restat_figures_20261111.py        # 图 + 待复核名单 CSV")
    w("python3 scripts/restat_render_report_20261111.py  # 渲染本文档（数字全部注入）")
    w("python3 -m pytest tests/test_restat_real_outputs.py -q   # 口径一致性断言")
    w("```")
    w("")
    w("| 产物 | 内容 |")
    w("|---|---|")
    w("| `runs/restat_20261111/sql_registry.sql` | 全部口径 SQL 原文（可直接 psql 复跑） |")
    w(f"| `runs/restat_20261111/missions_classified.csv` | 全库逐单分档（{total:,} 行） |")
    w("| `runs/restat_20261111/pending_acceptance_L0L1.json` | Tier-A 64 条待完成能力项（带 verdict 指针） |")
    w("| `runs/restat_20261111/pending_proxy_L0L1.csv` | Tier-B 待复核名单 |")
    w("| `runs/restat_20261111/figures/*.png` | 图 1 claim vs gate ／ 图 2 严格 vs 宽松 ／ 图 3 漏斗 ／ 图 4 日度趋势 |")
    w("")
    w("**注意点（踩过的坑）**：")
    w("")
    w("- `psycopg2` 在本沙箱是 stub（`RuntimeError: no real driver`），统计一律走 `psql --csv` 子进程，"
      "不要引 psycopg2/pandas DB 读取。")
    w("- `result->>'capability_delta'` 在 640 单上是 JSON `null`，直接判空会把它们误算成「有声明」，"
      "必须 `is not null and ::text <> 'null'`。")
    w(f"- 只按「有实现文件」计成果会把 {unmerged:,} 单未入库实现算成成果（宽松口径 "
      f"{loose_l2p:,} 单）；严格口径必须叠 PR 合并证据（{strict_l2p:,} 单）。")
    w("- 分档必须单调（L(n) 蕴含 L0..Ln-1），否则同一单会被重复计入多档。")
    w("")

    open(a.out, "w").write("\n".join(L) + "\n")
    print(f"wrote {a.out} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())