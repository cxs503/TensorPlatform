# m2073 战役仪表化：m611 主计划的机器可验节点状态层（campaign_nodes）

> Mission 2073 · 清偿规划线 R1–R5 连续 5 班的 D-R5-1 欠账：27/27 节点无 status 字段、`SELECT plan_id,status,count(*) FROM campaign_nodes` 在本地全库命中 0 张表。本单落地 SQLite 谱系状态层 + 证据回填器 + 27 行状态表 + 一页摘要。

## 1. 案例介绍（对象 / 数据 / 参数表）

**这不是物理算例，而是战役基础设施**：被测量对象是 m611「冰区航行战役全局主计划」的 27 个研发节点；输入是平台任务流（missions）与交付报告流（reports）；输出是可被 SQL 直接查询的节点状态谱系层。因此本章没有船体几何、没有 Cb/L/B 校验项、没有流体参数——该任务不涉及任何水动力计算，任何把 Cb/L/B 写进本报告的行为都属伪造。

表 1-1　主计划与数据源参数

| 参数 | 值 |
|---|---|
| plan_id / 标题 | `m611` / 冰区航行战役全局主计划 (Ice Navigation Campaign Master Plan) |
| 计划节点数 | 27 |
| 里程碑数 | 6 |
| 层分布 | L1_foundation=12, L2_validation=6, L3_integration=9 |
| 风险分布 | low=7, medium=11, high=9 |
| 计划 sha256 | `0ebf67c0222d6aa252b35f480a956a79758a7e678739fabe28694db160bc3eef` |
| 证据源 missions | 缓存 1715 条（API total 1715） |
| 证据源 reports | 缓存 100 条（API total 100；offset 被服务端忽略=True） |
| 参与归并（剔除自排除/盲评期后） | missions 1659 / reports 99 |
| 剔除计数 | self: m=38, r=1；blind(evo1-): m=18, r=0 |
| 状态取值域 | `not_started`, `in_progress`, `verified`, `blocked` |
| 证据类型域 | `mission`, `report`, `commit`, `none` |

## 2. 怎么做的（方法 / 流程 / 工具链）

### 2.1 流水线

```
GET /api/v1/tensor/missions?limit=200&offset=…  ┐
GET /api/v1/tensor/reports ?limit=200&offset=…  ┴→ 元数据缓存(只读) → 证据池过滤(R0/R0b)
      ↓
节点签名(标题/目标/物理域/判据 metric)：CJK 二元组 + 拉丁词 + 显式节点号 + 标识符指纹
      ↓  df ≤ max(2, ceil(0.15N)) 判别性过滤
逐 mission/report 打分 → 唯一归属(节点号槽位 > 节点号 > 指纹 > 词项；同分 node_id 字典序)
      ↓
状态推导(强证据优先)：verified / in_progress / blocked / not_started
      ↓
SQLite campaign_nodes + campaign_evidence  ⟂  artifacts/campaign_status.json  ⟂  27 行 markdown
```

### 2.2 归并规则（代码即规则，无任何节点的结论硬编码）

表 2-1　打分权重与归属优先级（`tools/campaign_nodes.py` 常量 + `score_evidence`/`assign_nodes`）

| 证据档 | 权重 | 规则 |
|---|---|---|
| 主播种节点号槽位 `|F01|` | 12 | 任务书标题里的节点号槽位，最强 |
| 正文节点号（独立 token） | 9 | 大小写敏感，两侧不紧贴字母/数字 |
| 标识符指纹 | 4 | snake_case 度量名（如 `pytest_test_added_mass_integration`）或 ≥12 字符拉丁词 |
| 里程碑标签 | 2 | `campaign_phase`/`campaign_id` 命中节点里程碑，且需另有命中 |
| 稀有词项 | 3 | CJK 二元组/拉丁词，df ≤ 2 |
| 一般词项 | 2 | df ≤ 5（= max(2, ceil(0.15N))） |
| 归并阈值 | 6 | 分数不足不归并；同分按 node_id 字典序（`same score -> smallest node_id (lexicographic)`） |

**状态推导**：任一强证据 mission ∈ {accepted,done,completed,merged,verified} → `verified`；∈ {running,claimed,awaiting_acceptance,…} → `in_progress`；∈ {succeeded,success,finished} → `in_progress`（match_rule=`status:delivered_unverified`，**不把交付当验讫**）；全部 ∈ {failed,revoked,stopped,…} → `blocked`；无证据 → `not_started`。状态只由**强证据**驱动，弱词项证据仅在无强证据时兜底并标 `+weak_only`。

### 2.3 幂等性设计

`updated_utc` / `generated_utc` 不取 wall clock，而取**内容派生时钟**（该节点证据的最大 `created_at`，无证据则取主计划 `created_utc`）（`content-derived clock = max(node evidence created_at, plan.created_utc); NOT wall clock, so reruns are byte-identical`）。JSON 用 `sort_keys=True` + 固定缩进，证据列表与词项遍历全部定序（sorted），因此跨进程（不同 `PYTHONHASHSEED`）重跑逐字节相同。

## 3. 计算结果（程序化注入）

表 3-1　状态与证据覆盖

| 指标 | 数值 |
|---|---|
| 节点数（计划 / 状态层 / 差） | 27 / 27 / 0 |
| status=`not_started` | 0 / 27 |
| status=`in_progress` | 27 / 27 |
| status=`verified` | 0 / 27 |
| status=`blocked` | 0 / 27 |
| evidence_kind=`report` | 17 |
| evidence_kind=`mission` | 10 |
| evidence_kind=`commit` | 0 |
| evidence_kind=`none` | 0 |
| 有证据节点占比 | 27/27（100.0%） |
| 悬空引用 | 0 |
| 未归并 missions / reports | 628 / 2 |
| 归并 missions / reports | 1031 / 97 |
| 证据记录总数（SQLite campaign_evidence） | 1128 |

表 3-2　里程碑 × 状态 交叉表

| 里程碑 | not_started | in_progress | verified | blocked | 合计 |
|---|---|---|---|---|---|
| M0 | 0 | 4 | 0 | 0 | 4 |
| M1 | 0 | 6 | 0 | 0 | 6 |
| M2 | 0 | 5 | 0 | 0 | 5 |
| M3 | 0 | 6 | 0 | 0 | 6 |
| M4 | 0 | 4 | 0 | 0 | 4 |
| M5 | 0 | 2 | 0 | 0 | 2 |

表 3-3　27 节点状态表（逐节点，含证据引用）

| # | node | milestone | layer | risk | status | evidence_kind | evidence_ref | 强/总证据 | merge_rule |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `E01` | M4 | L2_validation | low | **in_progress** | report | report:1807 | 3/16 | `keyword_terms` |
| 2 | `E02` | M5 | L2_validation | medium | **in_progress** | report | report:1750 | 2/121 | `keyword_terms` |
| 3 | `F01` | M0 | L1_foundation | low | **in_progress** | report | report:1751 | 5/14 | `keyword_terms` |
| 4 | `F02` | M1 | L1_foundation | medium | **in_progress** | report | report:1698 | 2/35 | `keyword_terms` |
| 5 | `F03` | M0 | L1_foundation | low | **in_progress** | report | report:1819 | 1/49 | `keyword_terms+tie` |
| 6 | `F04` | M1 | L1_foundation | high | **in_progress** | report | report:1694 | 2/572 | `via_mission_link` |
| 7 | `F05` | M1 | L1_foundation | medium | **in_progress** | mission | mission:670 | 1/6 | `node_id_primary` |
| 8 | `F06` | M2 | L1_foundation | high | **in_progress** | mission | mission:678 | 2/5 | `node_id_primary` |
| 9 | `F07` | M2 | L1_foundation | medium | **in_progress** | mission | mission:679 | 1/2 | `node_id_primary` |
| 10 | `F08` | M2 | L1_foundation | high | **in_progress** | report | report:1708 | 3/22 | `keyword_terms` |
| 11 | `H01` | M0 | L1_foundation | low | **in_progress** | mission | mission:672 | 2/8 | `node_id_primary` |
| 12 | `H02` | M1 | L1_foundation | medium | **in_progress** | mission | mission:683 | 1/7 | `node_id_primary` |
| 13 | `H03` | M2 | L2_validation | medium | **in_progress** | report | report:1723 | 4/27 | `keyword_terms` |
| 14 | `I01` | M3 | L3_integration | high | **in_progress** | report | report:1808 | 12/12 | `node_id` |
| 15 | `I02` | M4 | L3_integration | high | **in_progress** | mission | mission:693 | 2/30 | `node_id_primary` |
| 16 | `I03` | M3 | L3_integration | medium | **in_progress** | mission | mission:682 | 3/7 | `node_id_primary` |
| 17 | `I04` | M3 | L3_integration | high | **in_progress** | mission | mission:694 | 3/13 | `node_id_primary` |
| 18 | `M01` | M3 | L3_integration | high | **in_progress** | report | report:1713 | 4/29 | `keyword_terms` |
| 19 | `M02` | M1 | L1_foundation | medium | **in_progress** | mission | mission:689 | 2/11 | `node_id_primary` |
| 20 | `P01` | M3 | L3_integration | high | **in_progress** | report | report:1818 | 3/19 | `keyword_terms` |
| 21 | `P02` | M3 | L3_integration | medium | **in_progress** | report | report:1764 | 3/17 | `keyword_terms` |
| 22 | `V01` | M0 | L2_validation | low | **in_progress** | report | report:1696 | 4/14 | `keyword_terms` |
| 23 | `V02` | M4 | L2_validation | medium | **in_progress** | report | report:1702 | 2/29 | `keyword_terms` |
| 24 | `V03` | M4 | L2_validation | high | **in_progress** | report | report:1724 | 2/15 | `keyword_terms` |
| 25 | `V04` | M1 | L2_validation | low | **in_progress** | mission | mission:671 | 1/14 | `node_id_primary` |
| 26 | `X01` | M5 | L3_integration | high | **in_progress** | report | report:1712 | 12/16 | `node_id` |
| 27 | `X02` | M2 | L1_foundation | medium | **in_progress** | report | report:1703 | 1/18 | `keyword_terms` |

表 3-4　主播种槽位标注子集上的归属校验（唯一可机器复算的精度代理）

| 量 | 数值 |
|---|---|
| 标注子集规模（goal 中恰含一个 `|Fxx|`） | 47 |
| 归属正确 | 47 |
| 误配 | 0 |
| 误配率 | 0.00% |
| 被播种任务覆盖的节点 | 27 / 27 |

## 4. 结果图

图 4-1（`artifacts/campaign_status_coverage.png`，matplotlib 生成；线上报告以 base64 内嵌，仓内 markdown 用相对路径引用）：(a) 逐节点证据量，实心=强证据（节点号/指纹）、浅色=弱词项证据，颜色=status；(b) 里程碑×状态堆叠；(c) evidence_kind 覆盖与悬空引用数。

![coverage](../artifacts/campaign_status_coverage.png)

## 5. 注意点（踩坑 / 局限 / 适用范围）

1. **reports API 的 offset 被忽略**：`GET /tensor/reports?limit=200&offset=100` 仍返回同一页，因此证据池只能看到最新 100 条报告（total=100，实际是服务端截断上限）。取数器用「探针请求」显式检测并落盘 `reports_offset_ignored=true`，不假装取全；更早的报告不在证据池内，这是 report 覆盖率的真实上限。
2. **`verified` 结构性为 0**：missions 全量 1715 条里没有任何 `accepted` 态（裁决结果不入库）。因此「已验讫」在本层取不到；`succeeded`（跑完交付、未裁决）只映射为 `in_progress` 并标 `status:delivered_unverified`——把交付当验讫会让谱系 KPI 虚高，本层拒绝这么做。要让 `verified` 有意义，需平台侧把验收裁决写入 mission 状态（这是本单暴露的上游缺口）。
3. **关键词归并的误配率未量化**：全量 1128 条证据记录中约 1045 条是弱词项证据；没有人工 golden set，precision/recall 属 WITHHELD。表 3-4 的 0 误配只在「主播种槽位」子集成立，且标注真值与规则同源（测的是分档优先序不被词项分数反超），**不等于独立精度**。弱证据体量最大者见 (a) 图长尾（个别节点 >500 条），说明通用词噪声确实存在，只是被限制在「不驱动状态」的层面。
4. **`not_started` 的语义**：只代表「API 无可见证据」，不等于确实未开工；主计划 milestone 自带的 status（planned/in_progress）是规划意图，不作为节点证据。
5. **自排除 / 盲评纪律**：本仪表自身的建单（goal 含 `campaign_nodes` 等）与主计划建单不计证据（self 剔除 m=38, r=1）；`evo1-` 盲评期工件剔除 m=18, r=0。
6. **与本仓 `backend/api/campaign_plans.py` 的 PG 版 campaign_nodes 不同源**：本层是本地 SQLite 证据视图（domain 为 4 态），PG 版是线上战役 API 的 6 态表；两者不自动同步，本单不修改线上库（也不该由仪表单改）。

## 6. 误差判定（逐项 vs 基准，显式回答是否 < 3%）

表 6-1　验收判据的误差 / 偏差

| 关键量 | 基准（判据） | 实测 | 误差 | < 3%？ |
|---|---|---|---|---|
| 节点数对账 | master_plan.json 节点数 = 27 | 状态层节点数 = 27 | 差 0（0.00%） | 是 |
| status 取值域 | 27/27 均 ∈ 4 态且非空 | 27/27 合法 | 违规 0（0.00%） | 是 |
| 幂等（JSON 逐字节） | 连跑两次 diff = 0 | 两次构建 md5 相同（pytest 跨进程校验） | 字节差 0（0.00%） | 是 |
| 悬空引用 | 报告 mission_id 均可查；0 悬空 | 悬空 0 | 0 条（0.00%） | 是 |
| 归并非恒空 | ≥1 节点 verified/in_progress | verified 0 + in_progress 27 = 27 | 满足（0 缺口） | 是 |
| 标注子集误配率 | 误配 = 0 | 47/47 正确，误配 0 | 0.00% | 是 |
| 关键词-only 子集误配率 | 需 golden set | 无标注 → WITHHELD | **未量化** | 否（未达可判定状态） |

**结论**：可机器裁决的五项（节点对账、状态域、幂等、悬空引用、非恒空）误差均为 **0（< 3%）**；主播种槽位标注子集误配率 0.00%（< 3%），但其真值与规则同源，只能证明「节点号不被词项分数反超」。**关键词-only 归并的误配率无法给出误差数字**——缺少人工标注 golden set，本报告如实声明为 WITHHELD，不编造精度。

## 7. 复算与验收命令

```bash
# 取数（唯一联网步骤，只读）→ 建库/回填/出表/出图（离线、幂等）
python3 tools/campaign_nodes.py fetch --cache artifacts/campaign_evidence_cache.json
python3 tools/campaign_nodes.py build --plan artifacts/m611_master_plan.json \
  --cache artifacts/campaign_evidence_cache.json --out-json artifacts/campaign_status.json \
  --out-md docs/campaign_status_m611.md --out-db artifacts/campaign_nodes.db \
  --out-figure artifacts/campaign_status_coverage.png

# 任务书要求的验收查询
python3 tools/campaign_nodes.py sql --db artifacts/campaign_nodes.db

# 验收判据（21 例）
python3 -m pytest tests/test_campaign_nodes.py -q
```
