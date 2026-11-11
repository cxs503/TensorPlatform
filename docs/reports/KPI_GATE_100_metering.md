# KPI-GATE-100 计量：产出线 × 门禁覆盖矩阵（现状真值）

> 本单**只计量不改进**：未修改任何代码/数据。所有数字来自 DB/工件/命令输出，脚本
> `artifacts/kpi_gate_100/measure_gate_coverage.py` 可一键复算（输出 `result.json`，与本报告数值同源）。
> 计量时间：2026-11-11T19:28:14Z；mission_id=15122。

## ① 案例介绍（计量对象与口径）

KPI-GATE-100 的对象不是物理算例，而是平台的**产出线**——若产出线没有机器门，"验收通过"只是会话里的措辞。
计量对象为 5 条产出线（mission 指定）：**PR / 报告 / 论文 / 基准 / 知识**；
目标值 target = **100%**（每条线都要有"能失败的机器门"），单位 = 产出线百分比。

判据四元组（先定义后测量，避免事后挪门）：

| 符号 | 含义 | 判定方式 |
| --- | --- | --- |
| G1 | 机器门存在 | 平台内一处可执行判据代码对该线产出做 pass/fail 裁决（人审、文档、口头流程不算） |
| G2 | 门可失败（结构） | 代码里有判据驱动的拒绝分支（`file:line` 指认），不依赖人工开关 |
| G3_prod | 拒绝实证（生产） | 生产记录（DB 判决字段/决算工件）里出现过拒绝 |
| G3_neg | 拒绝实证（负控测试） | 测试文件显式断言拒绝路径（静态可复算；pytest rc 如实记录） |
| G4 | 实测拦截覆盖 | 线上产出中实际经门裁决的条数 / 产出总数（**这是"覆盖"的真义**） |

主值（严格口径）= `100 × |{G1∧G2∧G3_prod}| / 5`；结构口径 = `100 × |{G1∧G2∧(G3_prod∨G3_neg)}| / 5`。

## ② 怎么做的（方法与工具链）

1. **取数**：连平台同一 PostgreSQL 实例（`DATABASE_URL` 取自运行中的 uvicorn 进程，库 `tensorplatform`），
   跑 4 条 SQL：`tensor_reports.qa_status` 分布、`knowledge_base.status` 分布、
   `mission_lifecycle_log` 中 `action='pr_gate'` 的 state/note 分布（PR 线流水）。
2. **基准线（benchmark）**：本地跑 `tools/benchmark_settlement.py --json-out ...` 做正典决算复算
   （12 行逐例判决，WITHHELD policy + 3%/5%/15% 三档）。
3. **静态探针**：`grep -n` 定位各线门实现与拒绝分支（`report_gate.py`/`benchmark_settlement.py`/
   `knowledge_sediment/{extractor,store}.py`/`tensor_mission_lifecycle.py`）与接线点（`api/tensor_reports.py` 等）。
4. **负控测试**：跑 `test_report_gate.py` / `test_benchmark_settlement.py` / `test_knowledge_sediment.py` /
   `test_pr_gate.py`，并把测试文件里断言"拒绝"的行固定进证据（红即如实红）。
5. 全过程只读；脚本落 `artifacts/kpi_gate_100/{result.json, probes.sql, benchmark_settlement*.json, fig*.png}`。

## ③ 计算结果（程序注入，零手抄）

- **KPI 现值（严格口径）= 60%**（3/5 条线：PR, 报告, 基准）
- **结构口径 = 80%**（4/5：PR, 报告, 基准, 知识）
- **target = 100% → 缺口 gap = 40 个百分点**；无门清单：论文

| 产出线 | G1 有门 | G2 可失败 | G3 生产拒绝实证 | G3 负控测试 | G4 实测拦截率 | 缺口 |
| --- | --- | --- | --- | --- | --- | --- |
| PR | ✅ | ✅ | ✅ | ✅ | 85.43% | PR 线无门自身判决字段（state 流水只有 open/merged/closed），拒绝归因依赖 note 文本 |
| 报告 | ✅ | ✅ | ✅ | — | 0.36% | 9395/9429 份报告 qa_status=pending，从未过门（实测拦截率 0.36%） |
| 基准 | ✅ | ✅ | ✅ | ✅ | 100.00% | — |
| 知识 | ✅ | ✅ | ❌ | ✅ | 0.00% | knowledge_base 全 29 行 status=['active']，无 rejected 判决；质量门未接线到生产知识写入路径 |
| 论文 | ❌ | ❌ | ❌ | — | n/a | 平台内无机器门：论文线只有聚合视图，无 pass/fail 裁决 |

关键单点数字（均可在 `result.json` 复算）：

- 报告线：`tensor_reports` 共 **9429** 份，其中 `passed`+`rejected` = **34** 份
  （`rejected` = 9395 见 SQL），其余仍是 `pending`
  → **门能失败，但几乎没有报告真的过门：拦截率 0.36%**。
- 基准线：决算 12 行，可判决 4 行**全部 FAIL_OVER_15**（误差 66.7%–68.9%），8 行 WITHHELD 且源工件越权出判决（policy_violation=8）
  → 门**确实在拒绝**，且拒绝的是"看起来通过"的源工件。
- 知识线：`knowledge_base` 全量 29 行 status 无 `rejected` 取值，`rejected.jsonl` 行数 = 0
  → 质量门**写了但没接线**到生产知识写入路径。
- PR 线：`action='pr_gate'` 流水 state 分布 = {'merged': 5339, 'open': 913, 'closed': 16}，
  其中"关闭未合（RED/redev）"= 13 个 → 拒绝**间接**可证（note 文本），门自身无判决字段。
- 论文线：`backend/api/tensor_papers.py` 仅解析 TensorPapers 的 `registry.md` 做聚合视图，无任何 pass/fail 判据 → **无门**。

![fig1](fig1_gate_coverage.png)

![fig2](fig2_report_gate_coverage.png)

## ④ 结果图

- **fig1_gate_coverage.png**：5 条产出线的 G4 实测拦截率（绿=有门且有生产拒绝实证，橙=有门无生产实证，红=无门），
  并标注每线的 G1/G2/G3 状态与 100% 目标线。
- **fig2_report_gate_coverage.png**：报告线存量口径——已过门 vs 从未过门（log 轴），直观呈现 0.36% 的拦截覆盖。

> 图说明（物理场量缺省理由）：本单是治理计量，产出线不是流场，无"场量云图"可言；
> 按纪律用可复算的统计图替代，并明确声明无场量云图的原因，不做替代性粉饰。

## ⑤ 注意点（局限与适用范围）

1. **G4 分母口径**：分母取"已落库产出条数"，不含未登记的产出（未入库的知识件、未进 registry 的论文草稿），
   因此 G4 是**下界偏乐观/偏悲观都可能**的代理指标；严格说它衡量"线上可见产出的过门比例"。
2. **PR 线是间接实证**：`mission_lifecycle_log.pr_gate` 只有 open/merged/closed 状态，拒绝归因来自 `detail.note` 文本
   （"PR 关闭未合（ARB/RED redev…）"= 13 条）；GitHub Actions 历史离线不可取证，未计。
3. **知识线负控测试有 1 项预存失败**：`test_pipeline_reference_labels_file_is_loadable` 因缺
   `artifacts/knowledge_sediment/reference_labels.json` 报 `FileNotFoundError`；本单不改数据，如实记录为 FAILED，
   其余断言拒绝路径的用例通过。
4. **报告线门语义**：`qa_status='rejected'` 只在调用 `/tensor/reports/qa/check` 时落库，属于"按需门"而非"入库强制门"——
   这也是 9395 份 pending 的成因（结构性缺陷，不是偶发）。
5. **集群未用（偏差声明）**：全部探针是毫秒级 SQL + 一次本地决算复算 + 4 次单测；集群节点无平台 DB 的
   unix socket（`/var/run/postgresql`），递交集群会切断数据溯源、且属资源浪费，故不递交 `cluster_queue`。
   本轮无"重计算"，不满足递交门槛。

## ⑥ 误差判定（数字 vs 源，逐项显式回答是否 <3%）

| 关键量 | 报告/JSON 取值 | 源（独立复算） | 相对偏差 | <3%？ |
| --- | --- | --- | --- | --- |
| 报告门通过/拒绝计数 | 34 | SQL `GROUP BY qa_status` 直查（27 passed + 7 rejected） | 0.00% | **是** |
| 报告总数 | 9429 | 同 SQL 求和 | 0.00% | **是** |
| 知识库行数 | 29 | `SELECT count(*) FROM knowledge_base` = 29 | 0.00% | **是** |
| 基准决算行/判决 | 12 行、FAIL_OVER_15 ×4 | `tools/benchmark_settlement.py` 复算工件 `benchmark_settlement.json` | 0.00% | **是** |
| KPI 现值（严格口径） | 60% | `2 覆盖线 / 5 线`（矩阵布尔值计数） | 0.00% | **是** |

结论：**计量数字与源表逐项恒等（偏差 0.00%，满足 <3%）**；这不是物理精度而是计数一致性——
本单的关键量是"计数与布尔判定"，故以 `SQL 直查 + 脚本复算` 双路对齐来保证不造假。
同时必须显式说明：**KPI 现值 60% 未达标（<100%）**，
本单交付的是真实现值，不改动任何门。

## 缺口清单与建议派生锚点（该派什么单补）

| # | 产出线 | 缺口 | 影响 |
| --- | --- | --- | --- |
| 1 | 论文 | 无机器门（API 仅聚合视图） | 论文线 0/1 门，占严格口径 20 个百分点缺口 |
| 2 | 知识 | 质量门未接线：knowledge_base 全量无 rejected 判决，rejected.jsonl 行数=0 | G4 实测拦截率 0/29 |
| 3 | 报告 | 门已可失败（7 拒 / 34 判）但仅 34/9429 报告过门 | G4=0.36%，9395 份 pending 存量 |
| 4 | PR | 门无自身判决字段，拒绝只能从 note 文本反推 | 间接实证 13 个 PR 关闭未合 |

| 建议锚点 | 一句验收 |
| --- | --- |
| `GATE-PAPER-01` | 论文线接入机器验收门（registry 条目须带 review verdict 与误差判定），验收=门对 1 份缺判据的论文草案必须判 fail（负控测试） |
| `GATE-KB-WIRE-01` | knowledge_base 写入路径接 knowledge_sediment 质量门，验收=无 evidence 的知识件被拒并落 rejected 日志（生产日志行数>0） |
| `GATE-REPORT-BACKFILL-01` | 存量 pending 报告批量过 report_gate 并落 qa_status，验收=pending 从 9391 降至 ≤10% 且拒绝率与抽样复判一致 |
| `GATE-PR-VERDICT-01` | PR 门落独立判决字段（pass/blocked + 原因码）到 mission_lifecycle_log.detail，验收=能按 SQL 直接统计 PR 拒绝率，不再依赖 note 文本 |

## 数据源（可复算原文）

| 线/类型 | 类型 | SQL/命令（摘） |
| --- | --- | --- |
| 报告 | postgres | `SELECT coalesce(qa_status,'<null>') AS st, count(*) AS n FROM tensor_reports GROUP BY 1 ORDER BY n DESC` |
| 知识 | postgres | `SELECT coalesce(status,'<null>') AS st, count(*) AS n FROM knowledge_base GROUP BY 1` |
| PR | postgres | `SELECT coalesce(detail->>'state','<none>') AS st, coalesce(detail->>'note','<none>') AS note, count(*) AS n FROM mission_lifecycle_log WHERE action='pr_gate' GROUP BY 1,2 ORDER BY n DESC` |
| 基准 | postgres | `python3 tools/benchmark_settlement.py --json-out artifacts/kpi_gate_100/benchmark_settlement.json` |
| static | static | `grep -n '<reject pattern>' <file>` |
| test | test | `` |

完整 SQL 见 `artifacts/kpi_gate_100/probes.sql`；复算命令：
`python3 artifacts/kpi_gate_100/measure_gate_coverage.py`（重写 `result.json` 与图输入）。
