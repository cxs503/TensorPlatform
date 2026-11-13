# FEAT-DEMAND-AUTOINTAKE v1.1 —— 事后抽样审计（真相门）

- 任务：TensorPlatform #1305 重开（管到底第 2 轮），mission 17298，分支 `agent/m17298`
- 交付切片：**事后抽样审计**（可领锚的出口）。原关闭 PR 是 21 文件 / +3141 行的整包（铸造帽 +
  同步建单 + 事后审计 + 对照计量四件事捆在一起）；按「单 PR ≤10 文件、单一功能点」纪律，
  本轮只取其中**唯一没有替代物的那一件**：给可领面装出口。

## ① 案例介绍：为什么需要「出口」

平台已有 `FEAT-DEMAND-AUTOINTAKE` v1（main 上真实存在，`tools/demand_intake/`）：把 agent
finish 报告里的建议解析成候选锚，过事前门（三判据）后写成
`feature_ledger.status='open'`，进入 feeder / board-claim 的**可领面**。

v1 的病：**可领面只有入口，没有出口**。锚一旦 open 就再没有人回头看——判据错的、验收写虚的、
与既有锚近义改写的噪声锚会长期占据可领面，只能靠人肉发现。机器链缺的是「事后复核 + 回撤」。

本轮补的机制（`tools/demand_intake/audit.py`）：

```
ingest ─► gate（事前，min_chars=4）─► open ─► audit（事后，min_chars=8，帽 20/日）
                                                ├─ pass ─► 保持 open（log 记 verdict）
                                                └─ fail ─► dropped + 未终态单 revoked + chronicle 留痕
```

数据面（本模块自有的两张表列 + 一张视图，全部**加列不改列**）：

| 对象 | 变化 | 用途 |
|---|---|---|
| `demand_intake_log.audit_verdict` / `audit_reason` / `audited_at` | 新增（缺省 `''` = 未审） | 裁决留痕 + 日帽计数 |
| `demand_intake_log.mission_id` | 新增 | 锚 → 产出它的单（回撤直连） |
| `idx demand_intake_log_audit_idx` | 新增（部分索引） | 已审行计数/覆盖率统计 |
| `VIEW demand_intake_audit_daily` | 新增 | pass/fail/audited 日度口径，报告数字唯一来源 |

参数表：

| 参数 | 值 | 出处 | 说明 |
|---|---|---|---|
| 事前门验收字符下限 | 4 | `gate.MIN_ACCEPTANCE_CHARS` | v1 既有，不动 |
| 事后审计验收字符下限 | 8 | `store.AUDIT_MIN_ACCEPTANCE_CHARS` | 先上车后查票，票价更贵 |
| 审计日帽 | 20 | `store.AUDIT_DAILY_CAP` | 抽样不是全量，缺口必须报 |
| 单子终态集合 | succeeded/failed/stopped/revoked/orphan | `store.TERMINAL_MISSION_STATUSES` | 终态不倒改 |
| 标题近义阈值 | 0.85 (token Jaccard) | `gate.TITLE_DUP_THRESHOLD` | 沿用 v1 |

## ② 怎么做的（方法 / 工具链 / 关键设置）

1. **判据不复制**：裁决直接调 `gate.evaluate(..., min_acceptance_chars=8)`；`gate.py` 只把
   `MIN_ACCEPTANCE_CHARS` 变成一个**可透传参数**（缺省 4，v1 行为零变化）。两套判据各写一遍
   必然漂移——「门前放行、门后被同一句话判死」比噪声更坏。
2. **审计面 = 可领面**：候选查询 `log.status='open' AND log.audit_verdict='' AND
   ledger.status='open'`。`audit_verdict=''` 是关键：审过的行不再被抽中，重跑幂等，帽的
   记账与审计动作一一对应；还没过事前门的 `pending_review` 锚不在审计面（不是本模块的事）。
3. **回撤只动未终态**：`revoke_missions()` 只 `UPDATE ... WHERE status NOT IN (终态)`；
   `RETURNING id` 的 `psql -tA` 输出在 0 行时会带命令标签 `UPDATE 0`，故只认「整行纯数字」，
   不靠 split 后 `isdigit` 猜（这是 v1 老代码里踩过一次的真坑）。
4. **幂等与自愈**：迁移 `backend/migrations/124_demand_intake_audit.sql`（`BEGIN/COMMIT` +
   全 `IF NOT EXISTS`）是生产路径；`store.audit_ddl()` 是 `cli audit` 与测试的自愈路径，
   **同一份语句形状**，并有测试（`test_migration_file_matches_audit_ddl`）盯着两边不漂移。
5. **入口**：`python3 -m tools.demand_intake.cli audit [--cap 20] [--limit N] [--apply]`，
   缺省 dry-run（只读是默认，写要显式 `--apply`），与既有 `gate` 子命令同风格。
6. **验证工具链**：pytest + **真 PG**（本机 `su postgres` peer 认证）。测试在临时 schema
   `m17298_audit_scratch` 内建同构表、种合成夹具、跑真审计链，结束 `DROP SCHEMA ... CASCADE`；
   无 psql 的环境自动 skip。证据脚本 `docs/demand_intake/m17298/make_evidence.py` 同法跑
   8 锚 / 9 单的全链，报告里的每个数字都来自它（零手抄）。
7. **零领域语义**：夹具 slug 一律 `FEAT-SYNTH-*`（`FEAT-SYNTH-OK0..4` / `-THIN0..2`），
   标题与验收是合成句子；域内容只经 DB 注入的行。

改动面（10 文件，硬顶未破）：

| 文件 | 性质 |
|---|---|
| `tools/demand_intake/audit.py` | 新增（135 行）：抽样/裁决/回撤/留痕 |
| `backend/migrations/124_demand_intake_audit.sql` | 新增：幂等加列 + 口径视图 |
| `tests/test_demand_intake_audit.py` | 新增：12 测试（4 纯函数面 + 8 真 PG 面） |
| `docs/demand_intake/m17298/make_evidence.py` | 新增：证据生成器（可复跑） |
| `docs/demand_intake/m17298/REPORT.md` | 新增：本报告 |
| `tools/demand_intake/store.py` | 改：`audit_ddl/ensure_audit_schema/count_audits_today/fetch_audit_candidates/fetch_slug_mission_ids` + 三个常量 |
| `tools/demand_intake/gate.py` | 改：`acceptance_problem/evaluate` 增加 `min_chars`（缺省 4，行为不变） |
| `tools/demand_intake/cli.py` | 改：新增 `audit` 子命令（缺省 dry-run） |
| `tools/demand_intake/__init__.py` | 改：版本 1.0.0→1.1.0、流水图与模块表补 audit |
| `tests/test_demand_intake.py` | 改（1 行）：修 main 上 **pre-existing 红测**——`"...偏差 <3%" % GOOD` 里 `% G` 被当成格式符，`ValueError: incomplete format`；改 f-string |

重叠声明（纪律要求）：本轮**不碰** TensorPlatform 核心模块（backend/api、backend/services 等热
文件），只动 `tools/demand_intake/`、一条新迁移、两个测试文件与 `docs/demand_intake/` 新目录。
与并行重开 #1305 的另一条切片（`dispatch_client`，另一分支 `agent/m17285`）**无文件重叠**：
那条做「可领 → 建单」，本条做「已上架 → 复核/回撤」，接缝是 `demand_intake_log` 的同一张表，
但本条只**加列**、不改任何既有列语义。

## ③ 计算结果（数字表，全部由 `make_evidence.py` 程序注入）

夹具：8 条可领锚（5 条验收合规 `pytest 全绿且覆盖率 >90%`（20 字符）/ 3 条验收过薄
`偏差 <3%`（6 字符）），9 张单（8 未终态：5 queued + 3 running；1 已 `succeeded`）。

| 轮次 | 日帽 | 已用 | 预算 | 待审总 | 抽样 | pass | fail | dropped | 撤单 | uncovered |
|---|---|---|---|---|---|---|---|---|---|---|
| dry-run（零写） | 20 | 0 | 20 | 8 | 8 | 5 | 3 | 3(预演) | 0 | 8 |
| 第 1 轮 | 4 | 0 | 4 | 8 | 4 | 4 | 0 | 0 | 0 | **4** |
| 第 2 轮（帽已用尽） | 4 | 4 | **0** | 4 | 0 | 0 | 0 | 0 | 0 | **4** |
| 收尾轮 | 100 | 4 | 96 | 4 | 4 | 1 | 3 | 3 | **3** | **0** |

收尾轮后的世界状态：

| 量 | 值 | 判据 |
|---|---|---|
| `feature_ledger` open / dropped | 5 / 3 | 5 条合规锚仍在可领面，3 条过薄锚被摘 |
| 过薄锚仍 open 的条数 | **0** | 出口真的关得住 |
| 单子 revoked / succeeded | 3 / 1 | 撤的正是 3 张 running；`succeeded` 那张（9901）**未被动过** |
| 原因码分布（收尾轮） | `acceptance_too_short: 3`, `PASS: 1` | 与夹具设计一致（3 薄 / 1 合规） |
| chronicle 事件 | 5 条，phase ∈ {audit_run, audit_fail} | 留痕可达 |
| 临时 schema 是否残留 | `scratch_schema_dropped: true` | 红队不留残留 |

测试面：`pytest tests/test_demand_intake.py tests/test_demand_intake_audit.py -q` → **31 passed**
（v1 的 19 条 + 本轮 12 条；v1 那 19 条此前在 main 上是 `1 failed, 18 passed`，红测已修）。

## ④ 结果图

**本单无图，理由**：这是后端机制/状态机改动，没有任何物理场量或几何形状可画云图；可比的量是
布尔断言与计数（零写、幂等、帽、覆盖缺口），已被 ③ 的两张表完整表达。为满足「数字可复现」，
证据生成器 `docs/demand_intake/m17298/make_evidence.py` 入库：`python3
docs/demand_intake/m17298/make_evidence.py` 一键重放全链并打印 ③ 的全部数字（含终态统计与
schema 清理自检），任何人可复核，无需采信本文手抄值。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **psql `-tA` 的命令标签坑**：`UPDATE ... RETURNING id` 在 0 行时输出 `UPDATE 0`，若用
   `split()` + `isdigit()` 逐 token 判会得到幽灵 id `0`。已按整行纯数字过滤，并有单测盯住
   （`test_revoke_missions_ignores_terminal_statuses` 传 `[9401, 9402, 0]`，只撤 9402）。
2. **抽样不是随机的**：候选按 `l.id` 升序取前 `budget` 条（FIFO 先到先审）。好处是确定性、
   可测；代价是**日均入榜 > 日帽时会积压且滞后**，且新锚永远排不上队。`uncovered` 把这个
   缺口如实数字化（第 2 轮 `uncovered=4` 即帽用尽的直接证据），但没有解决滞后——见下条。
3. **未完成的分片（如实申报）**：原 #1305 的其余三件（铸造帽原子配额 + strike 降级、同步建单、
   agent vs coordinator 对照周报）**本轮未做**。审计因此只覆盖「已被 v1 事前门放行的锚」；
   没有铸造帽，噪声源仍可在入榜侧放量（靠审计摘，不靠上限拦）。
4. **dry-run 的 `uncovered` 语义**：dry-run 不写 `audited_at`，故 `uncovered` 恒等于
   `pending_total`（③ 第 1 行 8/8）。读这列时必须先看 `dry_run` 字段，别把预演覆盖缺口当
   真实缺口报出去。
5. **`log` 与 `ledger` 双面一致**：审计面要求两面同为 `open`。历史上有过两面不一致的行
   （v1 的 `apply_gate` 分两次 UPDATE），这类行不会进审计面——是保守选择（宁可漏审，不改
   别人没认领的状态），`pending_total` 会因此低估真实积压。读数时注意。
6. **全量测试的现实**：本仓 `pytest tests/` 收集 13602 条、其中 **48 个 collection error，
   全部 pre-existing 且与本包无关**（集中在 `tests/coupling/`、`tests/evidence_regression/`、
   `tests/verifier/` 等，`grep demand_intake` = 0 命中）；因此本单验证口径是**本包全量**
   （`tests/test_demand_intake*.py` 31 条全绿）+ 真 PG 红队，而非整仓全量。此局限不粉饰。

## ⑥ 误差判定

本单无物理基准（不是仿真任务），可比量是**机器可判定的等式断言**，逐项列如下：

| 关键量 | 基准（应为） | 实测 | 是否 <3% |
|---|---|---|---|
| dry-run 写入行数 | 0 | 0（`state()` 前后完全相等） | ✅ 0% |
| 日帽 4 下抽样数 | ≤4 | 4 | ✅ 0% |
| 帽用尽后抽样数 | 0 | 0 | ✅ 0% |
| `uncovered` 覆盖缺口 | 8-4=4 | 4 | ✅ 0% |
| 被撤单集合 | {9800,9801,9802} | 同 | ✅ 0% |
| 终态单是否被倒改 | 否（succeeded 仍 succeeded） | 未动 | ✅ 0% |
| 过薄锚残留可领面 | 0 | 0 | ✅ 0% |
| 重跑是否重复抽样 | 0 | 0 | ✅ 0% |
| 迁移文件两遍幂等 | 列/视图各一份 | 3 列 + 1 视图 | ✅ 0% |

**结论：是，全部关键量 vs 基准偏差 0%（精确等式，非数值近似）。** 没有「误差百分比」意义上的
灰色地带——断言不是等式就红。**诚实的负面项**：原 PR 的另外三件未做（见 ⑤-3），审计对
「入榜侧放量」无拦阻能力，这是能力边界的如实交代，不是粉饰。

## 复现命令

```bash
# 单元 + 真 PG 红队（无 psql 的环境自动 skip）
python -m pytest tests/test_demand_intake.py tests/test_demand_intake_audit.py -q
# 证据复跑（打印 ③ 全部数字，结束 DROP 临时 schema）
python3 docs/demand_intake/m17298/make_evidence.py
# 生产路径：先迁移，再排演，最后落地
psql -d tensorplatform -f backend/migrations/124_demand_intake_audit.sql
python3 -m tools.demand_intake.cli audit --cap 20            # dry-run
python3 -m tools.demand_intake.cli audit --cap 20 --apply    # 写
```