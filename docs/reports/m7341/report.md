# IN99-N09 算例身份键与证据失效成熟化：唯一标识 · 版本钉扎 · stale 自动检测 · 回滚

**节点**：IN99-N09（战役自主播种，物理域，风险中） · **分支**：`agent/m7341` · **落库**：`TensorPlatform`
**模块**：`ops/taskplane/case_identity.py`（机制层）· `backend/migrations/110_case_identity_evidence_staleness.sql`（PostgreSQL 表结构）
**用例**：`tests/test_case_identity.py`（45 passed）· **工件**：`artifacts/m7341/`（验收 JSON / 审计库 / 两张图）
**纪律**：零领域语义（全用合成命名空间 `acme_ns` / `acme_solver`）；所有时间戳显式注入；判据数字全部由程序或 SQL 生成，零手抄。

---

## ① 案例介绍：要解决的是什么问题（物理/工程背景 + 参数表）

### 1.1 背景

平台已有大量"验证结果"（战役节点的验收证据：收敛表、误差曲线、场量图），但它们今天只有**文本身份**——报告里写一句"某算例验证通过"，工件挂在某个路径上。由此产生三类静默腐化：

1. **算例身份不唯一**：同一算例在不同任务书里写成 `Case A` / `A-2` / `case a`，机器无法判断两条证据是否同一算例，证据无法聚合也无法互相裁决。
2. **无版本钉扎**：证据产生时用的代码 git sha、依赖求解器版本、证据工件内容摘要（sha256）没有结构化落库。上游公式一改，下游证据仍然"有效"，而且没人能证明它什么时候失效。
3. **无失效回路**：即使知道上游变了，也没有机制把相关证据标 `stale`、置 `blocked`、生成重算任务，更没有"把算例钉回上一个可用版本"的回滚动作。

本节点把这三件事做成**机制**：身份键走数据库 UNIQUE 约束（权威在 DB，不在应用层 if）、版本钉扎记录"git sha + 依赖清单 + 每个证据文件 sha256"、上游指纹比对自动判 stale 并按 24 h SLA 记账、stale 证据自动进 `blocked` 并生成**幂等**重算任务、回滚把算例切回旧钉扎且不粉饰旧证据。

### 1.2 算例与参数表（合成算例，无领域语义）

| 项 | 取值 | 单位/形态 | 说明 |
|---|---|---|---|
| 算例外部标识 `ext_id` | `acme_ns:case_alpha` | `<namespace>:<case_key>` | 命名空间段 2–32 字符、键段 2–64 字符，全小写 |
| 允许命名空间白名单 | `acme_ns`, `acme_solver` | 列表（配置注入） | 模块内不硬编码任何域名词 |
| 钉扎版本 | `v001`, `v002` | 标签 | `UNIQUE(case_id, version_label)` |
| 代码钉扎 | `d1e2f3a4b5c6d1e2f3a4b5c6d1e2f3a4b5c6d1e2` | 40 位十六进制 git sha | 非 sha 形态直接拒收 |
| 上游单元 A | `acme_solver.core` 1.4.0 → 1.4.1 | 版本号 + 代码 sha | 真版本推进（正控） |
| 上游单元 B | `acme_ns.table_loader` 0.9.2 → 0.9.3 | 版本号 + 数据工件 sha256 | 第二次真版本推进 |
| 证据工件 | `case_alpha_fit.txt`（24 B）、`case_alpha_grid.txt`（14 B） | 文件，sha256 64 位十六进制 | 内容 sha256 当场计算 |
| 检测 SLA 上限 | 24 | h（小时） | 上游变更后 24 h 内必须标记 |
| 参考时钟 | `2026-11-08T00:00:00Z` | ISO-8601 UTC | 所有场景时间由该时钟推导 |
| 证据状态机 | `valid → stale → blocked / not_supported` | 枚举 | `stale` 是中间态，必须被推进到终态 |

---

## ② 怎么做的：方法、流程与工具链

### 2.1 设计要点

| 机制 | 实现 | 为什么这么做 |
|---|---|---|
| 身份键 | `case_registry.ext_id` + `UNIQUE(ext_id)`；`validate_ext_id()` 做形态与命名空间白名单前置校验；`sqlite3.IntegrityError → DuplicateExtId` | 唯一性权威必须在数据库（并发/多进程都成立），应用层 if 只做友好报错 |
| 版本钉扎 | `case_version_pins`：`git_sha` + `dep_manifest`（每依赖 unit/kind/version/git_sha/artifact_sha256/fingerprint）+ `dep_digest` + `pin_digest`；`case_evidence` 每行存证据文件 sha256 与 size | 钉扎摘要可复算，证据内容漂移能被独立复核 |
| stale 检测 | 依赖指纹 `sha256(unit, kind, version, git_sha, artifact_sha256)` 对上游当前指纹做逐字段校验；不一致 → 每条受影响证据记一条 `stale_events`（含 `latency_hours`、`sla_hours`、`sla_met`） | 只比版本号会漏掉"同版本号、内容已改"的腐化（见 C5 负控） |
| 处置 | `remediate_stale()`：`stale → blocked` + `recompute_tasks`（`task_key = sha256(ext_id|version|node_ref|artifact|new_fingerprint)`）+ `UNIQUE(task_key)`；`recompute=False` 走 `not_supported` | 幂等：重复运行/并发重放都不会重复建任务 |
| 回滚 | `rollback(ext_id, to_version)`：`active_version` 切回旧钉扎、更新版本证据置 `blocked(reason=superseded_by_rollback:*)` 并入队重算；**因上游变更被 block 的旧证据不复活** | 回滚不是删数据、也不是粉饰：被上游腐化的证据必须走重算才能重新有效 |
| 单一真源 | `SCHEMA` 生成 SQLite DDL（测试/本地）与 PostgreSQL DDL（签入 `backend/migrations/110_*.sql`），drift 由用例逐字节比对 | 两种方言一个真源，杜绝"测试库与生产库两套表" |
| 可复算 | 所有 `now` 由调用方注入；`iso()` 固定格式；`dep_digest`/`pin_digest` 规范化 JSON 排序后哈希 | 同输入两次运行逐字节相同（用例 `test_demo_all_criteria_pass_and_is_deterministic` 守护） |

### 2.2 流程

```
register_case(ext_id)            # C1：UNIQUE 约束拒绝重复/非法形态
   ↓
pin_version(git_sha, deps, evidence)   # C2：sha256 当场算、清单落库、pin_digest 可复算
   ↓   （上游推进：record_upstream_state，版本或内容任一变化都体现为指纹变化）
detect_stale(now)                # C3：指纹比对 → stale_events（时延 + SLA 记账，幂等）
   ↓
remediate_stale(now)             # C4：stale → blocked + 幂等重算任务（或 not_supported）
   ↓   （可选）
rollback(to_version)             # 回滚：切回旧钉扎、更新版证据 blocked 并队重算
```

### 2.3 工具链

Python 3.11 · SQLite 3（引擎与用例）· SQLAlchemy 不参与（本模块刻意零 ORM 依赖，便于离线复算与 loong64 节点执行）· pytest 9 · matplotlib（图）· `backend/migrations/`（PostgreSQL DDL 签入）。

复现命令：

```bash
python3 ops/taskplane/case_identity.py --demo \
    --workdir artifacts/m7341/demo_work \
    --json-out artifacts/m7341/case_identity_acceptance.json \
    --db artifacts/m7341/case_identity_audit.db
python3 ops/taskplane/case_identity_figures.py \
    --acceptance artifacts/m7341/case_identity_acceptance.json --outdir artifacts/m7341
python3 -m pytest tests/test_case_identity.py -q     # 45 passed
```

---

## ③ 计算结果（数字全部程序化注入：`artifacts/m7341/case_identity_acceptance.json` + `case_identity_sql_audit.json`）

### 3.1 判据总览（机器裁决）

| 判据 | 内容 | 结果 | 关键数字（工件字段） |
|---|---|---|---|
| C1 | ext_id 全库唯一，违反时 INSERT 拒绝 | **PASS** | 重复插入抛 `DuplicateExtId`；注册后、重复尝试后行数均为 1；非法形态 9/9 拒收（用例参数化 9 例，演示场景 5 例） |
| C2 | 钉扎含 git sha + 依赖版本列表 + 证据 sha256 | **PASS** | git_sha 40 位十六进制；依赖 2 项（`acme_ns.table_loader` 0.9.2 / `acme_solver.core` 1.4.0）；证据 2 行 sha256 等于文件重算值 |
| C3 | 上游变更后 24 h 内标记 stale | **PASS** | 变更 t=+2.0 h、检测 t=+3.0 h，时延 **1.0 h ≤ 24 h**；标记 2 条证据；`sla_breaches=0`；重跑不重复记账（0 新事件） |
| C4 | stale 节点进 blocked 并生成重算任务 | **PASS** | 首次：`blocked=2`、`tasks_created=2`、队列 2；二次运行：`tasks_created=0`（幂等）；`recompute=False` 分支 → `not_supported=2` |
| C5 | 负控：人为制造版本冲突验证检测触发 | **PASS** | 同版本号 `1.0.0`、代码 sha 变 → `version_conflict=True`，指纹检测器命中 1 条证据；只比版本号的朴素实现命中 **0**（漏检） |
| 回滚 | stale 证据可回滚/可重算，不粉饰 | **PASS** | `active_version` 切回 `v001`；v002 证据 1 条置 blocked 并入队 1 个重算任务；因上游变更被 block 的 v001 证据 **2 条保持 blocked** |

### 3.2 C2 钉扎记录（片段，直接从库导出）

| 字段 | 值 |
|---|---|
| `version_label` | `v001` |
| `git_sha` | `d1e2f3a4b5c6d1e2f3a4b5c6d1e2f3a4b5c6d1e2` |
| `dep_digest` | `24507f1b0ecd8d926395246d10a2cdf9a87d6d9596bd215d4c4ce0878dc5d281` |
| `pin_digest` | `25d431b6819ddaf480618925ab53980ccd3e8573f159e5360fde1457d2bd3959` |
| 证据 `node-fit` sha256 | `c8dbdf83686e7b3738a1816c0fa6d6344c806ddc96184cfc8f250d97ba702467` |
| 证据 `node-grid` sha256 | `8b202113eeb4f56317ed0a4ed49763048fc4dc275369bcfe23b9e43f5bfcc7d2` |

### 3.3 stale 事件台账（`stale_events` 全表，4 条）

| id | 证据 | 上游单元 | 检测时延 (h) | SLA 上限 (h) | SLA 达成 | 处置 |
|---|---|---|---|---|---|---|
| 1 | 1 | `acme_solver.core` | 1.0 | 24 | 是 | blocked |
| 2 | 2 | `acme_solver.core` | 1.0 | 24 | 是 | blocked |
| 3 | 1 | `acme_ns.table_loader` | 0.5 | 24 | 是 | blocked |
| 4 | 2 | `acme_ns.table_loader` | 0.5 | 24 | 是 | blocked |

### 3.4 超 SLA 场景（隔离库，诚实记账）

上游 t=+1 h 变更、t=+30 h 才检测 → 时延 **29.0 h > 24 h**，`sla_met=0`、`sla_breaches=1`；证据照样被标 stale，但**记账如实写成 breach**，不因"我们自己的机制晚检测到"而改口径。

### 3.5 审计快照（demo 持久库 `artifacts/m7341/case_identity_audit.db`）

`cases=1` · `pins=2` · `evidence=3` · `stale_events=4` · `sla_breaches=0` · `queued_recompute_tasks=3` · `case_status={rolled_back:1}` · `evidence_status={blocked:3}`
（DB sha256 与逐表导出见 `artifacts/m7341/case_identity_sql_audit.json`，可 `sqlite3` 直接复核。）

---

## ④ 结果图

**图 1** `case_identity_timeline.png` — 证据生命周期时间轴：上游变更 t=+2.0 h（蓝虚线）→ stale 检测 t=+3.0 h（红色，时延 1.0 h）→ 处置（blocked + 重算入队）→ 回滚至 v001；浅蓝带为 24 h SLA 窗口（图中数字全部读自验收 JSON）。

**图 2** `case_identity_detector_matrix.png` — C5 负控的检测器 × 场景命中矩阵：真版本推进（1.4.0→1.4.1）两种检测器都命中（指纹 2 条 / 版本号 1 条）；**同版本号但代码 sha 变化**时指纹检测器仍命中 1 条，而只比版本号的实现命中 0 条——这就是必须用指纹而非版本号的直接证据。

---

## ⑤ 注意点 / 局限 / 适用范围

1. **引擎只在 SQLite 上实跑**。PostgreSQL DDL 已签入 `backend/migrations/110_case_identity_evidence_staleness.sql` 并有逐字节 drift 用例守护，但本环境没有 PG 实例，**未在 PG 上实际执行过**（唯一索引语义两者一致，`BIGSERIAL/TEXT/DOUBLE PRECISION` 为标准类型）。
2. **检测是拉模式**：`detect_stale()` 需要被调度器周期性调用（本节点只提供机制与判据，未接入平台常驻 cron；接入点建议挂在战役节点完成钩子 + 每日巡检）。
3. **上游台账需被喂数**：`upstream_state` 未登记的单元在比对时被**跳过而非报警**——这是已知盲区（宁可漏标也不假标），后续应加"钉扎引用了未登记单元"的显式告警。
4. **"24 h 内"依赖调度频率**：判据由注入时钟验证；生产达成率取决于巡检间隔，本模块只保证"检测到即如实记录时延与 SLA 达成与否"。
5. **回滚是元数据级**：`rollback()` 改钉扎指向与证据状态，**不动 git 分支、不删工件**；被上游腐化的证据不会因回滚复活（必须重算），这是有意的保守语义。
6. **未做**：权限/审计主体（谁钉扎、谁回滚）目前只落 `pinned_by` 文本字段；跨租户命名空间隔离尚未强制（白名单由调用方注入）。

---

## ⑥ 误差判定（逐项 + 显式回答"是否 < 3%"）

**先说口径**：本节点是**机制层**，不是仿真算例，因此不存在"计算值 vs 实验/解析参考值"的物理误差——这里**无基准**（无解析解、无实验值、无参考值可比），不以伪造的物理误差百分比充数。可判的"误差"是机制指标的偏差与漏检率：

| 关键量 | 本实现 | 基准/判据 | 偏差 | 是否达标 |
|---|---|---|---|---|
| stale 检测召回（4 条应标证据） | 4/4 = 100 % | 判据"全部标记" | 0 条漏检 = 0 % | 达标 |
| 检测时延 vs 24 h SLA 窗口 | 1.0 h | SLA 24 h | 占窗口 4.2 %（余量 95.8 %） | 达标 |
| 超 SLA 场景记账（29.0 h） | `sla_met=0` | 如实记账 | 记录 breach 1 条，无粉饰 | 达标（诚实失败） |
| C1 非法/重复标识拒收 | 9/9 非法 + 1/1 重复 | 判据"全部拒绝" | 0 % 漏放 | 达标 |
| C5 同版本号冲突漏检率：本实现 | 0/1 = 0 % 漏检 | 判据"必须触发" | 0 % | 达标 |
| C5 同版本号冲突漏检率：只比版本号（对照基线） | 1/1 = 100 % 漏检 | 同一判据 | **100 %**（基线不达标，即本节点要修掉的盲区） | 基线不达标 |
| 重算任务幂等性（二次运行） | 新增任务 0 个 | 判据"不重复建" | 0 % 重复 | 达标 |

**显式结论**：本轮**没有**与实验/基准参考值对比的物理量，因此对物理量而言"是否 < 3 %"**不适用**（原因如上，无基准）；对可量化的机制指标，最坏一项偏差是检测时延占 SLA 窗口 **4.2 %**（> 3 %），若强行套 3 % 门槛则该指标不满足——但它服务的判据是"24 h 内"，1.0 h 相对 24 h 有 95.8 % 余量，判据成立。**机器判据 C1–C5 + 回滚共 6/6 全部通过**（`artifacts/m7341/case_identity_acceptance.json: all_passed=true`）。

---

## 附：审计链

| 环节 | 工件/命令 | 可复核性 |
|---|---|---|
| 判据数字 | `artifacts/m7341/case_identity_acceptance.json` | CLI 一次重跑即重现（同参考时钟逐字节相同） |
| 落库证据 | `artifacts/m7341/case_identity_audit.db` + `case_identity_sql_audit.json`（含 DB sha256、逐表导出） | `sqlite3` 直接查 |
| 表结构 | `backend/migrations/110_case_identity_evidence_staleness.sql`（生成器输出逐字节一致） | `pytest tests/test_case_identity.py::TestSchema` |
| 图 | `case_identity_timeline.png` / `case_identity_detector_matrix.png`（数字读自验收 JSON） | `ops/taskplane/case_identity_figures.py` |
| 用例 | `tests/test_case_identity.py`（45 passed） | `python3 -m pytest tests/test_case_identity.py -q` |