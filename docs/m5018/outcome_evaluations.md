# S×E 成果评价实体与记录流（AOG SPEC v1.2 §7.1 落机器 · m5018）

> 一句话：把「成果价值分级（S1–S5 与否弃轨 S-N）」与「证据成熟度（E0–E4）」
> 从规范条文变成**可写、可拒、可复算**的表 + API + 记录流，并用真实平台数据回填。
> 分级是**正交二维**：S 回答「创造了什么价值」，E 回答「凭什么相信」；本模块
> 不提供任何合成分数或主体排名（`policy.weighted_score = None`、`policy.ranking = None`）。

## 1. 交付物一览

| 路径 | 作用 | 证据级 |
|---|---|---|
| `tools/outcome_evaluations.py` | 实体 DDL + 判据执行器 `decide()` + 记录构造 + 统计 `summarize()` + 可复算 SQL 生成；纯逻辑、零领域语义 | REAL |
| `backend/api/outcome_evaluations.py` | 6 个端点（写入/流水/明细/统计/统计 SQL/待评价补建）+ 组织隔离 + 幂等落库 | REAL |
| `backend/migrations/103_outcome_evaluations.sql` | PostgreSQL 迁移（已应用，含 6 条 CHECK + 分部唯一索引） | REAL |
| `backend/api/tensor_missions.py` | succeeded 完结钩子：自动生成待评价记录（fail-open，不阻断完结） | REAL |
| `backend/main.py` | 路由挂载 `/api/v1/tensor/outcome-evaluations/*` | REAL |
| `tools/backfill_outcome_evaluations.py` | 真实回填（待评价记录 + 规则化 S/E 草案 + 统计快照） | REAL |
| `tools/plot_outcome_evaluations.py` | 分布图（S×E 矩阵热力图 + 记录流覆盖 + 分层计数） | REAL |
| `tests/test_outcome_evaluations.py` | 36 个判据测试（含 5 类 MUST-拒负例） | REAL |
| `artifacts/m5018_outcome_evaluations/` | 回填报告 / 统计快照 / SQL / psql 复算输出 / 图 / PG 冒烟 | REAL |

## 2. 实体：`outcome_evaluations`

字段与 §7.1「评价记录与治理要求」逐条对应（列名见迁移 103）：

| 字段 | 语义 | §7.1 要求 |
|---|---|---|
| `artifact_id` | 成果 ID（默认 `mission:<id>`，一 mission 一主成果；多成果由调用方另起 artifact） | 成果 ID |
| `mission_id` | 关联 Mission | 关联 Mission |
| `contributor` | 贡献 Agent/团队 | 贡献 Agent/团队 |
| `grade` | `S1`–`S5` / `S-N`，pending 行为 `NULL` | 成果等级 |
| `grade_reason` | 等级理由，写入时**去空白后 ≥50 字符** | 等级理由 |
| `e_level` | `E0`–`E4` | 证据等级 |
| `reviewer` / `reviewer_kind` / `review_status` | 审查者、审查类型（`independent`/`auto_scan`）、审查状态 | 独立审查者 / 审查意见 |
| `evidence_refs` | JSON 数组 `[{kind, ref}]`，kind ∈ `report/commit/artifact/sql/run/doc/api/benchmark` | Artifact 引用 |
| `baseline_or_scope` | 对照基线（S2/S4 必需）或新颖性检索范围（S5 必需） | 对照基线/检索范围 |
| `boundary` | 适用边界（必填） | 适用边界 |
| `status` | `pending/confirmed/candidate/rejected` | — |
| `reason_codes` | 判据命中码（JSON 数组） | — |
| `reason_source` | `manual` / `rule_derived` / `auto_pending` | 申报诚实度可追溯 |
| `window_finished_at` | 所属 mission 完结时间（统计窗口锚点） | 时间 |
| `created_at` / `decided_at` | 记录生成 / 判定时间 | 时间 |

数据库层**硬约束**（绕过 API 直写也进不来）：

```sql
CHECK (status IN ('pending','confirmed','candidate','rejected'))
CHECK (grade IS NULL OR grade IN ('S1','S2','S3','S4','S5','S-N'))
CHECK (e_level IS NULL OR e_level IN ('E0','E1','E2','E3','E4'))
CHECK (reviewer_kind IS NULL OR reviewer_kind IN ('independent','auto_scan'))
CHECK (status <> 'pending' OR (grade IS NULL AND e_level IS NULL))        -- 待评价不得预填等级
CHECK (status IN ('pending','rejected') OR (grade IS NOT NULL AND e_level IS NOT NULL))
CREATE UNIQUE INDEX uq_outcome_eval_artifact_active
  ON outcome_evaluations (artifact_id) WHERE status <> 'rejected';        -- 同成果仅一条有效记录
```

## 3. 判据（`decide()`：纯函数、先于实现、可单测）

### 3.1 硬判据 → `rejected`（**无理由等级必拒**）

| 判据码 | 触发条件 |
|---|---|
| `ARTIFACT_ID_MISSING` / `MISSION_ID_INVALID` / `CONTRIBUTOR_MISSING` | 标识缺失 |
| `GRADE_MISSING` / `GRADE_INVALID` | 等级缺失或不在 `S1..S5, S-N` |
| `E_LEVEL_MISSING` / `E_LEVEL_INVALID` | 证据级缺失或不在 `E0..E4` |
| **`REASON_MISSING`** | 理由为空/全空白 |
| **`REASON_TOO_SHORT`** | 理由**去全部空白后** < 50 字符（防空格填充凑数） |
| `EVIDENCE_REF_MISSING` / `EVIDENCE_REF_INVALID` | 无证据引用 / kind 不在白名单 / ref 为空 / 混入非法项 |
| `REVIEWER_MISSING` / `REVIEWER_KIND_INVALID` | 无审查者 / 审查类型非法 |
| `REVIEWER_NOT_INDEPENDENT` | 审查者与贡献者同一主体（含大小写、空白、`@` 后缀归一）→ 自评不成立 |
| `E0_NOT_REGISTRABLE` | §7.1 规则 8：任何等级须 E≥1，E0 只能停在任务描述层 |
| `NEGATIVE_BELOW_E2` | §7.1 否定轨：S-N 须 E≥2（否定结论必须可复现） |
| `BOUNDARY_MISSING` | 未声明适用边界 |
| `BASELINE_REQUIRED` | S2/S4 缺对照基线（§7.1 规则 4） |
| `SCOPE_REQUIRED` | S5 缺新颖性检索范围 |

### 3.2 软判据 → `candidate`（**合法登记，但不计入分布**，§7.1 规则 9）

| 判据码 | 触发条件 |
|---|---|
| `CANDIDATE_S3_NEEDS_E2` | S3 仅 E1：能力真实可用尚未被独立验证 |
| `CANDIDATE_S4_NEEDS_E2` | S4/E1：§7.1 规则 9 明确 S4/E1 只能是「候选」 |
| `CANDIDATE_S5_NEEDS_E3` | S5 且 E<3：§7.1 规则 9 明确 S5 须 E≥3 方为已确认 |
| `CANDIDATE_NEEDS_INDEPENDENT_REVIEW` | S4/S5/S-N 由 `auto_scan`（机器扫描）判出——机器扫描不是独立同行评审 |

### 3.3 合法组合与结果（现状 = 实现，可执行）

| S | 确认门槛（其余落 candidate） |
|---|---|
| S1 | E≥1 |
| S2 | E≥1 + 对照基线 |
| S3 | E≥2 |
| S4 | E≥2 + 基线 + **独立评审**（`reviewer_kind=independent`） |
| S5 | E≥3 + 检索范围 + **独立评审** |
| S-N | E≥2 + **独立复核** |

## 4. 记录流（每张 succeeded mission 自动生成待评价记录）

1. **自动生成**：`complete_mission`（`POST /tensor/missions/{id}/complete`）把 status 置为
   `succeeded` 时，调用 `ensure_pending_for_mission()` 为**本单**建一条 `pending` 记录
   （等级留空、`reason_codes=["PENDING_NO_EVALUATION"]`）。整段包在 `try/except` 内：
   表缺失或迁移未上只告警，**绝不阻断完结主流程**。
2. **存量补建**：`POST /tensor/outcome-evaluations/pending/sync`（幂等，`INSERT ... WHERE NOT EXISTS`）。
3. **判定就地升级**：写入评价时命中该成果的 `pending` 行 → `UPDATE` 就地判定，不新增行；
   重复写入/重判同样是 `UPDATE`（`status <> 'rejected'` 条件），因此**可重放幂等**。
4. **负例留痕**：被拒写入落 `status='rejected'` 一行（`evaluation_id = <artifact>:rejected:<rand>`），
   不占用分部唯一索引，审计链保留「谁在何时提交过无法成立的评价」。

## 5. API（prefix `/api/v1/tensor/outcome-evaluations`）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/` | 写入评价：`confirmed`/`candidate` → 200；硬判据不过 → **422 + reason_codes**（并落 rejected 留痕）；mission 非 succeeded → 409 |
| GET | `/` | 流水（过滤：`mission_id`/`status`/`grade`/`include_rejected`） |
| GET | `/{evaluation_id}` | 单条明细（含判据码） |
| GET | `/stats?repo=&window=30` | 分层分布（含 markdown 渲染、policy 声明） |
| GET | `/stats/sql?window=30` | 统计 SQL 原文（不执行，供独立复算） |
| POST | `/pending/sync?repo=&limit=` | 待评价记录补建（幂等） |

鉴权沿用平台 `require_auth` + 组织隔离（非管理员只见本组织）；无排名/加权接口，**不提供**。

## 6. 真实回填（规则化扫描，非人工评审）

方法（`tools/backfill_outcome_evaluations.py`）：

- 窗口：本仓（`TensorPlatform`）近 30 张 `succeeded` mission（`finished_at DESC`）。
- **E 级**（证据只认平台事实）：报告（按 `tensor_reports.mission_id` 关联，因 `report_id` 常空 =
  报告欠账机制）+ 能力增量（`tensor_capability_deltas`）双证据 → **E2**；仅其一 → **E1**；
  都没有 → E0（不可登记，保持 pending）。**扫描上限 E2**：E3/E4 要求外部复现/独立复算，
  机器扫描不可自证。
- **S 级**：能力增量类别 → `new_capability`/`reusable_knowledge` → S3；`capability_extension`/
  `defect_fix` 且有基线证据 → S2（否则 S1）；`falsified_hypothesis` → S-N；`no_substantive_delta`
  → S1（执行性下限）。
- 审查者 = `platform-evidence-scan`，`reviewer_kind='auto_scan'`：明确标注**不是独立评审**，
  因此 S4/S5/S-N 只可能落 `candidate`。
- 理由文本程序化生成（内嵌 mission / 报告号 / 正文字数 / 图数 / 增量类别 / 工件数），
  无任何手抄数字；`reason_source='rule_derived'`。

结果（快照 2026-11-07T02:15Z，窗口 30 单，全部 `confirmed`）：

| 维度 | 计数 |
|---|---|
| 窗口 succeeded mission | 30 |
| 已建记录 / 已判定 / 仍待评价 | 30 / 30 / 0 |
| S 等级 | S1 = 30（S2–S5、S-N = 0） |
| E 等级 | E1 = 3，E2 = 27 |
| S×E（confirmed） | S1/E1 = 3，S1/E2 = 27，余 0 |
| 全仓覆盖 | 691 张 succeeded mission 中 30 张有记录（窗口口径） |

**这是一条必须如实报告的发现**：分布完全退化为 S1。原因是平台能力增量账本
（`tensor_capability_deltas`，全生态 1170 行）**100% 申报 `no_substantive_delta`**，
因此结构判据只能给执行性下限 S1 —— 也就是说 §7.1 要治的病（「把完成任务误当创造价值」）
在数据上真实存在：分级机制已就位，但价值分级**事实上没有被使用**。E 维尚能分层（E1/E2），
说明记录流与证据口径确实在工作。

## 7. 统计口径与独立复算（禁加权禁排名）

- 分布只数 `confirmed`；`candidate` 与 `pending` **单列**（`sxe_matrix_candidate` /
  `ladders.*.pending`），不混入 confirmed 分布（§7.1 规则 9）。
- 否定轨 S-N 单列在等级域内，但**不并入** S1–S5 正面梯的任何汇总（`distribution_confirmed.by_grade`
  中 S-N 单独一行）。
- `summarize()` 不做任何二次算术：全部数字来自 `stats_queries()` 的 SQL 聚合，Python 只重塑形状，
  因此「统计 SQL 可复算」是结构性的而不是声称的。
- 输出硬编码 `weighting/ranking = forbidden`，并提供 `weighted_score=None`、`ranking=None`。

复算（`artifacts/m5018_outcome_evaluations/`）：

- `stats.sql` —— 9 条命名 SQL（`window`/`coverage`/`by_grade`/`by_e_level`/`matrix`/
  `by_reviewer_kind`/`by_reason_source`/`attempts`/`records`）+ 绑定参数。
- `stats_pinned.sql` + `stats_snapshot.json` —— 按快照窗口 mission id **钉住**的复算 SQL
  （平台是活系统，窗口会滑动；钉住后任何时刻复算结果恒定）。
- `recompute_psql.txt` —— `psql` 直接执行上述两份 SQL 的原始输出，与 `backfill_report.json`
  中的矩阵逐格一致（S1/E1=3、S1/E2=27）。
- `api_smoke_pg.json` —— 用真实 PostgreSQL 打这套 API 的冒烟结果：stats/list/sync 均 200，
  无理由写入 → **422 `REASON_MISSING`**，并留下 1 行 `rejected` 留痕。

## 8. 局限与边界（不粉饰）

1. **分布退化**（见 §6）：S 维暂无区分度。要让 S×E 分布有意义，先得让能力增量账本说真话
   （申报 `new_capability`/`capability_extension` 等），这是**上游申报纪律**问题，
   不是本模块能自证的部分。
2. **`auto_scan` ≠ 独立评审**：本单回填全部为机器证据扫描；S4/S5/S-N 只能落 `candidate`。
   真实晋升需要引入独立审查主体（人工/跨主体），本单未做。
3. **`evidence_refs` 只做形状校验**：不解析引用内容是否真的支持该等级；引用可解析性
   由审查者负责（§7.1「独立审查者」栏留痕）。
4. **一 mission 一主成果**：默认 `artifact_id=mission:<id>`。一 mission 多成果需调用方
   按成果另起 artifact（同一 mission 允许并存多条有效记录，前提是 artifact 不同）。
5. **生产可达性**：本 worktree 的 router 已挂载在 `backend/main.py`；线上 8010 进程服务的是
   另一份部署目录，**需部署本分支后才在 8010 可达**（TensorPlatform 处于冻结期，未改部署）。
   本单的可验证证据是：单测（TestClient）+ 真实 PG 冒烟（`api_smoke_pg.json`）。
6. **回填是「草案」不是「评审结论」**：`reason_source='rule_derived'`，`reviewer_kind='auto_scan'`，
   统计里 `by_reviewer_kind` / `by_reason_source` 单独分层，避免把扫描结果冒充人工评审。

## 9. 机器验收判据对照

| 判据 | 结果 | 证据 |
|---|---|---|
| 实体 + API 落地 | 表已建于 `tensorplatform`（迁移 103）+ 6 端点 | `backend/migrations/103_outcome_evaluations.sql`；`api_smoke_pg.json`（stats 200） |
| succeeded 完结自动生成待评价记录 | 钩子 `ensure_pending_for_mission`（fail-open、单 mission、幂等） | `tensor_missions.py`；`test_hook_helper_...`、`test_mission_completion_hook_is_wired` |
| 评价须理由 ≥50 字且带证据引用，无理由必拒 | 12 类硬判据；5 类无理由/短理由负例全拒 | `test_reject_without_sufficient_reason`（5 参数）、`test_reason_threshold_boundary` |
| 真实回填 ≥10 例 | **30 例**（窗口 30 单全判定） | `backfill_report.json`（`evaluations_written=30`） |
| 统计 SQL 可复算 | 9 条 SQL + 钉住快照 + psql 独立复算逐格一致 | `stats.sql`、`stats_pinned.sql`、`recompute_psql.txt` |
| pytest ≥8 含无理由 MUST 拒负例 | **36 passed**（含无理由负例 5 参数化 + API 422 负例） | `python -m pytest tests/test_outcome_evaluations.py -q` |
| 零领域语义（域词禁硬编码，夹具合成域名） | 代码与夹具无任何学科/业务词；夹具用 `SyntheticRepo`/`worker-a`/`reviewer-x` | `tests/test_outcome_evaluations.py` |
| 分层呈现、禁加权禁排名 | 矩阵 + 阶梯 + `policy.weighted_score=None` | `test_stats_stratified_recomputable_and_unweighted` |

## 10. 复现命令

```bash
# 1) 迁移（PG）
su postgres -c "psql -d tensorplatform -f backend/migrations/103_outcome_evaluations.sql"

# 2) 判据单测（无 DB 依赖，内存 SQLite）
python -m pytest tests/test_outcome_evaluations.py -q

# 3) 真实回填 + 统计快照（幂等，可重放）
python tools/backfill_outcome_evaluations.py \
  --dsn "postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql" \
  --repo TensorPlatform --window 30 --mode all \
  --out artifacts/m5018_outcome_evaluations

# 4) 出图
python tools/plot_outcome_evaluations.py \
  --report artifacts/m5018_outcome_evaluations/backfill_report.json

# 5) 独立复算
su postgres -c "psql -d tensorplatform -f artifacts/m5018_outcome_evaluations/stats_pinned.sql"
```