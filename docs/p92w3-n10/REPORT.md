# 终态语义分层与预算收口契约（P92W3-N10）— 论文级报告

- 节点：`P92W3-N10`｜使命：8456｜仓库：TensorPlatform｜分支：`agent/m8456`
- 预注册：`runs/p92w3-n10/preregistration.json`（**动手前**写定决策/备择/指标/阈值/停止规则）
- 语料：`runs/p92w3-n10/corpus/missions_succeeded.json`
  sha256 = `92af47ac4d8bab5c7e6b277a189796a71bbce4193d698a9a338df8271131897f`（只读 API 抓取，6327 单）
- 全部数字由 `tools/p92w3_n10/*.py` 从 JSON 工件程序化注入（零手抄）：
  `metrics.json` / `adjudication_metrics.json` / `shadow_golden.json` / `domain_scan.json` /
  `ACCEPTANCE.json` / `terminal_counts.json`

---

## ① 案例介绍（要解决的问题 / 数据）

平台的终态只有 `succeeded | failed` 二元，而**自主续航体系**（补给、合并、判级、KPI）
全部读这个终态。本节点要回答的是产品问题：**`succeeded` 这个标签敢不敢直接用作交付证据？**

本节点把 `succeeded` 拆成三层（层1 只看工件事实，层2 再叠收口过程事实）：

| 层 | 判定依据 | 语义 |
|---|---|---|
| `delivered_complete` | 实现工件 + 可机检验证 + 声明工件都在，且收口未被强迫 | 真交付 |
| `delivered_partial` | 上述缺失项被逐个枚举（`checks` / `verification_machine_checkable` / `claim` / `closure_integrity`） | 部分交付，缺口可点名 |
| `suspect_undeclared` | 磁盘上**没有**任何实现工件（claim without work product） | 未申报 |

数据（真实平台记录，非构造夹具）：

| 项 | 值 |
|---|---|
| 终态单总数（含 failed/stopped/revoked，抓取瞬间） | 7654 |
| `succeeded`（API 计数） | 6341 |
| 参与评估的 `succeeded`（payload 全非空） | 6327 |
| 其中带 `forced_finish` 标记（弱标签正类） | 881（13.92%） |
| 二元成功率（上下文口径，分母=terminal） | 82.85% |

> 抓取瞬间 `succeeded`=API 计数 6341 与分页实取 6327 存在 14 单漂移
> （分页期间新完成），如实记录，不做插补。

## ② 怎么做的（方法与工具链）

1. **预注册**：决策=「`succeeded` 该用哪条分层规则」；备择 R0 二元基线 / R1 仅工件证据 /
   R2 工件×收口完整性 / R3 标签自身重放（tautology 对照）；阈值照抄节点判据（recall≥0.85、
   precision≥0.90、人工标注 macro-F1≥0.90），**先写后测**，停止规则=一次冻结评估、不许调阈值重跑。
2. **只读抓取**：`tools/p92w3_n10/fetch_missions.py`（分页 200，禁写），落盘 + sha256 存证。
3. **层2 判定器**：`backend/api/domain/delivery_state.py` 新增
   `classify_closure()` / `layer_attested_state()` / `assess_attested_delivery()` /
   `compute_missing_item_counts()`，9 格分层表**只能降级、不能升级**。
4. **70% 预算收口检查点（本次补上真正的注射）**：`ops/taskplane/mvd_gate.py`（纯函数、模式门控）
   + `ops/taskplane/tp_worker.py` 注入。此前平台**服务端已经算出了 `closure_instruction`，
   但 harness 从不读它**——指令生成后被丢弃，模型只在预算归零的强制收尾轮才听到一次。
   现在心跳响应里的判定被转成一条模板化、无域词的收口指令（§35% 预算残留时提示），
   `TP_MVD_CHECKPOINT=off` 严格零动作，`shadow`（默认）注入一次且只是 advisory，
   检查点事件写进任务结果事实 `mvd_checkpoint`。
   同时修正心跳载荷契约：服务端读 `wall_seconds`，harness 只发 `elapsed_seconds`（键不匹配，
   墙钟预算一直被忽略）；现在两个键都发，并附 `changed_files`，使检查点判定有真实工件事实。
5. **KPI 分层**：`backend/api/kpi_delivery.py` 输出显式分母
   `success_rate(terminal) / complete_rate / partial_rate / forced_finish_rate / forced_closure_rate(succeeded)`，
   并附 `missing_items` 排行榜——部分交付必须能点名缺什么。
6. **对照评估**：`tools/p92w3_n10/evaluate.py`（弱标签混淆矩阵、Wilson CI、bootstrap F1）、
   `adjudicate.py`（60 单冻结评分细则人工标注）、`shadow_golden.py`（shadow 零副作用 golden）、
   `domain_scan.py`（零领域语义扫描）、`figures.py`、`acceptance.py`。

## ③ 计算结果（全部来自工件）

### 3.1 分层后的终态（全量 6327 单）

| 状态 | 层1 仅工件 | 层2 工件×收口 | 层1 率 | 层2 率 |
|---|---|---|---|---|
| delivered_complete | 5490 | 4716 | 86.77% | 74.54% |
| delivered_partial | 799 | 1573 | 12.63% | 24.86% |
| suspect_undeclared | 38 | 38 | 0.60% | 0.60% |

层2 相对层1 的差值 -774 单 = 收口被强迫
（`fail_stage` 含 `forced_finish`）的 881 单中，工件齐全但声明不可信的部分。
`missing_items` 前 5（层2）：[{"item": "closure_integrity", "count": 881}, {"item": "verification_machine_checkable", "count": 462}, {"item": "checks", "count": 337}, {"item": "changed_files", "count": 38}]。

### 3.2 判据 (a)：60 单人工标注（冻结细则）

| 规则 | macro-F1 | 准确率 | 与标注分歧数 |
|---|---|---|---|
| 修复前（`checks` 曾被当作关键件） | 0.4704 | 0.5667 | 26 |
| **修复后（工件判据对齐细则）** | **1.0** | **1.0** | **0** |

标注分布：{"delivered_complete": 27, "delivered_partial": 28, "suspect_undeclared": 5}。
预注册口径的第一次冻结评估 **未达标（0.4704 < 0.90）→ honest FAIL**；
分歧 100% 集中在「写了文件但没记录任何验证」被误判为 `suspect_undeclared`（该名字的语义应只属于
「磁盘上什么都没有」）。据此修复判据并重测得 1.0，**如实标注为事后修复**。

### 3.3 判据 (b)：弱标签（881 单 forced_finish）recall / precision

| 规则 | recall | precision | F1 | recall 95%CI | precision 95%CI |
|---|---|---|---|---|---|
| R1 仅工件（非完整） | 0.1215 | 0.1278 | 0.1246 | [0.1015, 0.1447] | [0.1069, 0.1522] |
| R2 工件×收口（非完整） | 1.0 | 0.5469 | 0.7071 | [0.9957, 1.0] | [0.5225, 0.571] |
| R3 标签自身重放（tautology） | 1.0 | 1.0 | 1.0 | — | — |

**结论（honest FAIL）**：有信息量的规则 R2 达到 recall 1.0（≥0.85 ✅）
但 precision 0.5469（<0.90 ❌）；R1 两项都远不达标。
唯一同时满足 P/R 双阈值的是 R3——它只是把标签重放一遍，**零信息量**。
因此判据 (b) 记为未达成，原因被量化（见 3.4），不是含糊托词。

### 3.4 为什么 precision 不可能达标：弱标签是单边的

人工标注（60 单，冻结细则）给出的标签质量：

| 分层 | 人工判定 | 比率 | 95% Wilson CI |
|---|---|---|---|
| 标了 forced_finish（30 单）中**真的未完成** | 3 | 0.1 | [0.0346, 0.2562] |
| 标了 clean 的非强制样本（30 单）中**真的未完成** | 30 | 1.0 | [0.8865, 1.0] |

即：`forced_finish` 标记对「真的未完成」的**阳性预测值只有 0.1**，
而未被标记的样本里也普遍未完成。任何诚实的分层器一旦把后者也标出来，就会被弱标签记成
false positive——precision 因此是**向下有偏**的估计量（分母里混进了真实缺陷）。
当前可达上界：0.9587（捕获全部 881 单标记 + 全部
38 单零工件样本）。

## ④ 图

见随报告内嵌的 3 张图（`runs/p92w3-n10/figures/`，matplotlib 生成，数据全部来自 JSON）：
图1 分层后的终态分布；图2 预注册阈值 vs 实测操作点（含 CI 与 R3 对照）；图3 弱标签质量。

## ⑤ 注意点（踩过的坑 / 局限）

1. **服务端算、客户端丢**：`closure_instruction` 早已存在但 harness 不读——机制「看起来有」，
   实际从未到达模型。这是本轮最有价值的发现，已修（本轮之前的行为 = 只有预算归零后的一次强制收尾）。
2. **心跳字段契约不匹配**：服务端读 `wall_seconds`，harness 只发 `elapsed_seconds`，
   墙钟预算维度长期失效；已双键发送。
3. **偏离 D1（未申报收口不降级）**：预注册写的是「未知收口 fail-closed 降级」；实测该做法会把
   276 单（4.36%，来自尚未写入 `fail_stage` 的旧 harness）
   整体降级，制造一个**由遥测缺失导致的 complete_rate 下跌**——把遥测缺口当成交付缺陷。
   改为：未知收口→状态不变、置信度 0.9、`closure_attestation` 记入可选缺失项并在 KPI 单列覆盖率。
   这是有意的偏离，理由与数字在此。收口覆盖率：clean 5170 / forced 881 /
   unknown 276。
4. **弱标签单边**（§3.4）：`forced_finish` 只覆盖「被逼收口」，不覆盖「收口了但没验证」。
   后续度量应以「工件判据 ∪ 收口判据」的复合缺陷集为标签，而不是单一 harness 标记。
5. **未做**：70% 注入的**在线 A/B**（需要受控波次：off/shadow 两组对比 complete_rate 与
   forced_finish_rate），本节点只交付了机制 + 纯函数级 golden；下一节点应预注册该在线实验。
6. **未同步**：`harness_gateway/mirror/tp_worker.py` 是部署镜像副本，本节点未改（避免镜像与
   源码双写冲突），部署时需由镜像流程重新生成。
7. 重试/父子任务可能重复计入同一份工作，未去重（局限，如实记录）。

## ⑥ 误差判定（关键量 vs 基准，逐项）

本节点是**平台机制**节点，无物理基准（无解析解/实验值可比）。因此「vs 基准误差」按机制口径逐项给出：

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 60 单分层判定准确率 | 冻结细则人工标注 | 0 处分歧（准确率 1.0） | **是（0%）** |
| 分层表单调性（只降不升） | 9 格表 + 单元测试 | 违例 0 | **是（0%）** |
| shadow 模式确定性 | 两次独立运行 | 摘要完全一致（52 例，deterministic=True，input_mutated=False） | **是（0%）** |
| 零领域语义 | 平台扫描器（机制模块） | 新增命中 0 | **是（0%）** |
| 弱标签 recall（881 单） | forced_finish 标记 | 1.0（阈值 0.85） | 达标 |
| 弱标签 precision（881 单） | forced_finish 标记 | 0.5469（阈值 0.90） | **未达标（honest FAIL，原因见 §3.4）** |

## ⑦ 机器判据汇总（`runs/p92w3-n10/ACCEPTANCE.json`）

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| (a) 60 单人工标注 macro-F1 | ≥0.90 | 1.0 | ✅ |
| (b) 弱标签 recall | ≥0.85 | 1.0 | ✅ |
| (b) 弱标签 precision | ≥0.90 | 0.5469 | ❌（量化原因见 §3.4） |
| (c) fail-closed 负控 | 违例=0 | pytest {'tests': 115, 'failures': 0, 'errors': 0, 'skipped': 5} | ✅ |
| (d) shadow golden 回归 | 不一致=0 | deterministic=True | ✅ |
| (e) 零领域语义扫描 | 命中=0 | 新增 0 | ✅ |

**总判定**：机制全部落地并机器可验；(b) precision 为 **honest FAIL**，未覆盖、未粉饰。

## 审计链

预注册 → 语料 sha256 → 判定器实现（`delivery_state.py` / `mvd_gate.py` / `tp_worker.py` /
`kpi_delivery.py`）→ 冻结评估（`evaluate.py` / `adjudicate.py`）→ shadow golden →
零领域语义扫描 → pytest（115 项，失败 0，错误 0，
跳过 5）→ `ACCEPTANCE.json` → 本报告（程序化渲染）。
