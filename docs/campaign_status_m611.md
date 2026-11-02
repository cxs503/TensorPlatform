# 战役主计划节点状态摘要 —— m611 冰区航行战役全局主计划 (Ice Navigation Campaign Master Plan)

> 机器可验状态层 `campaign_nodes`（SQLite） · 生成时钟 `2026-11-02T05:48:46.910491+00:00`（内容派生，非 wall clock） · 计划 sha256 `0ebf67c0222d6aa2…` · schema `1.0.0`

## 1. 总览

| 指标 | 数值 |
|---|---|
| 节点数（计划 / 状态层） | 27 / 27（差 0） |
| status=`not_started`（未启动） | 0 / 27 |
| status=`in_progress`（进行中） | 27 / 27 |
| status=`verified`（已验讫） | 0 / 27 |
| status=`blocked`（受阻） | 0 / 27 |
| 有证据节点 | 27 / 27（100.0%） |
| evidence_kind=report / mission / commit / none | 17 / 10 / 0 / 0 |
| 悬空引用 | 0 |
| reports API 分页 | 服务端忽略 offset，只能取到最新 100 条（total=100）：更早报告不在证据池内 |
| 未归并 missions / reports | 628 / 2 |

## 2. 节点状态表（逐节点）

| # | node | milestone | layer | risk | status | evidence_kind | evidence_ref | 证据数 | status_rule | merge_rule | 标题 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `E01` | M4 | L2_validation | low | **in_progress** | report | report:1807 | 16 | `status:delivered_unverified+strong` | `keyword_terms` | 环境场驱动接口 (风/流/温度) |
| 2 | `E02` | M5 | L2_validation | medium | **in_progress** | report | report:1750 | 121 | `status:delivered_unverified+strong` | `keyword_terms` | ITTC 冰池试验对标框架 |
| 3 | `F01` | M0 | L1_foundation | low | **in_progress** | report | report:1751 | 14 | `status:delivered_unverified+strong` | `keyword_terms` | Winkler 弹性地基板连续模型 |
| 4 | `F02` | M1 | L1_foundation | medium | **in_progress** | report | report:1698 | 35 | `status:delivered_unverified+strong` | `keyword_terms` | 板-水附加质量集成到 FEM 质量矩阵 |
| 5 | `F03` | M0 | L1_foundation | low | **in_progress** | report | report:1819 | 49 | `status:delivered_unverified+strong` | `keyword_terms+tie` | FEM→DEM 碎片数据导出接口 |
| 6 | `F04` | M1 | L1_foundation | high | **in_progress** | report | report:1694 | 572 | `status:delivered_unverified+strong` | `via_mission_link` | 3D 船体曲面接触（STL/OBJ 三角网格） |
| 7 | `F05` | M1 | L1_foundation | medium | **in_progress** | mission | mission:670 | 6 | `status:delivered_unverified+strong` | `node_id_primary` | DEM 切向摩擦与滚动阻力模型 |
| 8 | `F06` | M2 | L1_foundation | high | **in_progress** | mission | mission:678 | 5 | `status:delivered_unverified+strong` | `node_id_primary` | 3D DEM↔流体双向耦合框架 |
| 9 | `F07` | M2 | L1_foundation | medium | **in_progress** | mission | mission:679 | 2 | `status:delivered_unverified+strong` | `node_id_primary` | DEM 浮力与湿耦合模型 |
| 10 | `F08` | M2 | L1_foundation | high | **in_progress** | report | report:1708 | 22 | `status:delivered_unverified+strong` | `keyword_terms` | coupled_hull 大转动几何非线性 |
| 11 | `H01` | M0 | L1_foundation | low | **in_progress** | mission | mission:672 | 8 | `status:delivered_unverified+strong` | `node_id_primary` | LBM 自由面捕获与液滴验证 |
| 12 | `H02` | M1 | L1_foundation | medium | **in_progress** | mission | mission:683 | 7 | `status:delivered_unverified+strong` | `node_id_primary` | 冰-水拖曳力系数标定 |
| 13 | `H03` | M2 | L2_validation | medium | **in_progress** | report | report:1723 | 27 | `status:delivered_unverified+strong` | `keyword_terms` | 碎冰场拖曳力 DEM 标定 |
| 14 | `I01` | M3 | L3_integration | high | **in_progress** | report | report:1808 | 12 | `status:active_mission+strong` | `node_id` | SIR 七腿船冰耦合全链集成 |
| 15 | `I02` | M4 | L3_integration | high | **in_progress** | mission | mission:693 | 30 | `status:delivered_unverified+strong` | `node_id_primary` | G6 规模化碎冰场（100k+ 颗粒） |
| 16 | `I03` | M3 | L3_integration | medium | **in_progress** | mission | mission:682 | 7 | `status:delivered_unverified+strong` | `node_id_primary` | FEM cohesive→DEM 断裂碎片交接闭环 |
| 17 | `I04` | M3 | L3_integration | high | **in_progress** | mission | mission:694 | 13 | `status:delivered_unverified+strong` | `node_id_primary` | 破冰力时间序列与实验对标 |
| 18 | `M01` | M3 | L3_integration | high | **in_progress** | report | report:1713 | 29 | `status:delivered_unverified+strong` | `keyword_terms` | 冰区操纵性数学模型 (MMG-type in ice) |
| 19 | `M02` | M1 | L1_foundation | medium | **in_progress** | mission | mission:689 | 11 | `status:delivered_unverified+strong` | `node_id_primary` | 冰脊力学模型 (ridge sailing/keel) |
| 20 | `P01` | M3 | L3_integration | high | **in_progress** | report | report:1818 | 19 | `status:delivered_unverified+strong` | `keyword_terms` | 推进器-冰相互作用模型 |
| 21 | `P02` | M3 | L3_integration | medium | **in_progress** | report | report:1764 | 17 | `status:delivered_unverified+strong` | `keyword_terms` | 推进器负载波动与扭矩脉动分析 |
| 22 | `V01` | M0 | L2_validation | low | **in_progress** | report | report:1696 | 14 | `status:delivered_unverified+strong` | `keyword_terms` | Hetényi 级数解验证锚 |
| 23 | `V02` | M4 | L2_validation | medium | **in_progress** | report | report:1702 | 29 | `status:delivered_unverified+strong` | `keyword_terms` | DEM 大规模颗粒压测基准（10k-100k） |
| 24 | `V03` | M4 | L2_validation | high | **in_progress** | report | report:1724 | 15 | `status:delivered_unverified+strong` | `keyword_terms` | GPU cell list 原生空间搜索 |
| 25 | `V04` | M1 | L2_validation | low | **in_progress** | mission | mission:671 | 14 | `status:delivered_unverified+strong` | `node_id_primary` | Mixed-Mode cohesive 非比例路径验证 |
| 26 | `X01` | M5 | L3_integration | high | **in_progress** | report | report:1712 | 16 | `status:delivered_unverified+strong` | `node_id` | 全尺度实船数据关联 |
| 27 | `X02` | M2 | L1_foundation | medium | **in_progress** | report | report:1703 | 18 | `status:delivered_unverified+strong` | `keyword_terms` | 闭环时钟同步与子循环框架 |

## 3. 里程碑 × 状态 交叉表

| 里程碑 | not_started | in_progress | verified | blocked | 合计 |
|---|---|---|---|---|---|
| M0 | 0 | 4 | 0 | 0 | 4 |
| M1 | 0 | 6 | 0 | 0 | 6 |
| M2 | 0 | 5 | 0 | 0 | 5 |
| M3 | 0 | 6 | 0 | 0 | 6 |
| M4 | 0 | 4 | 0 | 0 | 4 |
| M5 | 0 | 2 | 0 | 0 | 2 |

## 4. 归并规则与局限

- 阈值 6：主播种节点号槽位 `|F01|` +12、正文节点号 +9、长标识符指纹 +4、里程碑标签 +2、稀有判别词项 (df≤2) +3、一般判别词项 +2（df ≤ 5）；归属优先级 节点号 > 指纹 > 词项，同档按 node_id 字典序。
- 自排除 `['campaign_nodes', 'campaign_status', 'plan_id,status,count(*)']` 与盲评标签 `evo1-` 不参与归并。
- 证据强度分布：有强证据（节点号/指纹）的节点 27/27；逐条证据 17 条报告类、1128 条证据记录（明细在 SQLite `campaign_evidence`，JSON 只留每条节点前 8 条）。
- **局限（须声明）**：**关键词-only 归并的误配率仍未量化**（无人工 golden set，precision/recall 未知，状态=WITHHELD）。可机器复算的只有「主播种槽位 `|Fxx|` 子集」上的优先档位校验（见 `tests/test_campaign_nodes.py::test_real_artifact_labelled_subset_has_zero_misassignment`）；该标注真值与规则同源，故它证明的是「节点号不被词项分数反超」，**不等于**独立精度。`keyword_terms`、`keyword_terms+tie`、`via_mission_link` 三档仍最弱，需人工抽查。
- **局限**：`verified` 计 0/27 是平台现状的函数——missions API 全量里没有任何 `accepted` 态（裁决不入库），故「已验讫」在本层结构性地取不到；`succeeded` 只作「已交付未裁决」→ in_progress。
- **局限**：`not_started` 只代表「API 无可见证据」，不等于确实未开工；主计划 milestone 的 status 字段（in_progress/planned）是规划意图，不作为节点证据。

## 5. 复算方式

```bash
python3 tools/campaign_nodes.py fetch --cache artifacts/campaign_evidence_cache.json
python3 tools/campaign_nodes.py build \
  --plan artifacts/m611_master_plan.json --cache artifacts/campaign_evidence_cache.json \
  --out-json artifacts/campaign_status.json --out-md docs/campaign_status_m611.md \
  --out-db artifacts/campaign_nodes.db
python3 tools/campaign_nodes.py sql --db artifacts/campaign_nodes.db
```
