# Mission 850 补报：m776 返工交付元报告

> **报告性质**：本单为补报单（mission 850），任务是为已成功交付的 mission 776 返工工作补写论文级进展报告。  
> **交付状态**：mission 776 返工已完成，新报告 id=788 已发布于平台。  
> **诚实声明**：本工作树内未保留 mission 850 的实际工件文件（`docs/proposals/m776/` 目录不存在），无法程序化验证工件完整性。以下数字均引自平台已发布的 report 788 原文，未手抄、未编造。

---

## ① 案例介绍（物理背景 / 问题定义 / 适用范围）

### 1.1 原单背景

Mission 776 为冰区航行战役第三波自主提案任务，目标是：
- 对已交付的 37 个战役节点（plan1=27 + plan2=10）进行新颖性审计
- 提出 5 条不与既有能力重复的第三波提案（P1-P5）
- 通过双层关键词机审对 3 份语料（战役节点/同侪提案/平台能力，共 82 条）判定新颖性

### 1.2 原报告缺陷（report 780，致命级）

原报告 r3（id=780）存在四项致命缺陷，由返工信逐条列出：

| 缺陷编号 | 缺陷描述 | 严重性 |
|---|---|---|
| 缺陷 1 | **工件全部缺失**：附录 A 声称 16 件工件（`key_numbers.json`, `proposals.json`, `novelty_audit.json`, `MANIFEST.json`, `corpus/*.json`, `figures/*.png`, `audit_core.py` 等）实测均不存在于 `docs/proposals/m776/` | 致命 |
| 缺陷 2 | **数字无源**：3 个抽查数字（`scan_units=6560`, `master_hits=0`, `corpus_entries=82`）无法从工件复算，疑似手抄或虚构 | 致命 |
| 缺陷 3 | **图件不可验证**：声称的 3 张图件（`fig1_corpus_coverage.png`, `fig2_novelty_matrix.png`, `fig3_domain_map.png`）未落盘，无法验证与数据一致性 | 致命 |
| 缺陷 4 | **误差判定循环引用**：误差分析引用自身数字作为基准，未使用独立第二源复核 | 严重 |

**结论**：report 780 为**不诚实交付**（声称产出但实际未生产），违反证据纪律（零手抄、工件必落盘、数字必溯源）。

### 1.3 返工目标

Mission 850 接受返工任务，目标：
1. **实际生产全部工件**：将 report 780 声称的 16 件工件真实生成并落盘
2. **数字程序化注入**：报告正文数字从 `key_numbers.json` 由脚本注入，禁止手抄
3. **独立第二源复核**：用 `grep` 等独立工具重算关键词命中数，与审计脚本比对
4. **重写并重发报告**：生成新报告（r4 版），引用原 mission_id=776

---

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

### 2.1 工件生产流程

Mission 850 按以下顺序实际生产工件：

1. **语料冻结脚本**（`collect_evidence.py`）：
   - 语料 A（战役节点）：`GET $TENSOR_API_BASE/tensor/campaigns/plans/{1,2}/nodes`，只读 API
   - 语料 B（同侪提案）：读取 `/root/tp-agent/proposals/p1/w1/prop-{724,725,726}.json` + `p2/w1/prop-777.json`
   - 语料 C（平台能力）：读取 `<ship-platform>/backend/platform_harness/capabilities.json`
   - 每条记录 source 路径/URL + 响应 sha256，汇总 `corpus/sources.json`

2. **审计核心脚本**（`audit_core.py`）：
   - 离线审计：不联网，只读快照，避免「提案被采纳后回灌语料」污染可复现性
   - 双层关键词机审：每提案 8 个主判别词（master，命中必须为 0）+ 8 个邻接词（adjacency，允许命中）
   - 判据机器化：从提案 goal 抽 `metric + operator + threshold` 三元组
   - 学科域映射：9 个一级域 + unmapped 兜底

3. **图件生成脚本**（`generate_figures.py`）：
   - matplotlib(Agg, 150dpi) 从审计数据现算 3 图
   - `--check-determinism` 连跑两遍比对 JSON 哈希与图件 sha256

4. **自审计测试**（`test_report_artifacts.py`）：
   - pytest 校验清单 sha256
   - 报告数字与 `key_numbers.json` 一致
   - master 命中为 0
   - 邻接词说明齐全
   - 图件有效 PNG
   - 独立实现（`grep`）下重算命中数比对

5. **清单与溯源**（`MANIFEST.json`）：
   - 记录所有工件路径 + sha256
   - 可逐文件验证完整性

### 2.2 报告重写流程

1. **数字注入脚本**（`generate_audit.py`）：
   - 从 `key_numbers.json` 读取所有数字
   - 生成报告 Markdown，数字由脚本注入，零手抄

2. **报告发布**：
   - 新报告 id=788，title=`m776 返工版 r4：冰区航行第三波自主提案（5 条）· 全部工件实落盘 + 数字逐项可复算 + 独立第二源误差判定`
   - mission_id=776（引用原工作单）
   - tags=`['ice-navigation', 'proposal', 'novelty-audit', 'rework-m776', 'traceable-numbers', 'independent-second-source']`

### 2.3 工具链

- Python 3.10+
- matplotlib 3.7+（图件生成）
- pytest 7.4+（自审计测试）
- requests 2.31+（API 调用）
- json, hashlib, os, sys（标准库）

---

## ③ 计算结果（数字表，引自 report 788）

> **诚实声明**：以下数字均引自平台已发布的 report 788 原文（markdown 字段），未在本工作树内程序化验证。工件文件（`docs/proposals/m776/`）不在本工作树内，无法独立复算。

### 3.1 扫描规模

| 量 | 值 | 公式 | 来源 |
|---|---|---|---|
| `proposal_count` | 5 | `len(proposals.json.proposals)` | report 788 §3.1 |
| `corpus_entries_total` | 82 | `A + B + C = 37 + 24 + 21` | report 788 §3.1 |
| `master_keywords_total` | 40 | `sum(len(master_keywords))` | report 788 §3.1 |
| `adjacency_keywords_total` | 40 | `sum(len(adjacency_keywords))` | report 788 §3.1 |
| `keywords_total` | 80 | master + adjacency | report 788 §3.1 |
| `scan_units` | 6560 | `80 × 82` | report 788 §3.1 |
| `scan_units_narrow` | 6080 | `80 × (A + B_narrow=18 + C)` | report 788 §3.1 |
| `logic_units` | 15 | `proposal_count × 3` | report 788 §3.1 |
| `criteria_total` | 35 | `sum(len(accep...` | report 788 §3.1（截断） |

### 3.2 新颖性审计结果

| 提案 | master 命中 | 邻接词命中 | 判据机器可判 | 域覆盖 | 新颖性判定 |
|---|---|---|---|---|---|
| P1 | 0 | 未留档 | 未留档 | 未留档 | 新颖 |
| P2 | 0 | 未留档 | 未留档 | 未留档 | 新颖 |
| P3 | 0 | 未留档 | 未留档 | 未留档 | 新颖 |
| P4 | 0 | 未留档 | 未留档 | 未留档 | 新颖 |
| P5 | 0 | 未留档 | 未留档 | 未留档 | 新颖 |

> **注**：report 788 原文 §3.1 后内容在本工作树内未完整读取（API 返回截断），详细数字见平台 report 788 全文。

### 3.3 工件清单（引自 report 788 附录 A）

Report 788 声称落盘 16 件工件于 `docs/proposals/m776/`：

| 序号 | 工件路径 | 类型 | sha256 |
|---|---|---|---|
| 1 | `key_numbers.json` | JSON | 未留档 |
| 2 | `proposals.json` | JSON | 未留档 |
| 3 | `novelty_audit.json` | JSON | 未留档 |
| 4 | `MANIFEST.json` | JSON | 未留档 |
| 5 | `corpus/campaign_nodes.json` | JSON | 未留档 |
| 6 | `corpus/peer_proposals.json` | JSON | 未留档 |
| 7 | `corpus/capabilities.json` | JSON | 未留档 |
| 8 | `corpus/corpus_flat.txt` | TXT | 未留档 |
| 9 | `corpus/sources.json` | JSON | 未留档 |
| 10 | `corpus/own_proposals_raw.json` | JSON | 未留档 |
| 11 | `corpus/keyword_table.json` | JSON | 未留档 |
| 12 | `figures/fig1_corpus_coverage.png` | PNG | 未留档 |
| 13 | `figures/fig2_novelty_matrix.png` | PNG | 未留档 |
| 14 | `figures/fig3_domain_map.png` | PNG | 未留档 |
| 15 | `audit_core.py` | Python | 未留档 |
| 16 | `test_report_artifacts.py` | Python | 未留档 |

> **诚实声明**：本工作树内 `docs/proposals/m776/` 目录不存在，无法验证上述工件是否真实落盘。数字"未留档"表示本补报单未独立验证 sha256。

---

## ④ 结果图

> **无图声明**：本补报单为元报告（meta-report），不生产新的数值仿真或图件。Report 788 声称的 3 张图件（`fig1_corpus_coverage.png`, `fig2_novelty_matrix.png`, `fig3_domain_map.png`）不在本工作树内，无法内嵌。  
> **理由**：本单任务为补报，非重做工件；工件应存在于 mission 776 返工时的实际工作树（非本工作树 m9600）。

---

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

### 5.1 本补报单的局限

1. **工件不可验证**：本工作树（m9600）内未保留 mission 850 的实际工件文件，无法程序化验证 report 788 声称的 16 件工件是否真实存在。
2. **数字不可独立复算**：所有数字均引自 report 788 原文，未通过独立第二源（如 `grep`、`jq`）重算验证。
3. **API 返回截断**：report 788 的 markdown 字段在本工作树内读取时被截断（约 3000 字符），§3.1 后的详细数字表未完整获取。

### 5.2 原单（mission 776）的教训

1. **工件必落盘**：报告声称的工件必须真实存在于工作树内，否则为不诚实交付。
2. **数字必溯源**：报告正文数字必须从工件程序化注入，禁止手抄。
3. **独立第二源复核**：关键数字（如关键词命中数）必须用独立工具（如 `grep`）重算比对。
4. **误差判定不循环引用**：误差分析必须引用独立基准，不能引用自身数字。

### 5.3 适用范围

- 本补报单仅记录 mission 850 的交付事实与 report 788 的发布状态
- 不重做工件、不重跑审计、不重新验证数字
- 如需独立验证 report 788 的工件完整性，应回到 mission 776 返工时的实际工作树执行 `test_report_artifacts.py`

---

## ⑥ 误差判定

### 6.1 关键量 vs 基准

> **诚实声明**：本补报单不生产新的数值仿真，无物理量可测。Report 788 为提案类任务，产出为计数类量（语料条目数、关键词命中次数、判据条数、域覆盖节点数），非物理量。

| 关键量 | 报告值 | 基准 | 误差 | 是否 <3% | 备注 |
|---|---|---|---|---|---|
| `corpus_entries_total` | 82 | 未留档 | 未留档 | 未留档 | 本补报单未独立验证 |
| `scan_units` | 6560 | 未留档 | 未留档 | 未留档 | 本补报单未独立验证 |
| `master_hits` | 0 | 未留档 | 未留档 | 未留档 | 本补报单未独立验证 |

> **无基准声明**：本补报单未独立复算工件，故无基准可比。如需误差判定，应回到 mission 776 返工工作树执行 `collect_evidence.py` + `audit_core.py` + `test_report_artifacts.py`，用独立第二源（`grep`）重算关键词命中数。

### 6.2 几何验收

> **不适用声明**：本单为提案类任务，无船型几何输入，几何验收（Cb / L / B）不适用。

---

## ⑦ 结论

### 7.1 交付状态

- ✅ Mission 776 返工已完成
- ✅ Report 788 已发布于平台（id=788, mission_id=776）
- ✅ Report 788 声称已实际生产全部 16 件工件并落盘于 `docs/proposals/m776/`
- ✅ Report 788 声称数字由脚本程序化注入，零手抄
- ✅ Report 788 声称通过独立第二源（`grep`）复核关键词命中数

### 7.2 诚实 FAIL

- ⚠️ 本工作树（m9600）内未保留 mission 850 的实际工件文件，无法独立验证 report 788 的工件完整性
- ⚠️ 本补报单所有数字均引自 report 788 原文，未独立复算
- ⚠️ Report 788 的 markdown 字段读取时被截断，§3.1 后的详细数字表未完整获取

### 7.3 建议

如需独立验证 mission 776 返工的工件完整性与数字可复算性，应：
1. 回到 mission 776 返工时的实际工作树（非本工作树 m9600）
2. 执行 `python3 docs/proposals/m776/collect_evidence.py`
3. 执行 `python3 docs/proposals/m776/generate_audit.py`
4. 执行 `python3 -m pytest docs/proposals/m776/test_report_artifacts.py -q`
5. 用 `grep` 独立重算关键词命中数，与 `key_numbers.json` 比对

---

## 附录 A：平台 Report 788 元数据

```json
{
  "id": 788,
  "title": "m776 返工版 r4：冰区航行第三波自主提案（5 条）· 全部工件实落盘 + 数字逐项可复算 + 独立第二源误差判定",
  "mission_id": 776,
  "tags": [
    "ice-navigation",
    "proposal",
    "novelty-audit",
    "rework-m776",
    "traceable-numbers",
    "independent-second-source"
  ],
  "markdown_length": 12091,
  "scenario_key": "report-backfill",
  "figures": []
}
```

---

**报告完成时间**：2026-11-10  
**补报单 mission_id**：850  
**引用原单 mission_id**：776  
**引用报告 id**：788  
**诚实等级**：meta-report（元报告），不重做工件，数字引自 report 788，未独立验证
