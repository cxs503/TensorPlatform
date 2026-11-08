# P84-W1-G84 继承门审计 —— 前置节点未验收，honest `not_ready`

> 战役：P84 Wave1（plan_id=185，p83_wave1_arbitration）
> 门节点：G84（campaign_node 1124，mission 6836）
> 裁决：**not_ready**（综合评分 0.0，节点验收 0/7）
> 输入摘要 sha256：`da671c1e30f25034e9dbc50afcd74ee511f1512f38ea754d27ab5814a48d128e`
> 本报告为 honest FAIL：判据未过如实记录，不粉饰、不覆盖。

## ① 案例介绍

本节点是 P84 Wave1 战役 DAG 的**继承门（gate）**，不是新的物理前沿算例：它不产出新的
物理场量，而是对前置节点 N02–N07 的验收判据、honest FAIL 记录、产物完整性与能力台账
做**机器可裁的继承裁决**。战役前置节点的物理主题横跨 M433=M1 几何与基础验证, M434=M2 船舶性能验证面, M435=M3 平台治理与成熟化，
但门的验收对象是「前置是否真的验收通过」这一治理量，故本报告不给出几何/场量参数表
（门节点无独立几何 Cb/L/B——该纪律适用于物理求解节点，见 ⑤ 局限）。

- 前置节点集合（config 显式注入）：[1118, 1119, 1120, 1121, 1122, 1123] = N02–N07；
- 门依赖（DB `dependencies`）：N02,N04,N05,N06,N07（N03 为族级集成上游，一并纳入审计）；
- 覆盖里程碑：M1(433)/M2(434)/M3(435)；门自身 M4(436) 由 `exclude_node_ids` 自排除；
- 输入快照：`artifacts/m6836/plan185_snapshot.json`（只读 DB 导出，零写库）。

## ② 怎么做的（方法 / 流程 / 工具链）

1. **DB 只读快照**：新增零领域模块 `ops/taskplane/campaign_gate_snapshot.py`，用
   `psql ... row_to_json` 从 `campaign_plans / campaign_milestones / campaign_nodes /
   tensor_dev_missions / tensor_reports` 五表只读装配成 `inheritance_gate` 可直接消费的
   snapshot（mission 以 `str(id)` 为键、report 按 mission_id 聚合）。
2. **继承裁决（复用既有纯函数）**：`ops/taskplane/inheritance_gate.py::evaluate_inheritance_gate`
   对节点是否真验收、跨节点同一量互裁、治理成熟度、就绪度评分做纯函数裁决；同一
   snapshot+config 逐字节可复算（`input_digest` 内容派生）。
3. **任务书判据审计叠层**：`audit_gate_criteria` 在 gate 报告之上机裁四条任务书判据：
   (a) 前置全 done；(b) 无未记录 honest FAIL；(c) 产物完整率 100%；(d) 能力台账已更新。
   判据覆写全部由 config 注入，模块内不含任何领域词条（平台铁律）。
4. **产物**：`inheritance_gate.json/.md`、`predecessor_audit.json`、3 张 PNG；
   pytest `tests/test_campaign_gate_snapshot.py`（14 例，合成数据，零领域词条）。

## ③ 计算结果（数字表，程序化注入）

### 3.1 任务书四条判据

| 判据 | 结论 | 关键数字 |
|---|---|---|
| (a) 前置节点全 done | **FAIL** | done 0/6 |
| (b) 无未记录 honest FAIL | **FAIL** | 未记录失败 mission 3 个（要求 0） |
| (c) 产物完整性 100% | **FAIL** | 完整率 0.0%（0/6） |
| (d) 能力台账已更新 | PASS（文件级） | 21 条，schema 2.0 |

**门就绪度：False；阻塞判据：a_all_predecessors_done, b_no_unrecorded_honest_fail, c_artifact_completeness。**

### 3.2 前置节点状态（判据 a 逐节点）

| ext_id | node_id | status | mission | 判定 |
|---|---|---|---|---|
| P84-W1-N02 | 1118 | in-flight | 6831 | **未 done** |
| P84-W1-N03 | 1119 | in-flight | 6832 | **未 done** |
| P84-W1-N04 | 1120 | in-flight | 6983 | **未 done** |
| P84-W1-N05 | 1121 | in-flight | 6833 | **未 done** |
| P84-W1-N06 | 1122 | in-flight | 6834 | **未 done** |
| P84-W1-N07 | 1123 | in-flight | 6835 | **未 done** |

### 3.3 未记录 honest FAIL（判据 b）

失败终态 `status=failed` 且 `report_owed=true` 但库内无对应报告 = 未记录：

| mission | task_key | status | report_owed | has_report |
|---|---|---|---|---|
| 6830 | p185-P84-W1-N04-a1 | failed | True | False |
| 6833 | p185-P84-W1-N05-a1 | failed | True | False |
| 6834 | p185-P84-W1-N06-a1 | failed | True | False |

### 3.4 产物完整性（判据 c）

| ext_id | node_id | 有报告 | 工件数 | 判定 |
|---|---|---|---|---|
| P84-W1-N02 | 1118 | False | 0 | **缺产物** |
| P84-W1-N03 | 1119 | False | 0 | **缺产物** |
| P84-W1-N04 | 1120 | False | 0 | **缺产物** |
| P84-W1-N05 | 1121 | False | 0 | **缺产物** |
| P84-W1-N06 | 1122 | False | 0 | **缺产物** |
| P84-W1-N07 | 1123 | False | 0 | **缺产物** |

### 3.5 战役 mission 状态分布

`failed=3, queued=1, running=5`（共 9 条 p185 mission 记录，含重派 a1/a2 尝试）。

### 3.6 底层继承门评分分量

| 分量 | 得分 | 硬门 |
|---|---|---|
| milestone | 0.0 | all_covered_milestones_complete=False |
| criteria | 0.0 | cross_consistency_within_tolerance=False |
| consistency | 0.0 | governance_nodes_accepted=False |
| governance | 0.0 | score_above_min=False |

## ④ 结果图

- 图 1：四条任务书判据 pass/fail 矩阵（gate_ready=False）。
- 图 2：前置 N02–N07 的 done 状态 vs 判据 pass/declared 比例（全 0）。
- 图 3：p185 campaign mission 状态分布 + 未记录失败计数。

（门节点为治理量，无物理场量云图；「与解析解/基线比较曲线」以判据通过率对验收线
100% / 未记录失败对 0 的偏差替代，见图 1/图 2。）

## ⑤ 注意点 / 局限

1. **前置节点在飞而非完成**：N02–N07 当前全部 `in-flight`，N03 上游 N01 亦在飞；
   继承门按 DAG 语义应在前置 done 后才判 ready，本门被提前派发 → 如实判 `not_ready`。
2. **3 个未记录 honest FAIL**：mission 6830(N04-a1) / 6833(N05-a1) / 6834(N06-a1) 失败且
   `report_owed=true`，库内无报告 → 属「未记录」，须补报或以责任报告闭环。
3. **产物完整性 0%**：p185 全部 mission 尚无报告/工件入库，故 (c)=0%。这不是「无产物」的
   断言，而是「此刻可追溯产物为零」的客观读数。
4. **判据 (d) 口径**：本门对能力台账做「文件存在 + 可解析 + 条目数」结构检查（21 条，
   通过）；本 mission 的能力增量经平台 `finish.capability_delta` 机制入账（平台管理），
   故 (d) 的平台级「已更新」由该通道交付，门内仅做文件级核对。
5. **零领域语义**：快照/审计模块不含任何域词条；节点 id、判据绑定的关键量/容差均来自
   DB metadata 与 config，可复算、可移植到任意计划。
6. **适用边界**：本门结论随前置节点进展变化，须在前置 done 后**重跑同一命令**复核；
   门本身不修改任何前置节点/任务状态（只读）。

## ⑥ 误差判定（关键量 vs 基准，显式回答）

本节点为**治理/继承门节点**，关键量为「判据通过率 / 产物完整率 / 未记录失败计数」，
不是物理标量，**无实验/解析解数值基准**，故「相对误差是否 <3%」这一物理判据在此**不适用**
（原因：治理计数型量，基准是任务书规定的 100% / 0，而非连续物理解）。逐项偏差如下：

| 关键量 | 实测 | 基准 | 偏差 | 判定 |
|---|---|---|---|---|
| 前置 done 率 | 0/6=0.0% | 100% | −100.0 个百分点 | FAIL |
| 产物完整率 | 0.0% | 100% | −100.0 个百分点 | FAIL |
| 未记录失败数 | 3 | 0 | +3 | FAIL |
| 能力台账条目 | 21 | ≥1 | +20 | PASS |

**结论：判据 (a)/(b)/(c) 未过，(d) 文件级通过；继承门 honest `not_ready`，不得粉饰。**

---
复算命令：
```
python3 ops/taskplane/campaign_gate_snapshot.py --plan 185 \
  --config artifacts/m6836/gate_config.json \
  --snapshot-out artifacts/m6836/plan185_snapshot.json \
  --json-out artifacts/m6836/inheritance_gate.json \
  --md-out artifacts/m6836/inheritance_gate.md \
  --audit-out artifacts/m6836/predecessor_audit.json
python3 -m pytest tests/test_campaign_gate_snapshot.py -q
```
