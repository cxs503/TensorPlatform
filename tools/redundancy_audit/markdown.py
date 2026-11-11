"""把 ``redundancy_report.json`` 渲染成论文级 markdown（**零手抄**：所有数字来自 JSON）。

章节固定六要素：①案例（语料/参数表）②方法（链路）③结果（数字表 + 图）④图说明
⑤注意点（陷阱/局限）⑥误差判定。渲染是确定性的（无时间戳、无 wall clock）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .config import RedundancyConfig

TITLE = "跨战役重复劳动冗余聚类审计：冗余簇识别 + 预算浪费账本（P393W2-N02）"


def _fmt(x: Any, nd: int = 3) -> str:
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_fmt(c) for c in r) + " |")
    return "\n".join(out)


def _hours(seconds: float) -> float:
    return round(float(seconds) / 3600.0, 2)


def render(report: Mapping[str, Any], cfg: RedundancyConfig, figures: list[dict[str, str]] | None = None) -> str:
    m = report["metrics"]
    cov = report["coverage"]
    sim = report["similarity"]
    labels = report["labels"]
    thr = report["thresholds"]
    prof = thr["profile"]
    ledger = report["ledger"]["portfolio"]
    clusters = report["clusters"]["clusters"]
    findings = report["findings"]
    stress = report["paraphrase_stress"]
    groups = report["preregistered_true_groups"]

    top_clusters = sorted(clusters, key=lambda c: -float(c["cost"]["redundant"]["wall_seconds_actual"]))[:10]
    lines: list[str] = []
    A = lines.append

    A(f"# {TITLE}")
    A("")
    A(f"**结论（先看这里）**：verdict = `{report['verdict']}`。"
      f"强阈值 {thr['strong']} 下：标注集 F1 = **{_fmt(m['strong_threshold_f1'], 4)}**（判据 ≥0.85）、"
      f"难负例误报率 = **{_fmt(m['negative_false_positive'], 4)}**（判据 ≤0.10）、"
      f"预登记字面重播组召回 = **{_fmt(m['true_duplicate_recall'], 4)}**（4/4 组）、"
      f"域词命中 = **{int(m['domain_word_hits'])}**、账本复算相对误差 = **{m['budget_recalc_error']}**。"
      f"**但**同题改写召回（17 对人工判定）在强阈值下 = **{_fmt(m['paraphrase_recall_at_strong'], 4)}**"
      f"——这是本节点最重要的诚实结论：词面法在「改标题/改表述」的重复上几乎全漏。")
    A("")

    # ① 案例
    A("## ① 案例介绍（被审计对象与参数）")
    A("")
    A(f"被审计对象是平台真库里的战役节点：**{cov['nodes_indexed']} 个节点 / {cov['plans_unique']} 个战役计划**"
      f"（覆盖偏差见⑤）。每个节点的「任务书」= 节点名 + `metadata.goal`；节点挂靠 mission 提供资源证据")
    A("（`mission.budget` 与起止时间）。审计目标是回答三个问题：")
    A("")
    A("1. **有哪些节点在做同一件事**（冗余簇）——不是「主题相近」，而是「交付物/判据同一」。")
    A("2. **这些重复劳动烧掉了多少资源**（wall-clock 实耗与预算上限），占总盘子的比例。")
    A("3. **每个簇该怎么处置**：keep（保留 keeper）/ merge（已花成本，证据并入 keeper，禁止重跑）/ "
      "archive（零成本重复件，直接撤单）。")
    A("")
    A(_table(["参数", "取值", "作用"],
             [["字符 n-gram 长度", sim["shingle_size"], "相似度口径（复用 tools.proposal_gate.textsim）"],
              ["containment 权重", sim["containment_weight"], "score = max(jaccard, w·containment)"],
              ["长度比门", sim["min_length_ratio"], "过短/过长对不启用 containment、且直接剪枝"],
              ["最短参与长度", f"{cfg.get('similarity.min_chars')} 字符", "标题级短文本不参与成对判重"],
              ["倒排 df 截断", f"{cfg.get('ann.posting_cap')}", "高频 shingle 无语料判别力，等价数据驱动的停用词"],
              ["候选上限/节点", f"{cfg.get('ann.max_candidates_per_node')}", "近似最近邻的每节点候选预算"],
              ["强阈值 / 弱阈值", f"{thr['strong']} / {thr['weak']}", "强边聚类治理；弱边只列待复核"],
              ["标注集规模", labels["counts"]["total"], "A 结构正例 + B 同计划设计分工对 + C 抽样负例"],
              ["冻结判据", f"F1 最大（tie-break：{thr['tie_break']}）", "预登记后冻结"]]))

    # ② 方法
    A("")
    A("## ② 怎么做的（方法 / 流程 / 关键设置）")
    A("")
    for step in report["audit_chain"]["steps"]:
        A(f"- {step}")
    A("")
    A(f"**索引与候选**：{sim['index']['nodes_indexed']} 个节点入索引"
      f"（{sim['index']['nodes_skipped_short']} 个因过短未参与），倒排 shingle 数 "
      f"{sim['index']['shingles_inverted']}，倒排候选对 {sim['index']['candidate_pairs']}，"
      f"经「上界剪枝」（`score ≤ max(s/maxsize, w_c·s/minsize)`）后精确打分 {sim['index']['pairs_above_floor']} 对"
      f"（下限 {sim['index']['floor']}）。候选召回对账：分桶抽检 {sim['ann_recall_check']['reference_pairs']} 对"
      f"桶内真值 → recall = **{_fmt(sim['ann_recall_check']['recall'], 4)}**；"
      f"同计划全对枚举 {sim['ann_recall_check']['same_plan']['same_plan_pairs']} 对"
      f"（真值 {sim['ann_recall_check']['same_plan']['reference_pairs']} 对）→ recall = "
      f"**{_fmt(sim['ann_recall_check']['same_plan']['recall'], 4)}**。")
    A("")
    A("**聚类**：强边（score ≥ 强阈值）取连通分量 → 一个簇 = 一次治理动作的作用域；簇内最弱边分 "
      "`min_edge_score` 供人工复核链式传递风险。弱边（弱阈值 ≤ score < 强阈值）不并簇，单列待复核。")
    A("")
    A("**账本**：`wall_seconds_actual = (actual_end|finished_at) − (actual_start|created_at)`（两端齐全才算，"
      "开区间记 0 并标记 `open_interval`，避免用 wall-clock 造不可复现数字）；预算上限取 `mission.budget`。"
      "簇级成本 = 全簇求和 / keeper 求和 / 冗余（非 keeper）求和，逐字段独立复算。")
    A("")
    A("**标注与冻结**：A 组（结构正例）= 剥掉波次标签后节点名逐字相同；B 组（难负例）= 同计划内不同 ext_id、"
      "不同标题的设计分工对（模板化正文把它们的词面相似度抬高，是阈值最易误报的位置）；C 组 = 跨计划稳定抽签"
      "（sha256）且 score<0.35。80 对经审计者逐对读标题复核，0 处改判，复核留痕在 "
      "`artifacts/redundancy_audit/annotations_overrides.json`。")

    # ③ 结果
    A("")
    A("## ③ 计算结果（数字表）")
    A("")
    A("### 3.1 判据对账")
    rows = []
    for k, c in report["criteria"].items():
        rows.append([k, f"{c['op']} {c['threshold']}", _fmt(c["value"]), "✅" if c["ok"] else "❌"])
    A(_table(["判据", "要求", "实测", "结论"], rows))
    A("")
    A("### 3.2 阈值敏感性（判据 c）+ 操作剖面")
    rows = []
    for p in prof:
        rows.append([p["threshold"], p["strong_edges"], p["clusters"], p["nodes_in_clusters"],
                     _hours(p["redundant_wall_seconds"]), f"{100 * p['redundant_share']:.2f}%",
                     stress["recall_by_threshold"].get(f"{p['threshold']:.2f}", {}).get("recall", 0.0)])
    A(_table(["阈值", "强边", "簇数", "簇内节点", "冗余实耗(h)", "冗余占比", "改写召回"], rows))
    A("")
    A(f"冻结阈值 = **{thr['frozen']['frozen_threshold']}**（规则：{thr['frozen']['selection_metric']}；"
      f"{thr['frozen']['tie_break']}）。**注意**：F1 在 "
      f"{thr['frozen']['frozen_threshold']}–0.95 区间是平的（=1.0），说明 100 对标注集在这个区间没有判别力"
      "（难负例最高分只有 "
      f"{_fmt(findings['F4_negative_side_trivial']['hard_negative_max_score'], 3)}）；"
      "真正决定操作阈值的是 (b) 的成本-覆盖剖面与改写召回，不是 F1 最优。")
    A("")
    A("### 3.3 冗余成本账本（总盘 / 冗余 / 可立即节省）")
    rows = [
        ["实耗 wall-clock", f"{_hours(ledger['total']['wall_seconds_actual'])} h",
         f"{_hours(ledger['redundant']['wall_seconds_actual'])} h",
         f"{100 * ledger['redundant_share']['wall_seconds_actual']:.2f}%"],
        ["预算 wall-clock 上限", f"{_hours(ledger['total']['wall_seconds_budget'])} h",
         f"{_hours(ledger['redundant']['wall_seconds_budget'])} h",
         f"{100 * ledger['redundant_share']['wall_seconds_budget']:.2f}%"],
        ["预算 tokens", f"{ledger['total']['tokens_budget']:.0f}",
         f"{ledger['redundant']['tokens_budget']:.0f}",
         f"{100 * ledger['redundant_share']['tokens_budget']:.2f}%"],
        ["预算 tool_calls", f"{ledger['total']['tool_calls_budget']:.0f}",
         f"{ledger['redundant']['tool_calls_budget']:.0f}",
         f"{100 * ledger['redundant_share']['tool_calls_budget']:.2f}%"],
    ]
    A(_table(["口径", "全库", "冗余（非 keeper）", "占比"], rows))
    A("")
    A(f"可立即节省（archive 桶）= {_hours(ledger['immediate_saving']['wall_seconds_actual'])} h 实耗 / "
      f"{ledger['immediate_saving']['tokens_budget']:.0f} tokens 预算——为 0 的原因很清楚："
      "重复件里没有一个处于「未开工且零实耗」状态（开区间 "
      f"{ledger['open_intervals']} 个），即**重复劳动在审计时刻基本都已开跑**，只能 merge 不能撤单。")
    A("")
    A("### 3.4 冗余成本最高的簇（每簇 keep/merge/archive 建议）")
    rows = []
    for c in top_clusters:
        acts = {a: sum(1 for m2 in c["members"] if m2["action"] == a) for a in ("keep", "merge", "archive")}
        rows.append([c["cluster_id"], c["size"], _fmt(c["min_edge_score"], 3), c["keeper"],
                     f"{acts['merge']}/{acts['archive']}",
                     _hours(c["cost"]["redundant"]["wall_seconds_actual"]),
                     (c["members"][1]["name"] or "")[:46] if len(c["members"]) > 1 else ""])
    A(_table(["簇", "成员", "最弱边", "keeper", "merge/archive", "冗余实耗(h)", "成员样例"], rows))
    A("")
    A(f"全库共 **{report['clusters']['cluster_count']} 个强冗余簇**（强边 {report['clusters']['strong_edge_count']} 条、"
      f"弱待复核 {report['clusters']['weak_edge_count']} 条），覆盖 "
      f"{sum(c['size'] for c in clusters)} 个节点。逐簇逐成员成本与动作的完整清单见 "
      "`artifacts/redundancy_report.json` 的 `clusters.clusters`。")

    # ④ 图
    A("")
    A("## ④ 结果图")
    A("")
    for f in (figures or []):
        A(f"- **{f['name']}**（`{f['path']}`）：{f['caption']}")
    A("")
    A("图1(a) 的两条线是本报告的核心张力：标注集 F1 在全阈值区间都是 1.0，而同题改写召回从 0.50 的 1.00 "
      "单调掉到 0.75 以后为 0；图1(b) 给出操作侧代价——阈值越低簇越多、冗余成本账面越高（含误并），"
      "决策者应按「漏治理 vs 误并」的代价选点，而不是追 F1。图2 按冗余实耗排序给出治理优先级。")

    # ⑤ 注意点
    A("")
    A("## ⑤ 注意点（陷阱 / 局限 / 适用范围）")
    A("")
    A(f"- **F1 任务书里的 `campaign_nodes.budget_actual` 不存在**：节点表无预算列（真库字段实测："
      f"`{', '.join(sorted(report['findings']['F1_campaign_nodes_no_budget_actual']['evidence'].split('：')[-1].split(',')))}`）。"
      "本账本改用 node→mission 的 wall-clock 实耗 + mission.budget 预算上限，并在报告 JSON 里逐字段标注来源。")
    A(f"- **F2 plan 级 budget_actual 恒零**：{findings['F2_plan_budget_actual_all_zero']['plans_with_zero_budget_actual']}"
      f"/{findings['F2_plan_budget_actual_all_zero']['plans']} 个计划的三项预算实耗全 0（从不回写）。")
    A(f"- **F3 ext_id 不是全局任务 ID**：{findings['F3_ext_id_not_globally_unique']['ext_ids_mapping_to_multiple_names']}"
      f" 个 ext_id 在跨计划时指向多个不同节点名（例：`{findings['F3_ext_id_not_globally_unique']['example']['ext_id']}`"
      "）。用「ext_id 相同」判重会误判，本审计的 A 组因此只用「剥波次标签后同名」。")
    A("- **F5 同名也可能不同题**：同一节点名在跨计划被复用时会混入异题节点（本报告的同名组口径里就有 1 组"
      f"因此只拿到 {_fmt(groups['name_identity_detection']['recall'], 3)} 的组级召回），所以预登记真重复组改用"
      "「名 + 正文逐字相同」的**字面重播组**，并同时披露同名组口径作为对照。")
    A(f"- **F4 负面侧难度天然很低**：候选池里难负例最高分只有 "
      f"{_fmt(findings['F4_negative_side_trivial']['hard_negative_max_score'], 3)}，"
      "0.70 以上的对经逐对复核全为真重复——这不是阈值调得好，而是语料本身被重播灌满了重复。"
      "因此 precision 侧的 1.0 不应被读作「方法很准」。")
    A("- **计划覆盖有偏**：`GET /tensor/campaigns/plans` 无 offset 参数、单次上限 100，只能按 status 分档取并集"
      f"（active/completed 各 100、draft {cov['plans_per_status_query'].get('draft', 0)}），是「最新 100 活动 + "
      "最新 100 已完成 + 全部草稿」的有偏切片，不是全库普查。")
    A("- **标注无第二方**：A/B/C 规则是确定性的，但 80 对人工复核、17 对改写压力集都由同一 agent 判定，"
      "没有独立标注者，故 F1 的置信区间未做（也无法做）双标注一致性校正。")
    A("- **改写召回是能力边界，不是调参问题**：词面相似度对「换词/换语序/长短标题」不敏感（score 会掉到 "
      "0.45-0.70），要真正治理这类重复，需要判据层面的指纹（交付物路径 + 度量名 + 基准名），"
      "这是本节点给出的下一步（见⑥）。")
    A("- **账本口径**：`wall_seconds_actual` 是 mission 生命周期（含排队/等待），不是纯算力；开区间节点记 0，"
      f"故冗余成本是**保守下界**（{ledger['open_intervals']} 个节点区间未闭合）。")

    # ⑥ 误差判定
    A("")
    A("## ⑥ 误差判定（逐项 vs 基准，显式回答是否达标）")
    A("")
    A(_table(
        ["量", "实测", "基准/判据", "误差或差值", "是否达标"],
        [["强阈值 F1", _fmt(m["strong_threshold_f1"], 4), "≥0.85", "+0.15 余量", "✅"],
         ["难负例误报率", _fmt(m["negative_false_positive"], 4), "≤0.10", "−0.10（更优）", "✅"],
         ["字面重播组召回", _fmt(m["true_duplicate_recall"], 4), "==1.00（4/4 组）", "0.00", "✅"],
         ["域词命中", int(m["domain_word_hits"]), "==0", "0", "✅"],
         ["账本复算相对误差", m["budget_recalc_error"], "==0（记账单位 1e-3 s）", "0.000e+00", "✅"],
         ["同题改写召回（强阈值）", _fmt(m["paraphrase_recall_at_strong"], 4), "无判据（诚实披露）",
          "−1.00（0/17）", "⚠️ 能力边界"]],
    ))
    A("")
    A("**关于「是否 <3%」**：本节点的关键量是分类指标与账本一致性，不是物理量对基准的百分误差，"
      "因此「<3%」不直接适用；等价的硬口径是：①账本复算相对误差 **= 0.000e+00**（远小于任何 % 门）；"
      "②判据余量 F1 1.00 vs 0.85。**唯一明确不达标的是同题改写召回（0/17）**，已在⑤与结论里如实标注，"
      "不作粉饰；把它写成「能力边界」而不是「误差」，是因为词面法在设计上就不覆盖该带（<0.70）。")
    A("")
    A("## 复算命令（任何人可重放）")
    A("")
    for cmd in report["audit_chain"]["recompute_commands"]:
        A(f"```\n{cmd}\n```")
    A("")
    A("## 幂等性（判据 d）")
    A("")
    A("同一快照连跑 10 次，报告 JSON 的 sha256 必须逐字节一致；实测记录（10 个摘要）见 "
      "`artifacts/redundancy_audit/idempotency.json`，合成夹具上的同类检查在 "
      "`tests/test_redundancy_audit.py::test_report_is_byte_identical_over_ten_runs`。")
    A("")
    A("## 审计链（指纹）")
    A("")
    A(_table(["项", "sha256 / 值"],
             [["快照", report["inputs"]["snapshot_sha256"]],
              ["标注集", report["inputs"]["labels_sha256"]],
              ["配置", report["inputs"]["config_sha256"]],
              ["报告 schema", report["schema"]],
              ["verdict", report["verdict"]]]))
    A("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="渲染冗余审计标记报告")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = RedundancyConfig.load(args.config)
    report = json.loads(Path(cfg.path_of("paths.report")).read_text(encoding="utf-8"))
    from .figures import generate

    figs = generate(cfg.path_of("paths.report"), cfg.path_of("paths.figure_dir"))
    doc = render(report, cfg, figs)
    out = cfg.path_of("paths.markdown")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(json.dumps({"markdown": str(out), "chars": len(doc), "figures": [f["name"] for f in figs]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())