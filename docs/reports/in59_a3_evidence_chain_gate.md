# IN59-A3 · 结论五元证据链（evidence chain）与 E 级封顶门

> 战役节点 IN59-A3（平台治理面）。仓库 TensorPlatform 分支 `agent/m6330`。
> 结论一句话：**S2+ 成果声明现在必须带「理论 / 代码 / 配置 / 原始结果 / 报告」五元引用；
> 缺件自动把 E 级封顶到 E1，S3+ 因封顶跌破 E 门槛者落 `candidate`（不计分布、不给信用）；
> 结算面（信用入账）按 `chain_blocked` 再拦一次；质量中心给出链完整度。**

## 1. 问题（案例介绍）

平台已有 `outcome_evaluations`（迁移 103）判「S×E 等级」，其硬判据只要求
`evidence_refs` **非空**：给一条 `{"kind": "report", "ref": "9"}` 就能登记 S3。
这正是评价体系最容易被击穿的位置——**报告有、原始数据没有，结论仍然"可证"**：

| 现状（IN59-A3 之前） | 缺口 |
|---|---|
| `evidence_refs` 只校验 kind 白名单 + 非空 | 不区分「引用的是理论/代码/配置/原始数据/报告」 |
| E 级由申报者自填，`decide()` 只做上下限交叉 | 无法机裁「原始数据缺件 → E 不该超过 E1」 |
| 信用结算（`credit_settler_loop`）按 `grade` 直接给 delta | 结算面没有第二条链检，历史脏行也能入账 |
| 质量中心（`/tensor/centers/quality`）无追溯面 | 链完整度不可见，缺件无法被统计出来 |

## 2. 方法（怎么做的）

### 2.1 五元契约（单一契约源 `tools/evidence_chain_link.py`）

| 元素 | 键 | 接受形态（机器判据） |
|---|---|---|
| 理论或文献依据 | `theory` | `doi:10.xxxx/...`、`http(s)://`、`arxiv:`、`isbn:`、`derivation:`、`cite:`、`std:`（前缀 + 最短长度） |
| 代码版本 | `code` | 7–40 位十六进制 sha，或 `repo@sha` |
| 实验配置 | `config` | `sha256:<64hex>` / `<64hex>` / `path@sha256:<64hex>` |
| 原始计算结果 | `data` | 文件路径 / `artifact:` / `file:` / `s3://` / `gs://` / `run:` / `sha256:`（**裸编号不收**） |
| 分析报告 | `report` | `report:123` / `#123` / `123`（正整数 report_id） |

完整度 = 可解析元数 / 5。形态非法的元素**不计入已提供**（不算完整），并给出
`CHAIN_INVALID_<元素>` 命中码；缺失给 `CHAIN_MISSING_<元素>`；缺 `data` 额外给
`CHAIN_RAW_DATA_MISSING`（红队口径）。

### 2.2 封顶与降级（`tools/outcome_evaluations.decide`）

* 只对 `grade ∈ {S2, S3, S4, S5, S-N}` 要求链（S1 豁免——S1 本身不宣称新结论）。
* 链不全 → `e_level = min(申报 E, E1)`（**只降不升**），命中 `CHAIN_CAPS_E_TO_E1`；
  未给链额外命中 `CHAIN_ABSENT`。
* 封顶后若低于该 S 级的 E 门槛（S3/S4/S-N 需 E≥2、S5 需 E≥3）→ `status=candidate`
  并命中 `CANDIDATE_CHAIN_CAP_BELOW_GRADE`（候选不计入分布、不给信用）。
* 封顶不改变既有 §7.1 硬判据的顺序：`E0_NOT_REGISTRABLE`、`NEGATIVE_BELOW_E2` 等照旧生效。

### 2.3 schema（迁移 105 与模块 DDL 同源）

* 表 `outcome_evidence_chains`：一评价一行，五元引用 + `present/missing/invalid` +
  `completeness` + `chain_status` + `capped` + `e_level_requested/granted` + `codes`；
  CHECK 兜 `chain_status ∈ {complete,partial,missing}`、`0 ≤ completeness ≤ 1`、E 取值域；
  `evaluation_id` 唯一（重判先删同键再插 → 幂等）。
* 视图 `outcome_eval_chain_audit`：左连评价行，给出 `chain_status / chain_completeness /
  chain_blocked`；`chain_blocked` **只对 `status='confirmed'` 且 grade ∈ {S3,S4,S5,S-N}
  且链不完整**成立（candidate 本就不入账，不虚增"拦付"数）。
* 迁移 105 已在 PostgreSQL 15.5 实际应用（`psql -f backend/migrations/105_...sql`），
  表/索引/视图齐备。

### 2.4 三条接线

1. **判定面**：`decide()` 内部调用 `analyze_chain`，所有走 `decide` 的路径（API 写入、
   回填 `tools/backfill_outcome_evaluations.py`、在途评价 `tools/evaluation_ops/inflight.py`）
   自动获得链检，不存在"绕过 decide 就逃掉"的分支。
2. **落库/读面**：`POST /api/v1/tensor/outcome-evaluations` 接受 `evidence_chain`
   （+ 可选 `conclusion_ref`），落链表并回显 `evidence_chain` 摘要；
   新增 `GET /chains/stats`、`GET /{evaluation_id}/chain`。
3. **结算面**：`harness_gateway/mirror/credit_settler_loop.py` 改走
   `outcome_eval_chain_audit`，`chain_blocked = FALSE` 才入信用账；视图缺失时退回原表并
   记 WARN（不静默假装已链检）。
4. **质量中心**：`GET /api/v1/tensor/centers/quality` 增 7 个指标 + 第 5 道门
   `evidence_chain_gate` + 最近缺件清单；表缺失时 `status=not_implemented`、`value=null`
   （不写 0 冒充）。

## 3. 计算结果（判据矩阵，全部程序化注入）

`scripts/in59_a3_measure.py` 跑 (S 级 6 × 申报 E 4 × 链形态 5) = **120 格**，
原始结果 `artifacts/in59_a3/results.json`（config hash `sha256:f94a3963…a222f`）。

| 统计量 | 值 |
|---|---|
| 矩阵行数 | 120 |
| confirmed / candidate / rejected | 51 / 52 / 17 |
| 被链封顶（`CHAIN_CAPS_E_TO_E1`）行数 | 64 |
| 授予 E < 申报 E 的行数 | 60 |
| **授予 E > 申报 E 的行数** | **0**（只降不升） |
| 完整链被压级的行数 | 0（`complete_chain_grade_preserved=true`） |
| S3 缺 `data` 全部降级 | 是（4/4 落 candidate/E1 + `CHAIN_RAW_DATA_MISSING`） |

分形态看（S3 面，4 个申报 E 档合计）：

| 链形态 | confirmed | candidate | 授予 E 集合 |
|---|---|---|---|
| 五元齐全 | 3 | 1（申报 E1 → 需 E≥2） | {E1,E2,E3,E4} |
| 缺 `data`（红队） | 0 | 4 | {E1} |
| 缺 `theory` | 0 | 4 | {E1} |
| `code=main`（非 sha） | 0 | 4 | {E1} |
| 未给链 | 0 | 4 | {E1} |

> 64 vs 80 的差：`S-N` 的 16 格在封顶到 E1 后触发既有 `NEGATIVE_BELOW_E2`（否定轨须 E≥2）
> 直接 `rejected`（17 = 16 + completion E1 一例），硬拒路径不附带链命中码，故不计入 64。

图（`artifacts/in59_a3/`，均由 results.json 生成）：

* `fig_ec_cap_matrix.png` — 申报 E × 链形态 → 授予 E 矩阵（缺件整列 E1）。
* `fig_ec_status_by_variant.png` — S3 各形态 confirmed/candidate/rejected 计数。
* `fig_ec_completeness_vs_granted.png` — 完整度 → 授予 E 散点（完整度<1 全压在 E1）。

### 3.1 PostgreSQL 端到端演练

`scripts/in59_a3_demo_chain.py`（单事务、演练后回滚，产物 `artifacts/in59_a3/pg_demo.json`）
在 `tensorplatform` 上走产品代码路径：

三个演练例（同一事务内）：

| 例 | 构造 | 判定 | 链行 | 结算守卫 | 视图 `chain_blocked` |
|---|---|---|---|---|---|
| `complete` | 五元齐全 S3/E3 | `confirmed` E3 | `complete`, 1.0, `capped=false` | **放行** | false |
| `redteam_missing_data` | 同声明缺 `data` | `candidate` E1（命中 `CHAIN_RAW_DATA_MISSING / CHAIN_CAPS_E_TO_E1 / CANDIDATE_CHAIN_CAP_BELOW_GRADE`） | `partial`, 0.8, `capped=true` | 不入账（非 confirmed/rejected） | false |
| `legacy_confirmed_no_chain` | 模拟上线前已 confirmed 的 S3（无链，直写形态） | 判据不回溯改行 → 仍 confirmed | `missing`, 0.0 | **拦付** | **true** |

机器自检（`artifacts/in59_a3/pg_demo.json:checks`）：`complete_confirmed / redteam_downgraded /
redteam_code_present / guard_admits_complete_only / legacy_confirmed_blocked` 全 `true`，
`all_pass=true`；演练后回滚自检 `rolled_back_rowcount=0`，平台评价行数保持 4546。

## 4. 注意点（踩过的坑 / 局限 / 适用范围）

1. **视图不能带 `IF NOT EXISTS`**：PostgreSQL 无 `CREATE VIEW IF NOT EXISTS`，
   而 SQLite 无 `CREATE OR REPLACE VIEW` → 统一用 `DROP VIEW IF EXISTS` + `CREATE VIEW`
   才能同一段 DDL 双引擎可跑（踩过：先写 `IF NOT EXISTS` 在 SQLite 通过、PG 语法失败）。
2. **视图必须暴露 `organization_id`**：质量中心的计数一律带 org 过滤，视图缺这一列会让
   "结算拦付数"静默变成 0（踩过，靠 pytest 的 0 == 1 断言抓到）。
3. **封顶命中码不能进硬拒通道**：链命中码若混入硬判据列表，会把"缺件"判成 `rejected`，
   与"封顶到 E1"的语义相反 → 链码单独成列，只在终态合并。
4. **S-N 的封顶是"拒收"而不是"降级"**：否定轨本就要求 E≥2，被封到 E1 后不可登记；
   这是既有硬判据的结果，不是本节点新增规则。
5. **历史行影响**：迁移 105 应用时平台现有 4546 条评价中 `S3+/S4/S5` 的 confirmed 行 **0 条**
   （S3+ 全部已是 candidate），所以 `chain_blocked` 当前为 **0**、未入账被拦 **0**
   ——没有回改任何历史信用。链判据对**未来**的 S2+ 登记生效；对历史 row 的直接 SQL
   篡改则由结算面兜住（红队测试 `test_redteam_legacy_confirmed_row_is_barred_by_settlement_guard`）。
6. **回填/在途评价会被压级**：`tools/backfill_outcome_evaluations.py` 等从任务事实合成
   S2/S3 声明，但拿不到五元（尤其原始数据与 commit sha）→ 现在会落 `candidate/E1`。
   这是**预期行为**（不给自己发无法追溯的等级），但会让回填产出统计下降；需要时应在
   回填器里接真实工件（本节点未做，见局限）。
7. **链元素只验"形态可解析"，不验"内容为真"**：`report:123` 只保证是个正整数，
   是否真的存在、是否与本结论相关仍归 `tools/eval_redteam.py` 的引用解析与复算。
   本模块是**必要条件**（没有就封顶），不是充分条件。
8. **链表当前 0 行是如实状态**：迁移 105 已在 `tensorplatform` 应用，但线上部署的 API
   仍是旧代码（本分支未合并），因此还没有任何评价走过带链的写入路径 →
   `outcome_evidence_chains` 空表、质量中心显示 `0 / null`。合并上线后新写入的 S2+ 评价
   才会带链；这也是「不编造完整率」的体现（空表不写 0% 也不写 100%）。
9. **`e_level_granted` 与 `outcome_evaluations.e_level` 应当一致**（同一 verdict 写两处）；
   若有人直接改评价表，链表与视图会暴露不一致（视图取链表的 `chain_status`，
   评价表取 grade/status）——这点在读面可复核。

## 5. 误差判定（vs 判据基准）

本节点是治理/契约类改动，基准是**判据矩阵的期望值**而非物理量，但同样逐项量化：

| 关键量 | 基准 | 实测 | 误差 | <3%? |
|---|---|---|---|---|
| 完整链被压级行数 | 0 | 0 | 0 | ✅（0 绝对误差） |
| 授予 E > 申报 E 的行数（防"封顶反而加分"） | 0 | 0 | 0 | ✅ |
| S3 缺 `data` 降级率 | 100%（4/4） | 100%（4/4） | 0 | ✅ |
| 缺件行封顶到 E1 的比例（S2–S5 非硬拒行） | 100%（64/64） | 100%（64/64） | 0 | ✅ |
| pytest：`tests/test_evidence_chain_link.py` | 32 passed | 32 passed | 0 | ✅ |
| pytest：`tests/test_outcome_evaluations.py`（回归） | 36 passed | 36 passed | 0 | ✅ |
| 质量中心链完整度 vs 手写 SQL 对拍 | 0.8 / 0.9 精确相等 | 一致 | 0 | ✅ |

无物理基准量（本节点不含数值求解），故不涉 >15% 不可接受判据；矩阵基准与实测**逐格相等**。

## 6. 复现

```bash
# 判据矩阵 + 图（零手抄）
python3 scripts/in59_a3_measure.py --out artifacts/in59_a3/results.json
python3 scripts/in59_a3_gen_figures.py

# 测试
python3 -m pytest tests/test_evidence_chain_link.py tests/test_outcome_evaluations.py -q
cd backend && python3 -m pytest tests/test_tensor_centers_contract.py -q

# PostgreSQL 迁移（幂等）
su postgres -c "psql -d tensorplatform -f backend/migrations/105_outcome_evidence_chains.sql"

# PG 端到端演练（单事务，回滚）
runuser -u postgres -- python3 scripts/in59_a3_demo_chain.py \
    --dsn "postgresql:///tensorplatform?host=/var/run/postgresql" \
    --code-sha <commit> --config-hash sha256:<hex> \
    --data-ref artifacts/in59_a3/results.json --report-id <report_id>
```

## 7. 五元自指（本结论自己的证据链）

| 元素 | 引用 |
|---|---|
| 理论/文献 | AOG SPEC v1.2 §7.1（S×E 判据）+ 本节点 `derivation:#evidence-chain-five-elements` |
| 代码版本 | `TensorPlatform@a6371945dcbe0e51dddb49196e263b737d550404`（分支 `agent/m6330`，见 `pg_demo.json:code_sha`） |
| 实验配置 | `artifacts/in59_a3/run_config.json` 的 `sha256:f94a396383fb95486ebd03d61d6dfbeded7cd4a50dfae3e1fef9f83d4dea222f` |
| 原始结果 | `artifacts/in59_a3/results.json`（120 格矩阵）+ `artifacts/in59_a3/pg_demo.json`（PG 演练） |
| 分析报告 | 本文件 + `/tensor/reports` **report_id 7512**（`artifacts/in59_a3/pg_demo.json:report_id` 与 `cases.complete` 的五元 `report` 引用） |

## 8. 未做 / 局限（honest）

* **回填器未接真实工件**：`backfill_outcome_evaluations.py` / `inflight.py` 仍不能自动
  组装五元（缺 commit sha、原始数据锚的通用取法），因此其 S2+ 产出被压级为 candidate/E1。
  正确做法是给回填器接 `contracts/tensorphysics-run-manifest-v1` 的 run 登记（run_id →
  commit/配置/文件摘要），本节点**未实现**。
* **前端未改**：质量中心后端已给完整度面，`frontend-console` 的四中心页面尚未渲染
  这 7 个指标（后端契约先行，前端下一节点接）。
* **`report_id` 只验形态**：链不校验报告是否为该 mission 的、是否 QA passed；那部分归
  `tools/eval_redteam.py`（引用解析）与报告 QA 门。
* **信用结算守护依赖视图**：旧库未跑迁移 105 时结算会退回无链检路径（日志 WARN），
  平台已在 `tensorplatform` 应用迁移，故当前为链检生效态。