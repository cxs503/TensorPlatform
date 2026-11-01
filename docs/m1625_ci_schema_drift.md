# m1625 — `tests/factory` schema 漂移（main CI control-plane job 连红）修复报告

- 仓库/分支：TensorPlatform · `agent/m1625`（基线 main @6e24298，并入 2e0dc4b）
- 失败签名：`psycopg2.errors.UndefinedColumn: column "report_id" / "fail_stage" of relation
  "tensor_dev_missions" does not exist`（5 例 FAILED，`tests/factory/test_agent_control_plane.py`、
  `tests/factory/test_strategy_campaign.py`）
- 结论：**不是测试写错列名，是测试库 schema 少了一批迁移**。列名 `report_id` / `fail_stage`
  在模型与生产 schema 中都真实存在（分别由 `096_report_gate.sql` 与 `095_mission_lifecycle.sql`
  增加），错的是「把迁移喂给测试库」这条通道。

## 1. 根因链

| # | 事实 | 证据 |
|---|------|------|
| 1 | `backend/services/factory_bootstrap.py` 用**手写文件名元组**把迁移喂给测试库：084 基线 + `086..093` + soul runner 里的 `094` | `git log -S` → 引入于 `2f7d2fe`（2026-10-21，抽出 resident community/development harness 的 PR）；之后该元组再未被更新过（`git log -- backend/services/factory_bootstrap.py` 只有 `2f7d2fe`、`5a1eae5` 两次） |
| 2 | 生产侧走版本登记 + glob 的 `backend/migrations/run_migration.py`（`[0-9][0-9][0-9]_*.sql` 全量按名排序），所以**生产库有这些列，测试库没有** | `run_migration.py:16,22` |
| 3 | 后续迁移按 084 之后编号落地，但没有一条回头改 bootstrap 元组：`090_tensor_reports.sql`（PR #16 feat/tensor-reports，`2ea470b`）、`095_campaign_plans.sql` / `097_campaign_plans_superset.sql`、`095_mission_lifecycle.sql`（PR #223 agent/m597，`1f5f711`，新增 `fail_stage`/`report_owed`/`suspended_at`/`budget_used`）、`096_report_gate.sql`（`966ea2e` 快照并入 main，新增 `report_id` FK→`tensor_reports`） | `grep -n "fail_stage"`→`095_mission_lifecycle.sql:35`；`report_id`→`096_report_gate.sql:12` |
| 4 | API 侧先按正确 schema 写了依赖：`tensor_missions.py`（m641，`0e7686f`，PR #229）在任务完成/欠报告路径读写 `report_id`、`report_owed`；`tensor_mission_lifecycle.py`（m597）读写 `fail_stage` | `backend/api/tensor_missions.py:758,780,1272`；`backend/api/tensor_mission_lifecycle.py:327,350,381` |
| 5 | 因此 5 例 FAILED 不是某一张 PR 自带的红：任一 PR 单跑自己的用例都绿，`main` 上把「新迁移 + 新 API 依赖」拼齐后才红，`e1089492/2e0dc4b4/a50a7f22` 只是继承者 | 本报告 §3 基线复现 |

**为什么漏过**：schema 契约在两条路径上被分别维护——生产用 glob（自动），测试用手写清单（会漂）。
没有任何测试断言「磁盘上的迁移文件 == 测试库已应用的迁移」，所以新增迁移时 CI 只是静默少建列，
直到某个 API 路径被 `tests/factory` 走到才炸。**修复采取「补迁移通道」而不是「改测试列名」**：
列名与模型/生产一致，改测试等于把 schema 漂移固化成契约。

## 2. 修复（3 个文件）

1. `backend/services/factory_bootstrap.py`
   - 新增 `factory_migration_files()`：glob `[0-9][0-9][0-9]_*.sql`，取编号 > `BASE_PREFIX`(84) 的文件，
     按**文件名序**（与生产 `run_migration.py` 同一排序规则）返回；`PRODUCTION_ONLY` 白名单留给
     「确实需要生产平台 schema」的迁移，且必须写理由。
   - bootstrap 改为遍历该清单应用，删掉手写元组 → 新增迁移**不可能再被测试库跳过**。
2. `backend/migrations/097_campaign_plans_superset.sql`
   - 顺带修出链上第二个断点：该迁移无条件用 m611 遗留列回填
     （`UPDATE campaign_plans SET name = COALESCE(name, title) ...`），而全新库是按
     `095_campaign_plans.sql` 建的表、**没有** `title/created_utc/source_mission_id` →
     空库执行直接 `column "title" does not exist`，整条迁移链在全新安装上断裂
     （修前不可达，因为 097 从未在全新库跑过；补通道后立刻暴露）。
   - 改为 `DO $$ ... information_schema.columns ... IF EXISTS ... EXECUTE ... $$` 条件回填：
     遗留库（生产）行为逐字不变，全新库安全跳过回填。
3. `tests/factory/test_schema_migrations.py`（新增，4 例）
   - 断言 084 之后每个编号迁移都在应用清单里、清单必须按名排序、`PRODUCTION_ONLY` 必须带理由；
   - 从迁移文件里解析 `ALTER TABLE tensor_dev_missions|campaign_plans ADD COLUMN IF NOT EXISTS`，
     逐列断言在实际测试库里存在（直接把本次故障模式编成回归门）。

## 3. 证据（全部程序化读回，不手抄）

环境说明：本地沙箱无外网，`pgserver` / `pytest-asyncio` 无法安装；用一个仅存在于 `/tmp`、
**未入库**的 `pgserver` 替身（`initdb`/`pg_ctl` 以 `postgres` 用户起本地 unix-socket 集群）+
`psycopg2`/`bcrypt` 兼容 shim 跑真实 PostgreSQL 集成测试。CI 用的是官方 `pgserver`，DDL 路径一致。

| 检查 | 命令 | 结果 |
|------|------|------|
| 基线复现（修复前） | `pytest tests/factory/test_strategy_campaign.py -x` | `FAILED` · `psycopg2.errors.UndefinedColumn: column "report_id" of relation "tensor_dev_missions" does not exist` |
| 修复后全量 | `python -m pytest tests/factory -q` | **168 passed**, 36.42s |
| 变异验证（把 bootstrap 换回手写元组） | `pytest tests/factory/test_schema_migrations.py` | **3 failed**，逐列点名 `budget_frozen/budget_used/fail_stage/report_id/report_owed/suspend_reason…`、`campaign_plans` 整表缺失 → 守护测试确实抓得住本故障 |
| 迁移通道实测 | `python artifacts/m1625/probe_schema.py`（两个一次性集群，`information_schema` 读回） | 修复前应用 **9** 个迁移文件 / 8 个签名列**全缺**；修复后应用 **19** 个（18 迁移 + 084 基线）/ 8 列**全在**，`public` 表 15 → 43 |
| backend/tests 抽样 | 见 §4 | 无连带破坏 |

图：`artifacts/m1625/schema_drift_coverage.png`（左：迁移文件应用数 9→19；右：CI 签名点名的列
在测试库中的存在性 before/after）。原始数字：`artifacts/m1625/schema_coverage.json`。

## 4. backend/tests 抽样（连带破坏排查）

| 模块 | 结果 | 判读 |
|------|------|------|
| `test_abtest_unit.py`, `test_audit_unit.py`, `test_agent_routing.py`, `test_tensor_repro_redteam.py` | 全绿 | 无影响 |
| `test_alert_unit.py`, `test_api_key_unit.py` | FAILED（`'Depends' object has no attribute 'query'`） | **预存在**：`git stash` 净树复跑同样 FAILED，与本次改动无关（本地 FastAPI 0.136 依赖注入版本漂移） |
| `test_auth_guard.py` | FAILED（async 未支持） | 环境缺 `pytest-asyncio`，非代码问题 |
| 24 个模块 | collection ERROR（`redis` 等三方包缺失） | 离线沙箱依赖不全，非代码问题 |

## 5. 注意点 / 局限

- `094_factory_soul.sql` 现在会经两条通道各执行一次（glob + soul 版本登记器）；DDL 全部
  `IF NOT EXISTS` 且登记器幂等，实测无副作用，但这是「双轨」的已知冗余，后续可收敛。
- `097` 文件里保留了迁移自身的 `BEGIN; ... COMMIT;`；在被外层事务包裹时 PostgreSQL 只告警，
  行为与生产一致，本次未动以保持最小改动。
- `factory_migration_files()` 目前把 `085_tensor_workspace.sql`、`999_tensorphysics_run_registry.sql`
  也纳入（两者在 084 之后、可在全新库执行），实测应用成功；若将来出现真正的「生产专用」迁移，
  必须登记到 `PRODUCTION_ONLY` 并写理由（守护测试会强制理由非空）。
- 本单只修 schema 通道；未审计生产库实际 `schema_migrations` 登记与
  `backend/migrations/versions/`（alembic 侧仅一个空壳 initial 版本）的关系，留待平台审计项。
