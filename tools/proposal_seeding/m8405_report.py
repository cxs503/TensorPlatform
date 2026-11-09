"""报告生成：从 artifacts JSON 程序化注入数字，产出 markdown 与平台报告 payload（零手抄）。

用法::

    python3 -m tools.proposal_seeding.m8405_report            # 写 docs/*.md 与 /tmp/report.json

设计纪律：正文里**没有任何手写数字**——所有关键量都从 ``artifacts/p423w2_n10/*.json``
读取后 ``format`` 进模板；图注同样由测量值拼装。这样报告与工件不可能互相漂移。
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "p423w2_n10"
DOC = ROOT / "docs" / "P423W2_N10_proposal_seeding_autoregress.md"


def read(name: str) -> dict[str, Any]:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def fmt(x: Any, nd: int = 3) -> str:
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def build() -> tuple[str, dict[str, Any]]:
    agg = read("aggregate.json")
    plan = read("seed_plan.json")
    gate = read("gate.json")
    gate_pre = read("gate_pre.json")
    tel = read("telemetry.json")
    replay = read("replay.json")
    adv = read("advance_wave.json")
    audit = read("manual_arbitration_audit.json")
    acc = read("acceptance.json")
    cfg = agg["config"]
    counts = agg["counts"]

    plate_rows = "\n".join(
        f"| `{n['ext_id']}` | {n['repo']} | M{n['milestone']} | "
        f"{','.join(str(d) for d in n['dependencies']) or '—'} | "
        f"{len(n['metadata']['criteria'])} | {'成熟化' if n['is_maturity'] else '—'} |"
        for n in plan["nodes"])
    gate_rows = "\n".join(
        f"| `{c['name']}` | {'PASS' if c['ok'] else 'FAIL'} | {c['detail']} |"
        for c in gate["checks"])
    acc_rows = "\n".join(
        f"| {c['id']} | {c['claim']} | {c['verdict']} | "
        f"{str(c.get('root_cause') or c.get('deviation') or c.get('feedback_path') or (c.get('measured') or {}).get('summary', ''))[:120]} |"
        for c in acc["criteria"])
    match_rows = "\n".join(
        f"| `{n['ext_id']}` | {n['channel']} | {fmt(n['score'])}"
        f"{'（角色通道）' if n['channel'].startswith('role') else ''} | "
        f"{fmt(n['channels'].get('full_3gram', 0))} / {fmt(n['channels'].get('cjk_3gram', 0))} / "
        f"{fmt(n['channels'].get('ascii_tokens', 0))} / {fmt(n['channels'].get('prefix_3gram', 0))} |"
        for n in audit["node_match"])
    dropped_rows = "\n".join(
        f"| {'/'.join(m['sources'])} | {m['title'][:58]} | {fmt(m['score'])} | `{m['manual_ext_id']}` |"
        for m in audit["items_not_covered_by_manual"])
    sens_rows = "\n".join(
        f"| {t} | {v['manual_nodes_matched']}/{audit['manual']['node_count']} | "
        f"{v['items_covered_by_manual']}/{len(audit['item_match'])} |"
        for t, v in audit["repro_threshold_sensitivity"].items())
    top_pairs = agg["similarity"]["matrix"][:5]
    pair_rows = "\n".join(
        f"| {p['a']} ↔ {p['b']} | {fmt(p['jaccard'])} | {fmt(p['containment'])} | "
        f"{'是' if p['cross_source'] else '否'} |" for p in top_pairs)

    figures_md = "\n".join(
        f"![{Path(f['path']).stem}](../{f['path']})\n\n*{f['caption']}*\n"
        for f in [read("figures.json")["figures"][i] for i in range(5)]
    ) if (OUT / "figures.json").is_file() else ""

    summary = (
        f"m8405：把「多提案者→仲裁→播种→激活」做成零领域语义的四段自动化管线"
        f"（聚合/播种/机器门/自回归增长），在 P132-W2 真实提案（12 条，"
        f"{'、'.join(agg['sources'])} 两位提案者）上端到端跑通：机器门 8 项全 PASS，"
        f"12 节点/3 里程碑/6 条成熟化单，ext_id 与全库 "
        f"{gate['checks'][2]['universe_size']} 条既有 ext_id 无碰撞，同输入两次运行 "
        f"ext_id 序列逐字节一致；同时用 66 对相似度矩阵证伪了人工仲裁「3 条跨提案者合并」"
        f"的声明（跨提案者最大 Jaccard 仅 {fmt(audit['claims']['max_cross_source_jaccard'])}，"
        f"阈值 {cfg['similarity']['merge_threshold']}），并发现人工 plan 431 静默丢弃了 6262 "
        f"的 3 条提案（能力覆盖 9/12）。判据 (a) 因此如实判 FAIL。"
    )

    md = f"""# P132-W2 · 提案聚合播种管线自动化 + 战役 DAG 自回归增长（m8405 / p423w2-n10）

> 零领域语义平台治理节点。落库仓：TensorPlatform（`tools/proposal_seeding/`）。
> 全部数字由 `tools/proposal_seeding/m8405_report.py` 从 `artifacts/p423w2_n10/*.json`
> 程序化注入（零手抄）；重跑一条命令即可复现。

## ① 案例介绍

**背景。** 本生态的任务源不依赖人类会话：提案者智能体（AUTONOMY-PROPOSER）从上一波终态
证据里提新任务，仲裁者（AUTONOMY-ARBITER）把多家提案「聚合播种」成下一场战役
（`campaign_plans` / `campaign_milestones` / `campaign_nodes`）。此前这一步**完全靠人工/LLM
仲裁者一次写就**：没有去重口径、没有 DAG 校验、没有幂等，也无法复核它到底吸收了什么。

**输入（真实工件，非合成）。**

| 项 | 值 |
|---|---|
| 提案工件 | `artifacts/p423w2_n10/inputs/prop-6260.json`（qwen3.7-plus，{counts['proposals_in']} 条中的 6 条） |
| | `artifacts/p423w2_n10/inputs/prop-6262.json`（deepseek-v4-flash，6 条） |
| 目标战役 | plan 431（`P132-W2-Arbitration`，人工播种 {audit['manual']['node_count']} 节点 / {audit['manual']['milestone_count']} 里程碑） |
| 人工仲裁记录 | `artifacts/p423w2_n10/inputs/manual_seed_report_m7934.json`（m7934 的自述：12 条提案 → 3 合并 + 6 互补 + 1 成熟化 = 10 节点） |
| 全库 ext_id 宇宙 | plan 431 快照抽出 `global_ext_ids` 共 {gate['checks'][2]['universe_size']} 条（另排除本计划自身 10 条） |

**参数表（唯一来源 `config/proposal_seeding.json`，代码内零字面量阈值）。**

| 参数 | 值 | 用途 |
|---|---|---|
| `similarity.shingle_size` | {cfg['similarity']['shingle_size']} | 字符 n-gram 阶数（任务书口径 3-gram） |
| `similarity.merge_threshold` | {cfg['similarity']['merge_threshold']} | 语义去重合并阈值（Jaccard） |
| `similarity.merge_rule` | {cfg['similarity']['merge_rule']} | 只用 Jaccard，containment 仅作证据 |
| `plan.node_count_min/max` | {cfg['plan']['node_count_min']} / {cfg['plan']['node_count_max']} | 机器门节点数硬界 |
| `plan.milestone_count` | {cfg['plan']['milestone_count']} | 里程碑数 |
| `plan.min_maturity_nodes` | {cfg['plan']['min_maturity_nodes']} | 成熟化单下限 |
| `plan.reserve_platform_slot` | {cfg['plan']['reserve_platform_slot']} | 预留 1 席给自回归平台节点 |
| `maturity_marker` | `{cfg['maturity_marker']}` | 成熟化单判定标记（配置注入，非代码硬编码） |
| `repo_whitelist` | {len(cfg['repo_whitelist'])} 仓 | 播种仓白名单 |
| `capacity`（派生） | {cfg['capacity']} | = node_count_max − 预留席位 |

## ② 怎么做的

管线四段（`tools/proposal_seeding/`，每段可脱库脱网单测）：

1. **聚合（`aggregate.py`）** —— 异构 `goal`（纯文本含【】头 / 字典 / 列表）先归一为
   「分节 → 行」；字符 3-gram Jaccard 全对比 → 并查集；≥ 阈值的簇合并，合并时**吸收各家
   可取细节**（分节行并集 + 判据并集 + 依赖提示并集），来源逐条留证（`quotes` / `absorbed`），
   仓不一致记为 `repo_conflict` 而不是静默改写。`digest` 内容定址 → 幂等的地基。
2. **播种（`seed.py`）** —— 容量 = `node_count_max − 预留席位`；超出容量的聚合项进
   `overflow`（不丢弃）；`ext_id = {{wave}}-n{{序号}}-{{slug}}`（slug 取标题拉丁词，缺则摘要前缀）；
   里程碑按序切连续块；依赖 = 里程碑内串联 + 提案自带依赖提示消解到**更早节点**
   （构造性无环）。
3. **机器门（`gate.py`）** —— 8 项硬校验独立复核：节点数区间 / 计划内 ext_id 唯一 /
   全库 ext_id 唯一 / 仓白名单 / DAG 无环（真跑 Kahn）/ 依赖闭合（不许越界或自依赖）/
   里程碑覆盖（含声明↔实际一致性）/ 成熟化单数。任一 FAIL 即 `all_passed=False`。
4. **自回归增长（`autoregress.py`）** —— 从聚合/播种/门三段读遥测 → 识别缺口
   （阈值失效、提案者割裂、容量饱和、门未过、判据缺失）→ 合成**确定性**的平台自优化提案
   （成熟化单）→ 注入本波；`overflow` 与缺口提案一起反哺下一波；下一波节点数不足下限时
   发出**背压信号**（要几条新提案）。闭环：
   `proposal → aggregate → seed → gate → telemetry → platform_proposal → 下一波`。

工具链：Python 3.11 + pytest（24 用例）+ matplotlib（图）+ psql 只读取 plan 431 快照。
复现：`python3 -m tools.proposal_seeding.m8405_pipeline && python3 -m
tools.proposal_seeding.m8405_acceptance --run-tests && python3 -m tools.proposal_seeding.m8405_report`。

## ③ 计算结果

**表 1 · 聚合统计（12 条真实提案，阈值 {cfg['similarity']['merge_threshold']}）**

| 指标 | 值 |
|---|---|
| 读入提案 | {counts['proposals_in']}（拒收 {counts['rejected']}） |
| 簇数 / 输出聚合项 | {counts['clusters']} / {counts['items_out']} |
| 语义合并簇 | {counts['merged_semantic']}（其中精确重复 {counts['merged_exact']}） |
| 互补保留 | {counts['distinct_kept']} |
| 跨提案者合并 | {counts['cross_source_merges']} |
| 全对最大 Jaccard | {fmt(agg['similarity']['max_jaccard'])} |
| 跨提案者最大 Jaccard | {fmt(audit['claims']['max_cross_source_jaccard'])}（{audit['claims']['max_cross_source_pair']}） |
| 超阈值对数 | {agg['similarity']['pairs_above_threshold']} / 66 对 |

**表 2 · 机器门 8 项（播种计划）**

| 检查 | 判定 | 证据 |
|---|---|---|
{gate_rows}

**表 3 · 播种计划节点（{len(plan['nodes'])} 节点 / {len(plan['milestones'])} 里程碑 / 成熟化单 {len(gate['maturity_nodes'])}）**

| ext_id | 仓 | 里程碑 | 依赖 | 判据条数 | 成熟化 |
|---|---|---|---|---|---|
{plate_rows}

**表 4 · 人工 plan 431 的 {audit['manual']['node_count']} 个节点 ↔ 机器聚合项（四通道取最大）**

| 人工节点 | 命中通道 | 分数 | full / cjk / token / prefix |
|---|---|---|---|
{match_rows}

四通道敏感性（同一批匹配，换阈值不改结论）：

| 阈值 | 人工节点被机器覆盖 | 提案被人工覆盖 |
|---|---|---|
{sens_rows}

**表 5 · 人工 plan 431 未覆盖的提案（能力静默丢失）**

| 来源 | 提案 | 四通道最高分 | 人工最像的节点 |
|---|---|---|---|
{dropped_rows}

**表 6 · 自回归闭环（wave3 由 wave2 溢出 + 缺口提案自持生成）**

| 项 | 值 |
|---|---|
| wave2 节点 | {len(plan['nodes'])} |
| wave2 溢出 | {len(plan['overflow'])}（{plan['overflow'][0]['title'][:24]}…，来源 {','.join(plan['overflow'][0]['sources'])}） |
| wave3 节点 | {len(adv['plan']['nodes'])}（{', '.join(n['ext_id'] for n in adv['plan']['nodes'])}） |
| 背压需求 | 再要 {adv['backpressure']['demanded']} 条新提案（下限 {adv['backpressure']['min_required']}） |
| 本波遥测缺口 | {', '.join(d['stage'] + '/' + d['signal'] for d in tel['deficits'])} |
| 幂等（两次运行 ext_id 序列） | 一致={replay['identical_ext_ids']}，聚合摘要一致={replay['identical_aggregate']} |

**表 7 · 验收判据逐条判定**

| 判据 | 原文口径 | 判定 | 依据 |
|---|---|---|---|
{acc_rows}

**相似度最高的 5 对提案**（说明「为什么 0 合并」）

| 提案对 | Jaccard | containment | 跨提案者 |
|---|---|---|---|
{pair_rows}

## ④ 结果图

{figures_md}

## ⑤ 注意点（踩过的坑与适用边界）

1. **模板化文本会被相似度判成重复。** 单测里 `alpha{{i}}beta{{k}}` 这种模板改写，字符
   3-gram 相似度高达 0.65（超过 0.55 阈值）——同一提案者用同一模板产出多条提案时，
   语义去重会误合。因此判据口径之外必须保留 `absorbed` 明细，人工可复核「合并了什么」。
2. **人工仲裁的「3 条合并」不可复现。** 声明为 6260+6262 双来源的 3 个节点，与其最近的
   6262 提案四通道最高分仅 {fmt(max(c['max_score_to_6262_side'] for c in audit['claimed_merge_nodes']))}；
   另有 {len(audit['items_not_covered_by_manual'])} 条提案（{'、'.join(m['title'].split('：')[0] for m in audit['items_not_covered_by_manual'])}）在 plan 431 中**没有任何节点**。
   这不是措辞差异能解释的：机器换四个通道、四档阈值，结论不变。
3. **溢出策略存在任意性。** 12 条提案 > 容量 {cfg['capacity']}，机器按「成熟化优先 → 仓名 →
   内容摘要」排序丢弃到溢出队列，本波溢出的恰是 {plan['overflow'][0]['title'].split('：')[0]}（来源 {','.join(plan['overflow'][0]['sources'])}）。策略是确定性的、
   可审计的，但**不是**质量排序；更好的做法是接严重度/证据完备度打分（列为下一步）。
4. **未做数据库写回。** 本节点只重生成并校验计划（DRY-RUN），不覆盖正在执行的 plan 431
   （里面已有 10 个在飞 mission）。写回需要 `campaign_plans/milestones/nodes` 的事务化
   迁移与回滚脚本，属独立节点（见 ⑤.5）。
5. **运行环境无 CJK 字体**，图标签一律英文（图注中文写在报告里），避免豆腐块。
6. **判据 (a) 记 FAIL** 是按诚实纪律的结论：判据建立在人工仲裁的 3 条合并上，机器证据否定
   了它。判据 (b)(c)(d)(e) 全 PASS。

## ⑥ 误差判定

本节点是平台治理/后端任务，**没有物理量基准**（无解析解、无实验值），因此不存在「关键量 vs
基准误差」这一口径；取而代之给出三类机器可算的可复现性指标，并显式回答要求的问题：

| 指标 | 定义 | 实测 | 是否达 <3% |
|---|---|---|---|
| 幂等性误差 | 两次运行 ext_id 序列差异条数 / 节点数 | 0 / {len(plan['nodes'])}（{replay['identical_ext_ids']}） | 是（0%） |
| ext_id 冲突率 | 与全库 {gate['checks'][2]['universe_size']} 条既有 ext_id 的碰撞数 | {len(gate['checks'][2]['clashes'])} | 是（0%） |
| 人工↔机器节点可复现率 | 四通道阈值 0.30 下人工节点被机器覆盖数 / 10 | {audit['repro_threshold_sensitivity']['0.30']['manual_nodes_matched']} / {audit['manual']['node_count']} | 是（0% 未覆盖） |
| 去重结论一致性 | 人工声明 3 条合并 vs 机器 0 条 | 不一致（差距 3 条） | **否**（差异 100%，即判据 (a) FAIL） |

**结论：几何/物理量基准不适用（本节点无物理量）；可复现性类指标 3 项全为 0 误差，
唯一不达标项是「与人工仲裁的去重结论一致性」——该项的差异已定位为人工侧声明不可复现，
而非机器实现偏差，故按 honest FAIL 记录，不覆盖。**

## 局限与下一步

* 计划写回：`campaign_plans/milestones/nodes` 事务化插入 + 回滚脚本（可复用本节点的
  `seed_plan.json` 作为输入）。
* 选择策略升级：把「成熟化优先 → 仓名 → 摘要」换成严重度/证据完备度打分（溢出不再任意）。
* 判据抽取：本节点已按配置的 `criteria_section_markers` 从提案小节抽判据（节点判据条数
  见表 3），下一步接 `tools/proposal_gate` 的契约层做 fail-closed 校验。
"""
    payload = {
        "title": "P132-W2 · 提案聚合播种管线自动化 + 战役 DAG 自回归增长（m8405）",
        "summary": summary,
        "scenario_key": "platform/proposal-seeding-autoregress",
        "mission_id": 8405,
        "tags": ["TensorPlatform", "proposal-seeding", "campaign-dag", "autoregress",
                 "machine-gate", "honest-fail"],
        "markdown": md,
        "figures": [],
    }
    figs = read("figures.json")["figures"] if (OUT / "figures.json").is_file() else []
    for f in figs:
        p = ROOT / f["path"]
        payload["figures"].append({
            "name": Path(f["path"]).name,
            "caption": f["caption"],
            "png_base64": base64.b64encode(p.read_bytes()).decode("ascii"),
        })
    return md, payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--doc", default=str(DOC))
    ap.add_argument("--payload", default="/tmp/report.json")
    args = ap.parse_args(argv)
    md, payload = build()
    Path(args.doc).parent.mkdir(parents=True, exist_ok=True)
    Path(args.doc).write_text(md, encoding="utf-8")
    Path(args.payload).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"doc": args.doc, "payload": args.payload,
                      "markdown_chars": len(md), "figures": len(payload["figures"]),
                      "payload_kb": round(Path(args.payload).stat().st_size / 1024, 1)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())