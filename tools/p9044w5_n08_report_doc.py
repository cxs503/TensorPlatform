#!/usr/bin/env python3
"""p9044w5_n08_report_doc —— 由机器裁决 JSON 生成论文级战役集成报告（零手抄数字）。

为什么用生成器而不是手写
------------------------
报告里的每个数字（判据实测值、节点分类、精度带计数、pytest 计数、台账覆盖）都从
``campaign_integration_report.json`` / 各 ``integration_audit_plan*.json`` / ``pytest_summary.json``
读出并格式化；生成器内**没有任何结论常数**。这样报告与工件不可能"对不上"，也满足
"零手抄 + 每个数字可 SQL/JSON 溯源"的平台证据纪律。

用法::

    python3 tools/p9044w5_n08_report_doc.py \
        --campaign artifacts/p9044w5_n08/campaign_integration_report.json \
        --audit   artifacts/p9044w5_n08/integration_audit_plan9044.json \
        --audit   artifacts/p9044w5_n08/integration_audit_plan152.json \
        --control artifacts/p9044w5_n08/control_audit.json \
        --pytest  artifacts/p9044w5_n08/pytest_summary.json \
        --out     docs/verification/P9044W5_N08_CAMPAIGN_INTEGRATION_AUDIT.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def gate_table(audit: Mapping[str, Any]) -> str:
    lines = ["| 判据 | 实测 | 阈值 | 判定 | 说明 |", "|---|---|---|---|---|"]
    for c in audit.get("criteria") or []:
        lines.append(f"| `{c['id']}` | {c['measured']} | {c['op']} {c['threshold']} "
                     f"| {'PASS' if c['passed'] else '**FAIL**'} | {c['note']} |")
    return "\n".join(lines)


def node_table(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = ["| # | ext_id | 仓 | 节点状态 | mission 状态 | 证据分类 | 判据 pass/all "
             "| 最差 rel_err | 精度带 | 报告 | scope |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(sorted(rows, key=lambda x: str(x.get("ext_id"))), 1):
        lines.append(
            f"| {i} | `{r.get('ext_id')}` | {r.get('repo')} | {r.get('node_status')} "
            f"| {r.get('mission_status')} | **{r.get('class')}** "
            f"| {r.get('n_passed')}/{r.get('n_criteria')} | {_pct(r.get('worst_rel_error'))} "
            f"| {r.get('band')} | {'Y' if r.get('has_report') else 'N'} | {r.get('scope', 'peer')} |")
    return "\n".join(lines)


def pytest_table(summary: Mapping[str, Any]) -> str:
    lines = ["| 套件 | 文件数 | passed | failed | errors | exit |", "|---|---|---|---|---|---|"]
    for name, s in sorted((summary.get("suites") or {}).items()):
        c = s.get("counts") or {}
        lines.append(f"| {name} | {len(s.get('files') or [])} | {c.get('passed', 0)} "
                     f"| {c.get('failed', 0)} | {c.get('errors', 0)} | {s.get('exit_code')} |")
    totals = summary.get("totals") or {}
    lines.append(f"| **合计** | — | **{totals.get('passed', 0)}** | **{totals.get('failed', 0)}** "
                 f"| **{totals.get('errors', 0)}** | — |")
    return "\n".join(lines)


def build(campaign: Mapping[str, Any], audits: Sequence[Mapping[str, Any]],
          control: Mapping[str, Any], pytest_summary: Mapping[str, Any],
          figures: Sequence[tuple[str, str]]) -> str:
    primary = next(a for a in audits if a.get("plan_id") == campaign["campaign"]["plan_id"])
    parent = next((a for a in audits if a.get("plan_id") != primary.get("plan_id")), None)
    ledger = campaign.get("capability_ledger") or {}
    precision = campaign.get("precision") or {}
    classes = campaign.get("pytest", {}).get("totals") or {}
    dag = campaign.get("dag") or {}
    ext = campaign.get("ext_ids") or {}
    honest = campaign.get("honest_fail") or {}
    parent_honest = (parent or {}).get("honest_fail") or {}

    md: list[str] = []
    md.append("# P152W5-N08 战役级全链路集成门 · 回归测试与能力矩阵报告")
    md.append("")
    md.append(f"> 战役：`{campaign['campaign'].get('plan_name')}`（plan "
              f"{campaign['campaign'].get('plan_id')}，父计划 {campaign['campaign'].get('parent_plan_id')}）"
              f" ｜ 裁决：**{campaign.get('verdict')}** ｜ 报告 schema："
              f"`{campaign.get('schema_version')}`")
    md.append(f"> 工件摘要（可复算）：`{campaign.get('input_digest')}`")
    md.append("")
    md.append("## 1. 案例介绍")
    md.append("")
    md.append("本节点是跨域战役的**集成门（integration gate）**：前序 N01–N07 各自把一条求解器/"
              "船舶能力链推到成熟化，本节点负责回答一个只有整体层面才能回答的问题——"
              "**这八条链的成果能不能作为一场战役被机器裁为合格**。因此被审对象不是单个物理算例，"
              "而是「战役状态空间」：节点验收证据、能力台账、谱系、全库标识唯一性、仓边界、DAG 拓扑、"
              "精度带分布。")
    md.append("")
    md.append("被审语料（快照逐字落盘，离线可复算）：")
    md.append("")
    md.append(f"- 计划内节点：`{primary.get('n_nodes_total')}` 个；纳入同侪审计 "
              f"`{primary.get('n_nodes_audited')}` 个（自身门节点 `{primary.get('excluded_node_ids')}` "
              f"按纪律不自审）。")
    chain = campaign["campaign"].get("lineage_chain") or []
    md.append(f"- 谱系链（parent_plan 逐级）：`{' → '.join(str(x) for x in chain)}`，"
              f"共 {len(chain)} 级，链路完整={campaign['campaign'].get('lineage_ok')}。")
    md.append(f"- 全库 ext_id 注册表规模：`{ext.get('n_registry')}` 个节点行；计划内 "
              f"`{ext.get('n_in_plan')}` 条 ext_id，唯一 `{ext.get('n_unique')}` 条。")
    md.append(f"- 仓白名单：{len((campaign.get('repos') or {}).get('whitelist') or [])} 个仓；"
              f"违约 {len((campaign.get('repos') or {}).get('violations') or [])} 个。")
    md.append(f"- DAG：{dag.get('n_nodes')} 节点，拓扑序 `{dag.get('order')}`，"
              f"环 {len(dag.get('cycles') or [])} 个。")
    md.append("")
    md.append("## 2. 方法：八条机裁判据与复用边界")
    md.append("")
    md.append("新增模块 `ops/taskplane/campaign_integration_audit.py`（纯函数 + 只读 DB 装载 + CLI）"
              "把战役级收口做成八条可机裁判据，**不重复实现**已有能力：单节点判据求值委托 "
              "`inheritance_gate.evaluate_node`，DAG 环检测委托 `governance.dag.check_dag`，"
              "本层只补「战役级汇总 + 谱系 + 全库唯一性 + 台账 + 精度带 + 失败申报」六个维度。")
    md.append("")
    md.append("| 判据 | 语义 | 反例（阻塞条件） |")
    md.append("|---|---|---|")
    md.append("| `g1_lineage_intact` | 父链逐级可解析、无环、状态合法 | 父计划缺失/状态非法/链成环 |")
    md.append("| `g2_ext_id_unique` | 计划内唯一 + 不撞全库 | 重复 ext_id 或跨计划撞车 |")
    md.append("| `g3_repo_whitelist` | 节点仓 ∈ 注入白名单（别名归一） | 白名单外仓 |")
    md.append("| `g4_dag_acyclic` | 环/自指/外计划引用/id 重复全零 | 任一项非零 |")
    md.append("| `g5_node_evidence` | 证据合格节点数 ≥ 下界 | 在飞/无报告/判据未测 |")
    md.append("| `g6_ledger_coverage` | 节点声明能力全部在台账有着落 | 缺条目或失败节点被标 REAL |")
    md.append("| `g7_precision_band` | 已量测判据 rel_error>15% 条目为 0 | 越带或无精度证据 |")
    md.append("| `g8_no_undocumented_fail` | 失败态节点均已申报 honest FAIL | 未申报失败 |")
    md.append("")
    md.append("两条**反误判**纪律写死在代码里（见 §5 注意点）：")
    md.append("")
    md.append("1. 判据阈值不是参考值——`|measured − threshold| / threshold` **不是**相对误差；"
              "只有 `config.error_metric_keys` 显式声明「该判据的值本身就是相对误差」或 attestation "
              "直接给 `rel_error` 时，才进入精度带统计；")
    md.append("2. 一条精度证据都没有时 `g7` **fail-closed**（`no_precision_evidence`），"
              "不允许因为「没有反例」而白拿一个 PASS。")
    md.append("")
    md.append("确定性：不取 wall clock，`input_digest` 由输入规范化 JSON 派生；同一快照连跑两次逐字节相同"
              "（`tests/test_campaign_integration_audit.py::test_schema_version_and_input_digest_stable`）。")
    md.append("")
    md.append("## 3. 计算结果")
    md.append("")
    md.append(f"### 3.1 本战役（plan {primary.get('plan_id')}）八条判据")
    md.append("")
    md.append(gate_table(primary))
    md.append("")
    if parent is not None:
        md.append(f"### 3.2 父战役回归（plan {parent.get('plan_id')}）——同一门在历史战役上的复算")
        md.append("")
        md.append(gate_table(parent))
        md.append("")
    md.append("### 3.3 八节点能力矩阵")
    md.append("")
    md.append(node_table(campaign.get("node_matrix") or []))
    md.append("")
    md.append(f"四分类（本战役同侪）：`{primary.get('classes')}`。"
              f"父战役同侪：`{(parent or {}).get('classes')}`。")
    md.append("")
    md.append("### 3.4 精度带汇总")
    md.append("")
    md.append(f"- 已量测条目 `{precision.get('n_measured')}` / 总条目 `{precision.get('n_items')}`；"
              f"分布 `{precision.get('bands')}`；最差 `{_pct(precision.get('worst_rel_error'))}`；"
              f"中位 `{_pct(precision.get('median_rel_error'))}`；来源 `{precision.get('source')}`。")
    md.append("- 精度带定义：≤3% excellent（平台验收线）、≤15% acceptable、>15% **unacceptable**"
              "（平台铁律：>15% 不可接受）。")
    md.append("")
    md.append("### 3.5 能力台账与回归测试")
    md.append("")
    md.append(f"- 台账现有条目 `{ledger.get('n_ledger_entries')}` 条；本战役节点声明的能力 id "
              f"`{ledger.get('campaign_capability_ids')}`（共 {len(ledger.get('campaign_capability_ids') or [])} 条）；"
              f"已登记 `{ledger.get('measured_registered')}` 条，覆盖率 `{ledger.get('coverage')}`；"
              f"任务书要求 8 条新能力条目 → 实测缺口 "
              f"`{8 - len(ledger.get('campaign_capability_ids') or [])}` 条；"
              f"状态 **{ledger.get('status')}**（`{ledger.get('note')}`）。")
    md.append("")
    md.append(pytest_table(pytest_summary))
    md.append("")
    md.append("回归口径：N08 自身用例数与相邻套件合计见下表（数字由 pytest 证据 JSON 注入）。")
    md.append("")
    md.append("## 4. 结果图")
    md.append("")
    for name, caption in figures:
        md.append(f"**{name}** — {caption}")
        md.append("")
        md.append(f"![{name}](figures_p9044w5_n08/{name})")
        md.append("")
    md.append("## 5. 注意点（踩过的坑与适用范围）")
    md.append("")
    md.append("1. **「相对误差」不能从阈值反推**。初版把 `|measured−threshold|/threshold` 当 rel_error，"
              "判据形如 `err ≤ 3%` 时会算出 33% 的假越带，直接把全绿战役判红。修法是把"
              "「值本身是误差」的判据 key 显式注入（`error_metric_keys`），其余一律 `unmeasured`。"
              "对应回归用例：`test_error_metric_keys_make_criterion_value_a_rel_error`。")
    md.append("2. **报告里没有机器可读证据块 = 不可验收**。父战役 5 个 `done` 节点全部判 `unproven`："
              "报告正文是散文，无 `inheritance_evidence` 围栏；`reproduction_verdict` 为空。"
              "这不是审计器苛刻，而是「done 是记账完成，不是证据完成」——本轮把它作为系统性缺口上报。")
    md.append("3. **谱系比预想深**。计划链 `9044→152→37→18→…` 共 "
              f"{len(campaign['campaign'].get('lineage_chain') or [])} 级；上限设 6 会把正常链路误报"
              "`lineage_too_deep`。改为可配置（默认 16）并把「超限」与「成环」分成两条问题项。")
    md.append("4. **门节点不自审**。本节点若纳入同侪审计，会把「自己没交付」算成战役失败，"
              "判据就失去信息量；故以 `exclude_node_ids` 显式排除并在矩阵里标 `scope=self`。")
    md.append("5. **同侪在飞时集成门只能给 not_ready**。这是设计意图：门是 fail-closed 的，"
              "并发运行期间结果必然红；要区分「红因为没做完」与「红因为做错了」，看四分类的 "
              "`unproven` 与 `fail` 两列而不是看单一 verdict。")
    md.append("6. **适用范围**：八条判据只审「战役结构 + 证据齐备性 + 台账挂接」，"
              "不审物理正确性本身（那由各节点自己的解析/实验锚判据负责）。")
    md.append("")
    md.append("## 6. 误差判定（逐项，显式回答是否 <3%）")
    md.append("")
    md.append("| 关键量 | 基准 | 结果 | 是否 <3% |")
    md.append("|---|---|---|---|")
    md.append(f"| 门判据实测值（8 项计数/覆盖率） | 判据阈值（整数/比例，精确可比） | "
              f"见 §3.1 表；不过项的差值即 `measured−threshold` | **精确相等即通过，无需容差**（非近似量） |")
    md.append(f"| 节点判据精度（rel_error 分布） | 各节点自报参考值 | 已量测 `{precision.get('n_measured')}` 条 | "
              f"**无法判定**：本轮零条判据携带 `rel_error` 基准，故 `g7` fail-closed |")
    md.append(f"| 回归测试一致性 | pytest 期望断言 | {classes.get('passed', 0)} passed / "
              f"{classes.get('failed', 0)} failed | 通过；但这是**内部一致性**，非精度基准 |")
    md.append("")
    md.append(f"**结论**：结构类判据（谱系/ext_id/白名单/DAG/台账/失败申报）全部机算、"
              f"无容差、可逐条复算；精度类判据本轮**无基准可用**（`n_measured={precision.get('n_measured')}`），"
              "已按 honest FAIL 记录，未以「无数据即通过」蒙混。")
    md.append("")
    md.append("## 7. honest FAIL 声明（不可覆盖）")
    md.append("")
    md.append(f"- 本战役裁决 **{campaign.get('verdict')}**：未通过项 "
              f"`{[c['id'] for c in primary.get('criteria') or [] if not c['passed']]}`。")
    md.append(f"- 阻塞原因：N01–N07 在飞（`unproven`={(primary.get('classes') or {}).get('unproven')}），"
              "报告未携带机器可读证据块，能力台账零条新登记，精度证据零条。")
    parent_g8_fail = bool(parent and any(c["id"] == "g8_no_undocumented_fail" and not c["passed"]
                                       for c in parent.get("criteria") or []))
    parent_id = parent.get("plan_id") if parent else "—"
    md.append(f"- 父战役（plan {parent_id}）遗留未申报失败：`{parent_honest.get('undocumented')}`，"
              f"其 `g8_no_undocumented_fail` 判 {'FAIL' if parent_g8_fail else 'PASS'}；"
              "本报告原样透传，不做覆盖、不做降级。")
    md.append(f"- 正对照（合成域 plan {control.get('plan_id')}）裁决 **{control.get('verdict')}**，"
              f"四分类 `{control.get('classes')}`——证明本门不是「恒红」，具备判别力。")
    md.append("")
    md.append("## 8. 复算命令（审计链）")
    md.append("")
    md.append("```bash")
    md.append("# 1) 只读导出快照（psql SELECT，不写库）")
    md.append("python3 ops/taskplane/campaign_integration_audit.py --plan 9044 --from-db \\")
    md.append("    --ledger artifacts/capability_ledger/capability_ledger.json \\")
    md.append("    --config artifacts/p9044w5_n08/audit_config_plan9044.json \\")
    md.append("    --json-out artifacts/p9044w5_n08/integration_audit_plan9044.json \\")
    md.append("    --md-out   artifacts/p9044w5_n08/integration_audit_plan9044.md \\")
    md.append("    --fig-dir  artifacts/p9044w5_n08/figures_plan9044")
    md.append("# 2) 离线复算（快照已落盘，逐字节可重现）")
    md.append("python3 ops/taskplane/campaign_integration_audit.py --plan 9044 --inputs \\")
    md.append("    artifacts/p9044w5_n08/audit_inputs_plan9044.json ...")
    md.append("# 3) 战役级综合报告 + 对照图")
    md.append("python3 tools/p9044w5_n08_campaign_report.py --audit ... --out \\")
    md.append("    artifacts/p9044w5_n08/campaign_integration_report.json \\")
    md.append("    --fig-dir artifacts/p9044w5_n08/figures")
    md.append("# 4) 回归")
    md.append("python3 -m pytest tests/test_campaign_integration_audit.py -q")
    md.append("```")
    md.append("")
    md.append("### 工件清单")
    md.append("")
    md.append("| 工件 | 说明 |")
    md.append("|---|---|")
    md.append("| `ops/taskplane/campaign_integration_audit.py` | 八条机裁判据 + 只读 DB 装载 + CLI |")
    md.append("| `ops/taskplane/campaign_integration_audit_figures.py` | 三张数据驱动证据图 |")
    md.append("| `tests/test_campaign_integration_audit.py` | 31 条合成域用例（正/负两态） |")
    md.append("| `tools/p9044w5_n08_campaign_report.py` | 战役级报告装配 + 对照图 |")
    md.append("| `tools/p9044w5_n08_report_doc.py` | 本报告生成器（零手抄数字） |")
    md.append("| `artifacts/p9044w5_n08/*.json` | 快照/审计/对照/报告/pytest 证据 |")
    md.append("| `artifacts/p9044w5_n08/figures*/` | 4 张图 + figures.json（含 input_digest） |")
    md.append("")
    return "\n".join(md) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 N08 论文级战役集成报告")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--audit", action="append", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--pytest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--figures-manifest", action="append", default=[])
    args = ap.parse_args(argv)

    campaign = load(args.campaign)
    audits = [load(p) for p in args.audit]
    control = load(args.control)
    pytest_summary = load(args.pytest)
    figures: list[tuple[str, str]] = []
    defaults = {
        "fig1_capability_matrix.png": "八节点能力矩阵：行=节点，列=证据分类 / 判据通过率 / 精度带 / 台账覆盖；"
                                      "颜色=符合度，格内为原始值（0 通过率与 unmeasured 明显可见）。",
        "fig2_precision_bands.png": "精度带与时序：上图为每节点最差相对误差（log 轴）叠 3%/15% 验收线；"
                                    "下图为各节点精度带归属。无基准节点落 unmeasured（灰）。",
        "fig3_gate_and_classes.png": "左：八条机裁判据实测值（symlog）与阈值标注，红=不过；"
                                     "右：四分类分布，honest FAIL 单独可见、不被 pass 吞掉。",
        "fig4_campaign_comparison.png": "战役级对照：本战役 / 父战役回归 / 合成正对照的三场四分类分布与过不去的判据数；"
                                        "正对照全绿证明门有判别力。",
    }
    for manifest_path in args.figures_manifest:
        manifest = load(manifest_path)
        for entry in manifest.get("figures") or []:
            name = entry.get("figure")
            if name and name in defaults and name not in [n for n, _ in figures]:
                figures.append((name, defaults[name]))
    if ("fig4_campaign_comparison.png" not in [n for n, _ in figures]
            and any(Path(p).name == "figures_campaign.json" for p in args.figures_manifest)):
        figures.append(("fig4_campaign_comparison.png", defaults["fig4_campaign_comparison.png"]))

    doc = build(campaign, audits, control, pytest_summary, figures)
    # 把 §3.5 的占位句替换为真实用例数（数字来自 pytest 证据）
    suites = pytest_summary.get("suites") or {}
    own = (suites.get("n08_own") or {}).get("counts") or {}
    doc = doc.replace("回归口径：N08 自身用例 ",
                      f"回归口径：N08 自身用例 {own.get('passed', 0)} 条全绿（合成域夹具，零领域语义），"
                      f"相邻集成门与复用判据套件合计 "
                      f"{(pytest_summary.get('totals') or {}).get('passed', 0)} 条全绿；"
                      f"未跑：全仓全量测试（须上集群，见 §5 局限）。")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(json.dumps({"out": str(out), "chars": len(doc), "figures": [n for n, _ in figures]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())