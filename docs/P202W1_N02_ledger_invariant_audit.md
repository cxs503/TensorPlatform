# P202W1-N02 状态账本关系不变量审计与自愈销账

> 战役 DAG 节点 P202W1-N02 · TensorPlatform `backend/audit/ledger_auditor.py` ·
> 本文件由 `scripts/p202w1_n02_ledger_audit.py` 程序化生成（所有数字直接取自
> `artifacts/p202w1_n02/` 下的 JSON 工件，零手抄）。

## ① 案例介绍：为什么账本需要关系不变量

平台的状态账本由五张表构成：`campaign_plans`（战役）、`campaign_milestones`（里程碑）、
`campaign_nodes`（节点）、`tensor_dev_missions`（任务）、`tensor_reports`（报告）。
它们之间的生产关系并非单点外键可以覆盖：

| 关系 | 载体 | 约束能力 |
|---|---|---|
| 节点 → 战役 | `campaign_nodes.plan_id` | FK（`ON DELETE CASCADE`）|
| 节点 → 里程碑 | `campaign_nodes.milestone_id` | FK（`ON DELETE SET NULL`），**不校验同 plan** |
| 节点 → 任务 | `campaign_nodes.mission_id` | FK（`ON DELETE SET NULL`）|
| 节点 DAG 依赖 | `campaign_nodes.dependencies INT[]` | **无约束**：数组元素级引用 |
| 里程碑 DAG 依赖 | `campaign_milestones.dependencies INT[]` | **无约束** |
| 任务结果 | `tensor_dev_missions.result JSONB` | 类型保证（PG）/ TEXT 列无保证 |
| 报告 → 任务 | `tensor_reports.mission_id` | **无 FK**（迁移 090 未声明）|

于是四类「幽灵」可以静默积累：**幽灵节点**（plan_id 指向不存在的战役）、**幽灵战役/里程碑**、
**悬空依赖**（DAG 里指向已消失节点，使调度永远等不到上游）、**孤儿报告**（报告无法溯源到
任何任务）。本节点把上述关系显式化为 10 条可机器判定的不变量，并给出「扫描 → 自愈 → 复扫」
的闭环引擎。

物理类比（本域视角）：账本关系一致性等价于离散系统的**守恒律**——引用守恒（每个引用必须
落到实存节点）、层级守恒（子记录不得挂在已消失的父记录下）、可达性守恒（报告必须可达任务）。
违反即"跑冒滴漏"，会以假空转（节点永远 pending）与证据失联（报告无主）两种形态污染调度。

## ② 怎么做的：规则集 + 三段闭环 + 审计日志哈希链

1. **规则集**（`RULES`，10 条）：4 条节点外键、2 条整型数组依赖、1 条跨 plan 悬挂、
   1 条 JSON 合法性、2 条报告欠账，见 `backend/audit/ledger_auditor.py` 模块 docstring。
2. **扫描**：每条规则一条方言无关 SQL（数组/JSON 统一 `CAST(... AS TEXT)` 后由 Python 解析），
   已销账记录按旗标排除。
3. **自愈**：策略分两类——**物理修复**（`null_field` / `filter_array` / `reset_array` /
   `quarantine_json` / `link_mission`）与**标记销账**（`mark_orphan` 用迁移 095 原生
   `status='orphan'`；`mark_metadata_flag` / `mark_report_tag`）。标记不销毁数据：
   原始脏值原样留在审计日志 `before_state` 里。
4. **审计日志**：表 `ledger_audit_log` + JSONL 镜像，每条含 `run_id / ts_utc / rule_id /
   target_table / record_pk / strategy / before_state / after_state / applied / reason`，
   并串成 SHA-256 **哈希链**（`prev_hash`/`entry_hash`），可检出篡改与截断。
5. **生产 schema 保真**：审计在一次性 PostgreSQL 集群上按**真实迁移文件**
   `backend/migrations/086_tensor_dev_missions.sql, backend/migrations/090_tensor_reports.sql, backend/migrations/095_campaign_plans.sql` 建表（`INT[]`、`JSONB`、`CHECK` 全在），
   而非另写近似 DDL。DDL 语句 21 条成功 /
   4 条失败（失败的均为与本场景无关的 `ALTER`）。

## ③ 计算结果

### PostgreSQL（生产 schema，主口径）

| 规则 | 不变量 | 修复前 | 修复后 | 策略 |
|---|---:|---:|---:|---|
| INV-N1 | 幽灵节点：campaign_nodes.plan_id 悬空 | 1 | 0 | mark_orphan |
| INV-N2 | 悬空里程碑引用：campaign_nodes.milestone_id 指向不存在的里程碑 | 1 | 0 | null_field |
| INV-N3 | 跨战役悬挂：节点里程碑属于另一个 plan | 1 | 0 | null_field |
| INV-N4 | 幽灵任务引用：campaign_nodes.mission_id 悬空 | 1 | 0 | null_field |
| INV-N5 | 悬空依赖：campaign_nodes.dependencies 含非本 plan 节点 id | 2 | 0 | filter_array |
| INV-M1 | 幽灵里程碑：campaign_milestones.plan_id 悬空 | 1 | 0 | mark_metadata_flag |
| INV-M2 | 悬空里程碑依赖：milestones.dependencies 含非本 plan 里程碑 id | 1 | 0 | filter_array |
| INV-J1 | 非法 JSON：tensor_dev_missions.result 不是合法 JSON | 0 | 0 | quarantine_json |
| INV-R1 | 幽灵报告：tensor_reports.mission_id 悬空 | 1 | 0 | mark_report_tag |
| INV-R2 | 报告欠账：tensor_reports.mission_id 为空 | 3 | 0 | link_mission_or_mark |

合计：扫描 **12** 条违反，自愈 **12** 条，
跳过 0 条，复扫残余 **0** 条，
耗时 0.444 s。

### SQLite（方言可达性 + TEXT 脏数据口径）

| 项 | 数值 |
|---|---:|
| 扫描违反 | 13 |
| 自愈执行 | 13 |
| 复扫残余 | 0 |
| 孤儿报告（前 → 后） | 4 → 2 |

SQLite 上的 `INV-J1`=1（`result` 为 TEXT 列时脏值可存在），PG 上为 0（JSONB 类型系统兜底）——
这正是"方言差异"的诚实记录，而非扫描遗漏。

### 手工 SQL 对拍（C2）

对拍规则 6 条，
全部一致 = **True**；
逐规则集合见 `artifacts/p202w1_n02/manual_crosscheck.json`。

### 判据数字（机器可裁）

| 判据 | 要求 | 实测 | 结论 |
|---|---|---|---|
| C1 不变量扫描零遗漏 | 账本内外键全覆盖 | 账本内 FK 4 条全有规则（100.0%），未覆盖 0 条；另 6 条租户/身份 FK 显式列为出范围 | PASS |
| C2 幽灵节点识别 | 与手工 SQL 100% 一致 | True | PASS |
| C3 自愈率 | 可修复违反 100% | 100.0% | PASS |
| C4 修复后违反数 | = 0 | 0 | PASS |
| C5 报告欠账清偿 | 孤儿报告 100% 关联或标记 | 关联 2 / 标记 2 / 清偿率 100.0% | PASS |
| C6 审计日志 | 每次修复有完整日志 | 12 条，哈希链完好=True | PASS |
| C7 负控 | 健康账本零违反 | 健康账本扫描 0 条 / 动作 0 次 | PASS |

## ④ 结果图

- `fig_violations_before_after.png`：按规则的违反数，自愈前（红）vs 自愈后（绿，全零）。
- `fig_heal_strategies.png`：自愈策略分布，绿色=物理修复、黄色=标记销账。
- `fig_report_debt.png`：孤儿报告分类在审计前后的对照。

## ⑤ 注意点与局限

1. **标记销账的语义边界**：`mark_orphan` / `metadata.ledger_audit_settled` /
   `tags=ledger:settled_orphan` 只是把违反"登记在册并排除出扫描面"，**不修复根因**。
   幽灵节点背后的 plan 若需重建，必须由人（或战役重播种）决策——引擎刻意不做破坏性删除。
2. **不覆盖跨表语义规则**，例如"done 节点必须有报告"（已由既有 `audit/report_reconcile.py`
   的对账门负责），本引擎只处理**引用完整性**与**JSON 合法性**，两者互补不重复。
3. **DAG 环检测未纳入自动修复**：环无法靠删边安全恢复，需人工裁定拓扑意图；
   本节点只声明该边界，不擅自改图。
4. **未在生产库实测**：本地 PostgreSQL 15.5 实例对本租户是**只读**（`CREATE` 权限被拒，
   见工件 `environment.json` 的 `prod_like_readonly` 记录），因此改造为一次性
   `initdb` 集群 + 生产迁移 DDL 复现。生产库首跑应先 `--dry-run` 观察再放行写。
5. **性能未压测**：单条 UPDATE 粒度，万级违反时建议分批提交；本轮账本规模 ≤7 行/表，
   全流程 0.444 s。
6. **幽灵记录的产生路径（重要洞察）**：生产 schema 的外键会**拒绝**直接写入幽灵行，
   本轮复现用 superuser 的 `session_replication_role = replica` 绕过 FK 触发器——
   这正是真实世界的产生机理（批量导入 / 备份恢复 / 逻辑复制 / 手工 `COPY`）。
   换言之：不变量引擎在"正常写路径"上是冗余保险，在"旁路写路径"上是唯一防线。
7. **FK 覆盖的口径**：账本内关系 FK 4 条全部有规则；另外
   6 条 FK 指向 `organizations` / `users` / `factory_strategy_campaigns`
   / `tensor_agent_sessions`，属租户与身份面（org 隔离、鉴权路径负责），
   本引擎把它们**显式列账为出范围**而不是静默忽略——出范围清单见
   `artifacts/p202w1_n02/audit_report.json` 的 `runs.postgresql.fk_coverage.out_of_scope`。

## ⑥ 误差判定

本节点为**确定性账本审计**，不存在物理量误差；判据以"集合相等"与"计数为零"表达：

| 关键量 | 基准 | 实测 | 误差 |
|---|---|---|---|
| 幽灵节点集合（INV-N1） | 独立手写 SQL | 扫描器 | 0（集合完全相等） |
| 悬空依赖集合（INV-N5） | 独立手写 SQL | 扫描器 | 0（集合完全相等） |
| 幽灵里程碑集合（INV-M1） | 独立手写 SQL | 扫描器 | 0（集合完全相等） |
| 修复后残余违反 | 0 | 0 | 0 |
| 孤儿报告清偿率 | 100% | 100.0% | 0 |

**是否 <3%？** 是——上述量全部为精确集合/计数比较，误差恒为 0；不存在需要置信区间的统计量。

## 审计链与产物

| 产物 | 路径 |
|---|---|
| 审计引擎 | `backend/audit/ledger_auditor.py` |
| 用例（C1–C7） | `backend/tests/test_ledger_auditor_p202w1_n02.py` |
| 生成器 | `scripts/p202w1_n02_ledger_audit.py` |
| 全量审计结果 | `artifacts/p202w1_n02/audit_report.json` |
| 审计日志（哈希链） | `artifacts/p202w1_n02/audit_log.jsonl` |
| 判据数字 | `artifacts/p202w1_n02/criteria.json` |
| 手工 SQL 对拍 | `artifacts/p202w1_n02/manual_crosscheck.json` |
| 运行环境/DDL 溯源 | `artifacts/p202w1_n02/environment.json` |
