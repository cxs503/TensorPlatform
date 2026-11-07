# 四中心最小视图（PR-10 首期）—— 实现与验收报告（m5023 / IN19-Q8）

> 任务：前端四中心最小视图 —— Organization（舰队/战役实况）、Quality（评价流 + 质量门态）、
> Capability（能力台账 + Delta 面）、Innovation（提案 + 吸收率）四页最小版；
> 全部真后端数据零 mock，未实现面如实标注；判据＝四页可访问 + 展示数字与 SQL 对拍一致（≥6 项）+ 零假数据 + E2E ≥ 4。
> 仓：TensorPlatform（`backend/api/tensor_centers.py` 新增；`frontend-console` 四视图改造）。

## 1. 案例介绍：四中心要解决的平台问题

四中心是 AOG（自治组织治理）里的**可解释性面**：平台每天产生大量任务、报告、能力增量与战役节点，
但"组织现在什么状况"过去只能靠人写周报。四中心最小视图的物理对象是**平台运行状态本身**
（不是船舶物理场），因此验收基准不是解析解，而是**库内真值**：页面上的每个数字都必须能在
PostgreSQL 里用一条 SQL 复算出来。

生产库（org=1 `Eco Local`）在验收时刻的实测规模（`artifacts/m5023/center_payloads_snapshot.json`）：

| 实体 | 行数 | 备注 |
|---|---|---|
| `tensor_dev_missions` | 4670 | 8 种状态，succeeded 3848（82.4%） |
| `tensor_agent_sessions` | 2503 | active 43 |
| `tensor_executors` | 0 | ⚠ 舰队未登记（如实显示 0） |
| `campaign_plans` / `campaign_nodes` / `campaign_milestones` | 18 / 182 / 67 | 节点 in-flight 10 / blocked 15 / done 157 |
| `tensor_reports` | 3507 | qa_status: pending 3502 / passed 5（评价流严重积压） |
| `tensor_capability_deltas` | 1171 | ⚠ **全部** `no_substantive_delta`（实质增量 0，已入账 succeeded 1158/3848） |

> 上面两个 ⚠ 不是缺陷报告，而是**页面必须敢于显示的真值**：如果四中心用 0 冒充"无积压"、
> 用示意数字填补空表，它们就变成了第二个真相源。

## 2. 怎么做的（方法 / 流程 / 关键设置 / 工具链）

### 2.1 单一真值载荷（关键设计决定）

四中心不做"前端各调一堆细粒度端点再自己拼指标"，而是每中心一个**只读聚合端点**，
响应里 `metrics[]` 每项自带 `source`（人类可读的 SQL 口径）与后端实测 `value`：

```
GET /tensor/centers/{organization|quality|capability|innovation}
→ { center, schema_version, org_id, generated_at, implemented,
    metrics:[{key,label,value,unit,source,note,status}],
    gates:[{key,label,state,detail,source}],
    lists:{...}, not_implemented:[...], definitions:{...},
    provenance:{sources:[...], metric_count, data_origin:"platform_sql"} }
```

由此得到三条性质，正是判据要的：

1. **零假数据可机检**：页面模板里没有任何数字文本节点，`display()` 只有三个出口——
   后端值 / `—`（缺测）/ `未实现`（`status=not_implemented`）。
2. **口径同屏**：每个指标旁边直接显示它的 SQL 来源，用户不需要信任结论，只需要读口径。
3. **分母不由前端算**：比率（覆盖率、通过率、吸收率）在 SQL 侧算完再下发，避免"前端两数相除"这类隐性伪造。

### 2.2 四个中心的口径（全部可复算）

| 中心 | 面 | 口径（后端 `source` 逐项同屏） |
|---|---|---|
| Organization | 舰队 | `tensor_executors` 登记行；`tensor_agent_sessions` 在役（active/claimed/running/in_progress） |
| Organization | 战役实况 | `campaign_nodes.status` 分布（in-flight/blocked/done）+ 阻塞节点 `block_reason` 原文；org 隔离经 `campaign_plans.organization_id`（节点表本身无 org 列） |
| Quality | 评价流 | `tensor_reports.qa_status` 三态（pending/passed/rejected）；`qa_status IS NULL` 单列"未分类"，**不并入分母** |
| Quality | 质量门态 | 四道机器门的状态由门内计数直接判定：报告 QA 门（pending>0→有积压）/ 报告交付门（`report_owed`）/ 战役里程碑门 / 复现裁定门（无裁定入库→未实现） |
| Capability | 能力台账 | `tensor_capability_deltas` 六类词表（与 `/tensor/capability` 同一契约） |
| Capability | Delta 面 | 新到旧流水 + 覆盖率 = 已入账的 succeeded 任务数 / succeeded 任务总数（不入账不算覆盖） |
| Innovation | 提案 | 无提案存储 → `status=not_implemented`（显式不显示 0） |
| Innovation | 吸收率（代理） | (new_capability + capability_extension + reusable_knowledge) / 全部增量；证伪与缺陷修复**不计入分子** |

### 2.3 工具链

- 后端：FastAPI + SQLAlchemy Core `text()`；SQL 全部**可移植**（PG 生产 + sqlite CI）：
  不用 `FILTER`、不用 jsonb 运算符、不用 `now()/interval`，聚合一律 `SUM(CASE WHEN ...)`。
- 前端：Vue 3 + Element Plus；新增共享组件 `CenterBoard.vue`（指标栅格 / 门状态表 /
  未实现清单 / 口径定义），四视图变薄壳只负责取一次载荷 + 一张业务明细表。
- 验收：`pytest`（42 项后端契约 + 18 项前端静态）＋ 生产库对拍脚本 ＋ `vite build`。

## 3. 计算结果（数字表，程序化注入零手抄）

### 3.1 判据达成

| 判据 | 目标 | 实测 | 证据 |
|---|---|---|---|
| 四页可访问 | 4 页 | 4/4 路由 + 4/4 视图编译成独立 chunk | `artifacts/m5023/vite_build.log`（含 4 个 `*CenterView-*.js`） |
| 数字与 SQL 对拍 | ≥6 项 | **44/44 一致**（四中心全覆盖） | `artifacts/m5023/center_sql_crosscheck.json` |
| 零假数据 | 0 处 | 模板裸数字文本节点 0；mock/faker/random 0；脚本写死值 0 | `tests/test_four_centers_frontend.py`（18 passed，含阴性对照） |
| E2E | ≥4 | 4 条 HTTP 端到端（每中心一条）+ 33 项对拍参数化用例 | `artifacts/m5023/pytest_backend_centers.log`（47 passed） |
| 未实现如实标注 | 有实体支撑才出数 | 创新实体 `implemented=false` + 3 条 not_implemented；质检面缺列/缺表→`未实现` | `test_e2e_innovation_center` / `test_e2e_quality_center` |

### 3.2 生产库对拍抽样（端点数 = SQL 数）

| 中心 | 指标 | 端点值 | 独立 SQL 值 | 一致 |
|---|---|---|---|---|
| organization | missions_total | 4670 | 4670 | ✅ |
| organization | campaign_nodes_blocked | 15 | 15 | ✅ |
| organization | sessions_active | 43 | 43 | ✅ |
| organization | executors_registered | 0 | 0 | ✅ |
| quality | reports_qa_pending | 3502 | 3502 | ✅ |
| quality | reports_qa_passed | 5 | 5 | ✅ |
| capability | deltas_total | 1171 | 1171 | ✅ |
| capability | coverage_ratio | 0.3009 | 0.3009 | ✅ |
| innovation | absorption_rate | 0.0 | 0.0 | ✅ |

（全 44 项见 `center_sql_crosscheck.json`，`all_match: true`。库为在线生产库，上表取自 `center_payloads_snapshot.json` 与对拍记录同一时刻，后续计数会随平台运行漂移——页面读的是实时值，不是本报告的静态数字。）

## 4. 结果图

- `fig1_four_centers_live.png`：四中心生产库实测面——战役节点状态分布、报告 QA 评价流、
  能力增量类别分布、三个关键比率（覆盖率 30.09% / 吸收率 0.0% / QA 已裁定通过率 100%（分母仅 5 条已裁定））。
- `fig2_crosscheck_identity.png`：44 项"端点值 × 独立 SQL 值"散点，全部落在恒等线上
  （symlog 轴以同时容纳 0 与 4670）。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **`campaign_nodes` / `campaign_milestones` 没有 `organization_id` 列**。首版按
   `COALESCE(n.organization_id, p.organization_id)` 写，生产库直接 `UndefinedColumn`；
   正确做法是经 `campaign_plans.organization_id` 判域（节点表只挂 `plan_id`）。
   生产库 182/182 节点都能归属到计划，孤儿节点会计入"无 org"而不计入任何中心——这是已知边界。
2. **`quality_check_results` 既无 `organization_id` 列且库内 0 行**。容错查询不能用
   "吞异常"了事：被吞掉的失败会让 SQLAlchemy 会话停留在 aborted 事务里，后续查询连锁失败
   （实测报 `InFailedSqlTransaction`）。必须在 `except` 里 `rollback()`，否则整个中心端点连带 500。
3. **局部 `-q` 的 pytest 在本仓会吞掉汇总行**（`backend/pyproject.toml addopts="-q --tb=short"`），
   验收日志用 `-o addopts=""` 才能在报告的 artifact 里留下 `47 passed` 这种可读凭据。
4. **前端零假数据的判据只覆盖"模板裸数字 + mock 味 token + 脚本写死值"三种最短路径**。
   它抓不到"后端给了错口径"这类问题——那由 33 项 SQL 对拍负责。两层判据互补，但都不构成
   对"业务定义是否合理"的证明（例如吸收率的分子口径就是一种**规范选择**，见下条）。
5. **吸收率是代理指标，不是创新实体本体**。创新实体（提案受理 / 自主探索 / 创新产出账）
   在 SPEC 第 4 章仍属未实现，页面 `implemented=false` 且三条 `not_implemented` 明确写出理由；
   吸收率只承认"可复用增量"，证伪假设与缺陷修复不计入分子。当前生产库 1171 条增量**全部**
   为 `no_substantive_delta`，因此吸收率 0.0%——这是真值，不是渲染失败。
6. **部署边界（honest）**：本次没有重启线上 `:8010` 服务。端点行为是用**本仓代码**的 FastAPI
   router + **生产库只读会话**（TestClient）验证的，因此"代码对生产数据的正确性"已被证明，
   但"线上进程已加载新代码"**未**被证明（需要一次发布）。四页的浏览器端 Playwright E2E 未做：
   `frontend-console` 无 e2e 工程，改用 `vite build` 通过 + 路由/绑定静态判据 + 4 条后端 E2E 替代。

## 6. 误差判定（关键量 vs 基准，逐项回答"是否 < 3%"）

本任务的"基准"是**库内真值**（同一条 SQL 在同一个库上的结果），而非实验/解析解，故判据是
**逐位相等**而不是 3% 误差带：

| 关键量 | 基准 | 端点值 | 相对偏差 | <3% ? |
|---|---|---|---|---|
| organization.missions_total | SQL count = 4670 | 4670 | 0.00% | ✅（精确相等） |
| organization.campaign_nodes_blocked | 15 | 15 | 0.00% | ✅ |
| organization.sessions_active | 43 | 43 | 0.00% | ✅ |
| quality.reports_qa_pending | 3502 | 3502 | 0.00% | ✅ |
| capability.deltas_total | 1171 | 1171 | 0.00% | ✅ |
| capability.coverage_ratio | 0.3009 | 0.3009 | 0.00% | ✅ |
| innovation.absorption_rate | 0.0 | 0.0 | 0.00% | ✅ |
| 全量 44 项 | — | — | 0 项不一致 | ✅ 44/44 |

口径层面（不是数值层面）的两处"非零不确定"必须写明：①吸收率的分子集合是一种规范选择
（可复用增量 ⊆ 六类中的三类），换口径数值会变；②`qa_status IS NULL`（未分类）被排除在通过率
分母外，若并入分母，通过率会从当前值下降。两者都在 `definitions` 里显式写入口径，
页面同屏可读，不留模糊。

## 7. 复现

```bash
# 后端契约 + E2E + 对拍（离线，内存 sqlite，合成域名）
cd backend && DATABASE_URL=sqlite:///:memory: pytest tests/test_tensor_centers_contract.py -o addopts="" -q
# 前端静态判据（零假数据 / 四页可达 / 未实现标注）
pytest tests/test_four_centers_frontend.py -o addopts="" -q
# 生产库对拍（只读；需 DATABASE_URL）
python scripts/verify_four_centers_crosscheck.py --database-url "$DATABASE_URL"
# 验收图（程序化注入，零手抄）
python scripts/make_four_centers_figures.py --database-url "$DATABASE_URL"
# 前端可访问性（四页各自成 chunk）
cd frontend-console && npx vite build
```