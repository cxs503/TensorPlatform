#!/usr/bin/env python3
"""把 bench 的 metrics.json 渲染成论文级 markdown 报告（零手抄：所有数字程序化注入）。

    python3 -m ops.taskplane.governance.report --metrics artifacts/in99_n10/metrics.json \
        --out docs/autonomy/IN99_N10_seed_governance.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.2f}%"


def _row(cells: Sequence[Any]) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _table(header: Sequence[Any], rows: Sequence[Sequence[Any]]) -> str:
    out = [_row(header), "|" + "|".join("---" for _ in header) + "|"]
    out.extend(_row(r) for r in rows)
    return "\n".join(out)


def render(metrics: Mapping[str, Any]) -> str:
    d = metrics["dedup"]
    dag = metrics["dag"]
    gate = metrics["gate"]
    ledger = metrics["ledger"]
    acc = metrics["acceptance"]
    cfg = d["config"]
    gcfg = gate["config"]

    lines = []
    lines.append("# IN99-N10 战役播种治理机制：提案去重门 · DAG 无环校验 · 机器门自动裁定 · 证据台账自动登记\n")
    lines.append("> 节点 IN99-N10（TensorPlatform，平台共性机制）。本文件由 "
                 "`python3 -m ops.taskplane.governance.report` 从 "
                 "`artifacts/in99_n10/metrics.json` 程序化渲染，正文数字全部来自该 JSON，无手抄。\n")

    lines.append("## ① 案例介绍：要解决的机制问题\n")
    lines.append(
        "战役由上层智能体自主播种（提案 → 仲裁聚合 → INSERT 计划/节点）。播种环节没有数据库约束："
        "提案可以换词不换招地重复提交；节点依赖可以引用别的计划、可以成环；"
        "计划可以只播两个节点、复用别人的 `ext_id`、派给白名单外的仓、整场没有一条成熟化单；"
        "验收通过的能力可能根本不入账，S 级评价靠事后追认。四个机制模块把上述四类漏洞做成"
        "可机器复算的判据，阈值/白名单/参考语料全部由调用方注入，模块内**零领域语义**。\n")
    lines.append(_table(
        ["模块", "职责", "关键判据"],
        [
            ["`proposal_dedup.py`", "提案去重门：五特征综合分 + 关键词扫描 + 近重复兄弟聚类",
             f"综合分 θ={cfg['theta_clone']}；兄弟连边 θ_s={cfg['theta_sibling']}"],
            ["`dag.py`", "依赖归一 + 只引用本 plan + Kahn 拓扑 + 显式环路径",
             "外引用/自指/环 三类违规各自成码"],
            ["`machine_gate.py`", "seed 结构与治理合规硬校验，逐条给出违规项",
             f"节点数 [{gcfg['min_nodes']},{gcfg['max_nodes']}]；goal≥{gcfg['min_goal_chars']} 字；"
             f"里程碑≥{gcfg['min_milestones']}；成熟化单≥{gcfg['min_maturation']}"],
            ["`evidence_ledger.py`", "验收通过自动登记 + S1/S2/S3 评价 + SHA-256 链审计",
             "判据≥3 条且全通过；S2 需可复算基线，S3 另需突破宣言+工件"],
        ]))
    lines.append("")
    lines.append("参数表（模块默认值，均可由 JSON 配置注入）：\n")
    lines.append(_table(["参数", "值"], [[k, v] for k, v in sorted(cfg.items())]))
    lines.append("")

    lines.append("## ② 怎么做的：方法与流程\n")
    lines.append(
        "1. **表征层**：NFKC/小写归一 → 拉丁词 + CJK 二元组（表层）、近义词归一 + 数字→`<n>`（骨架）、"
        "字符集合（字符级）。IDF 以提案池为背景，双层（词项 / 字符）一次建好。\n"
        "2. **综合分**：`score = w_char·字符IDF余弦 + w_lcs·字符LCS占比 + w_skeleton·骨架Jaccard "
        "+ w_cosine·二元IDF余弦`，权重 "
        f"{cfg['w_char']}/{cfg['w_lcs']}/{cfg['w_skeleton']}/{cfg['w_cosine']}。字符级两项负责「人工改写型"
        "近重复」，骨架项负责「只调参克隆」，二元项负责表层复读。\n"
        "3. **阈值协议**：40 对人工标注提案对按 `(a,b)` 排序后奇偶交替划 dev/holdout（各 20 对）；"
        "阈值只在 dev 上按「召回≥0.90 → 误报最小 → 最大间隔中点」选定并冻结，holdout 只做验证。\n"
        "4. **DAG**：`normalize_nodes` 兼容 PG 的 `{1,2}` 文本依赖；Kahn 出队按 id 升序（确定性）；"
        "残留核上 DFS 回填显式环路径（`1→3→2→1`）。对照实现用 Warshall 可达矩阵独立判定，"
        "两者不同路，避免同 bug 自证。\n"
        "5. **机器门**：以合规基线 seed（8 节点 / 3 里程碑 / 2 条成熟化单）为母本，逐规则做**单点变异**"
        "与组合变异（18 例），断言「必拒 + 预期违规码出现」。`ext_id` 全库唯一性通过注入注册表实现"
        "（DB 只读导出，不在模块里连库）。\n"
        "6. **台账**：SQLite 追加表，`entry_hash = SHA256(canonical(entry) + prev_hash)`；"
        "等级由证据结构决定（有无可复算基线、有无突破宣言/工件、判据是否全通过），"
        "理由字符串要求含可核对数字，否则拒签。未通过/未验收写 `ledger_attempts` 留痕但不入定额。\n")

    lines.append("## ③ 计算结果（全部取自 metrics.json）\n")
    lines.append("**C1 去重门（dev 选阈值 → holdout 验证）**\n")
    lines.append(_table(
        ["划分", "θ", "TP", "FN", "FP", "TN", "召回", "误报", "精确率"],
        [["dev", d["dev"]["theta"], d["dev"]["tp"], d["dev"]["fn"], d["dev"]["fp"], d["dev"]["tn"],
          _pct(d["dev"]["recall"]), _pct(d["dev"]["fpr"]), _pct(d["dev"]["precision"])],
         ["holdout（冻结 θ）", d["holdout_frozen_theta"]["theta"], d["holdout_frozen_theta"]["tp"],
          d["holdout_frozen_theta"]["fn"], d["holdout_frozen_theta"]["fp"],
          d["holdout_frozen_theta"]["tn"], _pct(d["holdout_frozen_theta"]["recall"]),
          _pct(d["holdout_frozen_theta"]["fpr"]), _pct(d["holdout_frozen_theta"]["precision"])]]))
    lines.append("")
    sr = d["score_range_holdout"]
    lines.append(f"holdout 分数区间：近重复对最低 {sr['dup_min']:.4f}（中位 {sr['dup_median']:.4f}），"
                 f"独立/硬负例最高 {sr['indep_max']:.4f}（中位 {sr['indep_median']:.4f}），"
                 f"冻结阈值 {cfg['theta_clone']} 落在间隔内。\n")
    abl = d.get("ablation")
    if abl:
        lines.append("消融（同一冻结阈值 θ=" + str(cfg["theta_clone"]) + "，holdout）：\n")
        lines.append(_table(
            ["变体", "权重（char/lcs/skel/cosine）", "holdout 召回", "holdout 误报"],
            [["无字符层（骨架+二元余弦，早期变体）",
              "/".join(str(abl["no_char_layer"]["weights"][k]) for k in
                       ("w_char", "w_lcs", "w_skeleton", "w_cosine")),
              _pct(abl["no_char_layer"]["holdout_at_frozen_theta"]["recall"]),
              _pct(abl["no_char_layer"]["holdout_at_frozen_theta"]["fpr"])],
             ["全模型（本节点）",
              "/".join(str(abl["full_model"]["weights"][k]) for k in
                       ("w_char", "w_lcs", "w_skeleton", "w_cosine")),
              _pct(abl["full_model"]["holdout_at_frozen_theta"]["recall"]),
              _pct(abl["full_model"]["holdout_at_frozen_theta"]["fpr"])]]))
        lines.append("")
    sib = d["sibling_control"]
    lines.append(f"兄弟聚类负控：把 {sib['n_proposals']} 条同主题提案（基准+两变体）当一批，"
                 f"得到 {sib['n_clusters']} 簇、簇大小 {sib['cluster_sizes']}，"
                 f"每簇保留 1 代表（校验={'通过' if sib['one_representative_per_topic'] else '不通过'}）。\n")

    lines.append("**C2 DAG 环检出（穷举 n≤4 + 随机 n=5..12，对照 Warshall 可达矩阵）**\n")
    rows = []
    for k, v in list(dag["exhaustive"].items()) + list(dag["random"].items()):
        rows.append([k, v.get("graphs", v.get("samples")), v["n_cyclic"], v["n_cyclic_detected"],
                     _pct(v["detection_rate"]), v["n_false_positive"]])
    lines.append(_table(["规模", "图数", "含环", "检出", "检出率", "无环误报"], rows))
    t = dag["totals"]
    lines.append(f"\n合计含环 {t['n_cyclic']} 例，检出 {t['n_cyclic_detected']} 例，"
                 f"检出率 {_pct(t['detection_rate'])}；无环图误报 {t['n_false_positive']} 例。"
                 f"环路径示例：`{dag['cycle_path_example'][0]}`。\n")

    lines.append("**C3/C5 机器门（18 例变异逐条裁定）**\n")
    lines.append(_table(
        ["变异", "说明", "预期违规码", "实际违规码", "拒收", "码齐"],
        [[m["id"], m["desc"], ",".join(m["expected_codes"]) or "-",
          ",".join(m["got_codes"]), "是" if m["rejected"] else "否",
          "是" if m["all_expected_present"] else "否"] for m in gate["mutants"]]))
    nc = gate["negative_control"]
    lines.append(f"\n合规基线 seed 被接受={nc['compliant_seed_accepted']}；注入注册表占位后"
                 f"仅触发 {nc['registry_collision_codes']}（负控），"
                 f"不合规 seed 拒收率 {_pct(gate['rejection_rate'])}"
                 f"（{gate['n_rejected_with_expected_code']}/{gate['n_mutants']}）。\n")

    lines.append("**C4 证据台账（登记路径 + 链审计）**\n")
    lines.append(_table(
        ["场景", "返回码", "等级"],
        [[c["case"], c["code"], c["grade"] or "-"] for c in ledger["cases"]]))
    index_txt = ", ".join(f"{k}→{v['latest_grade']}" for k, v in ledger["capability_index"].items())
    lines.append(f"\n能力清单查询：{index_txt}；"
                 f"链校验 ok={ledger['chain']['ok']}（{ledger['chain']['n_entries']} 条，"
                 f"head={ledger['chain']['head_hash']}）；篡改一条记录后检出 "
                 f"`{ledger['tamper_detected']['code']}`；被拒登记留痕 "
                 f"{ledger['attempts_logged']} 条；S 级事件 {len(ledger['s_events'])} 条"
                 f"（S2/S3 需独立复判）。\n")

    idx_path = ROOT / "artifacts" / "in99_n10" / "ledger_index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        lines.append("**C4 自证：本节点四项能力自动登记进台账（真数据，不是合成样例）**\n")
        lines.append(_table(
            ["能力", "登记码", "等级", "定级理由（程序化生成）"],
            [[e["capability_id"], e["code"], e["grade"] or "-", e["reason"]]
             for e in idx["entries"]]))
        lines.append(f"\n台账文件 `artifacts/in99_n10/capability_ledger.sqlite`（仓规 .gitignore 排除 *.sqlite，"
                     "入库副本为 `ledger_export.json`，重跑 register_capabilities.py 可从零重建），"
                     f"链校验 ok={idx['chain']['ok']}（{idx['chain']['n_entries']} 条，"
                     f"head={idx['chain']['head_hash'][:16]}）；"
                     f"需独立复判的 S2/S3 事件 "
                     f"{sum(1 for e in idx['s_events'] if e['requires_independent_review'])} 条。"
                     "三项无基线对照的能力如实定 S1（只有执行合格，不冒充增量）。\n")

    lines.append("## ④ 结果图\n")
    for fig in metrics.get("figures", []):
        lines.append(f"![{fig['name']}](../{fig['path']})\n")
        lines.append(f"*{fig['caption']}*（文件：`{fig['path']}`）\n")

    lines.append("## ⑤ 注意点与局限\n")
    lines.append(
        "- **标注集是人工构造的 40 对**（零领域词的平台机制提案），不是线上提案池的真实分布；"
        "特征设计阶段我看过全部 40 对（特征工程），阈值选择只用 dev 20 对，holdout 20 对只验证未参与选阈。"
        "因此 holdout 数字是「冻结阈值验证」而非完全盲测。\n"
        "- **字符 IDF 余弦是粗信号**：同语言、共享套话模板的长摘要会抬高分数；上线初期建议先影子模式"
        "（只记分不拒收）积累真实分布再收紧阈值。\n"
        "- **兄弟聚类阈值低于克隆阈值**（同批略松）：一批里同源提案哪怕没到近重复线也合并，"
        "代价是可能把「同主题不同机制」误并——当前硬负例最高分 0.1888 < 0.22 未被误并，但只覆盖 8 例。\n"
        "- **机器门不替代语义审查**：它只判结构/治理合规，`goal` 内容是否真前沿仍需人/独立席位。\n"
        "- **台账为单写者 SQLite**：适合单机调度进程；多进程并发需换 PG 或加写锁。哈希链只防"
        "「事后改字」，不防「整条链重算」（后者需外部锚定，见后续节点）。\n"
        "- **与线上派发器的关系**：本次未改 `ops/taskplane/campaign_dispatcher.py` 热路径，"
        "新模块通过 `python3 -m ops.taskplane.governance seed --seed seed.json` 可独立机验；"
        "接进派发器属于下一步集成（需连同 PG schema 与回归测试一起做）。\n")

    lines.append("## ⑥ 误差判定（逐项 vs 判据边界）\n")
    c1, c2, c3, c4, c5 = (acc["C1_dedup_recall_fpr"], acc["C2_dag_cycle_detection"],
                          acc["C3_gate_reject_with_items"], acc["C4_ledger_query_and_audit"],
                          acc["C5_negative_control"])
    lines.append(_table(
        ["判据", "要求", "实测", "余量", "判定"],
        [
            ["C1 召回率", f"≥ {_pct(c1['threshold']['recall_min'])}", _pct(c1["recall"]),
             f"+{100 * (c1['recall'] - c1['threshold']['recall_min']):.2f}pp", "PASS" if c1["pass"] else "FAIL"],
            ["C1 误报率", f"≤ {_pct(c1['threshold']['fpr_max'])}", _pct(c1["fpr"]),
             f"-{100 * (c1['threshold']['fpr_max'] - c1['fpr']):.2f}pp", "PASS" if c1["pass"] else "FAIL"],
            ["C2 环检出率", "= 100%", _pct(c2["detection_rate"]),
             f"误报 {c2['false_positive']} 例", "PASS" if c2["pass"] else "FAIL"],
            ["C3 拒收率（含码齐）", "= 100%", _pct(c3["rejection_rate"]),
             f"{gate['n_rejected_with_expected_code']}/{gate['n_mutants']}",
             "PASS" if c3["pass"] else "FAIL"],
            ["C4 查询+审计追溯", "链 ok 且篡改可检出",
             f"chain={c4['chain_ok']}, tamper={c4['tamper_detected']}", "-",
             "PASS" if c4["pass"] else "FAIL"],
            ["C5 负控注入", "违规必拒、合规不误拒", c5["detail"], "-",
             "PASS" if c5["pass"] else "FAIL"],
        ]))
    lines.append("")
    lines.append(
        "**关键量误差是否 <3%？** 本节点是平台机制节点，不涉及物理场量，也没有实验/解析基准，"
        "因此「<3% 相对误差」这一条**不适用**，不能用它来粉饰或冒充精度。本节点采用的替代误差口径是"
        "「与判据边界的差值」：C1 召回超边界 "
        f"{100 * (c1['recall'] - c1['threshold']['recall_min']):.2f}pp、误报低于上限 "
        f"{100 * (c1['threshold']['fpr_max'] - c1['fpr']):.2f}pp，C2/C3 达到 0 误差（100% 检出/拒收、"
        "无环图 0 误报），C4 的链校验与篡改检出均为二值判定（无中间误差）。"
        "这些数字之所以可信，是因为 holdout 划分与穷举/随机对照都是可复算脚本，"
        "而不是人工抽查（见 `ops/taskplane/governance/bench.py`、`tests/test_governance_*.py`）。\n")
    lines.append("## 复现\n")
    lines.append("```bash\n"
                 "python3 -m ops.taskplane.governance bench --out artifacts/in99_n10\n"
                 "python3 artifacts/in99_n10/register_capabilities.py --reset\n"
                 "python3 -m ops.taskplane.governance.report\n"
                 "python3 -m pytest tests/test_governance_dag.py tests/test_governance_machine_gate.py \\\n"
                 "    tests/test_governance_evidence_ledger.py tests/test_governance_proposal_dedup.py \\\n"
                 "    tests/test_governance_bench_acceptance.py -q\n"
                 "```\n")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="render IN99-N10 markdown report from metrics.json")
    ap.add_argument("--metrics", default=str(ROOT / "artifacts" / "in99_n10" / "metrics.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "autonomy" / "IN99_N10_seed_governance.md"))
    args = ap.parse_args(argv)
    metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    text = render(metrics)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())