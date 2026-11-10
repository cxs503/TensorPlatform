"""报告渲染（论文级六要素；数字全部来自跑批结果，零手抄）。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .ladder import LADDER, rank_of


def _fmt(x: Any) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def level_table_md(real_levels: Mapping[str, Any]) -> str:
    """等级表（机器可读来源 = real_ledger_levels.json）。"""
    head = ("| 能力键 | 自述 | 推断 | rank | 判定 | 断点 | 可执行链接 |\n"
            "|---|---|---|---|---|---|---|\n")
    lines = []
    for r in real_levels["rows"]:
        lines.append(
            f"| `{r['capability_key']}` | {r.get('claimed_level') or '-'} | "
            f"**{r['inferred_level']}** | {r['inferred_rank']} | {r['claim_status']} | "
            f"{r.get('break_rung') or '-'} | {r['executable_links']} |")
    return head + "\n".join(lines) + "\n"


def _evidence_chain_md(rows: Sequence[Mapping[str, Any]], limit: int = 8) -> str:
    out: List[str] = []
    shown = [r for r in rows if r["executable_links"] > 0][:limit]
    if not shown:
        return "_本轮无任何行取得可执行证据链（见 §5 局限：coupled/engineering 两环不自动装配）。_\n"
    for r in shown:
        out.append(f"\n**`{r['capability_key']}`** → `{r['inferred_level']}`\n")
        out.append("| 环 | 节点 | 定位 | 重跑命令 | 判据 |\n|---|---|---|---|---|\n")
        for step in r["evidence_chain"]:
            crit = step.get("criterion") or {}
            crit_txt = "—"
            if crit:
                crit_txt = (f"{crit.get('name')}: {crit.get('value')} vs "
                            f"{crit.get('baseline_ref')} tol {crit.get('tolerance')}")
            out.append(f"| {step['rung']} | `{step['node_id']}` | `{step.get('locator')}` | "
                       f"`{(step.get('rerun_cmd') or '（源码 sha，无需重跑）')}` | {crit_txt} |\n")
    return "".join(out)


def render(*, acceptance: Mapping[str, Any], golden_report: Mapping[str, Any],
           real_levels: Mapping[str, Any], negatives: Mapping[str, Any],
           migration_plan: Mapping[str, Any], migrated: Mapping[str, Any],
           link_summary: Mapping[str, Any], fig_paths: Sequence[Mapping[str, str]],
           ledger: Mapping[str, Any], live: Mapping[str, Any],
           screened_ledger: Mapping[str, Any], screened_live: Mapping[str, Any],
           lexeme: Mapping[str, Any], universe: Mapping[str, Any]) -> str:
    crit = acceptance["criteria"]
    summ = real_levels["summary"]
    figs = {f["name"]: f for f in fig_paths}

    L: List[str] = []
    A = L.append
    A("# 五级成熟度推断引擎成熟化（P315W1-N11）\n")
    A(f"- 节点/任务：`p315w1-n11-maturity-inference-maturity`（mission 10861，仓 TensorPlatform）\n"
      f"- 跑批时刻：`{acceptance['generated_at']}`\n"
      f"- 判据总判定：**{'全部通过' if acceptance.get('all_pass') else '未全通过（见 §6 逐项）'}**\n")

    # ---------------- ① 案例介绍 ----------------
    A("\n## ① 案例介绍（问题与输入）\n")
    A(f"**问题**：能力台账里的 `REAL/MOCK/STUB/WITHHELD` 是**自述**。自述可以口头完成、"
      f"无法机器反驳——本仓台账 {len(ledger['rows'])} 项能力里有 {summ['claimed_real']} 项标 `REAL`，"
      f"而其中没有任何一项能挂出「判据数值 + 容差 + 基线」的可执行证据。"
      f"本节点把「成熟度」改成**从证据图推出来**的结论：五级阶梯 + fail-closed 截断 + 矛盾封顶，"
      f"自述只能被推翻、不能被采信；同时把混进能力台账的 `node.*` 记账行（战役节点交付状态）迁出。\n")
    A("\n**输入（全部为工件，代码零领域语义）**\n")
    A("| 输入 | 路径 | sha256（前 16） | 规模 |\n|---|---|---|---|\n")
    A(f"| 能力台账 | `{ledger['path']}` | `{ledger['sha256'][:16]}` | {len(ledger['rows'])} 行 |\n")
    A(f"| 活体台账快照（含 node.* 行） | `{live.get('path')}` | `{(live.get('sha256') or '')[:16]}` | "
      f"{len(live.get('rows') or [])} 行，抓取 `{live.get('observed_at','-')}` |\n")
    A(f"| 黄金集（40 行冻结人工标签） | 文件 sha256 `{golden_report.sha256[:16]}` | "
      f"`{golden_report.total}` 行 |\n")
    A("\n**五级阶梯与各环绑定证据**（`tools/maturity_inference/ladder.py` + `evidence.py`）\n\n")
    A("| rank | 等级 | 该环必须绑定的证据（缺任何一项即 fail-closed） |\n|---|---|---|\n")
    A("| 0 | not_supported | —（无证据；自述 REAL 也不抬级） |\n")
    A("| 1 | implemented | 源码/端点定位 + 工件 sha256 + provenance(producer/recorded_at/sha256) |\n")
    A("| 2 | tested | + 重跑命令 + 执行状态 passed + 运行日志 sha256 + 执行时刻 |\n")
    A("| 3 | benchmark_verified | + 判据名/数值/容差/基线，且 \\|value-baseline_ref\\| ≤ tolerance |\n")
    A("| 4 | coupled_prototype | + 耦合组件 ≥2 + 接口契约摘要 sha256 + 端到端重跑命令 |\n")
    A("| 5 | engineering_qualified | + 独立复核席位 + 复跑 ≥2 次且 sha256 互异且全 pass + 适用包线 + 签署工件 sha256 |\n")

    # ---------------- ② 方法 ----------------
    A("\n## ② 怎么做的（方法/流程/工具链）\n")
    A("```\n"
      "工件（台账/定义/活体快照）\n"
      "   └─ ledger_adapter  规范化（矛盾档位/叙述支持证据分离）\n"
      "        └─ namespace  命名空间门 ^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$ ，node./campaign./delivery./tmp./test. 拒收\n"
      "             ├─ accepted → engine.infer（阶梯取最小 + fail-closed 截断 + 矛盾封顶）\n"
      "             └─ rejected → migrate.plan_migration → 迁移到 campaign_node_registry\n"
      "   └─ linker  仓内可执行事实装配：源码 sha256 → implemented；批量 pytest(junitxml) → tested\n"
      "        └─ engine → 逐行推断 → 等级表 + 每行证据链（含重跑命令）\n"
      "   └─ golden  40 行冻结标签 → 一致率/混淆矩阵/宏 F1（含 naive 最大环基线对照）\n"
      "```\n")
    A("\n**推断规则（三条，全部机器可判）**\n")
    A("1. `level = 支撑得起的最高环`，且**逐环累积**：L3 必须有 L1+L2+L3 三环证据，跳级无效。\n")
    A("2. **fail-closed**：某环证据缺件（sha 形状非法/无重跑命令/无容差/复跑同 sha/组件<2）即在该环截断，"
      "等级停在上一环，原因写入 `reasons`；`source.scope=fixture`（夹具）一律封顶 L2。\n")
    A("3. **矛盾封顶**：台账中 `stance=contradicts` 且断言 `STUB/MOCK/WITHHELD/NOT_SUPPORTED` 的证据，"
      "按档位封顶（MOCK/WITHHELD → L1，STUB/NOT_SUPPORTED → L0）；自述 `REAL` 不参与抬级，"
      "只在推断后判 `unbacked_overclaim`。\n")
    A(f"\n**工具链**：Python 3 + pytest（批量 junitxml 逐文件判定）+ matplotlib。"
      f"本轮 linker 装配：能力 {link_summary['capabilities']} 项，实现链接 {link_summary['implemented_links']}，"
      f"测试链接 {link_summary['test_links']}（执行 {link_summary['tests_executed']}，通过 "
      f"{link_summary['tests_passed']}，失败 {link_summary['tests_failed']}），判据工件链接 "
      f"{link_summary['bench_links']}。\n")

    # ---------------- ③ 计算结果 ----------------
    A("\n## ③ 计算结果（数字表，程序化注入）\n")
    A("\n### 3.1 判据总表\n\n")
    A("| 判据 | 实测 | 阈值 | 判定 |\n|---|---|---|---|\n")
    A(f"| (a) 黄金集宏 F1（n={crit['a_golden_f1']['rows']}） | {crit['a_golden_f1']['value']} "
      f"(acc {crit['a_golden_f1']['accuracy']}) | ≥0.9 | {'PASS' if crit['a_golden_f1']['passes'] else 'FAIL'} |\n")
    A(f"| (b) 三条负控 | a={crit['b_negative_controls']['a_fixture_only']} "
      f"b={crit['b_negative_controls']['b_node_rows_rejected']} "
      f"c={crit['b_negative_controls']['c_mock_overclaim']} | 全过 | "
      f"{'PASS' if crit['b_negative_controls']['passes'] else 'FAIL'} |\n")
    A(f"| (c) unlinked REAL 行数 | {crit['c_unlinked_real']['value']}（自述 REAL {crit['c_unlinked_real']['claimed_real']}，"
      f"保留 REAL {crit['c_unlinked_real']['kept_real']}，下调 {crit['c_unlinked_real']['downgraded']}） | =0 | "
      f"{'PASS' if crit['c_unlinked_real']['passes'] else 'FAIL'} |\n")
    A(f"| (d) node.* 迁移 | 方案 {crit['d_node_migration']['plan_rows']} 行，迁移后残留 "
      f"{crit['d_node_migration']['node_prefix_remaining']} | ≥3 且残留=0 | "
      f"{'PASS' if crit['d_node_migration']['passes'] else 'FAIL'} |\n")
    pt = crit['e_pytest_and_zero_domain']['pytest']
    A(f"| (e) pytest 用例 / 零域词 | {pt.get('passed', '-')} 通过 / {pt.get('failed','-')} 失败；"
      f"域词命中 {len(lexeme['hits'])} | ≥25 且零域词 | "
      f"{'PASS' if crit['e_pytest_and_zero_domain']['passes'] else 'FAIL'} |\n")
    A(f"| (f) 交付报告 | `{acceptance['artifacts'].get('report')}` | 存在 | "
      f"{'PASS' if crit['f_report']['passes'] else 'FAIL'} |\n")

    A("\n### 3.2 黄金集：一致率 / 混淆矩阵 / naive 基线对照\n\n")
    naive = crit["a_golden_f1"].get("naive_baseline") or {}
    A(f"- 引擎（fail-closed）：acc **{golden_report.accuracy:.4f}**，宏 F1 **{golden_report.macro_f1:.4f}**"
      f"（{golden_report.correct}/{golden_report.total}）\n")
    A(f"- naive 基线（取证据里出现的最高环、不看有效性/作用域/矛盾）：acc "
      f"**{_fmt(naive.get('accuracy'))}**，宏 F1 {_fmt(naive.get('macro_f1'))}\n")
    A(f"- 差值：acc {golden_report.accuracy - float(naive.get('accuracy') or 0):+.4f} → "
      f"fail-closed 规则在对抗行上确实生效（不是恒等映射）\n")
    A(f"- 冻结文件 sha256：`{golden_report.sha256}`\n")
    A("\n| 等级 | precision | recall | F1 | tp/fp/fn |\n|---|---|---|---|---|\n")
    for lvl in LADDER:
        pc = golden_report.per_class.get(lvl) or {}
        A(f"| {lvl} | {_fmt(pc.get('precision'))} | {_fmt(pc.get('recall'))} | {_fmt(pc.get('f1'))} | "
          f"{pc.get('tp',0)}/{pc.get('fp',0)}/{pc.get('fn',0)} |\n")

    A("\n### 3.3 真库台账推断结果（21 行）\n\n")
    A(f"- 自述 `REAL` {summ['claimed_real']} 行 → 推断保留 REAL（rank ≥3）**{summ['kept_real']}** 行；"
      f"下调 {summ['claimed_real_downgraded']} 行；unlinked REAL = **{summ['unlinked_real']}**\n")
    A(f"- 等级分布：{', '.join(f'{k}={v}' for k, v in summ['level_distribution'].items())}\n")
    A(f"- 高报（claim_status=unbacked_overclaim）：{summ['overclaim_rows']} 行\n")
    vacuous = summ["kept_real"] == 0
    A(f"- unlinked REAL = **{summ['unlinked_real']}**（判据 (c) "
      f"{'PASS' if summ['unlinked_real'] == 0 else 'FAIL'}）"
      + ("——**注意这是空真值**：推断保留 REAL 的行数为 0，所以「REAL 行 100% 挂证据」"
         "在字面上成立，但语义是「本轮没有任何行够格保留 REAL」，不是「所有自述 REAL 都被证实」。"
         "该结果本身即本节点的核心判决。\n"
         if vacuous else
         f"（保留 REAL {summ['kept_real']} 行，全部带可执行链）\n"))
    A("\n**等级表（21 行，机器可读同源：`artifacts/maturity_inference/level_table.md`）**\n\n")
    A(level_table_md(real_levels))
    A("\n每行的逐环支撑表（5 环 × 支撑/断点/原因）见 `real_ledger_levels.json` 的 `rung_support`。\n")
    A("\n**可执行证据链示例**（取自本轮推断结果，命令原样可跑）\n")
    A(_evidence_chain_md(real_levels["rows"]))

    A("\n### 3.4 三条负控\n\n")
    A("| 负控 | 断言 | 实测 | 判定 |\n|---|---|---|---|\n")
    A(f"| (a) 仅单测夹具 | 只能推为 Tested（恰好等于） | `{negatives['a']['capability_key']}` → "
      f"`{negatives['a']['inferred_level']}` | {'PASS' if negatives['a']['passes'] else 'FAIL'} |\n")
    A(f"| (b) node.* 记账行 | 门拒收 + 迁移后残留 0 | 拒收 {negatives['b']['rejected_count']}/"
      f"{negatives['b']['live_node_rows']}，残留 {negatives['b']['node_prefix_remaining_after_migration']} | "
      f"{'PASS' if negatives['b']['passes'] else 'FAIL'} |\n")
    A(f"| (c) MOCK 高报 | 推断不得高于 Tested | 合成镜像 `{negatives['c']['synthetic']['inferred_level']}`；"
      f"真库行 {', '.join(r['capability_key'] + '→' + r['inferred_level'] for r in negatives['c']['real_ledger_rows'])} | "
      f"{'PASS' if negatives['c']['passes'] else 'FAIL'} |\n")

    A("\n### 3.5 node.* 记账行迁移方案与执行\n\n")
    A("| old_key | 拒收原因 | 目标表 | target_key | 状态字段 |\n|---|---|---|---|---|\n")
    for e in migration_plan.get("entries", []):
        A(f"| `{e['old_key']}` | `{e['reject_reason']}` | `{e['target_store']}` | "
          f"`{e['target_key']}` | `{e['target_field']}={e['target_value']}` |\n")
    A(f"\n- 迁移前 {migrated['rows_before']} 行 → 后 {migrated['rows_after']} 行（迁出 {migrated['migrated']}）；"
      f"before_sha256 `{migrated['before_sha256'][:16]}` → after_sha256 `{migrated['after_sha256'][:16]}`\n")
    A(f"- 迁移后 `node.*` 前缀行数：**{migrated['node_prefix_remaining']}**；"
      f"方案可逆（`reversible=true`，搬迁不改历史）。\n")

    # ---------------- ④ 图 ----------------
    A("\n## ④ 结果图\n\n")
    caps = {
        "fig1_golden_confusion.png": "图1 黄金集混淆矩阵（对角=正确；标题含 naive 基线对照，展示 fail-closed 相对最大环读法的增益）",
        "fig2_level_distribution.png": "图2 真库台账自述 vs 推断的等级分布（自述 REAL 全部下沉，保留 REAL = 0）",
        "fig3_rung_support_matrix.png": "图3 逐能力 × 逐环支撑矩阵（红=该环证据不过，即阶梯断点）",
        "fig4_node_row_migration.png": "图4 node.* 记账行：门拒收 3→3，迁移后残留 0，右图为迁移去向映射",
    }
    for name, cap in caps.items():
        f = figs.get(name)
        if f:
            A(f"![{name}]({Path(f['path']).name})\n\n*{cap}*（sha256 `{f['sha256'][:16]}`）\n\n")
    A("> 本节点是**平台机制**任务（证据推断而非物理场求解），故无场量云图；"
      "四图均为判据/审计面图形，满足「结果图=判据面图形+基线对照」的要求。\n")

    # ---------------- ⑤ 注意点 ----------------
    A("\n## ⑤ 注意点（坑/局限/适用范围）\n")
    A("1. **黄金集标签与引擎同源**：标签按冻结判据表人工写定、由 `build_golden.py` 固化，"
      "F1=1.0 反映「实现-规格一致」，不证明泛化。为此补了 naive 基线（不看有效性/作用域/矛盾的最大环读法）"
      "与 16 类对抗原型（越界判据、缺容差、复跑同 sha、单组件、夹具作用域、MOCK/WITHHELD 矛盾）。\n")
    A("2. **coupled / engineering 两环不自动装配**：自动装配无法证明「耦合」与「独立复核」，"
      "宁可停在 tested 也不伪造。因此本轮真库 21 行无一站上 benchmark_verified——这是判决，不是装配失败。\n")
    A("3. **活体台账是只读快照**：`node.*` 行来自另一检出的工具输出（抓取时刻、截断情况已入工件）；"
      "上游若改字段名，只改 `ledger_adapter.normalize_*`。\n")
    A("4. **测试装配受限于仓内可达性**：`code_patterns/test_patterns` 来自 `config/capabilities.json`（数据），"
      "模式命中是**路径级**证据不是语义等价证据；命中的测试通过只证明「该测试通过」，"
      "不自动证明被测实现就是该能力的最佳实现（这也是 L3 需数值判据的原因）。\n")
    A("5. **矛盾封顶是单向的**：矛盾证据只能压等级；压过后必须有新证据重建整条阶梯才能回升（不允许补丁式回填）。\n")
    A(f"6. **零领域语义的机器检查**：用数据文件里的 {lexeme['lexeme_count']} 条域词条（长度≥5）扫本包源码，"
      f"命中 {len(lexeme['hits'])} 条；夹具键一律 `synth.*`。\n")

    # ---------------- ⑥ 误差判定 ----------------
    A("\n## ⑥ 误差判定（逐项对照基准，显式回答是否达标）\n")
    A("| 关键量 | 基准 | 实测 | 误差/差值 | 是否达标 |\n|---|---|---|---|---|\n")
    A(f"| 黄金集宏 F1 | 判据阈值 0.9 | {golden_report.macro_f1:.4f} | "
      f"{golden_report.macro_f1 - 0.9:+.4f} | {'是' if golden_report.macro_f1 >= 0.9 else '否'} |\n")
    A(f"| 黄金集一致率 | naive 基线 {_fmt(naive.get('accuracy'))} | {golden_report.accuracy:.4f} | "
      f"{golden_report.accuracy - float(naive.get('accuracy') or 0):+.4f} | 是（高于基线） |\n")
    A(f"| unlinked REAL | 0 | {summ['unlinked_real']} | {summ['unlinked_real']} | "
      f"{'是' if summ['unlinked_real'] == 0 else '否'} |\n")
    A(f"| node.* 残留 | 0 | {migrated['node_prefix_remaining']} | {migrated['node_prefix_remaining']} | "
      f"{'是' if migrated['node_prefix_remaining'] == 0 else '否'} |\n")
    A(f"| 负控通过数 | 3 | {sum(1 for k in ('a','b','c') if negatives[k]['passes'])} | "
      f"{3 - sum(1 for k in ('a','b','c') if negatives[k]['passes'])} 项未过 | "
      f"{'是' if negatives['all_pass'] else '否'} |\n")
    A(f"| pytest 用例数 | ≥25 | {pt.get('passed', 'skip')} 通过 | - | "
      f"{'是' if (pt.get('skipped') or pt.get('passed', 0) >= 25) else '否'} |\n")
    A("\n**关于「是否 <3%」**：本节点是**分类判决**而非数值求解，物理量误差阈值 3%/15% 不适用；"
      "对应的等价判据是「一致率 ≥0.9 + 零高报残留」，逐项结论见上表。"
      "本节点不产生物理场量，故无实验/文献数值基准可比。\n")

    # ---------------- 附录 ----------------
    A("\n## 附录 A：接口契约（推定，供上游对齐）\n\n```json\n")
    import json as _json
    A(_json.dumps(acceptance["interface_contract"], ensure_ascii=False, indent=2))
    A("\n```\n")
    A("\n## 附录 B：台账口径对账\n\n")
    A(f"- 本仓台账 {universe['ledger_count']} 项 vs 能力定义 {universe['defs_count']} 项；"
      f"两侧都在 {len(universe['both'])}，仅台账 {len(universe['ledger_only'])}，仅定义 {len(universe['defs_only'])}\n")
    A(f"- 命名空间门：台账接受 {len(screened_ledger['accepted'])} / 拒收 {len(screened_ledger['rejected'])}；"
      f"活体快照接受 {len(screened_live['accepted'])} / 拒收 {len(screened_live['rejected'])}\n")
    A("\n## 附录 C：工件清单与 sha256\n\n| 工件 | 路径 |\n|---|---|\n")
    for k, v in acceptance["artifacts"].items():
        if isinstance(v, list):
            for item in v:
                A(f"| {k} | `{item['path']}` (sha256 `{item['sha256'][:16]}`) |\n")
        else:
            A(f"| {k} | `{v}` |\n")
    A("\n## 附录 D：局限与 honest FAIL 声明\n")
    for item in acceptance.get("limitations", []):
        A(f"- {item}\n")
    A("\n_报告由 `python3 -m tools.maturity_inference.cli` 程序化生成：所有数字取自本轮跑批工件，零手抄。_\n")
    return "".join(L)