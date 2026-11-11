# KPI-SUCCESS-90 · 第 2 轮：任务成功率计量 + 失败自动归因引擎

> 锚点：KPI-SUCCESS-90（总账口径：**任务成功率 ≥ 90 % 且失败自动归因**）。
> 本文件由 `python3 -m tools.failure_attribution.report` 从 `artifacts/kpi_success_90/result.json`
> 程序化注入生成，数字零手抄。快照 sha256 = `9da577898021de5fb48974d0a685a5c6d657f800596e84340ef1fd0074003aaa`，
> 快照行数 = 14126（只读导出，见 `tools/failure_attribution/export.sql`）。

## 1. 问题设定与基准出处

**被计量的两件事**（判据先于动手，机器可裁）：

1. **成功率** = `succeeded / (succeeded + failed)`，终态任务口径；同时报严口径
   `succeeded / (succeeded+failed+stopped+revoked)` 以暴露口径敏感性。
2. **失败自动归因** = 每条 failed 任务是否被机器打上 `(stage, kind)`，且归因必须有
   **可核验的证据通道**（declared 结构字段 / 规则命中片段）；无法归因的必须显式落在
   `unknown`，不许猜。

**基准与出处**：

| 来源 | 用途 | 出处 |
|---|---|---|
| 任务总账表 `tensor_dev_missions`（生产库快照） | 分子/分母/归因底账 | 本仓只读导出 SQL：`tools/failure_attribution/export.sql` |
| 前轮同锚点计量（m15119） | 历史基线对照 | `tensor_reports` id=14243；negk:gate:228 |
| OpenTelemetry Semantic Conventions（exceptions / status） | "错误必须有类型化属性" 的行业做法 | https://opentelemetry.io/docs/specs/semconv/ |
| Temporal failure types（Application/Timeout/Canceled/Terminated/Activity…） | 失败类型分层（谁失败、在哪层失败） | https://docs.temporal.io/ |
| Kubernetes PodFailurePolicy + 容器退出码（137=OOM/SIGKILL） | 基础设施失败的可判读信号 | https://kubernetes.io/docs/concepts/workloads/controllers/job/ |
| Apache Airflow task state / retry 语义 | 任务终态与重试的分离 | https://airflow.apache.org/docs/ |
| Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* (MAST taxonomy, 2025) | 智能体系统失败分类学 | arXiv:2503.13657 |
| He et al., *Drain: An Online Log Parsing Approach* (ICML 2017) | 日志按模板聚类做归因 | PMLR v70 |
| Google SRE Workbook, *Postmortem Culture* | 归因必须可回滚、可审计 | https://sre.google/workbook/postmortem-culture/ |

**选型结论（沉淀进 `oss_solutions`，feature_slug='KPI-SUCCESS-90'）**：业界一致的做法不是
"事后用文本猜"，而是**在终态时刻由执行器写入结构化的失败类型**（OTel status/exception、
Temporal failure、k8s 退出码），事后只做**聚合 + 兜底推断**。所以本单交付两层：
(a) 声明通道（declared）为唯一可信来源；(b) 规则推断通道必须过**实测精度门**才能进台账。
这与前轮 m15119 的"只计量"相比，补上了"自动归因 + 精度自证"。

## 2. 方法与实现

**成功率**（`tools/failure_attribution/measure.py::success_metrics`）：

```
rate = |{m : status = succeeded}| / (|{m : succeeded}| + |{m : failed}|)
窗口 = finished_at（缺则 created_at）>= as_of - 7 d
as_of = 快照内最新终态时间戳（measure.snapshot_as_of），不用墙钟
```

**归因引擎**（`tools/failure_attribution/engine.py::classify`）：规则表外置在
`config/failure_attribution.json`（平台通用词表，零领域语义），按 `(priority, rule_id)`
排序后首个命中即返回。两级谓词：

* **text 规则**：`(fields, regex[])` —— 在 headline / stop_condition / declared_summary /
  gate_reason / traceback_tail / git_status 上做正则命中，返回命中片段作为证据；
* **feature 规则**（第 2 轮新增）：`(dotted_path, op ∈ eq/ne/le/lt/ge/gt/empty/nonempty, value)`
  —— 对结构化字段（如 `result.tool_calls`）做谓词判定，命中片段同样落档。

**可信度分层**（`measure.py::trusted_view`，判据预登记在 config.targets）：

```
trusted(a) = declared(a)  OR  (inferred(a) ∧ support(rule) ≥ 20 ∧ precision(rule) ≥ 0.8)
```

其中 `precision(rule)` 在**有 declared 标签的队列**上用 "隐藏 declared 字段 + 掩蔽标签词" 的
held-out 臂实测（`engine.rule_stats`）。`support=0` 的规则=**未验证**，一律不得进台账
（unknown ≠ pass 的同一条纪律）。

**推断桶的独立佐证**（`measure.py::incident_cohesion`）：对每个 ≥100 单的 inferred 桶，
计算其最忙两天的占比与该两天在全体失败中的基线占比，`lift = share/baseline`；
`share ≥ 0.5 ∧ lift ≥ 2` 判为"时间上成簇、与单次基础设施事故相容"。这是**佐证不是真值**。

**回填**（`engine.emit_backfill_sql`）：只对 trusted 归因生成幂等 `UPDATE ... WHERE id=…
AND (fail_stage IS NULL OR fail_stage='')`，**只产出 SQL 不执行**（生产写回是治理决策，
见 §6 局限）。

## 3. 参数表（全部来自 config + 快照）

| 参数 | 值 | 含义 |
|---|---|---|
| `success_rate_target` | 0.9 | KPI 成功率红线 |
| `coverage_target` | 0.95 | 失败归因覆盖率红线 |
| `accuracy_target` | 0.8 | 推断臂 stage 精度红线 |
| `unknown_rate_max` | 0.05 | 允许的不可归因上限 |
| `min_rule_precision` | 0.8 | 单规则进台账的最低实测精度 |
| `min_rule_support` | 20 | 单规则有效样本下限 |
| `window_days` | 7 | 滚动窗口 |
| 快照队列 | terminal=14126；failed=3917；declared=1009 | 计量域 |
| 触发规则数（label 队列） | 6 条 / stage×kind | 见 §5 |

## 4. 对照表（ref vs got，逐条判据打勾/叉）

**4.1 KPI 判据**

| 判据 | 基准（ref） | 实测（got） | 差 | 判定 |
|---|---|---|---|---|
| 成功率（all-time） ≥ 90% | 90.00% | 70.71% | +19.29 pp | ❌ |
| 成功率（last 7d） ≥ 90% | 90.00% | 65.91% | +24.09 pp | ❌ |
| 归因覆盖率（raw） ≥ 95% | 95.00% | 87.57% | -7.43 pp | ❌ |
| 归因覆盖率（**trusted**） ≥ 95% | 95.00% | 25.76% | -69.24 pp | ❌ |
| 推断臂 held-out 精度 ≥ 80% | 80.00% | 32.90% | -47.10 pp | ❌ |
| 不可归因率 ≤ 5% | 5.00% | 12.43% | +7.43 pp | ❌ |

**结论：KPI 红线（成功率 ≥90%）未达成，gap = +24.09 pp（近 7 天）；
归因能力从"零"推进到"有可信通道"，但可信覆盖率仍差
69.24 pp。** 本单如实记 PARTIAL。

**4.2 与前轮基线对照（ref = m15119 实测，id=14243）**

| 量 | m15119（前轮） | 本单（第 2 轮） | 差 |
|---|---|---|---|
| 7 天成功率 | 0.6584 | 0.6591 | +0.0007 |
| 失败归因覆盖率 | 0.0000（fail_stage 列 0/3499） | raw 0.8757 / trusted 0.2576 | +0.8757 / +0.2576 |
| 推断臂实测精度 | 未测 | 0.3290 | 新增度量 |

成功率两轮基本持平（±0.001 内）——**说明本单没有、也不应声称提高成功率**；增量在归因能力。

**4.3 失败桶分布（n=3917）**

| stage | kind | 单数 | 占比 % |
|---|---|---|---|
| worktree | disk_full | 2316 | 59.13 |
| llm_infra | llm_error | 530 | 13.53 |
| unknown | unclassified | 487 | 12.43 |
| agent_loop | no_progress | 360 | 9.19 |
| budget | budget_exhausted | 131 | 3.34 |
| budget | wall_exhausted | 46 | 1.17 |
| worktree | vcs_error | 24 | 0.61 |
| verification | criteria_unmet | 15 | 0.38 |
| delivery | gate_rejected | 4 | 0.10 |
| verification | test_failure | 3 | 0.08 |
| delivery | report_missing | 1 | 0.03 |

**4.4 推断桶的时间成簇佐证**

| kind | n | 最忙两天 | top2 share | 基线 share | lift | 判据(share≥0.5∧lift≥2) |
|---|---|---|---|---|---|---|
| disk_full | 2316 | 2026-11-10, 2026-11-11 | 1.000 | 0.429 | 2.331 | yes |

## 5. 收敛性 / 参数敏感性（≥2 分辨率 + ≥2 参数点）

**5.1 时间窗分辨率**（同一快照、同一 `as_of`）

| 窗口 | 成功率 |
|---|---|
| 1 d | 0.363837 |
| 7 d | 0.659115 |
| 30 d | 0.707074 |
| all-time | 0.707074 |

窗口越短越低 → 近期失败率在恶化（与 §4.3 的 disk_full 事故簇一致），**不是**口径噪声。

**5.2 规则集 A/B（推断臂 stage 精度）**

| 规则集 | held-out 精度 |
|---|---|
| text-only（8 条文本规则） | 0.2101 |
| +feature 规则（`result.tool_calls == 0`） | 0.3290 |

**5.3 逐规则精度**（label 队列 n=1009，门限 0.8）

| rule_id | 预测 stage | support | precision |
|---|---|---|---|
| R-AGENT-01 | agent_loop | 722 | 0.272853 |
| R-BUDGET-01 | budget | 27 | 0.333333 |
| R-DEL-03 | delivery | 2 | 0.0 |
| R-LLM-F01 | llm_infra | 135 | 0.955556 |
| R-VER-02 | verification | 59 | 0.0 |
| UNKNOWN | unknown | 71 | 0.0 |

只有 `R-LLM-F01`（tool_calls==0 → llm_infra，n=132）过门；文本规则全部不过门——
**这是本单最重要的负面结论**：现有散文（"未调用 finish"）对所有失败类型都用同一模板，
文本推断精度 0.2–0.33，**不可用于自动归因**。

## 6. 失败模式、工件与复现

**工件**

- 度量入口：`tools/failure_attribution/`（engine / measure / report / export.sql）
- 快照导出（只读）：`tools/failure_attribution/export.sql` → `/tmp/kpi_success_90_missions.csv`
- 结果：`artifacts/kpi_success_90/result.json`（sha256=`9da577898021de5fb48974d0a685a5c6d657f800596e84340ef1fd0074003aaa`）、
  `failure_buckets.csv`、`rule_precision.csv`、`attribution_sample.csv`、
  `backfill_fail_stage.sql`（1009 条 trusted UPDATE，未执行）
- 图：- `artifacts/kpi_success_90/figures/fig1_success_rate.png`
- `artifacts/kpi_success_90/figures/fig2_failure_buckets.png`
- `artifacts/kpi_success_90/figures/fig3_attribution_channels.png`
- `artifacts/kpi_success_90/figures/fig4_rule_precision.png`
- 测试：`tests/test_failure_attribution.py`（16 cases）

**复现**

```bash
psql -U foundation -d tensorplatform -At -f tools/failure_attribution/export.sql \
  > /tmp/kpi_success_90_missions.csv
python3 -m tools.failure_attribution measure \
  --missions-csv /tmp/kpi_success_90_missions.csv --outdir artifacts/kpi_success_90
python3 -m tools.failure_attribution.report \
  --result artifacts/kpi_success_90/result.json --doc docs/KPI_SUCCESS_90_ROUND2.md
python3 -m pytest tests/test_failure_attribution.py -q
```

**失败模式与局限（如实）**

1. **标签偏置**：held-out 精度只能在有 declared 的 1009 单上测；
   另外 2908 单无任何结构化标签，其归因只能靠
   `unknown` 或"未验证推断"，不得计为通过。
2. **不可替代仪器化**：文本推断臂精度 0.33 <
   0.80 门限，结论是**必须在终态时刻写 `fail_stage`**（OTel/Temporal/k8s
   的同一做法），而不是事后猜。本单给出的机制 = 度量 + 精度门 + 幂等回填 SQL。
3. **生产写回未执行**：`backfill_fail_stage.sql` 只产出不执行（R2 治理决策，需 owner 批）。
4. **佐证 ≠ 真值**：disk_full 桶 lift=2.331 只说明"时间成簇、与单次事故相容"，
   不能证明每一单的原因。
5. **集群复核**：`run_cluster` 递交 cj1003（loong64，~OSS 横向扫描）本报告时点仍排队，
   标 **not_run**，不冒充通过；本地 4 路径（Python 度量 / CSV 派生 / SQL 快照 sha256 / pytest）已交叉。
6. **领域边界**：规则表只含工程失败词（disk/network/permission/gate…），零船舶域语义；
   域内容由 config 注入。
